/**
 * Pump.fun — complete token launch and trading service.
 *
 * All transaction-building uses PumpPortal (pumpportal.fun/api) for buy/sell,
 * and direct Anchor instruction construction for launch (via Rust service).
 *
 * All data queries proxy through our own backend (frontend-api.pump.fun).
 */

import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const PUMP_API = "https://pumpportal.fun/api";
const PUMP_DATA_API = "https://frontend-api.pump.fun";

export const PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
export const PUMP_FUN_FEE_RECIPIENT = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM";
export const PUMP_FUN_CREATE_FEE = 0.02; // SOL

// pump.fun bonding curve graduation threshold (SOL in reserves at migration)
export const GRADUATION_SOL_THRESHOLD = 85; // ~85 SOL triggers migration to Raydium
export const INITIAL_VIRTUAL_SOL_RESERVES = 30;     // SOL
export const INITIAL_VIRTUAL_TOKEN_RESERVES = 1_073_000_191; // tokens (6 decimals)

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface PumpFunToken {
  mint: string;
  name: string;
  symbol: string;
  description: string;
  image_uri: string;
  creator: string;
  created_timestamp: number;
  market_cap: number;
  usd_market_cap: number;
  virtual_sol_reserves: number;
  virtual_token_reserves: number;
  real_sol_reserves: number;
  real_token_reserves: number;
  bonding_curve: string;
  associated_bonding_curve: string;
  complete: boolean;           // true = migrated to Raydium
  raydium_pool?: string;
  king_of_the_hill_timestamp?: number;
  reply_count: number;
  last_reply?: number;
  total_supply: number;
  twitter?: string;
  telegram?: string;
  website?: string;
  show_name: boolean;
  inverted?: boolean;
  is_currently_live?: boolean;
  nsfw: boolean;
}

export interface PumpFunComment {
  id: number;
  mint: string;
  user: string;
  text: string;
  timestamp: number;
  profile_image?: string;
  username?: string;
  likes: number;
  replies: PumpFunComment[];
}

export interface PumpFunUserProfile {
  address: string;
  username?: string;
  profile_image?: string;
  bio?: string;
  created_tokens: number;
  coins_created: PumpFunToken[];
  coins_held: Array<{
    mint: string;
    balance: number;
    balance_usd: number;
  }>;
  followers?: number;
  following?: number;
}

export interface BondingCurveState {
  virtualSolReserves: number;   // lamports
  virtualTokenReserves: number; // base units
  realSolReserves: number;      // lamports
  realTokenReserves: number;    // base units
  fillPercent: number;          // 0-100
  pricePerToken: number;        // SOL per token
  pricePerTokenUsd: number;     // USD per token (approx)
  graduationThresholdSol: number;
  solToGraduation: number;      // SOL remaining to graduation
  graduated: boolean;
}

export interface LaunchTokenParams {
  name: string;
  symbol: string;
  description: string;
  imageUrl?: string;
  metadataUri?: string; // pre-hosted metadata JSON URL (from POST /upload/metadata)
  twitter?: string;
  telegram?: string;
  website?: string;
  bannerUrl?: string;
  initialBuyAmount?: string; // SOL
  slippage?: number;
  priorityFee?: number;
  mintKeypairBase58?: string;
  // pump.fun feature flags
  mayhemMode?: boolean; // increases price volatility for 24h post-launch
  cashback?: boolean;   // routes creator rewards to traders instead of creator
}

export interface PumpfunTradeParams {
  mint: string;
  amount: string;
  denominatedInSol?: boolean; // true = SOL, false = tokens
  slippage?: number;
  priorityFee?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: type === "launch_token"
      ? `~${PUMP_FUN_CREATE_FEE} SOL (creation fee)`
      : "~0.00025 SOL (network fee)",
    params, warnings: [], requiresApproval: false,
  };
}

async function fetchPumpData<T>(path: string): Promise<T> {
  const url = `${PUMP_DATA_API}${path}`;
  const res = await fetch(url, {
    headers: { "Accept": "application/json", "User-Agent": "oprai/1.0" },
  });
  if (!res.ok) {
    throw appError(`Pump.fun API error ${res.status}: ${path}`, res.status, "PUMPFUN_DATA_ERROR");
  }
  return res.json() as Promise<T>;
}

