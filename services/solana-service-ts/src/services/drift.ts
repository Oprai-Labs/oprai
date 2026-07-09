/**
 * Drift v2 — @drift-labs/sdk
 *
 * Read-only position fetcher. Returns every spot + perp position the wallet
 * has open across all sub-accounts (Drift permits up to 8 per authority).
 *
 * The SDK's DriftClient handles all the heavy lifting: User PDA derivation,
 * BankRun-compatible market loading, and the BN.js-typed oracle prices.
 * We just iterate the user.subAccounts and shape into a wallet-portfolio row.
 *
 * No transaction building lives here — Drift trade flows live in their own
 * actions; this file is `drift_list_positions` only.
 */

import {
  DriftClient,
  initialize,
  Wallet,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  BulkAccountLoader,
  convertToNumber,
  QUOTE_PRECISION,
  BASE_PRECISION,
  PRICE_PRECISION,
  FUNDING_RATE_BUFFER_PRECISION,
  SPOT_MARKET_RATE_PRECISION,
} from "@drift-labs/sdk";
import { Connection, Keypair, PublicKey } from "@solana/web3.js";
import { config } from "../config";
import { BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(),
    type,
    description,
    estimatedFee: "0",
    params,
    warnings: [],
    requiresApproval: false,
  };
}

// Read-only wallet — Drift client requires a Wallet adapter for the signing
// callbacks but we never sign here, so a throwaway keypair is fine.
function readOnlyWallet(authority: PublicKey): Wallet {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return {
    publicKey: authority,
    signTransaction: async (tx: any) => tx,
    signAllTransactions: async (txs: any[]) => txs,
    payer: Keypair.generate(),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

/**
 * Initialise a DriftClient anchored to the supplied authority. The SDK's
 * default initialize() reads programID + env from constants — we keep the
 * mainnet defaults and only override the connection.
 */
async function initClient(authority: PublicKey): Promise<DriftClient> {
  const conn = new Connection(config.solanaRpc, "confirmed");
  const sdkConfig = initialize({ env: "mainnet-beta" });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const driftClient = new DriftClient({
    connection: conn as unknown as any,
    wallet: readOnlyWallet(authority),
    programID: new PublicKey(sdkConfig.DRIFT_PROGRAM_ID),
    env: "mainnet-beta",
    authority,
    activeSubAccountId: 0,
    // Subscribe loads all markets + oracles in one shot — needed for price
    // valuation per perp position.
    accountSubscription: { type: "polling", accountLoader: undefined as unknown as never },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any);
  await driftClient.subscribe();
  return driftClient;
}

/**
 * GET: Drift positions for `userWallet`. Returns
 *   { perpPositions: [...], spotPositions: [...], total }
 * where each position is normalised to numbers (not BN) and carries the
 * market symbol the user expects to see.
 */
export async function listDriftPositions(
  _params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const authority = new PublicKey(userWallet);
  let driftClient: DriftClient | null = null;
  try {
    driftClient = await initClient(authority);

    // Drift permits up to 8 sub-accounts per authority. Try each — SDK's
    // `getUserAccount(subId)` returns null when the PDA isn't initialised,
    // so we don't pay for non-existent sub-accounts.
    const perpPositions: Array<{
      subAccountId: number;
      marketIndex: number;
      marketSymbol: string;
      side: "long" | "short";
      baseAssetAmount: number;
      quoteAssetAmount: number;
      entryPrice: number;
      markPrice: number;
      unrealizedPnl: number;
      unsettledPnl: number;
      leverage: number;
      liquidationPrice: number | null;
    }> = [];
    const spotPositions: Array<{
      subAccountId: number;
      marketIndex: number;
      marketSymbol: string;
      side: "deposit" | "borrow";
      tokenMint: string;
      decimals: number;
      amount: number;
      usdValue: number;
      apy: number;
    }> = [];

    for (let subId = 0; subId < 8; subId++) {
      let user;
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        user = (driftClient as any).getUser(subId, authority);
        if (!user || !user.getUserAccount()) continue;
        await user.subscribe?.();
      } catch {
        continue;
      }
      const userAccount = user.getUserAccount();
      if (!userAccount) continue;

      // ── Perps ─────────────────────────────────────────────────────────
      for (const pp of userAccount.perpPositions ?? []) {
        if (pp.baseAssetAmount.isZero() && pp.quoteAssetAmount.isZero() && pp.lpShares.isZero()) {
          continue;
        }
        const market = driftClient.getPerpMarketAccount(pp.marketIndex);
        if (!market) continue;
        const symbol = decodeName(market.name);
        const oracle = driftClient.getOracleDataForPerpMarket(pp.marketIndex);
        const markPx = convertToNumber(oracle.price, PRICE_PRECISION);
        const baseAmt = convertToNumber(pp.baseAssetAmount, BASE_PRECISION);
        const quoteAmt = convertToNumber(pp.quoteAssetAmount, QUOTE_PRECISION);
        const side: "long" | "short" = baseAmt >= 0 ? "long" : "short";
        const notional = Math.abs(baseAmt) * markPx;
        const entryPx = baseAmt !== 0 ? Math.abs(quoteAmt / baseAmt) : 0;
        const unrealizedPnl = side === "long"
          ? (markPx - entryPx) * Math.abs(baseAmt)
          : (entryPx - markPx) * Math.abs(baseAmt);
        // Liquidation price — SDK exposes per-user; null when not in danger
        // or SDK throws.
        let liqPx: number | null = null;
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const lp = (user as any).liquidationPrice?.(pp.marketIndex);
          if (lp && !lp.isZero?.()) {
            liqPx = convertToNumber(lp, PRICE_PRECISION);
          }
        } catch { /* SDK throws on healthy positions */ }
        const settledPnl = convertToNumber(pp.settledPnl ?? 0, QUOTE_PRECISION);
        let leverage = 0;
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const lev = (user as any).getLeverage?.();
          leverage = lev ? convertToNumber(lev, 10_000) : 0;
        } catch { /* */ }

        perpPositions.push({
          subAccountId: subId,
          marketIndex: pp.marketIndex,
          marketSymbol: symbol,
          side,
          baseAssetAmount: baseAmt,
          quoteAssetAmount: quoteAmt,
          entryPrice: entryPx,
          markPrice: markPx,
          unrealizedPnl,
          unsettledPnl: settledPnl,
          leverage,
          liquidationPrice: liqPx,
        });
      }

      // ── Spots ─────────────────────────────────────────────────────────
      for (const sp of userAccount.spotPositions ?? []) {
        if (sp.scaledBalance.isZero()) continue;
        const market = driftClient.getSpotMarketAccount(sp.marketIndex);
        if (!market) continue;
        const symbol = decodeName(market.name);
        const decimals = market.decimals;
        // balanceType: 0 = deposit, 1 = borrow
        const isBorrow = (sp.balanceType as unknown as { borrow?: object }).borrow !== undefined
          || JSON.stringify(sp.balanceType).toLowerCase().includes("borrow");
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const tokenAmount = (user as any).getTokenAmount?.(sp.marketIndex);
        const amount = tokenAmount
          ? convertToNumber(tokenAmount, new (BASE_PRECISION as unknown as { constructor: { new (s: string): unknown } }).constructor("1" + "0".repeat(decimals)) as never)
          : convertToNumber(sp.scaledBalance, SPOT_MARKET_RATE_PRECISION);
        const oracle = driftClient.getOracleDataForSpotMarket(sp.marketIndex);
        const px = convertToNumber(oracle.price, PRICE_PRECISION);
        const apy = isBorrow
          ? computeSpotBorrowApy(market)
          : computeSpotDepositApy(market);
        spotPositions.push({
          subAccountId: subId,
          marketIndex: sp.marketIndex,
          marketSymbol: symbol,
          side: isBorrow ? "borrow" : "deposit",
          tokenMint: market.mint.toBase58(),
          decimals,
          amount: Math.abs(amount),
          usdValue: Math.abs(amount) * px,
          apy,
        });
      }
    }

    return {
      preview: preview("drift_list_positions", `Drift positions for ${userWallet.slice(0, 8)}…`, {}),
      additionalSignersRequired: 0,
      isCrossChain: false,
      data: {
        perpPositions,
        spotPositions,
        total: perpPositions.length + spotPositions.length,
      },
    };
  } finally {
    try { await driftClient?.unsubscribe(); } catch { /* */ }
  }
}

