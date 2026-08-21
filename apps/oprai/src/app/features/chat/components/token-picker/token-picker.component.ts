import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnDestroy,
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
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { environment } from '../../../../../environments/environment';
import { createSolanaConnection } from '@core/utils/solana-connection';
import { TPipe } from '@core/i18n';

/**
 * The three questions a token picker actually answers: what do I have, what is
 * moving, and what equities can I buy. "All / Stables / LSTs" answered none of
 * them — a category filter over an alphabetical list of every mint on Solana,
 * which is a way to browse, not to choose.
 */
type Category = 'holdings' | 'trending' | 'stocks';

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
  imports: [CommonModule, LucideAngularModule, TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './token-picker.component.html',
  styleUrls: ['./token-picker.component.scss'],
})
export class TokenPickerComponent implements OnInit, OnDestroy {
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly wallet = inject(WalletService);
  private readonly priceFeed = inject(PriceFeedService);

  /** Side label shown in the modal header — "From token" / "To token". */
  @Input() title = 'Select token';
  /** Token currently selected on this side. Renders a check on its row. */
  @Input() currentMint = '';
  /** Mint to hide from the list (the OTHER side of a swap). */
  @Input() excludeMint = '';
  /** Default category filter when the modal opens. */
  @Input() initialCategory: Category = 'holdings';
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

  readonly category = signal<Category>('holdings');
  readonly searchQuery = signal('');
  readonly registryVersion = this.tokenRegistry.version;

  /** What the connected wallet holds, largest balance first. */
  readonly holdings = signal<Array<{ token: TokenMeta; amount: number; usd: number | null }>>([]);
  readonly holdingsLoading = signal(false);

  /**
   * Tokens worth offering even at a zero balance.
   *
   * A wallet with three tokens produced a three-row Holdings tab, which reads
   * as though the app knows nothing. Jupiter fills the same tab with the
   * majors at 0.00 — the list stays a list, and the tokens most people
   * actually want are one click away instead of behind a search.
   */
  private static readonly MAJORS = [
    'SOL', 'USDC', 'USDT', 'JUP', 'JLP', 'BONK', 'JTO', 'WIF', 'PYUSD', 'cbBTC',
  ];

  readonly heldResults = computed<Array<{ token: TokenMeta; amount: number; usd: number | null }>>(() => {
    this.registryVersion();
    const owned = this.holdings().filter(h => h.token.address !== this.excludeMint);
    const seen = new Set(owned.map(h => h.token.address));
    const filler = TokenPickerComponent.MAJORS
      .map(sym => this.tokenRegistry.getBySymbol(sym))
      .filter((t): t is TokenMeta => !!t)
      .filter(t => !seen.has(t.address) && t.address !== this.excludeMint)
      .map(token => ({ token, amount: 0, usd: null }));
    return [...owned, ...filler];
  });

  /** True when this row is in OPRAI's own registry, not merely known to Jupiter. */
  isVerified(mint: string): boolean {
    return !!this.tokenRegistry.getToken(mint);
  }

  /** Trending and stocks, fetched from Jupiter and cached for this modal. */
  readonly trending = signal<TokenMeta[]>([]);
  readonly stocks = signal<TokenMeta[]>([]);
  readonly tabLoading = signal(false);

  /**
   * A search query overrides the tab.
   *
   * Someone typing a symbol wants that token, not that token filtered down to
   * whichever tab happens to be open — and the mint they are hunting for is
   * usually in none of the three.
   */
  readonly results = computed<TokenMeta[]>(() => {
    this.registryVersion();
    const q = this.searchQuery().trim();
    const list = q
      ? this.tokenRegistry.listByCategory('all', q)
      : this.category() === 'trending'
        ? this.trending()
        : this.category() === 'stocks'
          ? this.stocks()
          : [];
    if (!this.excludeMint) return list;
    return list.filter(t => t.address !== this.excludeMint);
  });

  /** Held tokens are the Holdings tab, and are hidden once a search starts. */
  readonly showHoldings = computed(
    () => this.category() === 'holdings' && this.searchQuery().trim() === '',
  );

