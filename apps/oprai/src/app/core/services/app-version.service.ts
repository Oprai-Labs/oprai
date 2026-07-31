import { Injectable } from '@angular/core';

/**
 * Picks up a newer build without telling the user about it.
 *
 * An open tab keeps running the JavaScript it loaded at first paint. Hashed
 * bundles mean the browser never asks for a newer one, so a deploy is
 * invisible to it: a fix that shipped minutes ago is indistinguishable, from
 * where the user sits, from a fix that never landed.
 *
 * A "new version available" banner was the obvious answer and was rejected —
 * fairly, since it makes the deploy the user's problem. So this reloads on its
 * own, and only at a moment where a reload costs nothing:
 *
 *   - the tab is in the background, and
 *   - nothing is mid-flight (no action card waiting on a wallet, no stream).
 *
 * Both conditions matter. Reloading a visible tab would move things under the
 * cursor; reloading during a signature would drop it.
 */
@Injectable({ providedIn: 'root' })
export class AppVersionService {
  private entry: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  /** Set while an action is awaiting the wallet or a stream is open. */
  private busy = 0;

  private static readonly POLL_MS = 4 * 60_000;

  start(): void {
    if (this.timer) return;
    this.entry = AppVersionService.entryOf(document.documentElement.outerHTML);
    if (!this.entry) return;           // dev server — nothing to compare
    this.timer = setInterval(() => void this.check(), AppVersionService.POLL_MS);
    // The timer alone needs the tab to be hidden at the moment a tick lands,
    // so someone working in the app for an hour could sit on a build from
    // before they started — testing a fix and seeing the old behaviour.
    // Checking the moment the tab goes away makes a glance at another window
    // enough to pick one up.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) void this.check();
    });
  }

  /** Bracket anything a reload must not interrupt. */
  hold(): void { this.busy++; }
  release(): void { this.busy = Math.max(0, this.busy - 1); }

  private async check(): Promise<void> {
    if (!document.hidden || this.busy > 0) return;
    try {
      const html = await fetch('/index.html', { cache: 'no-store' }).then(r => (r.ok ? r.text() : null));
      const latest = html ? AppVersionService.entryOf(html) : null;
      if (!latest || latest === this.entry) return;
      // Re-check the guards: the fetch took time, and the user may have come
      // back to the tab or started signing in the meantime.
      if (document.hidden && this.busy === 0) window.location.reload();
    } catch {
      // Offline or a blip — the next tick tries again.
    }
  }

  /** The hashed entry bundle Angular names in index.html; it changes on every
   *  build that changes any code. */
  private static entryOf(html: string): string | null {
    return html.match(/src="(main-[A-Z0-9]+\.js)"/i)?.[1] ?? null;
  }
}
