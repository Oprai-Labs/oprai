import { Component, Input } from '@angular/core';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-loading-spinner',
  standalone: true,
  imports: [TPipe],
  templateUrl: './loading-spinner.component.html',
  styleUrl: './loading-spinner.component.scss',
})
export class LoadingSpinnerComponent {
  @Input() size: 'sm' | 'md' | 'lg' = 'md';
  @Input() message?: string;
}
