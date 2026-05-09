import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-table',
  standalone: true,
  template: `
    <div class="sk-card sk-table">
      <!-- Header row -->
      <div class="sk-row sk-table__header">
        @for (w of columnWidths; track $index) {
          <div class="sk" [style.width]="w" style="height: 10px;"></div>
        }
      </div>
      <!-- Body rows -->
      @for (r of rowItems; track r) {
        <div class="sk-row stagger-{{ r }}">
          @for (w of columnWidths; track $index) {
            <div class="sk" [style.width]="w" style="height: 14px;"></div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .sk-table__header {
      height: 40px;
      background: var(--op-bg-surface-2);
    }
  `],
})
export class SkeletonTableComponent {
  @Input() rows = 8;
  @Input() columns = 6;
  @Input() columnWidths: string[] = [];

  get rowItems(): number[] {
    return Array.from({ length: this.rows }, (_, i) => i + 1);
  }

  get effectiveWidths(): string[] {
    if (this.columnWidths.length > 0) return this.columnWidths;
    return Array.from({ length: this.columns }, () => '80px');
  }
}
