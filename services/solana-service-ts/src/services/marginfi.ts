/**
 * MarginFi v2 — @mrgnlabs/marginfi-client-v2 SDK (v6)
 *
 * Uses the official TypeScript SDK to build all transactions.
 * Transactions are built unsigned (via make*Tx methods) and returned to the
 * frontend for wallet signing.
 */

import {
  MarginfiClient,
  getConfig,
  MarginfiAccountWrapper,
  MarginRequirementType,
  type MarginfiAccount,
} from "@mrgnlabs/marginfi-client-v2";
// SolanaTransaction used in JSDoc only
import {
  Connection, PublicKey, Transaction, VersionedTransaction,
  TransactionInstruction, AddressLookupTableAccount,
} from "@solana/web3.js";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

// ─────────────────────────────────────────────────────────────────────────────
// Wallet stub — builds transactions without signing
// ─────────────────────────────────────────────────────────────────────────────

// The SDK uses its own @solana/web3.js (nested dependency). We use `as any`
// to bridge the type mismatch between the two copies of web3.js.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildWallet(publicKey: PublicKey): any {
  return {
    publicKey,
    signTransaction: async (tx: unknown) => tx,
    signAllTransactions: async (txs: unknown[]) => txs,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Client factory
// ─────────────────────────────────────────────────────────────────────────────

// Pass an existing connection to avoid creating duplicate RPC connections in
// operations that also need the connection for other calls (e.g. LUT fetch).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function getClient(userWallet: string, connection?: Connection): Promise<MarginfiClient> {
  const conn = connection ?? new Connection(config.solanaRpc, "confirmed");
  const marginfiConfig = getConfig("production");
  const wallet = buildWallet(new PublicKey(userWallet));
  // cast: MarginFi bundles its own web3.js — Connection is structurally identical
  return MarginfiClient.fetch(marginfiConfig, wallet, conn as unknown as any);
}

async function getUserAccounts(client: MarginfiClient): Promise<MarginfiAccountWrapper[]> {
  return client.getMarginfiAccountsForAuthority();
}

// Serialize a single SolanaTransaction (ExtendedTransaction | ExtendedV0Transaction) to base64.
function serializeOneTx(tx: unknown): string {
  if (tx && typeof tx === "object" && "serialize" in (tx as object)) {
    try {
      const vt = tx as VersionedTransaction;
      if (vt.message) return Buffer.from(vt.serialize()).toString("base64");
    } catch { /* fall through */ }
    const legacy = tx as Transaction;
    return legacy.serialize({ requireAllSignatures: false }).toString("base64");
  }
  throw appError("Cannot serialize transaction: unknown type", 500, "SERIALIZE_ERROR");
}

// For single-tx SDK returns (ExtendedTransaction | ExtendedV0Transaction | SolanaTransaction).
function serializeSolanaTx(tx: unknown): string {
  return serializeOneTx(tx);
}

interface SerializedResult {
  transactions: string[];
  actionTxIndex: number;
  transaction: string;   // = transactions[actionTxIndex], backwards compat
  txOverflown: boolean;
}

// For multi-tx SDK returns (TransactionBuilderResult / FlashloanActionResult).
function serializeBuilderResult(result: unknown): SerializedResult {
  if (!result || typeof result !== "object" || !("transactions" in (result as object))) {
    const tx = serializeOneTx(result);
    return { transactions: [tx], actionTxIndex: 0, transaction: tx, txOverflown: false };
  }
  const r = result as { transactions: unknown[]; actionTxIndex: number; txOverflown?: boolean };
  const txs = r.transactions.map(serializeOneTx);
  const idx = r.actionTxIndex ?? 0;
  return {
    transactions: txs,
    actionTxIndex: idx,
    transaction: txs[idx] ?? txs[0],
    txOverflown: r.txOverflown ?? false,
  };
}

// Derive warnings from a SerializedResult.
function buildResultWarnings(s: SerializedResult): string[] {
  const w: string[] = [];
  if (s.txOverflown) {
    w.push("Transaction size exceeds limit (txOverflown=true). Reduce the amount or split into smaller operations.");
  }
  if (s.transactions.length > 1) {
    w.push(`Requires ${s.transactions.length} transactions (e.g. oracle updates + main action). Submit them in order.`);
  }
  return w;
}

