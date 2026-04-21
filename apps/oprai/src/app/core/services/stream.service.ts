/**
 * Stream Service - Real-time data streaming via SSE
 *
 * Provides streaming capabilities for:
 * - Price updates
 * - Position updates
 * - Notifications
 *
 * Uses Server-Sent Events (SSE) for browser compatibility
 */

import { Injectable, signal, OnDestroy } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Subject } from 'rxjs';

export interface PriceUpdate {
  token_address: string;
  price: number;
  price_change_24h: number;
  volume_24h: number;
  timestamp: number;
  error?: string;
}

export interface PositionUpdate {
  wallet_address: string;
  protocol: string;
  token_address: string;
  token_symbol: string;
  amount: number;
  value_usd: number;
  apy: number;
  change: 'DEPOSIT' | 'WITHDRAW' | 'PROFIT' | 'LOSS' | 'REWARD';
  timestamp: number;
  error?: string;
}

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string;
  data?: any;
  timestamp: number;
  priority: 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT';
  read: boolean;
  error?: string;
}

@Injectable({ providedIn: 'root' })
export class StreamService implements OnDestroy {
  private readonly baseUrl = environment.apiBase;

  // Active event sources
  private priceEventSource: EventSource | null = null;
  private positionEventSource: EventSource | null = null;
  private notificationEventSource: EventSource | null = null;

  // Signals for reactive updates
  readonly prices = signal<Map<string, PriceUpdate>>(new Map());
  readonly notifications = signal<Notification[]>([]);
  readonly connected = signal<boolean>(false);

  // Subjects for observables
  private priceSubject = new Subject<PriceUpdate>();
  private positionSubject = new Subject<PositionUpdate>();
  private notificationSubject = new Subject<Notification>();

  // Observable streams
  priceUpdates$ = this.priceSubject.asObservable();
  positionUpdates$ = this.positionSubject.asObservable();
  notificationUpdates$ = this.notificationSubject.asObservable();

  constructor(private http: HttpClient) {}

  /**
   * Subscribe to price updates for specific tokens
   */
  subscribePrices(tokens: string[], intervalSeconds: number = 1): void {
    this.disconnectPrices();

    const tokenParam = tokens.join(',');
    const url = `${this.baseUrl}/stream/prices?tokens=${tokenParam}&interval=${intervalSeconds}`;

    this.priceEventSource = new EventSource(url);

    this.priceEventSource.onopen = () => {
      this.connected.set(true);
    };

    this.priceEventSource.onmessage = (event) => {
      try {
        const data: PriceUpdate = JSON.parse(event.data);

        if (data.error) {
          console.error('Price stream error:', data.error);
          return;
        }

        // Update signal
        this.prices.update(map => {
          const newMap = new Map(map);
          newMap.set(data.token_address, data);
          return newMap;
        });

        // Emit to subject
        this.priceSubject.next(data);
      } catch (e) {
        console.error('Failed to parse price update:', e);
      }
    };

    this.priceEventSource.onerror = (error) => {
      console.error('Price stream error:', error);
      this.connected.set(false);
    };
  }

  /**
   * Disconnect from price updates
   */
  disconnectPrices(): void {
    if (this.priceEventSource) {
      this.priceEventSource.close();
      this.priceEventSource = null;
    }
  }

  /**
   * Subscribe to position updates for a wallet
   */
  subscribePositions(walletAddress: string, protocols?: string[]): void {
    this.disconnectPositions();

    let url = `${this.baseUrl}/stream/positions?wallet=${walletAddress}`;
    if (protocols && protocols.length > 0) {
      url += `&protocols=${protocols.join(',')}`;
    }

    this.positionEventSource = new EventSource(url);

    this.positionEventSource.onopen = () => {
      this.connected.set(true);
    };

    this.positionEventSource.onmessage = (event) => {
      try {
        const data: PositionUpdate = JSON.parse(event.data);

        if (data.error) {
          console.error('Position stream error:', data.error);
          return;
        }

        // Emit to subject
        this.positionSubject.next(data);
      } catch (e) {
        console.error('Failed to parse position update:', e);
      }
    };

    this.positionEventSource.onerror = (error) => {
      console.error('Position stream error:', error);
      this.connected.set(false);
    };
  }

  /**
   * Disconnect from position updates
   */
  disconnectPositions(): void {
    if (this.positionEventSource) {
      this.positionEventSource.close();
      this.positionEventSource = null;
    }
  }

  /**
   * Subscribe to notifications for a wallet
   */
  subscribeNotifications(walletAddress: string): void {
    this.disconnectNotifications();

    const url = `${this.baseUrl}/stream/notifications?wallet=${walletAddress}`;

    this.notificationEventSource = new EventSource(url);

    this.notificationEventSource.onopen = () => {
      this.connected.set(true);
    };

    this.notificationEventSource.onmessage = (event) => {
      try {
        const data: Notification = JSON.parse(event.data);

        if (data.error) {
          console.error('Notification stream error:', data.error);
          return;
        }

        // Update signal
        this.notifications.update(list => [data, ...list].slice(0, 50));

        // Emit to subject
        this.notificationSubject.next(data);
      } catch (e) {
        console.error('Failed to parse notification:', e);
      }
    };

    this.notificationEventSource.onerror = (error) => {
      console.error('Notification stream error:', error);
      this.connected.set(false);
    };
  }

  /**
   * Disconnect from notifications
   */
  disconnectNotifications(): void {
    if (this.notificationEventSource) {
      this.notificationEventSource.close();
      this.notificationEventSource = null;
    }
  }

  /**
   * Disconnect from all streams
   */
  disconnectAll(): void {
    this.disconnectPrices();
    this.disconnectPositions();
    this.disconnectNotifications();
    this.connected.set(false);
  }

  /**
   * Get streaming service stats
   */
  async getStats(): Promise<any> {
    return this.http.get(`${this.baseUrl}/stream/stats`).toPromise();
  }

  /**
   * Mark notification as read
   */
  markNotificationRead(notificationId: string): void {
    this.notifications.update(list =>
      list.map(n => n.id === notificationId ? { ...n, read: true } : n)
    );
  }

  /**
   * Clear all notifications
   */
  clearNotifications(): void {
    this.notifications.set([]);
  }

  ngOnDestroy(): void {
    this.disconnectAll();
  }
}
