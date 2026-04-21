import { Component, Input, Output, EventEmitter } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

@Component({
  selector: 'app-empty-state',
  standalone: true,
  imports: [LucideAngularModule],
  template: `
    <div class="empty-state-enhanced">
      @if (badge) {
        <span class="empty-state__badge">{{ badge }}</span>
      }
      <div class="empty-state-svg">
        @switch (variant) {
          @case ('wallet') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="16" width="48" height="36" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <path d="M8 26h48" stroke="currentColor" stroke-width="1.5"/>
              <rect x="38" y="30" width="14" height="10" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="45" cy="35" r="2" class="svg-accent"/>
              <path d="M16 16V14a4 4 0 0 1 4-4h24a4 4 0 0 1 4 4v2" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
          @case ('chart') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="8" width="48" height="48" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <polyline points="16,44 26,30 34,36 48,20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
              <circle cx="16" cy="44" r="2" class="svg-accent"/>
              <circle cx="26" cy="30" r="2" class="svg-accent"/>
              <circle cx="34" cy="36" r="2" class="svg-accent"/>
              <circle cx="48" cy="20" r="2" class="svg-accent"/>
            </svg>
          }
          @case ('tokens') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="26" cy="26" r="16" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="38" cy="38" r="16" stroke="currentColor" stroke-width="1.5"/>
              <text x="26" y="30" text-anchor="middle" font-size="12" font-weight="600" class="svg-accent-fill">$</text>
            </svg>
          }
          @case ('swap') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="20" cy="32" r="14" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="44" cy="32" r="14" stroke="currentColor" stroke-width="1.5"/>
              <path d="M28 28l6-4v8l-6-4z" class="svg-accent" stroke-width="1.5"/>
              <path d="M36 36l-6 4v-8l6 4z" class="svg-accent" stroke-width="1.5"/>
            </svg>
          }
          @case ('stake') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="12" y="40" width="10" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <rect x="27" y="28" width="10" height="28" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <rect x="42" y="16" width="10" height="40" rx="2" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <path d="M17 36l15-12 15-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="3 3"/>
            </svg>
          }
          @case ('agents') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="16" y="8" width="32" height="40" rx="6" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="26" cy="24" r="3" class="svg-accent"/>
              <circle cx="38" cy="24" r="3" class="svg-accent"/>
              <path d="M24 34c0 0 4 4 8 4s8-4 8-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <line x1="32" y1="48" x2="32" y2="56" stroke="currentColor" stroke-width="1.5"/>
              <line x1="24" y1="56" x2="40" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @case ('nft') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="10" y="10" width="44" height="44" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <path d="M10 42l12-12 8 8 8-8 16 16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
              <circle cx="22" cy="24" r="4" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
          @case ('search') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="28" cy="28" r="16" stroke="currentColor" stroke-width="1.5"/>
              <line x1="40" y1="40" x2="54" y2="54" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <line x1="20" y1="28" x2="36" y2="28" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
              <line x1="28" y1="20" x2="28" y2="36" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
            </svg>
          }
          @case ('key') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="22" cy="24" r="12" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="22" cy="24" r="4" class="svg-accent"/>
              <path d="M32 32l20 20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M44 44l8 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M48 48l6 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @case ('shield') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 6L10 18v16c0 14 10 22 22 24 12-2 22-10 22-24V18L32 6z" stroke="currentColor" stroke-width="1.5"/>
              <polyline points="24,32 30,38 42,26" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
            </svg>
          }
          @case ('governance') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 8L8 22h48L32 8z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <rect x="14" y="26" width="6" height="24" stroke="currentColor" stroke-width="1.5"/>
              <rect x="29" y="26" width="6" height="24" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <rect x="44" y="26" width="6" height="24" stroke="currentColor" stroke-width="1.5"/>
              <rect x="8" y="50" width="48" height="6" rx="1" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
          @case ('rocket') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 8c0 0-16 8-16 32h32c0-24-16-32-16-32z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <circle cx="32" cy="28" r="4" class="svg-accent"/>
              <path d="M24 40l-8 8v-12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M40 40l8 8v-12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M26 48h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M28 52h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @case ('workflow') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="8" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <rect x="40" y="8" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <rect x="24" y="40" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <path d="M24 16h16" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3"/>
              <path d="M16 24v8l16 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M48 24v8l-16 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          }
          @case ('code') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="10" width="48" height="44" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <line x1="8" y1="20" x2="56" y2="20" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="16" cy="15" r="2" fill="currentColor" opacity="0.3"/>
              <circle cx="22" cy="15" r="2" fill="currentColor" opacity="0.3"/>
              <circle cx="28" cy="15" r="2" fill="currentColor" opacity="0.3"/>
              <polyline points="20,30 14,36 20,42" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
              <polyline points="36,30 42,36 36,42" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
              <line x1="30" y1="28" x2="26" y2="44" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @case ('webhook') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="32" cy="16" r="8" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="16" cy="44" r="8" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="48" cy="44" r="8" stroke="currentColor" stroke-width="1.5"/>
              <path d="M32 24v6l-12 10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
              <path d="M32 30l12 10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
            </svg>
          }
          @case ('notifications') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 8a16 16 0 0 0-16 16v12l-4 8h40l-4-8V24a16 16 0 0 0-16-16z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <path d="M26 48c0 4 2.7 8 6 8s6-4 6-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <circle cx="46" cy="14" r="6" class="svg-accent"/>
            </svg>
          }
          @case ('lend') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="18" width="48" height="32" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <line x1="8" y1="30" x2="56" y2="30" stroke="currentColor" stroke-width="1.5"/>
              <text x="32" y="46" text-anchor="middle" font-size="14" font-weight="600" class="svg-accent-fill">%</text>
              <path d="M20 18v-6a4 4 0 0 1 4-4h16a4 4 0 0 1 4 4v6" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
          @case ('liquidity') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 8c-12 16-20 24-20 34a20 20 0 0 0 40 0c0-10-8-18-20-34z" stroke="currentColor" stroke-width="1.5"/>
              <path d="M22 42c0 6 4.5 10 10 10s10-4 10-10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
            </svg>
          }
          @case ('bridge') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="4" y="24" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <rect x="44" y="24" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <path d="M20 32c4-8 8-12 12-12s8 4 12 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
              <path d="M20 32c4 8 8 12 12 12s8-4 12-12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="3 3"/>
            </svg>
          }
          @case ('sniper') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="32" cy="32" r="20" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="32" cy="32" r="10" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="32" cy="32" r="3" class="svg-accent"/>
              <line x1="32" y1="4" x2="32" y2="16" stroke="currentColor" stroke-width="1.5"/>
              <line x1="32" y1="48" x2="32" y2="60" stroke="currentColor" stroke-width="1.5"/>
              <line x1="4" y1="32" x2="16" y2="32" stroke="currentColor" stroke-width="1.5"/>
              <line x1="48" y1="32" x2="60" y2="32" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
          @case ('tax') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="12" y="6" width="40" height="52" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <line x1="20" y1="18" x2="44" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <line x1="20" y1="26" x2="44" y2="26" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <line x1="20" y1="34" x2="36" y2="34" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <rect x="34" y="38" width="14" height="14" rx="2" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <text x="41" y="49" text-anchor="middle" font-size="10" font-weight="600" class="svg-accent-fill">$</text>
            </svg>
          }
          @case ('mint') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="12" y="12" width="40" height="40" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="32" cy="32" r="12" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <line x1="32" y1="24" x2="32" y2="40" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <line x1="24" y1="32" x2="40" y2="32" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M12 16l-4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M52 16l4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @case ('dca') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="32" cy="32" r="20" stroke="currentColor" stroke-width="1.5"/>
              <path d="M32 16a16 16 0 0 1 0 32" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
              <path d="M32 48a16 16 0 0 1 0-32" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="3 3"/>
              <path d="M42 22l-4 6h8l-4 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
              <path d="M22 42l4-6h-8l4-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="32" cy="32" r="4" class="svg-accent"/>
            </svg>
          }
          @case ('limit-orders') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="8" y="8" width="48" height="48" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <line x1="16" y1="20" x2="48" y2="20" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3"/>
              <line x1="16" y1="36" x2="48" y2="36" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3" class="svg-accent"/>
              <polyline points="16,44 24,38 32,42 40,28 48,16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="40" cy="28" r="3" class="svg-accent"/>
              <path d="M36 36h8v-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="svg-accent"/>
            </svg>
          }
          @case ('copy-trade') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="22" cy="24" r="10" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="22" cy="22" r="4" stroke="currentColor" stroke-width="1.5"/>
              <path d="M14 34c0-4 3.6-7 8-7s8 3 8 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <circle cx="42" cy="24" r="10" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <circle cx="42" cy="22" r="4" stroke="currentColor" stroke-width="1.5" class="svg-accent"/>
              <path d="M34 34c0-4 3.6-7 8-7s8 3 8 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="svg-accent"/>
              <path d="M28 42l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M32 46v8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M22 50h20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="3 3"/>
            </svg>
          }
          @case ('airdrops') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="20" y="32" width="24" height="20" rx="3" stroke="currentColor" stroke-width="1.5"/>
              <line x1="32" y1="32" x2="32" y2="52" stroke="currentColor" stroke-width="1.5"/>
              <path d="M32 32c-6 0-12-4-12-10s6-10 12-2c6-8 12-4 12 2s-6 10-12 10z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" class="svg-accent"/>
              <line x1="20" y1="40" x2="44" y2="40" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="16" cy="12" r="2" fill="currentColor" opacity="0.2"/>
              <circle cx="48" cy="8" r="1.5" fill="currentColor" opacity="0.2"/>
              <circle cx="52" cy="20" r="2" fill="currentColor" opacity="0.15"/>
            </svg>
          }
          @case ('alerts') {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M32 8a18 18 0 0 0-18 18v10l-4 8h44l-4-8V26a18 18 0 0 0-18-18z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <path d="M26 48c0 4 2.7 8 6 8s6-4 6-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <circle cx="46" cy="14" r="7" class="svg-accent"/>
              <line x1="32" y1="4" x2="32" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          }
          @default {
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="32" cy="32" r="24" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="32" cy="32" r="8" class="svg-accent"/>
              <line x1="32" y1="4" x2="32" y2="12" stroke="currentColor" stroke-width="1.5"/>
              <line x1="32" y1="52" x2="32" y2="60" stroke="currentColor" stroke-width="1.5"/>
              <line x1="4" y1="32" x2="12" y2="32" stroke="currentColor" stroke-width="1.5"/>
              <line x1="52" y1="32" x2="60" y2="32" stroke="currentColor" stroke-width="1.5"/>
            </svg>
          }
        }
      </div>
      <h3>{{ title }}</h3>
      <p>{{ description }}</p>
      @if (actionLabel) {
        <button class="empty-state-action" (click)="action.emit()">
          @if (actionIcon) {
            <lucide-icon [name]="actionIcon" [size]="14" />
          }
          {{ actionLabel }}
        </button>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }

    .empty-state-svg {
      color: var(--op-text-tertiary);
      margin-bottom: 4px;
    }

    .svg-accent {
      stroke: var(--op-brand);
      fill: none;
    }

    .svg-accent-fill {
      fill: var(--op-brand);
    }

    .empty-state__badge {
      display: inline-flex;
      padding: 4px 14px;
      border-radius: var(--op-radius-full);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: var(--op-brand-subtle);
      color: var(--op-brand);
      margin-bottom: 12px;
    }

    .empty-state-action {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-top: 8px;
      padding: 8px 16px;
      border-radius: var(--op-radius-md);
      background: var(--op-gradient);
      color: #fff;
      font-size: 13px;
      font-weight: 500;
      font-family: var(--op-font-body);
      cursor: pointer;
      transition: all var(--op-fast);
      border: none;

      &:hover {
        transform: translateY(-1px);
        box-shadow: var(--op-shadow-md);
      }
    }
  `],
})
export class EmptyStateComponent {
  @Input({ required: true }) icon!: string;
  @Input({ required: true }) title!: string;
  @Input({ required: true }) description!: string;
  @Input() actionLabel?: string;
  @Input() actionIcon?: string;
  @Input() variant: string = 'default';
  @Input() badge?: string;
  @Output() action = new EventEmitter<void>();
}
