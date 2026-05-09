import { TestBed } from '@angular/core/testing';
import { ThemeService, Theme, ThemePreference } from './theme.service';

describe('ThemeService', () => {
  let service: ThemeService;

  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.classList.remove('light');

    TestBed.configureTestingModule({
      providers: [ThemeService],
    });
    service = TestBed.inject(ThemeService);
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.classList.remove('light');
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  // =====================
  // INITIAL STATE TESTS
  // =====================

  describe('Initial state', () => {
    it('should have theme signal defined', () => {
      expect(service.theme()).toBeDefined();
    });

    it('should have preference signal defined', () => {
      expect(service.preference()).toBeDefined();
    });

    it('should have isDark function', () => {
      expect(typeof service.isDark).toBe('function');
    });

    it('should default to system preference', () => {
      expect(service.preference()).toBe('system');
    });

    it('should return valid theme value', () => {
      const theme = service.theme();
      expect(['light', 'dark']).toContain(theme);
    });
  });

  // =====================
  // THEME SWITCHING TESTS
  // =====================

  describe('setTheme()', () => {
    it('should set theme to light', () => {
      service.setTheme('light');

      expect(service.theme()).toBe('light');
    });

    it('should set theme to dark', () => {
      service.setTheme('dark');

      expect(service.theme()).toBe('dark');
    });

    it('should apply light class to document', () => {
      service.setTheme('light');

      expect(document.documentElement.classList.contains('light')).toBe(true);
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });

    it('should apply dark class to document', () => {
      service.setTheme('dark');

      expect(document.documentElement.classList.contains('dark')).toBe(true);
      expect(document.documentElement.classList.contains('light')).toBe(false);
    });

    it('should save preference to localStorage', () => {
      service.setTheme('dark');

      expect(localStorage.getItem('oprai-theme')).toBe('dark');
    });

    it('should load preference from localStorage', () => {
      localStorage.setItem('oprai-theme', 'dark');

      // Create new service instance
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [ThemeService],
      });
      const newService = TestBed.inject(ThemeService);

      expect(newService.preference()).toBe('dark');
    });
  });

  describe('setPreference()', () => {
    it('should set preference to system', () => {
      service.setPreference('system');

      expect(service.preference()).toBe('system');
    });

    it('should set preference to light', () => {
      service.setPreference('light');

      expect(service.preference()).toBe('light');
    });

    it('should set preference to dark', () => {
      service.setPreference('dark');

      expect(service.preference()).toBe('dark');
    });

    it('should reflect system theme when preference is system', () => {
      service.setPreference('system');

      // Theme should match system
      const theme = service.theme();
      expect(['light', 'dark']).toContain(theme);
    });
  });

  // =====================
  // TOGGLE TESTS
  // =====================

  describe('toggle()', () => {
    it('should toggle from light to dark', () => {
      service.setTheme('light');
      service.toggle();

      expect(service.theme()).toBe('dark');
    });

    it('should toggle from dark to light', () => {
      service.setTheme('dark');
      service.toggle();

      expect(service.theme()).toBe('light');
    });

    it('should toggle from system to opposite of system', () => {
      service.setPreference('system');
      const systemTheme = service.theme();

      service.toggle();

      const newTheme = service.theme();
      expect(['light', 'dark']).toContain(newTheme);
      expect(newTheme).not.toBe(systemTheme);
    });
  });

  // =====================
  // IS DARK TESTS
  // =====================

  describe('isDark()', () => {
    it('should return true when theme is dark', () => {
      service.setTheme('dark');

      expect(service.isDark()).toBe(true);
    });

    it('should return false when theme is light', () => {
      service.setTheme('light');

      expect(service.isDark()).toBe(false);
    });
  });

  // =====================
  // INITIALIZE TESTS
  // =====================

  describe('initialize()', () => {
    it('should apply current theme to document', () => {
      service.setTheme('dark');
      document.documentElement.classList.remove('dark');

      service.initialize();

      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('should not throw when document is undefined', () => {
      const originalDocument = globalThis.document;
      Object.defineProperty(globalThis, 'document', { value: undefined, writable: true });

      expect(() => service.initialize()).not.toThrow();

      Object.defineProperty(globalThis, 'document', { value: originalDocument, writable: true });
    });
  });

  // =====================
  // CLASS APPLICATION TESTS
  // =====================

  describe('Document class application', () => {
    it('should add theme class to html element', () => {
      service.setTheme('dark');

      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('should remove previous theme class when switching', () => {
      service.setTheme('dark');
      expect(document.documentElement.classList.contains('dark')).toBe(true);

      service.setTheme('light');
      expect(document.documentElement.classList.contains('dark')).toBe(false);
      expect(document.documentElement.classList.contains('light')).toBe(true);
    });

    it('should handle rapid theme switches', () => {
      service.setTheme('light');
      service.setTheme('dark');
      service.setTheme('light');
      service.setTheme('dark');

      expect(document.documentElement.classList.contains('dark')).toBe(true);
      expect(localStorage.getItem('oprai-theme')).toBe('dark');
    });
  });

  // =====================
  // EDGE CASES
  // =====================

  describe('Edge cases', () => {
    it('should handle corrupted localStorage data gracefully', () => {
      localStorage.setItem('oprai-theme', 'not-a-valid-theme');

      // Should fall back to system
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [ThemeService],
      });
      const newService = TestBed.inject(ThemeService);

      expect(['dark', 'light', 'system']).toContain(newService.preference());
    });

    it('should handle missing localStorage', () => {
      const originalLocalStorage = globalThis.localStorage;
      Object.defineProperty(globalThis, 'localStorage', { value: undefined, writable: true });

      expect(() => {
        TestBed.configureTestingModule({
          providers: [ThemeService],
        });
        TestBed.inject(ThemeService);
      }).not.toThrow();

      Object.defineProperty(globalThis, 'localStorage', { value: originalLocalStorage, writable: true });
    });

    it('should handle missing window.matchMedia', () => {
      const originalMatchMedia = (window as any).matchMedia;
      Object.defineProperty(window, 'matchMedia', { value: undefined, writable: true });

      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [ThemeService],
      });
      const newService = TestBed.inject(ThemeService);
      expect(newService.theme()).toBeDefined();

      Object.defineProperty(window, 'matchMedia', { value: originalMatchMedia, writable: true });
    });
  });

  // =====================
  // TYPE TESTS
  // =====================

  describe('Type definitions', () => {
    it('should accept all valid Theme values', () => {
      const themes: Theme[] = ['light', 'dark'];

      themes.forEach((theme) => {
        service.setTheme(theme);
        expect(service.theme()).toBe(theme);
      });
    });

    it('should accept all valid ThemePreference values', () => {
      const prefs: ThemePreference[] = ['light', 'dark', 'system'];

      prefs.forEach((pref) => {
        service.setPreference(pref);
        expect(service.preference()).toBe(pref);
      });
    });
  });
});
