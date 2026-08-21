import {
  Component,
  inject,
  signal,
  computed,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { NativeStakeService, type StakeAccount, type Validator } from '../../services/native-stake.service';
import { WalletService } from '@core/services/wallet.service';
import { sanitizeErrorMessage } from '@core/utils/error-messages';
import { TPipe } from '@core/i18n';

type Tab = 'my-stakes' | 'new-stake';
type Phase = 'idle' | 'loading' | 'preview' | 'signing' | 'success' | 'error';

interface PreviewData {
  description: string;
  estimatedFee: string;
  estimatedRefund?: string;
  warnings: string[];
}

@Component({
  selector: 'app-native-stake-shell',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TPipe],
  templateUrl: './native-stake-shell.component.html',
  styleUrl: './native-stake-shell.component.scss',
})
export class NativeStakeShellComponent {
  private readonly stakeService = inject(NativeStakeService);
  readonly wallet = inject(WalletService);

  readonly activeTab = signal<Tab>('my-stakes');
  readonly phase = signal<Phase>('idle');
  readonly stakeAccounts = signal<StakeAccount[]>([]);
  readonly validators = signal<Validator[]>([]);
  readonly loadError = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly preview = signal<PreviewData | null>(null);
  readonly txSig = signal<string | null>(null);

  // New stake form
  readonly selectedValidator = signal('');
  readonly stakeAmount = signal('');

  // Manage account actions
  readonly actionAccount = signal<StakeAccount | null>(null);
  readonly pendingAction = signal<'deactivate' | 'withdraw' | 'merge' | null>(null);
  readonly mergeTarget = signal('');

  private pendingTransaction: string | null = null;
  private accountsLoaded = false;

  readonly isWalletConnected = computed(() => !!this.wallet.publicKey());

  readonly activeAccounts = computed(() =>
    this.stakeAccounts().filter((a) => a.status === 'active' || a.status === 'activating')
  );

  readonly mergeCompatibleAccounts = computed(() => {
    const current = this.actionAccount();
    if (!current) return [];
    return this.activeAccounts().filter(
      (a) => a.pubkey !== current.pubkey && a.validatorVoteAccount === current.validatorVoteAccount
    );
  });

  readonly hasMergeTargets = computed(() => this.mergeCompatibleAccounts().length > 0);

  readonly inactiveAccounts = computed(() =>
    this.stakeAccounts().filter((a) => a.status === 'inactive' || a.status === 'deactivating')
  );

  readonly totalStakedLamports = computed(() =>
    this.stakeAccounts().reduce((sum, a) => sum + a.stakedLamports, 0)
  );

  constructor() {
    effect(() => {
      if (this.isWalletConnected()) {
        if (!this.accountsLoaded) {
          this.accountsLoaded = true;
          this.loadAll();
        }
      } else {
        this.accountsLoaded = false;
        this.stakeAccounts.set([]);
        this.phase.set('idle');
      }
    });
  }

  readonly validatorLoadError = signal<string | null>(null);

  async loadAll() {
    this.phase.set('loading');
    this.loadError.set(null);
    this.validatorLoadError.set(null);

    // Load accounts and validators independently so one failure doesn't block the other
    const [accountsResult, validatorsResult] = await Promise.allSettled([
      this.stakeService.loadStakeAccounts(),
      this.stakeService.loadTopValidators(),
    ]);

    if (accountsResult.status === 'fulfilled') {
      this.stakeAccounts.set(accountsResult.value);
    } else {
      this.loadError.set((accountsResult.reason as any)?.message ?? 'Failed to load stake accounts');
    }

    if (validatorsResult.status === 'fulfilled') {
      this.validators.set(validatorsResult.value);
    } else {
      this.validatorLoadError.set('Could not load validator list. You can still manage existing stakes.');
    }

    this.phase.set('idle');
  }

  setTab(tab: Tab) {
    this.activeTab.set(tab);
    this.phase.set('idle');
    this.actionError.set(null);
    this.preview.set(null);
    this.pendingTransaction = null;
    this.actionAccount.set(null);
    this.pendingAction.set(null);
  }

  // ── New Stake ────────────────────────────────────────────────────────────────

  readonly stakeAmountError = computed(() => {
    const v = this.stakeAmount();
    if (!v) return null;
    const n = parseFloat(v);
    if (isNaN(n) || !isFinite(n)) return 'Enter a valid number';
    if (n < 1) return 'Minimum stake is 1 SOL';
    return null;
  });

