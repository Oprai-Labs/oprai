import { Component, Input, signal, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-price-display',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span
      class="price-display"
      [class.price-tick-up]="tickDirection() === 'up'"
      [class.price-tick-down]="tickDirection() === 'down'"
    >
      {{ formattedPrice() }}
    </span>
  `,
  styles: [`
    .price-display {
      font-family: var(--op-font-mono, 'JetBrains Mono', monospace);
      font-feature-settings: "tnum";
      white-space: nowrap;
      border-radius: 4px;
      padding: 0 2px;
    }
  `]
})
export class PriceDisplayComponent implements OnChanges {
  @Input() price: number | null = null;
  @Input() prefix = '$';
  @Input() animate = true;

  readonly formattedPrice = signal('--');
  readonly tickDirection = signal<'up' | 'down' | null>(null);

  private previousPrice: number | null = null;
  private tickTimeout: ReturnType<typeof setTimeout> | undefined;

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['price']) {
      const newPrice = this.price;

      if (this.animate && this.previousPrice !== null && newPrice !== null && newPrice !== this.previousPrice) {
        this.tickDirection.set(newPrice > this.previousPrice ? 'up' : 'down');
        clearTimeout(this.tickTimeout);
        this.tickTimeout = setTimeout(() => this.tickDirection.set(null), 1200);
      }

      this.previousPrice = newPrice;
      this.formattedPrice.set(this.formatPrice(newPrice));
    }
  }

  private formatPrice(price: number | null): string {
    if (price === null || price === undefined) return '--';
    if (price === 0) return `${this.prefix}0.00`;

    const abs = Math.abs(price);

    if (abs >= 1_000_000) return `${this.prefix}${(price / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) return `${this.prefix}${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (abs >= 1) return `${this.prefix}${price.toFixed(2)}`;
    if (abs >= 0.01) return `${this.prefix}${price.toFixed(4)}`;
    if (abs >= 0.0001) return `${this.prefix}${price.toFixed(6)}`;

    // Micro-cap: show significant digits
    const str = price.toFixed(20);
    const match = str.match(/^-?0\.(0+)/);
    if (match) {
      const zeroCount = match[1].length;
      return `${this.prefix}0.0{${zeroCount}}${str.slice(match[0].length, match[0].length + 4)}`;
    }
    return `${this.prefix}${price.toFixed(8)}`;
  }
}
