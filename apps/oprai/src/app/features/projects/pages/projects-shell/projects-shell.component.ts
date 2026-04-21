import { Component, computed, signal } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';
import { AgentCardComponent } from '../../components/agent-card/agent-card.component';
import { FeaturedCardComponent } from '../../components/featured-card/featured-card.component';
import { TrendingItemComponent } from '../../components/trending-item/trending-item.component';
import { CreateAgentModalComponent } from '../../components/create-agent-modal/create-agent-modal.component';
import { Agent, AGENTS, CATEGORY_TABS, CategoryTab } from '../../models/agent.models';

@Component({
  selector: 'app-projects-shell',
  standalone: true,
  imports: [
    LucideAngularModule,
    AgentCardComponent,
    FeaturedCardComponent,
    TrendingItemComponent,
    CreateAgentModalComponent,
  ],
  template: `
    <div class="page scrollbar-thin">
      <div class="container">
        <!-- Header: title left, create right -->
        <header class="header">
          <h1 class="header__title">Explore Agents</h1>
          <button class="create-btn" (click)="showModal.set(true)">
            <lucide-icon name="plus" [size]="16" />
            Create
          </button>
        </header>

        <!-- Search Bar (centered, large) -->
        <div class="search">
          <lucide-icon name="search" [size]="18" class="search__icon" />
          <input
            class="search__input"
            type="text"
            placeholder="Search agents..."
            [value]="searchQuery()"
            (input)="onSearch($event)"
          />
          @if (searchQuery()) {
            <button class="search__clear" (click)="searchQuery.set('')">
              <lucide-icon name="x" [size]="14" />
            </button>
          }
        </div>

        <!-- Category Tabs (text style, like ChatGPT) -->
        <nav class="tabs">
          @for (tab of tabs; track tab.id) {
            <button
              class="tab"
              [class.tab--active]="activeTab() === tab.id"
              (click)="activeTab.set(tab.id)"
            >
              {{ tab.label }}
            </button>
          }
          <button class="tab-arrow" aria-label="More categories">
            <lucide-icon name="arrow-right" [size]="14" />
          </button>
        </nav>

        <!-- Featured Section -->
        @if (featuredAgents().length > 0 && !searchQuery()) {
          <section class="section animate-fade-in-up">
            <p class="section__subtitle">Curated top picks from this week</p>
            <div class="featured-grid">
              @for (agent of featuredAgents(); track agent.id) {
                <app-featured-card [agent]="agent" />
              }
            </div>
          </section>
        }

        <!-- Trending Section -->
        @if (trendingAgents().length > 0 && !searchQuery()) {
          <section class="section">
            <div class="section__head">
              <h2 class="section__title">Trending</h2>
              <p class="section__subtitle-inline">Most popular by our community</p>
            </div>
            <div class="trending-grid">
              @for (agent of trendingAgents(); track agent.id; let i = $index) {
                <app-trending-item [agent]="agent" [rank]="i + 1" />
              }
            </div>
          </section>
        }

        <!-- All Agents -->
        <section class="section">
          <div class="section__head">
            <h2 class="section__title">
              {{ searchQuery() ? 'Search Results' : 'All Agents' }}
            </h2>
            @if (filteredAgents().length > 0) {
              <span class="section__count">{{ filteredAgents().length }}</span>
            }
          </div>
          @if (filteredAgents().length > 0) {
            <div class="agents-grid">
              @for (agent of filteredAgents(); track agent.id) {
                <app-agent-card [agent]="agent" />
              }
            </div>
          } @else {
            <div class="empty">
              <lucide-icon name="search" [size]="32" />
              <p>No agents found</p>
            </div>
          }
        </section>
      </div>
    </div>

    <!-- Create Modal -->
    @if (showModal()) {
      <app-create-agent-modal (close)="showModal.set(false)" (created)="onAgentCreated($event)" />
    }
  `,
  styles: [`
    :host { display: block; height: 100%; }

    .page {
      height: 100%;
      overflow-y: auto;
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px 24px 48px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }

    /* ── Header ── */
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .header__title {
      font-family: var(--op-font-display);
      font-size: 22px;
      font-weight: 700;
      color: var(--op-text-primary);
      letter-spacing: -0.03em;
    }

    .create-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 9px 20px;
      border-radius: var(--op-radius-md);
      font-size: 14px;
      font-weight: 600;
      color: #fff;
      background: var(--op-gradient);
      box-shadow: var(--op-shadow-sm);
      transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);

      &:hover {
        transform: translateY(-1px);
        box-shadow: var(--op-shadow-lg), var(--op-shadow-glow);
      }
      &:active { transform: translateY(0); }
    }

    /* ── Search ── */
    .search {
      position: relative;
    }

    .search__icon {
      position: absolute;
      left: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--op-text-tertiary);
      pointer-events: none;
    }

    .search__input {
      width: 100%;
      padding: 14px 44px 14px 46px;
      background: var(--op-bg-surface-1);
      border: 1px solid var(--op-border-default);
      border-radius: var(--op-radius-xl);
      font-size: 15px;
      color: var(--op-text-primary);
      transition: border-color 150ms ease, box-shadow 150ms ease;

      &::placeholder { color: var(--op-text-tertiary); }

      &:focus {
        border-color: var(--op-brand);
        box-shadow: var(--op-focus-ring);
      }
    }

    .search__clear {
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      width: 28px;
      height: 28px;
      border-radius: var(--op-radius-full);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-text-tertiary);
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }
    }

    /* ── Tabs (text-style like ChatGPT) ── */
    .tabs {
      display: flex;
      align-items: center;
      gap: 2px;
      overflow-x: auto;
      scrollbar-width: none;
      &::-webkit-scrollbar { display: none; }
    }

    .tab {
      padding: 6px 14px;
      border-radius: var(--op-radius-sm);
      font-size: 14px;
      font-weight: 500;
      color: var(--op-text-secondary);
      white-space: nowrap;
      transition: color 150ms ease, background 150ms ease;

      &:hover {
        color: var(--op-text-primary);
      }

      &--active {
        color: var(--op-text-primary);
        font-weight: 600;
        background: var(--op-hover);
      }
    }

    .tab-arrow {
      margin-left: auto;
      width: 28px;
      height: 28px;
      border-radius: var(--op-radius-full);
      border: 1px solid var(--op-border-default);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-text-secondary);
      flex-shrink: 0;
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-strong);
        color: var(--op-text-primary);
      }
    }

    /* ── Sections ── */
    .section {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .section__head {
      display: flex;
      align-items: baseline;
      gap: 10px;
    }

    .section__title {
      font-size: 20px;
      font-weight: 700;
      color: var(--op-text-primary);
      letter-spacing: -0.02em;
    }

    .section__subtitle {
      font-size: 13px;
      color: var(--op-text-tertiary);
    }

    .section__subtitle-inline {
      font-size: 13px;
      color: var(--op-text-tertiary);
    }

    .section__count {
      font-size: 12px;
      font-weight: 500;
      color: var(--op-text-tertiary);
    }

    /* ── Featured Grid: 3 columns ── */
    .featured-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }

    /* ── Trending Grid: 2 columns ── */
    .trending-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 2px;
    }

    /* ── All Agents Grid: 3 columns ── */
    .agents-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }

    /* ── Empty ── */
    .empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      padding: 56px 0;
      color: var(--op-text-tertiary);

      p {
        font-size: 14px;
      }
    }

    /* ── Responsive ── */
    @media (max-width: 900px) {
      .featured-grid { grid-template-columns: repeat(2, 1fr); }
      .agents-grid { grid-template-columns: repeat(2, 1fr); }
    }

    @media (max-width: 600px) {
      .container { padding: 16px 16px 32px; gap: 18px; }
      .header__title { font-size: 18px; }
      .search__input { padding: 12px 40px 12px 42px; font-size: 14px; }
      .featured-grid { grid-template-columns: 1fr; }
      .trending-grid { grid-template-columns: 1fr; }
      .agents-grid { grid-template-columns: 1fr; }
      .create-btn { padding: 8px 14px; font-size: 13px; }
    }
  `],
})
export class ProjectsShellComponent {
  readonly searchQuery = signal('');
  readonly activeTab = signal<string>('all');
  readonly showModal = signal(false);
  readonly customAgents = signal<Agent[]>([]);

