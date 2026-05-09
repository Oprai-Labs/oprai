/**
 * Streamflow Finance — @streamflow/stream SDK v11
 *
 * Transaction building strategy:
 *   - CREATE : client.buildCreateTransaction()  → partially signed tx (metadata keypair signs,
 *              user wallet signature missing) → returned as base64 for frontend to complete
 *   - CANCEL / WITHDRAW / TOPUP / TRANSFER / UPDATE :
 *              client.prepare*Instructions() → instructions array → build Transaction manually
 *              → serialize without requiring all signatures → base64 for frontend
 *   - LIST / GET_ONE : pure on-chain reads, no signing required
 */

import {
  SolanaStreamClient,
  getBN,
  StreamType,
  StreamDirection,
  WITHDRAW_AVAILABLE_AMOUNT,
  ICreateStreamData,
  ICancelData,
  IWithdrawData,
  ITopUpData,
  ITransferData,
  IUpdateData,
} from "@streamflow/stream";
import { Connection, PublicKey, Transaction } from "@solana/web3.js";
import BN from "bn.js";
import { config } from "../config";
import { BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuid } from "uuid";

// ─── Constants ────────────────────────────────────────────────────────────────

const MINT_MAP: Record<string, string> = {
  SOL:  "So11111111111111111111111111111111111111112",
  USDC: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  USDT: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
  BONK: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
  JUP:  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
  JTO:  "jtojtomepa8beP8AuQc6eXt5FriJwfFMwjx2v2jakG",
  PYTH: "HZ1JovNiVvGrGs74VtMB32xMSjXoGPSgR9xW2a7bFBX3",
  RAY:  "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
  MSOL: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
  JITOSOL: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
};

