/**
 * DeFi Strategy Route Visualizer
 *
 * Beautiful visualization of DeFi strategy routes as a roadmap.
 * Shows step-by-step journey with protocol icons and expected outcomes.
 */
import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { DeFiStrategy } from '@core/services/market/defi-strategy-optimizer.service';

@Component({
  selector: 'app-strategy-route-visualizer',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  template: `
    <div class="strategy-container">
      <!-- Strategy Header -->
      <div class="strategy-header">
        <div class="strategy-badge" [class]="'risk-' + strategy.riskLevel">
          {{ getRiskLabel(strategy.riskLevel) }}
        </div>
        <div class="strategy-title">
          <h3>{{ strategy.name }}</h3>
          <p class="strategy-description">{{ strategy.description }}</p>
        </div>
        <div class="strategy-apy">
          <span class="apy-value">{{ strategy.totalApy | number:'1.1-1' }}%</span>
          <span class="apy-label">APY</span>
        </div>
      </div>

      <!-- Route Visualization -->
      <div class="route-visualization">
        <div class="route-line"></div>

        @for (step of strategy.steps; track step.step; let i = $index) {
          <div class="route-step" [class.active]="i === 0" [class.completed]="i < currentStep">
            <div class="step-icon">
              <span class="protocol-emoji">{{ step.protocolIcon }}</span>
            </div>
            <div class="step-content">
              <div class="step-header">
                <span class="step-number">Step {{ step.step }}</span>
                <span class="step-action">{{ step.action }}</span>
              </div>
              <div class="step-protocol">{{ step.protocol }}</div>
              <div class="step-description">{{ step.description }}</div>

              <div class="step-tokens">
                <div class="token from">
                  <span class="token-amount">{{ step.amount | number:'1.2-4' }}</span>
                  <span class="token-symbol">{{ step.fromToken }}</span>
                </div>
                <div class="token-arrow">
                  <lucide-icon name="arrow-right" [size]="16"></lucide-icon>
                </div>
                <div class="token to">
                  <span class="token-amount">{{ step.expectedOutput | number:'1.2-4' }}</span>
                  <span class="token-symbol">{{ step.toToken }}</span>
                </div>
              </div>

              @if (step.apy) {
                <div class="step-apy">
                  <lucide-icon name="trending-up" [size]="14"></lucide-icon>
                  <span>{{ step.apy | number:'1.1-1' }}% APY</span>
                </div>
              }
            </div>
          </div>
        }

        <!-- Final Output -->
        <div class="route-end">
          <div class="end-icon">
            <lucide-icon name="check-circle" [size]="24"></lucide-icon>
          </div>
          <div class="end-content">
            <span class="end-label">Expected Output</span>
            <span class="end-value">\${{ formatNumber(strategy.estimatedOutput) }}</span>
          </div>
        </div>
      </div>

      <!-- Strategy Stats -->
      <div class="strategy-stats">
        <div class="stat">
          <lucide-icon name="clock" [size]="16"></lucide-icon>
          <span>{{ strategy.timeToExecute }} min</span>
        </div>
        @if (strategy.gasCost) {
          <div class="stat">
            <lucide-icon name="fuel" [size]="16"></lucide-icon>
            <span>~{{ strategy.gasCost.toFixed(4) }} SOL</span>
          </div>
        }
        <div class="stat">
          <lucide-icon name="shield" [size]="16"></lucide-icon>
          <span>{{ strategy.steps.length }} steps</span>
        </div>
      </div>

      <!-- Execute Button -->
      <button class="execute-btn" (click)="executeStrategy.emit(strategy)">
        <lucide-icon name="zap" [size]="18"></lucide-icon>
        Execute Strategy
      </button>
    </div>
  `,
  styles: [`
    .strategy-container {
      background: var(--op-bg-surface-1, #1a1a2e);
      border-radius: 16px;
      padding: 20px;
      border: 1px solid var(--op-border, #2a2a4a);
    }

    .strategy-header {
      display: flex;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 24px;
    }

    .strategy-badge {
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
    }

    .risk-low {
      background: rgba(16, 185, 129, 0.2);
      color: #10b981;
    }

    .risk-medium {
      background: rgba(245, 158, 11, 0.2);
      color: #f59e0b;
    }

    .risk-high {
      background: rgba(239, 68, 68, 0.2);
      color: #ef4444;
    }

    .strategy-title {
      flex: 1;
    }

    .strategy-title h3 {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      color: var(--op-text-primary, #fff);
    }

    .strategy-description {
      margin: 4px 0 0;
      font-size: 14px;
      color: var(--op-text-secondary, #a0a0b0);
    }

    .strategy-apy {
      text-align: right;
    }

    .apy-value {
      display: block;
      font-size: 28px;
      font-weight: 700;
      color: #10b981;
    }

    .apy-label {
      font-size: 12px;
      color: var(--op-text-secondary, #a0a0b0);
    }

    .route-visualization {
      position: relative;
      padding-left: 40px;
      margin-bottom: 24px;
    }

    .route-line {
      position: absolute;
      left: 19px;
      top: 30px;
      bottom: 60px;
      width: 2px;
      background: linear-gradient(to bottom, var(--op-brand, #6366f1), #10b981);
    }

    .route-step {
      position: relative;
      margin-bottom: 20px;
      opacity: 0.6;
      transition: opacity 0.3s;
    }

    .route-step.active,
    .route-step.completed {
      opacity: 1;
    }

    .step-icon {
      position: absolute;
      left: -40px;
      top: 0;
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: var(--op-bg-surface-2, #252540);
      border: 2px solid var(--op-border, #2a2a4a);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      z-index: 1;
    }

    .route-step.active .step-icon {
      border-color: var(--op-brand, #6366f1);
      box-shadow: 0 0 20px rgba(99, 102, 241, 0.4);
    }

    .route-step.completed .step-icon {
      background: #10b981;
      border-color: #10b981;
    }

    .step-content {
      background: var(--op-bg-surface-2, #252540);
      border-radius: 12px;
      padding: 16px;
      border: 1px solid var(--op-border, #2a2a4a);
    }

    .step-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }

    .step-number {
      font-size: 11px;
      font-weight: 600;
      color: var(--op-brand, #6366f1);
      text-transform: uppercase;
    }

    .step-action {
      font-size: 12px;
      padding: 2px 8px;
      background: rgba(99, 102, 241, 0.2);
      border-radius: 4px;
      color: var(--op-brand, #6366f1);
    }

    .step-protocol {
      font-size: 16px;
      font-weight: 600;
      color: var(--op-text-primary, #fff);
      margin-bottom: 8px;
    }

    .step-description {
      font-size: 13px;
      color: var(--op-text-secondary, #a0a0b0);
      margin-bottom: 12px;
    }

    .step-tokens {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }

    .token {
      background: var(--op-bg-surface-1, #1a1a2e);
      padding: 6px 10px;
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    .token-amount {
      font-size: 14px;
      font-weight: 600;
      color: var(--op-text-primary, #fff);
    }

    .token-symbol {
      font-size: 11px;
      color: var(--op-text-secondary, #a0a0b0);
    }

    .token-arrow {
      color: var(--op-text-secondary, #a0a0b0);
    }

    .step-apy {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 13px;
      color: #10b981;
    }

    .route-end {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 16px;
      background: rgba(16, 185, 129, 0.1);
      border-radius: 12px;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }

    .end-icon {
      color: #10b981;
    }

    .end-content {
      display: flex;
      flex-direction: column;
    }

    .end-label {
      font-size: 12px;
      color: var(--op-text-secondary, #a0a0b0);
    }

    .end-value {
      font-size: 18px;
      font-weight: 600;
      color: #10b981;
    }

    .strategy-stats {
      display: flex;
      justify-content: center;
      gap: 24px;
      margin-bottom: 20px;
      padding: 12px;
      background: var(--op-bg-surface-2, #252540);
      border-radius: 12px;
    }

    .stat {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      color: var(--op-text-secondary, #a0a0b0);
    }

    .execute-btn {
      width: 100%;
      padding: 14px;
      background: linear-gradient(135deg, var(--op-brand, #6366f1), #8b5cf6);
      border: none;
      border-radius: 12px;
      color: white;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: transform 0.2s, box-shadow 0.2s;
    }

    .execute-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 8px 20px rgba(99, 102, 241, 0.4);
    }
  `]
})
export class StrategyRouteVisualizerComponent {
  @Input() strategy!: DeFiStrategy;
  @Input() currentStep = 0;
  @Output() executeStrategy = new EventEmitter<DeFiStrategy>();

  formatNumber(value: number): string {
    return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  getRiskLabel(risk: string): string {
    const labels: Record<string, string> = {
      low: '🛡️ Low Risk',
      medium: '⚠️ Medium Risk',
      high: '🔥 High Risk',
    };
    return labels[risk] || risk;
  }
}
