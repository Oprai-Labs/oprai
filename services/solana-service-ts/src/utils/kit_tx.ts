/**
 * @solana/kit (web3.js v2) transaction assembly helpers.
 *
 * The Kamino SDKs (klend-sdk farm/leverage, kliquidity-sdk) build instructions
 * with @solana/kit, but the rest of this service is web3.js v1 and we hold no
 * signing key. These helpers compile kit `Instruction[]` into an UNSIGNED base64
 * v0 transaction that the web3.js-v1 frontend deserializes and signs. The fee
 * payer is a noop signer carrying only the user's address.
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
  compressTransactionMessageUsingAddressLookupTables,
  compileTransaction,
  getBase64EncodedWireTransaction,
  type Instruction,
} from "@solana/kit";
import { config } from "../config";

export function kitRpc() {
  return createSolanaRpc(config.solanaRpc);
}

/**
 * Compile kit instructions into an unsigned base64 v0 transaction.
 *
 * `lookupTables` (optional) is a map of ALT address → array of addresses it
 * contains; leverage txs exceed the legacy size limit and must be compressed
 * with Address Lookup Tables. Pass `undefined`/empty for small txs (farm stake).
 */
export async function buildUnsignedV0Tx(
  instructions: Instruction[],
  userWallet: string,
  lookupTables?: Record<string, string[]>,
): Promise<string> {
  if (instructions.length === 0) {
    throw new Error("No instructions to build a transaction from");
  }
  const feePayer = createNoopSigner(address(userWallet));
  const { value: latestBlockhash } = await kitRpc().getLatestBlockhash().send();

  let message = pipe(
    createTransactionMessage({ version: 0 }),
    (m) => setTransactionMessageFeePayerSigner(feePayer, m),
    (m) => setTransactionMessageLifetimeUsingBlockhash(latestBlockhash, m),
    (m) => appendTransactionMessageInstructions(instructions, m),
  );

  if (lookupTables && Object.keys(lookupTables).length > 0) {
    // Shrink the account list using the provided ALTs so the tx fits the size
    // limit. The kit helper takes a { [altAddress]: Address[] } map.
    const alts = Object.fromEntries(
      Object.entries(lookupTables).map(([k, v]) => [address(k), v.map((a) => address(a))]),
    );
    message = compressTransactionMessageUsingAddressLookupTables(message, alts) as typeof message;
  }

  return getBase64EncodedWireTransaction(compileTransaction(message));
}
