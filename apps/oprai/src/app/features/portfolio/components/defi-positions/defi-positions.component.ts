import { Component, Input, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { CategoryLabelPipe } from '../../pipes/category-label.pipe';
import type { DefiPositions, ProtocolPosition, ProtocolCategory } from '../../models/portfolio.models';

const CATEGORY_ORDER: ProtocolCategory[] = [
  'native-staking',
  'liquid-staking',
  'liquidity-pool',
  'lending',
  'borrowing',
  'perpetuals',
  'streaming',
];

@Component({
  selector: 'app-defi-positions',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe, CategoryLabelPipe],
  templateUrl: './defi-positions.component.html',
  styleUrl: './defi-positions.component.scss',
})
export class DefiPositionsComponent {
  @Input({ required: true }) positions!: DefiPositions;
  @Input() set protocolPositions(value: ProtocolPosition[]) {
    this._protocolPositions.set(value);
  }

  private readonly _protocolPositions = signal<ProtocolPosition[]>([]);

  readonly groupedPositions = computed(() => {
    const positions = this._protocolPositions();
    if (positions.length === 0) return [];

    const groups = new Map<ProtocolCategory, ProtocolPosition[]>();

    for (const pos of positions) {
      const group = groups.get(pos.category) ?? [];
      group.push(pos);
      groups.set(pos.category, group);
    }

    return CATEGORY_ORDER
      .filter((cat) => groups.has(cat))
      .map((cat) => ({
        category: cat,
        protocols: groups.get(cat)!,
        totalValue: groups.get(cat)!.reduce((s, p) => s + p.totalUsdValue, 0),
      }));
  });

  get hasPositions(): boolean {
    return this._protocolPositions().length > 0 || this.positions.stakePositions.length > 0;
  }
}
