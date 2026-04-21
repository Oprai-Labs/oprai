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
            Authorization: `Bearer ${localStorage.getItem('oprai-auth-token') ?? ''}`,
          },
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
