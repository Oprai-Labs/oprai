/**
 * Kamino farm staking — KMNO governance stake / unstake + reward claim.
 *
 * These actions require the @kamino-finance klend/farms SDKs, which speak the
 * new @solana/kit (web3.js v2) API. The rest of this service is web3.js v1, so
 * this module is self-contained: it builds instructions with the SDK, then
 * bridges them into an UNSIGNED base64 v0 transaction that the frontend
 * (web3.js v1 `VersionedTransaction.deserialize`) signs. We never hold a key —
 * the fee payer is a noop signer carrying only the user's address.
 *
 * The Rust solana-service (the gateway's upstream) delegates kamino_stake /
 * kamino_unstake / kamino_claim_rewards here because it has no Kamino SDK crate.
 */
import {
  address,
  pipe,
  createNoopSigner,
  createSolanaRpc,
  createTransactionMessage,
  setTransactionMessageFeePayerSigner,
  setTransactionMessageLifetimeUsingBlockhash,
  appendTransactionMessageInstructions,
  compileTransaction,
  getBase64EncodedWireTransaction,
  type Instruction,
} from "@solana/kit";
import Decimal from "decimal.js/decimal";
// These farm helpers aren't re-exported from the klend-sdk root, only from the
// classes/farm_utils module — import the subpath directly (the package has no
// `exports` restriction, so the deep path resolves at runtime and for types).
import {
  getFarmStakeIxs,
  getFarmUnstakeAndWithdrawIxs,
  getUserSharesInTokensStakedInFarm,
} from "@kamino-finance/klend-sdk/dist/classes/farm_utils";
import { Farms } from "@kamino-finance/farms-sdk";
import { v4 as uuidv4 } from "uuid";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";

// The single on-chain Kamino Farms farm whose stake token is KMNO — found via
// getProgramAccounts(memcmp KMNO mint @ token.mint) and verified by decoding the
// FarmState on mainnet (stake mint @ offset 72 == KMNo3nJs…). KMNO governance
// staking deposits go here. There is exactly one such farm.
const KMNO_STAKING_FARM = "2sFZDpBn4sA42uNbAD6QzQ98rPSmqnPyksYe6SJKVvay";
const KMNO_DECIMALS = 6;

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(),
    type,
    description,
    estimatedFee: "~0.00001 SOL",
    params,
    warnings: [],
    requiresApproval: false,
  };
}

function rpc() {
  return createSolanaRpc(config.solanaRpc);
}

/** Compile kit instructions into an unsigned base64 v0 tx for the wallet to sign. */
async function toBase64V0(instructions: Instruction[], userWallet: string): Promise<string> {
  if (instructions.length === 0) {
    throw appError("Nothing to do for this action", 400, "NO_OP");
  }
  const feePayer = createNoopSigner(address(userWallet));
  const { value: latestBlockhash } = await rpc().getLatestBlockhash().send();
  const message = pipe(
    createTransactionMessage({ version: 0 }),
    (m) => setTransactionMessageFeePayerSigner(feePayer, m),
    (m) => setTransactionMessageLifetimeUsingBlockhash(latestBlockhash, m),
    (m) => appendTransactionMessageInstructions(instructions, m),
  );
  return getBase64EncodedWireTransaction(compileTransaction(message));
}

/** UI decimal amount → base-unit Decimal (lamports) the farm SDK expects. */
function toLamports(amount: string, decimals: number): Decimal {
  const d = new Decimal(amount);
  if (!d.isFinite() || d.lte(0)) {
    throw appError("amount must be a positive number", 400, "INVALID_PARAMS");
  }
  return d.mul(new Decimal(10).pow(decimals)).floor();
}

export interface KaminoFarmParams {
  /** Decimal amount of KMNO (or the farm's stake token) to stake/unstake. */
  amount?: string;
  /** Farm address; defaults to the KMNO governance staking farm. */
  farm?: string;
}

/** Stake KMNO into the governance staking farm. */
export async function buildKaminoStake(params: KaminoFarmParams, userWallet: string): Promise<BuildResponse> {
  const farm = params.farm ?? KMNO_STAKING_FARM;
  const lamports = toLamports(params.amount ?? "", KMNO_DECIMALS);
  const user = createNoopSigner(address(userWallet));
  const ixs = await getFarmStakeIxs(rpc(), user, lamports, address(farm));
  const tx = await toBase64V0(ixs, userWallet);
  return {
    preview: preview("kamino_stake", `Stake ${params.amount} KMNO`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

/** Unstake KMNO from the farm and withdraw it back to the user's wallet. */
export async function buildKaminoUnstake(params: KaminoFarmParams, userWallet: string): Promise<BuildResponse> {
  const farm = params.farm ?? KMNO_STAKING_FARM;
  const user = createNoopSigner(address(userWallet));
  // "all"/"max"/empty → the user's full staked balance (read from the farm),
  // since the SDK needs an explicit lamport amount.
  const raw = (params.amount ?? "").trim().toLowerCase();
  let lamports: Decimal;
  if (raw === "" || raw === "all" || raw === "max" || raw === "full") {
    const staked = await getUserSharesInTokensStakedInFarm(rpc(), address(userWallet), address(farm), KMNO_DECIMALS);
    if (!staked || staked.lte(0)) {
      throw appError("You have no staked KMNO to unstake", 400, "NOTHING_STAKED");
    }
    lamports = staked.mul(new Decimal(10).pow(KMNO_DECIMALS)).floor();
  } else {
    lamports = toLamports(params.amount ?? "", KMNO_DECIMALS);
  }
  const { unstakeIx, withdrawIx } = await getFarmUnstakeAndWithdrawIxs(rpc(), user, lamports, address(farm));
  const tx = await toBase64V0([unstakeIx, withdrawIx], userWallet);
  return {
    preview: preview("kamino_unstake", `Unstake ${params.amount} KMNO`, params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

/** Claim all pending farm rewards for the user on the given (or KMNO) farm. */
export async function buildKaminoClaimRewards(params: KaminoFarmParams, userWallet: string): Promise<BuildResponse> {
  const farm = params.farm ?? KMNO_STAKING_FARM;
  const user = createNoopSigner(address(userWallet));
  const farms = new Farms(rpc());
  // isDelegated=false: the KMNO staking farm is a standard (non-delegated) farm.
  // The SDK throws if the user has no farm user-state (never staked) — turn that
  // (and an empty ix list) into a clear message instead of a 500.
  let ixs: Instruction[];
  try {
    ixs = await farms.claimForUserForFarmAllRewardsIx(user, address(userWallet), address(farm), false);
  } catch {
    throw appError("No staking position or rewards to claim on this farm", 400, "NO_REWARDS");
  }
  if (ixs.length === 0) {
    throw appError("No rewards available to claim on this farm", 400, "NO_REWARDS");
  }
  const tx = await toBase64V0(ixs, userWallet);
  return {
    preview: preview("kamino_claim_rewards", "Claim Kamino farm rewards", params as unknown as Record<string, unknown>),
    transaction: tx,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}
