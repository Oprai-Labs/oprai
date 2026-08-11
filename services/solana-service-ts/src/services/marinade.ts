/**
 * Marinade Finance — @marinade.finance/marinade-ts-sdk v5
 *
 * Full integration covering all SDK operations:
 *  - Liquid staking:    deposit (SOL → mSOL), liquidUnstake (mSOL → SOL instantly)
 *  - Delayed unstake:   orderUnstake, orderUnstakeWithPublicKey, claim
 *  - Native stake:      depositStakeAccount, depositDeactivatingStakeAccount,
 *                       depositActivatingStakeAccount, partiallyDepositStakeAccount
 *  - Stake pool tokens: depositStakePoolToken, liquidateStakePoolToken
 *  - Liquidity pool:    addLiquidity, removeLiquidity
 *  - Referral program:  getReferralPartnerState, getReferralGlobalState, getReferralPartners
 *  - Read-only queries: getMarinadeState, exchange rate, ticket info/list, due date estimate
 */

import {
  Marinade,
  MarinadeConfig,
} from "@marinade.finance/marinade-ts-sdk";
import type { ValidatorStats } from "@marinade.finance/marinade-ts-sdk/dist/src/marinade.types";
import { BN } from "@coral-xyz/anchor";
import { Connection, PublicKey, Transaction, VersionedTransaction } from "@solana/web3.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

// ── Constants ──────────────────────────────────────────────────────────────────

const MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So";
const MARINADE_API = "https://api.marinade.finance";

// ── Helpers ────────────────────────────────────────────────────────────────────

function mkPreview(
  type: string,
  description: string,
  params: Record<string, unknown>,
  opts?: { fee?: string; warnings?: string[] }
): ActionPreview {
  return {
    id: uuidv4(),
    type,
    description,
    estimatedFee: opts?.fee ?? "~0.000005 SOL",
    params,
    warnings: opts?.warnings ?? [],
    requiresApproval: false,
  };
}

function getMarinade(userWallet: string, referralCode?: string): Marinade {
  const connection = new Connection(config.solanaRpc, "confirmed");
  const marinadeConfig = new MarinadeConfig({
    connection,
    publicKey: new PublicKey(userWallet),
    ...(referralCode ? { referralCode: new PublicKey(referralCode) } : {}),
  });
  return new Marinade(marinadeConfig);
}

/** Serialize an unsigned legacy transaction to base64 */
/**
 * Serialize an unsigned legacy transaction for the browser to sign.
 *
 * The SDK hands back a Transaction with neither a fee payer nor a blockhash —
 * it expects a wallet adapter to fill them in at send time. We have no wallet,
 * so `serialize()` threw "Transaction recentBlockhash required" and EVERY
 * Marinade action failed at the last step, after the SDK had done all the work
 * correctly. Filled here, once, for all of them.
 */
async function prepareTx(tx: Transaction, userWallet: string): Promise<Transaction> {
  if (!tx.feePayer) tx.feePayer = new PublicKey(userWallet);
  if (!tx.recentBlockhash) {
    const connection = new Connection(config.solanaRpc, "confirmed");
    tx.recentBlockhash = (await connection.getLatestBlockhash("confirmed")).blockhash;
  }
  return tx;
}

async function serializeTx(tx: Transaction, userWallet: string): Promise<string> {
  await prepareTx(tx, userWallet);
  return tx.serialize({ requireAllSignatures: false }).toString("base64");
}

/** Serialize an unsigned VersionedTransaction to base64 */
function serializeVersionedTx(tx: VersionedTransaction): string {
  return Buffer.from(tx.serialize()).toString("base64");
}

/** Fetch Marinade validator list and map to SDK's ValidatorStats shape */
async function fetchValidators(): Promise<ValidatorStats[]> {
  const url =
    "https://validators-api.marinade.finance/validators" +
    "?limit=9999&order_field=score&order_direction=DESC";
  const resp = await fetch(url);
  if (!resp.ok) throw appError(`Failed to fetch Marinade validators: ${resp.status}`);

  const json = await resp.json() as {
    validators: Array<ValidatorStats & { activated_stake: string }>;
  };

  // API returns `activated_stake` instead of `decentralizer_stake` — remap that field only
  return json.validators.map((v) => ({
    ...v,
    decentralizer_stake: v.activated_stake,
  }));
}

function lamportsFromSol(sol: number): BN {
  return new BN(Math.round(sol * 1e9));
}

function lamportsFromMsol(msol: number): BN {
  // mSOL has 9 decimals same as SOL
  return new BN(Math.round(msol * 1e9));
}

// ── 1. Stake: SOL → mSOL (Liquid Staking) ────────────────────────────────────

export interface MarinadeStakeParams {
  /** SOL amount to stake (human-readable, e.g. "1.5") */
  amount: string;
  /** Optional referral code (base58 PublicKey) for the Marinade referral program */
  referralCode?: string;
  /**
   * Optional base58 public key of the owner for the mSOL token account.
   * Defaults to the connected wallet. Useful when staking on behalf of another account.
   */
  mintToOwnerAddress?: string;
}

