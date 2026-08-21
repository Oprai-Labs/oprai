import { Pipe, PipeTransform, inject } from '@angular/core';
import { TranslateService } from './translate.service';

/** `{{ 'Base fee' | t }}` — see TranslateService for why the key is English.
 *
 * Impure so a language switch repaints without a reload, which is what the
 * settings toggle needs. The work behind it is one map lookup, and the last
 * result is memoised per pipe instance, so a change-detection pass over a
 * table of a hundred labels costs a hundred string comparisons.
 */
@Pipe({ name: 't', standalone: true, pure: false })
export class TPipe implements PipeTransform {
  private readonly i18n = inject(TranslateService);
  private lastKey?: string;
  private lastLang?: string;
  private lastParams?: string;
  private lastValue = '';

  transform(key: string | null | undefined, params?: Record<string, string | number | null | undefined>): string {
    if (!key) return '';
    const lang = this.i18n.lang();
    const paramKey = params ? JSON.stringify(params) : '';
    if (key === this.lastKey && lang === this.lastLang && paramKey === this.lastParams) {
      return this.lastValue;
    }
    this.lastKey = key;
    this.lastLang = lang;
    this.lastParams = paramKey;
    this.lastValue = this.i18n.t(key, params);
    return this.lastValue;
  }
}
