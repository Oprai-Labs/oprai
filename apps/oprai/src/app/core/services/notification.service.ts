import { Injectable, signal } from '@angular/core';

export interface ToastNotification {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning' | 'tx-pending' | 'tx-success' | 'tx-failed';
  message: string;
  signature?: string;
  autoDismiss: boolean;
  duration: number;
  createdAt: number;
}

@Injectable({ providedIn: 'root' })
export class NotificationService {
  readonly notifications = signal<ToastNotification[]>([]);

  private counter = 0;

  success(message: string, duration = 5000): string {
    return this.add({ type: 'success', message, autoDismiss: true, duration });
  }

  error(message: string, duration = 0): string {
    return this.add({ type: 'error', message, autoDismiss: duration > 0, duration });
  }

  info(message: string, duration = 5000): string {
    return this.add({ type: 'info', message, autoDismiss: true, duration });
  }

  warning(message: string, duration = 5000): string {
    return this.add({ type: 'warning', message, autoDismiss: true, duration });
  }

  txPending(signature: string): string {
    return this.add({
      type: 'tx-pending',
      message: 'Transaction pending...',
      signature,
      autoDismiss: false,
      duration: 0,
    });
  }

  txSuccess(signature: string): void {
    // Replace pending notification with success
    this.notifications.update(list => {
      const idx = list.findIndex(n => n.signature === signature && n.type === 'tx-pending');
      if (idx >= 0) {
        const updated = [...list];
        updated[idx] = { ...updated[idx], type: 'tx-success', message: 'Transaction confirmed', autoDismiss: true, duration: 5000 };
        setTimeout(() => this.dismiss(updated[idx].id), 5000);
        return updated;
      }
      return list;
    });
  }

  txFailed(signature: string, error: string): void {
    this.notifications.update(list => {
      const idx = list.findIndex(n => n.signature === signature && n.type === 'tx-pending');
      if (idx >= 0) {
        const updated = [...list];
        updated[idx] = { ...updated[idx], type: 'tx-failed', message: error, autoDismiss: false };
        return updated;
      }
      return list;
    });
  }

  dismiss(id: string): void {
    this.notifications.update(list => list.filter(n => n.id !== id));
  }

  private add(opts: Omit<ToastNotification, 'id' | 'createdAt'>): string {
    const id = `toast-${++this.counter}`;
    const notification: ToastNotification = { ...opts, id, createdAt: Date.now() };
    this.notifications.update(list => [...list, notification]);

    if (opts.autoDismiss && opts.duration > 0) {
      setTimeout(() => this.dismiss(id), opts.duration);
    }

    return id;
  }
}
