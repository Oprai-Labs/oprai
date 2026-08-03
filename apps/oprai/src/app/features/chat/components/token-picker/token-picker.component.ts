import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  HostListener,
  Input,
  OnInit,
  Output,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';

import {
  TokenMeta,
  TokenRegistryService,
} from '@core/services/market/token-registry.service';
import { WalletService } from '@core/services/wallet.service';
import { environment } from '../../../../../environments/environment';
import { createSolanaConnection } from '@core/utils/solana-connection';

type Category = 'all' | 'stable' | 'lst';

/**
 * Modal token picker used inside the swap action card. Click the FROM or TO
 * token chip → this component opens, the user filters by category or types a
 * search query, picks a token, and we emit the selected mint back.
 *
 * Behavior:
 *  - Floats above the chat as a fixed-position overlay with a backdrop
 *  - Backdrop click + Escape key + close button all dismiss
 *  - Search box auto-focuses on open
 *  - Category chips: All / Stables / LSTs — backed by TokenRegistry helpers
 *  - Excluded mint (the OTHER side of the swap) is filtered out so you can't
 *    accidentally pick SOL → SOL
 *  - Currently-selected mint shows a check on its row
 */
@Component({
  selector: 'op-token-picker',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './token-picker.component.html',
  styleUrls: ['./token-picker.component.scss'],
})
export class TokenPickerComponent implements OnInit {
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly wallet = inject(WalletService);

  /** Side label shown in the modal header — "From token" / "To token". */
  @Input() title = 'Select token';
  /** Token currently selected on this side. Renders a check on its row. */
  @Input() currentMint = '';
  /** Mint to hide from the list (the OTHER side of a swap). */
  @Input() excludeMint = '';
  /** Default category filter when the modal opens. */
  @Input() initialCategory: Category = 'all';
  /**
   * Show what the wallet actually holds, above everything else.
   *
   * Set on the side being SPENT. Choosing what to pay with is a question about
   * your own wallet, and answering it from an alphabetical list of every token
   * on Solana makes the user hunt for something they already own. The receive
   * side is the opposite — anything is fair game there — so it stays off.
   */
  @Input() walletFirst = false;

  @Output() picked = new EventEmitter<string>();
  @Output() closed = new EventEmitter<void>();

  readonly category = signal<Category>('all');
  readonly searchQuery = signal('');
  readonly registryVersion = this.tokenRegistry.version;

  // Per-mint liquidity (USD). null = unknown (failed fetch or pending),
  // undefined = not yet attempted. Populated lazily via fetchLiquidities()
  // whenever the visible result set changes. We only batch-fetch what's
  // actually visible — full registry has ~hundreds of tokens, sending all
  // of them to Birdeye on modal-open would be wasteful.
  readonly liquidityMap = signal<Map<string, number | null>>(new Map());

  /** What the connected wallet holds, largest balance first. */
  readonly holdings = signal<Array<{ token: TokenMeta; amount: number }>>([]);
  readonly holdingsLoading = signal(false);

  /** Held tokens that survive the current search box and exclusion. */
  readonly heldResults = computed(() => {
    this.registryVersion();
    const q = this.searchQuery().trim().toLowerCase();
    return this.holdings().filter(h => {
      if (h.token.address === this.excludeMint) return false;
      if (!q) return true;
      return (
        h.token.symbol.toLowerCase().includes(q) ||
        h.token.name.toLowerCase().includes(q) ||
        h.token.address.toLowerCase() === q
      );
    });
  });

  /** The full list, minus anything already shown under "Your tokens". */
  readonly otherResults = computed(() => {
    const held = new Set(this.heldResults().map(h => h.token.address));
    return this.results().filter(t => !held.has(t.address));
  });

  readonly results = computed<TokenMeta[]>(() => {
    // Touch registry version so the list re-derives when Jupiter loads more
    // tokens after the modal is already open.
    this.registryVersion();
    const list = this.tokenRegistry.listByCategory(
      this.category(),
      this.searchQuery(),
    );
    if (!this.excludeMint) return list;
    return list.filter(t => t.address !== this.excludeMint);
  });

  // Re-fetch liquidities whenever the visible results set changes (category
  // tab switch, search-query narrowing, registry version bump). The fetcher
  // dedupes against already-known mints so repeated triggers are cheap.
  private readonly _liquidityRefreshEffect = effect(() => {
    const visible = this.results();
    if (visible.length === 0) return;
    void this.fetchLiquidities();
  });

  ngOnInit(): void {
    this.category.set(this.initialCategory);
    if (this.walletFirst) void this.loadHoldings();
    // Fire the full Jupiter list fetch in the background; modal stays usable
    // from bootstrap tokens while the network call resolves.
    void this.tokenRegistry.ensureLoaded().then(() => this.fetchLiquidities());
    this.fetchLiquidities();
  }

