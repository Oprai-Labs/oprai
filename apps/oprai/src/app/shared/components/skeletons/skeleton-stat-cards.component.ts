import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-stat-cards',
  standalone: true,
  template: `
    <div class="sk-stats-grid">
      @for (i of items; track i) {
        <div class="sk-card sk-stat-card stagger-{{ i }}">
          <div class="sk-stat-card__row">
            <div class="sk sk-circle" style="width: 44px; height: 44px; flex-shrink: 0;"></div>
            <div class="sk-stat-card__text">
              <div class="sk" style="width: 80px; height: 12px;"></div>
              <div class="sk" style="width: 100px; height: 24px;"></div>
            </div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .sk-stats-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 28px;
    }
    @media (max-width: 1024px) { .sk-stats-grid { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 640px) { .sk-stats-grid { grid-template-columns: 1fr; gap: 12px; } }
    .sk-stat-card { padding: 20px; }
    .sk-stat-card__row { display: flex; align-items: center; gap: 14px; }
    .sk-stat-card__text { display: flex; flex-direction: column; gap: 8px; }
  `],
})
export class SkeletonStatCardsComponent {
  @Input() count = 4;
  get items(): number[] {
    return Array.from({ length: this.count }, (_, i) => i + 1);
  }
}
