import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-skeleton-loader',
  standalone: true,
  template: `
    <div
      class="sk"
      [class.sk-circle]="variant === 'circle'"
      [class.sk-rounded]="variant === 'badge'"
      [style.width]="width"
      [style.height]="height"
      [style.border-radius]="borderRadius"
    ></div>
  `,
  styles: [`:host { display: inline-block; }`],
})
export class SkeletonLoaderComponent {
  @Input() width = '100%';
  @Input() height = '16px';
  @Input() borderRadius?: string;
  @Input() variant: 'text' | 'circle' | 'rect' | 'badge' = 'text';
}
