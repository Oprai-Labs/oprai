/**
 * SOL and SPL token transfers.
 * Uses @solana/web3.js + @solana/spl-token directly (no protocol SDK needed).
 */

import {
  Connection,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
} from "@solana/web3.js";
import {
  createTransferCheckedInstruction,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountIdempotentInstruction,
  getMint,
} from "@solana/spl-token";
import { config } from "../config";
import { appError, BuildResponse, ActionPreview } from "../types/index";
import { resolveToken, fetchTokenInfo, SOL_MINT } from "./tokens";
import { v4 as uuidv4 } from "uuid";

export interface TransferParams {
  to: string;
  amount: string;
  token: string;
  tokenDecimals?: number;
}

export async function buildTransfer(
  params: TransferParams,
  userWallet: string
): Promise<BuildResponse> {
  // Validate recipient
  let recipient: PublicKey;
  try {
    recipient = new PublicKey(params.to);
  } catch {
    throw appError("Invalid recipient address");
  }

  const amount = parseFloat(params.amount);
  if (isNaN(amount) || amount <= 0)
    throw appError("Amount must be a positive number");

  // Resolve token
  const tokenSymbol = params.token.toUpperCase();
  const tokenInfo = resolveToken(params.token) ?? (await fetchTokenInfo(params.token));
  if (!tokenInfo) throw appError(`Unknown token: ${params.token}`);

  const mint = tokenInfo.mint;
  const decimals = params.tokenDecimals ?? tokenInfo.decimals;
  const sender = new PublicKey(userWallet);

  const connection = new Connection(config.solanaRpc, "confirmed");
  const { blockhash } = await connection.getLatestBlockhash();
  const tx = new Transaction({ recentBlockhash: blockhash, feePayer: sender });

  const isSol = mint === SOL_MINT;

  if (isSol) {
    const lamports = Math.round(amount * 1e9);
    tx.add(
      SystemProgram.transfer({ fromPubkey: sender, toPubkey: recipient, lamports })
    );
  } else {
    const mintPubkey = new PublicKey(mint);
    const senderAta = await getAssociatedTokenAddress(mintPubkey, sender);
    const recipientAta = await getAssociatedTokenAddress(mintPubkey, recipient);

    // Create recipient ATA if needed (idempotent)
    tx.add(
      createAssociatedTokenAccountIdempotentInstruction(
        sender, recipientAta, recipient, mintPubkey
      )
    );

    const rawAmount = BigInt(Math.round(amount * 10 ** decimals));
    tx.add(
      createTransferCheckedInstruction(
        senderAta, mintPubkey, recipientAta, sender, rawAmount, decimals
      )
    );
  }

  const serialized = tx.serialize({ requireAllSignatures: false }).toString("base64");

  const preview: ActionPreview = {
    id: uuidv4(),
    type: "transfer",
    description: `Transfer ${amount} ${isSol ? "SOL" : tokenSymbol} to ${params.to.slice(0, 8)}...`,
    estimatedFee: "~0.000005 SOL",
    params: params as unknown as Record<string, unknown>,
    warnings: [],
    requiresApproval: false,
  };

  return {
    preview,
    transaction: serialized,
    additionalSignersRequired: 0,
    isCrossChain: false,
  };
}
