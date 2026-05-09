import { Component, Input, signal, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { TokenIconComponent } from '@shared/components/token-icon/token-icon.component';
import { PriceChangeComponent } from '@shared/components/price-change/price-change.component';
import { TokenRegistryService } from '../../../../core/services/market/token-registry.service';
import { TokenAnalyticsService } from '../../../../core/services/market/token-analytics.service';

@Component({
  selector: 'app-token-detail-card',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule, TokenIconComponent, PriceChangeComponent],
  template: `
    <div class="token-detail-card">
      @if (loading()) {
        <div class="token-detail-card__loading">
          <lucide-icon name="loader-2" [size]="14" class="spin" />
          <span>Loading token info...</span>
        </div>
      } @else if (data()) {
        <div class="token-detail-card__header">
          <app-token-icon [mint]="data()!.mint" [iconSrc]="data()!.logoURI" [symbol]="data()!.symbol" size="md" />
          <div class="token-detail-card__title">
            <span class="token-detail-card__symbol">{{ data()!.symbol }}</span>
            <span class="token-detail-card__name">{{ data()!.name }}</span>
          </div>
          <a [routerLink]="['/explore/token', data()!.mint]" class="token-detail-card__link">
            View <lucide-icon name="arrow-right" [size]="12" />
          </a>
        </div>

        <div class="token-detail-card__price-row">
          <span class="token-detail-card__price">\${{ formatPrice(data()!.price) }}</span>
          <app-price-change [change]="data()!.change24h" />
        </div>

        <div class="token-detail-card__stats">
          @if (data()!.marketCap) {
            <div class="token-detail-card__stat">
              <span class="token-detail-card__stat-label">Market Cap</span>
              <span class="token-detail-card__stat-value">\${{ shortenNumber(data()!.marketCap) }}</span>
            </div>
          }
          @if (data()!.volume24h) {
            <div class="token-detail-card__stat">
              <span class="token-detail-card__stat-label">Volume 24h</span>
              <span class="token-detail-card__stat-value">\${{ shortenNumber(data()!.volume24h) }}</span>
            </div>
          }
          @if (data()!.liquidity) {
            <div class="token-detail-card__stat">
              <span class="token-detail-card__stat-label">Liquidity</span>
              <span class="token-detail-card__stat-value">\${{ shortenNumber(data()!.liquidity) }}</span>
            </div>
          }
        </div>
      } @else {
        <div class="token-detail-card__error">
          <lucide-icon name="alert-circle" [size]="14" />
          <span>Token data unavailable</span>
        </div>
      }
    </div>
  `,
  styles: [`
    .token-detail-card {
      border: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      border-radius: 10px;
      background: var(--op-bg-surface-2, #161b28);
      margin: 6px 0;
      overflow: hidden;
    }
    .token-detail-card__header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
    }
    .token-detail-card__title { flex: 1; min-width: 0; }
    .token-detail-card__symbol {
      font-size: 14px;
      font-weight: 600;
      color: var(--op-text-primary, #e8ecf4);
      display: block;
    }
    .token-detail-card__name {
      font-size: 11px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .token-detail-card__link {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 500;
      color: var(--op-brand);
      text-decoration: none;
      white-space: nowrap;
    }
    .token-detail-card__link:hover { text-decoration: underline; }
    .token-detail-card__price-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px 8px;
    }
    .token-detail-card__price {
      font-size: 18px;
      font-weight: 700;
      font-family: var(--op-font-mono, monospace);
      font-feature-settings: "tnum";
      color: var(--op-text-primary, #e8ecf4);
    }
    .token-detail-card__stats {
      display: flex;
      gap: 12px;
      padding: 8px 12px 10px;
      border-top: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
    }
    .token-detail-card__stat {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .token-detail-card__stat-label {
      font-size: 10px;
      color: var(--op-text-tertiary, #5a6478);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .token-detail-card__stat-value {
      font-size: 12px;
      font-weight: 600;
      font-family: var(--op-font-mono, monospace);
      color: var(--op-text-primary, #e8ecf4);
    }
    .token-detail-card__loading, .token-detail-card__error {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 12px;
      font-size: 12px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .spin { animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class TokenDetailCardComponent implements OnInit {
  @Input() tokenSymbol = '';
  @Input() tokenMint = '';

  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly tokenAnalytics = inject(TokenAnalyticsService);

  readonly loading = signal(true);
  readonly data = signal<{
    mint: string;
    symbol: string;
    name: string;
    logoURI: string | null;
    price: number;
    change24h: number;
    marketCap: number;
    volume24h: number;
    liquidity: number;
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
      const analytics = await this.tokenAnalytics.getAnalytics(mint);
      const ov = analytics.overview;
      const bp = analytics.bestPair;

      this.data.set({
        mint,
        symbol: meta?.symbol ?? ov?.symbol ?? this.tokenSymbol ?? mint.slice(0, 6),
        name: meta?.name ?? ov?.name ?? '',
        logoURI: meta?.logoURI ?? ov?.logoURI ?? null,
        price: ov?.price ?? bp?.priceUsd ?? 0,
        change24h: ov?.priceChange24h ?? bp?.priceChange24h ?? 0,
        marketCap: ov?.marketCap ?? bp?.marketCap ?? 0,
        volume24h: ov?.volume24h ?? bp?.volume24h ?? 0,
        liquidity: ov?.liquidity ?? bp?.liquidity ?? 0,
      });
    } catch {
      this.data.set(null);
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

  shortenNumber(n: number): string {
    if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + 'B';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return n.toFixed(0);
  }
}
