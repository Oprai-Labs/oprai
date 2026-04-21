import { Component, Input, inject, signal, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TokenRegistryService } from '../../../core/services/market/token-registry.service';

@Component({
  selector: 'app-token-icon',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (iconUrl()) {
      <img
        [src]="iconUrl()"
        [alt]="alt"
        [style.width.px]="sizeMap[size]"
        [style.height.px]="sizeMap[size]"
        class="token-icon"
        (error)="onImgError()"
      />
    } @else {
      <div
        class="token-icon token-icon--fallback"
        [style.width.px]="sizeMap[size]"
        [style.height.px]="sizeMap[size]"
        [style.fontSize.px]="sizeMap[size] * 0.45"
        [style.backgroundColor]="fallbackColor()"
      >
        {{ fallbackLetter() }}
      </div>
    }
  `,
  styles: [`
    :host { display: inline-flex; }
    .token-icon {
      border-radius: 50%;
      object-fit: cover;
      flex-shrink: 0;
    }
    .token-icon--fallback {
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-weight: 600;
      font-family: var(--op-font-sans, 'Inter', sans-serif);
      text-transform: uppercase;
    }
  `]
})
export class TokenIconComponent implements OnChanges {
  @Input() mint = '';
  @Input() iconSrc: string | null = null;
  @Input() symbol = '';
  @Input() alt = '';
  @Input() size: 'sm' | 'md' | 'lg' | 'xl' = 'md';

  private readonly tokenRegistry = inject(TokenRegistryService);

  readonly sizeMap = { sm: 24, md: 32, lg: 40, xl: 48 };

  readonly iconUrl = signal<string | null>(null);
  readonly fallbackLetter = signal('?');
  readonly fallbackColor = signal('#6366f1');

  private static readonly COLORS = [
    '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
    '#ec4899', '#f43f5e', '#ef4444', '#f97316',
    '#eab308', '#84cc16', '#22c55e', '#14b8a6',
    '#06b6d4', '#0ea5e9', '#3b82f6', '#2563eb',
  ];

  ngOnChanges(_changes: SimpleChanges): void {
    this.resolveIcon();
  }

  private resolveIcon(): void {
    // Priority: explicit iconSrc > registry lookup
    if (this.iconSrc) {
      this.iconUrl.set(this.iconSrc);
    } else if (this.mint) {
      const url = this.tokenRegistry.getLogoUrl(this.mint);
      this.iconUrl.set(url);
    } else {
      this.iconUrl.set(null);
    }

    // Fallback letter and color
    const label = this.symbol || this.mint || '?';
    this.fallbackLetter.set(label.charAt(0));
    const hash = this.hashString(this.mint || this.symbol || 'x');
    this.fallbackColor.set(TokenIconComponent.COLORS[hash % TokenIconComponent.COLORS.length]);
  }

  onImgError(): void {
    this.iconUrl.set(null);
  }

  private hashString(str: string): number {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }
}
