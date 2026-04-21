/**
 * Jupiter DCA — Dollar-Cost Averaging orders.
 * Uses Jupiter DCA REST API / SDK.
 */

import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken } from "./tokens";
import { v4 as uuidv4 } from "uuid";

const JUP_DCA_API = "https://dca-api.jup.ag";
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

export interface DcaParams {
  inputMint: string;
  outputMint: string;
  inAmount: string;   // total input amount
  inAmountPerCycle: string;
  cycleSecondsApart: number; // seconds between cycles
  startAt?: number;   // unix timestamp; omit for immediate
  minOutAmount?: string;
  maxOutAmount?: string;
}

export async function buildDca(params: DcaParams, userWallet: string): Promise<BuildResponse> {
  const inToken = resolveToken(params.inputMint);
  const outToken = resolveToken(params.outputMint);
  const inputMint = inToken?.mint ?? params.inputMint;
  const outputMint = outToken?.mint ?? params.outputMint;
  const decimals = inToken?.decimals ?? 6;

  const res = await fetch(`${JUP_DCA_API}/dca`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      userPublicKey: userWallet,
      inputMint,
      outputMint,
      inAmount: Math.round(parseFloat(params.inAmount) * 10 ** decimals).toString(),
      inAmountPerCycle: Math.round(parseFloat(params.inAmountPerCycle) * 10 ** decimals).toString(),
      cycleSecondsApart: params.cycleSecondsApart,
      startAt: params.startAt ?? null,
      minOutAmountPerCycle: params.minOutAmount ?? null,
      maxOutAmountPerCycle: params.maxOutAmount ?? null,
    }),
  });

  if (!res.ok) throw appError(`DCA create failed: ${await res.text()}`, 502, "DCA_ERROR");
  const data = (await res.json()) as { tx?: string; transaction?: string };
  const tx = data.tx ?? data.transaction;
  if (!tx) throw appError("DCA returned no transaction", 502, "DCA_ERROR");

  const cycleName = params.cycleSecondsApart === 86400 ? "daily"
    : params.cycleSecondsApart === 3600 ? "hourly"
    : `every ${params.cycleSecondsApart}s`;

  return {
    preview: preview(
      "dca",
      `DCA ${params.inAmountPerCycle} ${inToken?.name ?? inputMint} → ${outToken?.name ?? outputMint} ${cycleName}`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildCancelDca(params: { dcaAddress: string }, userWallet: string): Promise<BuildResponse> {
  const res = await fetch(`${JUP_DCA_API}/dca/${params.dcaAddress}`, {
    method: "DELETE",
    headers: headers(),
    body: JSON.stringify({ userPublicKey: userWallet }),
  });
  if (!res.ok) throw appError(`DCA cancel failed: ${await res.text()}`, 502, "DCA_ERROR");
  const data = (await res.json()) as { tx?: string; transaction?: string };
  const tx = data.tx ?? data.transaction;
  if (!tx) throw appError("DCA cancel returned no transaction", 502, "DCA_ERROR");

  return {
    preview: preview("cancel_dca", `Cancel DCA order ${params.dcaAddress.slice(0, 8)}...`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function getDcaOrders(params: { status?: string }, userWallet: string): Promise<BuildResponse> {
  const status = params.status ?? "open";
  const res = await fetch(
    `${JUP_DCA_API}/user/${userWallet}?status=${status}`,
    { headers: headers() }
  );
  const data = res.ok ? await res.json() : [];
  return {
    preview: preview("jup_dca_orders", `DCA orders (${status})`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
