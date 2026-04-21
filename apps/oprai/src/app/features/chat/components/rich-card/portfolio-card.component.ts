import { Component, signal, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { PriceChangeComponent } from '@shared/components/price-change/price-change.component';
import { PortfolioService } from '../../../portfolio/services/portfolio.service';
import { WalletService } from '../../../../core/services/wallet.service';

@Component({
  selector: 'app-portfolio-card',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule, PriceChangeComponent],
  template: `
    <div class="portfolio-card">
      <div class="portfolio-card__header">
        <lucide-icon name="briefcase" [size]="14" />
        <span>Portfolio Summary</span>
        <a routerLink="/portfolio" class="portfolio-card__link">
          <lucide-icon name="external-link" [size]="12" />
        </a>
      </div>

      @if (!walletConnected()) {
        <div class="portfolio-card__empty">
          <span>Connect wallet to view portfolio</span>
        </div>
      } @else if (loading()) {
        <div class="portfolio-card__loading">
          <lucide-icon name="loader-2" [size]="14" class="spin" />
          <span>Loading portfolio...</span>
        </div>
      } @else {
        <div class="portfolio-card__body">
          <div class="portfolio-card__stat">
            <span class="portfolio-card__stat-label">Total Balance</span>
            <span class="portfolio-card__stat-value">\${{ formatValue(totalBalance()) }}</span>
          </div>
          <div class="portfolio-card__stat">
            <span class="portfolio-card__stat-label">SOL Balance</span>
            <span class="portfolio-card__stat-value">{{ solBalance().toFixed(4) }} SOL</span>
          </div>
          <div class="portfolio-card__stat">
            <span class="portfolio-card__stat-label">Tokens</span>
            <span class="portfolio-card__stat-value">{{ tokenCount() }}</span>
          </div>
          @if (change24h() !== 0) {
            <div class="portfolio-card__stat">
              <span class="portfolio-card__stat-label">24h Change</span>
              <app-price-change [change]="change24h()" />
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .portfolio-card {
      border: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      border-radius: 10px;
      background: var(--op-bg-surface-2, #161b28);
      margin: 6px 0;
      overflow: hidden;
    }
    .portfolio-card__header {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      font-size: 12px;
      font-weight: 600;
      color: var(--op-text-secondary, #8b95a8);
    }
    .portfolio-card__link {
      margin-left: auto;
      color: var(--op-text-disabled, #3a4358);
      transition: color 0.15s ease;
    }
    .portfolio-card__link:hover { color: var(--op-brand); }
    .portfolio-card__empty, .portfolio-card__loading {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 12px;
      font-size: 12px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .portfolio-card__body {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 10px 12px;
    }
    .portfolio-card__stat {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .portfolio-card__stat-label {
      font-size: 10px;
      color: var(--op-text-tertiary, #5a6478);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .portfolio-card__stat-value {
      font-size: 13px;
      font-weight: 600;
      font-family: var(--op-font-mono, monospace);
      color: var(--op-text-primary, #e8ecf4);
    }
    .spin { animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class PortfolioCardComponent implements OnInit {
  private readonly portfolio = inject(PortfolioService);
  private readonly wallet = inject(WalletService);

  readonly walletConnected = this.wallet.connected;
  readonly loading = signal(true);
  readonly totalBalance = signal(0);
  readonly solBalance = signal(0);
  readonly tokenCount = signal(0);
  readonly change24h = signal(0);

  async ngOnInit(): Promise<void> {
    if (!this.wallet.connected()) {
      this.loading.set(false);
      return;
    }

    try {
      const summary = this.portfolio.summary();
      if (summary) {
        this.totalBalance.set(summary.totalUsdValue);
        this.solBalance.set(summary.solBalance.sol);
        this.tokenCount.set(summary.tokens.length);

        const portfolioChange = this.portfolio.portfolioChange();
        if (portfolioChange) {
          this.change24h.set(portfolioChange.change24hPercent);
        }
      }
    } catch {}
    this.loading.set(false);
  }

  formatValue(value: number): string {
    if (value >= 1_000_000) return (value / 1_000_000).toFixed(2) + 'M';
    if (value >= 1_000) return (value / 1_000).toFixed(2) + 'K';
    return value.toFixed(2);
  }
}
