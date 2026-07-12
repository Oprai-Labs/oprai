import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { ThemeService, ThemePreference } from '@core/services/theme.service';

interface ThemeOption {
  value: ThemePreference;
  label: string;
  icon: string;
}

interface LanguageOption {
  value: 'en';
  label: string;
}

@Component({
  selector: 'app-theme-switcher',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule],
  templateUrl: './theme-switcher.component.html',
  styleUrl: './theme-switcher.component.scss',
})
export class ThemeSwitcherComponent {
  private readonly themeService = inject(ThemeService);
  readonly preference = this.themeService.preference;

  readonly options: ThemeOption[] = [
    { value: 'light', label: 'Light', icon: 'sun' },
    { value: 'dark', label: 'Dark', icon: 'moon' },
    { value: 'system', label: 'System', icon: 'monitor' },
  ];

  readonly languageOptions: LanguageOption[] = [
    { value: 'en', label: 'English' },
  ];

  selectedLanguage: 'en' = 'en';

  setTheme(pref: ThemePreference): void {
    this.themeService.setPreference(pref);
  }
}
