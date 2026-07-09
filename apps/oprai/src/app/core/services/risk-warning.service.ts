/**
 * RiskWarningService
 *
 * Shows risk warnings to the user before executing a transaction.
 * RiskWarningDialogComponent listens to this service's state and resolves/rejects the promise.
 *
 * Usage:
 *   const confirmed = await this.riskWarning.confirm(action, amountUsd);
 *   if (!confirmed) return; // user cancelled
 */

import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
import { ParsedAction } from '@features/chat/services/intent-parser.service';

export interface RiskWarningItem {
  icon: string;
  text: string;
}

/** A compact numeric fact rendered in the key-figures strip (label + value). */
export interface RiskWarningFigure {
  label: string;
  value: string;
  /** When true, the figure is emphasised in the severity colour (e.g. max loss). */
  emphasis?: boolean;
}

export interface RiskWarningPayload {
  title: string;
  message: string;
  severity: 'info' | 'warning' | 'danger';
  items: RiskWarningItem[];
  figures?: RiskWarningFigure[];
  confirmLabel: string;
  resolve: (confirmed: boolean) => void;
}

// Action types that are inherently risky regardless of amount
const LEVERAGED_TYPES = new Set([
  'perp_open', 'perp_close', 'jlp_add', 'jlp_remove',
  'kamino_multiply_open', 'kamino_multiply_add', 'kamino_long_open', 'kamino_short_open',
]);

const BORROW_TYPES = new Set([
  'borrow', 'kamino_borrow', 'marginfi_borrow',
]);

const LAUNCH_TYPES = new Set([
  'launch_token', 'pumpfun_launch', 'token_launch',
]);

const VOLATILE_BUY_TYPES = new Set([
  'pumpfun_buy', 'pumpswap_buy',
]);

// High-value thresholds
const WARN_THRESHOLD_USD  = 100;   // Show warning dialog
const DANGER_THRESHOLD_USD = 1000; // Show danger dialog

@Injectable({ providedIn: 'root' })
export class RiskWarningService {
  readonly warning$ = new Subject<RiskWarningPayload>();

  /**
   * Check whether a risk warning is needed for a transaction.
   * If needed, show dialog and return user decision.
   * If not needed, return true directly.
   */
  async confirm(action: ParsedAction, amountUsd: number): Promise<boolean> {
    const payload = this.buildPayload(action, amountUsd);
    if (!payload) return true; // No warning needed

    return new Promise<boolean>(resolve => {
      this.warning$.next({ ...payload, resolve });
    });
  }

  /**
   * Force the user to acknowledge a mint that Jupiter knows about but is not
   * in our verified `shared/tokens.json` registry. Surfaces the *real* symbol
   * Jupiter returned so a vanity-prefix imposter (`J1toso1u…<garbage>`) can't
   * masquerade as JitoSOL through prompt manipulation.
   */
  async confirmUnverifiedMint(symbol: string, name: string): Promise<boolean> {
    return new Promise<boolean>(resolve => {
      this.warning$.next({
        title: 'Unverified Token',
        message:
          `This token is not in the OPRAI verified registry. ` +
          `Jupiter recognises it as “${symbol}${name && name !== symbol ? ` (${name})` : ''}” — ` +
          `make sure that is what you intended to trade.`,
        severity: 'danger',
        confirmLabel: 'I understand, proceed',
        items: [
          { icon: 'shield-alert', text: `Symbol returned by Jupiter: ${symbol}` },
          { icon: 'alert-circle', text: 'Vanity-prefix scams can mimic well-known tokens.' },
          { icon: 'rotate-ccw', text: 'Once signed, the swap cannot be reversed.' },
        ],
        resolve,
      });
    });
  }

