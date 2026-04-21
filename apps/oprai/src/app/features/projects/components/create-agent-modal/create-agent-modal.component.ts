import { Component, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { AgentCategory } from '../../models/agent.models';

interface AgentForm {
  name: string;
  description: string;
  category: AgentCategory;
  instructions: string;
  icon: string;
}

const ICON_OPTIONS = [
  'bot', 'brain', 'zap', 'shield-check', 'crosshair', 'eye',
  'gift', 'coins', 'candlestick-chart', 'pie-chart', 'users', 'landmark',
];

@Component({
  selector: 'app-create-agent-modal',
  standalone: true,
  imports: [FormsModule, LucideAngularModule],
  template: `
    <!-- Backdrop -->
    <div class="backdrop" (click)="close.emit()"></div>

    <!-- Panel -->
    <div class="panel">
      <header class="panel__header">
        <h2 class="panel__title">Create Agent</h2>
        <button class="panel__close" (click)="close.emit()">
          <lucide-icon name="x" [size]="18" />
        </button>
      </header>

      <div class="panel__body">
        <!-- Name -->
        <div class="field">
          <label class="field__label">Name</label>
          <input
            class="field__input"
            type="text"
            placeholder="My Custom Agent"
            [(ngModel)]="form.name"
          />
        </div>

        <!-- Description -->
        <div class="field">
          <label class="field__label">Description</label>
          <input
            class="field__input"
            type="text"
            placeholder="What does this agent do?"
            [(ngModel)]="form.description"
          />
        </div>

        <!-- Category -->
        <div class="field">
          <label class="field__label">Category</label>
          <select class="field__input field__select" [(ngModel)]="form.category">
            <option value="trading">Trading</option>
            <option value="defi">DeFi</option>
            <option value="analytics">Analytics</option>
            <option value="nft">NFT</option>
            <option value="security">Security</option>
            <option value="utility">Utility</option>
          </select>
        </div>

        <!-- Icon -->
        <div class="field">
          <label class="field__label">Icon</label>
          <div class="icon-grid">
            @for (icon of icons; track icon) {
              <button
                class="icon-btn"
                [class.icon-btn--active]="form.icon === icon"
                (click)="form.icon = icon"
              >
                <lucide-icon [name]="icon" [size]="18" />
              </button>
            }
          </div>
        </div>

        <!-- Instructions -->
        <div class="field">
          <label class="field__label">Instructions</label>
          <textarea
            class="field__input field__textarea"
            rows="4"
            placeholder="Describe how this agent should behave..."
            [(ngModel)]="form.instructions"
          ></textarea>
        </div>
      </div>

      <footer class="panel__footer">
        <button class="btn-cancel" (click)="close.emit()">Cancel</button>
        <button class="btn-create" (click)="onCreate()">
          <lucide-icon name="plus" [size]="14" />
          Create Agent
        </button>
      </footer>
    </div>
  `,
  styles: [`
    :host {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      justify-content: flex-end;
    }

    .backdrop {
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.4);
      backdrop-filter: blur(4px);
      animation: fadeIn 150ms ease;
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    @keyframes slideIn {
      from { transform: translateX(100%); }
      to { transform: translateX(0); }
    }

    .panel {
      position: relative;
      width: 420px;
      max-width: 100%;
      height: 100%;
      background: var(--op-bg-surface-1);
      border-left: 1px solid var(--op-border-subtle);
      display: flex;
      flex-direction: column;
      animation: slideIn 250ms cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: -8px 0 30px rgba(0, 0, 0, 0.15);
    }

    .panel__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 20px 24px;
      border-bottom: 1px solid var(--op-border-subtle);
    }

    .panel__title {
      font-size: 16px;
      font-weight: 600;
      color: var(--op-text-primary);
    }

    .panel__close {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-text-secondary);
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }
    }

    .panel__body {
      flex: 1;
      overflow-y: auto;
      padding: 20px 24px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .field__label {
      font-size: 12px;
      font-weight: 600;
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .field__input {
      width: 100%;
      padding: 10px 14px;
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      border-radius: 10px;
      color: var(--op-text-primary);
      font-size: 13px;
      transition: border-color 150ms ease, box-shadow 150ms ease;

      &::placeholder { color: var(--op-text-tertiary); }

      &:focus {
        border-color: var(--op-brand);
        box-shadow: var(--op-focus-ring);
      }
    }

    .field__select {
      appearance: none;
      cursor: pointer;
    }

    .field__textarea {
      resize: vertical;
      min-height: 80px;
      line-height: 1.5;
    }

    .icon-grid {
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 6px;
    }

    .icon-btn {
      width: 40px;
      height: 40px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--op-text-secondary);
      background: var(--op-bg-surface-2);
      border: 1px solid var(--op-border-subtle);
      transition: all 150ms ease;
      cursor: pointer;

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

    .panel__footer {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      padding: 16px 24px;
      border-top: 1px solid var(--op-border-subtle);
    }

    .btn-cancel {
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      color: var(--op-text-secondary);
      transition: all 150ms ease;

      &:hover {
        background: var(--op-hover);
        color: var(--op-text-primary);
      }
    }

    .btn-create {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 18px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      color: #fff;
      background: var(--op-gradient);
      box-shadow: var(--op-shadow-md);
      transition: all 150ms ease;

      &:hover {
        transform: translateY(-1px);
        box-shadow: var(--op-shadow-lg), var(--op-shadow-glow);
      }
    }

    @media (max-width: 480px) {
      .panel { width: 100%; }
      .icon-grid { grid-template-columns: repeat(4, 1fr); }
    }
  `],
})
export class CreateAgentModalComponent {
  readonly close = output<void>();
  readonly created = output<AgentForm>();

  readonly icons = ICON_OPTIONS;

  form: AgentForm = {
    name: '',
    description: '',
    category: 'trading',
    instructions: '',
    icon: 'bot',
  };

  onCreate(): void {
    if (!this.form.name.trim()) return;
    this.created.emit({ ...this.form });
    this.close.emit();
  }
}