async function buildPumpPortalTx(body: Record<string, unknown>): Promise<string> {
  const res = await fetch(`${PUMP_API}/trade-local`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "unknown error");
    throw appError(`PumpFun trade-local failed: ${text}`, 502, "PUMPFUN_TX_ERROR");
  }
  const data = (await res.json()) as { tx?: string; transaction?: string };
  const tx = data.tx ?? data.transaction;
  if (!tx) throw appError("PumpFun returned no transaction data", 502, "PUMPFUN_TX_ERROR");
  return tx;
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonding curve calculations
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Calculate bonding curve state from token reserves.
 * Uses pump.fun's constant-product AMM formula with virtual liquidity.
 */
export function calcBondingCurveState(token: Partial<PumpFunToken>, solPriceUsd = 0): BondingCurveState {
  const vsr = (token.virtual_sol_reserves ?? INITIAL_VIRTUAL_SOL_RESERVES * 1e9);
  const vtr = (token.virtual_token_reserves ?? INITIAL_VIRTUAL_TOKEN_RESERVES * 1e6);
  const rsr = token.real_sol_reserves ?? 0;

  const graduated = token.complete === true;
  const vsrSol = vsr / 1e9;
  const solToGrad = Math.max(0, GRADUATION_SOL_THRESHOLD - vsrSol);
  const fillPercent = graduated ? 100 : Math.min(100, ((vsrSol - INITIAL_VIRTUAL_SOL_RESERVES) / (GRADUATION_SOL_THRESHOLD - INITIAL_VIRTUAL_SOL_RESERVES)) * 100);

  // price = virtual_sol_reserves / virtual_token_reserves (in SOL per base unit)
  // multiply by 1e6 for tokens with 6 decimals
  const pricePerToken = vtr > 0 ? (vsr / 1e9) / (vtr / 1e6) : 0;

  return {
    virtualSolReserves: vsr,
    virtualTokenReserves: vtr,
    realSolReserves: rsr,
    realTokenReserves: token.real_token_reserves ?? 0,
    fillPercent: Math.max(0, fillPercent),
    pricePerToken,
    pricePerTokenUsd: pricePerToken * solPriceUsd,
    graduationThresholdSol: GRADUATION_SOL_THRESHOLD,
    solToGraduation: solToGrad,
    graduated,
  };
}

/**
 * Estimate tokens received for a given SOL amount using the bonding curve formula.
 * tokens_out = (virtual_token_reserves * sol_in) / (virtual_sol_reserves + sol_in)
 */
export function estimateTokensForSol(solAmount: number, state?: Partial<BondingCurveState>): number {
  const vsr = state?.virtualSolReserves ?? INITIAL_VIRTUAL_SOL_RESERVES * 1e9;
  const vtr = state?.virtualTokenReserves ?? INITIAL_VIRTUAL_TOKEN_RESERVES * 1e6;
  const solLamports = solAmount * 1e9;
  return (vtr * solLamports) / (vsr + solLamports) / 1e6;
}

/**
 * Check if a token has graduated to Raydium.
 * Returns true if the token is on a DEX (not bonding curve anymore).
 */
export function isGraduated(token: Pick<PumpFunToken, "complete" | "raydium_pool">): boolean {
  return token.complete === true || !!token.raydium_pool;
}

// ─────────────────────────────────────────────────────────────────────────────
// Launch token
// ─────────────────────────────────────────────────────────────────────────────

