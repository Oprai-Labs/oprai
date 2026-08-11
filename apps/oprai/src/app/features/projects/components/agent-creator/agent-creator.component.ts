/**
 * Comprehensive Agent Creator Component
 *
 * Full-featured GUI for creating custom DeFi agents with:
 * - All protocol tools
 * - Risk management
 * - Automation rules
 * - Notifications
 * - Strategy configuration
 */
import { Component, output, signal, computed, effect } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import {
  AgentConfig,
  AgentTool,
  AgentCapability,
  AGENT_TOOLS,
  TOOL_CATEGORIES,
  validateAgentConfig,
  getDefaultAgentConfig,
  ToolCategory,
  AgentCategory,
} from '../../models/agent-tool.models';

type TabId = 'basic' | 'tools' | 'risk' | 'automation' | 'notifications' | 'advanced';

interface Tab {
  id: TabId;
  label: string;
  icon: string;
  badge?: number;
}

const TABS: Tab[] = [
  { id: 'basic', label: 'Basic', icon: 'info' },
  { id: 'tools', label: 'Tools', icon: 'wrench', badge: 0 },
  { id: 'risk', label: 'Risk', icon: 'shield' },
  { id: 'automation', label: 'Automation', icon: 'zap' },
  { id: 'notifications', label: 'Alerts', icon: 'bell' },
  { id: 'advanced', label: 'Advanced', icon: 'settings-2' },
];

const CATEGORIES: { value: AgentCategory; label: string; icon: string }[] = [
  { value: 'trading', label: 'Trading', icon: 'candlestick-chart' },
  { value: 'defi', label: 'DeFi', icon: 'coins' },
  { value: 'analytics', label: 'Analytics', icon: 'bar-chart-3' },
  { value: 'nft', label: 'NFT', icon: 'image' },
  { value: 'security', label: 'Security', icon: 'shield-check' },
  { value: 'utility', label: 'Utility', icon: 'wrench' },
  { value: 'custom', label: 'Custom', icon: 'sparkles' },
];

const ACCENT_COLORS = [
  '#6366F1', '#8B5CF6', '#A855F7', '#D946EF', '#EC4899',
  '#F43F5E', '#F97316', '#F59E0B', '#EAB308', '#84CC16',
  '#22C55E', '#10B981', '#14B8A6', '#06B6D4', '#0EA5E9',
  '#3B82F6', '#2563EB', '#1D4ED8',
];

const INTERVALS = [
  { value: 'hourly', label: 'Every Hour' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'custom', label: 'Custom Cron' },
];

const TRIGGER_TYPES = [
  { value: 'price', label: 'Price Target', icon: 'dollar-sign' },
  { value: 'apy', label: 'APY Threshold', icon: 'trending-up' },
  { value: 'volume', label: 'Volume Spike', icon: 'bar-chart-3' },
  { value: 'whale', label: 'Whale Movement', icon: 'fish' },
  { value: 'time', label: 'Scheduled', icon: 'clock' },
];

