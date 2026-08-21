import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { ThemeService, ThemePreference } from '@core/services/theme.service';
import { TranslateService, TPipe, type Lang } from '@core/i18n';

interface ThemeOption {
  value: ThemePreference;
  label: string;
  icon: string;
}

interface LanguageOption {
  value: Lang;
  /** Written in its own language — a language list you cannot read is no use. */
  label: string;
}

@Component({
  selector: 'app-theme-switcher',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule, TPipe],
  templateUrl: './theme-switcher.component.html',
  styleUrl: './theme-switcher.component.scss',
})
export class ThemeSwitcherComponent {
  private readonly themeService = inject(ThemeService);
  private readonly i18n = inject(TranslateService);
  readonly preference = this.themeService.preference;

  readonly options: ThemeOption[] = [
    { value: 'light', label: 'Light', icon: 'sun' },
    { value: 'dark', label: 'Dark', icon: 'moon' },
    { value: 'system', label: 'System', icon: 'monitor' },
  ];

  readonly languageOptions: LanguageOption[] = [
    { value: 'en', label: 'English' },
    { value: 'tr', label: 'Türkçe' },
  ];

  get selectedLanguage(): Lang {
    return this.i18n.lang();
  }
  set selectedLanguage(lang: Lang) {
    this.i18n.setLang(lang);
  }

  setTheme(pref: ThemePreference): void {
    this.themeService.setPreference(pref);
  }
}
