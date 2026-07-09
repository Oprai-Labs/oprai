/**
 * Jupiter Portfolio Service
 *
 * Wraps `api.jup.ag/portfolio/v1/*` via the gateway proxy. The proxy injects
 * the `x-api-key` server-side, so this service has no auth concerns; it
 * exists mainly to: (a) type the responses, (b) cache the platforms catalog
 * for the session, and (c) translate request failure into a quiet null /
 * empty so callers don't have to wrap every call in their own try/catch.
 *
 * Scope (per Jupiter docs): Jupiter products ONLY — DCA, limit orders,
 * perpetuals, lend, JUP / JupSOL stake, Jupiter LP. Cross-protocol positions
 * (Kamino / Meteora / Pumpfun) flow through other services and live in
 * `defi-positions.service.ts`.
 */
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import type {
  JupiterPortfolioResponse,
  JupiterStakedJupResponse,
  JupiterPlatform,
} from '../../../features/portfolio/models/portfolio.models';

const PORTFOLIO_BASE = '/market/jupiter/portfolio';

@Injectable({ providedIn: 'root' })
export class JupiterPortfolioService {
  private readonly api = inject(ApiService);

  // Platforms list is keyed at the org level, doesn't change between requests.
  // One-shot cache keeps every render off the network after the first hit.
  private platformsCache: JupiterPlatform[] | null = null;
  private platformsPromise: Promise<JupiterPlatform[]> | null = null;

  /**
   * Returns the wallet's Jupiter-product positions, or `null` when the call
   * fails or the API key isn't configured. `null` vs empty `elements: []` is
   * deliberate — empty means "no positions" (real signal), null means "we
   * don't know" and the caller should fall back to existing protocol-specific
   * integrations.
   *
   * Optional `platforms` is forwarded as `?platforms=jupiter-exchange,…`.
   */
  async getPositions(wallet: string, platforms?: string[]): Promise<JupiterPortfolioResponse | null> {
    if (!wallet) return null;
    const params = platforms && platforms.length > 0
      ? { platforms: platforms.join(',') }
      : undefined;
    try {
      return await firstValueFrom(
        this.api.get<JupiterPortfolioResponse>(`${PORTFOLIO_BASE}/positions/${wallet}`, params),
      );
    } catch {
      return null;
    }
  }

  /**
   * JUP staking info: amount staked + pending unstakes. Empty wallets return
   * `{stakedAmount: 0, unstaking: []}` — that's a real upstream response, so
   * callers should check the numeric fields, not truthiness of the object.
   */
  async getStakedJup(wallet: string): Promise<JupiterStakedJupResponse | null> {
    if (!wallet) return null;
    try {
      return await firstValueFrom(
        this.api.get<JupiterStakedJupResponse>(`${PORTFOLIO_BASE}/staked-jup/${wallet}`),
      );
    } catch {
      return null;
    }
  }

  /**
   * Full platforms catalog (logos, names, deprecation flags). Cached for the
   * session: the gateway already caches the upstream call for 1h server-side,
   * the in-process cache here saves the round-trip on every refresh.
   */
  getPlatforms(): Promise<JupiterPlatform[]> {
    if (this.platformsCache) return Promise.resolve(this.platformsCache);
    if (this.platformsPromise) return this.platformsPromise;

    this.platformsPromise = (async () => {
      try {
        const data = await firstValueFrom(
          this.api.get<JupiterPlatform[]>(`${PORTFOLIO_BASE}/platforms`),
        );
        this.platformsCache = Array.isArray(data) ? data : [];
        return this.platformsCache;
      } catch {
        return [];
      } finally {
        this.platformsPromise = null;
      }
    })();
    return this.platformsPromise;
  }

  /**
   * Resolve a platformId to its display name + logo. Returns `null` on miss
   * — callers should fall back to the raw platformId for the label.
   */
  async resolvePlatform(platformId: string): Promise<JupiterPlatform | null> {
    const list = await this.getPlatforms();
    return list.find(p => p.id === platformId) ?? null;
  }
}