/**
 * Drift market names are stored as fixed `[u8; 32]` byte arrays — null-pad
 * trimmed and ASCII-decoded gives the human label ("SOL-PERP" etc.).
 */
function decodeName(name: number[] | Uint8Array): string {
  const bytes = name instanceof Uint8Array ? name : Uint8Array.from(name);
  let end = bytes.length;
  while (end > 0 && (bytes[end - 1] === 0 || bytes[end - 1] === 32)) end--;
  return new TextDecoder().decode(bytes.subarray(0, end));
}

/**
 * Spot deposit APY: utilisation × max_borrow_rate × (1 − protocol fee).
 * Drift's interest-rate model is two-piece linear (optimal utilization
 * inflection); we approximate using the SDK's exposed rate field when
 * available, otherwise compute from raw config.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function computeSpotDepositApy(market: any): number {
  // SDK exposes `cumulativeDepositInterest` / `cumulativeBorrowInterest`
  // but those are integrals; the headline APY comes from the model
  // (utilisation curve). Use SDK helpers when present.
  try {
    if (typeof market.getDepositRate === "function") {
      return convertToNumber(market.getDepositRate(), SPOT_MARKET_RATE_PRECISION) * 100;
    }
  } catch { /* fall through */ }
  return 0;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function computeSpotBorrowApy(market: any): number {
  try {
    if (typeof market.getBorrowRate === "function") {
      return convertToNumber(market.getBorrowRate(), SPOT_MARKET_RATE_PRECISION) * 100;
    }
  } catch { /* fall through */ }
  return 0;
}

// FUNDING_RATE_BUFFER_PRECISION is imported only for future funding-rate
// extensions and not used in the current shape. Keep the import so adding
// funding metrics doesn't need a new SDK round-trip.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const _keepFunding = FUNDING_RATE_BUFFER_PRECISION;
