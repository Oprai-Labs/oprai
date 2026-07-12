import { Component, Input, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface ChartSegment {
  label: string;
  value: number;
  color: string;
  logoUri?: string | null;
}

const DEFAULT_COLORS = [
  '#6366F1', // Brand Indigo (SOL)
  '#06B6D4', // Brand Cyan
  '#F59E0B', // Amber
  '#10B981', // Emerald
  '#EF4444', // Red
  '#3B82F6', // Blue
  '#EC4899', // Pink
  '#8B5CF6', // Violet
];

const RADIUS = 70;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
/** Minimum visual arc: segments below this get boosted so they render cleanly */
const MIN_VISUAL_PERCENT = 2.5;
/**
 * Gap (in circumference px) carved out of the *end* of every segment so the
 * background track shows through between adjacent colours. Without it, two
 * 28px-wide strokes butt directly against each other and the anti-aliased
 * seam reads as a smeared/misaligned join — the "kaymalar" the donut had.
 */
const SEGMENT_GAP = 4;

@Component({
  selector: 'app-allocation-chart',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './allocation-chart.component.html',
  styleUrl: './allocation-chart.component.scss',
})
export class AllocationChartComponent {
  @Input() set segments(value: ChartSegment[]) {
    this._segments.set(value);
  }
  @Input() totalValue = 0;

  private readonly _segments = signal<ChartSegment[]>([]);

  readonly displaySegments = computed(() => {
    const segs = this._segments();
    if (segs.length === 0) return [];

    const total = segs.reduce((s, seg) => s + seg.value, 0);
    if (total === 0) return [];

    // Top 5 + "Other"
    const sorted = [...segs].sort((a, b) => b.value - a.value);
    const top = sorted.slice(0, 5);
    const rest = sorted.slice(5);

    const result = top.map((seg, i) => ({
      ...seg,
      color: seg.color || DEFAULT_COLORS[i % DEFAULT_COLORS.length],
      percent: (seg.value / total) * 100,
      logoUri: seg.logoUri ?? null,
    }));

    if (rest.length > 0) {
      const otherValue = rest.reduce((s, seg) => s + seg.value, 0);
      result.push({
        label: 'Other',
        value: otherValue,
        color: '#64748B',
        percent: (otherValue / total) * 100,
        logoUri: null,
      });
    }

    return result;
  });

  /** SVG arc data — uses single path per segment for clean rendering */
  readonly svgArcs = computed(() => {
    const segs = this.displaySegments();
    if (segs.length === 0) return [];

    // Single segment: full ring, no gaps
    if (segs.length === 1) {
      return [{
        color: segs[0].color,
        dashArray: `${CIRCUMFERENCE} 0`,
        dashOffset: 0,
      }];
    }

    // Apply minimum visual arc: boost tiny segments so they don't look broken
    const visualPercents = segs.map((s) =>
      Math.max(s.percent, MIN_VISUAL_PERCENT)
    );
    // Normalize back to 100%
    const rawTotal = visualPercents.reduce((a, b) => a + b, 0);

    let offset = 0;
    return segs.map((seg, i) => {
      const normalizedPercent = (visualPercents[i] / rawTotal) * 100;
      const arcLength = (normalizedPercent / 100) * CIRCUMFERENCE;
      // Draw the stroke `SEGMENT_GAP` shorter than the slot it occupies, but
      // still advance the offset by the *full* slot. That leaves a clean
      // background gap between this segment and the next instead of a
      // butted seam. Clamp so a tiny boosted segment never goes negative.
      const dashLength = Math.max(arcLength - SEGMENT_GAP, 1);
      const arc = {
        color: seg.color,
        dashArray: `${dashLength} ${CIRCUMFERENCE - dashLength}`,
        dashOffset: -offset,
      };
      offset += arcLength;
      return arc;
    });
  });

  readonly svgRadius = RADIUS;

  onImageError(event: Event): void {
    (event.target as HTMLElement).style.display = 'none';
    const parent = (event.target as HTMLElement).parentElement;
    const fallback = parent?.querySelector('.legend-dot-fallback') as HTMLElement;
    if (fallback) fallback.style.display = 'flex';
  }
}
