import { Injectable, signal, effect, computed } from '@angular/core';

export type ThemePreference = 'dark' | 'light' | 'system';
export type Theme = 'dark' | 'light';

const THEME_KEY = 'oprai-theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly _preference = signal<ThemePreference>(this.loadPreference());
  private readonly _systemTheme = signal<Theme>(this.getSystemTheme());

  readonly preference = this._preference.asReadonly();
  readonly theme = computed<Theme>(() => {
    const pref = this._preference();
    return pref === 'system' ? this._systemTheme() : pref;
  });
  readonly isDark = () => this.theme() === 'dark';

  constructor() {
    effect(() => {
      const theme = this.theme();
      this.applyTheme(theme);
    });

    effect(() => {
      const pref = this._preference();
      localStorage.setItem(THEME_KEY, pref);
    });

    if (typeof window !== 'undefined' && window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        this._systemTheme.set(e.matches ? 'dark' : 'light');
      });
    }
  }

  initialize(): void {
    this.applyTheme(this.theme());
  }

  toggle(): void {
    const current = this.theme();
    this.setPreference(current === 'dark' ? 'light' : 'dark');
  }

  setTheme(theme: Theme): void {
    this.setPreference(theme);
  }

  setPreference(pref: ThemePreference): void {
    this._preference.set(pref);
  }

  private loadPreference(): ThemePreference {
    if (typeof localStorage === 'undefined') return 'system';
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'dark' || stored === 'light' || stored === 'system') return stored;
    return 'system';
  }

  private getSystemTheme(): Theme {
    if (typeof window !== 'undefined' && window.matchMedia) {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    return 'light';
  }

  private applyTheme(theme: Theme): void {
    if (typeof document === 'undefined') return;
    const html = document.documentElement;
    html.classList.remove('dark', 'light');
    html.classList.add(theme);
  }
}
