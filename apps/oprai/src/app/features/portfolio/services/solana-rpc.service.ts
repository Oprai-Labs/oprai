import { Injectable } from '@angular/core';
import { environment } from '@env/environment';

const STAKE_PROGRAM_ID = 'Stake11111111111111111111111111111111111111';
const TOKEN_PROGRAM_ID_STR = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';

interface RpcResponse<T> {
  jsonrpc: string;
  id: number;
  result?: T;
  error?: { code: number; message: string };
}

interface RpcValueWrapper<T> {
  value: T[];
  context?: { slot: number };
}

interface ParsedTokenAccountData {
  info: {
    mint: string;
    owner: string;
    tokenAmount: {
      amount: string;
      decimals: number;
      uiAmount: number | null;
    };
  };
  type: string;
}

interface ParsedTokenAccount {
  pubkey: string;
  account: {
    data: { parsed: ParsedTokenAccountData };
    lamports: number;
  };
}

interface ParsedStakeAccountData {
  type: string;
  info?: {
    stake?: {
      delegation?: {
        voter: string;
        stake: string;
        activationEpoch: string;
        deactivationEpoch: string;
      };
    };
    meta?: { rentExemptReserve: string };
  };
}

interface ParsedStakeAccount {
  pubkey: string;
  account: {
    data: { parsed: ParsedStakeAccountData };
    lamports: number;
  };
}

interface RpcSignatureInfo {
  signature: string;
  blockTime: number | null;
  err: unknown;
  memo: string | null;
}

interface RpcPrioritizationFee {
  slot: number;
  prioritizationFee: number;
}

interface SignaturesOptions {
  limit: number;
  before?: string;
}

@Injectable({ providedIn: 'root' })
export class SolanaRpcService {
  private rpcUrl = environment.solanaRpc;
  private id = 0;

