/**
 * Orca Whirlpools — @orca-so/whirlpools-sdk
 *
 * Swaps are routed through Jupiter (dexes=Whirlpool).
 * Position management uses the SDK via lazy dynamic imports
 * to avoid Anchor version conflict at startup.
 * Pool data uses the Orca v2 public REST API.
 */

import { Connection, PublicKey, Transaction } from "@solana/web3.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken } from "./tokens";
import { v4 as uuidv4 } from "uuid";

const ORCA_V2_API = "https://api.orca.so/v2/solana";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

/* Lazy-load the Orca SDK to avoid Anchor version conflict at startup */
async function getOrcaSdk() {
  const sdk = await import("@orca-so/whirlpools-sdk") as any;
  const common = await import("@orca-so/common-sdk") as any;
  const BN = (await import("bn.js")).default;
  const Decimal = (await import("decimal.js")).default;
  const { Keypair } = await import("@solana/web3.js");
  return { sdk, common, BN, Decimal, Keypair };
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function getContext(userWallet: string, sdk: any): any {
  const connection = new Connection(config.solanaRpc, "confirmed");
  const publicKey = new PublicKey(userWallet);
  const fakeWallet: any = {
    publicKey,
    signTransaction: async (tx: any) => tx,
    signAllTransactions: async (txs: any[]) => txs,
  };
  return sdk.WhirlpoolContext.from(connection, fakeWallet);
}

function serializeTxBuilder(builtTx: any): string {
  const tx = builtTx?.transaction ?? builtTx;
  if (!tx) throw appError("No transaction returned from SDK", 500, "SERIALIZE_ERROR");
  return (tx as Transaction).serialize({ requireAllSignatures: false }).toString("base64");
}

// ─────────────────────────────────────────────────────────────────────────────
// Swap — routed through Jupiter with Whirlpool filter
// ─────────────────────────────────────────────────────────────────────────────

export interface OrcaSwapParams {
  inputMint: string;
  outputMint: string;
  amount: string;
  slippageBps?: number;
}

export async function buildOrcaSwap(params: OrcaSwapParams, userWallet: string): Promise<BuildResponse> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;
  const decimals = inToken?.decimals ?? 9;
  const amount = Math.round(parseFloat(params.amount) * 10 ** decimals).toString();

  const apiKey = config.jupiterApiKey;
  const baseUrl = apiKey ? "https://api.jup.ag/swap/v1" : "https://quote-api.jup.ag/v6";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["x-api-key"] = apiKey;

  const quoteRes = await fetch(
    `${baseUrl}/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${params.slippageBps ?? 50}&dexes=Whirlpool`,
    { headers }
  );
  if (!quoteRes.ok) throw appError(`Orca/Jupiter quote failed: ${await quoteRes.text()}`, 502, "ORCA_ERROR");
  const quote = await quoteRes.json();

  const swapRes = await fetch(`${baseUrl}/swap`, {
    method: "POST",
    headers,
    body: JSON.stringify({ quoteResponse: quote, userPublicKey: userWallet, wrapAndUnwrapSol: true }),
  });
  if (!swapRes.ok) throw appError(`Orca swap build failed: ${await swapRes.text()}`, 502, "ORCA_ERROR");
  const { swapTransaction } = (await swapRes.json()) as { swapTransaction: string };

  return {
    preview: preview(
      "orca_swap",
      `Swap ${params.amount} ${inToken?.name ?? params.inputMint} → ${outToken?.name ?? params.outputMint} via Orca`,
      params as unknown as Record<string, unknown>
    ),
    transaction: swapTransaction, additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Position management
// ─────────────────────────────────────────────────────────────────────────────

export interface OrcaOpenPositionParams {
  poolAddress: string;
  tokenAAmount: number;
  lowerPrice: number;
  upperPrice: number;
  slippageBps?: number;
}

export async function buildOrcaOpenPosition(params: OrcaOpenPositionParams, userWallet: string): Promise<BuildResponse> {
  const { sdk, BN, Decimal, Keypair } = await getOrcaSdk();
  const ctx = getContext(userWallet, sdk);
  const client = sdk.buildWhirlpoolClient(ctx);
  const pool: any = await client.getPool(new PublicKey(params.poolAddress));
  const poolData = pool.getData();

  const lowerTick = sdk.PriceMath.priceToInitializableTickIndex(
    new Decimal(params.lowerPrice), poolData.tokenMintA, poolData.tokenMintB, poolData.tickSpacing
  );
  const upperTick = sdk.PriceMath.priceToInitializableTickIndex(
    new Decimal(params.upperPrice), poolData.tokenMintA, poolData.tokenMintB, poolData.tickSpacing
  );

  const positionMintKeypair = Keypair.generate();

  const { tx }: any = await pool.openPosition(
    lowerTick, upperTick,
    { tokenA: new BN(params.tokenAAmount), tokenB: new BN(0) },
    new PublicKey(userWallet), new PublicKey(userWallet),
    undefined, positionMintKeypair
  );

  const builtTx = await tx.build();
  return {
    preview: preview(
      "orca_open_position",
      `Open Orca position [${params.lowerPrice} - ${params.upperPrice}]`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeTxBuilder(builtTx),
    additionalSignersRequired: 1,
    isCrossChain: false,
  };
}

export async function buildOrcaClosePosition(params: { positionAddress: string; slippageBps?: number }, userWallet: string): Promise<BuildResponse> {
  const { sdk, common } = await getOrcaSdk();
  const ctx = getContext(userWallet, sdk);
  const client = sdk.buildWhirlpoolClient(ctx);
  const slippage = common.Percentage.fromFraction(params.slippageBps ?? 100, 10000);
  const position = await client.getPosition(new PublicKey(params.positionAddress));
  const pool: any = await client.getPool(position.getData().whirlpool);

  const result: any = await pool.closePosition(
    new PublicKey(params.positionAddress), slippage, new PublicKey(userWallet)
  );
  const txBuilders: any[] = result.txs ?? (Array.isArray(result) ? result : [result]);
  const builtTx = await txBuilders[0].build();
  return {
    preview: preview("orca_close_position", `Close Orca position ${params.positionAddress.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: serializeTxBuilder(builtTx),
    additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildOrcaCollectFees(params: { positionAddress: string }, userWallet: string): Promise<BuildResponse> {
  const { sdk } = await getOrcaSdk();
  const ctx = getContext(userWallet, sdk);
  const client = sdk.buildWhirlpoolClient(ctx);
  const position = await client.getPosition(new PublicKey(params.positionAddress));
  const pool: any = await client.getPool(position.getData().whirlpool);

  const result: any = await pool.collectFees(
    new PublicKey(params.positionAddress), new PublicKey(userWallet)
  );
  const builtTx = await (result.tx ?? result).build();
  return {
    preview: preview("orca_collect_fees", `Collect Orca fees for ${params.positionAddress.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: serializeTxBuilder(builtTx),
    additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries
// ─────────────────────────────────────────────────────────────────────────────

export async function getOrcaPools(_params: Record<string, unknown>): Promise<BuildResponse> {
  const res = await fetch(`${ORCA_V2_API}/pools`);
  const data = await res.json();
  return {
    preview: preview("orca_get_pools", "Orca Whirlpool pools", {}),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
