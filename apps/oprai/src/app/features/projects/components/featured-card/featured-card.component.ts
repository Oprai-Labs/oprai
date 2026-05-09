import { Component, input } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';
import { Agent } from '../../models/agent.models';

@Component({
  selector: 'app-featured-card',
  standalone: true,
  imports: [LucideAngularModule],
  template: `
    <article class="featured" [style.--accent]="agent().accent">
      <div class="featured__avatar">
        <lucide-icon [name]="agent().icon" [size]="24" />
      </div>
      <div class="featured__body">
        <h3 class="featured__name">{{ agent().name }}</h3>
        <p class="featured__desc">
          <span class="featured__rating">{{ agent().rating }}</span>
          <lucide-icon name="star" [size]="11" class="featured__star" />
          <span class="featured__sep"> - </span>
          {{ agent().description }}
        </p>
        <span class="featured__creator">By {{ agent().creator }}</span>
      </div>
    </article>
  `,
  styles: [`
    :host { display: block; }

    .featured {
      --accent: #6366f1;
      display: flex;
      gap: 14px;
      padding: 16px 18px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      border-radius: 14px;
      cursor: pointer;
      transition: transform 200ms cubic-bezier(0.16, 1, 0.3, 1),
                  box-shadow 200ms cubic-bezier(0.16, 1, 0.3, 1),
                  border-color 150ms ease;

      &:hover {
        transform: translateY(-2px);
        border-color: color-mix(in srgb, var(--accent) 30%, var(--op-border-default));
        box-shadow: 0 8px 28px -4px color-mix(in srgb, var(--accent) 12%, rgba(0, 0, 0, 0.15));
      }
    }

    .featured__avatar {
      width: 52px;
      height: 52px;
      border-radius: var(--op-radius-full);
      background: color-mix(in srgb, var(--accent) 16%, transparent);
      color: var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .featured__body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
      justify-content: center;
    }

    .featured__name {
      font-size: 14px;
      font-weight: 600;
      color: var(--op-text-primary);
      line-height: 1.3;
    }

    .featured__desc {
      font-size: 12px;
      color: var(--op-text-secondary);
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .featured__rating {
      font-weight: 600;
      color: var(--op-text-primary);
    }

    .featured__star {
      color: #FBBF24;
      display: inline;
      vertical-align: middle;
      margin-top: -1px;
    }

    .featured__sep {
      color: var(--op-text-tertiary);
    }

    .featured__creator {
      font-size: 11px;
      color: var(--op-text-tertiary);
    }
  `],
})
export class FeaturedCardComponent {
  readonly agent = input.required<Agent>();
}