export async function buildMarinadeStake(
  params: MarinadeStakeParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");
  if (amount < 0.001) throw appError("Minimum stake amount is 0.001 SOL");

  let mintToOwnerPubkey: PublicKey | undefined;
  if (params.mintToOwnerAddress) {
    try {
      mintToOwnerPubkey = new PublicKey(params.mintToOwnerAddress);
    } catch {
      throw appError("mintToOwnerAddress must be a valid base58 public key");
    }
  }

  const marinade = getMarinade(userWallet, params.referralCode);
  const depositOptions = mintToOwnerPubkey ? { mintToOwnerAddress: mintToOwnerPubkey } : undefined;
  const { transaction, associatedMSolTokenAccountAddress } =
    await marinade.deposit(lamportsFromSol(amount), depositOptions);

  return {
    preview: mkPreview(
      "marinade_stake",
      `Stake ${params.amount} SOL → mSOL via Marinade Finance`,
      params as unknown as Record<string, unknown>,
      { warnings: params.referralCode ? [] : ["Consider setting a referral code to earn referral rewards"] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58() },
  };
}

// ── 2. Liquid Unstake: mSOL → SOL (Instant, ~0.3% fee) ───────────────────────

export interface MarinadeUnstakeParams {
  /** mSOL amount to unstake (human-readable, e.g. "1.5") */
  amount: string;
  /**
   * Optional base58 public key of the specific mSOL token account to burn from.
   * Defaults to the connected wallet's associated mSOL token account.
   * Only needed when the mSOL is held in a non-standard token account.
   */
  msolTokenAccount?: string;
}

export async function buildMarinadeUnstake(
  params: MarinadeUnstakeParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  let msolTokenAccountPubkey: PublicKey | undefined;
  if (params.msolTokenAccount) {
    try {
      msolTokenAccountPubkey = new PublicKey(params.msolTokenAccount);
    } catch {
      throw appError("msolTokenAccount must be a valid base58 public key");
    }
  }

  const marinade = getMarinade(userWallet);

  // Fetch current fee from state for accurate preview
  let feeDisplay = "~0.3%";
  try {
    const state = await marinade.getMarinadeState();
    const feeBp = Number((state as unknown as { liqPool: { lpLiquidityTarget: BN; lpMaxFee: { basisPoints: BN }; lpMinFee: { basisPoints: BN } } }).liqPool.lpMaxFee?.basisPoints ?? 30);
    feeDisplay = `~${(feeBp / 100).toFixed(2)}%`;
  } catch { /* use default */ }

  const { transaction, associatedMSolTokenAccountAddress } =
    await marinade.liquidUnstake(lamportsFromMsol(amount), msolTokenAccountPubkey);

  return {
    preview: mkPreview(
      "marinade_unstake",
      `Instantly unstake ${params.amount} mSOL → SOL via Marinade (fee: ${feeDisplay})`,
      params as unknown as Record<string, unknown>,
      { warnings: ["Liquid unstaking charges a ~0.3% fee. Use delayed unstake for no-fee withdrawal (takes 3-7 days)."] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58() },
  };
}

// ── 3. Delayed Unstake: mSOL → Ticket (No fee, 2-3 epochs ≈ 5-7 days) ────────

export interface MarinadeDelayedUnstakeParams {
  /** mSOL amount to unstake (human-readable) */
  amount: string;
}

export async function buildMarinadeDelayedUnstake(
  params: MarinadeDelayedUnstakeParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  const marinade = getMarinade(userWallet);
  const { transaction, ticketAccountKeypair, associatedMSolTokenAccountAddress } =
    await marinade.orderUnstake(lamportsFromMsol(amount));

  // Prepared BEFORE signing: partialSign signs the compiled message, so a
  // transaction with no blockhash throws there — before the serializer that
  // would have filled it in ever runs.
  await prepareTx(transaction, userWallet);
  // The ticket account must co-sign: the SDK returns the keypair but does NOT
  // pre-sign the transaction. We partial-sign here so the frontend only needs
  // the user's wallet signature (additionalSignersRequired = 0).
  transaction.partialSign(ticketAccountKeypair);

  return {
    preview: mkPreview(
      "marinade_delayed_unstake",
      `Order delayed unstake of ${params.amount} mSOL → SOL via Marinade (no fee, ~5-7 days)`,
      {
        ...params as unknown as Record<string, unknown>,
        ticketAccount: ticketAccountKeypair.publicKey.toBase58(),
      },
      {
        warnings: [
          "Delayed unstake takes 2-3 epochs (~5-7 days). Save the ticket account address to claim your SOL after the delay.",
          `Ticket account: ${ticketAccountKeypair.publicKey.toBase58()}`,
        ],
      }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0, // ticket keypair already partial-signed above
    data: {
      ticketAccount: ticketAccountKeypair.publicKey.toBase58(),
      associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58(),
    },
    isCrossChain: false,
  };
}

// ── 4. Claim: Ticket → SOL (after delay period has passed) ────────────────────

export interface MarinadeClaimParams {
  /** Base58 public key of the ticket account from orderUnstake */
  ticketAccount: string;
}

export async function buildMarinadeClaim(
  params: MarinadeClaimParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.ticketAccount) throw appError("ticketAccount is required");

  let ticketPubkey: PublicKey;
  try {
    ticketPubkey = new PublicKey(params.ticketAccount);
  } catch {
    throw appError("ticketAccount must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction } = await marinade.claim(ticketPubkey);

  return {
    preview: mkPreview(
      "marinade_claim",
      `Claim SOL from delayed unstake ticket ${params.ticketAccount.slice(0, 8)}…`,
      params as unknown as Record<string, unknown>,
      { warnings: ["This will fail if the delay period has not yet passed (2-3 epochs after ordering)."] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ── 5. Deposit Stake Account: Native Stake → mSOL ─────────────────────────────

export interface MarinadeDepositStakeParams {
  /** Base58 public key of the fully-activated stake account to deposit into Marinade */
  stakeAccount: string;
}

export async function buildMarinadeDepositStake(
  params: MarinadeDepositStakeParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakeAccount) throw appError("stakeAccount is required");

  let stakeAccountPubkey: PublicKey;
  try {
    stakeAccountPubkey = new PublicKey(params.stakeAccount);
  } catch {
    throw appError("stakeAccount must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction, voterAddress, associatedMSolTokenAccountAddress, mintRatio } =
    await marinade.depositStakeAccount(stakeAccountPubkey);

  return {
    preview: mkPreview(
      "marinade_deposit_stake",
      `Deposit stake account ${params.stakeAccount.slice(0, 8)}… into Marinade → receive mSOL (mint ratio: ${mintRatio?.toFixed(6) ?? "~1"})`,
      {
        stakeAccount: params.stakeAccount,
        validatorVoteAddress: voterAddress?.toBase58(),
        mintRatio,
      },
      {
        warnings: [
          "Your stake account will be managed by Marinade validators and you will receive mSOL in return.",
          "The stake account must be fully activated. If deactivating, use marinade_deposit_deactivating_stake instead.",
          "If activating (warm-up), use marinade_deposit_activating_stake instead.",
        ],
      }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      voterAddress: voterAddress?.toBase58(),
      associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58(),
      mintRatio,
    },
  };
}

// ── 6. Add Liquidity: SOL → LP Tokens ─────────────────────────────────────────

export interface MarinadeAddLiquidityParams {
  /** SOL amount to add to the liquidity pool (human-readable) */
  amount: string;
}

export async function buildMarinadeAddLiquidity(
  params: MarinadeAddLiquidityParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");
  if (amount < 0.001) throw appError("Minimum liquidity amount is 0.001 SOL");

  const marinade = getMarinade(userWallet);
  const { transaction, associatedLPTokenAccountAddress } =
    await marinade.addLiquidity(lamportsFromSol(amount));

  return {
    preview: mkPreview(
      "marinade_add_liquidity",
      `Add ${params.amount} SOL to Marinade liquidity pool → receive LP tokens`,
      params as unknown as Record<string, unknown>,
      {
        warnings: [
          "LP tokens earn trading fees from liquid unstaking operations.",
          "Liquidity can be removed at any time by burning LP tokens.",
        ],
      }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedLPTokenAccountAddress: associatedLPTokenAccountAddress.toBase58() },
  };
}

// ── 7. Remove Liquidity: LP Tokens → SOL + mSOL ───────────────────────────────

export interface MarinadeRemoveLiquidityParams {
  /** LP token amount to burn (human-readable) */
  amount: string;
}

export async function buildMarinadeRemoveLiquidity(
  params: MarinadeRemoveLiquidityParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  const marinade = getMarinade(userWallet);
  // LP token has 9 decimals
  const lpLamports = new BN(Math.round(amount * 1e9));
  const { transaction, associatedLPTokenAccountAddress, associatedMSolTokenAccountAddress } =
    await marinade.removeLiquidity(lpLamports);

  return {
    preview: mkPreview(
      "marinade_remove_liquidity",
      `Remove ${params.amount} LP tokens from Marinade → receive SOL + mSOL`,
      params as unknown as Record<string, unknown>,
      {
        warnings: ["You will receive a proportional mix of SOL and mSOL based on current pool composition."],
      }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      associatedLPTokenAccountAddress: associatedLPTokenAccountAddress.toBase58(),
      associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58(),
    },
  };
}

// ── 8. Protocol State (read-only) ─────────────────────────────────────────────

export async function getMarinadeState(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const state = await marinade.getMarinadeState();

  // Extract useful fields from MarinadeState
  const stateAny = state as unknown as Record<string, unknown>;

  // Calculate human-readable values from the already-fetched state
  let msolPrice = 1.0;
  let totalStakedSol = 0;

  try {
    // mSolPrice is a Q32 fixed-point number: divide by 2^32 to get SOL per mSOL
    const msolPriceBn = (stateAny.msolPrice as BN | undefined);
    if (msolPriceBn) {
      msolPrice = msolPriceBn.toNumber() / 0x1_0000_0000;
    }
    const validatorSystem = (stateAny.validatorSystem as Record<string, unknown> | undefined);
    if (validatorSystem?.totalActiveBalance) {
      totalStakedSol = (validatorSystem.totalActiveBalance as BN).toNumber() / 1e9;
    }
  } catch { /* best effort */ }

  // Also fetch from public Marinade API for enriched stats
  let apiStats: Record<string, unknown> = {};
  try {
    const resp = await fetch(`${MARINADE_API}/v1/marinade-state`);
    if (resp.ok) {
      apiStats = await resp.json() as Record<string, unknown>;
    }
  } catch { /* best effort */ }

  return {
    preview: mkPreview("marinade_state", "Marinade Finance protocol state", {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      msolMint: MSOL_MINT,
      msolPriceInSol: msolPrice,
      totalStakedSol,
      ...apiStats,
    },
  };
}

// ── 9. Exchange Rate (read-only) ───────────────────────────────────────────────

export async function getMarinadeExchangeRate(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const state = await marinade.getMarinadeState();
  const stateAny = state as unknown as Record<string, unknown>;

  // The SDK exposes the price under `mSolPrice`, not `msolPrice`. Reading the
  // wrong name found nothing and the catch-all default answered 1:1 — a rate
  // that is wrong by forty per cent and looks entirely plausible. mSOL has not
  // been worth one SOL since the day the pool opened.
  let msolPriceInSol = 0;
  const fromState = (stateAny.mSolPrice ?? stateAny.msolPrice) as number | BN | undefined;
  if (typeof fromState === "number" && fromState > 0) {
    msolPriceInSol = fromState;
  } else if (fromState && typeof (fromState as BN).toNumber === "function") {
    msolPriceInSol = (fromState as BN).toNumber() / 0x1_0000_0000;
  }

  // One call gives the APY and the price together. `/v1/apy` — what this used
  // to ask for — has been 404 for long enough that apyPercent was always null.
  // 30 days rather than 1: a single-day window annualises noise (11.17% today
  // against 5.86% over the month).
  let apyPercent: number | null = null;
  try {
    const resp = await fetch(`${MARINADE_API}/msol/apy/30d`);
    if (resp.ok) {
      const j = (await resp.json()) as { value?: number; end_price?: number };
      if (typeof j.value === "number") apyPercent = j.value * 100;
      // The same endpoint carries the price, so a state read that came back
      // empty still produces a real number rather than a default.
      if (!msolPriceInSol && typeof j.end_price === "number") msolPriceInSol = j.end_price;
    }
  } catch { /* best effort */ }

  if (!msolPriceInSol) {
    throw appError("Marinade's mSOL price is unavailable right now.", 503, "NO_RATE");
  }
  const solPriceInMsol = 1 / msolPriceInSol;

  return {
    preview: mkPreview("marinade_exchange_rate", "Marinade mSOL/SOL exchange rate", {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      msolMint: MSOL_MINT,
      msolPriceInSol,
      solPriceInMsol,
      apyPercent,
      description: `1 mSOL = ${msolPriceInSol.toFixed(6)} SOL | 1 SOL = ${solPriceInMsol.toFixed(6)} mSOL`,
    },
  };
}

// ── 10. Get Unstake Ticket Info (read-only) ────────────────────────────────────

export interface MarinadeTicketInfoParams {
  /** Base58 public key of the ticket account */
  ticketAccount: string;
}

export async function getMarinadeTicketInfo(
  params: MarinadeTicketInfoParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.ticketAccount) throw appError("ticketAccount is required");

  let ticketPubkey: PublicKey;
  try {
    ticketPubkey = new PublicKey(params.ticketAccount);
  } catch {
    throw appError("ticketAccount must be a valid base58 public key");
  }

  const connection = new Connection(config.solanaRpc, "confirmed");
  const accountInfo = await connection.getAccountInfo(ticketPubkey);

  if (!accountInfo) {
    throw appError(`Ticket account ${params.ticketAccount} not found on-chain. It may have already been claimed.`);
  }

  // Ticket accounts are 8+8+32+8 = 56 bytes
  // Layout: discriminator(8) + stateAddress(32) + beneficiary(32) + lamportsAmount(8) + createdEpoch(8)
  let lamportsAmount = 0;
  let createdEpoch = 0;
  try {
    const data = accountInfo.data;
    // Skip 8-byte discriminator + 32-byte stateAddress + 32-byte beneficiary = offset 72
    const view = Buffer.from(data);
    lamportsAmount = Number(view.readBigUInt64LE(72));
    createdEpoch = Number(view.readBigUInt64LE(80));
  } catch { /* best effort parsing */ }

  const epochInfo = await connection.getEpochInfo();
  const epochsRemaining = Math.max(0, createdEpoch + 2 - epochInfo.epoch);
  const isClaimable = epochsRemaining === 0;

  return {
    preview: mkPreview("marinade_ticket_info", `Ticket info: ${params.ticketAccount.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      ticketAccount: params.ticketAccount,
      lamportsAmount,
      solAmount: (lamportsAmount / 1e9).toFixed(9),
      createdEpoch,
      currentEpoch: epochInfo.epoch,
      epochsRemaining,
      isClaimable,
      status: isClaimable ? "CLAIMABLE" : `PENDING (${epochsRemaining} epoch(s) remaining, ~${epochsRemaining * 2.5} days)`,
    },
  };
}

// ── 11. List User's Delayed Unstake Tickets (read-only) ───────────────────────

export async function listMarinadeTickets(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const beneficiary = new PublicKey(userWallet);
  const ticketMap = await marinade.getDelayedUnstakeTickets(beneficiary);

  const epochInfo = await new Connection(config.solanaRpc, "confirmed").getEpochInfo();

  const tickets: Array<Record<string, unknown>> = [];
  for (const [pubkey, ticket] of ticketMap) {
    const ticketAny = ticket as unknown as Record<string, unknown>;
    const createdEpoch = Number(ticketAny.createdEpoch ?? 0);
    const lamportsAmount = Number(ticketAny.lamportsAmount ?? 0);
    const epochsRemaining = Math.max(0, createdEpoch + 2 - epochInfo.epoch);
    tickets.push({
      address: pubkey.toBase58(),
      solAmount: (lamportsAmount / 1e9).toFixed(9),
      lamportsAmount,
      createdEpoch,
      currentEpoch: epochInfo.epoch,
      epochsRemaining,
      isClaimable: epochsRemaining === 0,
      status: epochsRemaining === 0 ? "CLAIMABLE" : `PENDING (~${epochsRemaining * 2.5} days)`,
    });
  }

  return {
    preview: mkPreview("marinade_list_tickets", `${tickets.length} delayed unstake ticket(s) for ${userWallet.slice(0, 8)}…`, {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { tickets, count: tickets.length, currentEpoch: epochInfo.epoch },
  };
}

// ── 12. Order Unstake with Explicit Ticket Public Key ─────────────────────────

export interface MarinadeOrderUnstakeWithKeyParams {
  /** mSOL amount to unstake (human-readable) */
  amount: string;
  /** The desired public key for the ticket account (base58). Must be a fresh keypair. */
  ticketAccountPublicKey: string;
}

export async function buildMarinadeOrderUnstakeWithKey(
  params: MarinadeOrderUnstakeWithKeyParams,
  userWallet: string
): Promise<BuildResponse> {
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");
  if (!params.ticketAccountPublicKey) throw appError("ticketAccountPublicKey is required");

  let ticketPubkey: PublicKey;
  try {
    ticketPubkey = new PublicKey(params.ticketAccountPublicKey);
  } catch {
    throw appError("ticketAccountPublicKey must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction } = await marinade.orderUnstakeWithPublicKey(
    lamportsFromMsol(amount),
    ticketPubkey
  );

  return {
    preview: mkPreview(
      "marinade_order_unstake_with_key",
      `Order delayed unstake of ${params.amount} mSOL with deterministic ticket account`,
      params as unknown as Record<string, unknown>,
      { warnings: [`Ticket account: ${params.ticketAccountPublicKey}`, "Keep this address — you will need it to claim SOL after ~5-7 days."] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 1, // ticket keypair must co-sign
    data: { ticketAccount: params.ticketAccountPublicKey },
    isCrossChain: false,
  };
}

// ── 13. Deposit Deactivating Stake Account ────────────────────────────────────

export interface MarinadeDepositDeactivatingStakeParams {
  /** Base58 public key of the deactivating stake account */
  stakeAccount: string;
}

export async function buildMarinadeDepositDeactivatingStake(
  params: MarinadeDepositDeactivatingStakeParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakeAccount) throw appError("stakeAccount is required");

  let stakeAccountPubkey: PublicKey;
  try {
    stakeAccountPubkey = new PublicKey(params.stakeAccount);
  } catch {
    throw appError("stakeAccount must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction, associatedMSolTokenAccountAddress } =
    await marinade.depositDeactivatingStakeAccount(stakeAccountPubkey);

  return {
    preview: mkPreview(
      "marinade_deposit_deactivating_stake",
      `Deposit deactivating stake account ${params.stakeAccount.slice(0, 8)}… → mSOL`,
      params as unknown as Record<string, unknown>,
      { warnings: ["The stake account must be in deactivating state (cooling down from a previous epoch)."] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58() },
  };
}

// ── 14. Partially Deposit Stake Account (keep some SOL) ───────────────────────

export interface MarinadePartialDepositStakeParams {
  /** Base58 public key of the stake account */
  stakeAccount: string;
  /** SOL amount to keep in the stake account (the rest is converted to mSOL) */
  solToKeep: string;
}

export async function buildMarinadePartialDepositStake(
  params: MarinadePartialDepositStakeParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakeAccount) throw appError("stakeAccount is required");
  const solToKeep = parseFloat(params.solToKeep ?? "0");
  if (isNaN(solToKeep) || solToKeep < 0) throw appError("solToKeep must be a non-negative number");

  let stakeAccountPubkey: PublicKey;
  try {
    stakeAccountPubkey = new PublicKey(params.stakeAccount);
  } catch {
    throw appError("stakeAccount must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction, stakeAccountKeypair, voterAddress, associatedMSolTokenAccountAddress } =
    await marinade.partiallyDepositStakeAccount(
      stakeAccountPubkey,
      lamportsFromSol(solToKeep)
    );

  // Same ordering rule as the delayed unstake: prepare, then sign.
  await prepareTx(transaction, userWallet);
  // If the SDK creates a new residual stake account, it returns a keypair that must co-sign.
  let additionalSigners = 0;
  if (stakeAccountKeypair) {
    transaction.partialSign(stakeAccountKeypair);
    additionalSigners = 0; // already pre-signed
  }

  return {
    preview: mkPreview(
      "marinade_partial_deposit_stake",
      `Partially deposit stake account — keep ${params.solToKeep} SOL, convert the rest to mSOL`,
      params as unknown as Record<string, unknown>,
      { warnings: [`${params.solToKeep} SOL will remain in your stake account; the rest will become mSOL.`] }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: additionalSigners,
    isCrossChain: false,
    data: {
      associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress?.toBase58(),
      voterAddress: voterAddress?.toBase58(),
      residualStakeAccount: stakeAccountKeypair?.publicKey.toBase58(),
    },
  };
}

// ── 15. Deposit Activating Stake Account (beta) ───────────────────────────────

export interface MarinadeDepositActivatingStakeParams {
  /** Base58 public key of the activating (warming-up) stake account */
  stakeAccount: string;
  /** SOL amount to keep in the original stake account (non-negative) */
  solToKeep: string;
}

export async function buildMarinadeDepositActivatingStake(
  params: MarinadeDepositActivatingStakeParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakeAccount) throw appError("stakeAccount is required");
  const solToKeep = parseFloat(params.solToKeep ?? "0");
  if (isNaN(solToKeep) || solToKeep < 0) throw appError("solToKeep must be a non-negative number");

  let stakeAccountPubkey: PublicKey;
  try {
    stakeAccountPubkey = new PublicKey(params.stakeAccount);
  } catch {
    throw appError("stakeAccount must be a valid base58 public key");
  }

  const marinade = getMarinade(userWallet);
  const { transaction, stakeAccountKeypair, voterAddress, associatedMSolTokenAccountAddress } =
    await marinade.depositActivatingStakeAccount(
      stakeAccountPubkey,
      lamportsFromSol(solToKeep)
    );

  await prepareTx(transaction, userWallet);
  // If a new residual stake account keypair is created, pre-sign it server-side.
  if (stakeAccountKeypair) {
    transaction.partialSign(stakeAccountKeypair);
  }

  return {
    preview: mkPreview(
      "marinade_deposit_activating_stake",
      `Deposit activating (warming-up) stake account ${params.stakeAccount.slice(0, 8)}… into Marinade → mSOL`,
      params as unknown as Record<string, unknown>,
      {
        warnings: [
          "The stake account must be in activating (warm-up) state.",
          "If fully activated, use marinade_deposit_stake instead.",
          `${params.solToKeep} SOL will remain in the original stake account.`,
        ],
      }
    ),
    transaction: await serializeTx(transaction, userWallet),
    additionalSignersRequired: 0, // residual stake keypair pre-signed above if needed
    isCrossChain: false,
    data: {
      associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress?.toBase58(),
      voterAddress: voterAddress?.toBase58(),
      residualStakeAccount: stakeAccountKeypair?.publicKey.toBase58(),
    },
  };
}

// ── 16. Get All Protocol Delayed Unstake Tickets (read-only) ──────────────────

export async function getAllMarinadeTickets(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const ticketMap = await marinade.getAllDelayedUnstakeTickets();

  const connection = new Connection(config.solanaRpc, "confirmed");
  const epochInfo = await connection.getEpochInfo();

  let claimableCount = 0;
  let pendingCount = 0;
  let totalSol = 0;

  const tickets: Array<Record<string, unknown>> = [];
  for (const [pubkey, ticket] of ticketMap) {
    const ticketAny = ticket as unknown as Record<string, unknown>;
    const createdEpoch = Number(ticketAny.createdEpoch ?? 0);
    const lamportsAmount = Number(ticketAny.lamportsAmount ?? 0);
    const epochsRemaining = Math.max(0, createdEpoch + 2 - epochInfo.epoch);
    const isClaimable = epochsRemaining === 0;
    if (isClaimable) claimableCount++; else pendingCount++;
    totalSol += lamportsAmount / 1e9;
    tickets.push({
      address: pubkey.toBase58(),
      solAmount: (lamportsAmount / 1e9).toFixed(9),
      createdEpoch,
      epochsRemaining,
      isClaimable,
    });
  }

  return {
    preview: mkPreview("marinade_all_tickets", `All protocol delayed unstake tickets (${tickets.length} total)`, {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      tickets,
      count: tickets.length,
      claimableCount,
      pendingCount,
      totalSolLocked: totalSol.toFixed(6),
      currentEpoch: epochInfo.epoch,
    },
  };
}

// ── 17. Get Estimated Unstake Ticket Due Date ─────────────────────────────────

export async function getMarinadeTicketDueDate(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const dueDate = await marinade.getEstimatedUnstakeTicketDueDate();

  return {
    preview: mkPreview("marinade_ticket_due_date", "Estimated due date for new delayed unstake tickets", {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: dueDate as unknown as Record<string, unknown>,
  };
}

// ── 18. Referral Partner State (read-only) ────────────────────────────────────

export interface MarinadeReferralStateParams {
  /** Optional base58 public key of the referral code to look up. Omit to fetch caller's own state. */
  referralCode?: string;
}

export async function getMarinadeReferralPartnerState(
  params: MarinadeReferralStateParams,
  userWallet: string
): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);

  let refPubkey: PublicKey | undefined;
  if (params.referralCode) {
    try {
      refPubkey = new PublicKey(params.referralCode);
    } catch {
      throw appError("referralCode must be a valid base58 public key");
    }
  }

  const partnerState = await marinade.getReferralPartnerState(refPubkey);

  return {
    preview: mkPreview(
      "marinade_referral_partner_state",
      `Marinade referral partner state${params.referralCode ? ` for ${params.referralCode.slice(0, 8)}…` : ""}`,
      params as unknown as Record<string, unknown>
    ),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      referralStateAddress: partnerState.referralStateAddress.toBase58(),
      state: partnerState.state as unknown as Record<string, unknown>,
    },
  };
}

// ── 19. Referral Global State (read-only) ─────────────────────────────────────

export async function getMarinadeReferralGlobalState(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const globalState = await marinade.getReferralGlobalState();

  return {
    preview: mkPreview("marinade_referral_global_state", "Marinade referral program global state", {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: globalState as unknown as Record<string, unknown>,
  };
}

// ── 20. List All Referral Partners (read-only) ────────────────────────────────

export async function getMarinadeReferralPartners(userWallet: string): Promise<BuildResponse> {
  const marinade = getMarinade(userWallet);
  const partners = await marinade.getReferralPartners();

  return {
    preview: mkPreview("marinade_referral_partners", `Marinade referral partners (${partners.length})`, {}),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      partners: partners.map((p) => ({
        referralStateAddress: p.referralStateAddress.toBase58(),
        state: p.state as unknown as Record<string, unknown>,
      })),
      count: partners.length,
    },
  };
}

// ── 21. Deposit Stake Pool Token → mSOL (@beta) ───────────────────────────────
//
// Converts a stake pool LST token (jitoSOL, bSOL, etc.) into mSOL via Marinade.
// Validators list is fetched internally — the LLM only needs the token mint and amount.

export interface MarinadeDepositStakePoolTokenParams {
  /** Mint address (base58) of the stake pool token, e.g. jitoSOL or bSOL */
  stakePoolTokenAddress: string;
  /** Amount to deposit (human-readable, in stake pool token units) */
  amount: string;
}

export async function buildMarinadeDepositStakePoolToken(
  params: MarinadeDepositStakePoolTokenParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakePoolTokenAddress) throw appError("stakePoolTokenAddress is required");
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  let tokenAddress: PublicKey;
  try {
    tokenAddress = new PublicKey(params.stakePoolTokenAddress);
  } catch {
    throw appError("stakePoolTokenAddress must be a valid base58 public key");
  }

  const [marinade, validators] = await Promise.all([
    Promise.resolve(getMarinade(userWallet)),
    fetchValidators(),
  ]);

  const { transaction, associatedMSolTokenAccountAddress } =
    await marinade.depositStakePoolToken(tokenAddress, amount, validators);

  return {
    preview: mkPreview(
      "marinade_deposit_stake_pool_token",
      `Deposit ${params.amount} stake pool tokens (${params.stakePoolTokenAddress.slice(0, 8)}…) → mSOL via Marinade`,
      params as unknown as Record<string, unknown>,
      {
        warnings: [
          "Beta feature: converts LST tokens (jitoSOL, bSOL, etc.) into mSOL.",
          "Minimum deposit equivalent of 1 SOL required.",
          "Uses a versioned transaction with address lookup tables.",
        ],
      }
    ),
    transaction: serializeVersionedTx(transaction),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58() },
  };
}

// ── 22. Liquidate Stake Pool Token → SOL (@beta) ──────────────────────────────
//
// Converts a stake pool LST token into SOL in a single versioned transaction:
// withdraw stake → deposit into Marinade → liquid unstake.
// Validators list is fetched internally.

export interface MarinadeReferralLiquidateStakePoolTokenParams {
  /** Mint address (base58) of the stake pool token to liquidate */
  stakePoolTokenAddress: string;
  /** Amount to liquidate (human-readable, in stake pool token units) */
  amount: string;
}

export async function buildMarinadeLiquidateStakePoolToken(
  params: MarinadeReferralLiquidateStakePoolTokenParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.stakePoolTokenAddress) throw appError("stakePoolTokenAddress is required");
  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0) throw appError("amount must be a positive number");

  let tokenAddress: PublicKey;
  try {
    tokenAddress = new PublicKey(params.stakePoolTokenAddress);
  } catch {
    throw appError("stakePoolTokenAddress must be a valid base58 public key");
  }

  const [marinade, validators] = await Promise.all([
    Promise.resolve(getMarinade(userWallet)),
    fetchValidators(),
  ]);

  const { transaction, associatedMSolTokenAccountAddress } =
    await marinade.liquidateStakePoolToken(tokenAddress, amount, validators);

  return {
    preview: mkPreview(
      "marinade_liquidate_stake_pool_token",
      `Liquidate ${params.amount} stake pool tokens (${params.stakePoolTokenAddress.slice(0, 8)}…) → SOL via Marinade`,
      params as unknown as Record<string, unknown>,
      {
        warnings: [
          "Beta feature: converts LST tokens to SOL in a single atomic versioned transaction.",
          "Steps: withdraw stake → deposit into Marinade → liquid unstake.",
          "Minimum liquidation equivalent of 1 SOL required.",
          "Liquid unstake fee (~0.3%) applies.",
        ],
      }
    ),
    transaction: serializeVersionedTx(transaction),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { associatedMSolTokenAccountAddress: associatedMSolTokenAccountAddress.toBase58() },
  };
}

// ── Validators API helpers ─────────────────────────────────────────────────────

const VALIDATORS_API = "https://validators-api.marinade.finance";

async function validatorsApiFetch(path: string): Promise<unknown> {
  const resp = await fetch(`${VALIDATORS_API}${path}`);
  if (!resp.ok) throw appError(`Marinade validators API error ${resp.status}: ${path}`);
  return resp.json();
}

function mkQueryPreview(type: string, description: string, params: Record<string, unknown> = {}): BuildResponse {
  return {
    preview: mkPreview(type, description, params),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ── 23. Cluster Stats ──────────────────────────────────────────────────────────

export interface MarinadeClusterStatsParams {
  epochs?: number;
}

export async function getMarinadeClusterStats(
  params: MarinadeClusterStatsParams
): Promise<BuildResponse> {
  const qs = params.epochs ? `?epochs=${params.epochs}` : "";
  const data = await validatorsApiFetch(`/cluster-stats${qs}`);
  return { ...mkQueryPreview("marinade_cluster_stats", "Marinade cluster-wide staking statistics"), data: data as Record<string, unknown> };
}

// ── 24. Validator Scores ───────────────────────────────────────────────────────

export async function getMarinadeValidatorScores(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/validators/scores") as Record<string, unknown>;
  return { ...mkQueryPreview("marinade_validator_scores", "Marinade validator score list"), data };
}

// ── 25. Validator Score Breakdown ──────────────────────────────────────────────

export interface MarinadeScoreBreakdownParams {
  /** Vote account address of the validator */
  voteAccount: string;
}

export async function getMarinadeScoreBreakdown(
  params: MarinadeScoreBreakdownParams
): Promise<BuildResponse> {
  if (!params.voteAccount) throw appError("voteAccount is required");
  const data = await validatorsApiFetch(`/validators/score-breakdown?query_vote_account=${encodeURIComponent(params.voteAccount)}`);
  return {
    ...mkQueryPreview("marinade_score_breakdown", `Score breakdown for validator ${params.voteAccount.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 26. Validator Score Breakdowns (multiple) ──────────────────────────────────

export interface MarinadeScoreBreakdownsParams {
  voteAccount?: string;
  fromDate?: string;
}

export async function getMarinadeScoreBreakdowns(
  params: MarinadeScoreBreakdownsParams
): Promise<BuildResponse> {
  const qs = new URLSearchParams();
  if (params.voteAccount) qs.set("query_vote_account", params.voteAccount);
  if (params.fromDate) qs.set("query_from_date", params.fromDate);
  const data = await validatorsApiFetch(`/validators/score-breakdowns?${qs.toString()}`);
  return {
    ...mkQueryPreview("marinade_score_breakdowns", "Marinade validator score breakdowns", params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 27. Validator Commission History ──────────────────────────────────────────

export interface MarinadeCommissionsParams {
  /** Vote account address of the validator */
  voteAccount: string;
  fromDate?: string;
}

export async function getMarinadeValidatorCommissions(
  params: MarinadeCommissionsParams
): Promise<BuildResponse> {
  if (!params.voteAccount) throw appError("voteAccount is required");
  const qs = params.fromDate ? `?query_from_date=${encodeURIComponent(params.fromDate)}` : "";
  const data = await validatorsApiFetch(`/validators/${params.voteAccount}/commissions${qs}`);
  return {
    ...mkQueryPreview("marinade_validator_commissions", `Commission history for validator ${params.voteAccount.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 28. Validator Uptime History ───────────────────────────────────────────────

export interface MarinadeUptimesParams {
  /** Vote account address of the validator */
  voteAccount: string;
  fromDate?: string;
}

export async function getMarinadeValidatorUptimes(
  params: MarinadeUptimesParams
): Promise<BuildResponse> {
  if (!params.voteAccount) throw appError("voteAccount is required");
  const qs = params.fromDate ? `?query_from_date=${encodeURIComponent(params.fromDate)}` : "";
  const data = await validatorsApiFetch(`/validators/${params.voteAccount}/uptimes${qs}`);
  return {
    ...mkQueryPreview("marinade_validator_uptimes", `Uptime history for validator ${params.voteAccount.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 29. Validator Version History ─────────────────────────────────────────────

export interface MarinadeVersionsParams {
  /** Vote account address of the validator */
  voteAccount: string;
}

export async function getMarinadeValidatorVersions(
  params: MarinadeVersionsParams
): Promise<BuildResponse> {
  if (!params.voteAccount) throw appError("voteAccount is required");
  const data = await validatorsApiFetch(`/validators/${params.voteAccount}/versions`);
  return {
    ...mkQueryPreview("marinade_validator_versions", `Version history for validator ${params.voteAccount.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 30. Block Rewards ─────────────────────────────────────────────────────────

export async function getMarinadeBlockRewards(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/validators/block-rewards");
  return { ...mkQueryPreview("marinade_block_rewards", "Marinade validator block rewards data"), data: data as Record<string, unknown> };
}

// ── 31. Rewards ───────────────────────────────────────────────────────────────

export interface MarinadeRewardsParams {
  epochs?: number;
}

export async function getMarinadeRewards(
  params: MarinadeRewardsParams
): Promise<BuildResponse> {
  const qs = params.epochs ? `?epochs=${params.epochs}` : "";
  const data = await validatorsApiFetch(`/rewards${qs}`);
  return { ...mkQueryPreview("marinade_rewards", "Marinade staking rewards data (block, inflation, Jito, MEV)"), data: data as Record<string, unknown> };
}

// ── 32. Unstake Hints ─────────────────────────────────────────────────────────

export interface MarinadeUnstakeHintsParams {
  epoch?: number;
}

export async function getMarinadeUnstakeHints(
  params: MarinadeUnstakeHintsParams
): Promise<BuildResponse> {
  const qs = params.epoch !== undefined ? `?epoch=${params.epoch}` : "";
  const data = await validatorsApiFetch(`/unstake-hints${qs}`);
  return { ...mkQueryPreview("marinade_unstake_hints", "Marinade unstake hints for the current/specified epoch"), data: data as Record<string, unknown> };
}

// ── 33. Global Unstake Hints ──────────────────────────────────────────────────

export interface MarinadeGlobalUnstakeHintsParams {
  epoch?: number;
}

export async function getMarinadeGlobalUnstakeHints(
  params: MarinadeGlobalUnstakeHintsParams
): Promise<BuildResponse> {
  const qs = params.epoch !== undefined ? `?epoch=${params.epoch}` : "";
  const data = await validatorsApiFetch(`/global-unstake-hints${qs}`);
  return { ...mkQueryPreview("marinade_global_unstake_hints", "Marinade protocol-wide global unstake hints"), data: data as Record<string, unknown> };
}

// ── 34. Jito Stake Data ────────────────────────────────────────────────────────

export async function getMarinadeJito(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/jito");
  return { ...mkQueryPreview("marinade_jito", "Marinade Jito stake data per validator"), data: data as Record<string, unknown> };
}

// ── 35. MEV Data ──────────────────────────────────────────────────────────────

export async function getMarinadeMev(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/mev");
  return { ...mkQueryPreview("marinade_mev", "Marinade MEV data per validator"), data: data as Record<string, unknown> };
}

// ── 36. Staking Report ────────────────────────────────────────────────────────

export async function getMarinadeStakingReport(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/reports/staking");
  return { ...mkQueryPreview("marinade_staking_report", "Marinade planned staking report"), data: data as Record<string, unknown> };
}

// ── 37. Scoring Report ────────────────────────────────────────────────────────

export async function getMarinadeScoringReport(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/reports/scoring");
  return { ...mkQueryPreview("marinade_scoring_report", "Marinade validator scoring report"), data: data as Record<string, unknown> };
}

// ── 38. Commission Changes ────────────────────────────────────────────────────

export async function getMarinadeCommissionChanges(): Promise<BuildResponse> {
  const data = await validatorsApiFetch("/reports/commission-changes");
  return { ...mkQueryPreview("marinade_commission_changes", "Marinade validator commission change history"), data: data as Record<string, unknown> };
}

// ── api.marinade.finance endpoints ────────────────────────────────────────────

const MARINADE_HIST_API = "https://api.marinade.finance";

const MSOL_MINT_ADDR  = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So";
const LP_TOKEN_MINT   = "LPmSozJJ8Jh69ut2WP3XmVohTjL4ipR18yiCzxrUmVj";

async function marinadeHistFetch(path: string): Promise<unknown> {
  const resp = await fetch(`${MARINADE_HIST_API}${path}`);
  if (!resp.ok) throw appError(`Marinade API error ${resp.status}: ${path}`);
  const ct = resp.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return resp.json();
  // text/plain endpoints return a bare number
  const text = await resp.text();
  return parseFloat(text);
}

// ── 39. mSOL APY for a period ─────────────────────────────────────────────────

export interface MarinadeMsolApyParams {
  /**
   * Period string: e.g. "7d" (7 days), "2w" (2 weeks), "1y" (1 year).
   * Format: <number><unit> where unit is d=days, w=weeks, m=months, y=years.
   */
  period: string;
  /** Optional point-in-time (ISO 8601) to calculate APY as-of */
  time?: string;
}

export async function getMarinadeMsolApy(params: MarinadeMsolApyParams): Promise<BuildResponse> {
  if (!params.period) throw appError("period is required (e.g. '7d', '2w', '1y')");
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/msol/apy/${encodeURIComponent(params.period)}${qs}`);
  return {
    ...mkQueryPreview("marinade_msol_apy", `mSOL APY for period ${params.period}`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 40. mSOL-SOL LP APY for a period ─────────────────────────────────────────

export interface MarinadeLpApyParams {
  /**
   * Period string: e.g. "7d", "2w", "1y".
   */
  period: string;
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeLpApy(params: MarinadeLpApyParams): Promise<BuildResponse> {
  if (!params.period) throw appError("period is required (e.g. '7d', '2w', '1y')");
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/lp/apy/${encodeURIComponent(params.period)}${qs}`);
  return {
    ...mkQueryPreview("marinade_lp_apy", `mSOL-SOL LP APY for period ${params.period}`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 41. mSOL-SOL LP Price ─────────────────────────────────────────────────────

export interface MarinadeLpPriceParams {
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeLpPrice(params: MarinadeLpPriceParams): Promise<BuildResponse> {
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/lp/price${qs}`);
  return {
    ...mkQueryPreview("marinade_lp_price", "mSOL-SOL LP token price in SOL", params as unknown as Record<string, unknown>),
    data: { lpPriceInSol: data },
  };
}

// ── 42. mSOL Supply ───────────────────────────────────────────────────────────

export interface MarinadeMsolSupplyParams {
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeMsolSupply(params: MarinadeMsolSupplyParams): Promise<BuildResponse> {
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/msol/supply${qs}`);
  const lamports = data as number;
  return {
    ...mkQueryPreview("marinade_msol_supply", "Total mSOL supply", params as unknown as Record<string, unknown>),
    data: { supplyLamports: lamports, supplyMsol: (lamports / 1e9).toFixed(6) },
  };
}

// ── 43. mSOL Price in SOL ─────────────────────────────────────────────────────

export interface MarinadeMsolPriceSolParams {
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeMsolPriceSol(params: MarinadeMsolPriceSolParams): Promise<BuildResponse> {
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/msol/price_sol${qs}`);
  return {
    ...mkQueryPreview("marinade_msol_price_sol", "mSOL price in SOL", params as unknown as Record<string, unknown>),
    data: { msolPriceInSol: data },
  };
}

// ── 44. mSOL Price in USD ─────────────────────────────────────────────────────

export async function getMarinadeMsolPriceUsd(): Promise<BuildResponse> {
  const data = await marinadeHistFetch("/msol/price_usd");
  return {
    ...mkQueryPreview("marinade_msol_price_usd", "mSOL price in USD"),
    data: { msolPriceInUsd: data },
  };
}

// ── 45. Farm Stats ────────────────────────────────────────────────────────────

export interface MarinadeFarmStatsParams {
  /**
   * Token to query: "msol" or "lp".
   * Maps to mSOL mint or mSOL-SOL LP token mint respectively.
   */
  token: "msol" | "lp";
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeFarmStats(params: MarinadeFarmStatsParams): Promise<BuildResponse> {
  if (!params.token) throw appError("token is required: 'msol' or 'lp'");
  const mintAddress = params.token === "lp" ? LP_TOKEN_MINT : MSOL_MINT_ADDR;
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/farm/${mintAddress}${qs}`);
  return {
    ...mkQueryPreview("marinade_farm_stats", `Marinade ${params.token.toUpperCase()} farm stats`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 46. TVL History ───────────────────────────────────────────────────────────

export interface MarinadeTlvHistoryParams {
  /** Start date (ISO 8601), e.g. "2025-01-01T00:00:00Z" */
  from: string;
  /** End date (ISO 8601), e.g. "2025-04-01T00:00:00Z" */
  to: string;
}

export async function getMarinadeTlvHistory(params: MarinadeTlvHistoryParams): Promise<BuildResponse> {
  if (!params.from) throw appError("from is required (ISO 8601 date-time)");
  if (!params.to) throw appError("to is required (ISO 8601 date-time)");
  const qs = `?from=${encodeURIComponent(params.from)}&to=${encodeURIComponent(params.to)}`;
  const data = await marinadeHistFetch(`/tlv/history${qs}`);
  return {
    ...mkQueryPreview("marinade_tlv_history", `Marinade TVL history from ${params.from} to ${params.to}`, params as unknown as Record<string, unknown>),
    data: { snapshots: data },
  };
}

// ── 47. TVL (current or point-in-time) ───────────────────────────────────────

export interface MarinadeTlvParams {
  /** Optional point-in-time (ISO 8601) */
  time?: string;
}

export async function getMarinadeTlv(params: MarinadeTlvParams): Promise<BuildResponse> {
  const qs = params.time ? `?time=${encodeURIComponent(params.time)}` : "";
  const data = await marinadeHistFetch(`/tlv${qs}`);
  return {
    ...mkQueryPreview("marinade_tlv", "Marinade Total Value Locked (TVL)", params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── snapshots-api.marinade.finance endpoints ──────────────────────────────────

const SNAPSHOTS_API = "https://snapshots-api.marinade.finance";

async function snapshotsApiFetch(path: string): Promise<unknown> {
  const resp = await fetch(`${SNAPSHOTS_API}${path}`);
  if (!resp.ok) throw appError(`Marinade snapshots API error ${resp.status}: ${path}`);
  return resp.json();
}

// ── 48. mSOL Balance (latest snapshot) ───────────────────────────────────────

export interface MarinadeSnapshotMsolParams {
  /** Base58 public key of the wallet to query */
  pubkey: string;
}

export async function getMarinadeSnapshotMsol(
  params: MarinadeSnapshotMsolParams
): Promise<BuildResponse> {
  if (!params.pubkey) throw appError("pubkey is required");
  const data = await snapshotsApiFetch(`/v1/snapshot/latest/msol/${params.pubkey}`);
  return {
    ...mkQueryPreview("marinade_snapshot_msol", `mSOL balance (latest snapshot) for ${params.pubkey.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 49. VeMNDE Balance (latest snapshot) ─────────────────────────────────────

export interface MarinadeSnapshotVemndeParams {
  /** Base58 public key of the wallet to query */
  pubkey: string;
}

export async function getMarinadeSnapshotVemnde(
  params: MarinadeSnapshotVemndeParams
): Promise<BuildResponse> {
  if (!params.pubkey) throw appError("pubkey is required");
  const data = await snapshotsApiFetch(`/v1/snapshot/latest/vemnde/${params.pubkey}`);
  return {
    ...mkQueryPreview("marinade_snapshot_vemnde", `VeMNDE balance (latest snapshot) for ${params.pubkey.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 50. All Staker Balances for a Wallet ─────────────────────────────────────

export interface MarinadeStakersAllParams {
  /** Base58 public key of the wallet */
  pubkey: string;
}

export async function getMarinadeStakersAll(
  params: MarinadeStakersAllParams
): Promise<BuildResponse> {
  if (!params.pubkey) throw appError("pubkey is required");
  const data = await snapshotsApiFetch(`/v1/stakers/all/${params.pubkey}`);
  return {
    ...mkQueryPreview("marinade_stakers_all", `All stake balances for ${params.pubkey.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 51. Native Stake Balance for a Wallet ────────────────────────────────────

export interface MarinadeStakersNsParams {
  /** Base58 public key of the wallet */
  pubkey: string;
}

export async function getMarinadeStakersNs(
  params: MarinadeStakersNsParams
): Promise<BuildResponse> {
  if (!params.pubkey) throw appError("pubkey is required");
  const data = await snapshotsApiFetch(`/v1/stakers/ns/${params.pubkey}`);
  return {
    ...mkQueryPreview("marinade_stakers_ns", `Native stake balance for ${params.pubkey.slice(0, 8)}…`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 52. All Native Stake Balances (protocol-wide) ────────────────────────────

export async function getMarinadeStakersNsAll(): Promise<BuildResponse> {
  const data = await snapshotsApiFetch("/v1/stakers/ns/all");
  return {
    ...mkQueryPreview("marinade_stakers_ns_all", "Native stake balances for all Marinade stakers"),
    data: data as Record<string, unknown>,
  };
}

// ── 53. Latest mSOL Governance Votes ─────────────────────────────────────────

export async function getMarinadeVotesMsolLatest(): Promise<BuildResponse> {
  const data = await snapshotsApiFetch("/v1/votes/msol/latest");
  return {
    ...mkQueryPreview("marinade_votes_msol_latest", "Latest Marinade mSOL governance votes"),
    data: data as Record<string, unknown>,
  };
}

// ── 54. All mSOL Governance Votes (date range) ───────────────────────────────

export interface MarinadeVotesMsolAllParams {
  /** Start date (ISO 8601), e.g. "2025-01-01T00:00:00Z" */
  startDate: string;
  /** End date (ISO 8601), e.g. "2025-04-01T00:00:00Z" */
  endDate: string;
}

export async function getMarinadeVotesMsolAll(
  params: MarinadeVotesMsolAllParams
): Promise<BuildResponse> {
  if (!params.startDate) throw appError("startDate is required (ISO 8601)");
  if (!params.endDate) throw appError("endDate is required (ISO 8601)");
  const qs = `?startDate=${encodeURIComponent(params.startDate)}&endDate=${encodeURIComponent(params.endDate)}`;
  const data = await snapshotsApiFetch(`/v1/votes/msol/all${qs}`);
  return {
    ...mkQueryPreview("marinade_votes_msol_all", `mSOL governance votes from ${params.startDate} to ${params.endDate}`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}

// ── 55. Latest VeMNDE Governance Votes ───────────────────────────────────────

export async function getMarinadeVotesVemndeLatest(): Promise<BuildResponse> {
  const data = await snapshotsApiFetch("/v1/votes/vemnde/latest");
  return {
    ...mkQueryPreview("marinade_votes_vemnde_latest", "Latest Marinade veMNDE governance votes"),
    data: data as Record<string, unknown>,
  };
}

// ── 56. All VeMNDE Governance Votes (date range) ─────────────────────────────

export interface MarinadeVotesVemndeAllParams {
  /** Start date (ISO 8601) */
  startDate: string;
  /** End date (ISO 8601) */
  endDate: string;
}

export async function getMarinadeVotesVemndeAll(
  params: MarinadeVotesVemndeAllParams
): Promise<BuildResponse> {
  if (!params.startDate) throw appError("startDate is required (ISO 8601)");
  if (!params.endDate) throw appError("endDate is required (ISO 8601)");
  const qs = `?startDate=${encodeURIComponent(params.startDate)}&endDate=${encodeURIComponent(params.endDate)}`;
  const data = await snapshotsApiFetch(`/v1/votes/vemnde/all${qs}`);
  return {
    ...mkQueryPreview("marinade_votes_vemnde_all", `veMNDE governance votes from ${params.startDate} to ${params.endDate}`, params as unknown as Record<string, unknown>),
    data: data as Record<string, unknown>,
  };
}
