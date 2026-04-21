/**
 * RiskWarningService
 *
 * Shows risk warnings to the user before executing a transaction.
 * RiskWarningDialogComponent bu servisin state'ini dinler ve resolve/reject eder.
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

export interface RiskWarningPayload {
  title: string;
  message: string;
  severity: 'info' | 'warning' | 'danger';
  items: RiskWarningItem[];
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
  'launch_token', 'bonkfun_launch',
]);

const VOLATILE_BUY_TYPES = new Set([
  'pumpfun_buy', 'bonkfun_buy',
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

  private buildPayload(
    action: ParsedAction,
    amountUsd: number,
  ): Omit<RiskWarningPayload, 'resolve'> | null {
    const items: RiskWarningItem[] = [];
    let title = '';
    let message = '';
    let severity: 'info' | 'warning' | 'danger' = 'info';
    let confirmLabel = 'Confirm';
    let needsWarning = false;

    // ── Leveraged / Perp positions ──────────────────────────────────────────
    if (LEVERAGED_TYPES.has(action.type)) {
      needsWarning = true;
      title = 'Leveraged Position';
      message = 'This action opens a leveraged position. Losses can exceed your initial investment.';
      severity = 'danger';
      confirmLabel = 'I understand the risks';
      items.push(
        { icon: 'triangle-alert', text: 'Liquidation risk if price moves against your position' },
        { icon: 'trending-down',  text: 'Losses can exceed deposited collateral' },
        { icon: 'clock',          text: 'Funding rates may apply over time' },
      );
      const leverage = Number(action.params['leverage']);
      if (leverage > 0) {
        items.push({ icon: 'zap', text: `${leverage}x leverage selected` });
        // Price move % needed to trigger liquidation (approximate, ignores fees)
        const liqBuffer = (1 / leverage * 100).toFixed(1);
        items.push({ icon: 'target', text: `Position liquidated if price moves ~${liqBuffer}% against you` });
      }
      const collateral = Number(action.params['collateral'] ?? action.params['amount']);
      if (collateral > 0 && leverage > 0) {
        const maxLoss = collateral.toFixed(collateral < 1 ? 4 : 2);
        items.push({ icon: 'shield-alert', text: `Up to ${maxLoss} ${action.params['token'] ?? 'SOL'} at risk if liquidated` });
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

    // ── Token launch ────────────────────────────────────────────────────────
    else if (LAUNCH_TYPES.has(action.type)) {
      needsWarning = true;
      title = 'Token Launch';
      message = 'You are about to launch a new token on a bonding curve.';
      severity = 'warning';
      confirmLabel = 'Launch token';
      items.push(
        { icon: 'rocket',        text: 'Token will be listed on the bonding curve immediately' },
        { icon: 'users',         text: 'Trading opens to all users upon launch' },
        { icon: 'info',          text: 'Liquidity migrates to DEX at graduation threshold' },
      );
    }

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

    return { title, message, severity, items, confirmLabel };
  }

  private formatUsd(amount: number): string {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    }).format(amount);
  }
}
