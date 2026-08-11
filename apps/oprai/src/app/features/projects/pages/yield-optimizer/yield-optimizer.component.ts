import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { AgentRunnerService, AgentConfig, AgentProposal } from '@core/services/agent-runner.service';
import { YieldScannerService, ProtocolYield } from '@core/services/yield-scanner.service';
import { WalletService } from '@core/services/wallet.service';
import { SolanaActionService } from '@features/chat/services/solana-action.service';
import { NotificationService } from '@core/services/notification.service';
import { SpendingLimitService } from '@core/services/spending-limit.service';

@Component({
  selector: 'app-yield-optimizer',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './yield-optimizer.component.html',
  styleUrl: './yield-optimizer.component.scss',
})
export class YieldOptimizerComponent implements OnInit {
  private readonly agentRunner = inject(AgentRunnerService);
  private readonly scanner = inject(YieldScannerService);
  private readonly walletService = inject(WalletService);
  private readonly solanaAction = inject(SolanaActionService);
  private readonly notifications = inject(NotificationService);
  private readonly spendingLimit = inject(SpendingLimitService);
  private readonly router = inject(Router);

  readonly status = toSignal(this.agentRunner.status$, { initialValue: 'idle' as const });
  readonly currentRun = toSignal(this.agentRunner.currentRun$, { initialValue: null });
  readonly history = toSignal(this.agentRunner.history$, { initialValue: [] });

  readonly allYields = signal<ProtocolYield[]>([]);
  readonly allYieldsLoading = signal(false);
  readonly activeTab = signal<'opportunities' | 'yields' | 'history'>('opportunities');

  // Config
  readonly config = signal<AgentConfig>({
    minGainPct: 0.5,
    maxRisk: 3,
    minHoldingUsd: 10,
  });

  readonly proposals = computed(() => this.currentRun()?.proposals ?? []);
  readonly pendingProposals = computed(() => this.proposals().filter(p => p.status === 'pending'));
  readonly approvedProposals = computed(() => this.proposals().filter(p => p.status === 'approved' || p.status === 'executed'));

  readonly isConnected = computed(() => !!this.walletService.publicKey());
  readonly isScanning = computed(() => this.status() === 'scanning');

  ngOnInit(): void {
    this.loadAllYields();
  }

  // ── Tab ────────────────────────────────────────────────────────────────────

  setTab(tab: 'opportunities' | 'yields' | 'history'): void {
    this.activeTab.set(tab);
  }

  // ── Scan ───────────────────────────────────────────────────────────────────

  async runScan(): Promise<void> {
    if (!this.isConnected()) {
      this.notifications.warning('Connect your wallet first', 4000);
      return;
    }
    try {
      await this.agentRunner.run(this.config());
    } catch (err: any) {
      this.notifications.error(err?.message ?? 'Scan failed', 6000);
    }
  }

  reset(): void {
    this.agentRunner.reset();
  }

  // ── Proposals ──────────────────────────────────────────────────────────────

  async executeProposal(proposal: AgentProposal): Promise<void> {
    const limitCheck = this.spendingLimit.check(proposal.opportunity.estimatedAnnualGainUsd ?? 0);
    if (!limitCheck.allowed) {
      this.notifications.error(`Spending limit: ${limitCheck.reason === 'per_tx' ? `exceeds per-tx limit $${limitCheck.limitUsd}` : `daily limit reached`}`, 6000);
      return;
    }

    try {
      this.agentRunner.approveProposal(proposal.id);
      const sig = await this.solanaAction.execute(proposal.action);
      this.agentRunner.markExecuted(proposal.id, sig);
      this.notifications.success?.('Transaction submitted', 4000);
    } catch (err: any) {
      if (err?.message !== 'cancelled_by_user') {
        this.notifications.error(err?.message ?? 'Transaction failed', 6000);
      } else {
        // User cancelled risk dialog — reset proposal to pending
        this.agentRunner.reset();
      }
    }
  }

  rejectProposal(proposal: AgentProposal): void {
    this.agentRunner.rejectProposal(proposal.id);
  }

  setMinGain(event: Event): void {
    const v = +(event.target as HTMLInputElement).value;
    this.config.update(c => ({ ...c, minGainPct: v }));
  }

  setMaxRisk(event: Event): void {
    const v = +(event.target as HTMLSelectElement).value;
    this.config.update(c => ({ ...c, maxRisk: v }));
  }

  // ── Yield table ────────────────────────────────────────────────────────────

  private async loadAllYields(): Promise<void> {
    this.allYieldsLoading.set(true);
    try {
      const yields = await this.scanner.getAllYields();
      this.allYields.set(yields);
    } finally {
      this.allYieldsLoading.set(false);
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  formatApy(apy: number): string {
    return apy.toFixed(2) + '%';
  }

  formatUsd(usd: number): string {
    if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(1)}M`;
    if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
    return `$${usd.toFixed(0)}`;
  }

  riskLabel(score: number): string {
    return ['', 'Very Low', 'Low', 'Medium', 'High', 'Very High'][score] ?? 'Unknown';
  }

  riskColor(score: number): string {
    return ['', 'var(--op-success, #22c55e)', 'var(--op-success, #22c55e)', 'var(--op-warning, #f59e0b)', 'var(--op-danger, #ef4444)', 'var(--op-danger, #ef4444)'][score] ?? '';
  }

  protocolIcon(protocol: string): string {
    const map: Record<string, string> = {
      jito: 'zap', marinade: 'droplets', jupsol: 'layers',
    };
    return map[protocol] ?? 'circle';
  }

  historyProposalCount(run: any): number {
    return run.proposals?.length ?? 0;
  }

  historyExecutedCount(run: any): number {
    return run.proposals?.filter((p: any) => p.status === 'executed').length ?? 0;
  }

  navigateToChat(): void {
    this.router.navigate(['/']);
  }
}
