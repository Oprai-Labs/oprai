/**
 * Raydium liquidity + position transactions built with @raydium-io/raydium-sdk-v2.
 *
 * WHY THIS EXISTS: Raydium's transaction-v1 REST API only serves SWAPS
 * (/transaction/swap-base-in|out). There is NO REST endpoint for add/remove
 * liquidity, CLMM positions, or pool creation — the old code POSTed to
 * /transaction/add-liquidity etc. which 404 ("Cannot POST …"), producing the
 * "error decoding response body" failures. Those operations can only be built
 * with the SDK, which composes the instructions client-side.
 *
 * The server holds NO private key: we load the SDK with the user's PublicKey as
 * `owner` (build-only) and return an UNSIGNED base64 v0 transaction for the
 * browser wallet to sign. Never call `.execute()` here.
 *
 * SDK types are accessed loosely (`as any`) — same approach as orca.ts — to
 * avoid the heavy generic friction of the SDK's declaration files; correctness
 * is enforced at runtime + by the on-chain program.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */

import { Connection, PublicKey, VersionedTransaction } from "@solana/web3.js";
import BN from "bn.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken, symbolForMint } from "./tokens";
import { v4 as uuidv4 } from "uuid";

// ─────────────────────────────────────────────────────────────────────────────
// Shared SDK plumbing
// ─────────────────────────────────────────────────────────────────────────────

/** Load the Raydium SDK bound to the user's wallet as a build-only owner. */
async function loadRaydium(userWallet: string): Promise<{ sdk: any; raydium: any; owner: PublicKey }> {
  const sdk: any = await import("@raydium-io/raydium-sdk-v2");
  const connection = new Connection(config.solanaRpc, "confirmed");
  const owner = new PublicKey(userWallet);
  const raydium = await sdk.Raydium.load({
    connection,
    cluster: "mainnet",
    owner, // PublicKey → build-only, no signing
    disableLoadToken: true, // skip the token-list fetch — we resolve mints ourselves
    blockhashCommitment: "confirmed",
  });
  return { sdk, raydium, owner };
}

/** Serialize an unsigned v0 transaction to base64 for the browser to sign. */
function serializeV0(tx: VersionedTransaction): string {
  if (!tx) throw appError("Raydium SDK returned no transaction", 502, "RAYDIUM_ERROR");
  return Buffer.from(tx.serialize()).toString("base64");
}

function preview(type: string, description: string, params: Record<string, unknown>, warnings: string[] = []): ActionPreview {
  return {
    id: uuidv4(),
    type,
    description,
    estimatedFee: "~0.01 SOL",
    params,
    warnings,
    requiresApproval: true,
  };
}

