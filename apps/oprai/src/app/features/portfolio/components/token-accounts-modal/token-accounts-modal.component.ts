import { Component, computed, inject, input, output, signal, HostListener, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { BurnService, type TokenAccountInfo } from '../../../burn/services/burn.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { HeliusService } from '../../services/helius.service';
import { TPipe } from '@core/i18n';

const LAMPORTS_PER_SOL = 1_000_000_000;
const MAX_CLOSE_PER_TX = 25;
// Empty SPL token account rent-exempt minimum (165 bytes). Used as a
// pre-load fallback when we haven't resolved on-chain rent yet.
const TOKEN_ACCOUNT_RENT_LAMPORTS = 2_039_280;

type Phase = 'idle' | 'loading' | 'building' | 'signing' | 'success' | 'error';

/** Account row with Jupiter-registry-resolved symbol + logo. Falls back
 *  to the BurnService dict when registry hasn't resolved a mint yet. */
export interface ResolvedAccount extends TokenAccountInfo {
  resolvedSymbol: string;
  resolvedName: string;
  resolvedLogoUri: string | null;
}

/**
 * Sol-incinerator-style modal for managing every SPL token account the
 * wallet has open — empty AND non-empty. Two reclaim paths:
 *
 *   - **Empty accounts** → `close_accounts` tx(s). Selections larger than
 *     25 (the per-tx instruction ceiling under Solana's 1232-byte limit)
 *     are auto-chunked into multiple sequential signatures — the user is
 *     never asked to deselect. Pure rent refund, no token destruction.
 *   - **Non-empty accounts** → per-mint `burn` (amount=all, closeMint=true).
 *     Destroys the balance and closes the ATA in one signature per token.
 *     Used to reclaim rent from dust/spam tokens the user can't otherwise
 *     dispose of (no market, no transfer target).
 *
 * The user explicitly chooses which path applies via the row-level checkbox
 * — a non-empty row's checkbox triggers the burn pipeline, and the action
 * bar surfaces the burn count separately from the close count so it's
 * impossible to burn by accident.
 */
@Component({
  selector: 'app-token-accounts-modal',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TPipe],
  templateUrl: './token-accounts-modal.component.html',
  styleUrl: './token-accounts-modal.component.scss',
})
export class TokenAccountsModalComponent {
  isOpen = input<boolean>(false);
  close = output<void>();
  /** Fired after at least one successful close/burn so the parent can
   *  refresh the underlying portfolio data. */
  reclaimed = output<{ totalAccounts: number; totalLamports: number }>();

