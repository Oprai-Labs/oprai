/**
 * Jito — jitoSOL liquid staking via SPL Stake Pool.
 * Stake pool program: SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy
 * Data endpoints: kobe.mainnet.jito.network REST API.
 */

import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

// Official Jito staking API (returns unsigned tx)
const JITO_STAKE_API = "https://kobe.mainnet.jito.network/api/v1";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

async function jitoPost(path: string, body: Record<string, unknown>): Promise<string> {
  const res = await fetch(`${JITO_STAKE_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw appError(`Jito error: ${await res.text()}`, 502, "JITO_ERROR");
  const data = (await res.json()) as { transaction?: string; tx?: string };
  const tx = data.transaction ?? data.tx;
  if (!tx) throw appError("Jito returned no transaction", 502, "JITO_ERROR");
  return tx;
}

export interface JitoStakeParams {
  amount: string; // SOL
}

export async function buildJitoStake(params: JitoStakeParams, userWallet: string): Promise<BuildResponse> {
  const lamports = Math.round(parseFloat(params.amount) * 1e9);
  if (lamports <= 0) throw appError("Amount must be positive");

  const tx = await jitoPost("/staking/deposit", {
    wallet: userWallet,
    lamports: lamports.toString(),
  });

  return {
    preview: preview(
      "jito_stake",
      `Stake ${params.amount} SOL → jitoSOL via Jito`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export interface JitoUnstakeParams {
  amount: string; // jitoSOL
}

export async function buildJitoUnstake(params: JitoUnstakeParams, userWallet: string): Promise<BuildResponse> {
  const lamports = Math.round(parseFloat(params.amount) * 1e9);
  if (lamports <= 0) throw appError("Amount must be positive");

  const tx = await jitoPost("/staking/withdraw", {
    wallet: userWallet,
    lamports: lamports.toString(),
  });

  return {
    preview: preview(
      "jito_unstake",
      `Unstake ${params.amount} jitoSOL → SOL via Jito`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function getJitoStats(_params: Record<string, unknown>): Promise<BuildResponse> {
  const res = await fetch(`${JITO_STAKE_API}/staking/stats`);
  const data = res.ok ? await res.json() : {};
  return {
    preview: preview("jito_stats", "Jito MEV & staking stats", {}),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getJitoRewards(params: { epoch?: number }): Promise<BuildResponse> {
  const url = params.epoch
    ? `${JITO_STAKE_API}/staking/rewards?epoch=${params.epoch}`
    : `${JITO_STAKE_API}/staking/rewards`;
  const res = await fetch(url);
  const data = res.ok ? await res.json() : {};
  return {
    preview: preview("jito_rewards", "Jito MEV rewards", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