  readonly tabs: CategoryTab[] = CATEGORY_TABS;

  readonly allAgents = computed(() => [...AGENTS, ...this.customAgents()]);

  readonly featuredAgents = computed(() => {
    const tab = this.activeTab();
    return this.allAgents().filter(a =>
      a.featured && (tab === 'all' || a.category === tab)
    );
  });

  readonly trendingAgents = computed(() => {
    const tab = this.activeTab();
    return this.allAgents()
      .filter(a => a.trending && (tab === 'all' || a.category === tab))
      .slice(0, 6);
  });

  readonly filteredAgents = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    const tab = this.activeTab();

    return this.allAgents().filter(a => {
      const matchesTab = tab === 'all' || a.category === tab;
      const matchesSearch = !query ||
        a.name.toLowerCase().includes(query) ||
        a.description.toLowerCase().includes(query) ||
        a.creator.toLowerCase().includes(query);
      return matchesTab && matchesSearch;
    });
  });

  onSearch(event: Event): void {
    this.searchQuery.set((event.target as HTMLInputElement).value);
  }

  onAgentCreated(form: { name: string; description: string; category: string; instructions: string; icon: string }): void {
    const agent: Agent = {
      id: 'custom-' + Date.now(),
      name: form.name,
      description: form.description,
      category: form.category as Agent['category'],
      creator: 'You',
      rating: 0,
      usageCount: 0,
      icon: form.icon,
      accent: '#6366F1',
    };
    this.customAgents.update(list => [...list, agent]);
  }
}
