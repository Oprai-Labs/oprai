import { Component, Input, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import type { ProtocolPosition, PositionItem, ProtocolCategory } from '../../models/portfolio.models';

interface ProtocolGroup {
  protocolId: string;
  protocolName: string;
  protocolLogoUri: string | null;
  categories: ProtocolCategory[];
  totalUsdValue: number;
  totalClaimableUsd: number;
  claimableCount: number;
  // Weighted-average APY across the protocol's positions, USD-weighted so a
  // tiny test pool with 50% APY doesn't dominate the headline number.
  weightedApy: number | null;
  positions: Array<PositionItem & { category: ProtocolCategory }>;
}

/**
 * Active Positions & Rewards dashboard. Reads the already-aggregated
 * `ProtocolPosition[]` from `portfolio.service` and surfaces it as a
 * collapsible per-protocol breakdown:
 *
 *   - Header chips: total claimable USD across all protocols, total
 *     positions, total APY-weighted value.
 *   - Per-protocol row: logo, name, category tags, USD value, claimable.
 *     Each protocol expands to per-position rows with their own APY,
 *     claimable, and fees.
 *
 * No claim transactions live here — each protocol's claim flow is a
 * separate action (`orca_collect_fees`, `meteora_claim_reward`, etc.) and
 * lives in their respective services. Wiring a unified "Claim all" would
 * need orchestrated multi-protocol signing; that's a follow-up.
 */
@Component({
  selector: 'app-claimable-rewards',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './claimable-rewards.component.html',
  styleUrl: './claimable-rewards.component.scss',
})
export class ClaimableRewardsComponent {
  @Input() set protocolPositions(value: ProtocolPosition[]) {
    this._positions.set(value ?? []);
  }

  private readonly _positions = signal<ProtocolPosition[]>([]);
  // Track expanded state per `protocolId` so the user's toggle survives
  // re-emits of the underlying positions signal (which happens on every
  // portfolio refresh tick).
  readonly expanded = signal<Set<string>>(new Set());
  readonly collapsedAll = signal(false);

  /**
   * Fold multiple `ProtocolPosition` entries (one per protocol+category
   * pair) into a single ProtocolGroup per `protocolId`. Position rows
   * carry their original category so the expanded view can label them.
   *
   * A protocol shows up if EITHER it has claimable rewards OR any of its
   * positions report an APY — covers both the "passive yield" case
   * (jitoSOL, jupSOL — APY but no manual claim) and the "earned fees"
   * case (Meteora DLMM, Orca, Pumpfun rewards).
   */
  readonly groups = computed<ProtocolGroup[]>(() => {
    const map = new Map<string, ProtocolGroup>();
    for (const proto of this._positions()) {
      const hasClaimable = (proto.totalClaimableUsd ?? 0) > 0;
      const hasApy = proto.positions.some(p => p.apy != null);
      if (!hasClaimable && !hasApy) continue;

      let group = map.get(proto.protocolId);
      if (!group) {
        group = {
          protocolId: proto.protocolId,
          protocolName: proto.protocolName,
          protocolLogoUri: proto.protocolLogoUri,
          categories: [],
          totalUsdValue: 0,
          totalClaimableUsd: 0,
          claimableCount: 0,
          weightedApy: null,
          positions: [],
        };
        map.set(proto.protocolId, group);
      }
      if (!group.categories.includes(proto.category)) {
        group.categories.push(proto.category);
      }
      group.totalUsdValue += proto.totalUsdValue ?? 0;
      group.totalClaimableUsd += proto.totalClaimableUsd ?? 0;
      group.claimableCount += proto.claimableCount ?? 0;
      for (const pos of proto.positions) {
        group.positions.push({ ...pos, category: proto.category });
      }
    }

    // Compute USD-weighted APY per protocol once everything's aggregated.
    // Falls back to a simple average when individual positions have APYs
    // but no USD value (newly-opened positions are common).
    for (const g of map.values()) {
      let weightedSum = 0;
      let weight = 0;
      let simpleSum = 0;
      let simpleCount = 0;
      for (const p of g.positions) {
        if (p.apy == null) continue;
        simpleSum += p.apy;
        simpleCount += 1;
        const v = p.totalUsdValue ?? 0;
        if (v > 0) {
          weightedSum += p.apy * v;
          weight += v;
        }
      }
      g.weightedApy = weight > 0
        ? weightedSum / weight
        : (simpleCount > 0 ? simpleSum / simpleCount : null);
    }

    return Array.from(map.values()).sort((a, b) => {
      // Claimable-bearing protocols first (the most actionable line items),
      // then by USD value descending. Stake-only protocols slot in by value.
      if ((a.totalClaimableUsd > 0) !== (b.totalClaimableUsd > 0)) {
        return a.totalClaimableUsd > 0 ? -1 : 1;
      }
      return b.totalUsdValue - a.totalUsdValue;
    });
  });

  readonly totalClaimableUsd = computed(
    () => this.groups().reduce((s, g) => s + g.totalClaimableUsd, 0),
  );

  readonly totalPositionsCount = computed(
    () => this.groups().reduce((s, g) => s + g.positions.length, 0),
  );

  readonly totalDeployedUsd = computed(
    () => this.groups().reduce((s, g) => s + g.totalUsdValue, 0),
  );

  /** Average APY across the user's whole DeFi surface, weighted by USD
   *  deployed per protocol. Drives the "Blended APR" headline chip. */
  readonly blendedApy = computed<number | null>(() => {
    let weightedSum = 0;
    let weight = 0;
    for (const g of this.groups()) {
      if (g.weightedApy == null) continue;
      const v = g.totalUsdValue;
      if (v <= 0) continue;
      weightedSum += g.weightedApy * v;
      weight += v;
    }
    return weight > 0 ? weightedSum / weight : null;
  });

  isExpanded(protocolId: string): boolean {
    return this.expanded().has(protocolId);
  }

  toggleProtocol(protocolId: string): void {
    this.expanded.update(set => {
      const next = new Set(set);
      if (next.has(protocolId)) next.delete(protocolId);
      else next.add(protocolId);
      return next;
    });
  }

  expandAll(): void {
    const next = new Set<string>();
    for (const g of this.groups()) next.add(g.protocolId);
    this.expanded.set(next);
    this.collapsedAll.set(false);
  }

  collapseAll(): void {
    this.expanded.set(new Set());
    this.collapsedAll.set(true);
  }

  /** Pretty label for the expanded category chip — Title Case the dashes
   *  out of the raw category enum. */
  labelForCategory(c: ProtocolCategory): string {
    switch (c) {
      case 'native-staking': return 'Native staking';
      case 'liquid-staking': return 'Liquid staking';
      case 'liquidity-pool': return 'LP';
      case 'lending': return 'Lending';
      case 'borrowing': return 'Borrowing';
      case 'perpetuals': return 'Perps';
      case 'streaming': return 'Streams';
      case 'orders': return 'Open orders';
      case 'rewards': return 'Rewards';
      default: return c;
    }
  }
}
