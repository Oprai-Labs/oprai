import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import { WalletService } from '@core/services/wallet.service';
import { SolanaRpcService } from '../../portfolio/services/solana-rpc.service';
import { environment } from '../../../../environments/environment';
import { awaitSignature, createSolanaConnection } from '@core/utils/solana-connection';

export interface StakeAccount {
  pubkey: string;
  validatorVoteAccount: string;
  stakedLamports: number;
  totalLamports: number;
  status: 'active' | 'activating' | 'deactivating' | 'inactive';
}

export interface Validator {
  voteAccount: string;
  name: string;
  commission: number;
  /** Null when the network yield could not be read — show nothing, not a guess. */
  estimatedApy: number | null;
  activatedStakeSol: number;
  lastVote: number;
}

interface BuildResponse {
  transaction?: string | null;
  preview?: {
    id?: string;
    description?: string;
    estimatedFee?: string;
    estimatedRefund?: string;
    warnings?: string[];
    requiresApproval?: boolean;
  };
}

const LAMPORTS_PER_SOL = 1_000_000_000;

// Solana mainnet well-known validator vote accounts (verified from chain/explorer)
const KNOWN_VALIDATORS: Record<string, string> = {
  'CertusDeBmqN8ZawdkxK5kFGMwBXdudvs6zbe3onMC3A': 'Jito',
  'J1to1yufRnoWn81KYg1XkTWzmKjnYSnmE2VY8DGAJ9Ws': 'Jito 2',
  'DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy': 'Solana Foundation',
  '7K8DVxtNJGnMtUY1CQJT5jcs8sFGSZTDiG7kowvFpECh': 'Galaxy Digital',
  'GE6atKoWiQ2pt3zL7N13pjNHjdLVys8LinG8qeJLcAiL': 'Chorus One',
  'he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A': 'Everstake',
  'Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk': 'Figment',
  'BwwpzEpo1e5JCG2nT3BFEMhTMJopkHuEBrDcGTUNsENE': 'Coinbase Cloud',
  'EogKiqPFLsbfQ1aVCRQvgRRm4pcpD8kC8V1DP6K3LVkw': 'Binance Staking',
};

@Injectable({ providedIn: 'root' })
export class NativeStakeService {
  private readonly api = inject(ApiService);
  private readonly wallet = inject(WalletService);
  private readonly solanaRpc = inject(SolanaRpcService);

  async loadStakeAccounts(): Promise<StakeAccount[]> {
    const pk = this.wallet.publicKey();
    if (!pk) throw new Error('Wallet not connected');
    return this.solanaRpc.getStakeAccounts(pk);
  }