export async function buildLaunchToken(params: LaunchTokenParams, userWallet: string): Promise<BuildResponse> {
  if (!params.name?.trim() || !params.symbol?.trim()) {
    throw appError("Token name and symbol are required");
  }
  if (!params.metadataUri && !params.imageUrl) {
    throw appError(
      "Token image or metadata URI is required. " +
      "Upload image via POST /upload/image and metadata via POST /upload/metadata first."
    );
  }

  const initialBuy = parseFloat(params.initialBuyAmount ?? "0");

  const body: Record<string, unknown> = {
    publicKey: userWallet,
    action: "create",
    tokenMetadata: {
      name: params.name.trim(),
      symbol: params.symbol.trim().toUpperCase(),
      description: params.description ?? "",
      image: params.imageUrl,
      uri: params.metadataUri,       // on-chain metadata URI (pre-hosted JSON)
      twitter: params.twitter || undefined,
      telegram: params.telegram || undefined,
      website: params.website || undefined,
      showName: true,
      ...(params.bannerUrl ? { banner: params.bannerUrl } : {}),
      ...(params.mayhemMode ? { mayhemMode: true } : {}),
      ...(params.cashback   ? { cashback: true }    : {}),
    },
    denominatedInSol: "true",
    amount: String(initialBuy),
    slippage: params.slippage ?? 10,
    priorityFee: params.priorityFee ?? 0.0005,
    pool: "pump",
  };

  const tx = await buildPumpPortalTx(body);

  const warnings: string[] = [];
  if (!params.twitter && !params.telegram && !params.website) {
    warnings.push("No social links — adding them increases token credibility.");
  }
  if (initialBuy > 1) {
    warnings.push(`Large initial buy: ${initialBuy} SOL. This will move the price significantly.`);
  }

  return {
    preview: {
      ...preview("launch_token", `Launch $${params.symbol.toUpperCase()} (${params.name}) on Pump.fun`, params as unknown as Record<string, unknown>),
      estimatedFee: `~${(PUMP_FUN_CREATE_FEE + initialBuy).toFixed(4)} SOL`,
      warnings,
      requiresApproval: true,
    },
    transaction: tx,
    additionalSignersRequired: 1, // mint keypair must sign
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Buy on bonding curve
// ─────────────────────────────────────────────────────────────────────────────

export async function buildPumpfunBuy(params: PumpfunTradeParams, userWallet: string): Promise<BuildResponse> {
  if (!params.mint?.trim()) throw appError("mint address required");
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  // Check if token has graduated — if so, route to Jupiter instead
  try {
    const tokenData = await fetchPumpData<PumpFunToken>(`/coins/${params.mint}`);
    if (isGraduated(tokenData)) {
      throw appError(
        `Token ${tokenData.symbol} has graduated to Raydium. Use swap (Jupiter) instead.`,
        400,
        "TOKEN_GRADUATED"
      );
    }
  } catch (e: any) {
    if (e?.code === "TOKEN_GRADUATED") throw e;
    // If we can't fetch token info, proceed anyway
  }

  const tx = await buildPumpPortalTx({
    publicKey: userWallet,
    action: "buy",
    mint: params.mint,
    denominatedInSol: String(params.denominatedInSol !== false),
    amount: params.amount,
    slippage: params.slippage ?? 10,
    priorityFee: params.priorityFee ?? 0.0005,
    pool: "pump",
  });

  const label = params.denominatedInSol !== false
    ? `Buy with ${params.amount} SOL`
    : `Buy ${params.amount} tokens`;

  return {
    preview: preview("pumpfun_buy", `${label} of ${params.mint.slice(0, 8)}... on Pump.fun`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Sell on bonding curve
// ─────────────────────────────────────────────────────────────────────────────

export async function buildPumpfunSell(params: PumpfunTradeParams, userWallet: string): Promise<BuildResponse> {
  if (!params.mint?.trim()) throw appError("mint address required");
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  // Check if token has graduated — if so, route to Jupiter
  try {
    const tokenData = await fetchPumpData<PumpFunToken>(`/coins/${params.mint}`);
    if (isGraduated(tokenData)) {
      throw appError(
        `Token ${tokenData.symbol} has graduated to Raydium. Use swap (Jupiter) instead.`,
        400,
        "TOKEN_GRADUATED"
      );
    }
  } catch (e: any) {
    if (e?.code === "TOKEN_GRADUATED") throw e;
  }

  const tx = await buildPumpPortalTx({
    publicKey: userWallet,
    action: "sell",
    mint: params.mint,
    denominatedInSol: String(params.denominatedInSol === true),
    amount: params.amount,
    slippage: params.slippage ?? 10,
    priorityFee: params.priorityFee ?? 0.0005,
    pool: "pump",
  });

  const label = params.denominatedInSol === true
    ? `Sell for ${params.amount} SOL`
    : `Sell ${params.amount} tokens`;

  return {
    preview: preview("pumpfun_sell", `${label} of ${params.mint.slice(0, 8)}... on Pump.fun`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries (proxied from pump.fun frontend API)
// ─────────────────────────────────────────────────────────────────────────────

export async function getPumpfunTokenInfo(params: { mint: string }): Promise<BuildResponse> {
  if (!params.mint) throw appError("mint address required");
  const data = await fetchPumpData<PumpFunToken>(`/coins/${params.mint}`).catch(() => null);
  const curveState = data ? calcBondingCurveState(data) : null;
  return {
    preview: preview("pumpfun_token_info", `Pump.fun token info: ${params.mint.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false,
    data: { token: data, bondingCurve: curveState },
  };
}

export async function getPumpfunTrending(params: { limit?: number }): Promise<BuildResponse> {
  const limit = Math.min(params.limit ?? 20, 50);
  const data = await fetchPumpData<PumpFunToken[]>(
    `/coins?limit=${limit}&sort=market_cap&order=DESC&includeNsfw=false`
  ).catch(() => []);
  return {
    preview: preview("pumpfun_trending", "Pump.fun trending tokens by market cap", {}),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getPumpfunNew(params: { limit?: number }): Promise<BuildResponse> {
  const limit = Math.min(params.limit ?? 20, 50);
  const data = await fetchPumpData<PumpFunToken[]>(
    `/coins?limit=${limit}&sort=created_timestamp&order=DESC&includeNsfw=false`
  ).catch(() => []);
  return {
    preview: preview("pumpfun_new", "Newest pump.fun token launches", {}),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getPumpfunGraduating(params: { limit?: number }): Promise<BuildResponse> {
  const limit = Math.min(params.limit ?? 20, 50);
  // Fetch by last_reply — these tend to have the most activity near graduation
  const data = await fetchPumpData<PumpFunToken[]>(
    `/coins?limit=${limit}&sort=last_reply&order=DESC&includeNsfw=false`
  ).catch(() => []);
  // Annotate with bonding curve state so frontend can filter by fill %
  const annotated = (data as PumpFunToken[]).map(t => ({
    ...t,
    bondingCurve: calcBondingCurveState(t),
  }));
  return {
    preview: preview("pumpfun_graduating", "Pump.fun tokens near graduation (Raydium migration)", {}),
    additionalSignersRequired: 0, isCrossChain: false, data: annotated,
  };
}

export async function searchPumpfunTokens(params: { query: string; limit?: number }): Promise<BuildResponse> {
  if (!params.query?.trim()) throw appError("query is required");
  const limit = Math.min(params.limit ?? 20, 50);
  const data = await fetchPumpData<PumpFunToken[]>(
    `/coins/search?q=${encodeURIComponent(params.query)}&limit=${limit}&includeNsfw=false`
  ).catch(() => []);
  return {
    preview: preview("pumpfun_search", `Search pump.fun: "${params.query}"`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getPumpfunKingOfHill(_params: Record<string, unknown>): Promise<BuildResponse> {
  const data = await fetchPumpData<PumpFunToken>("/coins/king-of-the-hill?includeNsfw=false").catch(() => null);
  const curveState = data ? calcBondingCurveState(data) : null;
  return {
    preview: preview("pumpfun_koth", "Current Pump.fun King of the Hill", {}),
    additionalSignersRequired: 0, isCrossChain: false,
    data: { token: data, bondingCurve: curveState },
  };
}

export async function getPumpfunTokenComments(params: { mint: string; limit?: number; offset?: number }): Promise<BuildResponse> {
  if (!params.mint) throw appError("mint address required");
  const limit = Math.min(params.limit ?? 25, 100);
  const offset = params.offset ?? 0;
  const data = await fetchPumpData<PumpFunComment[]>(
    `/replies/${params.mint}?limit=${limit}&offset=${offset}`
  ).catch(() => []);
  return {
    preview: preview("pumpfun_comments", `Comments for ${params.mint.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getPumpfunUserProfile(params: { wallet: string }): Promise<BuildResponse> {
  if (!params.wallet) throw appError("wallet address required");
  const data = await fetchPumpData<PumpFunUserProfile>(`/users/${params.wallet}`).catch(() => null);
  return {
    preview: preview("pumpfun_user", `Pump.fun profile: ${params.wallet.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getPumpfunBondingCurve(params: { mint: string }): Promise<BuildResponse> {
  if (!params.mint) throw appError("mint address required");
  const token = await fetchPumpData<PumpFunToken>(`/coins/${params.mint}`).catch(() => null);
  const curveState = token ? calcBondingCurveState(token) : null;
  return {
    preview: preview("pumpfun_bonding_curve", `Bonding curve state for ${params.mint.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false,
    data: { token, bondingCurve: curveState },
  };
}
