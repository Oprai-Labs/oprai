import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/** Real brand marks for identity types — Solana, Ethereum, Telegram, X.
 *  Inline SVG so they scale crisply and need no asset requests. */
@Component({
  selector: 'app-brand-icon',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @switch (type) {
      @case ('solana_wallet') {
        <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 397.7 311.7" aria-hidden="true">
          <linearGradient id="op-sol" x1="360.9" y1="-37.5" x2="141.2" y2="383.3" gradientUnits="userSpaceOnUse">
            <stop offset="0" stop-color="#00FFA3" /><stop offset="1" stop-color="#DC1FFF" />
          </linearGradient>
          <path fill="url(#op-sol)" d="M64.6 237.9c2.4-2.4 5.7-3.8 9.2-3.8h317.4c5.8 0 8.7 7 4.6 11.1l-62.7 62.7c-2.4 2.4-5.7 3.8-9.2 3.8H15.5c-5.8 0-8.7-7-4.6-11.1l62.7-62.7z" />
          <path fill="url(#op-sol)" d="M64.6 3.8C67.1 1.4 70.4 0 73.8 0h317.4c5.8 0 8.7 7 4.6 11.1l-62.7 62.7c-2.4 2.4-5.7 3.8-9.2 3.8H15.5c-5.8 0-8.7-7-4.6-11.1L64.6 3.8z" />
          <path fill="url(#op-sol)" d="M333.1 120.1c-2.4-2.4-5.7-3.8-9.2-3.8H6.5c-5.8 0-8.7 7-4.6 11.1l62.7 62.7c2.4 2.4 5.7 3.8 9.2 3.8h317.4c5.8 0 8.7-7 4.6-11.1l-62.7-62.6z" />
        </svg>
      }
      @case ('evm_wallet') {
        <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 256 417" aria-hidden="true">
          <path fill="#343434" d="M127.9 0l-2.7 9.5v275.7l2.7 2.7 127.9-75.6z" />
          <path fill="#8C8C8C" d="M127.9 0L0 212.3l127.9 75.6V154.2z" />
          <path fill="#3C3C3B" d="M127.9 312.2l-1.5 1.9v98.2l1.5 4.5L256 236.6z" />
          <path fill="#8C8C8C" d="M127.9 416.9v-104.7L0 236.6z" />
          <path fill="#141414" d="M127.9 287.9l127.9-75.6-127.9-58.1z" />
          <path fill="#393939" d="M0 212.3l127.9 75.6V154.2z" />
        </svg>
      }
      @case ('telegram') {
        <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 240 240" aria-hidden="true">
          <circle cx="120" cy="120" r="120" fill="#229ED9" />
          <path fill="#fff" d="M53 118l122-47c6-2 11 1 9 10l-21 98c-1 6-5 8-10 5l-28-21-14 13c-1 2-3 3-6 3l2-30 55-50c2-2-1-3-4-1l-68 43-29-9c-6-2-6-6 1-9z" />
        </svg>
      }
      @case ('twitter') {
        <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 24 24" aria-hidden="true">
          <path fill="currentColor" d="M18.9 1.2h3.7l-8 9.1 9.4 12.4h-7.4l-5.8-7.6-6.6 7.6H.5l8.6-9.8L0 1.2h7.6l5.2 6.9zm-1.3 19.5h2L6.5 3.3H4.4z" />
        </svg>
      }
      @default {
        <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="10" fill="currentColor" opacity=".2" />
        </svg>
      }
    }
  `,
  styles: [`:host { display:inline-flex; line-height:0; color:var(--op-text-primary); }`],
})
export class BrandIconComponent {
  @Input() type = '';
  @Input() size = 24;
}
