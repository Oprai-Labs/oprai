/**
 * TransactionPreviewDialogComponent
 *
 * Standalone dialog for displaying transaction previews with
 * balance changes, fees, risk assessment, and execution controls.
 */
import { Component, Input, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TransactionPreviewService, TransactionPreview, BalanceChange } from '../../services/transaction-preview.service';
import { ParsedAction } from '../../services/intent-parser.service';

@Component({
  selector: 'app-transaction-preview-dialog',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  template: `
    @if (visible()) {
      <div class="txp-overlay" (click)="close()">
        <div class="txp-dialog" (click)="$event.stopPropagation()">
          <div class="txp-header">
            <h3>Transaction Preview</h3>
            <button class="txp-close" (click)="close()">
              <lucide-icon name="x" [size]="18" />
            </button>
          </div>

          @if (loading()) {
            <div class="txp-loading">
              <span class="txp-spinner"></span>
              <span>Loading preview...</span>
            </div>
          } @else if (preview()) {
            <div class="txp-body">
              <!-- Balance Changes -->
              <div class="txp-section">
                <h4>Balance Changes</h4>
                @for (change of preview()!.balanceChanges; track change.mint) {
                  <div class="txp-change" [class.txp-change--positive]="change.amount > 0" [class.txp-change--negative]="change.amount < 0">
                    <span class="txp-change-symbol">{{ change.symbol }}</span>
                    <span class="txp-change-amount">{{ formatChange(change) }}</span>
                    <span class="txp-change-usd" *ngIf="change.usdValue">{{ formatUsd(change.usdValue) }}</span>
                    <span class="txp-change-balance">
                      {{ formatBalance(change.balanceBefore) }} → {{ formatBalance(change.balanceAfter) }}
                    </span>
                  </div>
                } @empty {
                  <p class="txp-empty">No balance changes</p>
                }
              </div>

              <!-- Details -->
              <div class="txp-section">
                <h4>Details</h4>
                <div class="txp-detail">
                  <span>Network Fee</span>
                  <span>~{{ preview()!.networkFeeSol | number:'1.0-6' }} SOL ({{ formatUsd(preview()!.networkFeeUsd) }})</span>
                </div>
                @if (preview()!.priceImpactPercent > 0) {
                  <div class="txp-detail" [class.txp-warning]="preview()!.priceImpactPercent > 1">
                    <span>Price Impact</span>
                    <span>{{ preview()!.priceImpactPercent | number:'1.0-4' }}%</span>
                  </div>
                }
                <div class="txp-detail">
                  <span>Protocol</span>
                  <span>{{ preview()!.protocol }}</span>
                </div>
              </div>

              <!-- Warnings -->
              @if (preview()!.warnings.length > 0) {
                <div class="txp-warnings">
                  @for (w of preview()!.warnings; track $index) {
                    <div class="txp-warning">
                      <lucide-icon name="alert-circle" [size]="14" />
                      <span>{{ w }}</span>
                    </div>
                  }
                </div>
              }
            </div>

            <div class="txp-actions">
              <button class="txp-btn txp-btn--cancel" (click)="close()">Cancel</button>
              <button class="txp-btn txp-btn--confirm"
                      [disabled]="!preview()!.canExecute"
                      (click)="confirm()">
                <lucide-icon name="check" [size]="16" />
                Confirm & Sign
              </button>
            </div>
          }
        </div>
      </div>
    }
  `,
  styles: [`
    .txp-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
    .txp-dialog { background: var(--op-bg-surface-1, #1a1a2e); border-radius: 16px; max-width: 480px; width: 90%; max-height: 80vh; overflow-y: auto; }
    .txp-header { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.1); }
    .txp-header h3 { margin: 0; font-size: 16px; color: var(--op-text-primary, #fff); }
    .txp-close { background: none; border: none; color: var(--op-text-secondary, #aaa); cursor: pointer; }
    .txp-body { padding: 16px 20px; }
    .txp-section { margin-bottom: 16px; }
    .txp-section h4 { font-size: 13px; color: var(--op-text-secondary, #aaa); margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.5px; }
    .txp-change { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 14px; }
    .txp-change--positive .txp-change-amount { color: #22c55e; }
    .txp-change--negative .txp-change-amount { color: #ef4444; }
    .txp-change-symbol { font-weight: 600; min-width: 50px; }
    .txp-change-balance { margin-left: auto; font-size: 12px; color: var(--op-text-secondary, #aaa); }
    .txp-detail { display: flex; justify-content: space-between; padding: 6px 0; font-size: 14px; }
    .txp-warning { color: #f59e0b; }
    .txp-actions { display: flex; gap: 12px; padding: 16px 20px; border-top: 1px solid rgba(255,255,255,0.1); }
    .txp-btn { padding: 10px 20px; border-radius: 8px; border: none; cursor: pointer; font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
    .txp-btn--cancel { background: transparent; border: 1px solid rgba(255,255,255,0.2); color: var(--op-text-primary, #fff); }
    .txp-btn--confirm { background: var(--op-brand, #5b5fc7); color: #fff; flex: 1; justify-content: center; }
    .txp-btn--confirm:disabled { opacity: 0.5; cursor: not-allowed; }
    .txp-loading, .txp-empty { padding: 32px; text-align: center; color: var(--op-text-secondary, #aaa); }
    .txp-spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid rgba(255,255,255,0.2); border-top-color: var(--op-brand, #5b5fc7); border-radius: 50%; animation: spin 0.8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .txp-warnings { margin-bottom: 16px; }
    .txp-warnings .txp-warning { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 13px; }
  `],
})
export class TransactionPreviewDialogComponent {
  @Input() action!: ParsedAction;

  readonly previewService = inject(TransactionPreviewService);

  readonly visible = signal(false);
  readonly loading = signal(false);
  readonly preview = signal<TransactionPreview | null>(null);

  async show(action: ParsedAction): Promise<void> {
    this.action = action;
    this.visible.set(true);
    this.loading.set(true);
    try {
      const result = await this.previewService.preview(action);
      this.preview.set(result);
    } catch (e) {
      this.preview.set({
        action,
        actionType: action.type,
        description: `${action.type} failed`,
        balanceChanges: [],
        risk: { level: 'high', factors: [], warnings: ['Preview failed'] },
        estimatedFee: 0,
        networkFeeSol: 0,
        networkFeeUsd: 0,
        protocol: 'Unknown',
        summary: 'Preview error',
        timestamp: Date.now(),
        canExecute: false,
        priceImpactPercent: 0,
        warnings: ['Could not generate preview'],
      });
    } finally {
      this.loading.set(false);
    }
  }

  close(): void {
    this.visible.set(false);
    this.preview.set(null);
  }

  confirm(): void {
    this.visible.set(false);
  }

  formatChange(change: BalanceChange): string {
    return this.previewService.formatChange(change);
  }

  formatUsd(value: number): string {
    return this.previewService.formatUsd(value);
  }

  formatBalance(value: number): string {
    if (value === 0) return '0';
    if (value < 0.000001) return '<0.000001';
    if (value < 1) return value.toFixed(6);
    if (value < 10000) return value.toFixed(4);
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}