@Component({
  selector: 'app-agent-creator',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule],
  template: `
    <div class="agent-creator">
      <!-- Header -->
      <header class="header">
        <div class="header__left">
          <h1 class="header__title">Agent Creator</h1>
          <span class="header__subtitle">Build your custom DeFi agent</span>
        </div>
        <button class="header__close" (click)="close.emit()">
          <lucide-icon name="x" [size]="20" />
        </button>
      </header>

      <!-- Tabs -->
      <nav class="tabs">
        @for (tab of tabs; track tab.id) {
          <button
            class="tab"
            [class.tab--active]="activeTab() === tab.id"
            (click)="activeTab.set(tab.id)"
          >
            <lucide-icon [name]="tab.icon" [size]="16" />
            <span>{{ tab.label }}</span>
            @if (tab.badge && tab.badge > 0) {
              <span class="tab__badge">{{ tab.badge }}</span>
            }
          </button>
        }
      </nav>

      <!-- Content -->
      <div class="content">
        @switch (activeTab()) {
          @case ('basic') {
            <!-- Basic Info -->
            <section class="section">
              <h2 class="section__title">Basic Information</h2>

              <!-- Name -->
              <div class="field">
                <label class="field__label">Agent Name <span class="required">*</span></label>
                <input
                  class="field__input"
                  type="text"
                  placeholder="e.g., Yield Maximizer"
                  [(ngModel)]="config.name"
                  maxlength="50"
                />
                <span class="field__hint">{{ config.name.length }}/50</span>
              </div>

              <!-- Description -->
              <div class="field">
                <label class="field__label">Description <span class="required">*</span></label>
                <textarea
                  class="field__input field__textarea"
                  placeholder="What does this agent do?"
                  [(ngModel)]="config.description"
                  rows="3"
                  maxlength="500"
                ></textarea>
                <span class="field__hint">{{ config.description.length }}/500</span>
              </div>

              <!-- Category -->
              <div class="field">
                <label class="field__label">Category</label>
                <div class="category-grid">
                  @for (cat of categories; track cat.value) {
                    <button
                      class="category-btn"
                      [class.category-btn--active]="config.category === cat.value"
                      (click)="config.category = cat.value"
                    >
                      <lucide-icon [name]="cat.icon" [size]="18" />
                      <span>{{ cat.label }}</span>
                    </button>
                  }
                </div>
              </div>

              <!-- Icon & Color -->
              <div class="field-row">
                <div class="field">
                  <label class="field__label">Icon</label>
                  <div class="icon-picker">
                    <button
                      class="icon-preview"
                      [style.background]="config.accentColor"
                    >
                      <lucide-icon [name]="config.icon" [size]="24" />
                    </button>
                    <div class="icon-options">
                      @for (icon of iconOptions; track icon) {
                        <button
                          class="icon-btn"
                          [class.icon-btn--active]="config.icon === icon"
                          (click)="config.icon = icon"
                        >
                          <lucide-icon [name]="icon" [size]="16" />
                        </button>
                      }
                    </div>
                  </div>
                </div>

                <div class="field">
                  <label class="field__label">Accent Color</label>
                  <div class="color-picker">
                    @for (color of accentColors; track color) {
                      <button
                        class="color-btn"
                        [class.color-btn--active]="config.accentColor === color"
                        [style.background]="color"
                        (click)="config.accentColor = color"
                      ></button>
                    }
                  </div>
                </div>
              </div>
            </section>
          }

          @case ('tools') {
            <!-- Tools Selection -->
            <section class="section">
              <h2 class="section__title">Capabilities & Tools</h2>
              <p class="section__desc">Select the tools your agent can use</p>

              <!-- Category Filter -->
              <div class="tool-filter">
                <button
                  class="filter-btn"
                  [class.filter-btn--active]="!selectedToolCategory()"
                  (click)="selectedToolCategory.set(null)"
                >
                  All ({{ tools().length }})
                </button>
                @for (cat of toolCategories; track cat.id) {
                  <button
                    class="filter-btn"
                    [class.filter-btn--active]="selectedToolCategory() === cat.id"
                    (click)="selectedToolCategory.set(cat.id)"
                  >
                    {{ cat.label }} ({{ getToolCount(cat.id) }})
                  </button>
                }
              </div>

              <!-- Tools Grid -->
              <div class="tools-grid">
                @for (tool of filteredTools(); track tool.id) {
                  <div
                    class="tool-card"
                    [class.tool-card--enabled]="isToolEnabled(tool.id)"
                    [style.--tool-color]="tool.riskLevel === 'high' ? '#EF4444' : tool.riskLevel === 'medium' ? '#F59E0B' : '#10B981'"
                  >
                    <div class="tool-header">
                      <div class="tool-info">
                        <div class="tool-icon">
                          <lucide-icon [name]="tool.icon" [size]="20" />
                        </div>
                        <div>
                          <h3 class="tool-name">{{ tool.name }}</h3>
                          <span class="tool-category">{{ tool.category }}</span>
                        </div>
                      </div>
                      <label class="toggle">
                        <input
                          type="checkbox"
                          [checked]="isToolEnabled(tool.id)"
                          (change)="toggleTool(tool.id)"
                        />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>
                    <p class="tool-desc">{{ tool.description }}</p>
                    <div class="tool-meta">
                      @for (protocol of tool.protocols.slice(0, 3); track protocol) {
                        <span class="protocol-tag">{{ protocol }}</span>
                      }
                      <span class="risk-tag" [class]="'risk--' + tool.riskLevel">
                        {{ tool.riskLevel }}
                      </span>
                    </div>
                  </div>
                }
              </div>
            </section>
          }

          @case ('risk') {
            <!-- Risk Management -->
            <section class="section">
              <h2 class="section__title">Risk Management</h2>
              <p class="section__desc">Configure safety limits for your agent</p>

              <!-- Max Slippage -->
              <div class="field">
                <label class="field__label">Max Slippage Tolerance</label>
                <div class="slider-field">
                  <input
                    type="range"
                    min="0"
                    max="10"
                    step="0.5"
                    [(ngModel)]="config.risk.maxSlippage"
                  />
                  <span class="slider-value">{{ config.risk.maxSlippage }}%</span>
                </div>
              </div>

              <!-- Max Gas Fee -->
              <div class="field">
                <label class="field__label">Max Priority Fee (SOL)</label>
                <div class="input-with-suffix">
                  <input
                    class="field__input"
                    type="number"
                    min="0"
                    step="0.001"
                    [(ngModel)]="config.risk.maxGasFee"
                  />
                  <span class="suffix">SOL</span>
                </div>
              </div>

              <!-- Max Position Size -->
              <div class="field">
                <label class="field__label">Max Position Size (USD)</label>
                <div class="input-with-suffix">
                  <input
                    class="field__input"
                    type="number"
                    min="0"
                    step="100"
                    [(ngModel)]="config.risk.maxPositionSize"
                  />
                  <span class="suffix">USD</span>
                </div>
              </div>

              <!-- Stop Loss -->
              <div class="field">
                <label class="field__label">Auto Stop Loss</label>
                <div class="input-with-suffix">
                  <input
                    class="field__input"
                    type="number"
                    min="-100"
                    max="0"
                    step="1"
                    [(ngModel)]="config.risk.autoStopLoss"
                  />
                  <span class="suffix">%</span>
                </div>
                <span class="field__hint">Auto-sell when position drops this % (leave empty to disable)</span>
              </div>

              <!-- Take Profit -->
              <div class="field">
                <label class="field__label">Take Profit Level</label>
                <div class="input-with-suffix">
                  <input
                    class="field__input"
                    type="number"
                    min="0"
                    max="1000"
                    step="5"
                    [(ngModel)]="config.risk.takeProfitLevel"
                  />
                  <span class="suffix">%</span>
                </div>
              </div>

              <!-- Allowed Protocols -->
              <div class="field">
                <label class="field__label">Allowed Protocols</label>
                <div class="checkbox-group">
                  @for (protocol of availableProtocols; track protocol) {
                    <label class="checkbox">
                      <input
                        type="checkbox"
                        [checked]="config.risk.allowedProtocols.includes(protocol)"
                        (change)="toggleProtocol(protocol)"
                      />
                      <span>{{ protocol }}</span>
                    </label>
                  }
                </div>
              </div>

              <!-- Blocked Tokens -->
              <div class="field">
                <label class="field__label">Blocked Tokens</label>
                <input
                  class="field__input"
                  type="text"
                  placeholder="e.g., SCAM, RUG (comma separated)"
                  [(ngModel)]="blockedTokensInput"
                />
              </div>
            </section>
          }

          @case ('automation') {
            <!-- Automation Rules -->
            <section class="section">
              <h2 class="section__title">Automation Rules</h2>
              <p class="section__desc">Set up triggers and scheduled tasks</p>

              <!-- Enable Schedule -->
              <div class="field">
                <div class="field-header">
                  <label class="field__label">Scheduled Execution</label>
                  <label class="toggle">
                    <input type="checkbox" [(ngModel)]="config.schedule.enabled" />
                    <span class="toggle-slider"></span>
                  </label>
                </div>
              </div>

              @if (config.schedule.enabled) {
                <div class="field">
                  <label class="field__label">Interval</label>
                  <select class="field__input field__select" [(ngModel)]="config.schedule.interval">
                    @for (interval of intervals; track interval.value) {
                      <option [value]="interval.value">{{ interval.label }}</option>
                    }
                  </select>
                </div>

                @if (config.schedule.interval === 'custom') {
                  <div class="field">
                    <label class="field__label">Cron Expression</label>
                    <input
                      class="field__input"
                      type="text"
                      placeholder="e.g., */15 * * * *"
                      [(ngModel)]="config.schedule.customCron"
                    />
                    <span class="field__hint">Minute Hour Day Month Weekday</span>
                  </div>
                }

                <div class="field">
                  <label class="field__label">Timezone</label>
                  <input
                    class="field__input"
                    type="text"
                    [(ngModel)]="config.schedule.timezone"
                  />
                </div>
              }

              <!-- Auto Execute -->
              <div class="field">
                <div class="field-header">
                  <label class="field__label">Auto Execute</label>
                  <label class="toggle">
                    <input type="checkbox" [(ngModel)]="config.autoExecute" />
                    <span class="toggle-slider"></span>
                  </label>
                </div>
                <span class="field__hint">Execute actions without manual approval</span>
              </div>

              @if (!config.autoExecute) {
                <div class="field">
                  <label class="field__label">Approval Required Above</label>
                  <div class="input-with-suffix">
                    <input
                      class="field__input"
                      type="number"
                      min="0"
                      [(ngModel)]="config.approvalRequiredAbove"
                    />
                    <span class="suffix">USD</span>
                  </div>
                </div>
              }

              <!-- Triggers -->
              <div class="triggers-section">
                <div class="section-header">
                  <h3>Triggers</h3>
                  <button class="btn-add" (click)="addTrigger()">
                    <lucide-icon name="plus" [size]="14" />
                    Add Trigger
                  </button>
                </div>

                @for (trigger of config.triggers; track $index) {
                  <div class="trigger-card">
                    <div class="trigger-header">
                      <select class="field__input field__select" [(ngModel)]="trigger.type">
                        @for (type of triggerTypes; track type.value) {
                          <option [value]="type.value">{{ type.label }}</option>
                        }
                      </select>
                      <button class="btn-icon" (click)="removeTrigger($index)">
                        <lucide-icon name="trash-2" [size]="16" />
                      </button>
                    </div>
                    <div class="trigger-body">
                      <input
                        class="field__input"
                        type="text"
                        placeholder="Condition (e.g., price > 100)"
                        [(ngModel)]="trigger.condition"
                      />
                      <input
                        class="field__input"
                        type="number"
                        placeholder="Value"
                        [(ngModel)]="trigger.value"
                      />
                    </div>
                  </div>
                }

                @if (config.triggers.length === 0) {
                  <div class="empty-state">
                    <lucide-icon name="zap" [size]="32" />
                    <p>No triggers configured</p>
                  </div>
                }
              </div>
            </section>
          }

          @case ('notifications') {
            <!-- Notifications -->
            <section class="section">
              <h2 class="section__title">Notifications</h2>
              <p class="section__desc">Configure alerts and updates</p>

              <!-- Enable Notifications -->
              <div class="field">
                <div class="field-header">
                  <label class="field__label">Enable Notifications</label>
                  <label class="toggle">
                    <input type="checkbox" [(ngModel)]="config.notifications.enabled" />
                    <span class="toggle-slider"></span>
                  </label>
                </div>
              </div>

              @if (config.notifications.enabled) {
                <!-- Channels -->
                <div class="field">
                  <label class="field__label">Notification Channels</label>
                  <div class="channel-grid">
                    @for (channel of notificationChannels; track channel.value) {
                      <button
                        class="channel-btn"
                        [class.channel-btn--active]="config.notifications.channels.includes(channel.value)"
                        (click)="toggleNotificationChannel(channel.value)"
                      >
                        <lucide-icon [name]="channel.icon" [size]="18" />
                        <span>{{ channel.label }}</span>
                      </button>
                    }
                  </div>
                </div>

                <!-- Events -->
                <div class="field">
                  <label class="field__label">Notify On</label>
                  <div class="checkbox-group">
                    <label class="checkbox">
                      <input type="checkbox" [(ngModel)]="config.notifications.onExecute" />
                      <span>Action Executed</span>
                    </label>
                    <label class="checkbox">
                      <input type="checkbox" [(ngModel)]="config.notifications.onError" />
                      <span>Errors</span>
                    </label>
                    <label class="checkbox">
                      <input type="checkbox" [(ngModel)]="config.notifications.onProfit" />
                      <span>Profits</span>
                    </label>
                    <label class="checkbox">
                      <input type="checkbox" [(ngModel)]="config.notifications.onLoss" />
                      <span>Losses</span>
                    </label>
                  </div>
                </div>

                <!-- Quiet Hours -->
                <div class="field">
                  <div class="field-header">
                    <label class="field__label">Quiet Hours</label>
                    <label class="toggle">
                      <input type="checkbox" [(ngModel)]="quietHoursEnabled" />
                      <span class="toggle-slider"></span>
                    </label>
                  </div>
                  @if (quietHoursEnabled()) {
                    <div class="quiet-hours">
                      <input
                        class="field__input"
                        type="time"
                        [(ngModel)]="config.notifications.quietHours!.start"
                      />
                      <span>to</span>
                      <input
                        class="field__input"
                        type="time"
                        [(ngModel)]="config.notifications.quietHours!.end"
                      />
                    </div>
                  }
                </div>

                <!-- Min Profit Threshold -->
                <div class="field">
                  <label class="field__label">Min Profit for Alert</label>
                  <div class="input-with-suffix">
                    <input
                      class="field__input"
                      type="number"
                      min="0"
                      [(ngModel)]="config.notifications.minProfitThreshold"
                    />
                    <span class="suffix">USD</span>
                  </div>
                </div>
              }
            </section>
          }

          @case ('advanced') {
            <!-- Advanced Settings -->
            <section class="section">
              <h2 class="section__title">Advanced Settings</h2>
              <p class="section__desc">Fine-tune agent behavior</p>

              <!-- System Prompt -->
              <div class="field">
                <label class="field__label">System Instructions</label>
                <textarea
                  class="field__input field__textarea"
                  rows="6"
                  placeholder="Additional instructions for your agent..."
                  [(ngModel)]="config.systemPrompt"
                ></textarea>
                <span class="field__hint">Custom instructions to override default agent behavior</span>
              </div>

              <!-- Temperature -->
              <div class="field">
                <label class="field__label">Response Creativity</label>
                <div class="slider-field">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.1"
                    [(ngModel)]="config.temperature"
                  />
                  <span class="slider-value">{{ config.temperature | number:'1.1-1' }}</span>
                </div>
                <span class="field__hint">Lower = more focused, Higher = more creative</span>
              </div>

              <!-- Max Tokens -->
              <div class="field">
                <label class="field__label">Max Response Tokens</label>
                <input
                  class="field__input"
                  type="number"
                  min="256"
                  max="8192"
                  step="256"
                  [(ngModel)]="config.maxTokens"
                />
              </div>

              <!-- Max Concurrent -->
              <div class="field">
                <label class="field__label">Max Concurrent Actions</label>
                <input
                  class="field__input"
                  type="number"
                  min="1"
                  max="10"
                  [(ngModel)]="config.maxConcurrentActions"
                />
              </div>
            </section>
          }
        }
      </div>

      <!-- Footer -->
      <footer class="footer">
        <div class="footer__validation">
          @if (validationErrors().length > 0) {
            <div class="validation-errors">
              @for (error of validationErrors(); track error) {
                <span class="error">{{ error }}</span>
              }
            </div>
          }
        </div>
        <div class="footer__actions">
          <button class="btn-secondary" (click)="close.emit()">Cancel</button>
          <button
            class="btn-primary"
            [disabled]="!canCreate()"
            (click)="createAgent()"
          >
            <lucide-icon name="plus" [size]="16" />
            Create Agent
          </button>
        </div>
      </footer>
    </div>
  `,
  styles: [`
    .agent-creator {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      flex-direction: column;
      background: var(--op-bg-surface-1);
      animation: fadeIn 200ms ease;
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 20px 24px;
      border-bottom: 1px solid var(--op-border-subtle);
    }

    .header__title {
      font-size: 20px;
      font-weight: 700;
      color: var(--op-text-primary);
    }

    .header__subtitle {
      font-size: 13px;
      color: var(--op-text-secondary);
    }

    .header__close {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-text-secondary);
      background: transparent;
      border: none;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }
    }

    .tabs {
      display: flex;
      gap: 4px;
      padding: 12px 24px;
      border-bottom: 1px solid var(--op-border-subtle);
      overflow-x: auto;
    }

    .tab {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      color: var(--op-text-secondary);
      background: transparent;
      border: none;
      cursor: pointer;
      white-space: nowrap;
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }

      &--active {
        background: var(--op-brand-subtle);
        color: var(--op-brand);
      }
    }

    .tab__badge {
      padding: 2px 6px;
      border-radius: 10px;
      font-size: 10px;
      background: var(--op-brand);
      color: white;
    }

    .content {
      flex: 1;
      overflow-y: auto;
      padding: 24px;
    }

    .section {
      margin-bottom: 32px;
    }

    .section__title {
      font-size: 16px;
      font-weight: 600;
      color: var(--op-text-primary);
      margin-bottom: 4px;
    }

    .section__desc {
      font-size: 13px;
      color: var(--op-text-secondary);
      margin-bottom: 20px;
    }

    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;

      h3 {
        font-size: 14px;
        font-weight: 600;
      }
    }

    .field {
      margin-bottom: 20px;
    }

    .field-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }

    .field-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .field__label {
      display: block;
      font-size: 12px;
      font-weight: 600;
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 8px;
    }

    .required {
      color: #EF4444;
    }

    .field__input {
      width: 100%;
      padding: 10px 14px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      border-radius: 10px;
      color: var(--op-text-primary);
      font-size: 13px;
      transition: all 150ms ease;

      &::placeholder {
        color: var(--op-text-tertiary);
      }

      &:focus {
        outline: none;
        border-color: var(--op-brand);
        box-shadow: 0 0 0 3px var(--op-brand-subtle);
      }
    }

    .field__textarea {
      resize: vertical;
      min-height: 80px;
      line-height: 1.5;
    }

    .field__select {
      appearance: none;
      cursor: pointer;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
      padding-right: 36px;
    }

    .field__hint {
      display: block;
      font-size: 11px;
      color: var(--op-text-tertiary);
      margin-top: 6px;
    }

    .input-with-suffix {
      display: flex;
      align-items: center;
      gap: 8px;

      .field__input {
        flex: 1;
      }

      .suffix {
        font-size: 12px;
        color: var(--op-text-secondary);
        min-width: 40px;
      }
    }

    .slider-field {
      display: flex;
      align-items: center;
      gap: 16px;

      input[type="range"] {
        flex: 1;
        height: 6px;
        appearance: none;
        background: var(--op-bg-surface-2);
        border-radius: 3px;
        cursor: pointer;

        &::-webkit-slider-thumb {
          appearance: none;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: var(--op-brand);
          cursor: pointer;
        }
      }

      .slider-value {
        min-width: 48px;
        font-size: 14px;
        font-weight: 600;
        color: var(--op-brand);
        text-align: right;
      }
    }

    .category-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
      gap: 8px;
    }

    .category-btn {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      padding: 12px;
      border-radius: 10px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      font-size: 11px;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-default);
        color: var(--op-text-primary);
      }

      &--active {
        background: var(--op-brand-subtle);
        border-color: var(--op-brand);
        color: var(--op-brand);
      }
    }

    .icon-picker {
      display: flex;
      gap: 12px;
    }

    .icon-preview {
      width: 56px;
      height: 56px;
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      border: none;
    }

    .icon-options {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      flex: 1;
    }

    .icon-btn {
      width: 36px;
      height: 36px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-default);
        color: var(--op-text-primary);
      }

      &--active {
        background: var(--op-brand-subtle);
        border-color: var(--op-brand);
        color: var(--op-brand);
      }
    }

    .color-picker {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .color-btn {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      border: 2px solid transparent;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        transform: scale(1.1);
      }

      &--active {
        border-color: white;
        box-shadow: 0 0 0 2px var(--op-brand);
      }
    }

    .tool-filter {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 20px;
    }

    .filter-btn {
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-default);
      }

      &--active {
        background: var(--op-brand-subtle);
        border-color: var(--op-brand);
        color: var(--op-brand);
      }
    }

    .tools-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 12px;
    }

    .tool-card {
      padding: 16px;
      border-radius: 12px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-default);
      }

      &--enabled {
        border-color: var(--tool-color, var(--op-brand));
        background: color-mix(in srgb, var(--tool-color, var(--op-brand)) 5%, var(--op-bg-surface-2));
      }
    }

    .tool-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 10px;
    }

    .tool-info {
      display: flex;
      gap: 12px;
    }

    .tool-icon {
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: var(--op-bg-surface-1);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-brand);
    }

    .tool-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--op-text-primary);
    }

    .tool-category {
      font-size: 11px;
      color: var(--op-text-tertiary);
      text-transform: capitalize;
    }

    .tool-desc {
      font-size: 12px;
      color: var(--op-text-secondary);
      line-height: 1.4;
      margin-bottom: 12px;
    }

    .tool-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .protocol-tag {
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 10px;
      background: var(--op-bg-surface-1);
      color: var(--op-text-secondary);
    }

    .risk-tag {
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;

      &.risk--low {
        background: rgba(16, 185, 129, 0.15);
        color: #10B981;
      }

      &.risk--medium {
        background: rgba(245, 158, 11, 0.15);
        color: #F59E0B;
      }

      &.risk--high {
        background: rgba(239, 68, 68, 0.15);
        color: #EF4444;
      }
    }

    .toggle {
      position: relative;
      display: inline-block;
      width: 44px;
      height: 24px;
      cursor: pointer;

      input {
        opacity: 0;
        width: 0;
        height: 0;
      }

      .toggle-slider {
        position: absolute;
        inset: 0;
        background: var(--op-bg-surface-2);
        border-radius: 12px;
        transition: all 150ms ease;

        &::before {
          content: '';
          position: absolute;
          left: 3px;
          top: 3px;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: white;
          transition: all 150ms ease;
        }
      }

      input:checked + .toggle-slider {
        background: var(--op-brand);

        &::before {
          transform: translateX(20px);
        }
      }
    }

    .checkbox-group {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }

    .checkbox {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--op-text-secondary);
      cursor: pointer;

      input {
        width: 18px;
        height: 18px;
        accent-color: var(--op-brand);
      }
    }

    .triggers-section {
      margin-top: 24px;
    }

    .btn-add {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 500;
      background: var(--op-brand-subtle);
      color: var(--op-brand);
      border: none;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        background: var(--op-brand);
        color: white;
      }
    }

    .trigger-card {
      padding: 14px;
      border-radius: 10px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      margin-bottom: 12px;
    }

    .trigger-header {
      display: flex;
      gap: 10px;
      margin-bottom: 10px;

      .field__select {
        flex: 1;
      }
    }

    .trigger-body {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 10px;
    }

    .btn-icon {
      width: 36px;
      height: 36px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: transparent;
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        background: rgba(239, 68, 68, 0.1);
        border-color: #EF4444;
        color: #EF4444;
      }
    }

    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      padding: 40px;
      color: var(--op-text-tertiary);
      text-align: center;
    }

    .channel-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 10px;
    }

    .channel-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 10px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      font-size: 12px;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        border-color: var(--op-border-default);
        color: var(--op-text-primary);
      }

      &--active {
        background: var(--op-brand-subtle);
        border-color: var(--op-brand);
        color: var(--op-brand);
      }
    }

    .quiet-hours {
      display: flex;
      align-items: center;
      gap: 12px;

      input {
        width: 120px;
      }

      span {
        color: var(--op-text-secondary);
      }
    }

    .footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 24px;
      border-top: 1px solid var(--op-border-subtle);
      background: var(--op-bg-surface-1);
    }

    .footer__validation {
      flex: 1;
    }

    .validation-errors {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .error {
      font-size: 12px;
      color: #EF4444;
    }

    .footer__actions {
      display: flex;
      gap: 12px;
    }

    .btn-secondary {
      padding: 10px 20px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 500;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }
    }

    .btn-primary {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 20px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
      background: var(--op-gradient);
      border: none;
      color: white;
      cursor: pointer;
      transition: all 150ms ease;

      &:hover:not(:disabled) {
        transform: translateY(-1px);
        box-shadow: var(--op-shadow-lg), var(--op-shadow-glow);
      }

      &:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
    }
  `]
})
export class AgentCreatorComponent {
  readonly close = output<void>();
  readonly created = output<AgentConfig>();