  private async rpcCall<T>(method: string, params: unknown[]): Promise<T> {
    const res = await fetch(this.rpcUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      credentials: 'include',
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: ++this.id,
        method,
        params,
      }),
    });
    const json = await res.json() as RpcResponse<T>;
    if (json.error) throw new Error(json.error.message || 'RPC error');
    return json.result as T;
  }

  async getBalance(walletAddress: string): Promise<number> {
    const result = await this.rpcCall<{ value: number }>('getBalance', [walletAddress]);
    return result?.value ?? 0;
  }

  async getTokenAccounts(walletAddress: string): Promise<
    Array<{
      mint: string;
      balance: number;
      decimals: number;
    }>
  > {
    const result = await this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
      'getTokenAccountsByOwner',
      [walletAddress, { programId: TOKEN_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
    );

    return (result?.value ?? [])
      .map((account) => {
        const info = account?.account?.data?.parsed?.info;
        if (!info) return null;
        return {
          mint: info.mint,
          balance: info.tokenAmount?.uiAmount ?? 0,
          decimals: info.tokenAmount?.decimals ?? 0,
        };
      })
      .filter((t): t is NonNullable<typeof t> => t !== null && t.balance > 0);
  }

  /**
   * Single-token balance — one RPC call, mint-filtered. Sums across multiple
   * token accounts holding the same mint (rare but legal). Returns null when
   * the wallet holds none of this mint.
   */
  async getTokenBalance(
    walletAddress: string,
    mint: string,
  ): Promise<{ balance: number; decimals: number } | null> {
    const result = await this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
      'getTokenAccountsByOwner',
      [walletAddress, { mint }, { encoding: 'jsonParsed' }],
    );
    const accounts = result?.value ?? [];
    if (accounts.length === 0) return null;
    let total = 0;
    let decimals = 0;
    for (const acct of accounts) {
      const info = acct?.account?.data?.parsed?.info;
      if (info?.tokenAmount) {
        total += info.tokenAmount.uiAmount ?? 0;
        decimals = info.tokenAmount.decimals ?? decimals;
      }
    }
    return total > 0 ? { balance: total, decimals } : null;
  }

  async getAllTokenAccounts(walletAddress: string): Promise<
    Array<{
      pubkey: string;
      mint: string;
      balance: number;
      balanceRaw: string;
      decimals: number;
      rentLamports: number;
    }>
  > {
    const result = await this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
      'getTokenAccountsByOwner',
      [walletAddress, { programId: TOKEN_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
    );

    return (result?.value ?? [])
      .map((account) => {
        const info = account?.account?.data?.parsed?.info;
        if (!info) return null;
        return {
          pubkey: account.pubkey,
          mint: info.mint,
          balance: info.tokenAmount?.uiAmount ?? 0,
          balanceRaw: info.tokenAmount?.amount ?? '0',
          decimals: info.tokenAmount?.decimals ?? 0,
          rentLamports: account?.account?.lamports ?? 2_039_280,
        };
      })
      .filter((t): t is NonNullable<typeof t> => t !== null);
  }

  async getStakeAccounts(walletAddress: string): Promise<
    Array<{
      pubkey: string;
      validatorVoteAccount: string;
      stakedLamports: number;
      totalLamports: number;
      status: 'active' | 'activating' | 'deactivating' | 'inactive';
    }>
  > {
    // Fetch accounts and current epoch in parallel.
    // currentEpoch is needed to distinguish 'deactivating' (cooldown) from 'inactive' (ready to withdraw).
    const [result, epochInfo] = await Promise.all([
      this.rpcCall<ParsedStakeAccount[]>('getProgramAccounts', [
        STAKE_PROGRAM_ID,
        {
          encoding: 'jsonParsed',
          filters: [{ memcmp: { offset: 12, bytes: walletAddress } }],
        },
      ]),
      this.rpcCall<{ epoch: number }>('getEpochInfo', []).catch(() => ({ epoch: 0 })),
    ]);

    const currentEpoch: number = (epochInfo as any)?.epoch ?? 0;
    const U64_MAX = '18446744073709551615';

    return (result ?? []).map((account) => {
      const parsed = account?.account?.data?.parsed;
      const info = parsed?.info;
      const stake = info?.stake;
      const delegation = stake?.delegation;
      const totalLamports: number = account?.account?.lamports ?? 0;

      let status: 'active' | 'activating' | 'deactivating' | 'inactive' = 'inactive';
      const stakeType = parsed?.type ?? '';
      if (stakeType === 'delegated' && delegation) {
        const deactivationEpoch: string = delegation.deactivationEpoch ?? U64_MAX;
        if (deactivationEpoch !== U64_MAX) {
          // Cooldown complete when current epoch > deactivationEpoch
          status = currentEpoch > Number(deactivationEpoch) ? 'inactive' : 'deactivating';
        } else {
          const activationEpoch = Number(delegation.activationEpoch ?? 0);
          status = currentEpoch > activationEpoch ? 'active' : 'activating';
        }
      }

      return {
        pubkey: account.pubkey,
        validatorVoteAccount: delegation?.voter ?? '',
        stakedLamports: Number(delegation?.stake ?? 0),
        totalLamports,
        status,
      };
    });
  }

  async getRecentSignatures(
    walletAddress: string,
    limit = 20,
    before?: string,
  ): Promise<
    Array<{
      signature: string;
      blockTime: number | null;
      success: boolean;
      memo: string | null;
    }>
  > {
    const opts: SignaturesOptions = { limit };
    if (before) opts.before = before;

    const result = await this.rpcCall<RpcSignatureInfo[]>('getSignaturesForAddress', [
      walletAddress,
      opts,
    ]);

    return (result ?? []).map((sig) => ({
      signature: sig.signature,
      blockTime: sig.blockTime ?? null,
      success: sig.err === null,
      memo: sig.memo ?? null,
    }));
  }

  async getRecentPriorityFeeMicroLamports(): Promise<number> {
    const result = await this.rpcCall<RpcPrioritizationFee[]>('getRecentPrioritizationFees', []);
    const values = (result ?? [])
      .map((item) => item.prioritizationFee)
      .filter((value) => Number.isFinite(value) && value >= 0)
      .sort((a, b) => a - b);
    if (values.length === 0) return 0;
    return values[Math.floor(values.length / 2)] ?? 0;
  }
}
