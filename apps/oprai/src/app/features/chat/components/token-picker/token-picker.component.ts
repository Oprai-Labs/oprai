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

  /** Side label shown in the modal header — "From token" / "To token". */
  @Input() title = 'Select token';
  /** Token currently selected on this side. Renders a check on its row. */
  @Input() currentMint = '';
  /** Mint to hide from the list (the OTHER side of a swap). */
  @Input() excludeMint = '';
  /** Default category filter when the modal opens. */
  @Input() initialCategory: Category = 'all';

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
    // Fire the full Jupiter list fetch in the background; modal stays usable
    // from bootstrap tokens while the network call resolves.
    void this.tokenRegistry.ensureLoaded().then(() => this.fetchLiquidities());
    this.fetchLiquidities();
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
