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
    const url = `${JUP_QUOTE}/quote?inputMint=${inputs.inputMint}&outputMint=${inputs.outputMint}&amount=${amount}&slippageBps=50`;
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

async function loadMarket(): Promise<KaminoMarket> {
  const market = await KaminoMarket.load(kitRpc() as any, MAIN_MARKET, RECENT_SLOT_DURATION_MS);
  if (!market) throw appError("Could not load Kamino market", 502, "KAMINO_ERROR");
  return market;
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
  const collMintStr = resolveToken(params.token)?.mint ?? params.token;
  const debtMintStr = resolveToken(params.debtToken ?? "USDC")?.mint ?? (params.debtToken ?? "USDC");
  const leverage = new Decimal(params.leverage || "2");
  const amount = new Decimal(params.amount);
  if (!amount.isFinite() || amount.lte(0)) throw appError("amount must be positive", 400, "INVALID_PARAMS");
  if (leverage.lte(1) || leverage.gt(10)) throw appError("leverage must be between 1 and 10", 400, "INVALID_PARAMS");

  const market = await loadMarket();
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
  const tx = await buildUnsignedV0Tx(resp.ixs as Instruction[], userWallet, altMap(resp.lookupTables));
  return {
    preview: preview("kamino_multiply_open", `Open ${leverage}x Multiply on ${params.token}`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
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
  const collMintStr = resolveToken(params.token)?.mint ?? params.token;
  const debtMintStr = resolveToken(params.debtToken ?? "USDC")?.mint ?? (params.debtToken ?? "USDC");
  const amount = new Decimal(params.amount);
  if (!amount.isFinite() || amount.lte(0)) throw appError("amount must be positive", 400, "INVALID_PARAMS");

  const market = await loadMarket();
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
  const collMintStr = resolveToken(params.token)?.mint ?? params.token;
  const debtMintStr = resolveToken(params.debtToken ?? "USDC")?.mint ?? (params.debtToken ?? "USDC");

  const market = await loadMarket();
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
