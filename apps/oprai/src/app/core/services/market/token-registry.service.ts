import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { VERIFIED_TOKENS } from './tokens.generated';

export interface TokenMeta {
  address: string;
  symbol: string;
  name: string;
  decimals: number;
  logoURI: string | null;
  tags?: string[];
}

const TOKEN_LIST_TTL = 60 * 60 * 1000; // 1 hour

// Solana base-58 address: 32–44 alphanumeric chars (no I, O, l, 0)
const SOLANA_ADDR_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

// Pinned display names — Jupiter API returns "Wrapped SOL" for the native SOL mint,
// which is technically correct but confusing. Pin these so API responses can't overwrite.
const PINNED_NAMES: Record<string, string> = {
  'So11111111111111111111111111111111111111112': 'SOL',
};

// Bootstrap: most-used tokens available immediately, no API call needed.
// Source: shared/tokens.json (re-exported via tokens.generated.ts). Each entry
// is cross-checked against the Jupiter token API in CI by scripts/verify-tokens.mjs,
// which catches mint typos and vanity-prefix collisions before they ship.
const BOOTSTRAP_TOKENS: TokenMeta[] = VERIFIED_TOKENS.map((t) => ({
  address: t.address,
  symbol: t.symbol,
  name: t.name,
  decimals: t.decimals,
  logoURI: t.logoURI,
  tags: t.tags ? [...t.tags] : undefined,
}));

