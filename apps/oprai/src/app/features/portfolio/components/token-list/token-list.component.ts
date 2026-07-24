import { Component, Input, Output, EventEmitter, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { WalletService } from '@core/services/wallet.service';
import { HiddenTokensService } from '../../services/hidden-tokens.service';
import type { EnhancedTokenAccount, SolBalance, TokenSortField, SortDirection } from '../../models/portfolio.models';

@Component({
  selector: 'app-token-list',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './token-list.component.html',
  styleUrl: './token-list.component.scss',
})
export class TokenListComponent {
  @Input({ required: true }) set tokens(value: EnhancedTokenAccount[]) {
    this._tokens.set(value);
  }
  @Input({ required: true }) solBalance!: SolBalance;
  // Parent (portfolio-shell) owns the accounts-manager modal state — clicking
  // "Manage Accounts" emits here and the shell flips the modal open.
  @Output() openAccountsManager = new EventEmitter<void>();

  private readonly walletService = inject(WalletService);
  private readonly hiddenTokens = inject(HiddenTokensService);

  private readonly _tokens = signal<EnhancedTokenAccount[]>([]);
  readonly hideSmall = signal(false);
  // Combined hide filter — auto-detected spam *plus* user-hidden mints.
  // Defaults ON; toggling reveals the union, each row carries an Unhide
  // button so the user can reverse a manual hide or a heuristic
  // false-positive without having to dig into settings.
  readonly hideHidden = signal(true);
  readonly sortField = signal<TokenSortField>('value');
  readonly sortDirection = signal<SortDirection>('desc');
  readonly showAll = signal(false);
  readonly INITIAL_COUNT = 10;

  /**
   * True when the row should be filtered out. The user-store bit acts as
   * an *override* (XOR), so:
   *   - spam token, no override     → hidden (heuristic default)
   *   - spam token, override set    → visible (user said "Show this anyway")
   *   - normal token, no override   → visible
   *   - normal token, override set  → hidden (user manually hid it)
   * One toggle handles both "reveal a false-positive scam flag" and
   * "hide a normal token I don't want to see" without separate UI.
   */
  isHidden(t: EnhancedTokenAccount): boolean {
    const wallet = this.walletService.publicKey();
    const overridden = wallet ? this.hiddenTokens.isHidden(wallet, t.mint) : false;
    // Auto-hidden: spam heuristic OR a DeFi position-receipt token (LP/vault)
    // that already renders in Active Positions. The user override still flips it.
    const autoHide = !!t.isSuspectedSpam || !!t.isDefiPositionToken;
    return overridden ? !autoHide : autoHide;
  }

  readonly filteredAndSorted = computed(() => {
    // Touch the hidden-tokens signal so toggling a row triggers a fresh
    // computation. (Without this read the computed wouldn't re-fire.)
    this.hiddenTokens.changes();
    let tokens = this._tokens();

    if (this.hideHidden()) {
      tokens = tokens.filter((t) => !this.isHidden(t));
    }

    // Filter small balances
    if (this.hideSmall()) {
      tokens = tokens.filter((t) => (t.usdValue ?? 0) >= 1);
    }

    // Sort
    const field = this.sortField();
    const dir = this.sortDirection();
    const mult = dir === 'desc' ? -1 : 1;

    tokens = [...tokens].sort((a, b) => {
      switch (field) {
        case 'value':
          return mult * ((a.usdValue ?? 0) - (b.usdValue ?? 0));
        case 'name':
          return mult * a.symbol.localeCompare(b.symbol);
        case 'change24h':
          return mult * ((a.priceChange24h ?? 0) - (b.priceChange24h ?? 0));
        case 'allocation':
          return mult * (a.allocationPercent - b.allocationPercent);
        default:
          return 0;
      }
    });

    return tokens;
  });

  get walletTotal(): number {
    const solVal = this.solBalance.usdValue ?? 0;
    // Honour the hide toggle so the section header total matches the
    // visible rows. Otherwise the user sees a $1,173 wallet header but
    // tokens summing to $1,024 — confusing inconsistency.
    const tokens = this.hideHidden()
      ? this._tokens().filter((t) => !this.isHidden(t))
      : this._tokens();
    const tokensVal = tokens.reduce((sum, t) => sum + (t.usdValue ?? 0), 0);
    return solVal + tokensVal;
  }

  get visibleTokens(): EnhancedTokenAccount[] {
    const tokens = this.filteredAndSorted();
    if (this.showAll()) return tokens;
    return tokens.slice(0, this.INITIAL_COUNT);
  }

  get hiddenCount(): number {
    return Math.max(0, this.filteredAndSorted().length - this.INITIAL_COUNT);
  }

  get smallTokenCount(): number {
    return this._tokens().filter((t) => (t.usdValue ?? 0) < 1).length;
  }

  /** Combined count — auto-flagged spam + user-hidden mints. Drives the
   *  "X hidden" header chip. */
  get hiddenTokenCount(): number {
    return this._tokens().filter((t) => this.isHidden(t)).length;
  }

  toggleHideHidden(): void {
    this.hideHidden.update((v) => !v);
  }

  /**
   * Per-row hide / unhide. When `hideHidden` mode is on the row vanishes
   * immediately; in show-all mode it switches the row's action icon to the
   * unhide affordance. We never auto-toggle the chip — discoverability is
   * better when the user controls reveal independently of per-row hides.
   */
  toggleRowHide(t: EnhancedTokenAccount, ev: Event): void {
    ev.stopPropagation();
    const wallet = this.walletService.publicKey();
    if (!wallet) return;
    // For a heuristic-flagged spam row, toggle works the opposite way:
    // marking it explicitly *unhidden* in the user store overrides the
    // spam flag in `isHidden()` — we add a check below, but for symmetry
    // the simplest behaviour is: toggle just flips the user-stored bit.
    this.hiddenTokens.toggle(wallet, t.mint);
  }

  /** Tooltip + label helper — distinguishes "hide" vs "unhide" actions. */
  rowActionLabel(t: EnhancedTokenAccount): string {
    return this.isHidden(t) ? 'Show this token' : 'Hide this token';
  }

  toggleSort(field: TokenSortField): void {
    if (this.sortField() === field) {
      this.sortDirection.update((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.sortField.set(field);
      this.sortDirection.set('desc');
    }
  }

  toggleHideSmall(): void {
    this.hideSmall.update((v) => !v);
  }

  toggleShowAll(): void {
    this.showAll.update((v) => !v);
  }

  onImageError(event: Event): void {
    const img = event.target as HTMLImageElement;
    // Retry once through an image proxy before giving up: memecoin/NFT logos on
    // twimg.com / IPFS / arweave are frequently blocked by tracker/ad filters or
    // fail hotlink checks in the browser, so the raw <img> errors even though
    // the URL is valid. weserv fetches server-side from a neutral CDN.
    const src = img.getAttribute('src') ?? '';
    if (src && !img.dataset['proxied'] && !src.includes('images.weserv.nl')) {
      img.dataset['proxied'] = '1';
      img.src = `https://images.weserv.nl/?url=${encodeURIComponent(src)}&w=64&h=64&fit=cover`;
      return;
    }
    img.style.display = 'none';
    const fallback = img.nextElementSibling;
    if (fallback) (fallback as HTMLElement).style.display = 'flex';
  }

  /** Mint address most recently copied — drives the per-row "Copied!" badge. */
  readonly copiedMint = signal<string | null>(null);
  private copyTimer: number | null = null;

  /**
   * Copy the SPL mint address to the clipboard. Wired to the token symbol
   * cell so users can quickly grab a contract for a swap or block-explorer
   * lookup. Falls back to a hidden textarea on older browsers.
   */
  async copyMint(mint: string, ev: Event): Promise<void> {
    ev.stopPropagation();
    if (!mint) return;
    try {
      await navigator.clipboard.writeText(mint);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = mint;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* swallow */ }
      document.body.removeChild(ta);
    }
    this.copiedMint.set(mint);
    if (this.copyTimer) window.clearTimeout(this.copyTimer);
    this.copyTimer = window.setTimeout(() => {
      if (this.copiedMint() === mint) this.copiedMint.set(null);
      this.copyTimer = null;
    }, 1500);
  }

  getSortIcon(field: TokenSortField): string {
    if (this.sortField() !== field) return '';
    return this.sortDirection() === 'desc' ? ' \u25BE' : ' \u25B4';
  }
}