  private buildPayload(
    action: ParsedAction,
    amountUsd: number,
  ): Omit<RiskWarningPayload, 'resolve'> | null {
    const items: RiskWarningItem[] = [];
    const figures: RiskWarningFigure[] = [];
    let title = '';
    let message = '';
    let severity: 'info' | 'warning' | 'danger' = 'info';
    let confirmLabel = 'Confirm';
    let needsWarning = false;

    // ── Leveraged / Perp positions ──────────────────────────────────────────
    if (LEVERAGED_TYPES.has(action.type)) {
      needsWarning = true;
      title = 'Leveraged Position';
      message = 'This action opens a leveraged position. Losses can exceed the collateral you put up.';
      severity = 'danger';
      confirmLabel = 'I understand the risks';
      // Qualitative risks — plain sentences, no per-row iconography.
      items.push(
        { icon: '', text: 'Your position can be liquidated if the price moves against you.' },
        { icon: '', text: 'Losses can exceed the collateral you deposit.' },
        { icon: '', text: 'Funding fees accrue for as long as the position stays open.' },
      );
      // Quantitative facts — surfaced as a compact key-figures strip.
      const leverage = Number(action.params['leverage']);
      if (leverage > 0) {
        figures.push({ label: 'Leverage', value: `${leverage}x` });
        // Price move % needed to trigger liquidation (approximate, ignores fees).
        const liqBuffer = (1 / leverage * 100).toFixed(1);
        figures.push({ label: 'Liquidation move', value: `~${liqBuffer}%` });
      }
      const collateral = Number(action.params['collateralAmount'] ?? action.params['collateral'] ?? action.params['amount']);
      if (collateral > 0 && leverage > 0) {
        const maxLoss = collateral.toFixed(collateral < 1 ? 4 : 2);
        figures.push({ label: 'Max at risk', value: `${maxLoss} ${action.params['token'] ?? 'SOL'}`, emphasis: true });
      }
    }

    // ── Borrowing ───────────────────────────────────────────────────────────
    else if (BORROW_TYPES.has(action.type)) {
      needsWarning = true;
      title = 'Borrow Position';
      message = 'Borrowing introduces liquidation risk if your collateral value drops.';
      severity = 'warning';
      confirmLabel = 'Confirm borrow';
      items.push(
        { icon: 'alert-circle',  text: 'Position can be liquidated if health factor falls below 1.0' },
        { icon: 'percent',       text: 'Variable interest rates apply' },
        { icon: 'shield-alert',  text: 'Monitor your health factor regularly' },
      );
    }

    // ── Token launch — no confirmation dialog needed; action card is the UX ──

    // ── Volatile meme token buy ─────────────────────────────────────────────
    else if (VOLATILE_BUY_TYPES.has(action.type) && amountUsd >= WARN_THRESHOLD_USD) {
      needsWarning = true;
      title = 'Volatile Token Purchase';
      message = 'Meme tokens are extremely volatile. You may lose your entire investment.';
      severity = 'danger';
      confirmLabel = 'I accept the risk';
      items.push(
        { icon: 'trending-down', text: 'Price can drop to zero at any time' },
        { icon: 'alert-circle',  text: 'No fundamental value guarantee' },
        { icon: 'flame',         text: 'Bonding curve slippage may be high' },
      );
    }

    // ── Swap all SOL (no fee reserve) ──────────────────────────────────────
    else if (
      action.type === 'swap' &&
      String(action.params['inputMint']).toUpperCase() === 'SOL' &&
      String(action.params['amount']).toLowerCase() === 'all'
    ) {
      needsWarning = true;
      title = 'Swapping All SOL';
      message = 'You are swapping your entire SOL balance. You may not have enough SOL left to pay transaction fees.';
      severity = 'warning';
      confirmLabel = 'Proceed anyway';
      items.push(
        { icon: 'alert-circle', text: 'Transaction fees require a small SOL balance (~0.01 SOL)' },
        { icon: 'info',         text: 'Consider keeping at least 0.01 SOL for future fees' },
        { icon: 'rotate-ccw',   text: 'Swaps are irreversible once signed' },
      );
    }

    // ── High-value transaction ──────────────────────────────────────────────
    else if (amountUsd >= WARN_THRESHOLD_USD) {
      needsWarning = true;
      const isDanger = amountUsd >= DANGER_THRESHOLD_USD;
      severity = isDanger ? 'danger' : 'warning';
      title = isDanger ? 'Large Transaction' : 'High-Value Transaction';
      message = `This transaction is worth ${this.formatUsd(amountUsd)}. Please verify all details before proceeding.`;
      confirmLabel = isDanger ? 'Confirm large transaction' : 'Confirm';
      items.push(
        { icon: 'dollar-sign',   text: `Amount: ${this.formatUsd(amountUsd)}` },
        { icon: 'shield-check',  text: 'Double-check recipient / token addresses' },
        { icon: 'rotate-ccw',    text: 'Blockchain transactions are irreversible' },
      );
    }

    if (!needsWarning) return null;

    return { title, message, severity, items, figures, confirmLabel };
  }

  private formatUsd(amount: number): string {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    }).format(amount);
  }
}
