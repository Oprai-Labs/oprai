import { Component, Input, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { AllocationChartComponent, ChartSegment } from '../allocation-chart/allocation-chart.component';
import { BalanceSparklineComponent } from '../balance-sparkline/balance-sparkline.component';
import { PriceService } from '../../services/price.service';
import type { PortfolioSummary, DefiPositions, ProtocolPosition, PortfolioValueChange, ProtocolCard } from '../../models/portfolio.models';

@Component({
  selector: 'app-portfolio-overview',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe, AllocationChartComponent, BalanceSparklineComponent],
  templateUrl: './portfolio-overview.component.html',
  styleUrl: './portfolio-overview.component.scss',
})
export class PortfolioOverviewComponent implements OnInit {
  private readonly priceService = inject(PriceService);

  @Input({ required: true }) summary!: PortfolioSummary;
  @Input() defiPositions: DefiPositions | null = null;
  @Input() protocolPositions: ProtocolPosition[] = [];
  @Input() portfolioChange: PortfolioValueChange | null = null;
  // True while the cross-protocol position fetchers are still running. The
  // wallet-token total (SOL + SPL) resolves first (~$14 here) but the DeFi
  // legs (Jupiter Lend, Raydium LP, …) arrive seconds later. Showing the
  // partial wallet-only figure and then jumping to the full total reads as a
  // glitch, so the header total, sparkline, chart and protocol cards all hold
  // a skeleton until every platform balance is in.
  @Input() positionsLoading = false;

  /**
   * 7-day historical portfolio value series for the inline sparkline.
   *
   * Computed by fanning out per-token Birdeye OHLCV (`/defi/ohlcv?address=`)
   * via the gateway proxy, then summing `balance × close_at_t` across
   * every priced holding for each canonical timestamp. Tokens without
   * historical coverage (Pump.fun launches outside the indexer) contribute
   * their current USD as a flat baseline.
   *
   * Falls back to the SOL-only proxy if Birdeye returns no data (e.g.
   * gateway proxy unavailable). Loaded once on init.
   */
  readonly history7d = signal<number[]>([]);
  // Track whether the history fetch has run at least once. The sparkline
  // shows a shimmer placeholder while points is empty; once we've tried and
  // come back empty, we flip `historyReady` to true so the template can hide
  // the shimmer rather than leave it spinning forever.
  readonly historyReady = signal(false);

  async ngOnInit(): Promise<void> {
    // Build holdings list from the wallet (SOL + non-spam tokens). Spam
    // tokens are excluded so the chart doesn't get inflated by fake
    // airdropped value the user can't actually realize.
    const holdings: Array<{ mint: string; balance: number; currentUsdValue: number }> = [];
    holdings.push({
      mint: 'So11111111111111111111111111111111111111112',
      balance: this.summary.solBalance.sol,
      currentUsdValue: this.summary.solBalance.usdValue ?? 0,
    });
    for (const t of this.summary.tokens) {
      if (t.isSuspectedSpam) continue;
      if (t.balance <= 0) continue;
      holdings.push({
        mint: t.mint,
        balance: t.balance,
        currentUsdValue: t.usdValue ?? 0,
      });
    }

    // Single path: per-token Birdeye OHLCV via the gateway proxy. The
    // previous CoinGecko direct-fetch fallback was unreliable in browsers
    // (CORS blocked on most networks) and silently produced a frozen
    // shimmer, so the chart now either renders real Birdeye data or stays
    // empty — `BalanceSparklineComponent` already hides itself when the
    // input has fewer than 2 points.
    try {
      const real = await this.priceService.getPortfolioHistory7d(holdings);
      if (real.length > 1) {
        this.history7d.set(real.map((p) => p.value));
      }
    } finally {
      this.historyReady.set(true);
    }
  }

  /**
   * Wallet+protocols total. Two double-count traps:
   *  - native-staking is also reported via `defiPositions.totalStakedUsdValue`
   *  - liquid-staking tokens (jitoSOL, jupSOL, mSOL) sit in `summary.tokens`
   *    AND in protocolPositions (via DefiPositionsService.getLiquidStakingPositions)
   * Both excluded from the protocol sum so each dollar is counted exactly once.
   */
  get totalValue(): number {
    const stakingValue = this.defiPositions?.totalStakedUsdValue ?? 0;
    const protocolValue = this.protocolPositions
      .filter((p) => p.category !== 'native-staking' && p.category !== 'liquid-staking')
      .reduce((sum, p) => sum + p.totalUsdValue, 0);
    return this.summary.totalUsdValue + stakingValue + protocolValue;
  }

