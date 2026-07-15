/**
 * Kamino Multiply — leveraged looping (@kamino-finance/klend-sdk leverage).
 *
 * Deposit a collateral and the SDK borrows a correlated token, swaps it (via a
 * Jupiter `quoter`/`swapper` we supply) to more collateral, and re-deposits —
 * looping with a flash loan to reach `targetLeverage`. Builds @solana/kit (v2)
 * instructions + ALTs; we compile an unsigned base64 v0 tx for the frontend to
 * sign. Delegated here from the Rust service.
 *
 * NOTE: leverage is high-stakes (borrowing + flash loans + liquidation risk).
 * The builder produces the tx, but correctness of a live position must be
 * validated on mainnet with a small amount before trusting it.
 */
import {
  address,
  createNoopSigner,
  none,
  AccountRole,
  type Address,
  type Instruction,
} from "@solana/kit";
import {
  KaminoMarket,
  KaminoObligation,
  getDepositWithLeverageIxs,
  getWithdrawWithLeverageIxs,
  getScopeRefreshIxForObligationAndReserves,
  getUserLutAddressAndSetupIxs,
  uniqueAccountsWithProgramIds,
  extendLookupTableIxs,
  ObligationTypeTag,
  MultiplyObligation,
} from "@kamino-finance/klend-sdk";
import { fetchAllAddressLookupTable } from "@solana-program/address-lookup-table";
import Decimal from "decimal.js/decimal";
import { v4 as uuidv4 } from "uuid";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { kitRpc, buildUnsignedV0Tx } from "../utils/kit_tx";
import { resolveToken } from "./tokens";

const MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF" as Address;
const RECENT_SLOT_DURATION_MS = 450;
const JUP_QUOTE = config.jupiterApiKey ? "https://api.jup.ag/swap/v1" : "https://lite-api.jup.ag/swap/v1";
const JUP_PRICE = "https://api.jup.ag/price/v3";
const KAMINO_API = "https://api.kamino.finance";
// api.kamino.finance blocks non-browser User-Agents with a 403.
const KAMINO_UA = { "User-Agent": "Mozilla/5.0", Accept: "application/json" };

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return { id: uuidv4(), type, description, estimatedFee: "~0.00005 SOL", params, warnings: [], requiresApproval: false };
}

function jupHeaders(): Record<string, string> {
  const h: Record<string, string> = { Accept: "application/json" };
  if (config.jupiterApiKey) h["x-api-key"] = config.jupiterApiKey;
  return h;
}

/** Convert a Jupiter (web3.js v1 JSON) instruction into an @solana/kit Instruction. */
function jupIxToKit(ix: any): Instruction {
  return {
    programAddress: address(ix.programId),
    accounts: (ix.accounts ?? []).map((a: any) => ({
      address: address(a.pubkey),
      role: a.isSigner
        ? (a.isWritable ? AccountRole.WRITABLE_SIGNER : AccountRole.READONLY_SIGNER)
        : (a.isWritable ? AccountRole.WRITABLE : AccountRole.READONLY),
    })),
    data: Uint8Array.from(Buffer.from(ix.data ?? "", "base64")),
  };
}

/** USD price of a mint from Jupiter price v3. */
async function usdPrice(mint: string): Promise<number> {
  const r = await fetch(`${JUP_PRICE}?ids=${mint}`, { headers: jupHeaders() });
  if (!r.ok) throw appError("Could not fetch token price", 502, "PRICE_ERROR");
  const j: any = await r.json();
  const p = parseFloat(j?.[mint]?.usdPrice ?? "0");
  if (!p) throw appError("No price for token", 502, "PRICE_ERROR");
  return p;
}

