import { Injectable } from '@angular/core';
import { environment } from '@env/environment';
import type { HeliusAsset, HeliusAssetResponse, HeliusParsedTransaction } from '../models/helius.models';

@Injectable({ providedIn: 'root' })
export class HeliusService {
  // All Helius calls are proxied through the gateway — API key stays server-side

  async getAssetsByOwner(wallet: string): Promise<HeliusAsset[]> {
    const allAssets: HeliusAsset[] = [];
    let page = 1;
    const limit = 1000;

    try {
      while (true) {
        const response = await fetch(environment.solanaRpc, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            Authorization: `Bearer ${localStorage.getItem('oprai-auth-token') ?? ''}`,
          },
          credentials: 'include',
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: `assets-${page}`,
            method: 'getAssetsByOwner',
            params: {
              ownerAddress: wallet,
              page,
              limit,
              displayOptions: { showFungible: false, showNativeBalance: false },
            },
          }),
        });

        if (!response.ok) break;

        const json = await response.json() as { result: HeliusAssetResponse };
        const result: HeliusAssetResponse = json.result;

        if (!result?.items?.length) break;

        allAssets.push(...result.items);

        if (result.items.length < limit) break;
        page++;

        // Safety: max 5 pages
        if (page > 5) break;
      }
    } catch {
      // Graceful fallback
    }

    return allAssets;
  }

  /**
   * Resolve metadata for arbitrary mints via Helius `getAssetBatch`. This
   * reads the on-chain Metaplex metadata (or token-2022 extensions) so it
   * covers Pump.fun and any small-cap tokens that don't appear in the
   * Jupiter strict list or DexScreener cache. Returns a map keyed by mint.
   */
  async getAssetBatch(mints: string[]): Promise<Map<string, { name: string; symbol: string; logoUri: string | null }>> {
    const out = new Map<string, { name: string; symbol: string; logoUri: string | null }>();
    if (!mints.length) return out;

    // NOTE: the gateway's Helius RPC upstream returns `[null]` for the DAS
    // `getAssetBatch` method even though single `getAsset` works fine — so
    // metadata (names + logos) never resolved for pump.fun memecoins and
    // position-receipt NFTs, which then showed as "Unknown Token" with a
    // placeholder icon. Resolve each mint individually via `getAsset` instead
    // (capped concurrency). The auth cookie rides along via credentials:'include';
    // the old empty `Authorization: Bearer` header was a no-op (the JWT lives in
    // memory, not localStorage) and /rpc reads aren't wallet-gated.
    const ids = mints.slice(0, 100);
    const extract = (it: any): { name: string; symbol: string; logoUri: string | null } | null => {
      if (!it?.id) return null;
      const meta = it.content?.metadata ?? {};
      const links = it.content?.links ?? {};
      const files = it.content?.files ?? [];
      const logoUri =
        links.image ??
        files.find((f: any) => f?.uri && /image|png|jpg|webp|svg/i.test(f?.mime ?? f?.uri))?.uri ??
        files[0]?.uri ??
        null;
      return { name: meta.name ?? '', symbol: meta.symbol ?? '', logoUri };
    };

    const CONCURRENCY = 8;
    for (let i = 0; i < ids.length; i += CONCURRENCY) {
      const slice = ids.slice(i, i + CONCURRENCY);
      await Promise.all(slice.map(async (mint) => {
        try {
          const res = await fetch(environment.solanaRpc, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'include',
            body: JSON.stringify({ jsonrpc: '2.0', id: 'asset', method: 'getAsset', params: { id: mint } }),
          });
          if (!res.ok) return;
          const json = await res.json() as { result?: any };
          const parsed = extract(json.result);
          if (parsed) out.set(mint, parsed);
        } catch {
          // Per-mint blip — caller falls back to existing partial metadata.
        }
      }));
    }

    return out;
  }

  async parseTransactions(signatures: string[]): Promise<HeliusParsedTransaction[]> {
    if (signatures.length === 0) return [];

    const results: HeliusParsedTransaction[] = [];

    try {
      const batchSize = 100;
      const batches: string[][] = [];
      for (let i = 0; i < signatures.length; i += batchSize) {
        batches.push(signatures.slice(i, i + batchSize));
      }

      const responses = await Promise.all(
        batches.map(async (batch) => {
          const response = await fetch(`${environment.apiBase}/market/helius/transactions`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${localStorage.getItem('oprai-auth-token') ?? ''}`,
            },
            body: JSON.stringify({ transactions: batch }),
          });
          if (!response.ok) return [] as HeliusParsedTransaction[];
          const parsed: HeliusParsedTransaction[] = await response.json() as HeliusParsedTransaction[];
          return Array.isArray(parsed) ? parsed : [];
        })
      );

      for (const parsed of responses) {
        results.push(...parsed);
      }
    } catch {
      // Graceful fallback
    }

    return results;
  }
}
