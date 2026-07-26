/**
 * Kamino Liquidity — concentrated-liquidity (CLMM) strategy vaults (@kamino-finance/kliquidity-sdk).
 *
 * Distinct from the "Earn" lending vaults (kamino_vault_*): these provide LP into
 * Orca/Raydium CLMM pools via Kamino's auto-managed strategies. The SDK builds
 * @solana/kit (v2) instructions; we compile them into an unsigned base64 v0 tx
 * for the frontend to sign (see utils/kit_tx). Delegated here from the Rust
 * service, which has no Kamino SDK.
 */
import { address, createNoopSigner, type Address } from "@solana/kit";
import { Kamino } from "@kamino-finance/kliquidity-sdk";
import {
  findAssociatedTokenPda,
  getCreateAssociatedTokenIdempotentInstruction,
  TOKEN_PROGRAM_ADDRESS,
} from "@solana-program/token";
import Decimal from "decimal.js/decimal";
import { v4 as uuidv4 } from "uuid";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { kitRpc, buildUnsignedV0Tx } from "../utils/kit_tx";

const KAMINO_API = "https://api.kamino.finance";
// Kamino's API blocks the default Node user-agent (403); send a browser one.
const UA = { "User-Agent": "Mozilla/5.0", Accept: "application/json" };

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return { id: uuidv4(), type, description, estimatedFee: "~0.00002 SOL", params, warnings: [], requiresApproval: false };
}

function kamino() {
  // @ts-ignore kliquidity typings want a narrower Rpc; the full createSolanaRpc satisfies it at runtime.
  return new Kamino("mainnet-beta", kitRpc());
}

async function ataFor(owner: Address, mint: Address, tokenProgram: Address): Promise<Address> {
  const [ata] = await findAssociatedTokenPda({ owner, mint, tokenProgram });
  return ata;
}

export interface KaminoLiquidityDepositParams {
  strategy: string;
  amountA?: string;
  amountB?: string;
}

