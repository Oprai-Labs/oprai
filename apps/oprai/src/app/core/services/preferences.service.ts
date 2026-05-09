/**
 * User Preferences Service
 *
 * Manages user preferences including:
 * - Theme, language, timezone
 * - Notification settings
 * - Privacy settings
 * - Trading preferences
 *
 * Connects to chat-service-py /preferences endpoints
 */

import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';

export interface NotificationSettings {
  email_enabled: boolean;
  push_enabled: boolean;
  telegram_enabled: boolean;
  webhook_enabled: boolean;
  in_app_enabled: boolean;
  price_alerts: boolean;
  position_alerts: boolean;
  transaction_notifications: boolean;
  yield_notifications: boolean;
  news_alerts: boolean;
  whale_alerts: boolean;
  system_notifications: boolean;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  quiet_hours_timezone: string;
  max_daily_notifications: number;
  max_per_type_per_day: number;
  telegram_chat_id?: string;
  webhook_url?: string;
}

export interface PrivacySettings {
  share_portfolio_data: boolean;
  share_trading_history: boolean;
  anonymous_analytics: boolean;
  show_wallet_address: boolean;
  show_portfolio_value: boolean;
  show_yield_earned: boolean;
  receive_marketing: boolean;
  participate_beta: boolean;
  history_retention_days: number;
  analytics_retention_days: number;
}

export interface TradingPreferences {
  risk_tolerance: 'conservative' | 'moderate' | 'aggressive';
  max_slippage: number;
  max_transaction_value_usd: number;
  require_confirmation: boolean;
  auto_stake_rewards: boolean;
  auto_rebalance: boolean;
  auto_compound_yield: boolean;
  preferred_dex: string[];
  preferred_lending: string[];
  preferred_staking: string[];
  enable_simulation: boolean;
  check_token_security: boolean;
  mev_protection: boolean;
  daily_limit_usd: number;
  weekly_limit_usd: number;
  monthly_limit_usd: number;
}

export interface UserPreferences {
  user_id: string;
  wallet_address: string;
  theme: string;
  language: string;
  display_name?: string;
  timezone: string;
  notifications: NotificationSettings;
  privacy: PrivacySettings;
  trading: TradingPreferences;
  created_at?: string;
  updated_at?: string;
}

@Injectable({ providedIn: 'root' })
export class PreferencesService {
  private readonly baseUrl = environment.apiBase;

  // Signal for reactive preferences
  readonly preferences = signal<UserPreferences | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  constructor(private http: HttpClient) {}

  /**
   * Load preferences from backend
   */
  async loadPreferences(): Promise<UserPreferences | null> {
    this.loading.set(true);
    this.error.set(null);

    try {
      const response = await this.http.get<{ success: boolean; preferences: UserPreferences }>(
        `${this.baseUrl}/preferences`
      ).toPromise();

      if (response?.success && response.preferences) {
        this.preferences.set(response.preferences);
        return response.preferences;
      }
      return null;
    } catch (e: any) {
      console.error('Failed to load preferences:', e);
      this.error.set(e.message || 'Failed to load preferences');
      return null;
    } finally {
      this.loading.set(false);
    }
  }

  /**
   * Update preferences (partial update)
   */
  async updatePreferences(updates: Partial<UserPreferences>): Promise<UserPreferences | null> {
    this.loading.set(true);
    this.error.set(null);

    try {
      const response = await this.http.post<{ success: boolean; preferences: UserPreferences }>(
        `${this.baseUrl}/preferences`,
        updates
      ).toPromise();

      if (response?.success && response.preferences) {
        this.preferences.set(response.preferences);
        return response.preferences;
      }
      return null;
    } catch (e: any) {
      console.error('Failed to update preferences:', e);
      this.error.set(e.message || 'Failed to update preferences');
      return null;
    } finally {
      this.loading.set(false);
    }
  }

  /**
   * Reset preferences to defaults
   */
  async resetPreferences(): Promise<boolean> {
    this.loading.set(true);
    this.error.set(null);

    try {
      const response = await this.http.delete<{ success: boolean }>(
        `${this.baseUrl}/preferences`
      ).toPromise();

      if (response?.success) {
        this.preferences.set(null);
        await this.loadPreferences();
        return true;
      }
      return false;
    } catch (e: any) {
      console.error('Failed to reset preferences:', e);
      this.error.set(e.message || 'Failed to reset preferences');
      return false;
    } finally {
      this.loading.set(false);
    }
  }

  /**
   * Get enabled notification channels
   */
  async getNotificationChannels(): Promise<string[]> {
    try {
      const response = await this.http.get<{ success: boolean; channels: string[] }>(
        `${this.baseUrl}/preferences/channels`
      ).toPromise();

      return response?.success ? response.channels : [];
    } catch (e) {
      console.error('Failed to get notification channels:', e);
      return ['in_app']; // Default
    }
  }

  /**
   * Test a notification channel
   */
  async testNotificationChannel(channel: string): Promise<boolean> {
    try {
      const response = await this.http.post<{ success: boolean }>(
        `${this.baseUrl}/preferences/test-notification?channel=${channel}`,
        {}
      ).toPromise();

      return response?.success || false;
    } catch (e) {
      console.error('Failed to test notification channel:', e);
      return false;
    }
  }

  /**
   * Convenience methods for specific preference updates
   */

  async updateTheme(theme: string): Promise<UserPreferences | null> {
    return this.updatePreferences({ theme });
  }

  async updateLanguage(language: string): Promise<UserPreferences | null> {
    return this.updatePreferences({ language });
  }

  async updateTradingPreferences(trading: Partial<TradingPreferences>): Promise<UserPreferences | null> {
    const current = this.preferences();
    if (!current) return null;

    return this.updatePreferences({
      trading: { ...current.trading, ...trading }
    });
  }

  async updateNotificationSettings(notifications: Partial<NotificationSettings>): Promise<UserPreferences | null> {
    const current = this.preferences();
    if (!current) return null;

    return this.updatePreferences({
      notifications: { ...current.notifications, ...notifications }
    });
  }

  async updatePrivacySettings(privacy: Partial<PrivacySettings>): Promise<UserPreferences | null> {
    const current = this.preferences();
    if (!current) return null;

    return this.updatePreferences({
      privacy: { ...current.privacy, ...privacy }
    });
  }

  /**
   * Get current preferences value
   */
  get current(): UserPreferences | null {
    return this.preferences();
  }
}
