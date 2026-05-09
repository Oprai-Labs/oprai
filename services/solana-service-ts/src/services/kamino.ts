/**
 * Kamino Finance — REST API based.
 *
 * api.kamino.finance/ktx/ returns unsigned base64 transactions directly.
 * No SDK installation required for core lending/vault operations.
 * Data queries use api.kamino.finance/ read endpoints.
 */

import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

const KAMINO_API = "https://api.kamino.finance";
const KAMINO_MAIN_MARKET = "7u3HeL2w1613C9uTdnK9GkfFfByNTYT1Y5MiUBHTrfip";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

async function kaminoTx(
  path: string,
  body: Record<string, unknown>
): Promise<string> {
  const res = await fetch(`${KAMINO_API}/ktx${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.text();
    throw appError(`Kamino error: ${err}`, 502, "KAMINO_ERROR");
  }
  const data = (await res.json()) as { transaction?: string; tx?: string };
  const tx = data.transaction ?? data.tx;
  if (!tx) throw appError("Kamino returned no transaction", 502, "KAMINO_ERROR");
  return tx;
}

// ─────────────────────────────────────────────────────────────────────────────
// K-Lend
// ─────────────────────────────────────────────────────────────────────────────

export interface KaminoLendParams {
  token: string;
  amount: string;
  market?: string;
}

export async function buildKaminoDeposit(params: KaminoLendParams, userWallet: string): Promise<BuildResponse> {
  const market = params.market ?? KAMINO_MAIN_MARKET;
  const tx = await kaminoTx("/klend/deposit", {
    wallet: userWallet,
    market,
    mint: params.token,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_deposit", `Deposit ${params.amount} ${params.token} into Kamino`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildKaminoWithdraw(params: KaminoLendParams, userWallet: string): Promise<BuildResponse> {
  const market = params.market ?? KAMINO_MAIN_MARKET;
  const tx = await kaminoTx("/klend/withdraw", {
    wallet: userWallet,
    market,
    mint: params.token,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_withdraw", `Withdraw ${params.amount} ${params.token} from Kamino`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildKaminoBorrow(params: KaminoLendParams, userWallet: string): Promise<BuildResponse> {
  const market = params.market ?? KAMINO_MAIN_MARKET;
  const tx = await kaminoTx("/klend/borrow", {
    wallet: userWallet,
    market,
    mint: params.token,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_borrow", `Borrow ${params.amount} ${params.token} from Kamino`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildKaminoRepay(params: KaminoLendParams, userWallet: string): Promise<BuildResponse> {
  const market = params.market ?? KAMINO_MAIN_MARKET;
  const tx = await kaminoTx("/klend/repay", {
    wallet: userWallet,
    market,
    mint: params.token,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_repay", `Repay ${params.amount} ${params.token} to Kamino`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// K-Vault (Earn)
// ─────────────────────────────────────────────────────────────────────────────

export interface KaminoVaultParams {
  vault: string;
  amount: string;
}

export async function buildKaminoVaultDeposit(params: KaminoVaultParams, userWallet: string): Promise<BuildResponse> {
  const tx = await kaminoTx("/kvault/deposit", {
    wallet: userWallet,
    vault: params.vault,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_vault_deposit", `Deposit ${params.amount} into Kamino vault`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export async function buildKaminoVaultWithdraw(params: KaminoVaultParams, userWallet: string): Promise<BuildResponse> {
  const tx = await kaminoTx("/kvault/withdraw", {
    wallet: userWallet,
    vault: params.vault,
    amount: params.amount,
  });
  return {
    preview: preview("kamino_vault_withdraw", `Withdraw ${params.amount} from Kamino vault`, params as unknown as Record<string, unknown>),
    transaction: tx, additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries
// ─────────────────────────────────────────────────────────────────────────────

export async function getKaminoMarkets(_params: Record<string, unknown>): Promise<BuildResponse> {
  const res = await fetch(`${KAMINO_API}/kamino-market`);
  const data = await res.json();
  return {
    preview: preview("kamino_markets", "Kamino lending markets", {}),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getKaminoUserPositions(params: { market?: string }, userWallet: string): Promise<BuildResponse> {
  const market = params.market ?? KAMINO_MAIN_MARKET;
  const res = await fetch(
    `${KAMINO_API}/kamino-market/${market}/users/${userWallet}/obligations`
  );
  const data = await res.json();
  return {
    preview: preview("kamino_user_positions", "Kamino user positions", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
