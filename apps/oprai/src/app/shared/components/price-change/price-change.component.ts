import { Component, Input, signal, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-price-change',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span
      class="price-change"
      [class.price-change--up]="isPositive()"
      [class.price-change--down]="isNegative()"
      [class.price-change--neutral]="isNeutral()"
      [class.price-change--pill]="variant === 'pill'"
    >
      @if (!isNeutral()) {
        <span class="price-change__arrow">{{ isPositive() ? '\u25B2' : '\u25BC' }}</span>
      }
      {{ display() }}
    </span>
  `,
  styles: [`
    .price-change {
      font-family: var(--op-font-mono, 'JetBrains Mono', monospace);
      font-feature-settings: "tnum";
      font-size: inherit;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 2px;
    }
    .price-change--up { color: var(--op-gain, #34d399); }
    .price-change--down { color: var(--op-loss, #f87171); }
    .price-change--neutral { color: var(--op-text-secondary, #8b95a8); }
    .price-change--pill {
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 500;
    }
    .price-change--pill.price-change--up { background: rgba(52, 211, 153, 0.12); }
    .price-change--pill.price-change--down { background: rgba(248, 113, 113, 0.12); }
    .price-change--pill.price-change--neutral { background: rgba(139, 149, 168, 0.12); }
    .price-change__arrow { font-size: 0.7em; }
  `]
})
export class PriceChangeComponent implements OnChanges {
  @Input() change: number | null = null;
  @Input() variant: 'inline' | 'pill' = 'inline';

  readonly display = signal('--');
  readonly isPositive = signal(false);
  readonly isNegative = signal(false);
  readonly isNeutral = signal(true);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['change']) {
      const val = this.change;
      if (val === null || val === undefined) {
        this.display.set('--');
        this.isPositive.set(false);
        this.isNegative.set(false);
        this.isNeutral.set(true);
      } else {
        const prefix = val > 0 ? '+' : '';
        this.display.set(`${prefix}${val.toFixed(2)}%`);
        this.isPositive.set(val > 0);
        this.isNegative.set(val < 0);
        this.isNeutral.set(val === 0);
      }
    }
  }
}
