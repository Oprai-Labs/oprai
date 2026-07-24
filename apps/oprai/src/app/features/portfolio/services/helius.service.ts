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

    // Resolve on-chain metadata (name / symbol / logo) SERVER-SIDE via the
    // gateway's `/token-meta` endpoint, which fans out to Helius getAsset with
    // parallel goroutines and a 30m cache. Doing this client-side was the source
    // of the "logos never load" bug: the browser's ~6-connection-per-host cap
    // made per-mint getAsset calls queue behind the portfolio's other reads and
    // time out, and the fallback source (jup.ag) was intermittently
    // unresolvable via DNS. One round-trip here dodges the connection cap
    // entirely. Not wallet-gated (root-level route), but send the CSRF header +
    // cookie like every other gateway POST.
    const ids = mints.slice(0, 100);
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 15000);
      const res = await fetch(`${environment.apiBase}/token-meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include',
        signal: ctrl.signal,
        body: JSON.stringify({ mints: ids }),
      }).finally(() => clearTimeout(timer));
      if (res.ok) {
        const json = await res.json() as Record<string, { name?: string; symbol?: string; image?: string }>;
        for (const [mint, meta] of Object.entries(json ?? {})) {
          if (!meta) continue;
          out.set(mint, {
            name: meta.name ?? '',
            symbol: meta.symbol ?? '',
            logoUri: meta.image || null,
          });
        }
      }
    } catch {
      // Graceful fallback — enrichment leaves the placeholder icon in place.
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
