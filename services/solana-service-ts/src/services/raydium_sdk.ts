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
import { resolveToken } from "./tokens";
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
}
