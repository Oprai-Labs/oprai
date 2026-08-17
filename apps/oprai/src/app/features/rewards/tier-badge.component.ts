import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * OPRAI tier emblem — a hexagonal crystal badge whose colour escalates per tier
 * and whose six vertex studs light up one-by-one (1 lit = Bronze … 6 lit =
 * Legend), so the level reads straight off the geometry. Branded to the app's
 * crystal/tesseract motif rather than a generic medal icon.
 */
@Component({
  selector: 'app-tier-badge',
  standalone: true,
  imports: [CommonModule],
  template: `
    <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 48 48" fill="none"
         xmlns="http://www.w3.org/2000/svg" [style.filter]="glow ? 'drop-shadow(0 0 8px ' + color + '66)' : null">
      <defs>
        <linearGradient [attr.id]="gid" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" [attr.stop-color]="color" stop-opacity="0.9" />
          <stop offset="1" [attr.stop-color]="color" stop-opacity="0.35" />
        </linearGradient>
      </defs>

      <!-- hexagon frame -->
      <path [attr.d]="HEX" [attr.fill]="color" fill-opacity="0.10"
            [attr.stroke]="color" stroke-opacity="0.55" stroke-width="1.4" stroke-linejoin="round" />

      <!-- faceted crystal -->
      <path d="M24 15 L30 22 L24 33 L18 22 Z" [attr.fill]="'url(#' + gid + ')'"
            [attr.stroke]="color" stroke-opacity="0.9" stroke-width="1.3" stroke-linejoin="round" />
      <path d="M18 22 H30" [attr.stroke]="color" stroke-opacity="0.5" stroke-width="1" />
      <path d="M24 15 V33" [attr.stroke]="color" stroke-opacity="0.3" stroke-width="1" />
      <!-- extra facet on the upper tiers, so the crystal itself grows richer -->
      @if (tier >= 4) {
        <path d="M18 22 L24 15 L30 22" [attr.stroke]="color" stroke-opacity="0.4" stroke-width="1" fill="none" />
      }

      <!-- six vertex studs; the first {{ tier }} are lit -->
      @for (v of vertices; track $index) {
        <circle [attr.cx]="v.x" [attr.cy]="v.y" r="2.4"
                [attr.fill]="$index < tier ? color : 'transparent'"
                [attr.stroke]="color" [attr.stroke-opacity]="$index < tier ? 1 : 0.28" stroke-width="1.2" />
      }
    </svg>
  `,
})
export class TierBadgeComponent {
  @Input() tier = 1;
  @Input() color = '#5b5fc7';
  @Input() size = 40;
  /** Soft outer glow — on for the hero badge, off for the small ladder ones. */
  @Input() glow = false;

  readonly HEX = 'M24 4 L41.3 14 L41.3 34 L24 44 L6.7 34 L6.7 14 Z';
  // Clockwise from the top vertex, so studs fill in reading order.
  readonly vertices = [
    { x: 24, y: 4 },
    { x: 41.3, y: 14 },
    { x: 41.3, y: 34 },
    { x: 24, y: 44 },
    { x: 6.7, y: 34 },
    { x: 6.7, y: 14 },
  ];

  /** Unique gradient id per (tier,size) so multiple badges on a page don't clash. */
  get gid(): string {
    return 'tg' + this.tier + 'x' + Math.round(this.size);
  }
}
