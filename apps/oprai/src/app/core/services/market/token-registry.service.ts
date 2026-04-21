import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

export interface TokenMeta {
  address: string;
  symbol: string;
  name: string;
  decimals: number;
  logoURI: string | null;
  tags?: string[];
}

const TOKEN_LIST_TTL = 60 * 60 * 1000; // 1 hour

@Injectable({ providedIn: 'root' })
export class TokenRegistryService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;

  private tokenMap = new Map<string, TokenMeta>();
  private symbolIndex = new Map<string, TokenMeta>();
  private loaded = false;
  private loading: Promise<void> | null = null;
  private loadedAt = 0;

  /** Load the Jupiter verified token list. Safe to call multiple times. */
  async ensureLoaded(): Promise<void> {
    if (this.loaded && Date.now() - this.loadedAt < TOKEN_LIST_TTL) return;
    if (this.loading) return this.loading;
    this.loading = this.loadTokenList();
    await this.loading;
    this.loading = null;
  }

  private async loadTokenList(): Promise<void> {
    try {
      const tokens = await firstValueFrom(
        this.http.get<any[]>(`${this.apiBase}/market/tokens/strict`)
      );
      this.tokenMap.clear();
      this.symbolIndex.clear();
      for (const t of tokens) {
        const meta: TokenMeta = {
          address: t.address,
          symbol: t.symbol,
          name: t.name,
          decimals: t.decimals ?? 9,
          logoURI: t.logoURI ?? null,
          tags: t.tags,
        };
        this.tokenMap.set(t.address, meta);
        // Store by symbol (first wins for duplicates)
        if (!this.symbolIndex.has(t.symbol?.toUpperCase())) {
          this.symbolIndex.set(t.symbol?.toUpperCase(), meta);
        }
      }
      this.loaded = true;
      this.loadedAt = Date.now();
    } catch (err) {
      console.error('Failed to load token list:', err);
    }
  }

  /** Get token metadata by mint address. */
  getToken(mint: string): TokenMeta | null {
    return this.tokenMap.get(mint) ?? null;
  }

  /** Get token by symbol (e.g., "SOL", "USDC"). */
  getBySymbol(symbol: string): TokenMeta | null {
    return this.symbolIndex.get(symbol?.toUpperCase()) ?? null;
  }

  /** Search tokens by query (symbol or name). Returns top matches. */
  searchTokens(query: string, limit = 20): TokenMeta[] {
    if (!query || query.length < 1) return [];
    const q = query.toLowerCase();
    const results: TokenMeta[] = [];
    for (const meta of this.tokenMap.values()) {
      if (results.length >= limit) break;
      if (
        meta.symbol?.toLowerCase().includes(q) ||
        meta.name?.toLowerCase().includes(q)
      ) {
        results.push(meta);
      }
    }
    // Sort: exact symbol match first, then by symbol length
    results.sort((a, b) => {
      const aExact = a.symbol?.toLowerCase() === q ? 0 : 1;
      const bExact = b.symbol?.toLowerCase() === q ? 0 : 1;
      if (aExact !== bExact) return aExact - bExact;
      return (a.symbol?.length ?? 99) - (b.symbol?.length ?? 99);
    });
    return results;
  }

  /** Get logo URL for a mint, with fallback chain. */
  getLogoUrl(mint: string): string | null {
    const meta = this.tokenMap.get(mint);
    return meta?.logoURI ?? null;
  }

  /** Get all loaded tokens. */
  getAll(): TokenMeta[] {
    return Array.from(this.tokenMap.values());
  }

  /** Number of loaded tokens. */
  get count(): number {
    return this.tokenMap.size;
  }
}
