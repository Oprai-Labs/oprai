/**
 * Jupiter Swap — v6 REST API.
 *
 * Paid endpoint (api.jup.ag/swap/v1) used when JUPITER_API_KEY is set.
 * Public endpoint (quote-api.jup.ag/v6) used otherwise.
 */

import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken } from "./tokens";
import { v4 as uuidv4 } from "uuid";

const API_KEY = config.jupiterApiKey;
const QUOTE_URL = API_KEY
  ? "https://api.jup.ag/swap/v1/quote"
  : "https://quote-api.jup.ag/v6/quote";
const SWAP_URL = API_KEY
  ? "https://api.jup.ag/swap/v1/swap"
  : "https://quote-api.jup.ag/v6/swap";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface SwapParams {
  inputMint: string;
  outputMint: string;
  amount: string;
  slippageBps?: number;
  onlyDirectRoutes?: boolean;
  swapMode?: string;
  priorityFee?: string;
  restrictIntermediateTokens?: boolean;
}

export interface QuoteResult {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  otherAmountThreshold: string;
  swapMode: string;
  slippageBps: number;
  priceImpactPct: string;
  routePlan: unknown[];
  platformFee?: unknown;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function jupiterHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) h["x-api-key"] = API_KEY;
  return h;
}

function normalizeSwapMode(mode?: string): string {
  if (!mode) return "ExactIn";
  const lower = mode.toLowerCase();
  if (lower === "in" || lower === "exactin") return "ExactIn";
  if (lower === "out" || lower === "exactout") return "ExactOut";
  return "ExactIn";
}

function normalizeAmount(amount: string, decimals: number): string {
  const n = parseFloat(amount);
  if (isNaN(n) || n <= 0) throw appError("Amount must be a positive number");
  // If already looks like base units (large integer string), pass through
  if (!amount.includes(".") && n > 1_000_000) return amount;
  return Math.round(n * 10 ** decimals).toString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

export async function getQuote(params: {
  inputMint: string;
  outputMint: string;
  amount: string;
  slippageBps?: number;
  onlyDirectRoutes?: boolean;
  swapMode?: string;
  restrictIntermediateTokens?: boolean;
}): Promise<QuoteResult> {
  const url = new URL(QUOTE_URL);
  url.searchParams.set("inputMint", params.inputMint);
  url.searchParams.set("outputMint", params.outputMint);
  url.searchParams.set("amount", params.amount);
  url.searchParams.set("slippageBps", String(params.slippageBps ?? 50));
  url.searchParams.set("swapMode", normalizeSwapMode(params.swapMode));
  if (params.onlyDirectRoutes) url.searchParams.set("onlyDirectRoutes", "true");
  if (params.restrictIntermediateTokens)
    url.searchParams.set("restrictIntermediateTokens", "true");

  const res = await fetch(url.toString(), { headers: jupiterHeaders() });
  if (!res.ok) {
    const body = await res.text();
    throw appError(`Jupiter quote failed: ${body}`, 502, "JUPITER_ERROR");
  }
  return res.json() as Promise<QuoteResult>;
}

export async function buildSwap(
  params: SwapParams,
  userWallet: string
): Promise<BuildResponse> {
  // Resolve mints
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);

  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;
  const decimals = inToken?.decimals ?? 9;

  const amountLamports = normalizeAmount(params.amount, decimals);

  // Get quote
  const quote = await getQuote({
    inputMint,
    outputMint,
    amount: amountLamports,
    slippageBps: params.slippageBps ?? 50,
    onlyDirectRoutes: params.onlyDirectRoutes,
    swapMode: params.swapMode,
    restrictIntermediateTokens: params.restrictIntermediateTokens,
  });

  // Build swap transaction
  const body: Record<string, unknown> = {
    quoteResponse: quote,
    userPublicKey: userWallet,
    wrapAndUnwrapSol: true,
    dynamicComputeUnitLimit: true,
    dynamicSlippage: { maxBps: params.slippageBps ?? 50 },
  };

  // Priority fee
  if (params.priorityFee && params.priorityFee !== "auto") {
    const fee = parseInt(params.priorityFee, 10);
    if (!isNaN(fee)) body["prioritizationFeeLamports"] = fee;
  } else {
    body["prioritizationFeeLamports"] = "auto";
  }

  const res = await fetch(SWAP_URL, {
    method: "POST",
    headers: jupiterHeaders(),
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errBody = await res.text();
    throw appError(`Jupiter swap build failed: ${errBody}`, 502, "JUPITER_ERROR");
  }

  const data = (await res.json()) as { swapTransaction: string; lastValidBlockHeight?: number };

  const inSym = inToken?.name ?? params.inputMint;
  const outSym = outToken?.name ?? params.outputMint;
  const outUi = (parseFloat(quote.outAmount) / 10 ** (outToken?.decimals ?? 9)).toFixed(6);
  const inUi = (parseFloat(params.amount)).toFixed(6);

  const preview: ActionPreview = {
    id: uuidv4(),
    type: "swap",
    description: `Swap ${inUi} ${inSym} → ~${outUi} ${outSym} (slippage: ${(params.slippageBps ?? 50) / 100}%)`,
    estimatedFee: "~0.000005 SOL",
    params: params as unknown as Record<string, unknown>,
    warnings:
      parseFloat(quote.priceImpactPct) > 1
        ? [`High price impact: ${quote.priceImpactPct}%`]
        : [],
    requiresApproval: false,
  };

  return {
    preview,
    transaction: data.swapTransaction,
    additionalSignersRequired: 0,
    isCrossChain: false,
    quote,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Query actions (read-only, no transaction)
// ─────────────────────────────────────────────────────────────────────────────

export async function getPrice(params: { inputMint: string; outputMint: string }): Promise<BuildResponse> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;

  // Use 1 unit of input token
  const decimals = inToken?.decimals ?? 9;
  const amount = Math.round(1 * 10 ** decimals).toString();

  const quote = await getQuote({ inputMint, outputMint, amount, slippageBps: 50 });
  const price = parseFloat(quote.outAmount) / 10 ** (outToken?.decimals ?? 6) /
    (parseFloat(quote.inAmount) / 10 ** decimals);

  return {
    preview: {
      id: uuidv4(), type: "jup_price",
      description: `1 ${inToken?.name ?? inputMint} ≈ ${price.toFixed(6)} ${outToken?.name ?? outputMint}`,
      estimatedFee: "0", params: params as unknown as Record<string, unknown>,
      warnings: [], requiresApproval: false,
    },
    additionalSignersRequired: 0, isCrossChain: false,
    data: { price, inputMint, outputMint, quote },
  };
}

export async function searchTokens(params: { query: string; limit?: number }): Promise<BuildResponse> {
  const url = `https://tokens.jup.ag/tokens/search?q=${encodeURIComponent(params.query)}&limit=${params.limit ?? 10}`;
  const res = await fetch(url);
  const data = await res.json();
  return {
    preview: {
      id: uuidv4(), type: "jup_token_search",
      description: `Token search: "${params.query}"`,
      estimatedFee: "0", params: params as unknown as Record<string, unknown>,
      warnings: [], requiresApproval: false,
    },
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