  // State
  readonly activeTab = signal<TabId>('basic');
  readonly selectedToolCategory = signal<ToolCategory | null>(null);
  readonly blockedTokensInput = signal('');
  readonly quietHoursEnabled = signal(false);
  readonly validationErrors = signal<string[]>([]);

  // Config
  config: AgentConfig = getDefaultAgentConfig();

  // Constants
  readonly tabs = TABS;
  readonly categories = CATEGORIES;
  readonly toolCategories = TOOL_CATEGORIES;
  readonly accentColors = ACCENT_COLORS;
  readonly intervals = INTERVALS;
  readonly triggerTypes = TRIGGER_TYPES;

  readonly iconOptions = [
    'bot', 'brain', 'zap', 'shield-check', 'crosshair', 'eye',
    'gift', 'coins', 'candlestick-chart', 'pie-chart', 'users', 'landmark',
    'trending-up', 'shield', 'lock', 'key', 'globe', 'link',
    'arrow-up-circle', 'arrow-down-circle', 'swap-horizontal', 'waves',
    'activity', 'target', 'clock', 'bell', 'calendar', 'wrench',
  ];

  readonly notificationChannels: { value: 'in_app' | 'telegram' | 'discord' | 'email'; label: string; icon: string }[] = [
    { value: 'in_app', label: 'In-App', icon: 'bell' },
    { value: 'telegram', label: 'Telegram', icon: 'send' },
    { value: 'discord', label: 'Discord', icon: 'message-circle' },
    { value: 'email', label: 'Email', icon: 'mail' },
  ];

