import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-timeline',
  standalone: true,
  template: `
    <div class="sk-timeline">
      @for (i of items; track i) {
        <div class="sk-timeline__entry stagger-{{ i }}">
          <div class="sk-timeline__dot">
            <div class="sk sk-circle" style="width: 36px; height: 36px;"></div>
          </div>
          <div class="sk-timeline__content">
            <div class="sk" style="width: 60%; height: 14px;"></div>
            <div class="sk" style="width: 40%; height: 12px; margin-top: 6px;"></div>
          </div>
          <div class="sk" style="width: 70px; height: 12px; flex-shrink: 0;"></div>
        </div>
      }
    </div>
  `,
  styles: [`
    .sk-timeline {
      position: relative;
      padding-left: 20px;
    }
    .sk-timeline::before {
      content: '';
      position: absolute;
      left: 37px;
      top: 0;
      bottom: 0;
      width: 2px;
      background: var(--op-border-subtle);
      border-radius: 1px;
    }
    .sk-timeline__entry {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 14px 0;
    }
    .sk-timeline__dot {
      flex-shrink: 0;
      z-index: 1;
    }
    .sk-timeline__content {
      flex: 1;
      min-width: 0;
    }
  `],
})
export class SkeletonTimelineComponent {
  @Input() count = 5;
  get items(): number[] {
    return Array.from({ length: this.count }, (_, i) => i + 1);
  }
}
