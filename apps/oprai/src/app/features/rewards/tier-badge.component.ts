import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * OPRAI tier emblem — a ribboned medal whose colour escalates per tier
 * (Bronze → Legend). A classic rank medallion with a star, tinted to the app's
 * palette. `tier` selects the colour via the caller; the shape is constant so
 * the six tiers read as one coherent medal set.
 */
@Component({
  selector: 'app-tier-badge',
  standalone: true,
  imports: [CommonModule],
  template: `
    <svg [attr.width]="size" [attr.height]="size" viewBox="0 0 48 48" fill="none"
         xmlns="http://www.w3.org/2000/svg" [style.filter]="glow ? 'drop-shadow(0 0 7px ' + color + '66)' : null">
      <defs>
        <linearGradient [attr.id]="gid" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" [attr.stop-color]="color" stop-opacity="0.95" />
          <stop offset="1" [attr.stop-color]="color" stop-opacity="0.55" />
        </linearGradient>
      </defs>

      <!-- ribbons behind the medallion -->
      <path d="M16 4 L23.2 21 L19 23 L12 7 Z" [attr.fill]="color" fill-opacity="0.55" />
      <path d="M32 4 L24.8 21 L29 23 L36 7 Z" [attr.fill]="color" fill-opacity="0.55" />

      <!-- medallion -->
      <circle cx="24" cy="31" r="13" [attr.fill]="'url(#' + gid + ')'"
              [attr.stroke]="color" stroke-width="1.6" />
      <circle cx="24" cy="31" r="9.6" fill="none" [attr.stroke]="color" stroke-opacity="0.5" stroke-width="1" />

      <!-- star -->
      <path d="M24 25.5 L25.4 29.1 L29.2 29.3 L26.3 31.7 L27.2 35.5 L24 33.4 L20.8 35.5 L21.7 31.7 L18.8 29.3 L22.6 29.1 Z"
            fill="#fff" fill-opacity="0.92" />
    </svg>
  `,
})
export class TierBadgeComponent {
  @Input() tier = 1;
  @Input() color = '#5b5fc7';
  @Input() size = 40;
  /** Soft outer glow — on for the hero badge, off for the small ladder ones. */
  @Input() glow = false;

  /** Unique gradient id per (tier,size) so multiple badges on a page don't clash. */
  get gid(): string {
    return 'tg' + this.tier + 'x' + Math.round(this.size);
  }
}