function resp(type: string, tx: VersionedTransaction, params: Record<string, unknown>, description: string, warnings?: string[]): BuildResponse {
  return {
    preview: preview(type, description, params, warnings),
    transaction: serializeV0(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

/**
 * Map a raw SDK/RPC error to a clean, user-safe message. Never leak the raw SDK
 * body (token-account dumps, internal strings). Our own appErrors (they carry a
 * statusCode) pass through untouched.
 */
function mapRaydiumSdkError(e: any): never {
  if (e && typeof e.statusCode === "number") throw e; // already a clean appError
  // Log the raw SDK error server-side for diagnosis; the user only ever sees the
  // clean mapped message below (never the raw token-account dumps / internals).
  try { console.error("[raydium-sdk-raw]", e?.stack || e?.message || e); } catch { /* noop */ }
  const msg = String(e?.message ?? e ?? "");
  const low = msg.toLowerCase();
  if (low.includes("lptokenaccount") || (low.includes("lp") && low.includes("cannot found"))) {
    throw appError("You don't have a liquidity position in this pool.", 400, "RAYDIUM_NO_POSITION");
  }
  if ((low.includes("cannot found") && low.includes("tokenaccount")) ||
      (low.includes("token account") && (low.includes("don't has") || low.includes("dont has") || low.includes("some")))) {
    throw appError("Your wallet doesn't hold the tokens this action needs. Fund it first.", 400, "RAYDIUM_NO_TOKEN");
  }
  if (low.includes("insufficient")) {
    throw appError("Insufficient balance for this action.", 400, "RAYDIUM_INSUFFICIENT");
  }
  if ((low.includes("position") || low.includes("nft")) && (low.includes("not found") || low.includes("cannot found"))) {
    throw appError("That position wasn't found for your wallet.", 404, "RAYDIUM_NO_POSITION");
  }
  throw appError("Couldn't build the Raydium transaction. Please try again in a moment.", 502, "RAYDIUM_ERROR");
}

type PoolKind = "cpmm" | "ammv4" | "clmm" | "unknown";

/** Classify a Standard/Concentrated pool by its owning program. */
function classifyPool(sdk: any, info: any): PoolKind {
  const prog = String(info?.programId ?? "");
  const CPMM = sdk.CREATE_CPMM_POOL_PROGRAM?.toBase58?.();
  const AMM_V4 = sdk.AMM_V4?.toBase58?.();
  const AMM_STABLE = sdk.AMM_STABLE?.toBase58?.();
  const CLMM = sdk.CLMM_PROGRAM_ID?.toBase58?.();
  if (CPMM && prog === CPMM) return "cpmm";
  if ((AMM_V4 && prog === AMM_V4) || (AMM_STABLE && prog === AMM_STABLE)) return "ammv4";
  if (CLMM && prog === CLMM) return "clmm";
  // Fall back to the API type flag if the program isn't one we know.
  if (String(info?.type ?? "").toLowerCase() === "concentrated") return "clmm";
  if (String(info?.type ?? "").toLowerCase() === "standard") {
    return info?.config && !info?.marketId ? "cpmm" : "ammv4";
  }
  return "unknown";
}

/** Fetch the API pool record (carries programId, type, mintA/mintB metadata). */
async function fetchApiPool(raydium: any, poolId: string): Promise<any> {
  const list = await raydium.api.fetchPoolById({ ids: poolId });
  const info = Array.isArray(list) ? list[0] : list;
  if (!info) throw appError("Raydium pool not found for that ID", 404, "RAYDIUM_ERROR");
  return info;
}

/** Human symbol for a mint (getPoolInfoFromRpc leaves symbols blank with
 *  disableLoadToken:true, so resolve from our registry, else a short address). */
function symOf(mint: string): string {
  return symbolForMint(mint) ?? resolveToken(mint)?.name ?? (mint ? mint.slice(0, 4) + "…" : "?");
}

function toBaseUnits(amountUi: string, decimals: number): BN {
  const n = parseFloat(amountUi);
  if (!Number.isFinite(n) || n <= 0) throw appError("Amount must be a positive number", 400, "RAYDIUM_ERROR");
  // Avoid float dust: scale with a fixed-point string.
  const [whole, frac = ""] = String(amountUi).replace(/,/g, ".").split(".");
  const fracPadded = (frac + "0".repeat(decimals)).slice(0, decimals);
  return new BN((whole || "0") + fracPadded).add(new BN(0));
}

// ─────────────────────────────────────────────────────────────────────────────
// Add liquidity (Standard AMM v4 + CPMM) — single-sided input
// Frontend params: { poolId, amount (UI), inputMint, slippageBps, baseIn? }
// ─────────────────────────────────────────────────────────────────────────────

export interface RaydiumSdkAddLiquidityParams {
  poolId: string;
  amount: string;
  inputMint: string;
  slippageBps?: number;
  baseIn?: boolean;
}

export async function buildRaydiumAddLiquiditySdk(
  params: RaydiumSdkAddLiquidityParams,
  userWallet: string,
): Promise<BuildResponse> {
 try {
  if (!params.poolId) throw appError("poolId is required", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const slippageBps = params.slippageBps ?? 100;
  const slippage = new sdk.Percent(slippageBps, 10000);

  const apiInfo = await fetchApiPool(raydium, params.poolId);
  const kind = classifyPool(sdk, apiInfo);

  const inputMint = resolveToken(params.inputMint)?.mint ?? params.inputMint;
  const isA = inputMint === apiInfo.mintA.address;
  const inDecimals = isA ? apiInfo.mintA.decimals : apiInfo.mintB.decimals;
  const inputBase = toBaseUnits(params.amount, inDecimals);
  const pairDesc = `${apiInfo.mintA.symbol}/${apiInfo.mintB.symbol}`;

  if (kind === "cpmm") {
    const { poolInfo, poolKeys } = await raydium.cpmm.getPoolInfoFromRpc(params.poolId);
    const { transaction } = await raydium.cpmm.addLiquidity({
      poolInfo,
      poolKeys,
      inputAmount: inputBase,
      baseIn: isA,
      slippage,
      txVersion: sdk.TxVersion.V0,
    });
    return resp(
      "raydium_add_liquidity", transaction, params as any,
      `Add ${params.amount} ${isA ? apiInfo.mintA.symbol : apiInfo.mintB.symbol} liquidity to Raydium ${pairDesc}`,
      ["Impermanent loss risk — prices may diverge from entry"],
    );
  }

  if (kind === "ammv4") {
    const { poolInfo, poolKeys } = await raydium.liquidity.getPoolInfoFromRpc({ poolId: params.poolId });
    // Compute the paired amount for the fixed input side.
    const r = raydium.liquidity.computePairAmount({
      poolInfo,
      amount: params.amount,
      slippage,
      baseIn: isA,
    });
    // Build the input-side TokenAmount from the pool's mint metadata.
    const mkToken = (m: any) => new sdk.Token({ mint: m.address, decimals: m.decimals, symbol: m.symbol, name: m.name });
    const inputToken = mkToken(isA ? poolInfo.mintA : poolInfo.mintB);
    const inputTokenAmount = new sdk.TokenAmount(inputToken, inputBase);
    const { transaction } = await raydium.liquidity.addLiquidity({
      poolInfo,
      poolKeys,
      amountInA: isA ? inputTokenAmount : r.maxAnotherAmount,
      amountInB: isA ? r.maxAnotherAmount : inputTokenAmount,
      otherAmountMin: r.minAnotherAmount,
      fixedSide: isA ? "a" : "b",
      txVersion: sdk.TxVersion.V0,
    });
    return resp(
      "raydium_add_liquidity", transaction, params as any,
      `Add ${params.amount} ${isA ? apiInfo.mintA.symbol : apiInfo.mintB.symbol} liquidity to Raydium ${pairDesc}`,
      ["Impermanent loss risk — prices may diverge from entry"],
    );
  }

  if (kind === "clmm") {
    throw appError(
      "That's a concentrated-liquidity (CLMM) pool — open a range position instead of a standard deposit.",
      400, "RAYDIUM_ERROR",
    );
  }
  throw appError("Unsupported Raydium pool type for add-liquidity", 400, "RAYDIUM_ERROR");
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// Remove liquidity (Standard AMM v4 + CPMM)
// Frontend params: { poolId, lpAmount (UI), slippageBps }
// ─────────────────────────────────────────────────────────────────────────────

export interface RaydiumSdkRemoveLiquidityParams {
  poolId: string;
  lpAmount: string;
  slippageBps?: number;
}

export async function buildRaydiumRemoveLiquiditySdk(
  params: RaydiumSdkRemoveLiquidityParams,
  userWallet: string,
): Promise<BuildResponse> {
 try {
  if (!params.poolId) throw appError("poolId is required", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const slippageBps = params.slippageBps ?? 100;
  const slippage = new sdk.Percent(slippageBps, 10000);

  const apiInfo = await fetchApiPool(raydium, params.poolId);
  const kind = classifyPool(sdk, apiInfo);
  const pairDesc = `${apiInfo.mintA.symbol}/${apiInfo.mintB.symbol}`;

  if (kind === "cpmm") {
    const { poolInfo, poolKeys } = await raydium.cpmm.getPoolInfoFromRpc(params.poolId);
    const lpDecimals = poolInfo.lpMint?.decimals ?? apiInfo.lpMint?.decimals ?? 9;
    const lpAmount = toBaseUnits(params.lpAmount, lpDecimals);
    const { transaction } = await raydium.cpmm.withdrawLiquidity({
      poolInfo,
      poolKeys,
      lpAmount,
      slippage,
      txVersion: sdk.TxVersion.V0,
    });
    return resp("raydium_remove_liquidity", transaction, params as any, `Remove liquidity from Raydium ${pairDesc}`);
  }

  if (kind === "ammv4") {
    const { poolInfo, poolKeys } = await raydium.liquidity.getPoolInfoFromRpc({ poolId: params.poolId });
    // AMM v4 LP tokens are 9 decimals on classic pools; use the pool's if present.
    const lpDecimals = poolInfo.lpMint?.decimals ?? 9;
    const lpAmount = toBaseUnits(params.lpAmount, lpDecimals);
    void slippage; // AMM v4 removal enforces no per-side minimum here (see warning)
    const { transaction } = await raydium.liquidity.removeLiquidity({
      poolInfo,
      poolKeys,
      lpAmount,
      baseAmountMin: new BN(0),
      quoteAmountMin: new BN(0),
      txVersion: sdk.TxVersion.V0,
    });
    return resp(
      "raydium_remove_liquidity", transaction, params as any,
      `Remove liquidity from Raydium ${pairDesc}`,
      ["Minimum received not enforced — remove during stable prices"],
    );
  }

  if (kind === "clmm") {
    throw appError(
      "That's a concentrated-liquidity (CLMM) pool — decrease or close your range position instead.",
      400, "RAYDIUM_ERROR",
    );
  }
  throw appError("Unsupported Raydium pool type for remove-liquidity", 400, "RAYDIUM_ERROR");
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLMM helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Extract a BN from the {amount, fee} slippage shape the CLMM utils return. */
function pickAmount(x: any): BN {
  return (x && x.amount !== undefined) ? x.amount : x;
}

/** Find a user's CLMM position by its NFT mint (positionId). */
async function findClmmPosition(sdk: any, raydium: any, positionId: string): Promise<any> {
  const positions: any[] = await raydium.clmm.getOwnerPositionInfo({ programId: sdk.CLMM_PROGRAM_ID });
  const match = (positions ?? []).find((p) => {
    const nft = p?.nftMint?.toBase58 ? p.nftMint.toBase58() : String(p?.nftMint ?? "");
    return nft === positionId;
  });
  if (!match) throw appError("That CLMM position wasn't found for your wallet.", 404, "RAYDIUM_NO_POSITION");
  return match;
}

// ─────────────────────────────────────────────────────────────────────────────
// CLMM — open position
// Frontend params: { poolId, inputMint, inputAmount (UI), minPrice?, maxPrice?, tickLower?, tickUpper?, slippageBps }
// ─────────────────────────────────────────────────────────────────────────────

export async function buildRaydiumOpenPositionSdk(params: any, userWallet: string): Promise<BuildResponse> {
 try {
  if (!params.poolId) throw appError("poolId is required to open a CLMM position", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const Decimal = (await import("decimal.js")).default;
  const { poolInfo, poolKeys } = await raydium.clmm.getPoolInfoFromRpc(params.poolId);

  let tickLower: number, tickUpper: number;
  if (params.tickLower != null && params.tickUpper != null) {
    tickLower = Math.min(Number(params.tickLower), Number(params.tickUpper));
    tickUpper = Math.max(Number(params.tickLower), Number(params.tickUpper));
  } else {
    if (params.minPrice == null || params.maxPrice == null) {
      throw appError("Provide a price range (minPrice / maxPrice) for the position.", 400, "RAYDIUM_ERROR");
    }
    const t1 = sdk.TickUtils.getPriceAndTick({ poolInfo, price: new Decimal(params.minPrice), baseIn: true }).tick;
    const t2 = sdk.TickUtils.getPriceAndTick({ poolInfo, price: new Decimal(params.maxPrice), baseIn: true }).tick;
    tickLower = Math.min(t1, t2);
    tickUpper = Math.max(t1, t2);
  }

  const inputMint = resolveToken(params.inputMint)?.mint ?? params.inputMint;
  const isA = inputMint === poolInfo.mintA.address;
  const inDecimals = isA ? poolInfo.mintA.decimals : poolInfo.mintB.decimals;
  const baseAmount = toBaseUnits(String(params.inputAmount), inDecimals);

  const epochInfo = await raydium.fetchEpochInfo();
  const out = await sdk.PoolUtils.getLiquidityAmountOutFromAmountIn({
    poolInfo, slippage: (params.slippageBps ?? 100) / 10000, inputA: isA,
    tickUpper, tickLower, amount: baseAmount, add: true, amountHasFee: true, epochInfo,
  });
  const otherAmountMax = pickAmount(isA ? out.amountSlippageB : out.amountSlippageA);

  const { transaction } = await raydium.clmm.openPositionFromBase({
    poolInfo, poolKeys, tickLower, tickUpper,
    base: isA ? "MintA" : "MintB",
    baseAmount, otherAmountMax,
    ownerInfo: { useSOLBalance: true },
    withMetadata: "create",
    txVersion: sdk.TxVersion.V0,
  });
  return resp(
    "raydium_open_position", transaction, params,
    `Open Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position`,
    ["Concentrated liquidity — out-of-range positions stop earning fees"],
  );
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLMM — increase position
// Frontend params: { positionId, inputMint, inputAmount (UI), slippageBps }
// ─────────────────────────────────────────────────────────────────────────────

export async function buildRaydiumIncreasePositionSdk(params: any, userWallet: string): Promise<BuildResponse> {
 try {
  if (!params.positionId) throw appError("positionId is required", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const pos = await findClmmPosition(sdk, raydium, params.positionId);
  const poolId = pos.poolId?.toBase58 ? pos.poolId.toBase58() : String(pos.poolId);
  const { poolInfo, poolKeys } = await raydium.clmm.getPoolInfoFromRpc(poolId);

  const inputMint = resolveToken(params.inputMint)?.mint ?? params.inputMint;
  const isA = inputMint === poolInfo.mintA.address;
  const inDecimals = isA ? poolInfo.mintA.decimals : poolInfo.mintB.decimals;
  const baseAmount = toBaseUnits(String(params.inputAmount), inDecimals);

  const epochInfo = await raydium.fetchEpochInfo();
  const out = await sdk.PoolUtils.getLiquidityAmountOutFromAmountIn({
    poolInfo, slippage: (params.slippageBps ?? 100) / 10000, inputA: isA,
    tickUpper: pos.tickUpper, tickLower: pos.tickLower,
    amount: baseAmount, add: true, amountHasFee: true, epochInfo,
  });
  const otherAmountMax = pickAmount(isA ? out.amountSlippageB : out.amountSlippageA);

  const { transaction } = await raydium.clmm.increasePositionFromBase({
    poolInfo, poolKeys, ownerPosition: pos, ownerInfo: { useSOLBalance: true },
    base: isA ? "MintA" : "MintB", baseAmount, otherAmountMax,
    txVersion: sdk.TxVersion.V0,
  });
  return resp("raydium_increase_position", transaction, params, `Increase Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position`);
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLMM — decrease position
// Frontend params: { positionId, liquidity ("all" | "N%" | raw BN), slippageBps }
// ─────────────────────────────────────────────────────────────────────────────

export async function buildRaydiumDecreasePositionSdk(params: any, userWallet: string): Promise<BuildResponse> {
 try {
  if (!params.positionId) throw appError("positionId is required", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const pos = await findClmmPosition(sdk, raydium, params.positionId);
  const poolId = pos.poolId?.toBase58 ? pos.poolId.toBase58() : String(pos.poolId);
  const { poolInfo, poolKeys } = await raydium.clmm.getPoolInfoFromRpc(poolId);

  const total: BN = pos.liquidity;
  const raw = String(params.liquidity ?? "").trim().toLowerCase();
  let liquidity: BN;
  const pctMatch = raw.match(/^(\d+(?:\.\d+)?)\s*%$/);
  if (raw === "all" || raw === "max" || raw === "100%") {
    liquidity = total;
  } else if (pctMatch) {
    const pct = parseFloat(pctMatch[1]);
    liquidity = total.muln(Math.round(pct)).divn(100);
  } else if (raw) {
    liquidity = new BN(raw);
  } else {
    throw appError("Specify how much liquidity to remove (e.g. \"all\" or \"50%\").", 400, "RAYDIUM_ERROR");
  }
  if (!liquidity || liquidity.lten(0)) throw appError("Nothing to remove from this position.", 400, "RAYDIUM_ERROR");
  const closePosition = liquidity.gte(total);

  const { transaction } = await raydium.clmm.decreaseLiquidity({
    poolInfo, poolKeys, ownerPosition: pos,
    ownerInfo: { useSOLBalance: true, closePosition },
    liquidity, amountMinA: new BN(0), amountMinB: new BN(0),
    txVersion: sdk.TxVersion.V0,
  });
  return resp(
    "raydium_decrease_position", transaction, params,
    closePosition ? `Close Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position` : `Reduce Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position`,
  );
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLMM — close position (decrease all + close in one tx; plain close if empty)
// Frontend params: { positionId }
// ─────────────────────────────────────────────────────────────────────────────

export async function buildRaydiumClosePositionSdk(params: any, userWallet: string): Promise<BuildResponse> {
 try {
  if (!params.positionId) throw appError("positionId is required", 400, "RAYDIUM_ERROR");
  const { sdk, raydium } = await loadRaydium(userWallet);
  const pos = await findClmmPosition(sdk, raydium, params.positionId);
  const poolId = pos.poolId?.toBase58 ? pos.poolId.toBase58() : String(pos.poolId);
  const { poolInfo, poolKeys } = await raydium.clmm.getPoolInfoFromRpc(poolId);
  const total: BN = pos.liquidity;
  const desc = `Close Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position`;

  if (total && total.gtn(0)) {
    const { transaction } = await raydium.clmm.decreaseLiquidity({
      poolInfo, poolKeys, ownerPosition: pos,
      ownerInfo: { useSOLBalance: true, closePosition: true },
      liquidity: total, amountMinA: new BN(0), amountMinB: new BN(0),
      txVersion: sdk.TxVersion.V0,
    });
    return resp("raydium_close_position", transaction, params, desc, ["Withdraws all liquidity + fees and closes the position NFT"]);
  }
  const { transaction } = await raydium.clmm.closePosition({
    poolInfo, poolKeys, ownerPosition: pos, txVersion: sdk.TxVersion.V0,
  });
  return resp("raydium_close_position", transaction, params, desc);
 } catch (e) { mapRaydiumSdkError(e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// Create CPMM pool
// Frontend params: { mintA, mintB, amountA (UI), amountB (UI), startTime }
// ─────────────────────────────────────────────────────────────────────────────

export async function buildRaydiumCreatePoolSdk(params: any, userWallet: string): Promise<BuildResponse> {
 try {
  const { sdk, raydium } = await loadRaydium(userWallet);
  const mintAAddr = resolveToken(params.mintA)?.mint ?? params.mintA;
  const mintBAddr = resolveToken(params.mintB)?.mint ?? params.mintB;
  if (!mintAAddr || !mintBAddr) throw appError("Both tokens are required to create a pool.", 400, "RAYDIUM_ERROR");

  const mintAInfo = await raydium.token.getTokenInfo(mintAAddr);
  const mintBInfo = await raydium.token.getTokenInfo(mintBAddr);
  const feeConfigs = await raydium.api.getCpmmConfigs();
  if (!feeConfigs || !feeConfigs.length) throw appError("Couldn't load Raydium pool fee configs.", 502, "RAYDIUM_ERROR");

  const mintAAmount = toBaseUnits(String(params.amountA), mintAInfo.decimals);
  const mintBAmount = toBaseUnits(String(params.amountB), mintBInfo.decimals);

  const { transaction } = await raydium.cpmm.createPool({
    programId: sdk.CREATE_CPMM_POOL_PROGRAM,
    poolFeeAccount: sdk.CREATE_CPMM_POOL_FEE_ACC,
    mintA: mintAInfo,
    mintB: mintBInfo,
    mintAAmount,
    mintBAmount,
    startTime: new BN(Number(params.startTime ?? 0)),
    feeConfig: feeConfigs[0],
    associatedOnly: false,
    ownerInfo: { useSOLBalance: true },
    txVersion: sdk.TxVersion.V0,
  });
  return resp(
    "raydium_create_pool", transaction, params,
    `Create Raydium CPMM pool ${mintAInfo.symbol ?? "A"}/${mintBInfo.symbol ?? "B"}`,
    ["Creating a pool locks the initial deposit as the starting price"],
  );
 } catch (e) { mapRaydiumSdkError(e); }
}