/** Jupiter-backed quoter for the leverage swap (price of input in output units). */
function makeQuoter(inDecimals: (m: string) => number) {
  return async (inputs: any, _klendAccounts: Address[]) => {
    const amount = inputs.inputAmountLamports.floor().toString();
    // The leverage tx bundles Kamino flash-loan + deposit + this swap into ONE
    // v0 transaction that must fit Solana's 1232-byte limit. A default (multi-hop)
    // Jupiter route adds too many accounts and overflows, so constrain the route:
    // cap accounts and only hop through major tokens (which sit in shared ALTs).
    const url = `${JUP_QUOTE}/quote?inputMint=${inputs.inputMint}&outputMint=${inputs.outputMint}&amount=${amount}&slippageBps=50&maxAccounts=20&restrictIntermediateTokens=true`;
    const r = await fetch(url, { headers: jupHeaders() });
    if (!r.ok) throw appError("Jupiter quote failed for leverage swap", 502, "SWAP_ERROR");
    const q: any = await r.json();
    const inAmt = new Decimal(q.inAmount).div(new Decimal(10).pow(inDecimals(String(inputs.inputMint))));
    const outAmt = new Decimal(q.outAmount).div(new Decimal(10).pow(inDecimals(String(inputs.outputMint))));
    return { priceAInB: inAmt.gt(0) ? outAmt.div(inAmt) : new Decimal(0), quoteResponse: q };
  };
}

