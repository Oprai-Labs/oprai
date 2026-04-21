import { Component, inject, signal, computed, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LiquidStakeService, type LstHolding, type LstProtocol } from '../../services/liquid-stake.service';
import { WalletService } from '@core/services/wallet.service';

type Tab = 'holdings' | 'stake' | 'unstake';
type Phase = 'idle' | 'loading' | 'preview' | 'signing' | 'success' | 'error';
type MarinadeMode = 'instant' | 'delayed';

interface PreviewData {
  description: string;
  estimatedFee: string;
  warnings: string[];
}

const PROTOCOL_INFO: Record<LstProtocol, { label: string; symbol: string; apy: string; icon: string }> = {
  marinade: { label: 'Marinade', symbol: 'mSOL',    apy: '~7.5%', icon: '🧊' },
  jito:     { label: 'Jito',     symbol: 'JitoSOL', apy: '~7.8%', icon: '⚡' },
  jupsol:   { label: 'JupSOL',  symbol: 'jupSOL',  apy: '~7.6%', icon: '🪐' },
};

@Component({
  selector: 'app-liquid-stake-shell',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './liquid-stake-shell.component.html',
  styleUrl: './liquid-stake-shell.component.scss',
})
export class LiquidStakeShellComponent {
  private readonly service = inject(LiquidStakeService);
  readonly wallet = inject(WalletService);

  readonly activeTab = signal<Tab>('holdings');
  readonly phase = signal<Phase>('idle');
  readonly holdings = signal<LstHolding[]>([]);
  readonly solBalance = signal<number>(0);
  readonly loadError = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly preview = signal<PreviewData | null>(null);
  readonly txSig = signal<string | null>(null);

  // Stake tab
  readonly selectedProtocol = signal<LstProtocol>('marinade');
  readonly stakeAmount = signal('');

  // Unstake tab
  readonly selectedHolding = signal<LstHolding | null>(null);
  readonly unstakeAmount = signal('');
  readonly marinadeMode = signal<MarinadeMode>('instant');

  readonly protocolEntries = Object.entries(PROTOCOL_INFO) as [LstProtocol, typeof PROTOCOL_INFO[LstProtocol]][];

  private pendingTransaction: string | null = null;
  private loaded = false;

  readonly isWalletConnected = computed(() => !!this.wallet.publicKey());

  readonly totalHoldingsUsd = computed(() =>
    this.holdings().reduce((s, h) => s + h.balance, 0)
  );

  readonly stakeAmountError = computed(() => {
    const v = this.stakeAmount();
    if (!v) return null;
    const n = parseFloat(v);
    if (isNaN(n) || n <= 0) return 'Enter a valid amount';
    if (n > this.solBalance()) return 'Insufficient SOL balance';
    return null;
  });

  readonly unstakeAmountError = computed(() => {
    const v = this.unstakeAmount();
    const h = this.selectedHolding();
    if (!v) return null;
    const n = parseFloat(v);
    if (isNaN(n) || n <= 0) return 'Enter a valid amount';
    if (h && n > h.balance) return `Insufficient ${h.symbol} balance`;
    return null;
  });

  constructor() {
    effect(() => {
      if (this.isWalletConnected()) {
        if (!this.loaded) { this.loaded = true; this.loadData(); }
      } else {
        this.loaded = false;
        this.holdings.set([]);
        this.solBalance.set(0);
        this.phase.set('idle');
      }
    });
  }

  async loadData() {
    this.phase.set('loading');
    this.loadError.set(null);
    const [holdings, sol] = await Promise.allSettled([
      this.service.loadHoldings(),
      this.service.loadSolBalance(),
    ]);
    if (holdings.status === 'fulfilled') this.holdings.set(holdings.value);
    else this.loadError.set('Failed to load holdings');
    if (sol.status === 'fulfilled') this.solBalance.set(sol.value);
    this.phase.set('idle');
  }