function preview(type: string, description: string, params: Record<string, unknown>, warnings: string[] = []): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings, requiresApproval: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Param types
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiCreateAccountParams {
  accountLabel?: string;
}

export interface MarginfiDepositParams {
  bank: string;       // token mint or bank address
  amount: number;
  accountAddress?: string;
}

export interface MarginfiWithdrawParams {
  bank: string;
  amount: number;
  withdrawAll?: boolean;
  accountAddress?: string;
}

export interface MarginfiBorrowParams {
  bank: string;
  amount: number;
  accountAddress?: string;
}

export interface MarginfiRepayParams {
  bank: string;
  amount: number;
  repayAll?: boolean;
  accountAddress?: string;
}

export interface MarginfiLiquidateParams {
  liquidateeAccount: string;
  assetBank: string;
  liabilityBank: string;
  amount: number;
  accountAddress?: string;
}

export interface MarginfiCloseAccountParams {
  accountAddress?: string;
}

export interface MarginfiClaimEmissionsParams {
  accountAddress?: string;
  banks?: string[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function getBankByMintOrAddress(client: MarginfiClient, tokenOrAddress: string) {
  // Try by token symbol first (e.g. "SOL", "USDC")
  const bySymbol = client.getBankByTokenSymbol(tokenOrAddress);
  if (bySymbol) return bySymbol;
  // Try by bank public key or mint address
  try {
    const pk = new PublicKey(tokenOrAddress);
    const byPk = client.getBankByPk(pk);
    if (byPk) return byPk;
    const byMint = client.getBankByMint(pk);
    if (byMint) return byMint;
  } catch { /* not a valid public key */ }
  throw appError(`Bank not found for: ${tokenOrAddress}`, 404, "BANK_NOT_FOUND");
}

async function resolveAccount(
  client: MarginfiClient,
  accountAddress?: string
): Promise<MarginfiAccountWrapper> {
  if (accountAddress) {
    const account = await MarginfiAccountWrapper.fetch(
      new PublicKey(accountAddress),
      client
    );
    if (!account) throw appError("MarginFi account not found", 404, "ACCOUNT_NOT_FOUND");
    return account;
  }
  const accounts = await getUserAccounts(client);
  if (accounts.length === 0)
    throw appError("No MarginFi account found. Create one first.", 404, "NO_ACCOUNT");
  return accounts[0];
}

// ─────────────────────────────────────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────────────────────────────────────

export async function buildCreateAccount(
  _params: MarginfiCreateAccountParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const tx = await client.createMarginfiAccountTx();
  return {
    preview: preview("marginfi_create_account", "Create MarginFi account", _params as unknown as Record<string, unknown>),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildDeposit(
  params: MarginfiDepositParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bank = getBankByMintOrAddress(client, params.bank);
  const account = await resolveAccount(client, params.accountAddress);

  const tx = await account.makeDepositTx(params.amount, bank.address);
  return {
    preview: preview(
      "marginfi_deposit",
      `Deposit ${params.amount} ${bank.tokenSymbol ?? params.bank} into MarginFi`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildWithdraw(
  params: MarginfiWithdrawParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bank = getBankByMintOrAddress(client, params.bank);
  const account = await resolveAccount(client, params.accountAddress);

  const result = await account.makeWithdrawTx(
    params.amount,
    bank.address,
    params.withdrawAll ?? false
  );
  const serialized = serializeBuilderResult(result);
  return {
    preview: preview(
      "marginfi_withdraw",
      `Withdraw ${params.withdrawAll ? "all" : params.amount} ${bank.tokenSymbol ?? params.bank} from MarginFi`,
      params as unknown as Record<string, unknown>,
      buildResultWarnings(serialized)
    ),
    ...serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildBorrow(
  params: MarginfiBorrowParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bank = getBankByMintOrAddress(client, params.bank);
  const account = await resolveAccount(client, params.accountAddress);

  const result = await account.makeBorrowTx(params.amount, bank.address);
  const serialized = serializeBuilderResult(result);
  return {
    preview: preview(
      "marginfi_borrow",
      `Borrow ${params.amount} ${bank.tokenSymbol ?? params.bank} from MarginFi`,
      params as unknown as Record<string, unknown>,
      buildResultWarnings(serialized)
    ),
    ...serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildRepay(
  params: MarginfiRepayParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bank = getBankByMintOrAddress(client, params.bank);
  const account = await resolveAccount(client, params.accountAddress);

  const tx = await account.makeRepayTx(
    params.amount,
    bank.address,
    params.repayAll ?? false
  );
  return {
    preview: preview(
      "marginfi_repay",
      `Repay ${params.repayAll ? "all" : params.amount} ${bank.tokenSymbol ?? params.bank} to MarginFi`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildLiquidate(
  params: MarginfiLiquidateParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const assetBank = getBankByMintOrAddress(client, params.assetBank);
  const liabBank = getBankByMintOrAddress(client, params.liabilityBank);
  const liquidatorAccount = await resolveAccount(client, params.accountAddress);
  const liquidateeAccount = await MarginfiAccountWrapper.fetch(
    new PublicKey(params.liquidateeAccount),
    client
  );
  if (!liquidateeAccount)
    throw appError("Liquidatee account not found", 404, "ACCOUNT_NOT_FOUND");

  // makeLendingAccountLiquidateIx returns InstructionsWrapper — wrap in tx
  const ixsWrapper = await liquidatorAccount.makeLendingAccountLiquidateIx(
    liquidateeAccount as unknown as MarginfiAccount,
    assetBank.address,
    params.amount,
    liabBank.address
  );

  const connection = new Connection(config.solanaRpc, "confirmed");
  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    recentBlockhash: blockhash,
    feePayer: new PublicKey(userWallet),
  });
  tx.add(...ixsWrapper.instructions);

  return {
    preview: preview(
      "marginfi_liquidate",
      `Liquidate ${params.amount} of account ${params.liquidateeAccount.slice(0, 8)}...`,
      params as unknown as Record<string, unknown>
    ),
    transaction: tx.serialize({ requireAllSignatures: false }).toString("base64"),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildCloseAccount(
  params: MarginfiCloseAccountParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const account = await resolveAccount(client, params.accountAddress);
  const tx = await account.makeCloseAccountTx();
  return {
    preview: preview("marginfi_close_account", "Close MarginFi account", params as unknown as Record<string, unknown>),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

export async function buildClaimEmissions(
  params: MarginfiClaimEmissionsParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const account = await resolveAccount(client, params.accountAddress);

  // Resolve bank addresses (all active banks if none specified)
  let bankAddresses: PublicKey[];
  if (params.banks && params.banks.length > 0) {
    bankAddresses = params.banks.map((b) => getBankByMintOrAddress(client, b).address);
  } else {
    bankAddresses = account.activeBalances.map((b) => b.bankPk);
  }

  const tx = await account.makeWithdrawEmissionsTx(bankAddresses);
  return {
    preview: preview("marginfi_claim_emissions", "Claim MarginFi emissions", params as unknown as Record<string, unknown>),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Query actions (read-only)
// ─────────────────────────────────────────────────────────────────────────────

export async function getAccountInfo(
  params: { accountAddress?: string },
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const account = await resolveAccount(client, params.accountAddress);

  const healthComponents = account.computeHealthComponents(MarginRequirementType.Maintenance);
  const healthFactor = healthComponents.assets.isZero()
    ? 0
    : healthComponents.assets.dividedBy(healthComponents.liabilities).toNumber();

  const balances = account.activeBalances.map((b) => {
    const bank = client.getBankByPk(b.bankPk);
    return {
      bankAddress: b.bankPk.toBase58(),
      tokenSymbol: bank?.tokenSymbol ?? "unknown",
      assetShares: b.assetShares.toNumber(),
      liabilityShares: b.liabilityShares.toNumber(),
    };
  });

  return {
    preview: preview("marginfi_account_info", "MarginFi account info", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      address: account.address.toBase58(),
      authority: account.authority.toBase58(),
      healthFactor,
      balances,
    },
  };
}

export async function getBanks(
  _params: Record<string, unknown>,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bankList = Array.from(client.banks.entries()).map(([addr, bank]) => {
    const rates = bank.computeInterestRates();
    return {
      address: addr,
      tokenMint: bank.mint.toBase58(),
      tokenSymbol: bank.tokenSymbol,
      depositApy: rates.lendingRate.toNumber(),
      borrowApy: rates.borrowingRate.toNumber(),
    };
  });
  return {
    preview: preview("marginfi_banks", "MarginFi bank list", {}),
    additionalSignersRequired: 0, isCrossChain: false,
    data: { banks: bankList, count: bankList.length },
  };
}

export async function getUserAccounts_(
  _params: Record<string, unknown>,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const accounts = await getUserAccounts(client);
  return {
    preview: preview("marginfi_user_accounts", "MarginFi user accounts", {}),
    additionalSignersRequired: 0, isCrossChain: false,
    data: {
      accounts: accounts.map((a) => {
        const hc = a.computeHealthComponents(MarginRequirementType.Maintenance);
        const hf = hc.assets.isZero() ? 0 : hc.assets.dividedBy(hc.liabilities).toNumber();
        return {
          address: a.address.toBase58(),
          healthFactor: hf,
          balanceCount: a.activeBalances.length,
        };
      }),
      count: accounts.length,
    },
  };
}

/**
 * Detailed per-balance breakdown across all user accounts: each active
 * lending/borrow position with USD value, APY, and the underlying mint.
 *
 * The SDK's `computeBalanceUsdValue` returns a `BigNumber` (mrgn-common's
 * fixed-point wrapper) for the position's USD notional. APYs come from the
 * bank's `computeInterestRates()` — both returned as decimals (0.045 not 4.5).
 * We normalise to percentages here so the frontend doesn't have to.
 */
export async function getUserBalances(
  _params: Record<string, unknown>,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const accounts = await getUserAccounts(client);

  type BalanceRow = {
    accountAddress: string;
    healthFactor: number;
    bankAddress: string;
    tokenMint: string;
    tokenSymbol: string;
    side: "deposit" | "borrow";
    amount: number;
    usdValue: number;
    apy: number;
    weight: number;
    decimals: number;
  };
  const balances: BalanceRow[] = [];

  for (const acct of accounts) {
    const hc = acct.computeHealthComponents(MarginRequirementType.Maintenance);
    const hf = hc.assets.isZero() ? 0 : hc.assets.dividedBy(hc.liabilities).toNumber();
    const acctAddr = acct.address.toBase58();

    for (const bal of acct.activeBalances) {
      const bank = client.getBankByPk(bal.bankPk);
      if (!bank) continue;
      const rates = bank.computeInterestRates();
      const assetAmount = bal.computeQuantityUi(bank).assets.toNumber();
      const liabAmount = bal.computeQuantityUi(bank).liabilities.toNumber();
      const isBorrow = liabAmount > assetAmount;
      const amount = isBorrow ? liabAmount : assetAmount;
      if (amount <= 0) continue;
      // SDK exposes the per-balance USD via the bank's oracle. Compute on
      // both sides; the dominant one becomes the row's USD.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const oraclePx = ((bank as any).getPrice?.() ?? bank.computeAssetUsdValue?.(
        bank.getAssetQuantity(bal.assetShares), undefined, undefined,
      ))?.toNumber?.() ?? 0;
      const usd = amount * Number(oraclePx ?? 0);
      balances.push({
        accountAddress: acctAddr,
        healthFactor: hf,
        bankAddress: bal.bankPk.toBase58(),
        tokenMint: bank.mint.toBase58(),
        tokenSymbol: bank.tokenSymbol,
        side: isBorrow ? "borrow" : "deposit",
        amount,
        usdValue: usd,
        apy: 100 * (isBorrow ? rates.borrowingRate.toNumber() : rates.lendingRate.toNumber()),
        weight: isBorrow
          ? bank.config.liabilityWeightInit.toNumber()
          : bank.config.assetWeightInit.toNumber(),
        decimals: bank.mintDecimals,
      });
    }
  }

  return {
    preview: preview("marginfi_user_balances", "MarginFi user balances", {}),
    additionalSignersRequired: 0, isCrossChain: false,
    data: { balances, count: balances.length },
  };
}

export async function getHealth(
  params: { accountAddress?: string },
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const account = await resolveAccount(client, params.accountAddress);
  return {
    preview: preview("marginfi_health", "MarginFi account health", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false,
    data: (() => {
      const hc = account.computeHealthComponents(MarginRequirementType.Maintenance);
      const hf = hc.assets.isZero() ? 0 : hc.assets.dividedBy(hc.liabilities).toNumber();
      return {
        address: account.address.toBase58(),
        healthFactor: hf,
        isHealthy: hf >= 1,
      };
    })(),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Jupiter swap-instructions helper (used by loop + repayWithCollateral)
// ─────────────────────────────────────────────────────────────────────────────

type RawIx = {
  programId: string;
  accounts: { pubkey: string; isSigner: boolean; isWritable: boolean }[];
  data: string;
};

function decodeIx(raw: RawIx): TransactionInstruction {
  return new TransactionInstruction({
    programId: new PublicKey(raw.programId),
    keys: raw.accounts.map((a) => ({
      pubkey: new PublicKey(a.pubkey),
      isSigner: a.isSigner,
      isWritable: a.isWritable,
    })),
    data: Buffer.from(raw.data, "base64"),
  });
}

async function getJupiterSwapIxs(
  inputMint: string,
  outputMint: string,
  amountUi: number,
  inputDecimals: number,
  slippageBps: number,
  userWallet: string,
  connection: Connection
): Promise<{ instructions: TransactionInstruction[]; lookupTables: AddressLookupTableAccount[] }> {
  const amountRaw = Math.round(amountUi * 10 ** inputDecimals).toString();
  const apiKey = config.jupiterApiKey;
  const baseUrl = apiKey ? "https://api.jup.ag/swap/v1" : "https://quote-api.jup.ag/v6";
  const hdrs: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) hdrs["x-api-key"] = apiKey;

  const quoteRes = await fetch(
    `${baseUrl}/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amountRaw}&slippageBps=${slippageBps}`,
    { headers: hdrs }
  );
  if (!quoteRes.ok) throw appError(`Jupiter quote failed: ${await quoteRes.text()}`, 502, "JUP_ERROR");
  const quote = await quoteRes.json();

  const ixRes = await fetch(`${baseUrl}/swap-instructions`, {
    method: "POST",
    headers: hdrs,
    body: JSON.stringify({ quoteResponse: quote, userPublicKey: userWallet, wrapAndUnwrapSol: true }),
  });
  if (!ixRes.ok) throw appError(`Jupiter swap-instructions failed: ${await ixRes.text()}`, 502, "JUP_ERROR");
  const ixData = (await ixRes.json()) as {
    swapInstruction: RawIx;
    setupInstructions?: RawIx[];
    cleanupInstruction?: RawIx;
    addressLookupTableAddresses?: string[];
  };

  const instructions: TransactionInstruction[] = [
    ...(ixData.setupInstructions ?? []).map(decodeIx),
    decodeIx(ixData.swapInstruction),
    ...(ixData.cleanupInstruction ? [decodeIx(ixData.cleanupInstruction)] : []),
  ];

  const lookupTables: AddressLookupTableAccount[] = [];
  for (const addr of ixData.addressLookupTableAddresses ?? []) {
    const res = await connection.getAddressLookupTable(new PublicKey(addr));
    if (res.value) lookupTables.push(res.value);
  }

  return { instructions, lookupTables };
}

// ─────────────────────────────────────────────────────────────────────────────
// Loop (leveraged deposit)
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiLoopParams {
  /** Bank to deposit into — token symbol, mint address, or bank address */
  depositBank: string;
  /** Bank to borrow from */
  borrowBank: string;
  /** Initial deposit amount (UI units) */
  depositAmount: number;
  /** Borrow amount for each loop leg (UI units) */
  borrowAmount: number;
  /** Slippage for the internal Jupiter swap (bps, default 50) */
  slippageBps?: number;
  accountAddress?: string;
}

export async function buildLoop(
  params: MarginfiLoopParams,
  userWallet: string
): Promise<BuildResponse> {
  // Single connection shared between MarginFi client and LUT fetch
  const connection = new Connection(config.solanaRpc, "confirmed");
  const client = await getClient(userWallet, connection);
  const depositBank = getBankByMintOrAddress(client, params.depositBank);
  const borrowBank = getBankByMintOrAddress(client, params.borrowBank);
  const account = await resolveAccount(client, params.accountAddress);

  // Swap: borrowed token → deposit token
  const { instructions, lookupTables } = await getJupiterSwapIxs(
    borrowBank.mint.toBase58(),
    depositBank.mint.toBase58(),
    params.borrowAmount,
    borrowBank.mintDecimals,
    params.slippageBps ?? 50,
    userWallet,
    connection
  );

  const result = await account.makeLoopTxV2({
    depositAmount: params.depositAmount,
    borrowAmount: params.borrowAmount,
    depositBankAddress: depositBank.address,
    borrowBankAddress: borrowBank.address,
    swap: { instructions, lookupTables },
    // Ensure token accounts (ATAs) for both banks are initialised
    setupBankAddresses: [depositBank.address, borrowBank.address],
  });

  const serialized = serializeBuilderResult(result);
  if (serialized.txOverflown) {
    throw appError(
      "Loop transaction exceeds size limit. Reduce depositAmount or borrowAmount.",
      400,
      "TX_OVERFLOWN"
    );
  }
  return {
    preview: preview(
      "marginfi_loop",
      `Loop: deposit ${params.depositAmount} ${depositBank.tokenSymbol ?? params.depositBank}, borrow ${params.borrowAmount} ${borrowBank.tokenSymbol ?? params.borrowBank}`,
      params as unknown as Record<string, unknown>,
      buildResultWarnings(serialized)
    ),
    ...serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Repay with collateral
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiRepayWithCollateralParams {
  /** Collateral bank (will be withdrawn from) — token symbol, mint, or bank address */
  depositBank: string;
  /** Debt bank (will be repaid) */
  borrowBank: string;
  /** Amount of debt to repay (UI units) */
  repayAmount: number;
  /** Amount of collateral to withdraw and swap (UI units) */
  withdrawAmount: number;
  /** Repay entire debt balance */
  repayAll?: boolean;
  /** Withdraw entire collateral balance */
  withdrawAll?: boolean;
  /** Slippage for the internal Jupiter swap (bps, default 50) */
  slippageBps?: number;
  accountAddress?: string;
}

export async function buildRepayWithCollateral(
  params: MarginfiRepayWithCollateralParams,
  userWallet: string
): Promise<BuildResponse> {
  const connection = new Connection(config.solanaRpc, "confirmed");
  const client = await getClient(userWallet, connection);
  const depositBank = getBankByMintOrAddress(client, params.depositBank);
  const borrowBank = getBankByMintOrAddress(client, params.borrowBank);
  const account = await resolveAccount(client, params.accountAddress);

  // Swap: collateral token → debt token
  const { instructions, lookupTables } = await getJupiterSwapIxs(
    depositBank.mint.toBase58(),
    borrowBank.mint.toBase58(),
    params.withdrawAmount,
    depositBank.mintDecimals,
    params.slippageBps ?? 50,
    userWallet,
    connection
  );

  const result = await account.makeRepayWithCollatTxV2({
    repayAmount: params.repayAmount,
    withdrawAmount: params.withdrawAmount,
    borrowBankAddress: borrowBank.address,
    depositBankAddress: depositBank.address,
    withdrawAll: params.withdrawAll ?? false,
    repayAll: params.repayAll ?? false,
    swap: { instructions, lookupTables },
  });

  const serialized = serializeBuilderResult(result);
  if (serialized.txOverflown) {
    throw appError(
      "Repay-with-collateral transaction exceeds size limit. Reduce the amounts.",
      400,
      "TX_OVERFLOWN"
    );
  }
  return {
    preview: preview(
      "marginfi_repay_with_collateral",
      `Repay ${params.repayAll ? "all" : params.repayAmount} ${borrowBank.tokenSymbol ?? params.borrowBank} using ${depositBank.tokenSymbol ?? params.depositBank} collateral`,
      params as unknown as Record<string, unknown>,
      buildResultWarnings(serialized)
    ),
    ...serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Flash loan
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiFlashLoanParams {
  /** Bank to flash-borrow from */
  borrowBank: string;
  /** Amount to flash-borrow (UI units) */
  borrowAmount: number;
  /** Bank to repay the flash loan into */
  repayBank: string;
  /** Amount to repay (UI units) — typically same as borrowAmount */
  repayAmount: number;
  accountAddress?: string;
}

export async function buildFlashLoan(
  params: MarginfiFlashLoanParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const borrowBank = getBankByMintOrAddress(client, params.borrowBank);
  const repayBank = getBankByMintOrAddress(client, params.repayBank);
  const account = await resolveAccount(client, params.accountAddress);

  const borrowIxWrapper = await account.makeBorrowIx(params.borrowAmount, borrowBank.address);
  const repayIxWrapper = await account.makeRepayIx(params.repayAmount, repayBank.address, false);

  const tx = await account.buildFlashLoanTx({
    ixs: [
      ...borrowIxWrapper.instructions,
      ...repayIxWrapper.instructions,
    ],
  });

  return {
    preview: preview(
      "marginfi_flash_loan",
      `Flash loan: borrow ${params.borrowAmount} ${borrowBank.tokenSymbol ?? params.borrowBank}, repay ${params.repayAmount} ${repayBank.tokenSymbol ?? params.repayBank}`,
      params as unknown as Record<string, unknown>,
      ["Flash loan wraps borrow + repay instructions. Ensure repayAmount ≥ borrowAmount to avoid liquidation."]
    ),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Withdraw all (multiple banks at once)
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiWithdrawAllParams {
  /** List of banks + amounts to withdraw. If amount = 0, withdraws entire balance. */
  banks: { bank: string; amount: number }[];
  accountAddress?: string;
}

export async function buildWithdrawAll(
  params: MarginfiWithdrawAllParams,
  userWallet: string
): Promise<BuildResponse> {
  if (!params.banks || params.banks.length === 0) {
    throw appError("banks array must not be empty", 400, "INVALID_PARAMS");
  }
  for (const b of params.banks) {
    if (typeof b.amount !== "number" || b.amount <= 0) {
      throw appError(`Invalid amount for bank ${b.bank}: must be > 0`, 400, "INVALID_PARAMS");
    }
  }

  const client = await getClient(userWallet);
  const account = await resolveAccount(client, params.accountAddress);

  const bankWithAmounts = params.banks.map((b) => ({
    amount: b.amount,
    bankAddress: getBankByMintOrAddress(client, b.bank).address,
  }));

  const tx = await account.makeWithdrawAllTx(bankWithAmounts);
  return {
    preview: preview(
      "marginfi_withdraw_all",
      `Withdraw from ${params.banks.length} MarginFi bank(s)`,
      params as unknown as Record<string, unknown>
    ),
    transaction: serializeSolanaTx(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Move position (transfer balance to another MarginFi account)
// ─────────────────────────────────────────────────────────────────────────────

export interface MarginfiMovePositionParams {
  /** Bank whose position to move */
  bank: string;
  /** Amount to move (UI units) */
  amount: number;
  /** Destination MarginFi account address */
  destinationAccount: string;
  accountAddress?: string;
}

export async function buildMovePosition(
  params: MarginfiMovePositionParams,
  userWallet: string
): Promise<BuildResponse> {
  const client = await getClient(userWallet);
  const bank = getBankByMintOrAddress(client, params.bank);
  const account = await resolveAccount(client, params.accountAddress);

  const result = await account.makeMovePositionTx(
    params.amount,
    bank.address,
    new PublicKey(params.destinationAccount)
  );

  const serialized = serializeBuilderResult(result);
  return {
    preview: preview(
      "marginfi_move_position",
      `Move ${params.amount} ${bank.tokenSymbol ?? params.bank} to account ${params.destinationAccount.slice(0, 8)}...`,
      params as unknown as Record<string, unknown>,
      buildResultWarnings(serialized)
    ),
    ...serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}
