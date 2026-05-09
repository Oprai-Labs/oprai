import { Component, inject } from '@angular/core';
import { ThemeService } from '@core/services/theme.service';
import { LucideAngularModule } from 'lucide-angular';

@Component({
  selector: 'app-theme-toggle',
  standalone: true,
  imports: [LucideAngularModule],
  templateUrl: './theme-toggle.component.html',
  styleUrl: './theme-toggle.component.scss',
})
export class ThemeToggleComponent {
  readonly themeService = inject(ThemeService);

  toggle(): void {
    this.themeService.toggle();
  }
}