/** Jupiter-backed swapper — returns the swap ixs + Jupiter ALTs for the leverage tx. */
function makeSwapper(owner: string) {
  return async (_inputs: any, _klendAccounts: Address[], quote: any) => {
    const r = await fetch(`${JUP_QUOTE}/swap-instructions`, {
      method: "POST",
      headers: { ...jupHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({
        quoteResponse: quote.quoteResponse,
        userPublicKey: owner,
        wrapAndUnwrapSol: true,
      }),
    });
    if (!r.ok) throw appError("Jupiter swap-instructions failed", 502, "SWAP_ERROR");
    const j: any = await r.json();
    const setup = (j.setupInstructions ?? []).map(jupIxToKit);
    const swapIx = jupIxToKit(j.swapInstruction);
    const altAddrs: Address[] = (j.addressLookupTableAddresses ?? []).map((a: string) => address(a));
    const lookupTables = altAddrs.length ? await fetchAllAddressLookupTable(kitRpc() as any, altAddrs) : [];
    return [{ preActionIxs: setup, swapIxs: [swapIx], lookupTables, quote }];
  };
}

/** Map the leverage response's ALT accounts → the { altAddress: addresses[] } shape our tx helper wants. */
function altMap(lookupTables: any[]): Record<string, string[]> {
  const map: Record<string, string[]> = {};
  for (const lut of lookupTables ?? []) {
    const addr = String(lut.address);
    const addrs = (lut.data?.addresses ?? lut.addresses ?? []).map(String);
    if (addr && addrs.length) map[addr] = addrs;
  }
  return map;
}

/** A leveraged Kamino deposit references ~50 accounts and can't fit Solana's
 *  1232-byte tx limit unless the user's per-position lookup table (LUT)
 *  compresses them. This resolves that LUT for a set of deposit instructions:
 *   - `lutAddress` / `lutAddresses` — the LUT and the full address set to
 *     compress against (existing on-chain contents ∪ everything the deposit
 *     touches that a Jupiter ALT doesn't already cover);
 *   - `setupIxBatches` — the create/init + extend transactions the user must
 *     land (and let warm up) BEFORE the deposit. Empty when the LUT already
 *     holds every needed account (a returning multiply user), giving a 1-tx flow.
 *
 *  The Kamino SDK's own extend only adds ~10 position-specific addresses, which
 *  isn't enough — so we extend with the deposit's complete account set instead. */
async function resolveUserMultiplyLut(
  market: KaminoMarket,
  owner: ReturnType<typeof createNoopSigner>,
  userWallet: string,
  depositIxs: Instruction[],
  jupAltAddresses: string[],
): Promise<{ lutAddress: string; lutAddresses: string[]; setupIxBatches: Instruction[][] }> {
  // create + init the user metadata/LUT if needed (no auto-extend — we do our own).
  const [lutAddr, initBatches] = await getUserLutAddressAndSetupIxs(market, owner, none(), false, []);
  const lutAddress = String(lutAddr);

  // Every account the deposit touches that a Jupiter ALT doesn't already cover.
  const jupAlts = jupAltAddresses.map((a) => address(a));
  const needed = uniqueAccountsWithProgramIds(depositIxs, jupAlts)
    .map(String)
    .filter((a) => a !== userWallet); // fee payer can't be compressed

  // What the LUT already holds on-chain, so we only extend with what's missing.
  const onchain = new Set<string>();
  try {
    const [existing] = await fetchAllAddressLookupTable(kitRpc() as any, [lutAddr]);
    for (const a of ((existing as any)?.data?.addresses ?? [])) onchain.add(String(a));
  } catch { /* LUT not created yet */ }

  const toAdd = needed.filter((a) => !onchain.has(a));
  const extendIxs = toAdd.length ? extendLookupTableIxs(owner, lutAddr, toAdd.map((a) => address(a)), owner) : [];

  const setupIxBatches: Instruction[][] = [
    ...initBatches.filter((b) => b.length > 0), // create + init metadata (if new)
    ...extendIxs.map((ix) => [ix]),              // one extend chunk per tx
  ];

  return {
    lutAddress,
    lutAddresses: [...new Set([...onchain, ...needed])],
    setupIxBatches,
  };
}

async function loadMarket(): Promise<KaminoMarket> {
  const market = await KaminoMarket.load(kitRpc() as any, MAIN_MARKET, RECENT_SLOT_DURATION_MS);
  if (!market) throw appError("Could not load Kamino market", 502, "KAMINO_ERROR");
  return market;
}

const BASE58_MINT_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

/** Resolve a collateral/debt token param (mint OR symbol) to a mint address.
 *  Tries, in order: an already-valid base58 mint, the static token map
 *  (SOL/USDC/…), then the market's own reserve list — so LSTs and newer tokens
 *  that the static map doesn't know (cbBTC, PYUSD, hubSOL…) still resolve
 *  instead of failing later as an invalid base58 address. */
function resolveMint(market: KaminoMarket, tokenStr: string): string {
  if (!tokenStr) return tokenStr;
  if (BASE58_MINT_RE.test(tokenStr)) return tokenStr;
  const mapped = resolveToken(tokenStr)?.mint;
  if (mapped) return mapped;
  const reserve = market.getReserveBySymbol(tokenStr);
  return reserve ? reserve.getLiquidityMint().toString() : tokenStr;
}

function reserveDecimals(market: KaminoMarket): (m: string) => number {
  return (mint: string) => {
    try {
      const r = market.getReserveByMint(address(mint));
      return r ? Number(r.stats.decimals) : 9;
    } catch {
      return 9;
    }
  };
}

/**
 * Load the user's existing Multiply obligation for a coll/debt pair, plus the
 * coll/debt reserves and their current deposited/borrowed lamport amounts.
 * Throws a clean error if the position doesn't exist.
 */
async function loadMultiplyPosition(
  market: KaminoMarket,
  userWallet: string,
  collMintStr: string,
  debtMintStr: string,
): Promise<{ obligation: KaminoObligation; deposited: Decimal; borrowed: Decimal }> {
  const collReserve = market.getReserveByMint(address(collMintStr));
  const debtReserve = market.getReserveByMint(address(debtMintStr));
  if (!collReserve || !debtReserve) throw appError("Token not supported on the Kamino main market", 400, "UNSUPPORTED");
  const obligationType = new MultiplyObligation(address(collMintStr), address(debtMintStr), market.programId);
  const obligation = await market.getObligationByWallet(address(userWallet), obligationType);
  if (!obligation) {
    throw appError("No open Multiply position found for this token pair", 400, "NO_POSITION");
  }
  const deposited = obligation.getDepositAmountByReserve(collReserve);
  const borrowed = obligation.getBorrowAmountByReserve(debtReserve);
  return { obligation, deposited, borrowed };
}

export interface KaminoMultiplyOpenParams {
  token: string; // collateral token symbol/mint
  amount: string; // collateral amount (token units)
  leverage: string; // target leverage, e.g. "3"
  debtToken?: string; // borrowed token; defaults to USDC
  slippagePct?: string;
}

/** Open a leveraged Multiply position. */
export async function buildKaminoMultiplyOpen(params: KaminoMultiplyOpenParams, userWallet: string): Promise<BuildResponse> {
  const leverage = new Decimal(params.leverage || "2");
  const amount = new Decimal(params.amount);
  if (!amount.isFinite() || amount.lte(0)) throw appError("amount must be positive", 400, "INVALID_PARAMS");
  if (leverage.lte(1) || leverage.gt(10)) throw appError("leverage must be between 1 and 10", 400, "INVALID_PARAMS");

  const market = await loadMarket();
  const collMintStr = resolveMint(market, params.token);
  const debtMintStr = resolveMint(market, params.debtToken ?? "USDC");
  const collReserve = market.getReserveByMint(address(collMintStr));
  const debtReserve = market.getReserveByMint(address(debtMintStr));
  if (!collReserve || !debtReserve) throw appError("Token not supported on the Kamino main market", 400, "UNSUPPORTED");

  const owner = createNoopSigner(address(userWallet));
  const decOf = reserveDecimals(market);

  // priceDebtToColl = how many coll tokens one debt token is worth.
  const [collUsd, debtUsd] = await Promise.all([usdPrice(collMintStr), usdPrice(debtMintStr)]);
  const priceDebtToColl = new Decimal(debtUsd).div(collUsd);

  const currentSlot = await (kitRpc() as any).getSlot().send();
  const scopeRefreshIx = await getScopeRefreshIxForObligationAndReserves(market, collReserve, debtReserve, undefined, undefined);

  const responses = await getDepositWithLeverageIxs({
    owner,
    kaminoMarket: market,
    debtTokenMint: address(debtMintStr),
    collTokenMint: address(collMintStr),
    depositAmount: amount,
    priceDebtToColl,
    targetLeverage: leverage,
    slippagePct: new Decimal(params.slippagePct || "0.5"),
    selectedTokenMint: address(collMintStr),
    obligation: null,
    obligationTypeTagOverride: ObligationTypeTag.Multiply,
    referrer: none(),
    currentSlot,
    scopeRefreshIx,
    quoteBufferBps: new Decimal(100),
    quoter: makeQuoter(decOf),
    swapper: makeSwapper(userWallet),
    useV2Ixs: true,
  } as any);

  if (!responses || responses.length === 0) throw appError("Could not build the Multiply position", 502, "KAMINO_ERROR");
  if (responses.length > 1) {
    throw appError("This Multiply position needs multiple transactions, which isn't supported yet — try a smaller amount or lower leverage", 400, "MULTI_TX");
  }
  const resp = responses[0];

  // Compress the deposit against the user's per-position LUT so it fits the
  // 1232-byte limit; a first-time user must land the LUT setup tx(s) first.
  const alts = altMap(resp.lookupTables);
  const { lutAddress, lutAddresses, setupIxBatches } = await resolveUserMultiplyLut(
    market, owner, userWallet, resp.ixs as Instruction[], Object.keys(alts),
  );
  if (lutAddresses.length) alts[lutAddress] = lutAddresses;
  const depositTx = await buildUnsignedV0Tx(resp.ixs as Instruction[], userWallet, alts);

  const pv = preview("kamino_multiply_open", `Open ${leverage}x Multiply on ${params.token}`, params as unknown as Record<string, unknown>);
  if (setupIxBatches.length > 0) {
    // New user: create/extend the LUT first (each batch is one tx; the LUT must
    // confirm + warm up before the deposit can reference it), then deposit.
    const setupTxs: string[] = [];
    for (const batch of setupIxBatches) setupTxs.push(await buildUnsignedV0Tx(batch, userWallet, {}));
    const transactions = [...setupTxs, depositTx];
    return {
      preview: pv,
      transaction: depositTx,
      transactions,
      actionTxIndex: transactions.length - 1,
      additionalSignersRequired: 0,
      isCrossChain: false,
    };
  }
  return { preview: pv, transaction: depositTx, additionalSignersRequired: 0, isCrossChain: false };
}

export interface KaminoMultiplyAddParams {
  token: string; // collateral token symbol/mint
  amount: string; // additional collateral to deposit
  leverage?: string; // target leverage to maintain; defaults to the position's current leverage
  debtToken?: string;
  slippagePct?: string;
}

/** Add collateral to an existing Multiply position (re-loops to the target leverage). */
export async function buildKaminoMultiplyAdd(params: KaminoMultiplyAddParams, userWallet: string): Promise<BuildResponse> {
  const amount = new Decimal(params.amount);
  if (!amount.isFinite() || amount.lte(0)) throw appError("amount must be positive", 400, "INVALID_PARAMS");

  const market = await loadMarket();
  const collMintStr = resolveMint(market, params.token);
  const debtMintStr = resolveMint(market, params.debtToken ?? "USDC");
  const collReserve = market.getReserveByMint(address(collMintStr));
  const debtReserve = market.getReserveByMint(address(debtMintStr));
  if (!collReserve || !debtReserve) throw appError("Token not supported on the Kamino main market", 400, "UNSUPPORTED");

  const { obligation } = await loadMultiplyPosition(market, userWallet, collMintStr, debtMintStr);
  const targetLeverage = params.leverage ? new Decimal(params.leverage) : obligation.refreshedStats.leverage;
  if (!targetLeverage.isFinite() || targetLeverage.lte(1)) throw appError("Could not determine target leverage", 400, "INVALID_PARAMS");

  const owner = createNoopSigner(address(userWallet));
  const decOf = reserveDecimals(market);
  const [collUsd, debtUsd] = await Promise.all([usdPrice(collMintStr), usdPrice(debtMintStr)]);
  const priceDebtToColl = new Decimal(debtUsd).div(collUsd);
  const currentSlot = await (kitRpc() as any).getSlot().send();
  const scopeRefreshIx = await getScopeRefreshIxForObligationAndReserves(market, collReserve, debtReserve, obligation, undefined);

  const responses = await getDepositWithLeverageIxs({
    owner,
    kaminoMarket: market,
    debtTokenMint: address(debtMintStr),
    collTokenMint: address(collMintStr),
    depositAmount: amount,
    priceDebtToColl,
    targetLeverage,
    slippagePct: new Decimal(params.slippagePct || "0.5"),
    selectedTokenMint: address(collMintStr),
    obligation,
    obligationTypeTagOverride: ObligationTypeTag.Multiply,
    referrer: none(),
    currentSlot,
    scopeRefreshIx,
    quoteBufferBps: new Decimal(100),
    quoter: makeQuoter(decOf),
    swapper: makeSwapper(userWallet),
    useV2Ixs: true,
  } as any);

  if (!responses || responses.length === 0) throw appError("Could not build the Multiply deposit", 502, "KAMINO_ERROR");
  if (responses.length > 1) throw appError("This action needs multiple transactions, which isn't supported yet — try a smaller amount", 400, "MULTI_TX");
  const resp = responses[0];
  const tx = await buildUnsignedV0Tx(resp.ixs as Instruction[], userWallet, altMap(resp.lookupTables));
  return {
    preview: preview("kamino_multiply_add", `Add ${params.amount} ${params.token} to Multiply`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export interface KaminoMultiplyWithdrawParams {
  token: string; // collateral token symbol/mint
  amount?: string; // collateral to withdraw; omitted/"all" closes the position
  debtToken?: string;
  slippagePct?: string;
}

/** Shared withdraw/close builder. `close=true` unwinds the whole position. */
async function buildMultiplyWithdrawInternal(
  params: KaminoMultiplyWithdrawParams,
  userWallet: string,
  close: boolean,
): Promise<BuildResponse> {
  const market = await loadMarket();
  const collMintStr = resolveMint(market, params.token);
  const debtMintStr = resolveMint(market, params.debtToken ?? "USDC");
  const collReserve = market.getReserveByMint(address(collMintStr));
  const debtReserve = market.getReserveByMint(address(debtMintStr));
  if (!collReserve || !debtReserve) throw appError("Token not supported on the Kamino main market", 400, "UNSUPPORTED");

  const { obligation, deposited, borrowed } = await loadMultiplyPosition(market, userWallet, collMintStr, debtMintStr);

  const raw = (params.amount ?? "").trim().toLowerCase();
  const isClosingPosition = close || raw === "" || raw === "all" || raw === "max" || raw === "full";
  const withdrawAmount = isClosingPosition ? deposited : new Decimal(params.amount as string);
  if (!withdrawAmount.isFinite() || withdrawAmount.lte(0)) throw appError("Nothing to withdraw from this position", 400, "NOTHING_TO_WITHDRAW");

  const owner = createNoopSigner(address(userWallet));
  const decOf = reserveDecimals(market);
  const [collUsd, debtUsd] = await Promise.all([usdPrice(collMintStr), usdPrice(debtMintStr)]);
  const priceCollToDebt = new Decimal(collUsd).div(debtUsd);
  const currentSlot = await (kitRpc() as any).getSlot().send();
  const scopeRefreshIx = await getScopeRefreshIxForObligationAndReserves(market, collReserve, debtReserve, obligation, undefined);

  const responses = await getWithdrawWithLeverageIxs({
    owner,
    kaminoMarket: market,
    debtTokenMint: address(debtMintStr),
    collTokenMint: address(collMintStr),
    obligation,
    deposited,
    borrowed,
    withdrawAmount,
    priceCollToDebt,
    slippagePct: new Decimal(params.slippagePct || "0.5"),
    isClosingPosition,
    selectedTokenMint: address(collMintStr),
    referrer: none(),
    currentSlot,
    scopeRefreshIx,
    quoteBufferBps: new Decimal(100),
    quoter: makeQuoter(decOf),
    swapper: makeSwapper(userWallet),
    useV2Ixs: true,
    userSolBalanceLamports: 0,
  } as any);

  if (!responses || responses.length === 0) throw appError("Could not build the Multiply withdrawal", 502, "KAMINO_ERROR");
  if (responses.length > 1) throw appError("This action needs multiple transactions, which isn't supported yet — try a smaller amount", 400, "MULTI_TX");
  const resp = responses[0];
  const tx = await buildUnsignedV0Tx(resp.ixs as Instruction[], userWallet, altMap(resp.lookupTables));
  const type = close ? "kamino_multiply_close" : "kamino_multiply_withdraw";
  const desc = close ? `Close Multiply position on ${params.token}` : `Withdraw ${params.amount} ${params.token} from Multiply`;
  return {
    preview: preview(type, desc, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

/** Withdraw part of the collateral from a Multiply position (partial deleverage). */
export async function buildKaminoMultiplyWithdraw(params: KaminoMultiplyWithdrawParams, userWallet: string): Promise<BuildResponse> {
  return buildMultiplyWithdrawInternal(params, userWallet, false);
}

/** Fully unwind (close) a Multiply position — repays the debt and returns the collateral. */
export async function buildKaminoMultiplyClose(params: KaminoMultiplyWithdrawParams, userWallet: string): Promise<BuildResponse> {
  return buildMultiplyWithdrawInternal(params, userWallet, true);
}

// ── Multiply market discovery (read-only query) ───────────────────────────────

interface KaminoLeverageMetric {
  depositReserve: string;
  borrowReserve: string;
  tag: string; // "Multiply" | "Leverage"
  tvl?: string;
  avgLeverage?: string;
  totalDepositedUsd?: string;
  totalBorrowedUsd?: string;
}
interface KaminoReserveMetric {
  reserve: string;
  liquidityToken: string; // symbol
  liquidityTokenMint: string;
  maxLtv?: string;
  borrowApy?: string;
  supplyApy?: string;
  totalSupplyUsd?: string;
}

async function kaminoGet<T>(path: string): Promise<T> {
  const r = await fetch(`${KAMINO_API}${path}`, { headers: KAMINO_UA });
  if (!r.ok) throw appError("Could not load Kamino market data", 502, "KAMINO_ERROR");
  return (await r.json()) as T;
}

export interface KaminoMultiplyMarketsParams {
  token?: string; // filter to pairs whose collateral OR debt symbol matches
  limit?: string; // default 8, max 30
  sort?: string; // "apy" (default) | "tvl" | "leverage"
}

interface MultiplyMarketRow {
  collToken: string; collMint: string; debtToken: string; debtMint: string;
  maxLeverage: number; maxLtvPct: number; estMaxApyPct: number; avgLeverage: number;
  tvlUsd: number; liquidityUsd: number; collSupplyApyPct: number; debtBorrowApyPct: number;
}

/**
 * List the Main-Market Kamino Multiply pools with real metrics: exact max
 * leverage (SDK `getMaxLeverageForPair`, which honours elevation groups), an
 * estimated net APY at max leverage from live on-chain rates, TVL, average
 * leverage actually taken, and available debt liquidity. Sorted (default by
 * estimated APY), filterable by token. Read-only — returns `data`, no tx.
 */
export async function getKaminoMultiplyMarkets(params: KaminoMultiplyMarketsParams): Promise<BuildResponse> {
  const token = params.token?.trim().toUpperCase();
  // Card fetches the full set (limit≈100) and sorts/filters/paginates
  // client-side; a text answer uses a small limit. Cap at 100 (>57 pools).
  const limit = Math.max(1, Math.min(parseInt(params.limit ?? "8", 10) || 8, 100));
  const sort = (params.sort ?? "apy").trim().toLowerCase();

  // Live REST datasets (parallel): per-market leverage metrics, per-reserve
  // metrics, and collateral staking yields (LST/yield-bearing tokens).
  const [levMetrics, reserveMetrics, stakingYields, market] = await Promise.all([
    kaminoGet<KaminoLeverageMetric[]>(`/kamino-market/${MAIN_MARKET}/leverage/metrics`),
    kaminoGet<KaminoReserveMetric[]>(`/kamino-market/${MAIN_MARKET}/reserves/metrics`),
    kaminoGet<{ apy: string; tokenMint: string }[]>(`/v2/staking-yields`),
    loadMarket(),
  ]);

  const byReserve = new Map<string, KaminoReserveMetric>();
  for (const r of reserveMetrics) byReserve.set(r.reserve, r);
  const stakingByMint = new Map<string, number>();
  for (const s of stakingYields) stakingByMint.set(s.tokenMint, parseFloat(s.apy ?? "0") || 0);

  const rows: MultiplyMarketRow[] = [];
  for (const m of levMetrics) {
    if (m.tag !== "Multiply") continue;
    const coll = byReserve.get(m.depositReserve);
    const debt = byReserve.get(m.borrowReserve);
    if (!coll || !debt) continue; // reserve not on the main market — skip

    // Exact max leverage for the pair (elevation-group aware). maxLtv is the
    // consistent inverse (maxLev = 1/(1-maxLtv)).
    let maxLeverage = 0;
    try {
      maxLeverage = market.getMaxLeverageForPair(address(coll.liquidityTokenMint), address(debt.liquidityTokenMint));
    } catch {
      maxLeverage = 0;
    }
    if (!maxLeverage || !isFinite(maxLeverage) || maxLeverage <= 1) continue;
    const maxLtv = 1 - 1 / maxLeverage;

    // Estimated net APY at max leverage from live rates:
    //   collYield = collateral supply APY + collateral staking yield
    //   netApy    = L·collYield − (L−1)·debtBorrowApy
    // Clearly an ESTIMATE (fees/slippage/rate drift excluded), not Kamino's
    // exact displayed figure — labelled as such on the card.
    const collYield = (parseFloat(coll.supplyApy ?? "0") || 0) + (stakingByMint.get(coll.liquidityTokenMint) ?? 0);
    const debtBorrow = parseFloat(debt.borrowApy ?? "0") || 0;
    const estMaxApyPct = (maxLeverage * collYield - (maxLeverage - 1) * debtBorrow) * 100;

    rows.push({
      collToken: coll.liquidityToken,
      collMint: coll.liquidityTokenMint,
      debtToken: debt.liquidityToken,
      debtMint: debt.liquidityTokenMint,
      maxLeverage: Math.round(maxLeverage * 100) / 100,
      maxLtvPct: Math.round(maxLtv * 1000) / 10,
      estMaxApyPct: Math.round(estMaxApyPct * 100) / 100,
      avgLeverage: Math.round((parseFloat(m.avgLeverage ?? "0") || 0) * 100) / 100,
      tvlUsd: parseFloat(m.totalDepositedUsd ?? m.tvl ?? "0") || 0,
      liquidityUsd: parseFloat(debt.totalSupplyUsd ?? "0") || 0,
      collSupplyApyPct: Math.round(collYield * 10000) / 100,
      debtBorrowApyPct: Math.round(debtBorrow * 10000) / 100,
    });
  }

  let list = rows;
  if (token) list = list.filter((r) => r.collToken.toUpperCase() === token || r.debtToken.toUpperCase() === token);
  const cmp: Record<string, (a: MultiplyMarketRow, b: MultiplyMarketRow) => number> = {
    tvl: (a, b) => b.tvlUsd - a.tvlUsd,
    leverage: (a, b) => b.maxLeverage - a.maxLeverage,
    apy: (a, b) => b.estMaxApyPct - a.estMaxApyPct,
  };
  list.sort(cmp[sort] ?? cmp.apy);
  const total = list.length;
  list = list.slice(0, limit);

  return {
    preview: preview("kamino_multiply_markets", "Kamino Multiply pools", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { markets: list, total, market: "main", sortedBy: sort in cmp ? sort : "apy" },
  } as BuildResponse;
}
