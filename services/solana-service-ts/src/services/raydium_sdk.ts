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

function resp(
  type: string,
  tx: VersionedTransaction,
  params: Record<string, unknown>,
  description: string,
  warnings?: string[],
  additionalSignersRequired = 0,
): BuildResponse {
  return {
    preview: preview(type, description, params, warnings),
    transaction: serializeV0(tx),
    additionalSignersRequired,
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

/** The wallet's live LP-token balance (raw base units) for a mint. */
async function liveLpBalance(userWallet: string, lpMint: string): Promise<BN> {
  const connection = new Connection(config.solanaRpc, "confirmed");
  const owner = new PublicKey(userWallet);
  const r = await connection.getParsedTokenAccountsByOwner(owner, { mint: new PublicKey(lpMint) });
  let total = new BN(0);
  for (const a of r.value) {
    total = total.add(new BN((a.account.data as any).parsed?.info?.tokenAmount?.amount ?? "0"));
  }
  return total;
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
  const lpDecimals = apiInfo.lpMint?.decimals ?? 9;
  const lpMintAddr = apiInfo.lpMint?.address ?? apiInfo.lpMint;

  // Resolve the LP amount against the LIVE on-chain balance. "all"/"max" (or an
  // empty amount) removes everything; an explicit amount is CAPPED at the live
  // balance so a slightly-stale snapshot (e.g. a card that pre-filled the amount
  // from an earlier read) can't request more LP than the user holds and revert
  // the whole withdraw with "insufficient" — which is why a "close" appeared to
  // do nothing and the position kept showing.
  const requested = String(params.lpAmount ?? "").trim().toLowerCase();
  const live = lpMintAddr ? await liveLpBalance(userWallet, String(lpMintAddr)) : new BN(0);
  let lpBase: BN;
  if (requested === "all" || requested === "max" || requested === "") {
    lpBase = live;
  } else {
    lpBase = toBaseUnits(params.lpAmount, lpDecimals);
    if (live.gtn(0) && lpBase.gt(live)) lpBase = live;
  }
  if (lpBase.lten(0)) throw appError("You have no LP tokens in this pool to withdraw.", 400, "RAYDIUM_NO_POSITION");

  if (kind === "cpmm") {
    const { poolInfo, poolKeys } = await raydium.cpmm.getPoolInfoFromRpc(params.poolId);
    const lpAmount = lpBase;
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
    const lpAmount = lpBase;
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
/**
 * Load a CLMM pool for a liquidity operation.
 *
 * `clmm.getPoolInfoFromRpc` alone is NOT enough: the SDK derives the reward
 * token accounts it appends to decrease/increase-liquidity from
 * `poolInfo.rewardDefaultInfos`, and the RPC-built poolInfo doesn't carry them.
 * On a pool that emits rewards (e.g. WSOL/USDC pays RAY) the instruction then
 * arrives with fewer reward accounts than the program expects and fails with
 * `InvalidRewardInputAccountNumber` (custom error 6030) — which is what made
 * every CLMM close fail. Raydium's own SDK demo reads poolInfo from the API on
 * mainnet for exactly this reason; we do the same and keep the RPC poolKeys.
 */
async function loadClmmPool(raydium: any, poolId: string): Promise<{ poolInfo: any; poolKeys: any }> {
  const rpc = await raydium.clmm.getPoolInfoFromRpc(poolId);
  try {
    const api = await raydium.api.fetchPoolById({ ids: poolId });
    const apiInfo = Array.isArray(api) ? api[0] : api;
    if (apiInfo?.rewardDefaultInfos?.length) {
      // Keep the RPC pool state (live tick/price) but take the reward
      // definitions from the API so the SDK builds every reward account.
      return { poolInfo: { ...rpc.poolInfo, rewardDefaultInfos: apiInfo.rewardDefaultInfos }, poolKeys: rpc.poolKeys };
    }
  } catch { /* API unreachable — fall back to the RPC-only view */ }
  return rpc;
}

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

  // A CLMM position is NFT-backed, so opening one mints a fresh NFT that must
  // sign. Left to itself the SDK generates that key here and signs with it,
  // and what reaches the wallet is a transaction already carrying a stranger's
  // signature with the user's own slot still empty — the shape Phantom's
  // scanner cannot vouch for, and the reason its multi-signer guidance asks
  // for the wallet's signature first.
  //
  // `getEphemeralSigners` is the SDK's own hook for exactly this: return the
  // public key the caller already holds and the SDK uses it WITHOUT signing.
  // The browser generated it, so the browser adds that signature after the
  // wallet has added its own.
  const clientNftMint: string | undefined =
    typeof params.positionNftMint === "string" && params.positionNftMint.trim()
      ? params.positionNftMint.trim()
      : undefined;

  const { transaction } = await raydium.clmm.openPositionFromBase({
    poolInfo, poolKeys, tickLower, tickUpper,
    base: isA ? "MintA" : "MintB",
    baseAmount, otherAmountMax,
    ownerInfo: { useSOLBalance: true },
    withMetadata: "create",
    txVersion: sdk.TxVersion.V0,
    // Absent (an older client), the SDK falls back to generating and signing
    // the key itself — the previous behaviour, kept so a stale frontend works.
    ...(clientNftMint
      ? { getEphemeralSigners: async (_k: number) => [new PublicKey(clientNftMint)] }
      : {}),
  });
  return resp(
    "raydium_open_position", transaction, params,
    `Open Raydium CLMM ${symOf(poolInfo.mintA.address)}/${symOf(poolInfo.mintB.address)} position`,
    ["Concentrated liquidity — out-of-range positions stop earning fees"],
    // One slot is the client's NFT key, still empty and waiting for it. This
    // reported 0 while shipping a slot the server had already filled, which is
    // how a pre-signed transaction went unnoticed.
    clientNftMint ? 1 : 0,
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
  const { poolInfo, poolKeys } = await loadClmmPool(raydium, poolId);

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
  const { poolInfo, poolKeys } = await loadClmmPool(raydium, poolId);

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
  const { poolInfo, poolKeys } = await loadClmmPool(raydium, poolId);
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

// ─────────────────────────────────────────────────────────────────────────────
// Read: the user's Raydium CLMM positions — straight from chain via the SDK
// (raydium.clmm.getOwnerPositionInfo), NOT a cross-protocol portfolio aggregator.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Token amounts a CLMM position currently holds, derived from its liquidity L
 * and tick range. Raw `liquidity` is an internal constant — meaningless to a
 * user — so we surface the actual "you'd get back X A + Y B" figures instead.
 *
 * Standard concentrated-liquidity math: the price at a tick, 1.0001^tick, is a
 * ratio of RAW (integer) token units, so the pool's UI price has to be scaled
 * by the decimal difference before taking square roots.
 *
 * These are display estimates (float math, fees excluded); the exact payout is
 * computed on-chain at withdrawal.
 */
function clmmPositionAmounts(
  liquidity: string,
  tickLower: number,
  tickUpper: number,
  uiPrice: number, // mintB per mintA, as Raydium reports `price`
  decA: number,
  decB: number,
): { amountA: number; amountB: number } | null {
  const L = Number(liquidity);
  if (!Number.isFinite(L) || L <= 0) return null;
  if (!Number.isFinite(uiPrice) || uiPrice <= 0) return null;
  if (!Number.isFinite(tickLower) || !Number.isFinite(tickUpper)) return null;

  const sqrtP = Math.sqrt(uiPrice * Math.pow(10, decB - decA));
  const sqrtPa = Math.pow(1.0001, tickLower / 2);
  const sqrtPb = Math.pow(1.0001, tickUpper / 2);
  if (!(sqrtPa > 0) || !(sqrtPb > sqrtPa)) return null;

  let raw0 = 0;
  let raw1 = 0;
  if (sqrtP <= sqrtPa) {
    // Price below the range → the position sits entirely in token A.
    raw0 = L * (1 / sqrtPa - 1 / sqrtPb);
  } else if (sqrtP >= sqrtPb) {
    // Price above the range → entirely token B.
    raw1 = L * (sqrtPb - sqrtPa);
  } else {
    raw0 = L * (1 / sqrtP - 1 / sqrtPb);
    raw1 = L * (sqrtP - sqrtPa);
  }
  const amountA = raw0 / Math.pow(10, decA);
  const amountB = raw1 / Math.pow(10, decB);
  if (!Number.isFinite(amountA) || !Number.isFinite(amountB)) return null;
  return { amountA, amountB };
}

export async function getRaydiumUserPositionsSdk(_params: any, userWallet: string): Promise<BuildResponse> {
 try {
  const { sdk, raydium } = await loadRaydium(userWallet);
  const raw: any[] = (await raydium.clmm.getOwnerPositionInfo({ programId: sdk.CLMM_PROGRAM_ID })) ?? [];

  const poolCache: Record<string, any> = {};
  const positions: any[] = [];
  for (const p of raw) {
    const poolId = p.poolId?.toBase58 ? p.poolId.toBase58() : String(p.poolId);
    let pair = "";
    try {
      if (!poolCache[poolId]) {
        const info = await raydium.api.fetchPoolById({ ids: poolId });
        poolCache[poolId] = Array.isArray(info) ? info[0] : info;
      }
      const pi = poolCache[poolId];
      if (pi?.mintA) pair = `${pi.mintA.symbol || symOf(pi.mintA.address)}/${pi.mintB.symbol || symOf(pi.mintB.address)}`;
    } catch { /* best-effort enrichment */ }
    const liqStr = p.liquidity?.toString ? p.liquidity.toString() : String(p.liquidity ?? "0");
    const pcpi = poolCache[poolId];
    const mintMeta = (m: any) => m ? { address: m.address, symbol: m.symbol || symOf(m.address), logoURI: m.logoURI ?? null } : null;
    // Convert the raw liquidity constant into the token amounts the position
    // actually holds, so the UI can show "0.0123 SOL + 4.56 USDC" instead of
    // an opaque "106903912".
    const amounts = clmmPositionAmounts(
      liqStr,
      Number(p.tickLower),
      Number(p.tickUpper),
      Number(pcpi?.price),
      Number(pcpi?.mintA?.decimals ?? 9),
      Number(pcpi?.mintB?.decimals ?? 6),
    );
    positions.push({
      kind: "clmm",
      positionId: p.nftMint?.toBase58 ? p.nftMint.toBase58() : String(p.nftMint),
      poolId,
      pair,
      mintA: mintMeta(pcpi?.mintA),
      mintB: mintMeta(pcpi?.mintB),
      tickLower: p.tickLower,
      tickUpper: p.tickUpper,
      liquidity: liqStr,
      ...(amounts ? { amountA: amounts.amountA, amountB: amounts.amountB } : {}),
      empty: liqStr === "0",
    });
  }

  // Standard AMM / CPMM LP positions: getOwnerPositionInfo only covers CLMM, so
  // scan the wallet's token accounts for Raydium LP mints (a Standard/CPMM
  // deposit mints LP tokens, not a position NFT). Raydium's pools/info/lps maps
  // an LP mint back to its pool.
  try {
    const connection = new Connection(config.solanaRpc, "confirmed");
    const owner = new PublicKey(userWallet);
    const TOKEN_PROG = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
    const TOKEN_2022 = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
    const held: { mint: string; amt: number }[] = [];
    for (const prog of [TOKEN_PROG, TOKEN_2022]) {
      const r = await connection.getParsedTokenAccountsByOwner(owner, { programId: prog });
      for (const a of r.value) {
        const info = (a.account.data as any).parsed?.info;
        const amt = info?.tokenAmount?.uiAmount;
        if (info?.mint && amt > 0) held.push({ mint: info.mint, amt });
      }
    }
    if (held.length) {
      const res = await fetch(`https://api-v3.raydium.io/pools/info/lps?lps=${held.map((h) => h.mint).join(",")}`);
      const j: any = await res.json();
      for (const pool of (j?.data ?? []).filter(Boolean)) {
        const lpMint = pool.lpMint?.address ?? pool.lpMint;
        const bal = held.find((h) => h.mint === lpMint);
        // LP tokens are an abstraction — surface the underlying token amounts
        // they redeem for (share of supply × each reserve), same reasoning as
        // the CLMM amounts above.
        const lpSupply = Number(pool.lpAmount);
        const share = lpSupply > 0 ? (bal?.amt ?? 0) / lpSupply : 0;
        const lpAmounts = share > 0
          ? { amountA: share * Number(pool.mintAmountA), amountB: share * Number(pool.mintAmountB) }
          : null;
        positions.push({
          kind: "lp",
          ...(lpAmounts && Number.isFinite(lpAmounts.amountA) && Number.isFinite(lpAmounts.amountB) ? lpAmounts : {}),
          poolId: pool.id,
          pair: `${pool.mintA?.symbol || symOf(pool.mintA?.address)}/${pool.mintB?.symbol || symOf(pool.mintB?.address)}`,
          mintA: pool.mintA ? { address: pool.mintA.address, symbol: pool.mintA.symbol || symOf(pool.mintA.address), logoURI: pool.mintA.logoURI ?? null } : null,
          mintB: pool.mintB ? { address: pool.mintB.address, symbol: pool.mintB.symbol || symOf(pool.mintB.address), logoURI: pool.mintB.logoURI ?? null } : null,
          poolType: pool.type, // "Standard"
          lpMint,
          lpAmount: bal?.amt ?? 0,
        });
      }
    }
  } catch { /* best-effort LP scan */ }

  const clmmCount = positions.filter((x) => x.kind === "clmm").length;
  const lpCount = positions.filter((x) => x.kind === "lp").length;
  return {
    preview: preview(
      "raydium_get_user_positions",
      positions.length ? `Raydium positions (${clmmCount} CLMM, ${lpCount} LP)` : "No active Raydium positions",
      {}, [],
    ),
    data: { source: "raydium-sdk", count: positions.length, clmmCount, lpCount, positions },
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
 } catch (e) { mapRaydiumSdkError(e); }
}
