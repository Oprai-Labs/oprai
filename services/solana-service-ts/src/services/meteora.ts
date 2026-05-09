/**
 * Meteora DLMM — @meteora-ag/dlmm SDK
 *
 * Dynamic Liquidity Market Maker (bin-based concentrated liquidity).
 */

import DLMM from "@meteora-ag/dlmm";
import {
  Connection,
  PublicKey,
  Transaction,
  VersionedTransaction,
} from "@solana/web3.js";
import BN from "bn.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

const METEORA_API = "https://dlmm-api.meteora.ag";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

function getConnection(): Connection {
  return new Connection(config.solanaRpc, "confirmed");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function serializeTx(tx: any): string {
  if (!tx) throw appError("No transaction returned", 500, "SERIALIZE_ERROR");
  const item = Array.isArray(tx) ? tx[0] : tx;
  if ("message" in item) {
    return Buffer.from((item as VersionedTransaction).serialize()).toString("base64");
  }
  return (item as Transaction).serialize({ requireAllSignatures: false }).toString("base64");
}

// ─────────────────────────────────────────────────────────────────────────────
// Add / Remove Liquidity
// ─────────────────────────────────────────────────────────────────────────────

export interface MeteoraAddLiquidityParams {
  poolAddress: string;
  tokenXAmount: string;
  tokenYAmount: string;
  strategy?: string;
  slippageBps?: number;
}

export async function buildMeteoraAddLiquidity(params: MeteoraAddLiquidityParams, userWallet: string): Promise<BuildResponse> {
  const connection = getConnection();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const dlmmPool: any = await (DLMM as any).create(connection, new PublicKey(params.poolAddress));
  const user = new PublicKey(userWallet);

  const activeBin = await dlmmPool.getActiveBin();
  const minBinId = activeBin.binId - 10;
  const maxBinId = activeBin.binId + 10;

  const tokenXDecimals = dlmmPool.tokenX.decimal ?? 9;
  const tokenYDecimals = dlmmPool.tokenY.decimal ?? 6;
  const xAmount = new BN(Math.round(parseFloat(params.tokenXAmount) * 10 ** tokenXDecimals));
  const yAmount = new BN(Math.round(parseFloat(params.tokenYAmount) * 10 ** tokenYDecimals));

  const strategyType = params.strategy === "curve" ? 1 : params.strategy === "bid_ask" ? 2 : 0;

  const createPositionTx = await dlmmPool.initializePositionAndAddLiquidityByStrategy({
    positionPubKey: PublicKey.unique(),
    user,
    totalXAmount: xAmount,
    totalYAmount: yAmount,
    strategy: { maxBinId, minBinId, strategyType },
  });

  return {
    preview: preview(
      "meteora_add_liquidity",
      `Add liquidity to Meteora pool ${params.poolAddress.slice(0, 8)}...`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeTx(createPositionTx),
    additionalSignersRequired: 1,
    isCrossChain: false,
  };
}

export interface MeteoraRemoveLiquidityParams {
  positionAddress: string;
  poolAddress: string;
  bpsToRemove?: number;
}

export async function buildMeteoraRemoveLiquidity(params: MeteoraRemoveLiquidityParams, userWallet: string): Promise<BuildResponse> {
  const connection = getConnection();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const dlmmPool: any = await (DLMM as any).create(connection, new PublicKey(params.poolAddress));
  const user = new PublicKey(userWallet);
  const positionPublicKey = new PublicKey(params.positionAddress);

  const { userPositions } = await dlmmPool.getPositionsByUserAndLbPair(user);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const position = userPositions.find((p: any) => p.publicKey.equals(positionPublicKey));
  if (!position) throw appError("Position not found", 404, "POSITION_NOT_FOUND");

  const binIds = position.positionData.positionBinData.map((bin: { binId: number }) => bin.binId);
  const bpsToRemove = new BN(params.bpsToRemove ?? 10000);

  const removeTx = await dlmmPool.removeLiquidity({
    user,
    position: positionPublicKey,
    fromBinId: Math.min(...binIds),
    toBinId: Math.max(...binIds),
    bps: bpsToRemove,
    shouldClaimAndClose: true,
  });

  return {
    preview: preview(
      "meteora_remove_liquidity",
      `Remove liquidity from Meteora position ${params.positionAddress.slice(0, 8)}...`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeTx(removeTx),
    additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildMeteoraClaimFees(params: { positionAddress: string; poolAddress: string }, userWallet: string): Promise<BuildResponse> {
  const connection = getConnection();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const dlmmPool: any = await (DLMM as any).create(connection, new PublicKey(params.poolAddress));
  const user = new PublicKey(userWallet);

  const claimTx = await dlmmPool.claimAllSwapFee({
    owner: user,
    positions: [{ publicKey: new PublicKey(params.positionAddress) }],
  });

  return {
    preview: preview("meteora_claim_fees", `Claim Meteora fees for ${params.positionAddress.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: serializeTx(claimTx),
    additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries
// ─────────────────────────────────────────────────────────────────────────────

export async function getMeteoraPools(params: { tokenA?: string; tokenB?: string }): Promise<BuildResponse> {
  let url = `${METEORA_API}/pair/all_with_pagination?limit=100&sort_key=tvl&order_by=desc`;
  if (params.tokenA) url += `&tokenAMint=${params.tokenA}`;
  if (params.tokenB) url += `&tokenBMint=${params.tokenB}`;
  const res = await fetch(url);
  const data = await res.json();
  return {
    preview: preview("meteora_get_pools", "Meteora DLMM pools", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getMeteoraUserPositions(params: { poolAddress?: string }, userWallet: string): Promise<BuildResponse> {
  const res = await fetch(`${METEORA_API}/position/user/${userWallet}`);
  const data = await res.json();
  return {
    preview: preview("meteora_user_positions", "Meteora user positions", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
