import { Pipe, PipeTransform } from '@angular/core';
import type { ProtocolCategory } from '../models/portfolio.models';

const LABELS: Record<ProtocolCategory, string> = {
  'native-staking': 'Native Staking',
  'liquid-staking': 'Liquid Staking',
  'liquidity-pool': 'Liquidity Pool',
  'lending': 'Lending',
  'borrowing': 'Borrowing',
  'perpetuals': 'Perpetuals',
  'streaming': 'Streams & Vesting',
};

@Pipe({ name: 'categoryLabel', standalone: true })
export class CategoryLabelPipe implements PipeTransform {
  transform(value: ProtocolCategory): string {
    return LABELS[value] ?? value;
  }
}
