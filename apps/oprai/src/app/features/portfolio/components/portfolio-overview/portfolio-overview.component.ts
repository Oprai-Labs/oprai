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

  get allocationSegments(): ChartSegment[] {
    const segments: ChartSegment[] = [];

    // SOL
    if (this.summary.solBalance.usdValue) {
      segments.push({
        label: 'SOL',
        value: this.summary.solBalance.usdValue,
        color: '#845EF7',
        logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png',
      });
    }

    // Top tokens — vibrant palette with coin icons
    const colors = ['#22B8CF', '#FF922B', '#51CF66', '#FF6B6B', '#339AF0', '#F06595', '#20C997'];
    let colorIdx = 0;
    for (const token of this.summary.tokens.slice(0, 7)) {
      if (token.usdValue && token.usdValue > 0) {
        segments.push({
          label: token.symbol,
          value: token.usdValue,
          color: colors[colorIdx % colors.length],
          logoUri: token.logoUri,
        });
        colorIdx++;
      }
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
