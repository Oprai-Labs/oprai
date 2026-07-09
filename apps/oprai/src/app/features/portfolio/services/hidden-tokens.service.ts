import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'oprai-hidden-tokens';

/**
 * Per-wallet user-managed hidden token registry. Persists to localStorage
 * so the user's hide preferences survive page reloads + wallet switches.
 *
 * Auto-detected spam tokens (`EnhancedTokenAccount.isSuspectedSpam`) are
 * NOT stored here — they're flagged by the portfolio service every load.
 * The token-list component combines both sets when filtering. This split
 * lets the user "unhide" something the heuristic flagged as spam, without
 * fighting the heuristic on every refresh.
 */
@Injectable({ providedIn: 'root' })
export class HiddenTokensService {
  // Map<walletAddress, Set<mint>> — keyed by wallet so different connected
  // accounts maintain independent hide lists. Wrapped in a signal so the
  // token-list re-renders the moment the user toggles a row.
  private readonly _all = signal<Map<string, Set<string>>>(new Map());

  constructor() {
    this.loadFromStorage();
  }

  /** True when the user has manually hidden this mint for this wallet. */
  isHidden(wallet: string, mint: string): boolean {
    return this._all().get(wallet)?.has(mint) ?? false;
  }

  /** Snapshot of hidden mints for a wallet — used for set-membership checks. */
  hiddenSet(wallet: string): Set<string> {
    return this._all().get(wallet) ?? new Set<string>();
  }

  /** Read-only signal so components can subscribe to changes. */
  get changes() {
    return this._all.asReadonly();
  }

  hide(wallet: string, mint: string): void {
    if (!wallet || !mint) return;
    const map = new Map(this._all());
    const set = new Set(map.get(wallet) ?? []);
    set.add(mint);
    map.set(wallet, set);
    this._all.set(map);
    this.persist();
  }

  unhide(wallet: string, mint: string): void {
    if (!wallet || !mint) return;
    const map = new Map(this._all());
    const set = new Set(map.get(wallet) ?? []);
    set.delete(mint);
    if (set.size === 0) map.delete(wallet);
    else map.set(wallet, set);
    this._all.set(map);
    this.persist();
  }

  toggle(wallet: string, mint: string): void {
    if (this.isHidden(wallet, mint)) this.unhide(wallet, mint);
    else this.hide(wallet, mint);
  }

  private loadFromStorage(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const obj = JSON.parse(raw) as Record<string, string[]>;
      const map = new Map<string, Set<string>>();
      for (const [wallet, mints] of Object.entries(obj)) {
        if (Array.isArray(mints) && mints.length) {
          map.set(wallet, new Set(mints));
        }
      }
      this._all.set(map);
    } catch {
      // Storage read can fail under privacy mode / disabled storage —
      // graceful degrade, hides simply don't persist for this session.
    }
  }

  private persist(): void {
    try {
      const obj: Record<string, string[]> = {};
      for (const [wallet, set] of this._all()) {
        obj[wallet] = Array.from(set);
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch {
      // Same as above — ignore quota / privacy errors.
    }
  }
}