  async previewStake() {
    const validator = this.selectedValidator();
    const amount = this.stakeAmount();
    if (!validator || !amount || this.stakeAmountError()) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      const result = await this.stakeService.buildStake(validator, amount);
      this.pendingTransaction = result.transaction ?? null;
      this.preview.set({
        description: result.preview?.description ?? `Stake ${amount} SOL`,
        estimatedFee: result.preview?.estimatedFee ?? '~0.000005 SOL',
        estimatedRefund: result.preview?.estimatedRefund,
        warnings: result.preview?.warnings ?? [],
      });
      this.phase.set('preview');
    } catch (e: any) {
      this.actionError.set(sanitizeErrorMessage(
        e?.error?.error ?? e?.error?.message ?? e?.message ?? '', 'stake',
      ) || 'Couldn’t prepare this transaction. Please try again in a moment.');
      this.phase.set('idle');
    }
  }

  // ── Manage existing stakes ───────────────────────────────────────────────────

  openAction(account: StakeAccount, action: 'deactivate' | 'withdraw' | 'merge') {
    this.actionAccount.set(account);
    this.pendingAction.set(action);
    this.actionError.set(null);
    this.preview.set(null);
    this.pendingTransaction = null;
  }

  cancelAction() {
    this.actionAccount.set(null);
    this.pendingAction.set(null);
    this.actionError.set(null);
    this.preview.set(null);
    this.pendingTransaction = null;
    this.phase.set('idle');
  }

  async previewManageAction() {
    const account = this.actionAccount();
    const action = this.pendingAction();
    if (!account || !action) return;
    this.phase.set('loading');
    this.actionError.set(null);
    try {
      let result: any;
      if (action === 'deactivate') {
        result = await this.stakeService.buildDeactivate(account.pubkey);
      } else if (action === 'withdraw') {
        result = await this.stakeService.buildWithdraw(account.pubkey);
      } else if (action === 'merge') {
        const target = this.mergeTarget();
        if (!target) {
          this.actionError.set('Select a destination stake account');
          this.phase.set('idle');
          return;
        }
        result = await this.stakeService.buildMerge(target, account.pubkey);
      }
      this.pendingTransaction = result.transaction ?? null;
      this.preview.set({
        description: result.preview?.description ?? action,
        estimatedFee: result.preview?.estimatedFee ?? '~0.000005 SOL',
        estimatedRefund: result.preview?.estimatedRefund,
        warnings: result.preview?.warnings ?? [],
      });
      this.phase.set('preview');
    } catch (e: any) {
      this.actionError.set(sanitizeErrorMessage(
        e?.error?.error ?? e?.error?.message ?? e?.message ?? '', 'stake',
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
      const sig = await this.stakeService.signAndSubmit(this.pendingTransaction);
      this.txSig.set(sig);
      this.phase.set('success');
      // Reload silently
      this.stakeService.loadStakeAccounts().then((accounts) => {
        this.stakeAccounts.set(accounts);
      }).catch(() => {});
      this.stakeAmount.set('');
      this.selectedValidator.set('');
      this.actionAccount.set(null);
      this.pendingAction.set(null);
    } catch (e: any) {
      this.actionError.set(e?.message ?? 'Transaction failed');
      this.phase.set('preview');
    }
  }

  statusLabel(status: StakeAccount['status']): string {
    switch (status) {
      case 'active': return 'Active';
      case 'activating': return 'Activating…';
      case 'deactivating': return 'Deactivating…';
      case 'inactive': return 'Inactive';
    }
  }

  statusClass(status: StakeAccount['status']): string {
    switch (status) {
      case 'active': return 'status--active';
      case 'activating': return 'status--activating';
      case 'deactivating': return 'status--deactivating';
      case 'inactive': return 'status--inactive';
    }
  }

  formatSol(lamports: number): string {
    return this.stakeService.formatSol(lamports);
  }

  shortPubkey(pk: string): string {
    if (pk.length < 12) return pk;
    return `${pk.slice(0, 8)}…${pk.slice(-4)}`;
  }

  formatStakeSol(sol: number): string {
    if (sol >= 1_000_000) return `${(sol / 1_000_000).toFixed(1)}M SOL`;
    if (sol >= 1_000) return `${(sol / 1_000).toFixed(0)}K SOL`;
    return `${sol} SOL`;
  }
}