  /**
   * What staking on Solana actually pays right now, before a validator's cut.
   *
   * This was written in as 6.5%. The network pays what inflation hands to
   * stakers, divided by how much of the supply is staked — measured the day
   * this replaced the constant: 3.688% inflation over a 68.8% staked share is
   * 5.36%, so 6.5% was a fifth too high, and it was multiplied by every
   * validator's commission to produce the figure people chose by.
   *
   * The result cross-checks: Jito pays 5.05% and Marinade 5.55%, and native
   * staking sitting between them is exactly right — the liquid tokens add MEV
   * and then take a fee.
   *
   * Total stake comes from the validator list already fetched, so this costs
   * two extra reads. If either fails, the caller gets null and shows no APY
   * rather than a number nobody measured.
   */
  private async networkStakingApy(rpcUrl: string, validators: any[]): Promise<number | null> {
    const call = async (method: string, params: unknown[]): Promise<any> => {
      const res = await fetch(rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include',
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
      });
      return (await res.json())?.result;
    };
    try {
      const [inflation, supply] = await Promise.all([
        call('getInflationRate', []),
        call('getSupply', [{ excludeNonCirculatingAccountsList: true }]),
      ]);
      const rate = Number(inflation?.validator);
      const totalSupply = Number(supply?.value?.total);
      const staked = validators.reduce((sum, v) => sum + Number(v?.activatedStake ?? 0), 0);
      if (!(rate > 0) || !(totalSupply > 0) || !(staked > 0)) return null;
      const stakedShare = staked / totalSupply;
      if (!(stakedShare > 0 && stakedShare <= 1)) return null;
      return (rate / stakedShare) * 100;
    } catch {
      return null;
    }
  }

  async loadTopValidators(): Promise<Validator[]> {
    const rpcUrl = environment.solanaRpc;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);

    let json: any;
    try {
      const res = await fetch(rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include',
        signal: controller.signal,
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          method: 'getVoteAccounts',
          params: [{ commitment: 'confirmed', keepUnstakedDelinquents: false }],
        }),
      });
      json = await res.json();
    } finally {
      clearTimeout(timer);
    }

    const current: any[] = json?.result?.current ?? [];
    const BASE_APY = await this.networkStakingApy(rpcUrl, current);

    return current
      .filter((v) => v.commission < 100 && (v.activatedStake ?? 0) > 0)
      .sort((a, b) => (b.activatedStake ?? 0) - (a.activatedStake ?? 0))
      .slice(0, 100)
      .map((v) => {
        const commission: number = v.commission ?? 0;
        const estimatedApy = BASE_APY === null
          ? null
          : parseFloat((BASE_APY * (1 - commission / 100)).toFixed(2));
        const activatedStakeSol = Math.round((v.activatedStake ?? 0) / LAMPORTS_PER_SOL);
        return {
          voteAccount: v.votePubkey as string,
          name: KNOWN_VALIDATORS[v.votePubkey] ?? `${(v.votePubkey as string).slice(0, 8)}…`,
          commission,
          estimatedApy,
          activatedStakeSol,
          lastVote: v.lastVote ?? 0,
        };
      });
  }

  async buildStake(validatorVoteAccount: string, amount: string): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'native_stake',
          params: { validatorVoteAccount, amount },
        })
        .pipe(timeout(30_000))
    );
  }

  async buildDeactivate(stakeAccount: string): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'native_stake_deactivate',
          params: { stakeAccount },
        })
        .pipe(timeout(30_000))
    );
  }

  async buildWithdraw(stakeAccount: string, amount = 'all'): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'native_stake_withdraw',
          params: { stakeAccount, amount },
        })
        .pipe(timeout(30_000))
    );
  }

  async buildMerge(destinationStakeAccount: string, sourceStakeAccount: string): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'native_stake_merge',
          params: { destinationStakeAccount, sourceStakeAccount },
        })
        .pipe(timeout(30_000))
    );
  }

  async signAndSubmit(transaction: string): Promise<string> {
    const web3 = await import('@solana/web3.js');
    const connection = createSolanaConnection('confirmed');

    const txBuffer = Uint8Array.from(atob(transaction), (c) => c.charCodeAt(0));
    const tx = web3.Transaction.from(txBuffer);

    // native_stake and native_stake_split are partially signed by the backend
    // (stake keypair). Replacing the blockhash invalidates those signatures.
    const hasBackendSigs = tx.signatures.some((s) => s.signature !== null);

    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('confirmed');
    if (!hasBackendSigs) {
      tx.recentBlockhash = blockhash;
    }

    const signedTx = (await this.wallet.signTransaction(tx)) as { serialize(): Uint8Array };
    const raw = signedTx.serialize();

    const signature = await connection.sendRawTransaction(raw, {
      skipPreflight: false,
      preflightCommitment: 'confirmed',
    });

    // Poll rather than subscribe: the websocket derived from our RPC proxy's
    // URL is answered with 405, so a subscription never connects.
    await awaitSignature(connection, signature);
    return signature;
  }

  formatSol(lamports: number): string {
    return (lamports / LAMPORTS_PER_SOL).toFixed(4);
  }
}
