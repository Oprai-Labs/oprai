import { Component, input } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';
import { Agent } from '../../models/agent.models';

@Component({
  selector: 'app-trending-item',
  standalone: true,
  imports: [LucideAngularModule],
  template: `
    <div class="trending" [style.--accent]="agent().accent">
      <span class="trending__rank">{{ rank() }}</span>
      <div class="trending__avatar">
        <lucide-icon [name]="agent().icon" [size]="18" />
      </div>
      <div class="trending__body">
        <h4 class="trending__name">{{ agent().name }}</h4>
        <p class="trending__desc">{{ agent().description }}</p>
        <span class="trending__creator">By {{ agent().creator }}</span>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }

    .trending {
      --accent: #6366f1;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 12px;
      border-radius: 12px;
      cursor: pointer;
      transition: background 150ms ease;

      &:hover {
        background: var(--op-hover);
      }
    }

    .trending__rank {
      font-size: 16px;
      font-weight: 700;
      color: var(--op-text-disabled);
      width: 20px;
      text-align: center;
      flex-shrink: 0;
      font-variant-numeric: tabular-nums;
    }

    .trending__avatar {
      width: 44px;
      height: 44px;
      border-radius: var(--op-radius-full);
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      color: var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .trending__body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .trending__name {
      font-size: 14px;
      font-weight: 600;
      color: var(--op-text-primary);
      line-height: 1.3;
    }

    .trending__desc {
      font-size: 12px;
      color: var(--op-text-secondary);
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .trending__creator {
      font-size: 11px;
      color: var(--op-text-tertiary);
    }
  `],
})
export class TrendingItemComponent {
  readonly agent = input.required<Agent>();
  readonly rank = input.required<number>();
}
