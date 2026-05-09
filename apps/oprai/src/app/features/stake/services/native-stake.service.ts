import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import { WalletService } from '@core/services/wallet.service';
import { SolanaRpcService } from '../../portfolio/services/solana-rpc.service';
import { environment } from '../../../../environments/environment';

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
  estimatedApy: number;
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
    const BASE_APY = 6.5; // approximate Solana network APY %

    return current
      .filter((v) => v.commission < 100 && (v.activatedStake ?? 0) > 0)
      .sort((a, b) => (b.activatedStake ?? 0) - (a.activatedStake ?? 0))
      .slice(0, 100)
      .map((v) => {
        const commission: number = v.commission ?? 0;
        const estimatedApy = parseFloat((BASE_APY * (1 - commission / 100)).toFixed(2));
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
    const connection = new web3.Connection(environment.solanaRpc, { commitment: 'confirmed', httpHeaders: { 'X-Requested-With': 'XMLHttpRequest' } });

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

    await connection.confirmTransaction({ signature, blockhash, lastValidBlockHeight }, 'confirmed');
    return signature;
  }

  formatSol(lamports: number): string {
    return (lamports / LAMPORTS_PER_SOL).toFixed(4);
  }
}
