/**
 * AgentRunnerService
 *
 * Yield optimization agent. Scans user portfolio, finds yield improvements,
 * queues action proposals for user approval via the standard action card flow.
 *
 * Design: PROPOSAL-BASED (not fully autonomous).
 * The agent never executes anything without user approval.
 */

import { Injectable, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { WalletService } from './wallet.service';
import { YieldScannerService, YieldOpportunity } from './yield-scanner.service';
import { ParsedAction } from '@features/chat/services/intent-parser.service';
import { environment } from '../../../environments/environment';

// ── Types ────────────────────────────────────────────────────────────────────

export type AgentStatus = 'idle' | 'scanning' | 'ready' | 'error';

export interface AgentProposal {
  id: string;
  opportunity: YieldOpportunity;
  action: ParsedAction;
  status: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed';
  createdAt: number;
  executedAt?: number;
  txSignature?: string;
}

export interface AgentRun {
  id: string;
  startedAt: number;
  finishedAt?: number;
  proposals: AgentProposal[];
  config: AgentConfig;
  error?: string;
}

export interface AgentConfig {
  /** Minimum APY gain to surface an opportunity (percentage points) */
  minGainPct: number;
  /** Max risk score 1-5 */
  maxRisk: number;
  /** Only include tokens with USD value above this */
  minHoldingUsd: number;
}

const DEFAULT_CONFIG: AgentConfig = {
  minGainPct: 0.5,
  maxRisk: 3,
  minHoldingUsd: 10,
};

const HISTORY_KEY = 'oprai:agent-runs';
const MAX_HISTORY = 10;

// ── Service ──────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class AgentRunnerService {
  private readonly wallet = inject(WalletService);
  private readonly scanner = inject(YieldScannerService);

  private readonly _status$ = new BehaviorSubject<AgentStatus>('idle');
  private readonly _currentRun$ = new BehaviorSubject<AgentRun | null>(null);
  private readonly _history$ = new BehaviorSubject<AgentRun[]>(this.loadHistory());

  readonly status$ = this._status$.asObservable();
  readonly currentRun$ = this._currentRun$.asObservable();
  readonly history$ = this._history$.asObservable();

  // ── Public API ─────────────────────────────────────────────────────────────

  status(): AgentStatus { return this._status$.value; }
  currentRun(): AgentRun | null { return this._currentRun$.value; }
  history(): AgentRun[] { return this._history$.value; }

  /**
   * Run a full yield scan for the connected wallet.
   * Produces proposals in currentRun$.
   */
  async run(config: AgentConfig = DEFAULT_CONFIG): Promise<AgentRun> {
    const wallet = this.wallet.publicKey();
    if (!wallet) throw new Error('No wallet connected');
    if (this._status$.value === 'scanning') throw new Error('Already scanning');

    this._status$.next('scanning');

    const run: AgentRun = {
      id: `run-${Date.now()}`,
      startedAt: Date.now(),
      proposals: [],
      config,
    };
    this._currentRun$.next(run);

    try {
      // Build holdings list — we use static estimates here for a first scan.
      // In production this would integrate with WalletService.getBalances().
      const holdings = await this.buildHoldings(wallet);

      const opportunities = await this.scanner.findOpportunities(holdings, {
        minGainPct: config.minGainPct,
        maxRisk: config.maxRisk,
      });

      run.proposals = opportunities.map(opp => this.opportunityToProposal(opp));
      run.finishedAt = Date.now();

      this._status$.next('ready');
      this._currentRun$.next({ ...run });

      this.addToHistory(run);
      return run;
    } catch (err: any) {
      run.error = err?.message ?? 'Scan failed';
      run.finishedAt = Date.now();
      this._status$.next('error');
      this._currentRun$.next({ ...run });
      throw err;
    }
  }

  /** Approve a proposal (marks it as approved — execution is done by action card) */
  approveProposal(proposalId: string): void {
    this.updateProposal(proposalId, { status: 'approved' });
  }

  /** Reject a proposal */
  rejectProposal(proposalId: string): void {
    this.updateProposal(proposalId, { status: 'rejected' });
  }

  /** Mark a proposal as executed with a tx signature */
  markExecuted(proposalId: string, txSignature: string): void {
    this.updateProposal(proposalId, {
      status: 'executed',
      executedAt: Date.now(),
      txSignature,
    });
  }

  /** Reset to idle */
  reset(): void {
    this._status$.next('idle');
    this._currentRun$.next(null);
  }

  // ── Private ────────────────────────────────────────────────────────────────

  /**
   * Build a list of holdings by fetching real token balances from the Solana RPC
   * and USD prices from the Jupiter price API.
   */
  private async buildHoldings(
    wallet: string
  ): Promise<Array<{ token: string; mint: string; amountUsd: number; currentProtocol?: string; currentApy?: number }>> {
    try {
      const { Connection, PublicKey } = await import('@solana/web3.js');
      const rpc = environment.solanaRpc;
      const connection = new Connection(rpc, { commitment: 'confirmed', httpHeaders: { 'X-Requested-With': 'XMLHttpRequest' } });
      const walletPk = new PublicKey(wallet);

      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
      const TOKEN_2022    = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';

      // Fetch SOL balance and all token accounts in parallel
      const [solLamports, r1, r2] = await Promise.all([
        connection.getBalance(walletPk),
        connection.getParsedTokenAccountsByOwner(walletPk, { programId: new PublicKey(TOKEN_PROGRAM) }),
        connection.getParsedTokenAccountsByOwner(walletPk, { programId: new PublicKey(TOKEN_2022) }),
      ]);

      // Collect raw balances: { mint, symbol, amount }
      const rawBalances: Array<{ mint: string; symbol: string; amount: number }> = [];

      // SOL
      const solAmount = solLamports / 1e9;
      if (solAmount > 0) rawBalances.push({ mint: SOL_MINT, symbol: 'SOL', amount: solAmount });

      // SPL tokens
      for (const account of [...r1.value, ...r2.value]) {
        const info = (account.account.data as any).parsed?.info;
        const mint: string = info?.mint ?? '';
        const uiAmount: number = info?.tokenAmount?.uiAmount ?? 0;
        const symbol: string = info?.tokenAmount?.decimals != null ? '' : '';
        if (mint && uiAmount > 0) {
          rawBalances.push({ mint, symbol, amount: uiAmount });
        }
      }

      if (rawBalances.length === 0) return [];

      // Fetch USD prices from Jupiter
      const mints = rawBalances.map(b => b.mint).join(',');
      let prices: Record<string, number> = {};
      try {
        const priceResp = await fetch(
          `https://price.jup.ag/v4/price?ids=${mints}`,
          { signal: AbortSignal.timeout(8000) }
        );
        if (priceResp.ok) {
          const priceData: any = await priceResp.json();
          for (const [mint, info] of Object.entries(priceData?.data ?? {})) {
            prices[mint] = (info as any).price ?? 0;
          }
        }
      } catch { /* skip prices on error */ }

      return rawBalances
        .map(b => ({
          token: b.symbol || b.mint.slice(0, 4) + '…',
          mint: b.mint,
          amountUsd: b.amount * (prices[b.mint] ?? 0),
          currentProtocol: 'wallet',
          currentApy: 0,
        }))
        .filter(h => h.amountUsd >= 10);
    } catch {
      // Fall back to empty list rather than crashing the scan
      return [];
    }
  }

  private opportunityToProposal(opp: YieldOpportunity): AgentProposal {
    const action: ParsedAction = {
      type: opp.actionType,
      params: { ...opp.actionParams },
      raw: `[ACTION:${opp.actionType}] ${JSON.stringify(opp.actionParams)}`,
    };

    return {
      id: `proposal-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      opportunity: opp,
      action,
      status: 'pending',
      createdAt: Date.now(),
    };
  }

  private updateProposal(proposalId: string, update: Partial<AgentProposal>): void {
    const run = this._currentRun$.value;
    if (!run) return;
    const proposals = run.proposals.map(p =>
      p.id === proposalId ? { ...p, ...update } : p
    );
    this._currentRun$.next({ ...run, proposals });
  }

  private addToHistory(run: AgentRun): void {
    const history = [run, ...this._history$.value].slice(0, MAX_HISTORY);
    this._history$.next(history);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    } catch { /* ignore */ }
  }

  private loadHistory(): AgentRun[] {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  }
}