  /**
   * Read the wallet's token accounts straight from the chain.
   *
   * Deliberately not filtered to the registry: someone selling a memecoin they
   * bought an hour ago needs to find it here, and it will not be in any curated
   * list. Unknown mints are shown by their address until the registry knows
   * better. Zero balances are dropped — they are closed or dust accounts and
   * only make the list harder to read.
   */
  private async loadHoldings(): Promise<void> {
    const owner = this.wallet.publicKey();
    if (!owner) return;
    this.holdingsLoading.set(true);
    try {
      const { PublicKey } = await import('@solana/web3.js');
      const connection = createSolanaConnection('confirmed');
      const pk = new PublicKey(owner);
      const [sol, classic, t22] = await Promise.all([
        connection.getBalance(pk),
        connection.getParsedTokenAccountsByOwner(pk, {
          programId: new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'),
        }),
        connection
          .getParsedTokenAccountsByOwner(pk, {
            programId: new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'),
          })
          .catch(() => ({ value: [] as Array<{ account: { data: { parsed: { info: Record<string, any> } } } }> })),
      ]);

      const rows: Array<{ token: TokenMeta; amount: number }> = [];
      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      if (sol > 0) {
        rows.push({
          token: this.tokenRegistry.getToken(SOL_MINT) ?? {
            address: SOL_MINT, symbol: 'SOL', name: 'Solana', decimals: 9, logoURI: null,
          },
          amount: sol / 1e9,
        });
      }
      for (const acc of [...classic.value, ...(t22 as { value: any[] }).value]) {
        const info = (acc as any).account?.data?.parsed?.info;
        const amount = Number(info?.tokenAmount?.uiAmount ?? 0);
        const mint = String(info?.mint ?? '');
        if (!mint || !(amount > 0)) continue;
        rows.push({
          token: this.tokenRegistry.getToken(mint) ?? {
            address: mint,
            symbol: `${mint.slice(0, 4)}…${mint.slice(-4)}`,
            name: 'Unknown token',
            decimals: Number(info?.tokenAmount?.decimals ?? 0),
            logoURI: null,
          },
          amount,
        });
      }
      // Largest first: the token you are most likely to spend is the one you
      // have most of, and an alphabetical wallet list helps nobody.
      rows.sort((a, b) => b.amount - a.amount);
      this.holdings.set(rows);
      void this.nameTheUnknown(rows);
    } catch {
      // Holdings are an aid, not the feature. The full list still works.
    } finally {
      this.holdingsLoading.set(false);
    }
  }

  /**
   * Give the unnamed mints their names.
   *
   * A pump.fun token minted this morning is in no curated list, so the wallet
   * showed rows reading "CSJq…pump / Unknown token" — the user is asked to
   * choose between two anonymous addresses. Their metadata is on chain, and
   * the gateway already resolves it for the portfolio; the picker just never
   * asked. Runs after the list renders, so balances appear immediately and
   * names fill in behind them.
   */
  private async nameTheUnknown(rows: Array<{ token: TokenMeta; amount: number }>): Promise<void> {
    const unknown = rows.filter(r => r.token.name === 'Unknown token').map(r => r.token.address);
    if (unknown.length === 0) return;
    try {
      const res = await fetch(`${environment.apiBase}/token-meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include',
        body: JSON.stringify({ mints: unknown.slice(0, 100) }),
      });
      if (!res.ok) return;
      const meta = (await res.json()) as Record<string, { name?: string; symbol?: string; image?: string }>;
      this.holdings.update(list =>
        list.map(r => {
          const m = meta?.[r.token.address];
          if (!m || (!m.symbol && !m.name)) return r;
          return {
            ...r,
            token: {
              ...r.token,
              symbol: m.symbol || r.token.symbol,
              name: m.name || r.token.name,
              logoURI: m.image || r.token.logoURI,
            },
          };
        }),
      );
    } catch {
      // The address is still a usable label; a missing name is not a failure.
    }
  }

  /** Pull liquidity USD for the currently visible result set, batch-style. */
  private async fetchLiquidities(): Promise<void> {
    const visible = this.results();
    if (visible.length === 0) return;
    const mints = visible.map(t => t.address);
    // Skip mints we already have a liquidity reading for — Map.has covers
    // both real numbers and the explicit-null "we tried, came up empty" case.
    const have = this.liquidityMap();
    const missing = mints.filter(m => !have.has(m));
    if (missing.length === 0) return;
    const fresh = await this.tokenRegistry.getLiquidities(missing);
    if (fresh.size === 0) return;
    const merged = new Map(have);
    for (const [k, v] of fresh) merged.set(k, v);
    this.liquidityMap.set(merged);
  }

  /**
   * Human badge for a token's liquidity. Tier thresholds mirror the swap
   * card's price-impact tiers so the user sees consistent severity language
   * end-to-end: "$10M+" green, "$1M+" neutral, "<$100K" orange (low), "—"
   * grey (unknown).
   */
  liquidityBadge(mint: string): { label: string; tier: 'high' | 'mid' | 'low' | 'unknown' } {
    const v = this.liquidityMap().get(mint);
    if (v == null) return { label: '', tier: 'unknown' };
    if (v >= 10_000_000) return { label: '$10M+', tier: 'high' };
    if (v >= 1_000_000)  return { label: '$1M+',  tier: 'mid' };
    if (v >= 100_000)    return { label: '$100K+', tier: 'mid' };
    if (v > 0)           return { label: '<$100K', tier: 'low' };
    return { label: 'No liquidity', tier: 'low' };
  }

  setCategory(c: Category): void {
    this.category.set(c);
  }

  onSearch(value: string): void {
    this.searchQuery.set(value);
  }

  /** Trim a balance to something readable without lying about the size. */
  formatAmount(v: number): string {
    if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (v >= 1) return v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
    return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  pick(token: TokenMeta): void {
    this.picked.emit(token.address);
  }

  dismiss(): void {
    this.closed.emit();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.dismiss();
  }

  /** Letter fallback when no logo URI is available (matches token-chip style). */
  letterFor(t: TokenMeta): string {
    return (t.symbol ?? '?').charAt(0).toUpperCase();
  }

  onLogoError(ev: Event): void {
    const img = ev.target as HTMLImageElement | null;
    if (img) img.style.display = 'none';
  }
}
