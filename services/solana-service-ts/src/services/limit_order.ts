/**
 * Jupiter Limit Orders — REST API.
 */

import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken } from "./tokens";
import { v4 as uuidv4 } from "uuid";

const LIMIT_API = "https://api.jup.ag/limit/v2";
const API_KEY = config.jupiterApiKey;

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) h["x-api-key"] = API_KEY;
  return h;
}

export interface LimitOrderParams {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;   // limit price expressed as total out amount
  expiredAt?: number;  // unix timestamp; null = never
}

export async function buildLimitOrder(params: LimitOrderParams, userWallet: string): Promise<BuildResponse> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;

  const inDecimals = inToken?.decimals ?? 6;
  const outDecimals = outToken?.decimals ?? 6;

  const res = await fetch(`${LIMIT_API}/createOrder`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      maker: userWallet,
      payer: userWallet,
      inputMint,
      outputMint,
      params: {
        makingAmount: Math.round(parseFloat(params.inAmount) * 10 ** inDecimals).toString(),
        takingAmount: Math.round(parseFloat(params.outAmount) * 10 ** outDecimals).toString(),
        expiredAt: params.expiredAt?.toString() ?? null,
      },
    }),
  });

  if (!res.ok) throw appError(`Limit order create failed: ${await res.text()}`, 502, "LIMIT_ORDER_ERROR");
  const data = (await res.json()) as { tx?: string; transaction?: string };
  const tx = data.tx ?? data.transaction;
  if (!tx) throw appError("Limit order returned no transaction", 502, "LIMIT_ORDER_ERROR");

  return {
    preview: preview(
      "limit_order",
      `Limit order: sell ${params.inAmount} ${inToken?.name ?? inputMint} for ${params.outAmount} ${outToken?.name ?? outputMint}`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildCancelLimitOrder(params: { orderAddress: string }, userWallet: string): Promise<BuildResponse> {
  const res = await fetch(`${LIMIT_API}/cancelOrders`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      maker: userWallet,
      orders: [params.orderAddress],
    }),
  });
  if (!res.ok) throw appError(`Cancel limit order failed: ${await res.text()}`, 502, "LIMIT_ORDER_ERROR");
  const data = (await res.json()) as { txs?: string[]; transactions?: string[] };
  const txs = data.txs ?? data.transactions ?? [];
  return {
    preview: preview("cancel_limit_order", `Cancel limit order ${params.orderAddress.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: txs[0], additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildCancelAllLimitOrders(_params: Record<string, unknown>, userWallet: string): Promise<BuildResponse> {
  // Fetch open orders first
  const ordersRes = await fetch(`${LIMIT_API}/openOrders?wallet=${userWallet}`, { headers: headers() });
  const orders = ordersRes.ok ? (await ordersRes.json()) as { publicKey: string }[] : [];
  if (orders.length === 0) {
    throw appError("No open limit orders to cancel", 400, "NO_ORDERS");
  }

  const res = await fetch(`${LIMIT_API}/cancelOrders`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      maker: userWallet,
      orders: orders.map((o) => o.publicKey),
    }),
  });
  if (!res.ok) throw appError(`Cancel all limit orders failed: ${await res.text()}`, 502, "LIMIT_ORDER_ERROR");
  const data = (await res.json()) as { txs?: string[] };
  return {
    preview: preview("cancel_all_limit_orders", `Cancel all ${orders.length} limit orders`, {}),
    transaction: data.txs?.[0], additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function getLimitOrders(params: { status?: string }, userWallet: string): Promise<BuildResponse> {
  const status = params.status ?? "open";
  const endpoint = status === "open" ? "openOrders" : "orderHistory";
  const res = await fetch(`${LIMIT_API}/${endpoint}?wallet=${userWallet}`, { headers: headers() });
  const data = res.ok ? await res.json() : [];
  return {
    preview: preview("jup_limit_orders", `Limit orders (${status})`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