/** Provide liquidity to a Kamino CLMM strategy (balanced two-token deposit). */
export async function buildKaminoLiquidityDeposit(params: KaminoLiquidityDepositParams, userWallet: string): Promise<BuildResponse> {
  if (!params.strategy) throw appError("strategy is required", 400, "INVALID_PARAMS");
  const k = kamino();
  const owner = createNoopSigner(address(userWallet));
  const ownerAddr = address(userWallet);

  const [swa] = await k.getStrategiesWithAddresses([address(params.strategy)]);
  if (!swa) throw appError("Kamino liquidity strategy not found", 404, "NOT_FOUND");
  const s = swa.strategy;

  // CLMM deposits are ratio-constrained by the current price/range: derive the
  // matching pair from whichever side the user gave.
  let a: Decimal;
  let b: Decimal;
  if (params.amountA && params.amountB) {
    a = new Decimal(params.amountA);
    b = new Decimal(params.amountB);
  } else if (params.amountA) {
    [a, b] = await k.calculateAmountsToBeDeposited(swa, new Decimal(params.amountA), undefined);
  } else if (params.amountB) {
    [a, b] = await k.calculateAmountsToBeDeposited(swa, undefined, new Decimal(params.amountB));
  } else {
    throw appError("amountA or amountB is required", 400, "INVALID_PARAMS");
  }
  if (!a.isFinite() || !b.isFinite() || (a.lte(0) && b.lte(0))) {
    throw appError("Could not compute a valid deposit amount for this strategy", 400, "INVALID_PARAMS");
  }

  // deposit() doesn't create ATAs — prepend idempotent creates for tokenA/B + shares.
  const [ataA, ataB, ataShares] = await Promise.all([
    ataFor(ownerAddr, s.tokenAMint, s.tokenATokenProgram),
    ataFor(ownerAddr, s.tokenBMint, s.tokenBTokenProgram),
    ataFor(ownerAddr, s.sharesMint, TOKEN_PROGRAM_ADDRESS),
  ]);
  const ataIxs = [
    getCreateAssociatedTokenIdempotentInstruction({ payer: owner, ata: ataA, owner: ownerAddr, mint: s.tokenAMint, tokenProgram: s.tokenATokenProgram }),
    getCreateAssociatedTokenIdempotentInstruction({ payer: owner, ata: ataB, owner: ownerAddr, mint: s.tokenBMint, tokenProgram: s.tokenBTokenProgram }),
    getCreateAssociatedTokenIdempotentInstruction({ payer: owner, ata: ataShares, owner: ownerAddr, mint: s.sharesMint, tokenProgram: TOKEN_PROGRAM_ADDRESS }),
  ];

  const depositIx = await k.deposit(swa, a, b, owner);
  const tx = await buildUnsignedV0Tx([...ataIxs, depositIx], userWallet);
  return {
    preview: preview("kamino_liquidity_deposit", `Provide liquidity to Kamino strategy ${params.strategy.slice(0, 4)}…`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export interface KaminoLiquidityWithdrawParams {
  strategy: string;
  shares?: string;
}

/** Withdraw liquidity (redeem shares) from a Kamino CLMM strategy. */
export async function buildKaminoLiquidityWithdraw(params: KaminoLiquidityWithdrawParams, userWallet: string): Promise<BuildResponse> {
  if (!params.strategy) throw appError("strategy is required", 400, "INVALID_PARAMS");
  const k = kamino();
  const owner = createNoopSigner(address(userWallet));

  const [swa] = await k.getStrategiesWithAddresses([address(params.strategy)]);
  if (!swa) throw appError("Kamino liquidity strategy not found", 404, "NOT_FOUND");

  const raw = (params.shares ?? "").trim().toLowerCase();
  const withdrawAll = raw === "" || raw === "all" || raw === "max" || raw === "full";
  const res = withdrawAll
    ? await k.withdrawAllShares(swa, owner)
    : await k.withdrawShares(swa, new Decimal(params.shares!), owner);
  if (!res) throw appError("You have no liquidity position in this strategy", 400, "NOTHING_TO_WITHDRAW");

  const ixs = [...res.prerequisiteIxs, res.withdrawIx, ...(res.closeSharesAtaIx ? [res.closeSharesAtaIx] : [])];
  const tx = await buildUnsignedV0Tx(ixs, userWallet);
  return {
    preview: preview("kamino_liquidity_withdraw", `Withdraw liquidity from Kamino strategy ${params.strategy.slice(0, 4)}…`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

/** List live Kamino CLMM strategies (for discovery / clarify options), TVL-sorted. */
export async function getKaminoLiquidityStrategies(params: { token?: string; limit?: string }): Promise<BuildResponse> {
  const res = await fetch(`${KAMINO_API}/strategies/metrics?env=mainnet-beta&status=LIVE`, { headers: UA });
  if (!res.ok) throw appError("Could not load Kamino liquidity strategies", 502, "KAMINO_ERROR");
  const all = (await res.json()) as any[];

  const token = params.token?.trim().toUpperCase();
  const limit = Math.max(1, Math.min(parseInt(params.limit ?? "8", 10) || 8, 25));
  let list = all
    .filter((s) => s.strategy && (s.tokenAMint || s.tokenBMint))
    .map((s) => ({
      strategy: s.strategy,
      tokenA: s.tokenA,
      tokenB: s.tokenB,
      tokenAMint: s.tokenAMint,
      tokenBMint: s.tokenBMint,
      dex: s.dex,
      poolLabel: s.poolLabel ?? s.strategyType,
      tvlUsd: parseFloat(s.totalValueLocked ?? "0") || 0,
      // apy is a nested object: { vault, kamino, totalApy }. Prefer the
      // Kamino-boosted total, fall back to the base pool apy.
      apyPct: (parseFloat(s.kaminoApy?.totalApy ?? s.apy?.totalApy ?? "0") || 0) * 100,
    }));
  if (token) list = list.filter((s) => s.tokenA?.toUpperCase() === token || s.tokenB?.toUpperCase() === token);
  list.sort((x, y) => y.tvlUsd - x.tvlUsd);
  list = list.slice(0, limit);

  return {
    preview: preview("kamino_liquidity_strategies", "Kamino liquidity strategies", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { strategies: list },
  } as BuildResponse;
}
