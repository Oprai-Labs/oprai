import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { AllocationChartComponent, ChartSegment } from '../allocation-chart/allocation-chart.component';
import type { PortfolioSummary, DefiPositions, ProtocolPosition, PortfolioValueChange, ProtocolCard } from '../../models/portfolio.models';

@Component({
  selector: 'app-portfolio-overview',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe, AllocationChartComponent],
  templateUrl: './portfolio-overview.component.html',
  styleUrl: './portfolio-overview.component.scss',
})
export class PortfolioOverviewComponent {
  @Input({ required: true }) summary!: PortfolioSummary;
  @Input() defiPositions: DefiPositions | null = null;
  @Input() protocolPositions: ProtocolPosition[] = [];
  @Input() portfolioChange: PortfolioValueChange | null = null;

  get totalValue(): number {
    const stakingValue = this.defiPositions?.totalStakedUsdValue ?? 0;
    const protocolValue = this.protocolPositions
      .filter((p) => p.category !== 'native-staking') // avoid double counting staking
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
        color: '#845EF7',
        logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png',
      });
    }

    // One segment per protocol position. Skip native-staking — already
    // surfaced via the explicit staking section.
    const colors = ['#22B8CF', '#FF922B', '#51CF66', '#FF6B6B', '#339AF0', '#F06595', '#20C997', '#FAB005'];
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
