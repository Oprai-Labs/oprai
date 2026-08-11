import { Injectable } from '@angular/core';

/**
 * Live yield lookups sourced from DefiLlama's public yields API
 * (`https://yields.llama.fi/pools`). The endpoint is unauthenticated and
 * returns ~3700 pools across every chain; we filter to Solana once and
 * keep an in-memory map of `mint → apy` and `project → apy` for 5 minutes.
 *
 * Why not hit individual protocol APIs (Jito kobe, Sanctum, Marinade)?
 * One round-trip covers every LST + every lend market in our portfolio
 * carries the project name we use to match against our internal protocolId.
 *
 * Values are returned as percentages (5.66 not 0.0566) — matches the rest
 * of the codebase.
 */

interface DefiLlamaPool {
  chain: string;
  project: string;
  symbol: string;
  apy: number | null;
  apyBase: number | null;
  apyReward: number | null;
  tvlUsd: number;
  // Common when present, missing for most LSTs. Lending pools carry an
  // `underlyingTokens[]` array with the deposit mint.
  underlyingTokens?: string[];
  // LST mint hidden in the symbol-keyed pool; some pools include a `pool`
  // field that is the mint address itself.
  pool?: string;
}

// Hardcoded mint mapping for LSTs — DefiLlama doesn't return mint addresses
// in a structured field, so we resolve via project name. The map is small,
// curated and never grows in the hot path.
const LST_MINT_TO_PROJECT: Record<string, string> = {
  // jitoSOL → jito-liquid-staking
  'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn': 'jito-liquid-staking',
  // jupSOL → jupiter-staked-sol
  'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v': 'jupiter-staked-sol',
  // mSOL → marinade-liquid-staking
  'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So': 'marinade-liquid-staking',
  // bSOL → blazestake (sometimes listed as blaze-stake)
  'bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1': 'blazestake',
  // hSOL → helius-staked-sol
  'he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A': 'helius-staked-sol',
  // INF → infinity (sanctum-infinity pool)
  '5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm': 'sanctum-infinity',
  // stSOL → lido (Lido shutdown but pool may linger)
  '7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj': 'lido',
};

const LLAMA_URL = 'https://yields.llama.fi/pools';
const CACHE_TTL_MS = 5 * 60_000;

interface SnapshotCache {
  apyByMint: Map<string, number>;
  apyByProject: Map<string, number>;
  fetchedAt: number;
}

@Injectable({ providedIn: 'root' })
export class LiveYieldsService {
  private cache: SnapshotCache | null = null;
  private inflight: Promise<SnapshotCache> | null = null;

  /**
   * Ensure the live yields snapshot is fresh. Returns the snapshot (never
   * throws — on failure returns an empty snapshot so callers fall back to
   * their hardcoded defaults gracefully).
   */
  async ensureLoaded(): Promise<SnapshotCache> {
    const now = Date.now();
    if (this.cache && now - this.cache.fetchedAt < CACHE_TTL_MS) {
      return this.cache;
    }
    if (this.inflight) return this.inflight;

    this.inflight = (async () => {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8_000);
        const res = await fetch(LLAMA_URL, { signal: controller.signal });
        clearTimeout(timeout);
        if (!res.ok) return this.emptySnapshot();

        const json = await res.json() as { data?: DefiLlamaPool[] };
        const apyByProject = new Map<string, number>();
        const apyByMint = new Map<string, number>();

        for (const p of json.data ?? []) {
          if (p.chain !== 'Solana') continue;
          if (p.apy == null || !Number.isFinite(p.apy)) continue;
          const project = p.project.toLowerCase();
          // Keep the highest-TVL pool per project so the symbol-keyed lookup
          // doesn't pick a random low-TVL outlier (lots of test pools live
          // under shared project names).
          const existing = apyByProject.get(project);
          if (existing == null || p.tvlUsd > 0) {
            apyByProject.set(project, p.apy);
          }
          // Underlying-token APYs (lending pools surface the deposit mint).
          for (const mint of p.underlyingTokens ?? []) {
            const cur = apyByMint.get(mint);
            if (cur == null || p.tvlUsd > 0) apyByMint.set(mint, p.apy);
          }
        }

        // Resolve LST mint → APY via the project mapping. Done after the
        // project map is fully populated so the lookup order is stable.
        for (const [mint, project] of Object.entries(LST_MINT_TO_PROJECT)) {
          const apy = apyByProject.get(project);
          if (apy != null) apyByMint.set(mint, apy);
        }

        const snap: SnapshotCache = { apyByMint, apyByProject, fetchedAt: Date.now() };
        this.cache = snap;
        return snap;
      } catch {
        return this.emptySnapshot();
      } finally {
        this.inflight = null;
      }
    })();
    return this.inflight;
  }

  /** Synchronous lookup — only returns a value when the cache is warm. Use
   *  `ensureLoaded` first if you need fresh data. */
  getApyByMint(mint: string): number | null {
    return this.cache?.apyByMint.get(mint) ?? null;
  }

  /** Project-name lookup. Project names mirror DefiLlama's slugs:
   *  jito-liquid-staking, marinade-liquid-staking, kamino-lend, etc. */
  getApyByProject(project: string): number | null {
    return this.cache?.apyByProject.get(project.toLowerCase()) ?? null;
  }

  private emptySnapshot(): SnapshotCache {
    return {
      apyByMint: new Map(),
      apyByProject: new Map(),
      fetchedAt: Date.now(),
    };
  }
}