  ngOnInit(): void {
    // Move this overlay to the document root.
    //
    // `position: fixed` is only relative to the viewport while no ancestor has
    // a transform. The action card animates in with `transform` and
    // `animation-fill-mode: both`, so that transform never goes away — which
    // makes the card a containing block, and the picker was being positioned
    // and clipped inside it: its top slid under the chat bubble and its bottom
    // ran off the screen. Relocating the element sidesteps every such ancestor,
    // now and in future, without asking the rest of the app to avoid
    // transforms.
    try { document.body.appendChild(this.host.nativeElement); } catch { /* SSR */ }

    this.category.set(this.walletFirst ? this.initialCategory : 'trending');
    void this.loadHoldings();
    // Fire the full Jupiter list fetch in the background; modal stays usable
    // from bootstrap tokens while the network call resolves.
    void this.tokenRegistry.ensureLoaded();
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

      const rows: Array<{ token: TokenMeta; amount: number; usd: number | null }> = [];
      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      if (sol > 0) {
        rows.push({
          token: this.tokenRegistry.getToken(SOL_MINT) ?? {
            address: SOL_MINT, symbol: 'SOL', name: 'Solana', decimals: 9, logoURI: null,
          },
          amount: sol / 1e9,
          usd: null,
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
          usd: null,
        });
      }
      // Largest first: the token you are most likely to spend is the one you
      // have most of, and an alphabetical wallet list helps nobody.
      rows.sort((a, b) => b.amount - a.amount);
      this.holdings.set(rows);
      void this.nameTheUnknown(rows);
      void this.priceHoldings(rows);
    } catch {
      // Holdings are an aid, not the feature. The full list still works.
    } finally {
      this.holdingsLoading.set(false);
    }
  }

  /**
   * Put a dollar figure beside each balance.
   *
   * "663.827,77" of something is not a quantity anyone can rank against
   * "5.460,75" of something else. The dollar value is what makes the list
   * sortable by eye, which is the whole point of showing holdings first.
   */
  private async priceHoldings(rows: Array<{ token: TokenMeta; amount: number; usd: number | null }>): Promise<void> {
    const mints = rows.map(r => r.token.address);
    if (mints.length === 0) return;
    try {
      const prices = await this.priceFeed.getPrices(mints);
      this.holdings.update(list =>
        list.map(r => {
          const p = prices.get(r.token.address)?.price;
          return p && p > 0 ? { ...r, usd: r.amount * p } : r;
        }),
      );
    } catch {
      // A balance without a price is still a balance.
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
  private async nameTheUnknown(rows: Array<{ token: TokenMeta; amount: number; usd: number | null }>): Promise<void> {
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

  setCategory(c: Category): void {
    this.category.set(c);
    if (c === 'trending' && this.trending().length === 0) void this.loadTab('trending');
    if (c === 'stocks' && this.stocks().length === 0) void this.loadTab('stocks');
  }

  /**
   * Fill a tab from Jupiter.
   *
   * Trending has a dedicated endpoint. Stocks does not — the tag API rejects
   * "stocks" and "rwa" — but searching "xstock" returns the tokenised equities
   * (SPYx, NVDAx, TSLAx …), which is what the tab means. Both go straight to
   * jup.ag, the same host the token registry already reads from.
   */
  private async loadTab(which: 'trending' | 'stocks'): Promise<void> {
    this.tabLoading.set(true);
    try {
      const url = which === 'trending'
        ? 'https://api.jup.ag/tokens/v2/toptrending/24h?limit=40'
        : 'https://api.jup.ag/tokens/v2/search?query=xstock&limit=40';
      const res = await fetch(url);
      if (!res.ok) return;
      const raw = (await res.json()) as Array<Record<string, unknown>>;
      if (!Array.isArray(raw)) return;
      const list: TokenMeta[] = raw
        .map(t => ({
          address: String(t['id'] ?? t['address'] ?? ''),
          symbol: String(t['symbol'] ?? ''),
          name: String(t['name'] ?? ''),
          decimals: Number(t['decimals'] ?? 0),
          logoURI: (t['icon'] as string) ?? (t['logoURI'] as string) ?? null,
        }))
        .filter(t => t.address && t.symbol);
      (which === 'trending' ? this.trending : this.stocks).set(list);
    } catch {
      // An empty tab is honest; a broken modal is not.
    } finally {
      this.tabLoading.set(false);
    }
  }

  onSearch(value: string): void {
    this.searchQuery.set(value);
  }

  ngOnDestroy(): void {
    // Angular removes the view, but the element now lives outside the parent's
    // DOM subtree, so it has to be taken away by hand.
    try { this.host.nativeElement.remove(); } catch { /* already gone */ }
  }

  /** Short form of a mint, for the second line of a row. */
  shortMint(mint: string): string {
    return mint.length > 12 ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : mint;
  }

  formatUsd(v: number | null): string {
    if (v === null) return '';
    if (v >= 1000) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    if (v >= 1) return `$${v.toFixed(2)}`;
    return `$${v.toFixed(4)}`;
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