  setTab(tab: Tab) {
    this.activeTab.set(tab);
    this.resetAction();
  }

  selectProtocol(p: LstProtocol) {
    this.selectedProtocol.set(p);
    this.stakeAmount.set('');
  }

  selectHolding(h: LstHolding) {
    this.selectedHolding.set(h);
    this.unstakeAmount.set('');
    this.marinadeMode.set('instant');
  }

  setMaxUnstake() {
    const h = this.selectedHolding();
    if (h) this.unstakeAmount.set(h.balance.toString());
  }

  setMaxStake() {
    const bal = this.solBalance();
    this.stakeAmount.set(Math.max(0, bal - 0.01).toFixed(4));
  }

  // ── Preview builders ────────────────────────────────────────────────────────

  async previewStake() {
    const amount = this.stakeAmount();
    if (!amount || this.stakeAmountError()) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      let result: any;
      const protocol = this.selectedProtocol();
      if (protocol === 'marinade') {
        result = await this.service.buildMarinadeStake(amount);
      } else if (protocol === 'jito') {
        result = await this.service.buildJitoStake(amount);
      } else {
        result = await this.service.buildJupSolStake(amount);
      }
      this.setPendingTx(result);
    } catch (e: any) {
      this.actionError.set(e?.error?.error ?? e?.error?.message ?? e?.message ?? 'Build failed');
      this.phase.set('idle');
    }
  }

  async previewUnstake() {
    const h = this.selectedHolding();
    const amount = this.unstakeAmount();
    if (!h || !amount || this.unstakeAmountError()) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      let result: any;
      const sym = h.symbol.toLowerCase();
      if (sym === 'msol') {
        result = this.marinadeMode() === 'instant'
          ? await this.service.buildMarinadeUnstake(amount)
          : await this.service.buildMarinadeDelayedUnstake(amount);
      } else if (sym === 'jitosol') {
        result = await this.service.buildJitoUnstake(amount, true);
      } else {
        result = await this.service.buildJupSolUnstake(amount);
      }
      this.setPendingTx(result);
    } catch (e: any) {
      this.actionError.set(e?.error?.error ?? e?.error?.message ?? e?.message ?? 'Build failed');
      this.phase.set('idle');
    }
  }

  cancelPreview() {
    this.phase.set('idle');
    this.preview.set(null);
    this.pendingTransaction = null;
    this.actionError.set(null);
  }

  async confirmAction() {
    if (!this.pendingTransaction) return;
    this.phase.set('signing');
    this.actionError.set(null);
    try {
      const sig = await this.service.signAndSubmit(this.pendingTransaction);
      this.txSig.set(sig);
      this.phase.set('success');
      this.service.loadHoldings().then(h => this.holdings.set(h)).catch(() => {});
      this.service.loadSolBalance().then(b => this.solBalance.set(b)).catch(() => {});
      this.stakeAmount.set('');
      this.unstakeAmount.set('');
    } catch (e: any) {
      this.actionError.set(e?.message ?? 'Transaction failed');
      this.phase.set('preview');
    }
  }

  protocolInfo(p: LstProtocol) {
    return PROTOCOL_INFO[p];
  }

  isMarinade(h: LstHolding | null): boolean {
    return h?.symbol?.toLowerCase() === 'msol';
  }

  shortAddr(addr: string): string {
    return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
  }

  private setPendingTx(result: any) {
    this.pendingTransaction = result.transaction ?? null;
    this.preview.set({
      description: result.preview?.description ?? 'Confirm transaction',
      estimatedFee: result.preview?.estimatedFee ?? '~0.000005 SOL',
      warnings: result.preview?.warnings ?? [],
    });
    this.phase.set('preview');
  }

  private resetAction() {
    this.phase.set('idle');
    this.preview.set(null);
    this.pendingTransaction = null;
    this.actionError.set(null);
    this.txSig.set(null);
  }
}
