import { Injectable, signal, effect } from '@angular/core';
import { TR } from './translations';

export type Lang = 'en' | 'tr';

const LANG_KEY = 'oprai-lang';

/** Runtime translations, keyed by the English source string.
 *
 * The key IS the English text — `t('Base fee')`, not `t('card.fee.label')`.
 * That one choice is what makes this safe to roll out a screen at a time:
 * a string nobody has translated yet renders its own key, which is already
 * the correct English, so a missing entry looks like today rather than like
 * `card.fee.label` leaking into the interface. There is no state in which a
 * user can see an identifier.
 */
@Injectable({ providedIn: 'root' })
export class TranslateService {
  private readonly _lang = signal<Lang>(this.load());
  readonly lang = this._lang.asReadonly();

  constructor() {
    effect(() => {
      const lang = this._lang();
      try {
        localStorage.setItem(LANG_KEY, lang);
      } catch {
        /* private mode — the choice just does not persist */
      }
      if (typeof document !== 'undefined') {
        document.documentElement.lang = lang;
      }
    });
  }

  setLang(lang: Lang): void {
    this._lang.set(lang);
  }

  /** Translate `key`, filling `{name}` placeholders from `params`. */
  t(key: string, params?: Record<string, string | number>): string {
    const table = this._lang() === 'tr' ? TR : undefined;
    let out = table?.[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        out = out.split(`{${k}}`).join(String(v));
      }
    }
    return out;
  }

  private load(): Lang {
    try {
      const saved = localStorage.getItem(LANG_KEY);
      if (saved === 'tr' || saved === 'en') return saved;
    } catch {
      /* fall through to the browser's preference */
    }
    if (typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('tr')) {
      return 'tr';
    }
    return 'en';
  }
}
