import { Injectable, inject } from '@angular/core';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';

export type AnalyticsEventType =
  | 'app_open'
  | 'funnel'
  | 'feature_used'
  | 'error_shown'
  | 'page_view'
  | 'action';

/**
 * Product-analytics emitter. Sends meta-only events (never message content or
 * PII) to the wallet-gated `/events` endpoint so the backend can build
 * engagement + conversion funnels.
 *
 * Two hard rules: it never throws (analytics must not affect the app), and it
 * stays silent while signed out (the endpoint is wallet-gated, so firing would
 * only produce 401 noise).
 */
@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  track(
    eventType: AnalyticsEventType,
    eventName: string,
    properties?: Record<string, unknown>,
  ): void {
    if (!this.auth.isAuthenticated()) return;
    try {
      this.api
        .post('/events', {
          event_type: eventType,
          event_name: eventName,
          properties: properties ?? {},
        })
        .subscribe({ next: () => {}, error: () => {} });
    } catch {
      /* analytics must never surface to the user */
    }
  }

  appOpen(): void {
    this.track('app_open', 'app_open');
  }

  pageView(path: string): void {
    this.track('page_view', path);
  }

  featureUsed(feature: string, properties?: Record<string, unknown>): void {
    this.track('feature_used', feature, properties);
  }

  /** One step of the action conversion funnel (card_shown → confirm → submitted → outcome). */
  action(step: string, properties?: Record<string, unknown>): void {
    this.track('action', step, properties);
  }
}
