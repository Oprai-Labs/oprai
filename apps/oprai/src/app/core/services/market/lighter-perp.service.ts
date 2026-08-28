/**
 * Lighter Perpetuals Service (Robinhood Chain Lighter Domain)
 *
 * API: https://api.rh.lighter.xyz  (proxied via the gateway)
 * Domain: zero-fee perps — stock perps (NVDA/TSLA/PLTR/COIN…) settled in USDG,
 *         plus crypto perps. Non-custodial: OPRAI signs trades with a delegated
 *         agent key the user authorised once (see the onboarding flow), so reads
 *         and trades are gas-free.
 *
 * Reads run through the gateway (`GET /market/lighter/account`) so the request
 * is authenticated and the account is resolved from the user's linked EVM
 * (L1) address. Mirrors JupiterPerpService so the Lighter query card can be a
 * faithful clone with the venue swapped.
 */
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';
import { AccountService } from '../account.service';

export interface LighterPosition {
  market: string;           // "NVDA", "TSLA", "BTC"…
  side: 'long' | 'short';
  sizeUsd: number;          // position size in USD
  collateral: number;       // collateral in USD
  entryPrice: number;       // USD
  currentPrice: number;     // USD (mark)
  unrealizedPnl: number;    // USD
  liquidationPrice: number; // USD
  leverage: number;         // e.g. 5.0
  baseAmount: number;       // position base size (for a close)
  closed?: boolean;         // UI flag: user initiated a close; keep it shown as closed
}

export interface LighterAccount {
  found: boolean;
  accountIndex: number | null;
  availableBalance: number;   // USD
  collateral: number;         // USD
  agentEnabled: boolean;      // has the account authorised OPRAI's agent key?
  positions: LighterPosition[];
}

export interface LighterMarket {
  symbol: string;                    // "NVDA", "CASHCAT", "BTC"…
  marketId: number | null;
  markPrice: number;                 // live mark price (USD)
  lastPrice: number;
  sizeDecimals: number;              // rounding for the base size
  priceDecimals: number;
  minBaseAmount: number;             // min order size in base units
  minQuoteAmount: number;            // min order VALUE in USD (~$10)
  maxLeverage: number;               // per-market cap (CASHCAT 3, NVDA 20, BTC 50)
  maintenanceMarginFraction: number; // fraction, e.g. 0.012 — for liq price
  takerFee: number;
  makerFee: number;
  dailyChangePct: number;
}

@Injectable({ providedIn: 'root' })
export class LighterPerpService {
  private readonly api = inject(ApiService);
  private readonly account = inject(AccountService);

  /**
   * Resolve the user's EVM (L1) address. Prefers the currently-connected
   * window.ethereum account, falling back to the account's linked evm_wallet
   * identity so a read works even before the wallet has granted access.
   */
  async resolveEvmAddress(): Promise<string> {
    try {
      const a = await (window as any).ethereum?.request?.({ method: 'eth_accounts' });
      if (a?.[0]) return a[0];
    } catch { /* not connected */ }
    try {
      const me = await firstValueFrom(this.account.getMe());
      return (me.identities || []).find((i) => i.type === 'evm_wallet')?.identifier || '';
    } catch { return ''; }
  }

