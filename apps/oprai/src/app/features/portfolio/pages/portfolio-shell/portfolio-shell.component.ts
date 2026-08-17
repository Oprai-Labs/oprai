import { Component, inject, effect, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { WalletService } from '@core/services/wallet.service';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { PortfolioService } from '../../services/portfolio.service';
import { PortfolioOverviewComponent } from '../../components/portfolio-overview/portfolio-overview.component';
import { PortfolioTabsComponent } from '../../components/portfolio-tabs/portfolio-tabs.component';
import { TokenListComponent } from '../../components/token-list/token-list.component';
import { DefiPositionsComponent } from '../../components/defi-positions/defi-positions.component';
import { NftGalleryComponent } from '../../components/nft-gallery/nft-gallery.component';
import { RecentActivityComponent } from '../../components/recent-activity/recent-activity.component';
import { ClaimableRewardsComponent } from '../../components/claimable-rewards/claimable-rewards.component';
import { TokenAccountsModalComponent } from '../../components/token-accounts-modal/token-accounts-modal.component';
import type { PortfolioTab } from '../../models/portfolio.models';

interface HeaderMetric {
  id: 'sol' | 'btc' | 'eth';
  label: string;
  valueText: string;
  change24h: number;
  iconUrl?: string;
}

// Self-hosted so the benchmark icons never depend on a third-party CDN — the old
// raw.githubusercontent.com URLs 429'd (rate limit) / timed out and showed broken
// placeholders. Served from /assets, covered by the CSP's 'self'.
const SOL_LOGO_URI = '/assets/coins/sol.svg';
const BTC_LOGO_URI = '/assets/coins/btc.svg';
const ETH_LOGO_URI = '/assets/coins/eth.svg';

@Component({
  selector: 'app-portfolio-shell',
  standalone: true,
  imports: [
    CommonModule,
    LucideAngularModule,
    SkeletonTableComponent,
    PortfolioOverviewComponent,
    PortfolioTabsComponent,
    TokenListComponent,
    DefiPositionsComponent,
    NftGalleryComponent,
    RecentActivityComponent,
    ClaimableRewardsComponent,
    TokenAccountsModalComponent,
  ],
  templateUrl: './portfolio-shell.component.html',
  styleUrl: './portfolio-shell.component.scss',
})
export class PortfolioShellComponent implements OnDestroy {
  readonly benchmarkQuotes = signal<HeaderMetric[]>([
    {
      id: 'btc',
      label: 'BTC',
      valueText: '--',
      change24h: 0,
      iconUrl: BTC_LOGO_URI,
    },
    {
      id: 'eth',
      label: 'ETH',
      valueText: '--',
      change24h: 0,
      iconUrl: ETH_LOGO_URI,
    },
    {
      id: 'sol',
      label: 'SOL',
      valueText: '--',
      change24h: 0,
      iconUrl: SOL_LOGO_URI,
    },
  ]);

  private benchmarkInterval: ReturnType<typeof setInterval> | null = null;
  private readonly walletService = inject(WalletService);
  readonly portfolioService = inject(PortfolioService);

  readonly connected = this.walletService.connected;
  readonly publicKey = this.walletService.publicKey;
  readonly summary = this.portfolioService.summary;
  readonly defiPositions = this.portfolioService.defiPositions;
  readonly recentTransactions = this.portfolioService.recentTransactions;
  readonly loadingState = this.portfolioService.loadingState;
  readonly error = this.portfolioService.error;
  readonly isLoading = this.portfolioService.isLoading;

  // New signals
  readonly activeTab = this.portfolioService.activeTab;
  readonly protocolPositions = this.portfolioService.protocolPositions;
  readonly protocolPositionsLoading = this.portfolioService.protocolPositionsLoading;
  // True once the first full load (wallet + every protocol + pricing) is in.
  // Gates the whole-page skeleton so the page renders in one shot instead of
  // the wallet total popping in ahead of the protocol balances.
  readonly positionsSettled = this.portfolioService.positionsSettled;
  readonly portfolioChange = this.portfolioService.portfolioChange;
  readonly nfts = this.portfolioService.nfts;
  readonly nftCollections = this.portfolioService.nftCollections;
  readonly nftLoadingState = this.portfolioService.nftLoadingState;
  readonly enhancedTransactions = this.portfolioService.enhancedTransactions;
  readonly historyLoadingState = this.portfolioService.historyLoadingState;
  readonly historyHasMore = this.portfolioService.historyHasMore;
  readonly historyLoadingMore = this.portfolioService.historyLoadingMore;

  // Token Accounts Manager modal — opened from the token-list header.
  readonly accountsModalOpen = signal(false);

  openAccountsModal(): void {
    this.accountsModalOpen.set(true);
  }
  closeAccountsModal(): void {
    this.accountsModalOpen.set(false);
  }
  /**
   * Re-fetch the wallet portfolio after a successful close/burn batch so
   * the freed lamports flow back into the SOL balance card and the closed
   * mints drop from the token list. Fire-and-forget — the modal stays open
   * (the user may want to run a second pass) and surfaces its own success
   * banner regardless.
   */
  onAccountsReclaimed(): void {
    const key = this.publicKey();
    if (key) this.portfolioService.loadPortfolio(key).catch(() => {});
  }

  private readonly walletEffect = effect(() => {
    const key = this.publicKey();
    if (key) {
      this.portfolioService.loadPortfolio(key);
    } else {
      this.portfolioService.reset();
    }
  });

  constructor() {
    this.loadBenchmarkQuotes();
    this.benchmarkInterval = setInterval(() => {
      this.loadBenchmarkQuotes();
    }, 60_000);
  }

  onTabChange(tab: PortfolioTab): void {
    this.portfolioService.setActiveTab(tab, this.publicKey());
  }

  refresh(): void {
    const key = this.publicKey();
    if (key) {
      this.portfolioService.refresh(key);
    }
  }

  loadMoreHistory(): void {
    const key = this.publicKey();
    if (key) {
      this.portfolioService.loadMoreHistory(key);
    }
  }

  formatUsd(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return '--';
    return '$' + value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }

  formatChange(value: number): string {
    const prefix = value > 0 ? '+' : '';
    return `${prefix}${value.toFixed(2)}%`;
  }

  ngOnDestroy(): void {
    this.walletEffect.destroy();
    if (this.benchmarkInterval) {
      clearInterval(this.benchmarkInterval);
      this.benchmarkInterval = null;
    }
  }

  private async loadBenchmarkQuotes(): Promise<void> {
    try {
      const data = await this.fetchBenchmarkPrices();

      this.benchmarkQuotes.set([
        {
          id: 'btc',
          label: 'BTC',
          valueText: this.formatUsd(data.bitcoin.usd),
          change24h: data.bitcoin.change24h,
          iconUrl: BTC_LOGO_URI,
        },
        {
          id: 'eth',
          label: 'ETH',
          valueText: this.formatUsd(data.ethereum.usd),
          change24h: data.ethereum.change24h,
          iconUrl: ETH_LOGO_URI,
        },
        {
          id: 'sol',
          label: 'SOL',
          valueText: this.formatUsd(data.solana.usd),
          change24h: data.solana.change24h,
          iconUrl: SOL_LOGO_URI,
        },
      ]);
    } catch {
      // Keep latest rendered values if the quote request fails.
    }
  }

  private async fetchBenchmarkPrices(): Promise<{
    solana: { usd: number; change24h: number };
    bitcoin: { usd: number; change24h: number };
    ethereum: { usd: number; change24h: number };
  }> {
    const response = await fetch(
      'https://api.coingecko.com/api/v3/simple/price?ids=solana,bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true'
    );
    if (!response.ok) {
      return {
        solana: { usd: 0, change24h: 0 },
        bitcoin: { usd: 0, change24h: 0 },
        ethereum: { usd: 0, change24h: 0 },
      };
    }

    const data = (await response.json()) as {
      solana?: { usd?: number; usd_24h_change?: number };
      bitcoin?: { usd?: number; usd_24h_change?: number };
      ethereum?: { usd?: number; usd_24h_change?: number };
    };

    return {
      solana: {
        usd: data.solana?.usd ?? 0,
        change24h: data.solana?.usd_24h_change ?? 0,
      },
      bitcoin: {
        usd: data.bitcoin?.usd ?? 0,
        change24h: data.bitcoin?.usd_24h_change ?? 0,
      },
      ethereum: {
        usd: data.ethereum?.usd ?? 0,
        change24h: data.ethereum?.usd_24h_change ?? 0,
      },
    };
  }
}
