import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface TokenHolder {
  address: string;
  owner: string;
  amount: number;
  decimals: number;
  uiAmount: number;
  percentage: number;
}

export interface HolderData {
  totalHolders: number;
  topHolders: TokenHolder[];
}

@Injectable({ providedIn: 'root' })
export class TokenHoldersService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;
  private readonly cache = new CacheStore<HolderData>(120_000);

  async getHolders(mint: string): Promise<HolderData> {
    const cacheKey = `holders:${mint}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;

    try {
      interface RawAccount { address?: string; owner?: string; amount?: string; decimals?: number }
      interface RawHoldersResp { result?: { token_accounts?: RawAccount[]; total?: number } | RawAccount[] }
      const resp = await firstValueFrom(
        this.http.get<RawHoldersResp>(`${this.apiBase}/market/holders/${mint}`)
      );

      const resultVal = resp?.result;
      const accounts: RawAccount[] = Array.isArray(resultVal)
        ? resultVal
        : resultVal?.token_accounts ?? [];
      let totalSupply = 0;
      const holders: TokenHolder[] = accounts.map((a) => {
        const amount = parseFloat(a.amount ?? '0');
        const decimals = a.decimals ?? 0;
        const uiAmount = amount / Math.pow(10, decimals);
        totalSupply += uiAmount;
        return {
          address: a.address ?? '',
          owner: a.owner ?? '',
          amount,
          decimals,
          uiAmount,
          percentage: 0,
        };
      });

      // Calculate percentages
      if (totalSupply > 0) {
        holders.forEach(h => {
          h.percentage = (h.uiAmount / totalSupply) * 100;
        });
      }

      // Sort by amount descending
      holders.sort((a, b) => b.uiAmount - a.uiAmount);

      const data: HolderData = {
        totalHolders: (!Array.isArray(resultVal) ? resultVal?.total : undefined) ?? holders.length,
        topHolders: holders.slice(0, 20),
      };

      this.cache.set(cacheKey, data);
      return data;
    } catch (err) {
      console.error('Holders fetch error:', err);
      return { totalHolders: 0, topHolders: [] };
    }
  }
}
