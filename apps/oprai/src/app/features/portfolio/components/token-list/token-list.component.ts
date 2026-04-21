import { Component, Input, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
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

  private readonly _tokens = signal<EnhancedTokenAccount[]>([]);
  readonly hideSmall = signal(false);
  readonly sortField = signal<TokenSortField>('value');
  readonly sortDirection = signal<SortDirection>('desc');
  readonly showAll = signal(false);
  readonly INITIAL_COUNT = 10;

  readonly filteredAndSorted = computed(() => {
    let tokens = this._tokens();

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
    const tokensVal = this._tokens().reduce((sum, t) => sum + (t.usdValue ?? 0), 0);
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
    img.style.display = 'none';
    const fallback = img.nextElementSibling;
    if (fallback) (fallback as HTMLElement).style.display = 'flex';
  }

  getSortIcon(field: TokenSortField): string {
    if (this.sortField() !== field) return '';
    return this.sortDirection() === 'desc' ? ' \u25BE' : ' \u25B4';
  }
}