@Injectable({ providedIn: 'root' })
export class TokenRegistryService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;

  private tokenMap = new Map<string, TokenMeta>();
  private symbolIndex = new Map<string, TokenMeta>();
  private loaded = false;
  private loading: Promise<void> | null = null;
  private loadedAt = 0;

  // Tracks in-flight mint fetches to avoid duplicate requests.
  private readonly fetching = new Set<string>();

  // Incremented whenever a new token is added — components read this signal
  // to create a reactive dependency and re-render when metadata arrives.
  readonly version = signal(0);

  constructor() {
    for (const t of BOOTSTRAP_TOKENS) {
      this.tokenMap.set(t.address, t);
      this.symbolIndex.set(t.symbol.toUpperCase(), t);
    }
  }

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
      for (const t of tokens) {
        const meta: TokenMeta = {
          address: t.address ?? t.id,
          symbol: t.symbol,
          name: t.name,
          decimals: t.decimals ?? 9,
          logoURI: t.icon ?? t.logoURI ?? null,
          tags: t.tags,
        };
        this.tokenMap.set(meta.address, meta);
        if (!this.symbolIndex.has(t.symbol?.toUpperCase())) {
          this.symbolIndex.set(t.symbol?.toUpperCase(), meta);
        }
      }
      this.loaded = true;
      this.loadedAt = Date.now();
      this.version.update(v => v + 1);
    } catch {
      // Silent — bootstrap tokens remain available
    }
  }

  private _bootstrapLoading = false;

  /**
   * Bulk-fetch logos for all bootstrap tokens.
   * Called lazily on first resolveAsync for a bootstrap token.
   * Tries gateway first (authenticated, cached), falls back to Jupiter directly.
   */
  private _loadBootstrapLogos(): void {
    if (this._bootstrapLoading) return;
    this._bootstrapLoading = true;

    const mints = BOOTSTRAP_TOKENS.map(t => t.address).join(',');
    const limit = BOOTSTRAP_TOKENS.length;

    const apply = (tokens: any[]) => {
      if (!Array.isArray(tokens)) return;
      for (const t of tokens) {
        const mint = t.id ?? t.address ?? t.mint;
        if (!mint) continue;
        const meta = this._parseJupiterToken(t, mint);
        if (meta) this._store(meta);
      }
    };

    this.http
      .get<any[]>(`${this.apiBase}/market/tokens/search?q=${mints}&limit=${limit}`)
      .subscribe({
        next: apply,
        error: () => {
          // Fallback: call Jupiter V2 directly from browser (CORS allowed from localhost)
          fetch(`https://api.jup.ag/tokens/v2/search?query=${mints}&limit=${limit}`)
            .then(r => r.ok ? r.json() : null)
            .then(tokens => { if (tokens) apply(tokens); })
            .catch(() => {});
        },
      });
  }

  /**
   * Fire-and-forget: fetch metadata for an unknown mint address.
   * Updates the registry and bumps `version` when done, causing
   * any component that reads `version()` to re-render automatically.
   */
  resolveAsync(mint: string): void {
    if (!mint || !SOLANA_ADDR_RE.test(mint)) return;
    if (this.tokenMap.get(mint)?.logoURI) return; // already has logo
    if (this.fetching.has(mint)) return;

    // Bootstrap tokens: trigger a single bulk fetch instead of per-token requests
    const isBootstrap = BOOTSTRAP_TOKENS.some(t => t.address === mint);
    if (isBootstrap) {
      this._loadBootstrapLogos();
      return;
    }

    // Unknown token: create stub and fetch individually
    if (!this.tokenMap.has(mint)) {
      this.tokenMap.set(mint, { address: mint, symbol: mint.slice(0, 5) + '…', name: mint, decimals: 9, logoURI: null });
      this.version.update(v => v + 1);
    }

    this.fetching.add(mint);
    this._fetchMeta(mint).finally(() => this.fetching.delete(mint));
  }

  /**
   * Resolve a token's metadata (decimals / logo), fetching from the gateway or
   * Jupiter if we only have a stub. Unlike `resolveAsync` (fire-and-forget for
   * templates), this awaits so callers that need real decimals (e.g. converting
   * a UI amount to base units for a quote) get an accurate value.
   */
  async resolveTokenMeta(mint: string): Promise<TokenMeta | null> {
    if (!mint || !SOLANA_ADDR_RE.test(mint)) return null;
    const existing = this.tokenMap.get(mint);
    // A resolved token has a logo; stubs created by resolveAsync have logoURI=null.
    if (existing?.logoURI) return existing;
    await this._fetchMeta(mint).catch(() => {});
    return this.tokenMap.get(mint) ?? existing ?? null;
  }

  private async _fetchMeta(mint: string): Promise<void> {
    // 1. Try authenticated gateway (server-side cache, no CORS issue)
    try {
      const data = await firstValueFrom(
        this.http.get<any>(`${this.apiBase}/market/tokens/${mint}`)
      );
      const meta = this._parseJupiterToken(data, mint);
      if (meta) { this._store(meta); return; }
    } catch { /* fall through */ }

    // 2. Try Jupiter V2 search API directly from browser
    try {
      const data = await fetch(`https://api.jup.ag/tokens/v2/search?query=${mint}&limit=1`)
        .then(r => r.ok ? r.json() : null);
      const meta = this._parseJupiterToken(data, mint);
      if (meta) { this._store(meta); return; }
    } catch { /* fall through */ }

    // 3. On-chain Metaplex metadata via Helius DAS (proxied through the gateway,
    //    API key server-side). This is the ONLY source that resolves a token
    //    minted seconds ago — freshly-launched pump.fun / small-cap tokens
    //    aren't in the Jupiter list yet, so without this they'd forever display
    //    as the raw mint address.
    try {
      const res = await fetch(environment.solanaRpc, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          Authorization: `Bearer ${localStorage.getItem('oprai-auth-token') ?? ''}`,
        },
        credentials: 'include',
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: 'asset-meta',
          method: 'getAsset',
          params: { id: mint, displayOptions: { showFungible: true } },
        }),
      });
      if (res.ok) {
        const json = await res.json() as { result?: any };
        const meta = this._parseHeliusAsset(json.result, mint);
        if (meta) { this._store(meta); return; }
      }
    } catch { /* give up — caller keeps the stub */ }
  }

  /** Parse a Helius DAS `getAsset` result into TokenMeta. Reads the on-chain
   *  Metaplex metadata (name/symbol) + linked image; used as the last-resort
   *  fallback for tokens absent from the Jupiter registry (pump.fun, brand-new
   *  mints). Returns null if the asset carries no usable name or symbol. */
  private _parseHeliusAsset(asset: any, mint: string): TokenMeta | null {
    if (!asset?.id) return null;
    const md = asset.content?.metadata ?? {};
    const links = asset.content?.links ?? {};
    const files = asset.content?.files ?? [];
    const symbol = (md.symbol ?? '').trim();
    const name = (md.name ?? '').trim();
    if (!symbol && !name) return null;
    const logoURI =
      links.image ??
      files.find((f: any) => f?.uri && /image|png|jpe?g|webp|svg|gif/i.test(f?.mime ?? f?.uri))?.uri ??
      files[0]?.uri ??
      null;
    return {
      address: mint,
      symbol: symbol || name || mint.slice(0, 6),
      name: name || symbol || mint,
      decimals: asset.token_info?.decimals ?? 9,
      logoURI,
    };
  }

  private _parseJupiterToken(data: any, mint: string): TokenMeta | null {
    if (!data) return null;
    // V2 search returns an array directly; v1/gateway returns object or { tokens: [...] }
    const t = Array.isArray(data) ? data[0] : (Array.isArray(data?.tokens) ? data.tokens[0] : data);
    if (!t) return null;
    const address = t.id ?? t.address ?? t.mint ?? mint;
    if (!address) return null;
    return {
      address,
      symbol: t.symbol ?? mint.slice(0, 6),
      name: t.name ?? t.symbol ?? mint,
      decimals: t.decimals ?? 9,
      logoURI: t.icon ?? t.logoURI ?? t.logo ?? null,
      tags: t.tags,
    };
  }

  private _store(meta: TokenMeta): void {
    const pinned = PINNED_NAMES[meta.address];
    if (pinned) meta = { ...meta, name: pinned };
    this.tokenMap.set(meta.address, meta);
    // Always update symbolIndex so lookups by symbol reflect the latest logo/metadata.
    // Guard only against overwriting a *different* token's symbol (address mismatch).
    const existing = this.symbolIndex.get(meta.symbol?.toUpperCase());
    if (!existing || existing.address === meta.address) {
      this.symbolIndex.set(meta.symbol.toUpperCase(), meta);
    }
    this.version.update(v => v + 1);
  }

  /** Get token metadata by mint address. */
  getToken(mint: string): TokenMeta | null {
    return this.tokenMap.get(mint) ?? null;
  }

  /** Get token by symbol (e.g., "SOL", "USDC"). */
  getBySymbol(symbol: string): TokenMeta | null {
    return this.symbolIndex.get(symbol?.toUpperCase()) ?? null;
  }

  /**
   * Stablecoin check that combines three signals so we don't depend on
   * Jupiter's tagging being complete:
   *   1. Registry tag list contains "stable"/"stablecoin"  (Jupiter + bootstrap)
   *   2. Symbol matches the USD-stable shape (USDC/USDT/USDS/PYUSD/...)
   *      i.e. starts or ends with USD, with optional 1-2 char prefix/suffix
   *   3. Name contains "USD" or "Tether" or "Dollar"
   * Pass a mint address OR a symbol. New stables with USD-shaped symbols are
   * detected automatically without touching the bootstrap list.
   */
  isStable(addressOrSymbol: string): boolean {
    if (!addressOrSymbol) return false;
    const meta = SOLANA_ADDR_RE.test(addressOrSymbol)
      ? this.getToken(addressOrSymbol)
      : this.getBySymbol(addressOrSymbol);
    const tags = meta?.tags ?? [];
    if (tags.some((t) => {
      const v = String(t).toLowerCase();
      return v === 'stablecoin' || v === 'stable';
    })) return true;
    const symbol = (meta?.symbol ?? (SOLANA_ADDR_RE.test(addressOrSymbol) ? '' : addressOrSymbol)).toUpperCase();
    if (/^[A-Z]{0,2}USD[A-Z]{0,2}$/.test(symbol)) return true;
    const name = (meta?.name ?? '').toLowerCase();
    return /\b(usd|tether|dollar)\b/.test(name);
  }

  /**
   * Liquid Staking Token check, same multi-signal shape as `isStable`:
   *   1. Tag list contains "lst" / "liquid-staking" / "lsd"
   *   2. Symbol matches the SOL-staked shape (jitoSOL, jupSOL, mSOL, bSOL,
   *      INF, ezSOL, vSOL, hSOL, …) — ends in "SOL" with 1–4 char prefix,
   *      OR is the bare "INF" stake token
   *   3. Name contains "liquid stak" / "staked SOL"
   * SOL itself is NOT an LST.
   */
  isLst(addressOrSymbol: string): boolean {
    if (!addressOrSymbol) return false;
    const meta = SOLANA_ADDR_RE.test(addressOrSymbol)
      ? this.getToken(addressOrSymbol)
      : this.getBySymbol(addressOrSymbol);
    const tags = meta?.tags ?? [];
    if (tags.some(t => {
      const v = String(t).toLowerCase();
      return v === 'lst' || v === 'liquid-staking' || v === 'lsd' || v === 'liquid_staking';
    })) return true;
    const symbol = (meta?.symbol ?? (SOLANA_ADDR_RE.test(addressOrSymbol) ? '' : addressOrSymbol)).toUpperCase();
    if (symbol === 'SOL' || symbol === 'WSOL') return false; // native, not LST
    if (symbol === 'INF') return true;
    // Matches jitoSOL, jupSOL, mSOL, bSOL, ezSOL, vSOL, hSOL, etc.
    if (/^[A-Z]{1,4}SOL$/.test(symbol)) return true;
    const name = (meta?.name ?? '').toLowerCase();
    return /liquid stak|staked sol/.test(name);
  }

  /**
   * List all loaded tokens, optionally filtered to a category and/or a free-text
   * query (matches against symbol or name). Used by the token picker modal in
   * the action-card swap form. Sorted by name for stable display order.
   */
  listByCategory(
    category: 'all' | 'stable' | 'lst' = 'all',
    query?: string,
  ): TokenMeta[] {
    const q = (query ?? '').trim().toLowerCase();
    const out: TokenMeta[] = [];
    for (const meta of this.tokenMap.values()) {
      if (category === 'stable' && !this.isStable(meta.address)) continue;
      if (category === 'lst' && !this.isLst(meta.address)) continue;
      if (q) {
        const sym = (meta.symbol ?? '').toLowerCase();
        const nm = (meta.name ?? '').toLowerCase();
        if (!sym.includes(q) && !nm.includes(q) && !meta.address.toLowerCase().includes(q)) {
          continue;
        }
      }
      out.push(meta);
    }
    out.sort((a, b) => {
      // Exact symbol match first when querying, else alphabetical by symbol
      if (q) {
        const aExact = a.symbol?.toLowerCase() === q ? 0 : 1;
        const bExact = b.symbol?.toLowerCase() === q ? 0 : 1;
        if (aExact !== bExact) return aExact - bExact;
      }
      return (a.symbol ?? '').localeCompare(b.symbol ?? '');
    });
    return out;
  }

  /**
   * Batch-fetch liquidity (USD) for a list of mints. Hits the gateway's
   * `/market/liquidity/multi` proxy → Birdeye `/defi/v3/token/market-data/multiple`.
   * Returns a `Map<mint, liquidityUsd>`; missing mints map to `null` so the
   * caller can render a "—" badge instead of "$0" (which reads as dead).
   *
   * Used by the token-picker modal so each row shows a $-volume badge BEFORE
   * the user picks — pre-empts the "I picked a token with no pool" case.
   * 5-minute server-side cache means rapid picker open/close is free.
   */
  async getLiquidities(mints: string[]): Promise<Map<string, number | null>> {
    const out = new Map<string, number | null>();
    if (mints.length === 0) return out;
    // Cap at Birdeye's documented batch limit. Caller can call multiple times
    // for huge sets — token-picker categories are well under 50.
    const ids = mints.slice(0, 50).join(',');
    try {
      const resp = await firstValueFrom(
        this.http.get<{ data?: Record<string, { liquidity?: number } | null> } | Record<string, any>>(
          `${this.apiBase}/market/liquidity/multi`,
          { params: { ids } },
        )
      );
      // Birdeye payload shape: { data: { <mint>: { liquidity, price, ... }, ... } }
      // Some proxies / chains wrap it differently; read defensively.
      const data = (resp as any)?.data ?? (resp as any);
      if (data && typeof data === 'object') {
        for (const mint of mints) {
          const row = data[mint];
          const liq = row?.liquidity;
          out.set(mint, typeof liq === 'number' && Number.isFinite(liq) ? liq : null);
        }
      } else {
        for (const mint of mints) out.set(mint, null);
      }
    } catch {
      // Failure → mark every mint as unknown rather than zero. The picker
      // hides the badge in that case rather than implying "dead pool".
      for (const mint of mints) out.set(mint, null);
    }
    return out;
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
    results.sort((a, b) => {
      const aExact = a.symbol?.toLowerCase() === q ? 0 : 1;
      const bExact = b.symbol?.toLowerCase() === q ? 0 : 1;
      if (aExact !== bExact) return aExact - bExact;
      return (a.symbol?.length ?? 99) - (b.symbol?.length ?? 99);
    });
    return results;
  }

  /** Get logo URL for a mint. */
  getLogoUrl(mint: string): string | null {
    return this.tokenMap.get(mint)?.logoURI ?? null;
  }

  /** Get all loaded tokens. */
  getAll(): TokenMeta[] {
    return Array.from(this.tokenMap.values());
  }

  get count(): number {
    return this.tokenMap.size;
  }
}