  /**
   * All active Lighter markets with live prices + trading limits. PUBLIC — no
   * wallet needed, so the trade card can populate its picker and poll the price
   * (every 3s) before the user has even connected. Always fetches fresh so the
   * poll gets a current mark price.
   */
  async markets(): Promise<LighterMarket[]> {
    try {
      const d = await firstValueFrom(this.api.get<any>('/market/lighter/markets'));
      const num = (v: unknown) => { const n = parseFloat(String(v ?? '0')); return Number.isFinite(n) ? n : 0; };
      return (d?.markets ?? []).map((m: any) => ({
        symbol: String(m.symbol ?? '').toUpperCase(),
        marketId: m.market_id ?? m.marketId ?? null,
        markPrice: num(m.mark_price ?? m.markPrice),
        lastPrice: num(m.last_price ?? m.lastPrice),
        sizeDecimals: Math.trunc(num(m.size_decimals ?? m.sizeDecimals)),
        priceDecimals: Math.trunc(num(m.price_decimals ?? m.priceDecimals)),
        minBaseAmount: num(m.min_base_amount ?? m.minBaseAmount),
        minQuoteAmount: num(m.min_quote_amount ?? m.minQuoteAmount) || 10,
        maxLeverage: Math.max(1, Math.trunc(num(m.max_leverage ?? m.maxLeverage)) || 1),
        maintenanceMarginFraction: num(m.maintenance_margin_fraction ?? m.maintenanceMarginFraction),
        takerFee: num(m.taker_fee ?? m.takerFee),
        makerFee: num(m.maker_fee ?? m.makerFee),
        dailyChangePct: num(m.daily_change_pct ?? m.dailyChangePct),
      })) as LighterMarket[];
    } catch {
      return [];
    }
  }

  /** Full Lighter account snapshot (balance, agent status, open positions). */
  async getAccount(evmAddress?: string): Promise<LighterAccount> {
    const empty: LighterAccount = {
      found: false, accountIndex: null, availableBalance: 0, collateral: 0,
      agentEnabled: false, positions: [],
    };
    const wallet = evmAddress || (await this.resolveEvmAddress());
    if (!wallet) return empty;
    try {
      const d = await firstValueFrom(this.api.get<any>('/market/lighter/account', { wallet }));
      if (!d || d.found === false) return { ...empty, found: false };
      const num = (v: unknown) => {
        const n = parseFloat(String(v ?? '0'));
        return Number.isFinite(n) ? n : 0;
      };
      const positions: LighterPosition[] = (d.positions ?? []).map((p: any) => ({
        market: (p.symbol ?? p.market ?? '').toString().toUpperCase(),
        side: (p.side ?? (num(p.sizeUsd ?? p.size_usd) < 0 ? 'short' : 'long')) as 'long' | 'short',
        sizeUsd: Math.abs(num(p.sizeUsd ?? p.size_usd ?? p.notional)),
        collateral: num(p.collateral ?? p.collateralUsd ?? p.margin),
        entryPrice: num(p.entryPrice ?? p.entry_price ?? p.avgEntryPrice),
        currentPrice: num(p.currentPrice ?? p.markPrice ?? p.mark_price),
        unrealizedPnl: num(p.unrealizedPnl ?? p.unrealized_pnl ?? p.pnl),
        liquidationPrice: num(p.liquidationPrice ?? p.liquidation_price ?? p.liqPrice),
        leverage: num(p.leverage),
        baseAmount: Math.abs(num(p.baseAmount ?? p.base_amount ?? p.size ?? p.position)),
      }));
      return {
        found: d.found ?? true,
        accountIndex: d.account_index ?? d.accountIndex ?? null,
        availableBalance: num(d.available_balance ?? d.availableBalance),
        collateral: num(d.collateral),
        agentEnabled: !!(d.agent_enabled ?? d.agentEnabled),
        positions,
      };
    } catch {
      return empty;
    }
  }

  /** Open perpetual positions for a wallet (empty when none / not onboarded). */
  async getPositions(evmAddress?: string): Promise<LighterPosition[]> {
    return (await this.getAccount(evmAddress)).positions;
  }

  /**
   * Like getPositions but reports whether the fetch actually succeeded, so
   * callers can distinguish "no open positions" (ok, empty) from "couldn't
   * reach the API" (not ok). A snapshot reconcile must not mark positions
   * closed on a transient failure — only on a confirmed-empty live result.
   */
  async getPositionsResult(evmAddress?: string): Promise<{ ok: boolean; positions: LighterPosition[] }> {
    const wallet = evmAddress || (await this.resolveEvmAddress());
    if (!wallet) return { ok: false, positions: [] };
    const acct = await this.getAccount(wallet);
    return { ok: acct.found, positions: acct.positions };
  }
}
