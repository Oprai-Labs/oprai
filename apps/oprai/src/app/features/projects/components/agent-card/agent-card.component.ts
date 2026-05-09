import { Component, inject, input } from '@angular/core';
import { Router } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { Agent } from '../../models/agent.models';

@Component({
  selector: 'app-agent-card',
  standalone: true,
  imports: [LucideAngularModule],
  template: `
    <article class="agent-card" [style.--accent]="agent().accent" (click)="open()">
      <div class="agent-card__top">
        <div class="agent-card__avatar">
          <lucide-icon [name]="agent().icon" [size]="18" />
        </div>
        <div class="agent-card__info">
          <h4 class="agent-card__name">{{ agent().name }}</h4>
          <span class="agent-card__creator">By {{ agent().creator }}</span>
        </div>
      </div>
      <p class="agent-card__desc">{{ agent().description }}</p>
      <div class="agent-card__footer">
        <div class="agent-card__rating">
          <lucide-icon name="star" [size]="12" />
          <span>{{ agent().rating }}</span>
        </div>
        <span class="agent-card__usage">{{ formatUsage(agent().usageCount) }} uses</span>
      </div>
    </article>
  `,
  styles: [`
    :host { display: block; }

    .agent-card {
      --accent: #6366f1;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 16px;
      background: var(--op-bg-surface-1);
      border: 1px solid var(--op-border-subtle);
      border-radius: 12px;
      cursor: pointer;
      transition: transform 200ms cubic-bezier(0.16, 1, 0.3, 1),
                  box-shadow 200ms cubic-bezier(0.16, 1, 0.3, 1),
                  border-color 150ms ease;

      &:hover {
        transform: translateY(-2px);
        border-color: var(--op-border-default);
        box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.12);
      }

      html.dark &:hover {
        box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.4);
      }
    }

    .agent-card__top {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .agent-card__avatar {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      color: var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .agent-card__info {
      flex: 1;
      min-width: 0;
    }

    .agent-card__name {
      font-size: 13px;
      font-weight: 600;
      color: var(--op-text-primary);
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .agent-card__creator {
      font-size: 11px;
      color: var(--op-text-tertiary);
    }

    .agent-card__desc {
      font-size: 12px;
      color: var(--op-text-secondary);
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .agent-card__footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: auto;
    }

    .agent-card__rating {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
      font-weight: 500;
      color: var(--op-text-primary);

      lucide-icon { color: #FBBF24; }
    }

    .agent-card__usage {
      font-size: 11px;
      color: var(--op-text-tertiary);
    }
  `],
})
export class AgentCardComponent {
  readonly agent = input.required<Agent>();
  private readonly router = inject(Router);

  open(): void {
    const route = this.agent().route;
    if (route) this.router.navigate(['/agents', route]);
  }

  formatUsage(count: number): string {
    if (count >= 1000) return (count / 1000).toFixed(1) + 'k';
    return count.toString();
  }
}
