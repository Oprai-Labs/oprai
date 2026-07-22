/**
 * Raydium — transaction-v1.raydium.io REST API.
 * Returns unsigned base64 transactions directly.
 * Data queries use api-v3.raydium.io.
 */

import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken } from "./tokens";
import { v4 as uuidv4 } from "uuid";

const RAYDIUM_TX = "https://transaction-v1.raydium.io";
const RAYDIUM_API = "https://api-v3.raydium.io";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

async function raydiumPost(path: string, body: Record<string, unknown>): Promise<string> {
  const res = await fetch(`${RAYDIUM_TX}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw appError(`Raydium error: ${await res.text()}`, 502, "RAYDIUM_ERROR");
  const data = (await res.json()) as { data?: { transaction?: string }[]; transaction?: string };
  const tx = data?.data?.[0]?.transaction ?? data?.transaction;
  if (!tx) throw appError("Raydium returned no transaction", 502, "RAYDIUM_ERROR");
  return tx;
}

// ─────────────────────────────────────────────────────────────────────────────
// Swap
// ─────────────────────────────────────────────────────────────────────────────

export interface RaydiumSwapParams {
  inputMint: string;
  outputMint: string;
  amount: string;
  slippageBps?: number;
  swapMode?: string;
}

export async function buildRaydiumSwap(params: RaydiumSwapParams, userWallet: string): Promise<BuildResponse> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;
  const decimals = inToken?.decimals ?? 9;
  const amount = Math.round(parseFloat(params.amount) * 10 ** decimals).toString();

  // Get quote from Raydium
  const quoteRes = await fetch(
    `${RAYDIUM_API}/compute/swap-base-in?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${params.slippageBps ?? 50}&txVersion=V0`
  );
  if (!quoteRes.ok) throw appError(`Raydium quote failed: ${await quoteRes.text()}`, 502, "RAYDIUM_ERROR");
  const quote = (await quoteRes.json()) as { data: { swapType: string; inputMint: string; outputMint: string; inputAmount: string; outputAmount: string; otherAmountThreshold: string } };

  const tx = await raydiumPost("/transaction/swap-base-in", {
    computeUnitPriceMicroLamports: "200000",
    swapResponse: quote.data,
    txVersion: "V0",
    wallet: userWallet,
    wrapSol: true,
    unwrapSol: true,
  });

  return {
    preview: preview(
      "raydium_swap",
      `Swap ${params.amount} ${inToken?.name ?? params.inputMint} → ${outToken?.name ?? params.outputMint} via Raydium`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export interface RaydiumQuoteResult {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  otherAmountThreshold: string;
  swapMode: string;
  priceImpactPct: string;
}

/**
 * Live price estimate for a Raydium swap — quoted from Raydium's OWN compute
 * endpoint (the same venue that `buildRaydiumSwap` executes on), NOT Jupiter.
 * Returns a Jupiter-shaped quote so the frontend swap widget can consume it
 * through the identical code path. ExactIn → swap-base-in (amount in input
 * token); ExactOut → swap-base-out (amount in output token).
 */
export async function getRaydiumQuote(params: RaydiumSwapParams): Promise<RaydiumQuoteResult> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;
  const modeStr = String(params.swapMode ?? "").toLowerCase();
  const isExactOut = modeStr.startsWith("exactout") || modeStr === "out";

  // ExactOut denominates the amount in the OUTPUT token; ExactIn in the input.
  const amtDecimals = isExactOut ? (outToken?.decimals ?? 9) : (inToken?.decimals ?? 9);
  const amount = Math.round(parseFloat(params.amount) * 10 ** amtDecimals).toString();
  const slippageBps = params.slippageBps ?? 50;
  const path = isExactOut ? "compute/swap-base-out" : "compute/swap-base-in";

  const res = await fetch(
    `${RAYDIUM_API}/${path}?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${slippageBps}&txVersion=V0`
  );
  if (!res.ok) throw appError(`Raydium quote failed: ${await res.text()}`, 502, "RAYDIUM_ERROR");
  const body = (await res.json()) as {
    success?: boolean;
    msg?: string;
    data?: {
      inputAmount?: string;
      outputAmount?: string;
      otherAmountThreshold?: string;
      priceImpactPct?: number | string;
    };
  };
  if (!body?.data) throw appError(body?.msg || "Raydium found no route for this pair", 404, "RAYDIUM_NO_ROUTE");
  const d = body.data;

  // Raydium returns priceImpactPct as a PERCENT number (e.g. 0.12 = 0.12%).
  // The frontend multiplies by 100 (Jupiter convention: fraction string), so
  // divide back to a fraction here to keep a single contract on the client.
  const impactPct = Number(d.priceImpactPct ?? 0);
  return {
    inputMint,
    outputMint,
    inAmount: String(d.inputAmount ?? amount),
    outAmount: String(d.outputAmount ?? d.otherAmountThreshold ?? "0"),
    otherAmountThreshold: String(d.otherAmountThreshold ?? "0"),
    swapMode: isExactOut ? "ExactOut" : "ExactIn",
    priceImpactPct: String((Number.isFinite(impactPct) ? impactPct : 0) / 100),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Liquidity
// ─────────────────────────────────────────────────────────────────────────────

export interface RaydiumLiquidityParams {
  poolId: string;
  amount: string;
  inputMint?: string;
  slippageBps?: number;
}

export async function buildRaydiumAddLiquidity(params: RaydiumLiquidityParams, userWallet: string): Promise<BuildResponse> {
  const tx = await raydiumPost("/transaction/add-liquidity", {
    wallet: userWallet,
    poolId: params.poolId,
    amount: params.amount,
    mint: params.inputMint,
    slippage: (params.slippageBps ?? 50) / 10000,
    txVersion: "V0",
  });
  return {
    preview: preview("raydium_add_liquidity", `Add liquidity to Raydium pool ${params.poolId.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildRaydiumRemoveLiquidity(params: { poolId: string; lpAmount: string; slippageBps?: number }, userWallet: string): Promise<BuildResponse> {
  const tx = await raydiumPost("/transaction/remove-liquidity", {
    wallet: userWallet,
    poolId: params.poolId,
    lpAmount: params.lpAmount,
    slippage: (params.slippageBps ?? 50) / 10000,
    txVersion: "V0",
  });
  return {
    preview: preview("raydium_remove_liquidity", `Remove liquidity from Raydium pool`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries
// ─────────────────────────────────────────────────────────────────────────────

export async function getRaydiumPools(params: { type?: string; page?: number; pageSize?: number }): Promise<BuildResponse> {
  const url = new URL(`${RAYDIUM_API}/pools/info/list`);
  if (params.type) url.searchParams.set("poolType", params.type);
  url.searchParams.set("pageSize", String(params.pageSize ?? 100));
  url.searchParams.set("page", String(params.page ?? 1));
  const res = await fetch(url.toString());
  const data = await res.json();
  return {
    preview: preview("raydium_get_pools", "Raydium pool list", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getRaydiumTokenInfo(params: { mint: string }): Promise<BuildResponse> {
  const res = await fetch(`${RAYDIUM_API}/mint/ids?mints=${params.mint}`);
  const data = await res.json();
  return {
    preview: preview("raydium_get_token_info", `Raydium token info: ${params.mint}`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