const TOKEN_DECIMALS: Record<string, number> = {
  SOL: 9, USDC: 6, USDT: 6, BONK: 5, JUP: 6, JTO: 9,
  PYTH: 6, RAY: 6, MSOL: 9, JITOSOL: 9,
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function resolveMint(mint: string): string {
  return MINT_MAP[mint.toUpperCase()] ?? mint;
}

function getDecimals(mint: string): number {
  return TOKEN_DECIMALS[mint.toUpperCase()] ?? 6;
}

function num(val: string | number | undefined, fallback = 0): number {
  if (val === undefined || val === null || val === "") return fallback;
  const n = Number(val);
  return isNaN(n) ? fallback : n;
}

function getClient(): SolanaStreamClient {
  return new SolanaStreamClient(config.solanaRpc);
}

function getConnection(): Connection {
  return new Connection(config.solanaRpc, "confirmed");
}

/** Minimal signer-like object — SDK only reads publicKey for instruction building */
function publicKeySender(wallet: string): { publicKey: PublicKey } {
  return { publicKey: new PublicKey(wallet) };
}

/** Serialize a transaction to base64 without requiring all signatures.
 *  Accepts both Transaction and VersionedTransaction (possibly from @streamflow/common's
 *  nested @solana/web3.js, which has structurally incompatible but functionally identical types). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function serialize(tx: any): string {
  const isVersioned = tx.version !== undefined;
  const buf: Buffer | Uint8Array = isVersioned
    ? tx.serialize()
    : tx.serialize({ requireAllSignatures: false, verifySignatures: false });
  return Buffer.from(buf).toString("base64");
}

function preview(
  actionType: string,
  description: string,
  params: Record<string, unknown>,
  warnings: string[] = [],
  fee = "~0.002 SOL",
): ActionPreview {
  return {
    id: uuid(),
    type: actionType,
    description,
    estimatedFee: fee,
    params,
    warnings,
    requiresApproval: true,
  };
}

// ─── BUILD: CREATE ────────────────────────────────────────────────────────────

export async function buildStreamflowCreate(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const mint = resolveMint(params.mint as string);
  const dec  = getDecimals(params.mint as string);
  const now  = Math.floor(Date.now() / 1000);
  const start = num(params.start as number, now);
  const period = num(params.period as number);

  const streamData: ICreateStreamData = {
    recipient:            params.recipient as string,
    tokenId:              mint,
    start,
    amount:               getBN(num(params.amount as string), dec),
    period,
    cliff:                num(params.cliff as number, start),
    cliffAmount:          getBN(num((params.cliff_amount ?? params.cliffAmount) as string, 0), dec),
    amountPerPeriod:      getBN(num((params.amount_per_period ?? params.amountPerPeriod) as string), dec),
    name:                 (params.name as string) ?? "OPRAI Stream",
    canTopup:             Boolean(params.can_topup ?? params.canTopup ?? false),
    cancelableBySender:   Boolean(params.cancelable_by_sender ?? params.cancelableBySender ?? true),
    cancelableByRecipient: Boolean(params.cancelable_by_recipient ?? params.cancelableByRecipient ?? false),
    transferableBySender:  Boolean(params.transferable_by_sender ?? params.transferableBySender ?? true),
    transferableByRecipient: Boolean(params.transferable_by_recipient ?? params.transferableByRecipient ?? false),
    automaticWithdrawal:  Boolean(params.automatic_withdrawal ?? params.automaticWithdrawal ?? false),
    withdrawalFrequency:  num((params.withdrawal_frequency ?? params.withdrawalFrequency) as number, period),
    partner:              (params.partner as string) ?? "",
  };

  const client = getClient();
  const sender = publicKeySender(userWallet);

  const { tx } = await client.buildCreateTransaction(streamData, {
    sender: sender as any,
    isNative: Boolean(params.is_native ?? params.isNative ?? false),
  });

  const warnings: string[] = [
    "Stream requires sender to maintain escrow balance.",
  ];
  if (streamData.automaticWithdrawal) warnings.push("Automatic withdrawal incurs an extra fee per period.");
  if (streamData.canTopup) warnings.push("Top-up enabled — stream duration can be extended.");

  return {
    preview: preview(
      "streamflow_create",
      `Stream ${params.amount} ${params.mint} → ${(params.recipient as string).slice(0, 8)}… every ${period}s`,
      params as Record<string, unknown>,
      warnings,
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── BUILD: CREATE MULTIPLE ───────────────────────────────────────────────────

export async function buildStreamflowCreateMultiple(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const mint = resolveMint(params.mint as string);
  const dec  = getDecimals(params.mint as string);
  const now  = Math.floor(Date.now() / 1000);
  const start = num(params.start as number, now);
  const period = num(params.period as number);

  const rawRecipients = params.recipients as Array<Record<string, unknown>>;

  const recipients = rawRecipients.map((r) => ({
    recipient:       r.recipient as string,
    amount:          getBN(num(r.amount as string), dec),
    name:            (r.name as string) ?? "OPRAI Stream",
    cliffAmount:     getBN(num((r.cliff_amount ?? r.cliffAmount) as string, 0), dec),
    amountPerPeriod: getBN(num((r.amount_per_period ?? r.amountPerPeriod) as string), dec),
  }));

  const multiData = {
    recipients,
    tokenId:             mint,
    start,
    period,
    cliff:               num(params.cliff as number, start),
    canTopup:            Boolean(params.can_topup ?? params.canTopup ?? false),
    cancelableBySender:  Boolean(params.cancelable_by_sender ?? params.cancelableBySender ?? true),
    cancelableByRecipient: Boolean(params.cancelable_by_recipient ?? params.cancelableByRecipient ?? false),
    transferableBySender:  Boolean(params.transferable_by_sender ?? params.transferableBySender ?? true),
    transferableByRecipient: Boolean(params.transferable_by_recipient ?? params.transferableByRecipient ?? false),
    automaticWithdrawal: Boolean(params.automatic_withdrawal ?? params.automaticWithdrawal ?? false),
    withdrawalFrequency: num((params.withdrawal_frequency ?? params.withdrawalFrequency) as number, period),
    partner:             (params.partner as string) ?? "",
  };

  const client = getClient();
  const sender = publicKeySender(userWallet);

  const batch = await client.buildCreateMultipleTransactions(multiData as any, {
    sender: sender as any,
    isNative: Boolean(params.is_native ?? params.isNative ?? false),
  });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const transactions = (batch.transactions ?? []).map((item: any) => serialize(item.tx));

  return {
    preview: preview(
      "streamflow_create_multiple",
      `Create ${recipients.length} streams | token=${params.mint}`,
      params as Record<string, unknown>,
      [`Creates ${recipients.length} separate stream accounts.`],
      `~${(0.002 * recipients.length).toFixed(3)} SOL`,
    ),
    transaction: transactions[0],
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { transactions, count: transactions.length },
  };
}

// ─── BUILD: CANCEL ────────────────────────────────────────────────────────────

export async function buildStreamflowCancel(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const id = (params.stream_id ?? params.streamId) as string;
  const client = getClient();
  const connection = getConnection();
  const invoker = publicKeySender(userWallet);

  const cancelData: ICancelData = { id };
  const ixs = await client.prepareCancelInstructions(cancelData, {
    invoker: invoker as any,
  });

  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    feePayer: new PublicKey(userWallet),
    recentBlockhash: blockhash,
  });
  tx.add(...ixs);

  return {
    preview: preview(
      "streamflow_cancel",
      `Cancel stream ${id.slice(0, 8)}…`,
      params as Record<string, unknown>,
      [
        "Unlocked tokens will be sent to recipient.",
        "Remaining locked tokens will be returned to sender.",
      ],
      "~0.001 SOL",
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── BUILD: WITHDRAW ──────────────────────────────────────────────────────────

export async function buildStreamflowWithdraw(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const id = (params.stream_id ?? params.streamId) as string;
  const hasAmount = params.amount !== undefined && params.amount !== "";
  const dec = 6;

  const client = getClient();
  const connection = getConnection();
  const invoker = publicKeySender(userWallet);

  const withdrawData: IWithdrawData = {
    id,
    amount: hasAmount
      ? getBN(num(params.amount as string), dec)
      : new BN(WITHDRAW_AVAILABLE_AMOUNT.toString()),
  };

  const ixs = await client.prepareWithdrawInstructions(withdrawData, {
    invoker: invoker as any,
  });

  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    feePayer: new PublicKey(userWallet),
    recentBlockhash: blockhash,
  });
  tx.add(...ixs);

  return {
    preview: preview(
      "streamflow_withdraw",
      hasAmount
        ? `Withdraw ${params.amount} tokens from stream ${id.slice(0, 8)}…`
        : `Withdraw all unlocked tokens from stream ${id.slice(0, 8)}…`,
      params as Record<string, unknown>,
      ["Only unlocked (vested) tokens can be withdrawn."],
      "~0.001 SOL",
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── BUILD: TOPUP ─────────────────────────────────────────────────────────────

export async function buildStreamflowTopup(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const id = (params.stream_id ?? params.streamId) as string;
  const dec = 6;

  const client = getClient();
  const connection = getConnection();
  const invoker = publicKeySender(userWallet);

  const topupData: ITopUpData = {
    id,
    amount: getBN(num(params.amount as string), dec),
  };

  const ixs = await client.prepareTopupInstructions(topupData, {
    invoker: invoker as any,
  });

  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    feePayer: new PublicKey(userWallet),
    recentBlockhash: blockhash,
  });
  tx.add(...ixs);

  return {
    preview: preview(
      "streamflow_topup",
      `Top up stream ${id.slice(0, 8)}… with ${params.amount} tokens`,
      params as Record<string, unknown>,
      [
        "Stream must have canTopup=true.",
        "Added tokens extend the stream at the same amountPerPeriod rate.",
      ],
      "~0.001 SOL",
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── BUILD: TRANSFER ──────────────────────────────────────────────────────────

export async function buildStreamflowTransfer(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const id           = (params.stream_id ?? params.streamId) as string;
  const newRecipient = (params.new_recipient ?? params.newRecipient) as string;

  const client = getClient();
  const connection = getConnection();
  const invoker = publicKeySender(userWallet);

  const transferData: ITransferData = { id, newRecipient };

  const ixs = await client.prepareTransferInstructions(transferData, {
    invoker: invoker as any,
  });

  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    feePayer: new PublicKey(userWallet),
    recentBlockhash: blockhash,
  });
  tx.add(...ixs);

  return {
    preview: preview(
      "streamflow_transfer",
      `Transfer stream ${id.slice(0, 8)}… → ${newRecipient.slice(0, 8)}…`,
      params as Record<string, unknown>,
      ["New recipient will receive all future unlocked tokens."],
      "~0.001 SOL",
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── BUILD: UPDATE ────────────────────────────────────────────────────────────

export async function buildStreamflowUpdate(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const id = (params.stream_id ?? params.streamId) as string;

  const client = getClient();
  const connection = getConnection();
  const invoker = publicKeySender(userWallet);

  const updateData: IUpdateData = {
    id,
    enableAutomaticWithdrawal: params.automatic_withdrawal !== undefined
      ? Boolean(params.automatic_withdrawal)
      : undefined,
    withdrawFrequency: params.withdrawal_frequency !== undefined
      ? new BN(num(params.withdrawal_frequency as number))
      : undefined,
    amountPerPeriod: params.amount_per_period !== undefined
      ? getBN(num(params.amount_per_period as string), 6)
      : undefined,
  };

  const ixs = await client.prepareUpdateInstructions(updateData, {
    invoker: invoker as any,
  });

  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({
    feePayer: new PublicKey(userWallet),
    recentBlockhash: blockhash,
  });
  tx.add(...ixs);

  return {
    preview: preview(
      "streamflow_update",
      `Update stream ${id.slice(0, 8)}… parameters`,
      params as Record<string, unknown>,
      ["Only certain parameters can be updated after stream creation."],
      "~0.001 SOL",
    ),
    transaction: serialize(tx),
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}

// ─── READ: LIST STREAMS ───────────────────────────────────────────────────────

export async function getStreamflowStreams(
  params: Record<string, unknown>,
  userWallet: string,
): Promise<BuildResponse> {
  const direction = (params.direction as string) ?? "all";
  const dir = direction === "incoming" ? StreamDirection.Incoming
    : direction === "outgoing" ? StreamDirection.Outgoing
    : StreamDirection.All;

  const client = getClient();
  const streams = await client.get({
    address: userWallet,
    type: StreamType.All,
    direction: dir,
  });

  const data = streams.map(([id, s]) => ({
    id,
    name:             s.name,
    recipient:        s.recipient,
    sender:           s.sender,
    mint:             s.mint,
    depositedAmount:  s.depositedAmount.toString(),
    withdrawnAmount:  s.withdrawnAmount.toString(),
    start:            s.start,
    end:              s.end,
    period:           s.period,
    cliff:            s.cliff,
    cliffAmount:      s.cliffAmount.toString(),
    amountPerPeriod:  s.amountPerPeriod.toString(),
    cancelableBySender:    s.cancelableBySender,
    cancelableByRecipient: s.cancelableByRecipient,
    automaticWithdrawal:   s.automaticWithdrawal,
    canTopup:         s.canTopup,
    lastWithdrawnAt:  s.lastWithdrawnAt,
    createdAt:        s.createdAt,
    closed:           s.closed,
  }));

  return {
    preview: {
      id: uuid(),
      type: "streamflow_list",
      description: `Streamflow streams for ${userWallet.slice(0, 8)}…`,
      estimatedFee: "0",
      params: { direction },
      warnings: [],
      requiresApproval: false,
    },
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: { streams: data, total: data.length },
  };
}

// ─── READ: GET ONE ────────────────────────────────────────────────────────────

export async function getStreamflowOne(
  params: Record<string, unknown>,
  _userWallet: string,
): Promise<BuildResponse> {
  const id = (params.stream_id ?? params.streamId) as string;
  const client = getClient();
  const s = await client.getOne({ id });

  return {
    preview: {
      id: uuid(),
      type: "streamflow_get_one",
      description: `Stream ${id.slice(0, 8)}… details`,
      estimatedFee: "0",
      params: { id },
      warnings: [],
      requiresApproval: false,
    },
    additionalSignersRequired: 0,
    isCrossChain: false,
    data: {
      id,
      name:             s.name,
      recipient:        s.recipient,
      sender:           s.sender,
      mint:             s.mint,
      depositedAmount:  s.depositedAmount.toString(),
      withdrawnAmount:  s.withdrawnAmount.toString(),
      start:            s.start,
      end:              s.end,
      period:           s.period,
      cliff:            s.cliff,
      cliffAmount:      s.cliffAmount.toString(),
      amountPerPeriod:  s.amountPerPeriod.toString(),
      cancelableBySender:    s.cancelableBySender,
      cancelableByRecipient: s.cancelableByRecipient,
      automaticWithdrawal:   s.automaticWithdrawal,
      canTopup:         s.canTopup,
      lastWithdrawnAt:  s.lastWithdrawnAt,
      createdAt:        s.createdAt,
      closed:           s.closed,
    },
  };
}
