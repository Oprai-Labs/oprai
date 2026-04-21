import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-card-grid',
  standalone: true,
  template: `
    <div class="sk-card-grid" [style.grid-template-columns]="gridCols">
      @for (i of items; track i) {
        <div class="sk-card sk-grid-card stagger-{{ i }}">
          <div class="sk sk-lg" [style.height]="imageHeight"></div>
          <div class="sk-grid-card__body">
            <div class="sk" style="width: 70%; height: 14px;"></div>
            <div class="sk" style="width: 50%; height: 12px;"></div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .sk-card-grid {
      display: grid;
      gap: 16px;
    }
    @media (max-width: 768px) { .sk-card-grid { grid-template-columns: repeat(2, 1fr) !important; } }
    @media (max-width: 480px) { .sk-card-grid { grid-template-columns: 1fr !important; } }
    .sk-grid-card { overflow: hidden; }
    .sk-grid-card__body { padding: 16px; display: flex; flex-direction: column; gap: 8px; }
  `],
})
export class SkeletonCardGridComponent {
  @Input() count = 6;
  @Input() columns = 3;
  @Input() imageHeight = '140px';

  get items(): number[] {
    return Array.from({ length: this.count }, (_, i) => i + 1);
  }

  get gridCols(): string {
    return `repeat(${this.columns}, 1fr)`;
  }
}
