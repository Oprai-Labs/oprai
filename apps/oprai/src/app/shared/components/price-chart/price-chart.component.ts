import { Component, Input, Output, EventEmitter, ElementRef, ViewChild, OnChanges, OnDestroy, SimpleChanges, signal, AfterViewInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  createChart, IChartApi, ISeriesApi, ColorType,
  CandlestickData, LineData, AreaData, Time,
  CandlestickSeries, LineSeries, AreaSeries,
} from 'lightweight-charts';

export type ChartType = 'candlestick' | 'area' | 'line';
export type ChartTimeframe = '1H' | '4H' | '1D' | '1W' | '1M';

export interface CandleDataPoint {
  time: number; // unix timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface LineDataPoint {
  time: number;
  value: number;
}

@Component({
  selector: 'app-price-chart',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="price-chart">
      @if (showTimeframes) {
        <div class="price-chart__timeframes">
          @for (tf of timeframes; track tf) {
            <button
              class="price-chart__tf-btn"
              [class.price-chart__tf-btn--active]="activeTimeframe() === tf"
              (click)="onTimeframeChange(tf)"
            >
              {{ tf }}
            </button>
          }
        </div>
      }
      <div #chartContainer class="price-chart__container" [style.height.px]="height"></div>
    </div>
  `,
  styles: [`
    .price-chart { width: 100%; }
    .price-chart__timeframes {
      display: flex;
      gap: 4px;
      padding: 0 0 12px;
    }
    .price-chart__tf-btn {
      padding: 4px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      font-family: var(--op-font-mono, monospace);
      color: var(--op-text-secondary, #8b95a8);
      background: transparent;
      border: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      cursor: pointer;
      transition: all 150ms ease;
    }
    .price-chart__tf-btn:hover {
      color: var(--op-text-primary, #e8ecf4);
      border-color: var(--op-border-default, rgba(255,255,255,0.10));
    }
    .price-chart__tf-btn--active {
      color: var(--op-brand, #6366F1);
      border-color: var(--op-brand, #6366F1);
      background: var(--op-brand-subtle, rgba(99,102,241,0.10));
    }
    .price-chart__container { width: 100%; }
  `]
})
export class PriceChartComponent implements AfterViewInit, OnChanges, OnDestroy {
  @ViewChild('chartContainer') chartContainer!: ElementRef<HTMLDivElement>;

  @Input() candleData: CandleDataPoint[] = [];
  @Input() lineData: LineDataPoint[] = [];
  @Input() chartType: ChartType = 'area';
  @Input() height = 300;
  @Input() showTimeframes = true;
  @Input() timeframes: ChartTimeframe[] = ['1H', '4H', '1D', '1W', '1M'];
  @Input() initialTimeframe: ChartTimeframe = '1D';

  readonly activeTimeframe = signal<ChartTimeframe>('1D');
  @Output() readonly timeframeChanged = new EventEmitter<ChartTimeframe>();

  private chart: IChartApi | null = null;
  private series: ISeriesApi<any> | null = null;
  private resizeObserver: ResizeObserver | null = null;

  ngAfterViewInit(): void {
    this.activeTimeframe.set(this.initialTimeframe);
    this.createChart();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if ((changes['candleData'] || changes['lineData'] || changes['chartType']) && this.chart) {
      this.updateData();
    }
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.chart?.remove();
    this.chart = null;
  }

  onTimeframeChange(tf: ChartTimeframe): void {
    this.activeTimeframe.set(tf);
    this.timeframeChanged.emit(tf);
  }

  private createChart(): void {
    if (!this.chartContainer?.nativeElement) return;

    const isDark = document.documentElement.classList.contains('dark');

    this.chart = createChart(this.chartContainer.nativeElement, {
      height: this.height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: isDark ? '#8B95A8' : '#475569',
        fontFamily: "'Inter', sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)' },
      },
      crosshair: {
        vertLine: { color: isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.2)', width: 1, style: 3, labelBackgroundColor: isDark ? '#232A3D' : '#e8ecf2' },
        horzLine: { color: isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.2)', width: 1, style: 3, labelBackgroundColor: isDark ? '#232A3D' : '#e8ecf2' },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    });

    this.updateData();

    // Auto-resize
    this.resizeObserver = new ResizeObserver(() => {
      if (this.chart && this.chartContainer?.nativeElement) {
        this.chart.applyOptions({ width: this.chartContainer.nativeElement.clientWidth });
      }
    });
    this.resizeObserver.observe(this.chartContainer.nativeElement);
  }

  private updateData(): void {
    if (!this.chart) return;

    // Remove existing series
    if (this.series) {
      this.chart.removeSeries(this.series);
      this.series = null;
    }

    const isDark = document.documentElement.classList.contains('dark');

    if (this.chartType === 'candlestick' && this.candleData.length > 0) {
      this.series = this.chart.addSeries(CandlestickSeries, {
        upColor: '#34D399',
        downColor: '#F87171',
        borderUpColor: '#34D399',
        borderDownColor: '#F87171',
        wickUpColor: '#34D399',
        wickDownColor: '#F87171',
      });
      const data: CandlestickData[] = this.candleData.map(d => ({
        time: d.time as Time,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }));
      this.series!.setData(data as any);
    } else if (this.chartType === 'line' && this.lineData.length > 0) {
      this.series = this.chart.addSeries(LineSeries, {
        color: isDark ? '#3B82F6' : '#2563EB',
        lineWidth: 2,
      });
      const data: LineData[] = this.lineData.map(d => ({
        time: d.time as Time,
        value: d.value,
      }));
      this.series!.setData(data as any);
    } else if (this.lineData.length > 0) {
      // area (default)
      this.series = this.chart.addSeries(AreaSeries, {
        lineColor: isDark ? '#3B82F6' : '#2563EB',
        topColor: isDark ? 'rgba(59,130,246,0.3)' : 'rgba(37,99,235,0.3)',
        bottomColor: isDark ? 'rgba(59,130,246,0.02)' : 'rgba(37,99,235,0.02)',
        lineWidth: 2,
      });
      const data: AreaData[] = this.lineData.map(d => ({
        time: d.time as Time,
        value: d.value,
      }));
      this.series!.setData(data as any);
    }

    this.chart.timeScale().fitContent();
  }
}
