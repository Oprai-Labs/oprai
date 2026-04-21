import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NotificationService } from '../../../core/services/notification.service';

@Component({
  selector: 'app-toast-container',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="toast-container">
      @for (toast of notifications(); track toast.id) {
        <div
          class="toast"
          [class.toast--success]="toast.type === 'success' || toast.type === 'tx-success'"
          [class.toast--error]="toast.type === 'error' || toast.type === 'tx-failed'"
          [class.toast--info]="toast.type === 'info'"
          [class.toast--warning]="toast.type === 'warning'"
          [class.toast--pending]="toast.type === 'tx-pending'"
        >
          <div class="toast__icon">
            @switch (toast.type) {
              @case ('success') { <span>&#10003;</span> }
              @case ('tx-success') { <span>&#10003;</span> }
              @case ('error') { <span>&#10007;</span> }
              @case ('tx-failed') { <span>&#10007;</span> }
              @case ('warning') { <span>&#9888;</span> }
              @case ('info') { <span>&#8505;</span> }
              @case ('tx-pending') { <span class="toast__spinner"></span> }
            }
          </div>
          <div class="toast__body">
            <span class="toast__message">{{ toast.message }}</span>
            @if (toast.signature) {
              <a
                class="toast__link"
                [href]="'https://solscan.io/tx/' + toast.signature"
                target="_blank"
                rel="noopener noreferrer"
              >
                View on Solscan
              </a>
            }
          </div>
          <button class="toast__close" (click)="dismiss(toast.id)">&times;</button>
        </div>
      }
    </div>
  `,
  styles: [`
    .toast-container {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 10000;
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: 380px;
    }
    .toast {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 16px;
      border-radius: 10px;
      background: var(--op-bg-elevated, #232a3d);
      border: 1px solid var(--op-border-default, rgba(255,255,255,0.10));
      box-shadow: 0 8px 24px rgba(0,0,0,0.3);
      animation: slideInFromRight 250ms cubic-bezier(0.16, 1, 0.3, 1);
      color: var(--op-text-primary, #e8ecf4);
      font-size: 13px;
    }
    .toast--success .toast__icon { color: #34d399; }
    .toast--error .toast__icon { color: #f87171; }
    .toast--warning .toast__icon { color: #fbbf24; }
    .toast--info .toast__icon { color: #60a5fa; }
    .toast--pending .toast__icon { color: #60a5fa; }
    .toast__icon {
      flex-shrink: 0;
      font-size: 16px;
      line-height: 1;
      margin-top: 1px;
    }
    .toast__spinner {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(96, 165, 250, 0.3);
      border-top-color: #60a5fa;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    .toast__body {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .toast__message { line-height: 1.4; }
    .toast__link {
      font-size: 12px;
      color: var(--op-brand-secondary, #60a5fa);
      text-decoration: none;
    }
    .toast__link:hover { text-decoration: underline; }
    .toast__close {
      flex-shrink: 0;
      background: none;
      border: none;
      color: var(--op-text-tertiary, #5a6478);
      font-size: 18px;
      cursor: pointer;
      padding: 0;
      line-height: 1;
    }
    .toast__close:hover { color: var(--op-text-primary, #e8ecf4); }

    @media (max-width: 480px) {
      .toast-container {
        left: 16px;
        right: 16px;
        bottom: 80px;
        max-width: none;
      }
    }
  `]
})
export class ToastContainerComponent {
  private readonly notificationService = inject(NotificationService);
  readonly notifications = this.notificationService.notifications;

  dismiss(id: string): void {
    this.notificationService.dismiss(id);
  }
}
