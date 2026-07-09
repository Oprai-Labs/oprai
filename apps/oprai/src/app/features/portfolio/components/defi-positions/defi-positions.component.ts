import { Component, Input, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { CategoryLabelPipe } from '../../pipes/category-label.pipe';
import type { DefiPositions, ProtocolPosition, ProtocolCategory } from '../../models/portfolio.models';

// Sort priority for grouping protocols. Native staking first because it's
// always SOL-denominated and visually anchors the section; LPs and lending
// follow by typical wallet weight. New categories (orders/rewards) land at
// the end — they're typically smaller and shouldn't push the headline
// positions below the fold.
const CATEGORY_ORDER: ProtocolCategory[] = [
  'native-staking',
  'liquid-staking',
  'liquidity-pool',
  'lending',
  'borrowing',
  'perpetuals',
  'streaming',
  'orders',
  'rewards',
];

type SectionPositions = ProtocolPosition['positions'];

interface ProtocolGroupSection {
  category: ProtocolCategory;
  positions: SectionPositions;
  usdValue: number;
}

interface ProtocolGroup {
  protocolId: string;
  protocolName: string;
  protocolLogoUri: string | null;
  totalUsdValue: number;
  // Aggregate claimable across all sections of this protocol — drives the
  // "X Reclaim" header pill so the count lines up with what the user sees
  // in the per-row Claimable Fees column.
  totalClaimableUsd: number;
  claimableCount: number;
  // Each entry corresponds to one protocol "section" (e.g. Jupiter Lend
  // can have both a lending and a borrowing section under the same
  // protocol header).
  sections: ProtocolGroupSection[];
}

@Component({
  selector: 'app-defi-positions',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe, CategoryLabelPipe],
  templateUrl: './defi-positions.component.html',
  styleUrl: './defi-positions.component.scss',
})
export class DefiPositionsComponent {
  /**
   * `positions` and `protocolPositions` are both signal-backed in the parent
   * shell. The shell switches tabs synchronously, but `loadPortfolio()` sets
   * `_summary` *before* `_defiPositions`, so there's a brief window where
   * the DeFi tab can render with `defiPositions()` still null. Default to an
   * empty staking shape so the template never crashes on that first click —
   * the second click previously "fixed" the empty state purely by race-luck.
   */
  @Input() set positions(value: DefiPositions | null | undefined) {
    this._positions.set(value ?? { stakePositions: [], totalStakedSol: 0, totalStakedUsdValue: null });
  }
  get positions(): DefiPositions {
    return this._positions();
  }
  @Input() set protocolPositions(value: ProtocolPosition[]) {
    this._protocolPositions.set(value);
  }
  // Surfaces the multi-protocol fan-out state from PortfolioService so the
  // template can show a skeleton on the first render (when LST scan is
  // already done but lend/LP/perp calls are still resolving) instead of
  // jumping straight to the "No positions found" empty state.
  @Input() set loading(value: boolean) {
    this._loading.set(!!value);
  }

  private readonly _positions = signal<DefiPositions>({
    stakePositions: [],
    totalStakedSol: 0,
    totalStakedUsdValue: null,
  });
  private readonly _protocolPositions = signal<ProtocolPosition[]>([]);
  private readonly _loading = signal<boolean>(false);
  readonly loadingSignal = this._loading.asReadonly();
  // Stable seed for skeleton row keys so trackBy doesn't churn each tick.
  readonly skeletonRows = [0, 1, 2, 3];
  readonly skeletonGroups = [0, 1];

  /**
   * Group by **protocol** first (DeBank-style), then within each protocol
   * fold the categories into sub-sections. Two reasons:
   *   1. Users think "where is my Jupiter Lend money" not "show me all
   *      lending across protocols" — the protocol IS the natural anchor.
   *   2. The previous category-first grouping forced us to render the
   *      category label as the giant pill and the protocol name as a
   *      smaller subtitle, which inverted the importance.
   * Sort: total USD descending so the user's biggest position sits up top.
   */
  readonly groupedPositions = computed<ProtocolGroup[]>(() => {
    const positions = this._protocolPositions();
    if (positions.length === 0) return [];

    const byProtocol = new Map<string, ProtocolGroup>();
    for (const pos of positions) {
      let group = byProtocol.get(pos.protocolId);
      if (!group) {
        group = {
          protocolId: pos.protocolId,
          protocolName: pos.protocolName,
          protocolLogoUri: pos.protocolLogoUri,
          totalUsdValue: 0,
          totalClaimableUsd: 0,
          claimableCount: 0,
          sections: [],
        };
        byProtocol.set(pos.protocolId, group);
      }
      group.sections.push({
        category: pos.category,
        positions: pos.positions,
        usdValue: pos.totalUsdValue,
      });
      group.totalUsdValue += pos.totalUsdValue;
      group.totalClaimableUsd += pos.totalClaimableUsd ?? 0;
      group.claimableCount += pos.claimableCount ?? 0;
    }

    // Sort sections within each protocol by CATEGORY_ORDER (so e.g. Jupiter
    // Lend renders Lending above Borrowing reliably), then sort protocols
    // by descending USD.
    for (const group of byProtocol.values()) {
      group.sections.sort(
        (a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category),
      );
    }

    return Array.from(byProtocol.values()).sort((a, b) => b.totalUsdValue - a.totalUsdValue);
  });

  get hasPositions(): boolean {
    return this._protocolPositions().length > 0 || this._positions().stakePositions.length > 0;
  }

  /** Bucket a health factor into UI risk tiers. hf is the liquidation ratio:
   *  ≥1 means safe; below 1 the position is liquidatable. We add headroom
   *  (1.5/2.0) so the badge warns *before* hitting the cliff. */
  riskTier(hf: number | null | undefined): 'safe' | 'warn' | 'danger' | 'unknown' {
    if (hf == null || !isFinite(hf)) return 'unknown';
    if (hf < 1.2) return 'danger';
    if (hf < 1.6) return 'warn';
    return 'safe';
  }

  /** Column visibility helpers — render the column header + cells only when
   *  at least one row in the section has a real value. Keeps the simple
   *  position layout (Asset / Tokens / Value) for protocols that don't
   *  expose APY / fees / pnl, instead of forcing an "—"-filled wide table. */
  sectionHasClaimable(section: ProtocolGroupSection): boolean {
    return section.positions.some(p => p.claimableUsd != null && p.claimableUsd > 0);
  }
  sectionHasApy(section: ProtocolGroupSection): boolean {
    return section.positions.some(p => p.apy != null);
  }
  sectionHasPnl(section: ProtocolGroupSection): boolean {
    return section.positions.some(p => p.pnlUsd != null);
  }
}
