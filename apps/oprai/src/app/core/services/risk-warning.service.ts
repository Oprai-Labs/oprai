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
  /** The mint this warning is about, shown verbatim with a copy button.
   *  A look-alike token is identified by its address and nothing else, so
   *  telling someone to "check the token" without showing it is empty advice. */
  mint?: string;
  /** Gate the confirm button behind a deliberate tick. Reserved for warnings
   *  where the danger is that the user is about to act on an assumption. */
  requireAck?: boolean;
  /** What the tick says. */
  ackLabel?: string;
  confirmLabel: string;
  resolve: (confirmed: boolean) => void;
}

// Action types that OPEN leveraged exposure. Closes are excluded — closing a
// position de-risks (realizes PnL + pays a close fee), it doesn't open leverage,
// so it shouldn't trigger the "opens a leveraged position" gate.
const LEVERAGED_TYPES = new Set([
  'perp_open',
]);

// JLP liquidity (add/remove) — NOT leveraged. You can't be liquidated and can't
// lose more than you deposit, but JLP is not a stablecoin: it tracks a basket
// (SOL/ETH/BTC + stables) and you're the counterparty to perp traders.
const JLP_TYPES = new Set([
  'jlp_add', 'jlp_remove',
]);

const BORROW_TYPES = new Set([
  'borrow', 'kamino_borrow',
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
  async confirmUnverifiedMint(symbol: string, name: string, mint?: string): Promise<boolean> {
    return new Promise<boolean>(resolve => {
      this.warning$.next({
        title: 'Unverified Token',
        message:
          `This token is not in the OPRAI verified registry. ` +
          `Jupiter recognises it as “${symbol}${name && name !== symbol ? ` (${name})` : ''}” — ` +
          `make sure that is what you intended to trade.`,
        severity: 'danger',
        confirmLabel: 'Continue',
        mint,
        // The whole risk here is mistaken identity, and the only thing that
        // settles it is the address. Asking for the tick makes checking it a
        // step rather than a suggestion.
        requireAck: !!mint,
        ackLabel: 'I checked the mint address',
        items: [
          { icon: 'shield-alert', text: `Token symbols aren't unique — ${symbol} can be duplicated.` },
          { icon: 'alert-circle', text: 'Look-alike mint addresses are a common scam.' },
          { icon: 'rotate-ccw', text: "Signed swaps can't be reversed." },
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
      message = 'This action opens a leveraged position. You can lose your entire collateral if the price moves against you.';
      severity = 'danger';
      confirmLabel = 'I understand the risks';
      // Qualitative risks — plain sentences, no per-row iconography.
      // Jupiter Perps uses ISOLATED margin: liquidation caps your loss at the
      // collateral you deposit, and it charges an hourly BORROW fee (paid to the
      // JLP pool), not a funding-rate payment.
      items.push(
        { icon: '', text: 'Your position can be liquidated if the price moves against you.' },
        { icon: '', text: 'You can lose your entire deposited collateral.' },
        { icon: '', text: 'A borrow fee accrues hourly for as long as the position stays open.' },
      );
      // Quantitative facts — surfaced as a compact key-figures strip.
      const leverage = Number(action.params['leverage']);
      if (leverage > 0) {
        figures.push({ label: 'Leverage', value: `${leverage}x` });
        // Approx price move against you that wipes the collateral. Real
        // liquidation triggers slightly earlier (fees + maintenance margin).
        const liqBuffer = (1 / leverage * 100).toFixed(1);
        figures.push({ label: 'Liquidation move', value: `~${liqBuffer}%` });
      }
      const collateral = Number(action.params['collateralAmount'] ?? action.params['collateral'] ?? action.params['amount']);
      if (collateral > 0) {
        const maxLoss = collateral.toFixed(collateral < 1 ? 4 : 2);
        const collToken = action.params['collateralToken'] ?? action.params['token'] ?? '';
        figures.push({ label: 'Max at risk', value: `${maxLoss}${collToken ? ' ' + collToken : ''}`, emphasis: true });
      }
    }

    // ── JLP liquidity (add / remove) ─────────────────────────────────────────
    else if (JLP_TYPES.has(action.type)) {
      needsWarning = true;
      title = action.type === 'jlp_remove' ? 'Redeem JLP' : 'Provide JLP Liquidity';
      message = action.type === 'jlp_remove'
        ? 'Redeeming JLP sells your pool share back at the current pool price.'
        : 'JLP is a liquidity position, not a stablecoin. Its value moves with the pool and is not guaranteed.';
      severity = 'warning';
      confirmLabel = action.type === 'jlp_remove' ? 'Redeem JLP' : 'Provide liquidity';
      // Accurate, non-leverage risks. No liquidation, no losses beyond deposit.
      items.push(
        { icon: '', text: 'JLP tracks a basket (SOL, ETH, BTC + stablecoins) — its price can fall if those drop.' },
        { icon: '', text: 'As a liquidity provider you are the counterparty to perp traders; when traders profit, the pool can lose.' },
        { icon: '', text: 'A pool fee and price impact apply when you enter or exit.' },
      );
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
    // `inputMint` can arrive as the "SOL" symbol OR the wrapped-SOL mint, so
    // match both — otherwise the warning silently never fires on the mint form.
    else if (
      action.type === 'swap' &&
      ['SOL', 'SO11111111111111111111111111111111111111112'].includes(
        String(action.params['inputMint']).toUpperCase(),
      ) &&
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
      // Only a transfer has a "recipient"; swaps/stakes/lends/DCA etc. don't —
      // point those at the token + amount instead of a non-existent recipient.
      const verifyText = action.type === 'transfer'
        ? 'Double-check the recipient address'
        : 'Double-check the token and amount';
      items.push(
        { icon: 'dollar-sign',   text: `Amount: ${this.formatUsd(amountUsd)}` },
        { icon: 'shield-check',  text: verifyText },
        { icon: 'rotate-ccw',    text: 'Once signed, the transaction cannot be reversed' },
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
