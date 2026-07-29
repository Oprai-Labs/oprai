/**
 * Meteora DAMM v2 (cp-amm) — @meteora-ag/cp-amm-sdk
 *
 * DAMM v2 is a SEPARATE on-chain program from DAMM v1
 * (`cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG`), with its own position
 * accounts. The Rust builders assumed it shared v1's program and read pool
 * fields that the API does not return, so every write against it was
 * addressing the wrong program with the wrong mints.
 *
 * Rather than hand-roll Anchor instructions for a program whose layout we'd be
 * guessing at, everything here goes through Meteora's own SDK: quotes, deposit
 * ratios and account derivation all come from the source of truth.
 *
 * Liquidity is a single opaque `liquidityDelta` (constant-product shares), not
 * a per-bin vector like DLMM — so a position has no range, and "add" always
 * means both sides at the pool's current ratio.
 */

import { CpAmm } from "@meteora-ag/cp-amm-sdk";
import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  VersionedTransaction,
  TransactionMessage,
} from "@solana/web3.js";
import BN from "bn.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

const DAMM_V2_API = "https://damm-v2.datapi.meteora.ag";

function getConnection(): Connection {
  return new Connection(config.solanaRpc, "confirmed");
}

function preview(
  type: string,
  description: string,
  params: Record<string, unknown>,
  estimatedFee = "~0.0003 SOL",
): ActionPreview {
  return { id: uuidv4(), type, description, estimatedFee, params, warnings: [], requiresApproval: true };
}

/**
 * Serialise to a v0 transaction with a fresh blockhash and an unsigned
 * signature slot, matching what the frontend expects from every other action.
 */
async function toV0Base64(
  connection: Connection,
  tx: Transaction,
  payer: PublicKey,
  signers: Keypair[] = [],
): Promise<string> {
  const { blockhash } = await connection.getLatestBlockhash("confirmed");
  const message = new TransactionMessage({
    payerKey: payer,
    recentBlockhash: blockhash,
    instructions: tx.instructions,
  }).compileToV0Message();
  const vtx = new VersionedTransaction(message);
  // Signers we hold — the position NFT mint. Signing the LEGACY transaction
  // instead fails outright ("recentBlockhash required") and would be thrown
  // away anyway: compiling to v0 builds a new message, so any signature over
  // the old one no longer applies. The wallet fills the fee-payer slot.
  if (signers.length) vtx.sign(signers);
  return Buffer.from(vtx.serialize()).toString("base64");
}

/**
 * The pool-state fields every quote needs: the reserves and total liquidity it
 * prices against, plus the fee mode (which decides whether fees come off the
 * input or the output). Passing them explicitly is what makes a quote reflect
 * THIS pool rather than a generic curve.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const poolQuoteBasis = (state: any) => ({
  collectFeeMode: state.collectFeeMode,
  tokenAAmount: state.tokenAAmount,
  tokenBAmount: state.tokenBAmount,
  liquidity: state.liquidity,
  sqrtPrice: state.sqrtPrice,
  minSqrtPrice: state.sqrtMinPrice,
  maxSqrtPrice: state.sqrtMaxPrice,
});

const toNum = (v: unknown, decimals: number): number => {
  const n = Number(v?.toString() ?? "0");
  return Number.isFinite(n) ? n / Math.pow(10, decimals) : 0;
};

/** Pool metadata from the data API — symbols, decimals and logos for display. */
async function fetchPoolMeta(pool: string): Promise<Record<string, any> | null> {
  try {
    const res = await fetch(`${DAMM_V2_API}/pools/${pool}`, {
      headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 Chrome/150.0" },
    });
    if (!res.ok) return null;
    return (await res.json()) as Record<string, any>;
  } catch {
    return null;
  }
}

interface PoolTokens {
  mintA: PublicKey;
  mintB: PublicKey;
  decA: number;
  decB: number;
  symA: string;
  symB: string;
  logoA: string | null;
  logoB: string | null;
}

