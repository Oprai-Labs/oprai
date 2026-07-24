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
    // Store the RAW metadata image URL (twimg/IPFS/arweave). The <img> tries it
    // directly, and the component's onImageError retries through an image proxy
    // (weserv) if the browser blocks/hotlink-rejects it — so we don't
    // double-proxy and a failed proxy URL can still fall back to a placeholder.
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

    const fetchOne = async (mint: string): Promise<void> => {
      // Two attempts with a hard timeout each: a hung/transient getAsset must
      // never stall the awaited enrichment nor leave the logo unresolved.
      for (let attempt = 0; attempt < 2; attempt++) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 8000);
        try {
          const res = await fetch(environment.solanaRpc, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'include',
            signal: ctrl.signal,
            body: JSON.stringify({ jsonrpc: '2.0', id: 'asset', method: 'getAsset', params: { id: mint } }),
          });
          if (res.ok) {
            const json = await res.json() as { result?: any };
            const parsed = extract(json.result);
            if (parsed) { out.set(mint, parsed); return; }
          }
        } catch {
          // fall through to retry
        } finally {
          clearTimeout(timer);
        }
        if (attempt === 0) await new Promise(r => setTimeout(r, 300));
      }
    };

    const CONCURRENCY = 6;
    for (let i = 0; i < ids.length; i += CONCURRENCY) {
      await Promise.all(ids.slice(i, i + CONCURRENCY).map(fetchOne));
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
              // The gateway 403s without X-Requested-With (CSRF) and 401s without
              // the auth cookie. The old empty Bearer from localStorage (the JWT
              // is in memory, not localStorage) meant this call ALWAYS 403'd, so
              // Helius never parsed a single tx — every row fell back to a bare
              // 'ACTION' stub. Send the CSRF header + cookie like every other
              // gateway call.
              'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'include',
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
