import { Component, Input, signal, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-sparkline',
  standalone: true,
  imports: [CommonModule],
  template: `
    <svg
      [attr.width]="width"
      [attr.height]="height"
      [attr.viewBox]="'0 0 ' + width + ' ' + height"
      class="sparkline"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient [attr.id]="gradientId" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" [attr.stop-color]="strokeColor()" stop-opacity="0.3" />
          <stop offset="100%" [attr.stop-color]="strokeColor()" stop-opacity="0" />
        </linearGradient>
      </defs>
      @if (areaPath()) {
        <path [attr.d]="areaPath()" [attr.fill]="'url(#' + gradientId + ')'" />
      }
      @if (linePath()) {
        <path
          [attr.d]="linePath()"
          fill="none"
          [attr.stroke]="strokeColor()"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      }
    </svg>
  `,
  styles: [`
    :host { display: inline-flex; }
    .sparkline { display: block; }
  `]
})
export class SparklineComponent implements OnChanges {
  @Input() data: number[] = [];
  @Input() width = 60;
  @Input() height = 24;

  private static instanceCount = 0;
  readonly gradientId = `sparkline-grad-${SparklineComponent.instanceCount++}`;

  readonly linePath = signal<string | null>(null);
  readonly areaPath = signal<string | null>(null);
  readonly strokeColor = signal('#34d399');

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['data']) {
      this.buildPaths();
    }
  }

  private buildPaths(): void {
    if (!this.data || this.data.length < 2) {
      this.linePath.set(null);
      this.areaPath.set(null);
      return;
    }

    const min = Math.min(...this.data);
    const max = Math.max(...this.data);
    const range = max - min || 1;
    const padding = 2;
    const w = this.width;
    const h = this.height;
    const usableH = h - padding * 2;

    const points = this.data.map((val, i) => ({
      x: (i / (this.data.length - 1)) * w,
      y: padding + usableH - ((val - min) / range) * usableH,
    }));

    const lineD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const areaD = `${lineD} L${w},${h} L0,${h} Z`;

    this.linePath.set(lineD);
    this.areaPath.set(areaD);

    // Color: green if trending up, red if trending down
    const trend = this.data[this.data.length - 1] - this.data[0];
    this.strokeColor.set(trend >= 0 ? '#34d399' : '#f87171');
  }
}