  readonly availableProtocols = [
    'Jupiter', 'Orca', 'Raydium', 'Meteora',
    'Jito', 'Marinade',
    'Wormhole', 'Relay',
  ];

  // Computed
  readonly tools = computed(() => AGENT_TOOLS);

  readonly filteredTools = computed(() => {
    const category = this.selectedToolCategory();
    if (!category) return this.tools();
    return this.tools().filter((t: AgentTool) => t.category === category);
  });

  // Update tab badges
  constructor() {
    effect(() => {
      const enabledCount = this.config.capabilities.filter((c: AgentCapability) => c.enabled).length;
      const tab = this.tabs.find(t => t.id === 'tools');
      if (tab) tab.badge = enabledCount;
    });
  }

  // Methods
  getToolCount(category: ToolCategory): number {
    return AGENT_TOOLS.filter((t: AgentTool) => t.category === category).length;
  }

  isToolEnabled(toolId: string): boolean {
    const cap = this.config.capabilities.find((c: AgentCapability) => c.toolId === toolId);
    return cap?.enabled ?? false;
  }

  toggleTool(toolId: string): void {
    const cap = this.config.capabilities.find((c: AgentCapability) => c.toolId === toolId);
    if (cap) {
      cap.enabled = !cap.enabled;
    } else {
      this.config.capabilities.push({ toolId, enabled: true });
    }
  }

