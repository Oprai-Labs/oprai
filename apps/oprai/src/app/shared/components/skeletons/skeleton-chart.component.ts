import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-chart',
  standalone: true,
  template: `
    <div class="sk-card sk-chart">
      <div class="sk-chart__header">
        <div class="sk" style="width: 120px; height: 16px;"></div>
        @if (showBadge) {
          <div class="sk sk-rounded" style="width: 60px; height: 22px;"></div>
        }
      </div>
      <div class="sk-chart__body">
        <div class="sk sk-lg" [style.height]="bodyHeight" style="width: 100%;"></div>
      </div>
    </div>
  `,
  styles: [`
    .sk-chart__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--op-border-subtle);
    }
    .sk-chart__body {
      padding: 20px;
    }
  `],
})
export class SkeletonChartComponent {
  @Input() bodyHeight = '180px';
  @Input() showBadge = true;
}
