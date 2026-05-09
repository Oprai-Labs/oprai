import { Component, Input, Output, EventEmitter, ContentChild, TemplateRef, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface TableColumn {
  key: string;
  label: string;
  sortable?: boolean;
  align?: 'left' | 'center' | 'right';
  width?: string;
}

@Component({
  selector: 'app-data-table',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="data-table" [class.data-table--compact]="density === 'compact'">
      <div class="data-table__wrapper">
        <table>
          <thead>
            <tr>
              @for (col of columns; track col.key) {
                <th
                  [class.data-table__th--sortable]="col.sortable"
                  [class.data-table__th--active]="sortKey() === col.key"
                  [style.text-align]="col.align || 'left'"
                  [style.width]="col.width"
                  (click)="col.sortable ? onSort(col.key) : null"
                >
                  {{ col.label }}
                  @if (col.sortable && sortKey() === col.key) {
                    <span class="data-table__sort-icon">
                      {{ sortDirection() === 'asc' ? '\u25B2' : '\u25BC' }}
                    </span>
                  }
                </th>
              }
            </tr>
          </thead>
          <tbody>
            @if (loading) {
              @for (i of skeletonRows; track i) {
                <tr class="data-table__skeleton-row">
                  @for (col of columns; track col.key) {
                    <td>
                      <div class="sk" [style.width]="getSkeletonWidth(col)" style="height: 14px;"></div>
                    </td>
                  }
                </tr>
              }
            } @else if (rows.length === 0) {
              <tr>
                <td [attr.colspan]="columns.length" class="data-table__empty">
                  {{ emptyMessage }}
                </td>
              </tr>
            } @else {
              @for (row of rows; track trackByFn ? trackByFn(row) : $index; let i = $index) {
                <tr
                  class="data-table__row"
                  [class.data-table__row--clickable]="rowClick.observed"
                  (click)="rowClick.emit(row)"
                >
                  @if (rowTemplate) {
                    <ng-container *ngTemplateOutlet="rowTemplate; context: { $implicit: row, index: i }" />
                  } @else {
                    @for (col of columns; track col.key) {
                      <td [style.text-align]="col.align || 'left'">{{ row[col.key] }}</td>
                    }
                  }
                </tr>
              }
            }
          </tbody>
        </table>
      </div>
    </div>
  `,
  styles: [`
    .data-table { width: 100%; overflow: hidden; }
    .data-table__wrapper { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    thead { position: sticky; top: 0; z-index: 1; }
    th {
      padding: 10px 16px;
      font-size: 12px;
      font-weight: 500;
      color: var(--op-text-secondary, #8b95a8);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      background: var(--op-bg-surface-1, #111520);
      white-space: nowrap;
      user-select: none;
    }
    .data-table__th--sortable { cursor: pointer; }
    .data-table__th--sortable:hover { color: var(--op-text-primary, #e8ecf4); }
    .data-table__th--active { color: var(--op-brand, #6366F1); }
    .data-table__sort-icon { font-size: 8px; margin-left: 4px; }
    td {
      padding: 12px 16px;
      font-size: 13px;
      color: var(--op-text-primary, #e8ecf4);
      border-bottom: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      white-space: nowrap;
    }
    .data-table--compact td { padding: 8px 12px; font-size: 12px; }
    .data-table--compact th { padding: 8px 12px; }
    .data-table__row { transition: background 100ms ease; }
    .data-table__row--clickable { cursor: pointer; }
    .data-table__row:hover { background: var(--op-hover, rgba(255,255,255,0.04)); }
    .data-table__skeleton-row td { padding: 14px 16px; }
    .data-table__empty {
      text-align: center;
      padding: 48px 16px;
      color: var(--op-text-tertiary, #5a6478);
      font-size: 14px;
    }
  `]
})
export class DataTableComponent {
  @Input() columns: TableColumn[] = [];
  @Input() rows: any[] = [];
  @Input() loading = false;
  @Input() emptyMessage = 'No data available';
  @Input() density: 'normal' | 'compact' = 'normal';
  @Input() trackByFn: ((row: any) => any) | null = null;

  @Output() rowClick = new EventEmitter<any>();
  @Output() sortChange = new EventEmitter<{ key: string; direction: 'asc' | 'desc' }>();

  @ContentChild('rowTemplate') rowTemplate: TemplateRef<any> | null = null;

  readonly sortKey = signal<string | null>(null);
  readonly sortDirection = signal<'asc' | 'desc'>('desc');

  readonly skeletonRows = Array.from({ length: 8 }, (_, i) => i);

  onSort(key: string): void {
    if (this.sortKey() === key) {
      this.sortDirection.update(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      this.sortKey.set(key);
      this.sortDirection.set('desc');
    }
    this.sortChange.emit({ key, direction: this.sortDirection() });
  }

  getSkeletonWidth(col: TableColumn): string {
    const widths: Record<string, string> = { price: '60px', change: '50px', volume: '70px', name: '120px' };
    return widths[col.key] || '80px';
  }
}