async function resolvePoolTokens(
  amm: CpAmm,
  connection: Connection,
  pool: PublicKey,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  state: any,
): Promise<PoolTokens> {
  const meta = await fetchPoolMeta(pool.toBase58());
  const x = meta?.["token_x"] ?? {};
  const y = meta?.["token_y"] ?? {};
  // Decimals come from the mints when the API is unavailable — they decide
  // amounts, so guessing 9/6 would silently misprice the deposit.
  let decA = Number(x.decimals);
  let decB = Number(y.decimals);
  if (!Number.isFinite(decA) || !Number.isFinite(decB)) {
    const infos = await connection.getMultipleParsedAccounts([state.tokenAMint, state.tokenBMint]);
    decA = (infos.value[0]?.data as any)?.parsed?.info?.decimals ?? 9;
    decB = (infos.value[1]?.data as any)?.parsed?.info?.decimals ?? 9;
  }
  return {
    mintA: state.tokenAMint,
    mintB: state.tokenBMint,
    decA,
    decB,
    symA: x.symbol ?? "?",
    symB: y.symbol ?? "?",
    logoA: x.logo_uri ?? x.logoURI ?? null,
    logoB: y.logo_uri ?? y.logoURI ?? null,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Read: the wallet's positions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Every DAMM v2 position the wallet holds, grouped by pool.
 *
 * The data API has no positions endpoint at all (every /portfolio and
 * /positions shape 404s), so this is the only route to them — and it is the
 * better one anyway: the SDK reads the position accounts directly, so the
 * amounts and unclaimed fees are the chain's, not a cache's.
 */
export async function getDammV2UserPositions(
  _params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const raw = await amm.getPositionsByUser(new PublicKey(userWallet));

  const byPool = new Map<string, any[]>();
  for (const p of raw) {
    const pool = (p as any).positionState.pool.toBase58();
    if (!byPool.has(pool)) byPool.set(pool, []);
    byPool.get(pool)!.push(p);
  }

  const pools = [];
  for (const [poolAddress, entries] of byPool) {
    const poolPk = new PublicKey(poolAddress);
    const state = await amm.fetchPoolState(poolPk);
    const t = await resolvePoolTokens(amm, connection, poolPk, state);

    const positions = entries.map(e => {
      const ps = e.positionState;
      // What the shares are worth right now, priced by the SDK against the
      // pool's current reserves — not a stored deposit amount, which drifts
      // the moment the price moves.
      const quote = amm.getWithdrawQuote({
        liquidityDelta: ps.unlockedLiquidity,
        ...poolQuoteBasis(state),
      });
      return {
        address: e.position.toBase58(),
        positionNftAccount: e.positionNftAccount.toBase58(),
        // X/Y naming, not A/B: the positions card is shared with DLMM and
        // reads amountX / unclaimedFeeX. Emitting A/B left every amount blank
        // on screen while the totals above them were right.
        amountX: toNum(quote.outAmountA, t.decA),
        amountY: toNum(quote.outAmountB, t.decB),
        unclaimedFeeX: toNum(ps.feeAPending, t.decA),
        unclaimedFeeY: toNum(ps.feeBPending, t.decB),
        // A locked position can't be withdrawn or closed until it vests; the
        // card has to say so rather than offering buttons that will fail.
        locked: !(ps.vestedLiquidity as BN).isZero() || !(ps.permanentLockedLiquidity as BN).isZero(),
        permanentlyLocked: !(ps.permanentLockedLiquidity as BN).isZero(),
      };
    });

    pools.push({
      poolAddress,
      tokenX: t.symA,
      tokenY: t.symB,
      tokenXMint: t.mintA.toBase58(),
      tokenYMint: t.mintB.toBase58(),
      tokenXIcon: t.logoA,
      tokenYIcon: t.logoB,
      tokenXDecimals: t.decA,
      tokenYDecimals: t.decB,
      openPositionCount: positions.length,
      listPositions: positions.map(p => p.address),
      balances: positions.reduce((s, p) => s + p.amountX, 0),
      unclaimedFees: positions.reduce((s, p) => s + p.unclaimedFeeX, 0),
      positions,
    });
  }

  return {
    preview: {
      id: uuidv4(),
      type: "meteora_dammv2_get_user_positions",
      description: `Meteora DAMM v2 positions for ${userWallet.slice(0, 8)}…`,
      estimatedFee: "0",
      params: {},
      warnings: [],
      requiresApproval: false,
    },
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { pools },
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Writes
// ─────────────────────────────────────────────────────────────────────────────

export interface DammV2AddParams {
  pool: string;
  position?: string;
  amountX?: string;
  amountY?: string;
  slippageBps?: number;
}

/**
 * Deposit into a pool. With no `position`, this opens one — DAMM v2 positions
 * are NFT-backed, so opening requires an extra signer for the mint.
 *
 * Only one side needs an amount: the pool's ratio fixes the other, and the SDK
 * computes it. Typing both and having them disagree is the common way to get a
 * deposit that reverts.
 */
export async function buildDammV2AddLiquidity(
  params: DammV2AddParams,
  userWallet: string,
): Promise<BuildResponse> {
  if (!params.pool) throw appError("pool is required", 400, "INVALID_PARAMS");
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const user = new PublicKey(userWallet);
  const poolPk = new PublicKey(params.pool);
  const state = await amm.fetchPoolState(poolPk);
  const t = await resolvePoolTokens(amm, connection, poolPk, state);

  const amtA = parseFloat(params.amountX ?? "0") || 0;
  const amtB = parseFloat(params.amountY ?? "0") || 0;
  if (amtA <= 0 && amtB <= 0) {
    throw appError("Enter an amount for either token", 400, "INVALID_PARAMS");
  }
  const isA = amtA > 0;
  const inAmount = new BN(
    Math.round((isA ? amtA : amtB) * Math.pow(10, isA ? t.decA : t.decB)),
  );

  const deposit = amm.getDepositQuote({
    inAmount,
    isTokenA: isA,
    ...poolQuoteBasis(state),
  });

  const slippage = (params.slippageBps ?? 100) / 10_000;
  const pad = (v: BN) => v.muln(Math.round((1 + slippage) * 1000)).divn(1000);

  let tx: Transaction;
  const localSigners: Keypair[] = [];
  if (params.position) {
    tx = await amm.addLiquidity({
      owner: user,
      pool: poolPk,
      position: new PublicKey(params.position),
      positionNftAccount: await resolveNftAccount(amm, user, params.position),
      liquidityDelta: deposit.liquidityDelta,
      maxAmountTokenA: pad(deposit.actualInputAmount ?? inAmount),
      maxAmountTokenB: pad(deposit.outputAmount),
      tokenAAmountThreshold: pad(deposit.actualInputAmount ?? inAmount),
      tokenBAmountThreshold: pad(deposit.outputAmount),
      tokenAMint: t.mintA,
      tokenBMint: t.mintB,
      tokenAVault: state.tokenAVault,
      tokenBVault: state.tokenBVault,
      tokenAProgram: (await connection.getAccountInfo(t.mintA))!.owner,
      tokenBProgram: (await connection.getAccountInfo(t.mintB))!.owner,
    });
  } else {
    // A DAMM v2 position is NFT-backed, so opening one mints a fresh NFT that
    // must sign. We generate and sign it here — there is no user secret in it.
    const positionNft = Keypair.generate();
    tx = await amm.createPositionAndAddLiquidity({
      owner: user,
      pool: poolPk,
      positionNft: positionNft.publicKey,
      liquidityDelta: deposit.liquidityDelta,
      maxAmountTokenA: pad(deposit.actualInputAmount ?? inAmount),
      maxAmountTokenB: pad(deposit.outputAmount),
      tokenAAmountThreshold: pad(deposit.actualInputAmount ?? inAmount),
      tokenBAmountThreshold: pad(deposit.outputAmount),
      tokenAMint: t.mintA,
      tokenBMint: t.mintB,
      tokenAProgram: (await connection.getAccountInfo(t.mintA))!.owner,
      tokenBProgram: (await connection.getAccountInfo(t.mintB))!.owner,
    });
    localSigners.push(positionNft);
  }

  return {
    preview: preview(
      "meteora_dammv2_add_liquidity",
      params.position
        ? `Add liquidity to DAMM v2 position ${params.position.slice(0, 8)}…`
        : `Open a DAMM v2 position in ${t.symA}/${t.symB}`,
      { ...params, pairedAmount: toNum(deposit.outputAmount, isA ? t.decB : t.decA) },
    ),
    transaction: await toV0Base64(connection, tx, user, localSigners),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      pairedAmount: toNum(deposit.outputAmount, isA ? t.decB : t.decA),
      pairedSymbol: isA ? t.symB : t.symA,
    },
  };
}

/** The NFT token account that proves ownership of a position. */
async function resolveNftAccount(
  amm: CpAmm,
  user: PublicKey,
  position: string,
): Promise<PublicKey> {
  const all = await amm.getPositionsByUser(user);
  const hit = all.find(p => (p as any).position.toBase58() === position);
  if (!hit) throw appError("Position not found for this wallet", 404, "POSITION_NOT_FOUND");
  return (hit as any).positionNftAccount;
}

export interface DammV2PositionParams {
  position: string;
  pool?: string;
  bpsToRemove?: number;
  slippageBps?: number;
}

async function positionContext(amm: CpAmm, connection: Connection, user: PublicKey, position: string) {
  const all = await amm.getPositionsByUser(user);
  const hit = all.find(p => (p as any).position.toBase58() === position);
  if (!hit) throw appError("Position not found for this wallet", 404, "POSITION_NOT_FOUND");
  const h = hit as any;
  const poolPk = h.positionState.pool as PublicKey;
  const state = await amm.fetchPoolState(poolPk);
  const t = await resolvePoolTokens(amm, connection, poolPk, state);
  return { h, poolPk, state, t };
}

/** Withdraw a share of a position. 10000 bps = everything. */
export async function buildDammV2RemoveLiquidity(
  params: DammV2PositionParams,
  userWallet: string,
): Promise<BuildResponse> {
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const user = new PublicKey(userWallet);
  const { h, poolPk, state, t } = await positionContext(amm, connection, user, params.position);

  const bps = params.bpsToRemove ?? 10_000;
  const unlocked: BN = h.positionState.unlockedLiquidity;
  if (unlocked.isZero()) {
    throw appError(
      "This position has no withdrawable liquidity — it is locked or already empty.",
      400,
      "NOTHING_TO_WITHDRAW",
    );
  }
  const delta = bps >= 10_000 ? unlocked : unlocked.muln(bps).divn(10_000);
  const quote = amm.getWithdrawQuote({
    liquidityDelta: delta,
    ...poolQuoteBasis(state),
  });
  const slip = (params.slippageBps ?? 100) / 10_000;
  const floor = (v: BN) => v.muln(Math.round((1 - slip) * 1000)).divn(1000);

  const tx = await amm.removeLiquidity({
    owner: user,
    pool: poolPk,
    position: h.position,
    positionNftAccount: h.positionNftAccount,
    liquidityDelta: delta,
    tokenAAmountThreshold: floor(quote.outAmountA),
    tokenBAmountThreshold: floor(quote.outAmountB),
    tokenAMint: t.mintA,
    tokenBMint: t.mintB,
    tokenAVault: state.tokenAVault,
    tokenBVault: state.tokenBVault,
    tokenAProgram: (await connection.getAccountInfo(t.mintA))!.owner,
    tokenBProgram: (await connection.getAccountInfo(t.mintB))!.owner,
    vestings: [],
    currentPoint: new BN(0),
  });

  return {
    preview: preview(
      "meteora_dammv2_remove_liquidity",
      `Withdraw ${bps / 100}% from DAMM v2 position ${params.position.slice(0, 8)}…`,
      params as unknown as Record<string, unknown>,
    ),
    transaction: await toV0Base64(connection, tx, user),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      receiveA: toNum(quote.outAmountA, t.decA),
      receiveB: toNum(quote.outAmountB, t.decB),
      symbolA: t.symA,
      symbolB: t.symB,
    },
  };
}

/** Collect the trading fees a position has accrued, leaving it open. */
export async function buildDammV2ClaimFee(
  params: DammV2PositionParams,
  userWallet: string,
): Promise<BuildResponse> {
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const user = new PublicKey(userWallet);
  const { h, poolPk, state, t } = await positionContext(amm, connection, user, params.position);

  const tx = await amm.claimPositionFee({
    owner: user,
    receiver: user,
    pool: poolPk,
    position: h.position,
    positionNftAccount: h.positionNftAccount,
    tokenAMint: t.mintA,
    tokenBMint: t.mintB,
    tokenAVault: state.tokenAVault,
    tokenBVault: state.tokenBVault,
    tokenAProgram: (await connection.getAccountInfo(t.mintA))!.owner,
    tokenBProgram: (await connection.getAccountInfo(t.mintB))!.owner,
  });

  return {
    preview: preview(
      "meteora_dammv2_claim_fee",
      `Claim fees from DAMM v2 position ${params.position.slice(0, 8)}…`,
      params as unknown as Record<string, unknown>,
    ),
    transaction: await toV0Base64(connection, tx, user),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      feeA: toNum(h.positionState.feeAPending, t.decA),
      feeB: toNum(h.positionState.feeBPending, t.decB),
      symbolA: t.symA,
      symbolB: t.symB,
    },
  };
}

/**
 * Withdraw everything, claim the fees and close the position account in one
 * transaction — the only path that returns the position's rent.
 */
export async function buildDammV2ClosePosition(
  params: DammV2PositionParams,
  userWallet: string,
): Promise<BuildResponse> {
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const user = new PublicKey(userWallet);
  const { h, poolPk, state, t } = await positionContext(amm, connection, user, params.position);

  const tx = await amm.removeAllLiquidityAndClosePosition({
    owner: user,
    position: h.position,
    positionNftAccount: h.positionNftAccount,
    positionState: h.positionState,
    poolState: state,
    tokenAAmountThreshold: new BN(0),
    tokenBAmountThreshold: new BN(0),
    vestings: [],
    currentPoint: new BN(0),
  });

  const quote = amm.getWithdrawQuote({
    liquidityDelta: h.positionState.unlockedLiquidity,
    ...poolQuoteBasis(state),
  });

  return {
    preview: preview(
      "meteora_dammv2_close_position",
      `Close DAMM v2 position ${params.position.slice(0, 8)}… (withdraw + claim + recover rent)`,
      params as unknown as Record<string, unknown>,
    ),
    transaction: await toV0Base64(connection, tx, user),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      receiveA: toNum(quote.outAmountA, t.decA),
      receiveB: toNum(quote.outAmountB, t.decB),
      feeA: toNum(h.positionState.feeAPending, t.decA),
      feeB: toNum(h.positionState.feeBPending, t.decB),
      symbolA: t.symA,
      symbolB: t.symB,
    },
  };
}