  toggleProtocol(protocol: string): void {
    const idx = this.config.risk.allowedProtocols.indexOf(protocol);
    if (idx >= 0) {
      this.config.risk.allowedProtocols.splice(idx, 1);
    } else {
      this.config.risk.allowedProtocols.push(protocol);
    }
  }

  toggleNotificationChannel(channel: string): void {
    const idx = this.config.notifications.channels.indexOf(channel as any);
    if (idx >= 0) {
      this.config.notifications.channels.splice(idx, 1);
    } else {
      this.config.notifications.channels.push(channel as any);
    }
  }

  addTrigger(): void {
    this.config.triggers.push({
      type: 'price',
      condition: '',
      value: 0,
    });
  }

  removeTrigger(index: number): void {
    this.config.triggers.splice(index, 1);
  }

  canCreate(): boolean {
    const validation = validateAgentConfig(this.config);
    this.validationErrors.set(validation.errors);
    return validation.valid;
  }

  createAgent(): void {
    // Parse blocked tokens
    this.config.risk.blockedTokens = this.blockedTokensInput()
      .split(',')
      .map(t => t.trim().toUpperCase())
      .filter(t => t.length > 0);

    // Set quiet hours if enabled
    if (!this.quietHoursEnabled()) {
      this.config.notifications.quietHours = undefined;
    }

    this.created.emit(this.config);
    this.close.emit();
  }
}