  get protocolCards(): ProtocolCard[] {
    const cards: ProtocolCard[] = [];

    // Wallet card (SOL + tokens)
    cards.push({
      id: 'wallet',
      name: 'Wallet',
      iconUrl: null,
      usdValue: this.summary.totalUsdValue,
    });

    // One card per protocol position
    for (const pos of this.protocolPositions) {
      cards.push({
        id: pos.protocolId,
        name: pos.protocolName,
        iconUrl: pos.protocolLogoUri,
        usdValue: pos.totalUsdValue,
      });
    }

    return cards;
  }

  /**
   * Donut segments grouped by *destination* — Wallet (liquid native + SPL)
   * vs each DeFi protocol the user has capital deployed in. This answers
   * the more useful question "where is my money working?" rather than the
   * raw token mix, which gets dominated by SOL almost every time.
   *
   * Liquid Staking Tokens (LSTs) live in summary.tokens AND in
   * protocolPositions (via DefiPositionsService.getLiquidStakingPositions),
   * so we exclude `isLiquidStaking` mints from the Wallet segment to avoid
   * counting them twice.
   */
  get allocationSegments(): ChartSegment[] {
    const segments: ChartSegment[] = [];

    // Wallet segment = SOL native + all SPL tokens that aren't routed
    // through a protocol (LSTs are credited to their staking protocol).
    let walletValue = this.summary.solBalance.usdValue ?? 0;
    for (const token of this.summary.tokens) {
      if (token.isLiquidStaking) continue;
      if (token.usdValue && token.usdValue > 0) walletValue += token.usdValue;
    }
    if (walletValue > 0) {
      segments.push({
        label: 'Wallet',
        value: walletValue,
        // Saturated electric violet — pops against the dark surface much
        // harder than the previous muted #845EF7. Matches our brand
        // gradient endpoint so it ties visually into the app shell.
        color: '#7C3AED',
        logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png',
      });
    }

    // One segment per protocol position. Skip native-staking — already
    // surfaced via the explicit staking section. Palette uses the
    // Tailwind 500-tier saturations: vivid enough that adjacent slices stay
    // distinguishable on a 28px ring without relying on the legend.
    const colors = [
      '#06B6D4', // cyan-500
      '#F97316', // orange-500
      '#22C55E', // green-500
      '#F43F5E', // rose-500
      '#3B82F6', // blue-500
      '#EC4899', // pink-500
      '#14B8A6', // teal-500
      '#EAB308', // yellow-500
      '#A855F7', // purple-500
      '#84CC16', // lime-500
    ];
    let colorIdx = 0;
    // Aggregate by protocolId so that "Jupiter Lend" lending + borrowing
    // (or any protocol with multiple categories) collapses to one slice.
    const byProtocol = new Map<string, { label: string; value: number; logoUri: string | null }>();
    for (const pos of this.protocolPositions) {
      if (pos.category === 'native-staking') continue;
      if (pos.totalUsdValue <= 0) continue;
      const existing = byProtocol.get(pos.protocolId);
      if (existing) {
        existing.value += pos.totalUsdValue;
      } else {
        byProtocol.set(pos.protocolId, {
          label: pos.protocolName,
          value: pos.totalUsdValue,
          logoUri: pos.protocolLogoUri,
        });
      }
    }
    for (const proto of byProtocol.values()) {
      segments.push({
        label: proto.label,
        value: proto.value,
        color: colors[colorIdx % colors.length],
        logoUri: proto.logoUri,
      });
      colorIdx++;
    }

    return segments;
  }

  copyAddress(): void {
    navigator.clipboard.writeText(this.summary.walletAddress);
  }

  getCardIconClass(cardId: string): string {
    if (cardId === 'wallet') return 'wallet';
    if (cardId === 'solana-staking') return 'staking';
    return 'protocol';
  }
}
