import { Component, Input, Output, EventEmitter, signal, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { TokenIconComponent } from '@shared/components/token-icon/token-icon.component';
import { ParsedAction } from '../../services/intent-parser.service';
import { JupiterSwapService, SwapQuote } from '../../../../core/services/market/jupiter-swap.service';
import { TokenRegistryService } from '../../../../core/services/market/token-registry.service';

@Component({
  selector: 'app-swap-preview-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TokenIconComponent],
  template: `
    <div class="swap-preview" [class.swap-preview--loading]="loading()">
      <div class="swap-preview__header">
        <lucide-icon name="arrow-right-left" [size]="14" />
        <span>Swap Preview</span>
      </div>

      @if (loading()) {
        <div class="swap-preview__loading">
          <lucide-icon name="loader-2" [size]="16" class="spin" />
          <span>Fetching quote...</span>
        </div>
      } @else if (error()) {
        <div class="swap-preview__error">
          <lucide-icon name="alert-circle" [size]="14" />
          <span>{{ error() }}</span>
        </div>
      } @else {
        <div class="swap-preview__route">
          <div class="swap-preview__token">
            <app-token-icon [mint]="inputMint()" [symbol]="inputSymbol()" size="sm" />
            <div class="swap-preview__token-info">
              <span class="swap-preview__amount">{{ inputAmount() }}</span>
              <span class="swap-preview__symbol">{{ inputSymbol() }}</span>
            </div>
          </div>
          <lucide-icon name="arrow-right" [size]="14" class="swap-preview__arrow" />
          <div class="swap-preview__token">
            <app-token-icon [mint]="outputMint()" [symbol]="outputSymbol()" size="sm" />
            <div class="swap-preview__token-info">
              <span class="swap-preview__amount">{{ outputAmount() }}</span>
              <span class="swap-preview__symbol">{{ outputSymbol() }}</span>
            </div>
          </div>
        </div>

        @if (priceImpact(); as impact) {
          <div class="swap-preview__detail">
            <span>Price Impact</span>
            <span [class.swap-preview__impact--warning]="impact > 1">{{ impact.toFixed(4) }}%</span>
          </div>
        }

        <div class="swap-preview__actions">
          <button class="swap-preview__btn swap-preview__btn--approve" (click)="onApprove()">
            <lucide-icon name="check" [size]="14" />
            Approve Swap
          </button>
          <button class="swap-preview__btn swap-preview__btn--reject" (click)="onReject()">
            <lucide-icon name="x" [size]="14" />
            Cancel
          </button>
        </div>
      }
    </div>
  `,
  styles: [`
    .swap-preview {
      border: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      border-radius: 10px;
      background: var(--op-bg-surface-2, #161b28);
      margin: 6px 0;
      overflow: hidden;
    }
    .swap-preview__header {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--op-border-subtle, rgba(255,255,255,0.06));
      font-size: 12px;
      font-weight: 600;
      color: var(--op-text-secondary, #8b95a8);
    }
    .swap-preview__loading, .swap-preview__error {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 16px 12px;
      font-size: 12px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .swap-preview__route {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px;
    }
    .swap-preview__token {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
    }
    .swap-preview__token-info {
      display: flex;
      flex-direction: column;
    }
    .swap-preview__amount {
      font-size: 14px;
      font-weight: 600;
      font-family: var(--op-font-mono, monospace);
      color: var(--op-text-primary, #e8ecf4);
    }
    .swap-preview__symbol {
      font-size: 11px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .swap-preview__arrow { color: var(--op-text-disabled, #3a4358); flex-shrink: 0; }
    .swap-preview__detail {
      display: flex;
      justify-content: space-between;
      padding: 0 12px 8px;
      font-size: 11px;
      color: var(--op-text-tertiary, #5a6478);
    }
    .swap-preview__impact--warning { color: var(--op-loss, #f87171); }
    .swap-preview__actions {
      display: flex;
      gap: 8px;
      padding: 8px 12px 12px;
    }
    .swap-preview__btn {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 8px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      border: none;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .swap-preview__btn--approve {
      background: var(--op-gain, #34d399);
      color: #0f172a;
    }
    .swap-preview__btn--approve:hover { filter: brightness(1.1); }
    .swap-preview__btn--reject {
      background: var(--op-border-subtle, rgba(255,255,255,0.06));
      color: var(--op-text-secondary, #8b95a8);
    }
    .swap-preview__btn--reject:hover {
      background: var(--op-loss, #f87171);
      color: #fff;
    }
    .spin { animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class SwapPreviewCardComponent implements OnInit {
  @Input({ required: true }) action!: ParsedAction;
  @Output() readonly approved = new EventEmitter<SwapQuote>();
  @Output() readonly rejected = new EventEmitter<void>();

  private readonly jupiterSwap = inject(JupiterSwapService);
  private readonly tokenRegistry = inject(TokenRegistryService);

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly quote = signal<SwapQuote | null>(null);

  readonly inputMint = signal('');
  readonly outputMint = signal('');
  readonly inputSymbol = signal('');
  readonly outputSymbol = signal('');
  readonly inputAmount = signal('');
  readonly outputAmount = signal('');
  readonly priceImpact = signal<number | null>(null);

  async ngOnInit(): Promise<void> {
    const params = this.action.params;
    const inMint = params['inputMint'] ?? '';
    const outMint = params['outputMint'] ?? '';
    const amount = params['amount'] ?? '0';

    this.inputMint.set(inMint);
    this.outputMint.set(outMint);
    this.inputSymbol.set(this.tokenRegistry.getToken(inMint)?.symbol ?? inMint.slice(0, 6));
    this.outputSymbol.set(this.tokenRegistry.getToken(outMint)?.symbol ?? outMint.slice(0, 6));
    this.inputAmount.set(amount);

    try {
      // Pass human-readable amount — the gateway/backend converts to smallest units.
      const q = await this.jupiterSwap.getQuote(inMint, outMint, amount, 50);
      if (!q) {
        this.error.set('No swap route found');
        return;
      }
      this.quote.set(q);

      const outMeta = this.tokenRegistry.getToken(outMint);
      const outDecimals = outMeta?.decimals ?? 6;
      const outAmt = parseInt(q.outAmount) / Math.pow(10, outDecimals);
      this.outputAmount.set(outAmt.toFixed(Math.min(outDecimals, 6)));
      this.priceImpact.set(this.jupiterSwap.getPriceImpact(q));
    } catch {
      this.error.set('Failed to fetch swap quote');
    } finally {
      this.loading.set(false);
    }
  }

  onApprove(): void {
    const q = this.quote();
    if (q) this.approved.emit(q);
  }

  onReject(): void {
    this.rejected.emit();
  }
}
