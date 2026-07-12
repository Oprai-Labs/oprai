import { Component, Input, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Transparent inline sparkline — no card background, no axis decoration,
 * sits flush on the page so the chart reads as part of the heading rather
 * than a separate widget. Stroke colour follows the 7-day delta (green up /
 * red down) and the area below the line gets a soft gradient fade matching
 * the same hue.
 */
@Component({
  selector: 'app-balance-sparkline',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="sparkline" [class.sparkline--empty]="series().length < 2">
      @if (series().length >= 2) {
        <svg
          class="sparkline-svg"
          [attr.viewBox]="viewBox()"
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient [attr.id]="gradId" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" [attr.stop-color]="strokeColor()" stop-opacity="0.32" />
              <stop offset="100%" [attr.stop-color]="strokeColor()" stop-opacity="0" />
            </linearGradient>
          </defs>
          <!-- Filled area under the curve -->
          <path
            class="sparkline-area"
            [attr.d]="areaPath()"
            [attr.fill]="'url(#' + gradId + ')'"
          />
          <!-- The line itself -->
          <path
            class="sparkline-line"
            [attr.d]="linePath()"
            [attr.stroke]="strokeColor()"
            stroke-width="1.6"
            fill="none"
            stroke-linecap="round"
            stroke-linejoin="round"
            vector-effect="non-scaling-stroke"
          />
          <!-- End-of-series marker dot -->
          <circle
            class="sparkline-dot"
            [attr.cx]="lastPoint().x"
            [attr.cy]="lastPoint().y"
            r="2.4"
            [attr.fill]="strokeColor()"
            vector-effect="non-scaling-stroke"
          />
        </svg>
      }
    </div>
  `,
  styles: [`
    :host {
      display: block;
      width: 100%;
      max-width: 360px;
    }
    .sparkline {
      width: 100%;
      height: 44px;
      position: relative;
    }
    .sparkline--empty {
      // Shimmer placeholder while the price history is loading. Keeps the
      // layout stable so the page doesn't shift up by 44px when data arrives.
      background: linear-gradient(
        90deg,
        transparent 0%,
        color-mix(in srgb, var(--op-text-secondary) 8%, transparent) 50%,
        transparent 100%
      );
      background-size: 200% 100%;
      animation: spark-shimmer 1.2s ease-in-out infinite;
      border-radius: 6px;
      opacity: 0.4;
    }
    .sparkline-svg {
      width: 100%;
      height: 100%;
      display: block;
    }
    @keyframes spark-shimmer {
      0%   { background-position:  200% 0; }
      100% { background-position: -200% 0; }
    }
  `],
})
export class BalanceSparklineComponent {
  /** Y-axis values — typically portfolio USD value, oldest first. */
  @Input() set points(value: number[]) {
    this._points.set(value ?? []);
  }

  private readonly _points = signal<number[]>([]);
  // Stable id so multiple sparklines on a page don't share a gradient ref.
  readonly gradId = `spark-grad-${Math.random().toString(36).slice(2, 9)}`;

  // SVG drawing space — viewBox lets us layout in arbitrary units while the
  // outer container sets the rendered size. preserveAspectRatio="none" lets
  // it stretch to the container width.
  private readonly W = 200;
  private readonly H = 44;
  // Padding so the line + dot don't get clipped at the edges.
  private readonly PAD_X = 2;
  private readonly PAD_Y = 4;

  readonly viewBox = computed(() => `0 0 ${this.W} ${this.H}`);

  readonly series = computed(() => {
    const pts = this._points();
    if (pts.length < 2) return [] as Array<{ x: number; y: number }>;
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const range = max - min || 1;
    const step = (this.W - this.PAD_X * 2) / (pts.length - 1);
    return pts.map((v, i) => ({
      x: this.PAD_X + i * step,
      // SVG y grows downward — invert the proportion.
      y: this.PAD_Y + (1 - (v - min) / range) * (this.H - this.PAD_Y * 2),
    }));
  });

  readonly linePath = computed(() => {
    const s = this.series();
    if (!s.length) return '';
    return s.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
  });

  readonly areaPath = computed(() => {
    const s = this.series();
    if (!s.length) return '';
    const baseline = this.H - this.PAD_Y;
    const top = s
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`)
      .join(' ');
    return `${top} L${s[s.length - 1].x.toFixed(2)},${baseline} L${s[0].x.toFixed(2)},${baseline} Z`;
  });

  readonly lastPoint = computed(() => {
    const s = this.series();
    return s[s.length - 1] ?? { x: 0, y: 0 };
  });

  readonly strokeColor = computed(() => {
    const pts = this._points();
    if (pts.length < 2) return 'var(--op-text-secondary)';
    return pts[pts.length - 1] >= pts[0] ? 'var(--op-gain, #22c55e)' : 'var(--op-loss, #ef4444)';
  });
}
