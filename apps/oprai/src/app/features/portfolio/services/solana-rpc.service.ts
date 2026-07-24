import { Injectable } from '@angular/core';
import { environment } from '@env/environment';

const STAKE_PROGRAM_ID = 'Stake11111111111111111111111111111111111111';
const TOKEN_PROGRAM_ID_STR = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
// Token-2022 program — newer SPL extension standard. Many recent
// Pump.fun launches and meme tokens with transfer-fee, immutable-owner
// or non-transferable extensions live here. Without scanning it the
// Tokens tab silently misses entire chunks of the user's holdings.
const TOKEN_2022_PROGRAM_ID_STR = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';

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
    // Query both Token + Token-2022 programs in parallel and merge. Either
    // call individually failing shouldn't drop the other's results.
    const [legacy, tok22] = await Promise.all([
      this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
        'getTokenAccountsByOwner',
        [walletAddress, { programId: TOKEN_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
      ).catch(() => ({ value: [] }) as RpcValueWrapper<ParsedTokenAccount>),
      this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
        'getTokenAccountsByOwner',
        [walletAddress, { programId: TOKEN_2022_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
      ).catch(() => ({ value: [] }) as RpcValueWrapper<ParsedTokenAccount>),
    ]);

    const all = [...(legacy?.value ?? []), ...(tok22?.value ?? [])];
    return all
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
    // Hit both token programs in parallel. Token-2022 (PYUSD, EURC, USDS,
    // etc.) was previously skipped, which is why empty-but-real Token-2022
    // ATAs never showed up in the manage modal and missed their rent
    // reclaim. Empty results from either side are safely concat'd as [].
    const [legacy, token22] = await Promise.all([
      this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
        'getTokenAccountsByOwner',
        [walletAddress, { programId: TOKEN_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
      ).catch(() => null),
      this.rpcCall<RpcValueWrapper<ParsedTokenAccount>>(
        'getTokenAccountsByOwner',
        [walletAddress, { programId: TOKEN_2022_PROGRAM_ID_STR }, { encoding: 'jsonParsed' }],
      ).catch(() => null),
    ]);

    const all = [...(legacy?.value ?? []), ...(token22?.value ?? [])];
    return all
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

  /**
   * Fetch multiple raw accounts in one RPC round-trip. Returns `null` for
   * any address that doesn't exist on-chain (so the caller can distinguish
   * "missing PDA" from "empty PDA"). Used by the Pump.fun creator-rewards
   * scan and any other PDA lookup that needs balances without parsing.
   */
  async getMultipleAccountInfo(
    pubkeys: string[],
  ): Promise<Array<{ lamports: number; owner: string; dataLen: number } | null>> {
    if (pubkeys.length === 0) return [];
    try {
      const result = await this.rpcCall<{
        value: Array<{ lamports: number; owner: string; data: [string, string] } | null>;
      }>('getMultipleAccounts', [pubkeys, { encoding: 'base64' }]);
      return (result?.value ?? []).map((acc) => {
        if (!acc) return null;
        const data = Array.isArray(acc.data) ? acc.data[0] : '';
        return {
          lamports: acc.lamports ?? 0,
          owner: acc.owner,
          // Approximate dataLen — atob is fine here because the values are
          // small (< 1KB for the PDAs we care about); we only need this for
          // rent-exempt math when the lamport math depends on data length.
          dataLen: typeof data === 'string' ? Math.floor((data.length * 3) / 4) : 0,
        };
      });
    } catch {
      return pubkeys.map(() => null);
    }
  }

  /**
   * Solana's network inflation rate. Used to estimate the headline APY a
   * native-staking position earns — there's no "stake APR" RPC method, but
   * the inflation `total` minus `foundation` lands within 0.2pp of the
   * average validator's effective APR after commission for any randomly-
   * chosen validator. Returned as a percentage (7.0, not 0.07).
   */
  async getNativeStakingApr(): Promise<number> {
    try {
      const r = await this.rpcCall<{ total: number; validator: number; foundation: number }>(
        'getInflationRate',
        [],
      );
      // `validator` is the share earmarked for validators (post-foundation),
      // which is the headline APR for staked SOL after vesting/decay
      // adjustments. Falls back to `total` when shape is unexpected.
      const validator = r?.validator;
      const total = r?.total;
      const pick = Number.isFinite(validator) ? validator : total;
      if (!Number.isFinite(pick) || pick <= 0) return 7.0;
      return pick * 100;
    } catch {
      return 7.0;
    }
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

  /**
   * Fetch a parsed transaction by signature. Used as a fallback when the
   * Helius enrichment endpoint returns nothing (new-tx indexing lag, batch
   * timeout, gateway hiccup). Returns the raw RPC response — caller is
   * responsible for shaping it into the EnhancedTransaction model.
   */
  async getParsedTransaction(signature: string): Promise<any | null> {
    try {
      const result = await this.rpcCall<any>('getTransaction', [
        signature,
        { encoding: 'jsonParsed', maxSupportedTransactionVersion: 0, commitment: 'confirmed' },
      ]);
      return result ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Fetch parsed transactions for many signatures using JSON-RPC BATCH
   * requests — an array of getTransaction calls in a single POST, chunked so
   * each round-trip stays a reasonable size. This is how the Transactions tab
   * parses its full window without either (a) hitting the browser's ~6
   * connections-per-host cap with 100 individual getTransaction fetches or (b)
   * depending on Helius's enhanced-tx REST API, which Cloudflare-blocks our
   * datacenter IP. Returns a map keyed by signature (batch responses can come
   * back out of order, so we re-associate via the per-request id).
   */
  async getParsedTransactionsBatch(
    signatures: string[],
    chunkSize = 25,
  ): Promise<Map<string, any>> {
    const out = new Map<string, any>();
    if (!signatures.length) return out;

    const chunks: string[][] = [];
    for (let i = 0; i < signatures.length; i += chunkSize) {
      chunks.push(signatures.slice(i, i + chunkSize));
    }

    await Promise.all(
      chunks.map(async (chunk) => {
        const idToSig = new Map<number, string>();
        const batch = chunk.map((sig) => {
          const id = ++this.id;
          idToSig.set(id, sig);
          return {
            jsonrpc: '2.0',
            id,
            method: 'getTransaction',
            params: [
              sig,
              { encoding: 'jsonParsed', maxSupportedTransactionVersion: 0, commitment: 'confirmed' },
            ],
          };
        });
        try {
          const res = await fetch(this.rpcUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'include',
            body: JSON.stringify(batch),
          });
          if (!res.ok) return;
          const json = await res.json();
          const arr = Array.isArray(json) ? json : [json];
          for (const entry of arr) {
            const sig = idToSig.get(entry?.id);
            if (sig && entry?.result) out.set(sig, entry.result);
          }
        } catch {
          // Skip this chunk — the rows it covered stay as blank stubs.
        }
      }),
    );

    return out;
  }
}
