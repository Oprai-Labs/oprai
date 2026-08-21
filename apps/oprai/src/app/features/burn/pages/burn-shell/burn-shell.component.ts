import {
  Component,
  inject,
  signal,
  computed,
  OnInit,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { BurnService, type TokenAccountInfo } from '../../services/burn.service';
import { WalletService } from '@core/services/wallet.service';
import { sanitizeErrorMessage } from '@core/utils/error-messages';
import { TPipe } from '@core/i18n';

type Tab = 'close' | 'burn';
type Phase = 'idle' | 'loading' | 'preview' | 'signing' | 'success' | 'error';

interface PreviewData {
  description: string;
  estimatedFee: string;
  estimatedRefund?: string;
  warnings: string[];
}

@Component({
  selector: 'app-burn-shell',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TPipe],
  templateUrl: './burn-shell.component.html',
  styleUrl: './burn-shell.component.scss',
})
export class BurnShellComponent implements OnInit {
  private readonly burnService = inject(BurnService);
  readonly wallet = inject(WalletService);

  readonly activeTab = signal<Tab>('close');
  readonly phase = signal<Phase>('idle');
  readonly accounts = signal<TokenAccountInfo[]>([]);
  readonly loadError = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly preview = signal<PreviewData | null>(null);
  readonly txSig = signal<string | null>(null);
  readonly faqOpen = signal(false);

  // Burn-specific state
  readonly burnMint = signal('');
  readonly burnAmount = signal('');
  readonly burnCloseAfter = signal(true);
  private pendingTransaction: string | null = null;

  readonly emptyAccounts = computed(() =>
    this.accounts().filter((a) => a.balance === 0)
  );

  readonly nonEmptyAccounts = computed(() =>
    this.accounts().filter((a) => a.balance > 0)
  );

  readonly selectedClose = computed(() =>
    this.emptyAccounts().filter((a) => a.selected)
  );

  readonly totalRefundLamports = computed(() =>
    this.selectedClose().reduce((sum, a) => sum + a.rentLamports, 0)
  );

  readonly isWalletConnected = computed(() => !!this.wallet.publicKey());

  private accountsLoaded = false;

  constructor() {
    effect(() => {
      if (this.isWalletConnected()) {
        if (!this.accountsLoaded) {
          this.accountsLoaded = true;
          this.loadAccounts();
        }
      } else {
        // Wallet disconnected — reset so reconnect triggers a fresh load
        this.accountsLoaded = false;
        this.accounts.set([]);
        this.phase.set('idle');
      }
    });
  }

  async ngOnInit() {}

  async loadAccounts() {
    this.phase.set('loading');
    this.loadError.set(null);
    try {
      const accts = await this.burnService.loadTokenAccounts();
      this.accounts.set(accts);
      this.phase.set('idle');
    } catch (e: any) {
      this.loadError.set(e?.message ?? 'Failed to load token accounts');
      this.phase.set('idle');
    }
  }

  setTab(tab: Tab) {
    this.activeTab.set(tab);
    this.phase.set('idle');
    this.actionError.set(null);
    this.preview.set(null);
    this.pendingTransaction = null;
  }

  toggleSelectClose(mint: string) {
    this.accounts.update((accts) =>
      accts.map((a) => (a.mint === mint ? { ...a, selected: !a.selected } : a))
    );
  }

  selectAllClose(checked: boolean) {
    this.accounts.update((accts) =>
      accts.map((a) => (a.balance === 0 ? { ...a, selected: checked } : a))
    );
  }

  readonly MAX_CLOSE_PER_TX = 15;

  readonly closeOverLimit = computed(() => this.selectedClose().length > this.MAX_CLOSE_PER_TX);

  selectBurnToken(mint: string) {
    this.burnMint.set(mint);
    // Reset amount when changing token
    this.burnAmount.set('');
  }

  setBurnAmountAll() {
    // "all" tells the backend to use the exact raw balance — avoids float precision errors
    this.burnAmount.set('all');
  }

  readonly selectedBurnAccount = computed(() =>
    this.accounts().find((a) => a.mint === this.burnMint())
  );

  async previewClose() {
    const selected = this.selectedClose();
    if (!selected.length) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      const mints = selected.slice(0, this.MAX_CLOSE_PER_TX).map((a) => a.mint);
      const result = await this.burnService.buildCloseAccounts(mints);
      this.pendingTransaction = result.transaction ?? null;
      this.preview.set({
        description: result.preview?.description ?? `Close ${selected.length} empty account(s)`,
        estimatedFee: result.preview?.estimatedFee ?? '~0.000005 SOL',
        estimatedRefund: result.preview?.estimatedRefund,
        warnings: result.preview?.warnings ?? [],
      });
      this.phase.set('preview');
    } catch (e: any) {
      this.actionError.set(sanitizeErrorMessage(
        e?.error?.error ?? e?.error?.message ?? e?.message ?? '', 'burn',
      ) || 'Couldn’t prepare this transaction. Please try again in a moment.');
      this.phase.set('idle');
    }
  }

  async previewBurn() {
    const mint = this.burnMint();
    const amount = this.burnAmount();
    if (!mint || !amount) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      const result = await this.burnService.buildBurn(mint, amount, this.burnCloseAfter());
      this.pendingTransaction = result.transaction ?? null;
      this.preview.set({
        description: result.preview?.description ?? `Burn ${amount} tokens`,
        estimatedFee: result.preview?.estimatedFee ?? '~0.000005 SOL',
        estimatedRefund: result.preview?.estimatedRefund,
        warnings: result.preview?.warnings ?? [],
      });
      this.phase.set('preview');
    } catch (e: any) {
      this.actionError.set(sanitizeErrorMessage(
        e?.error?.error ?? e?.error?.message ?? e?.message ?? '', 'burn',
      ) || 'Couldn’t prepare this transaction. Please try again in a moment.');
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
      const sig = await this.burnService.signAndSubmit(this.pendingTransaction);
      this.txSig.set(sig);
      this.phase.set('success');
      // Reload silently in background — don't overwrite 'success' phase
      this.burnService.loadTokenAccounts().then((accts) => {
        this.accounts.set(accts.map((a) => ({ ...a, selected: false })));
      }).catch(() => {});
      this.burnMint.set('');
      this.burnAmount.set('');
    } catch (e: any) {
      this.actionError.set(e?.message ?? 'Transaction failed');
      this.phase.set('preview');
    }
  }

  formatSol(lamports: number): string {
    return this.burnService.formatSol(lamports);
  }

  formatBalance(balance: number): string {
    if (balance === 0) return '0';
    if (balance < 0.001) return balance.toExponential(2);
    if (balance < 1000) return balance.toPrecision(4);
    return balance.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  toggleFaq() {
    this.faqOpen.update((v) => !v);
  }
}
