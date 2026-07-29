import { Injectable, signal } from '@angular/core';

/**
 * Detects that a NEWER build of the app has been deployed while this tab has
 * been open.
 *
 * An open single-page app keeps running the JavaScript it loaded at first
 * paint. Nothing about a deploy reaches it: hashed bundles mean the browser
 * never even asks. So a tab left open silently keeps the old behaviour, and
 * a fix that shipped an hour ago looks like it never shipped — which is
 * indistinguishable, from the user's side, from the bug not being fixed.
 *
 * The check is deliberately dumb: `index.html` is served `no-cache`, so
 * re-fetching it and comparing the entry-bundle hash is enough. No version
 * file to keep in sync, nothing to remember to bump.
 */
@Injectable({ providedIn: 'root' })
export class AppVersionService {
  /** True once a different build is live. Never flips back. */
  readonly updateAvailable = signal(false);

  private currentEntry: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  private static readonly POLL_MS = 5 * 60_000;

  start(): void {
    if (this.timer) return;
    this.currentEntry = this.entryFromDocument();
    if (!this.currentEntry) return;   // dev server / unexpected markup — skip
    this.timer = setInterval(() => {
      if (document.hidden || this.updateAvailable()) return;
      void this.check();
    }, AppVersionService.POLL_MS);
    // Also check when the tab comes back to the foreground — a laptop opened
    // the next morning is the common case, and it shouldn't wait five minutes.
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !this.updateAvailable()) void this.check();
    });
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  reload(): void {
    window.location.reload();
  }

  private async check(): Promise<void> {
    try {
      const html = await fetch('/index.html', { cache: 'no-store' }).then(r =>
        r.ok ? r.text() : null,
      );
      if (!html) return;
      const latest = AppVersionService.entryOf(html);
      if (latest && this.currentEntry && latest !== this.currentEntry) {
        this.updateAvailable.set(true);
        this.stop();
      }
    } catch {
      // Offline or a blip — the next tick tries again.
    }
  }

  private entryFromDocument(): string | null {
    return AppVersionService.entryOf(document.documentElement.outerHTML);
  }

  /** The hashed entry bundle Angular names in index.html — it changes on every
   *  build that changes any code. */
  private static entryOf(html: string): string | null {
    return html.match(/src="(main-[A-Z0-9]+\.js)"/i)?.[1] ?? null;
  }
}
