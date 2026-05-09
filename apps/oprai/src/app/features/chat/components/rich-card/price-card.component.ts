import { Component, Input, signal, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { TokenIconComponent } from '@shared/components/token-icon/token-icon.component';
import { PriceChangeComponent } from '@shared/components/price-change/price-change.component';
import { TokenRegistryService } from '../../../../core/services/market/token-registry.service';
import { BirdeyeService } from '../../../portfolio/services/birdeye.service';

@Component({
  selector: 'app-price-card',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule, TokenIconComponent, PriceChangeComponent],
  template: `
    <div class="price-card">
      @if (loading()) {
        <div class="price-card__skeleton">
          <div class="skeleton-circle"></div>
          <div class="skeleton-lines">
            <div class="skeleton-line skeleton-line--md"></div>
            <div class="skeleton-line skeleton-line--sm"></div>
          </div>
        </div>
      } @else if (tokenData()) {
        <a class="price-card__inner" [routerLink]="['/explore/token', tokenData()!.mint]">
          <app-token-icon [mint]="tokenData()!.mint" [iconSrc]="tokenData()!.logoURI" [symbol]="tokenData()!.symbol" size="md" />
          <div class="price-card__info">
            <div class="price-card__top">
              <span class="price-card__symbol">{{ tokenData()!.symbol }}</span>
              <span class="price-card__name">{{ tokenData()!.name }}</span>
            </div>
            <div class="price-card__bottom">
              <span class="price-card__price">\${{ formatPrice(tokenData()!.price) }}</span>
              <app-price-change [change]="tokenData()!.change24h" />
            </div>
          </div>
          <lucide-icon name="external-link" [size]="12" class="price-card__link-icon" />
        </a>
      } @else {
        <div class="price-card__error">
          <lucide-icon name="alert-circle" [size]="14" />
          <span>Token not found</span>
        </div>
      }
    </div>
  `,
  styles: [`
    .price-card {
      border: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      border-radius: 10px;
      background: var(--op-bg-surface-2, #161b28);
      overflow: hidden;
      margin: 6px 0;
    }
    .price-card__inner {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      text-decoration: none;
      color: inherit;
      transition: background 0.15s ease;
    }
    .price-card__inner:hover {
      background: var(--op-hover, rgba(255,255,255,0.04));
    }
    .price-card__info { flex: 1; min-width: 0; }
    .price-card__top {
      display: flex;
      align-items: baseline;
      gap: 6px;
    }
    .price-card__symbol {
      font-size: 13px;
      font-weight: 600;
      color: var(--op-text-primary, #e8ecf4);
    }
    .price-card__name {
      font-size: 11px;
      color: var(--op-text-tertiary, #5a6478);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .price-card__bottom {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 2px;
    }
    .price-card__price {
      font-size: 14px;
      font-weight: 600;
      font-family: var(--op-font-mono, 'JetBrains Mono', monospace);
      font-feature-settings: "tnum";
      color: var(--op-text-primary, #e8ecf4);
    }
    .price-card__link-icon {
      flex-shrink: 0;
      color: var(--op-text-disabled, #3a4358);
    }
    .price-card__error {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 10px 12px;
      font-size: 12px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .price-card__skeleton {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
    }
    .skeleton-circle {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--op-border-subtle, rgba(255,255,255,0.06));
      animation: pulse 1.5s ease infinite;
    }
    .skeleton-lines { flex: 1; display: flex; flex-direction: column; gap: 4px; }
    .skeleton-line {
      height: 10px;
      border-radius: 4px;
      background: var(--op-border-subtle, rgba(255,255,255,0.06));
      animation: pulse 1.5s ease infinite;
    }
    .skeleton-line--md { width: 60%; }
    .skeleton-line--sm { width: 40%; }
    @keyframes pulse {
      0%, 100% { opacity: 0.5; }
      50% { opacity: 1; }
    }
  `],
})
export class PriceCardComponent implements OnInit {
  @Input() tokenSymbol = '';
  @Input() tokenMint = '';

  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly birdeye = inject(BirdeyeService);

  readonly loading = signal(true);
  readonly tokenData = signal<{
    mint: string;
    symbol: string;
    name: string;
    logoURI: string | null;
    price: number;
    change24h: number;
  } | null>(null);

  async ngOnInit(): Promise<void> {
    try {
      let mint = this.tokenMint;
      if (!mint && this.tokenSymbol) {
        const results = this.tokenRegistry.searchTokens(this.tokenSymbol);
        if (results.length > 0) mint = results[0].address;
      }
      if (!mint) {
        this.loading.set(false);
        return;
      }

      const meta = this.tokenRegistry.getToken(mint);
      const prices = await this.birdeye.getTokenPrices([mint]);
      const priceData = prices.get(mint);

      this.tokenData.set({
        mint,
        symbol: meta?.symbol ?? this.tokenSymbol ?? mint.slice(0, 6),
        name: meta?.name ?? '',
        logoURI: meta?.logoURI ?? null,
        price: priceData?.price ?? 0,
        change24h: priceData?.change24h ?? 0,
      });
    } catch {
      this.tokenData.set(null);
    } finally {
      this.loading.set(false);
    }
  }

  formatPrice(price: number): string {
    if (price >= 1) return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (price >= 0.01) return price.toFixed(4);
    if (price >= 0.0001) return price.toFixed(6);
    return price.toExponential(2);
  }
}