  private readonly burnService = inject(BurnService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly heliusService = inject(HeliusService);

  // On-chain (Helius DAS) metadata fallback, keyed by mint. The Jupiter
  // strict list only covers ~5000 verified tokens, so most *dust* accounts
  // (spam airdrops, dead memes) have no registry entry. DAS reads the
  // on-chain Metaplex / token-2022 metadata so we can still show a name +
  // icon for almost any mint. Populated once per load in `loadAccounts`.
  private readonly dasMeta = signal<Map<string, { name: string; symbol: string; logoUri: string | null }>>(new Map());

  readonly accounts = signal<TokenAccountInfo[]>([]);
  // Trigger to invalidate `resolvedAccounts` when async registry resolves
  // arrive. Incremented from `loadAccounts` (post-batch resolve) and
  // whenever `resolveAsync` lands.
  readonly registryTick = signal(0);
  readonly selectedMints = signal<Set<string>>(new Set());
  readonly phase = signal<Phase>('idle');
  readonly error = signal<string | null>(null);
  readonly progress = signal<{ current: number; total: number; label: string } | null>(null);
  readonly successInfo = signal<{ count: number; signature: string } | null>(null);
  readonly hideZeroValue = signal(false);
  readonly burnConfirmation = signal(false);

  // Track total lamports reclaimed across all transactions in this session
  // so the success message can show running progress even when the user
  // splits work into multiple batches.
  private sessionReclaimedAccounts = 0;
  private sessionReclaimedLamports = 0;

  constructor() {
    // Reset transient state every time the modal opens. Keeps the user from
    // seeing a stale "Closed 3 accounts" success banner after re-opening to
    // run a second pass.
    effect(() => {
      if (this.isOpen()) {
        this.resetState();
        this.loadAccounts();
      }
    });
  }

  /** Close the modal on Escape. */
  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.isOpen()) this.close.emit();
  }

  private resetState(): void {
    this.selectedMints.set(new Set());
    this.error.set(null);
    this.progress.set(null);
    this.successInfo.set(null);
    this.phase.set('idle');
    this.burnConfirmation.set(false);
    this.dasMeta.set(new Map());
    this.sessionReclaimedAccounts = 0;
    this.sessionReclaimedLamports = 0;
  }

  // ──── Derived state ────

  readonly emptyAccounts = computed(() => this.accounts().filter(a => a.balance === 0));
  readonly nonEmptyAccounts = computed(() => this.accounts().filter(a => a.balance > 0));

  readonly visibleAccounts = computed<ResolvedAccount[]>(() => {
    // Read registryTick so the computed re-fires when an async resolve
    // lands — Angular doesn't trace plain method calls otherwise.
    this.registryTick();
    const all = this.accounts();
    const filtered = this.hideZeroValue() ? all.filter(a => a.balance === 0) : all;
    const sorted = [...filtered].sort((a, b) => {
      if ((a.balance === 0) !== (b.balance === 0)) {
        return a.balance === 0 ? -1 : 1;
      }
      return b.balance - a.balance;
    });
    return sorted.map(a => this.resolve(a));
  });

  /**
   * Layered symbol/name/logo lookup:
   *   1. Jupiter strict list (cached, covers ~5000 verified tokens)
   *   2. Helius DAS on-chain metadata (covers the dust/spam long tail that
   *      Jupiter doesn't list — fetched in `loadAccounts`)
   *   3. BurnService's hardcoded dict (wrapped stables / well-known tokens)
   *   4. Fallback to short mint
   */
  private resolve(a: TokenAccountInfo): ResolvedAccount {
    const reg = this.tokenRegistry.getToken(a.mint);
    if (!reg) {
      // Warm the registry so the next render picks the full metadata up.
      // resolveAsync is idempotent + de-duped internally.
      this.tokenRegistry.resolveAsync(a.mint);
    }
    const das = this.dasMeta().get(a.mint);

    const resolvedSymbol =
      reg?.symbol
      || (das?.symbol || '')
      || (a.symbol && !a.symbol.includes('…') ? a.symbol : '')
      || '';
    const resolvedName =
      reg?.name
      || das?.name
      || resolvedSymbol
      || this.shortMint(a.mint);
    const resolvedLogoUri = reg?.logoURI ?? das?.logoUri ?? a.logoUri ?? null;
    return {
      ...a,
      // Prefer the registry symbol; otherwise keep whatever DAS/BurnService
      // shipped (often a short-mint placeholder which we render with care).
      resolvedSymbol: resolvedSymbol || this.shortMint(a.mint),
      resolvedName,
      resolvedLogoUri,
    };
  }

  readonly selectedCount = computed(() => this.selectedMints().size);

  readonly selectedSplit = computed(() => {
    const sel = this.selectedMints();
    const empty: TokenAccountInfo[] = [];
    const nonEmpty: TokenAccountInfo[] = [];
    for (const acct of this.accounts()) {
      if (!sel.has(acct.mint)) continue;
      (acct.balance === 0 ? empty : nonEmpty).push(acct);
    }
    return { empty, nonEmpty };
  });

  readonly selectedReclaimSol = computed(() => {
    let total = 0;
    for (const acct of this.accounts()) {
      if (this.selectedMints().has(acct.mint)) {
        total += acct.rentLamports || TOKEN_ACCOUNT_RENT_LAMPORTS;
      }
    }
    return total / LAMPORTS_PER_SOL;
  });

  readonly totalReclaimableSol = computed(() => {
    let total = 0;
    for (const acct of this.accounts()) {
      total += acct.rentLamports || TOKEN_ACCOUNT_RENT_LAMPORTS;
    }
    return total / LAMPORTS_PER_SOL;
  });

  // How many close transactions the empty selection will be split into.
  // A Solana tx can only hold ~25 closeAccount instructions before it
  // exceeds the 1232-byte size limit, so large selections are chunked and
  // signed sequentially rather than rejected.
  readonly closeBatchCount = computed(() =>
    Math.ceil(this.selectedSplit().empty.length / MAX_CLOSE_PER_TX),
  );

  readonly hasBurnSelection = computed(() => this.selectedSplit().nonEmpty.length > 0);

  readonly closeBatchLimit = MAX_CLOSE_PER_TX;

  // ──── Loading / refresh ────

  async loadAccounts(): Promise<void> {
    this.phase.set('loading');
    this.error.set(null);
    try {
      // Warm up the registry first so the first render already has labels
      // for most well-known mints. ensureLoaded is a no-op after the
      // first call in the session.
      await this.tokenRegistry.ensureLoaded().catch(() => {});
      const accounts = await this.burnService.loadTokenAccounts();
      this.accounts.set(accounts);

      // Kick off async resolve for any mint the registry doesn't have yet.
      // Each landing increments `registryTick` indirectly — TokenRegistry
      // batches resolves and the change-detection picks it up on next CD.
      // Bump tick immediately so the initial render uses fresh metadata.
      const unresolved = accounts.filter(a => !this.tokenRegistry.getToken(a.mint));
      for (const a of unresolved) this.tokenRegistry.resolveAsync(a.mint);
      this.registryTick.update(v => v + 1);

      // On-chain metadata fallback: one batched Helius DAS call for every
      // mint the Jupiter registry doesn't already have a logo for. This is
      // what fills in icons + names for the long tail of dust/spam tokens
      // that aren't Jupiter-listed (the bulk of what shows up here).
      const needsDas = accounts
        .filter(a => !this.tokenRegistry.getToken(a.mint)?.logoURI)
        .map(a => a.mint);
      if (needsDas.length > 0) {
        this.heliusService
          .getAssetBatch(needsDas)
          .then(map => {
            if (map.size > 0) {
              this.dasMeta.set(map);
              this.registryTick.update(v => v + 1);
            }
          })
          .catch(() => {});
      }

      // Re-bump after a short delay so late-arriving registry entries flow
      // into the table without requiring a manual refresh.
      if (unresolved.length > 0) {
        setTimeout(() => this.registryTick.update(v => v + 1), 1500);
        setTimeout(() => this.registryTick.update(v => v + 1), 4000);
      }

      this.phase.set('idle');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load token accounts';
      this.error.set(msg);
      this.phase.set('error');
    }
  }

  // ──── Selection ────

  isSelected(mint: string): boolean {
    return this.selectedMints().has(mint);
  }

  toggleSelect(mint: string, ev?: Event): void {
    if (ev) ev.stopPropagation();
    if (this.isBusy()) return;
    this.selectedMints.update(set => {
      const next = new Set(set);
      if (next.has(mint)) next.delete(mint);
      else next.add(mint);
      return next;
    });
    // Resetting the burn-confirmation toggle whenever the selection changes
    // forces the user to re-affirm the destructive action against the
    // current selection rather than a stale earlier choice.
    this.burnConfirmation.set(false);
  }

  selectAllEmpty(): void {
    if (this.isBusy()) return;
    const next = new Set<string>();
    for (const a of this.emptyAccounts()) next.add(a.mint);
    this.selectedMints.set(next);
    this.burnConfirmation.set(false);
  }

  selectAll(): void {
    if (this.isBusy()) return;
    const next = new Set<string>();
    for (const a of this.accounts()) next.add(a.mint);
    this.selectedMints.set(next);
    this.burnConfirmation.set(false);
  }

  clearSelection(): void {
    this.selectedMints.set(new Set());
    this.burnConfirmation.set(false);
  }

  toggleHideZero(): void {
    this.hideZeroValue.update(v => !v);
  }

  toggleBurnConfirmation(): void {
    this.burnConfirmation.update(v => !v);
  }

  // ──── Helpers ────

  isBusy(): boolean {
    const p = this.phase();
    return p === 'building' || p === 'signing' || p === 'loading';
  }

  formatBalance(balance: number): string {
    if (balance === 0) return '0';
    if (balance < 0.0001) return balance.toExponential(2);
    if (balance < 1) return balance.toPrecision(4);
    if (balance < 1000) return balance.toFixed(4);
    return balance.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  shortMint(mint: string): string {
    return mint.slice(0, 4) + '…' + mint.slice(-4);
  }

  // ──── Submission ────

  /**
   * Execute the user's selection: a single batched `close_accounts` for the
   * empty rows, followed by one `burn` per non-empty row. Burns only proceed
   * if the user has explicitly flipped the confirmation toggle in the
   * action bar — there's no "implicit accept" path.
   */
  async closeSelected(): Promise<void> {
    const { empty, nonEmpty } = this.selectedSplit();
    if (empty.length === 0 && nonEmpty.length === 0) return;
    if (nonEmpty.length > 0 && !this.burnConfirmation()) {
      this.error.set('Tick the burn confirmation to destroy non-empty balances.');
      return;
    }

    this.error.set(null);
    this.successInfo.set(null);
    let lastSig: string | null = null;
    let processed = 0;
    let lamports = 0;
    // Empty accounts are chunked into batches of MAX_CLOSE_PER_TX (Solana
    // tx-size limit) and each batch is one signature. Burns stay one tx
    // per mint. `total` counts every signature the user will be asked for.
    const emptyBatches: TokenAccountInfo[][] = [];
    for (let i = 0; i < empty.length; i += MAX_CLOSE_PER_TX) {
      emptyBatches.push(empty.slice(i, i + MAX_CLOSE_PER_TX));
    }
    const total = emptyBatches.length + nonEmpty.length;

    try {
      for (let b = 0; b < emptyBatches.length; b++) {
        const batch = emptyBatches[b];
        const batchLabel = emptyBatches.length > 1 ? ` (batch ${b + 1}/${emptyBatches.length})` : '';
        this.phase.set('building');
        this.progress.set({ current: processed, total, label: `Building close-${batch.length} tx${batchLabel}…` });
        const built = await this.burnService.buildCloseAccounts(batch.map(a => a.mint));
        if (!built.transaction) throw new Error('Backend returned no transaction for close_accounts');
        this.phase.set('signing');
        this.progress.set({ current: processed, total, label: `Sign to close ${batch.length} empty account(s)${batchLabel}` });
        lastSig = await this.burnService.signAndSubmit(built.transaction);
        processed += 1;
        lamports += batch.reduce((s, a) => s + (a.rentLamports || TOKEN_ACCOUNT_RENT_LAMPORTS), 0);
      }

      for (const acct of nonEmpty) {
        this.phase.set('building');
        this.progress.set({ current: processed, total, label: `Building burn for ${acct.symbol}…` });
        const built = await this.burnService.buildBurn(acct.mint, 'all', true);
        if (!built.transaction) throw new Error(`Backend returned no transaction for ${acct.symbol}`);
        this.phase.set('signing');
        this.progress.set({ current: processed, total, label: `Sign burn & close: ${acct.symbol}` });
        lastSig = await this.burnService.signAndSubmit(built.transaction);
        processed += 1;
        lamports += acct.rentLamports || TOKEN_ACCOUNT_RENT_LAMPORTS;
      }

      this.sessionReclaimedAccounts += processed;
      this.sessionReclaimedLamports += lamports;
      this.phase.set('success');
      this.successInfo.set({ count: processed, signature: lastSig ?? '' });
      this.progress.set(null);
      this.burnConfirmation.set(false);
      this.selectedMints.set(new Set());

      // Re-fetch accounts so closed mints fall off the list.
      this.loadAccounts().catch(() => {});
      this.reclaimed.emit({
        totalAccounts: this.sessionReclaimedAccounts,
        totalLamports: this.sessionReclaimedLamports,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Transaction failed';
      this.error.set(msg);
      this.phase.set('error');
      this.progress.set(null);
    }
  }
}