export interface DammV2SwapParams {
  pool: string;
  inputMint: string;
  amount: string;
  slippageBps?: number;
}

/** Swap through one DAMM v2 pool, priced by the SDK's own quote. */
export async function buildDammV2Swap(
  params: DammV2SwapParams,
  userWallet: string,
): Promise<BuildResponse> {
  const connection = getConnection();
  const amm = new CpAmm(connection);
  const user = new PublicKey(userWallet);
  const poolPk = new PublicKey(params.pool);
  const state = await amm.fetchPoolState(poolPk);
  const t = await resolvePoolTokens(amm, connection, poolPk, state);

  const inputIsA = params.inputMint === t.mintA.toBase58();
  const decIn = inputIsA ? t.decA : t.decB;
  const amountIn = new BN(Math.round(parseFloat(params.amount) * Math.pow(10, decIn)));
  const slot = await connection.getSlot();
  const blockTime = (await connection.getBlockTime(slot)) ?? 0;

  const quote = amm.getQuote({
    inAmount: amountIn,
    inputTokenMint: new PublicKey(params.inputMint),
    slippage: (params.slippageBps ?? 50) / 100,
    poolState: state,
    currentTime: blockTime,
    currentSlot: slot,
    tokenADecimal: t.decA,
    tokenBDecimal: t.decB,
  });

  const tx = await amm.swap({
    payer: user,
    pool: poolPk,
    inputTokenMint: inputIsA ? t.mintA : t.mintB,
    outputTokenMint: inputIsA ? t.mintB : t.mintA,
    amountIn,
    minimumAmountOut: quote.minSwapOutAmount,
    tokenAMint: t.mintA,
    tokenBMint: t.mintB,
    tokenAVault: state.tokenAVault,
    tokenBVault: state.tokenBVault,
    tokenAProgram: (await connection.getAccountInfo(t.mintA))!.owner,
    tokenBProgram: (await connection.getAccountInfo(t.mintB))!.owner,
    referralTokenAccount: null,
  });

  return {
    preview: preview(
      "meteora_dammv2_swap",
      `Swap ${params.amount} ${inputIsA ? t.symA : t.symB} on DAMM v2`,
      params as unknown as Record<string, unknown>,
      "~0.00005 SOL",
    ),
    transaction: await toV0Base64(connection, tx, user),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      outAmount: toNum(quote.swapOutAmount, inputIsA ? t.decB : t.decA),
      minOutAmount: toNum(quote.minSwapOutAmount, inputIsA ? t.decB : t.decA),
      outSymbol: inputIsA ? t.symB : t.symA,
    },
  };
}
