/**
 * WYSIWYS ("What You See Is What You Sign") pre-signature safety scan.
 *
 * Every action transaction OPRAI signs is BUILT BY THE BACKEND and handed to
 * the wallet as opaque bytes — the frontend historically signed them without
 * ever looking inside. That trusts the builder completely: a compromised or
 * buggy `/actions/build`, or a malicious protocol SDK, could return a
 * transaction that drains the user while the card still shows a benign intent,
 * and the user would sign it blindly.
 *
 * This is the universal floor, applied to EVERY Solana signature (all wallet
 * sign sites route through WalletService). It enforces two invariants that hold
 * for every legitimate OPRAI transaction and are exactly what an attacker must
 * break:
 *
 *   1. The FEE PAYER is the connected wallet. Every real OPRAI action is paid
 *      and signed by the user; a transaction anchored to someone else's payer
 *      is not something we ever build.
 *   2. No SPL-Token instruction HANDS THE USER'S TOKEN AUTHORITY to another
 *      party — `Approve` / `ApproveChecked` (delegate spend authority) or
 *      `SetAuthority` (transfer account ownership). OPRAI's flows move tokens
 *      via owner-signed transfers inside the same transaction; they never need
 *      the user to approve a delegate or reassign authority, so the mere
 *      presence of one of these is a red flag.
 *
 * It also rejects a program id loaded from a lookup table (a static-keys scan
 * can't see behind one, and OPRAI never puts a program id there) and a
 * `CloseAccount` whose destination isn't the connected wallet (legit wSOL
 * unwrap returns to the owner; anything else is a drain).
 *
 * Deliberately NOT checked here (handled elsewhere or too false-positive-prone
 * for a hard block): per-action amount ceilings and generic transfer recipients
 * (need the displayed intent — see `verifyTransferWysiwys` in
 * solana-action.service — and the wallet's own simulation preview).
 *
 * Fail-open on DECODE failure (unknown shape, malformed bytes): a decode bug
 * must never brick signing. But a positive UNSAFE finding is a hard throw.
 */

const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';

/**
 * SPL-Token instruction discriminators (first data byte) that transfer control
 * of the user's tokens to a third party. Both the Token and Token-2022 programs
 * share this layout for these instructions.
 */
const FORBIDDEN_TOKEN_IX: Record<number, string> = {
  4: 'approval', // Approve
  6: 'authority-change', // SetAuthority
  13: 'approval', // ApproveChecked
};

/** SPL-Token CloseAccount discriminator. Legit for unwrapping wSOL back to the
 * owner, but its destination is attacker-controllable, so we allow it ONLY when
 * the rent/lamports go back to the connected wallet. */
const CLOSE_ACCOUNT_IX = 9;

/** Thrown when a transaction fails a WYSIWYS invariant — surfaced to the user. */
export class WysiwysError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WysiwysError';
  }
}

interface DecodedIx {
  /** Resolved program id, or '' when it is loaded from a lookup table (which a
   * legitimate OPRAI transaction never does — program ids live in static keys). */
  programId: string;
  programIdFromLut: boolean;
  discriminator: number | null;
  /** For a CloseAccount instruction, the destination that receives the rent /
   * wrapped lamports (accounts[1]); null if not a close or unresolvable. */
  closeDest: string | null;
  closeDestFromLut: boolean;
}

interface DecodedTx {
  feePayer: string | null;
  instructions: DecodedIx[];
}

function firstDataByte(data: unknown): number | null {
  // Buffer is a Uint8Array subclass, so this covers both legacy (Buffer) and
  // versioned (Uint8Array) instruction data.
  if (data instanceof Uint8Array) return data.length ? data[0] : null;
  if (Array.isArray(data)) return data.length ? Number(data[0]) : null;
  return null;
}

/**
 * Decode just enough of a legacy `Transaction` or `VersionedTransaction` to run
 * the safety scan, WITHOUT resolving address-lookup tables — every field we
 * read (fee payer = first account key, program ids, instruction discriminators)
 * lives in the static message, so this works for versioned+ALT transactions
 * too. Returns null if the shape is unrecognised (→ caller fails open).
 */
function decode(tx: any): DecodedTx | null {
  // VersionedTransaction: has `.message.staticAccountKeys`
  if (tx?.message && Array.isArray(tx.message.staticAccountKeys)) {
    const keys = tx.message.staticAccountKeys as Array<{ toBase58(): string }>;
    const n = keys.length; // indices >= n are loaded from a lookup table
    const feePayer = keys[0]?.toBase58() ?? null;
    const compiled = (tx.message.compiledInstructions ?? []) as Array<{
      programIdIndex: number;
      accountKeyIndexes?: number[];
      data: unknown;
    }>;
    const instructions: DecodedIx[] = compiled.map((ci) => {
      const pidFromLut = ci.programIdIndex >= n;
      const destIdx = ci.accountKeyIndexes?.[1];
      const destFromLut = typeof destIdx === 'number' && destIdx >= n;
      return {
        programId: pidFromLut ? '' : (keys[ci.programIdIndex]?.toBase58() ?? ''),
        programIdFromLut: pidFromLut,
        discriminator: firstDataByte(ci.data),
        closeDest:
          typeof destIdx === 'number' && !destFromLut ? (keys[destIdx]?.toBase58() ?? null) : null,
        closeDestFromLut: destFromLut,
      };
    });
    return { feePayer, instructions };
  }
  // Legacy Transaction: has `.instructions` array (no lookup tables)
  if (Array.isArray(tx?.instructions)) {
    const feePayer =
      tx.feePayer?.toBase58?.() ?? tx.signatures?.[0]?.publicKey?.toBase58?.() ?? null;
    const instructions: DecodedIx[] = (
      tx.instructions as Array<{
        programId?: { toBase58(): string };
        keys?: Array<{ pubkey?: { toBase58(): string } }>;
        data?: unknown;
      }>
    ).map((ix) => ({
      programId: ix.programId?.toBase58?.() ?? '',
      programIdFromLut: false,
      discriminator: firstDataByte(ix.data),
      closeDest: ix.keys?.[1]?.pubkey?.toBase58?.() ?? null,
      closeDestFromLut: false,
    }));
    return { feePayer, instructions };
  }
  return null;
}

/**
 * Assert a transaction is safe to sign for `ownerBase58` (the connected
 * wallet). Throws {@link WysiwysError} on a positive unsafe finding; returns
 * silently when safe OR when the transaction can't be decoded (fail-open).
 */
export function assertTxSignSafety(tx: unknown, ownerBase58: string | null): void {
  if (!ownerBase58) return; // no connected wallet to compare against

  let decoded: DecodedTx | null;
  try {
    decoded = decode(tx);
  } catch (e) {
    console.warn('[wysiwys] transaction decode threw; skipping safety scan', e);
    return;
  }
  if (!decoded) {
    console.warn('[wysiwys] unrecognised transaction shape; skipping safety scan');
    return;
  }

  // 1. Fee payer must be the connected wallet.
  if (decoded.feePayer && decoded.feePayer !== ownerBase58) {
    console.error('[wysiwys] fee-payer mismatch', { feePayer: decoded.feePayer, owner: ownerBase58 });
    throw new WysiwysError(
      'This transaction would be paid by a different wallet than the one connected, so it was not signed. Please try again.',
    );
  }

  for (const ix of decoded.instructions) {
    // 2. A program invoked through a lookup table can hide a token Approve /
    // SetAuthority / CloseAccount from a static-keys scan. Legitimate OPRAI
    // transactions always carry their program ids in the static keys, so a
    // lookup-table program id is itself the red flag.
    if (ix.programIdFromLut) {
      console.error('[wysiwys] program id loaded from a lookup table');
      throw new WysiwysError(
        'This transaction hides a program behind a lookup table, which OPRAI never does, so it was not signed. If you expected this action, please report it.',
      );
    }

    if (ix.programId !== TOKEN_PROGRAM && ix.programId !== TOKEN_2022_PROGRAM) continue;
    if (ix.discriminator == null) continue;

    // 3. CloseAccount is legitimate for unwrapping wSOL — but ONLY when the
    // rent/lamports return to the connected wallet. A close whose destination is
    // someone else (or hidden in a lookup table) is a drain.
    if (ix.discriminator === CLOSE_ACCOUNT_IX) {
      if (ix.closeDestFromLut || (ix.closeDest && ix.closeDest !== ownerBase58)) {
        console.error('[wysiwys] close-account to non-owner destination', {
          dest: ix.closeDest,
          fromLut: ix.closeDestFromLut,
          owner: ownerBase58,
        });
        throw new WysiwysError(
          'This transaction would close a token account and send its balance to a different wallet, so it was not signed. If you expected this action, please report it.',
        );
      }
      continue;
    }

    const kind = FORBIDDEN_TOKEN_IX[ix.discriminator];
    if (kind) {
      console.error('[wysiwys] forbidden token instruction', { discriminator: ix.discriminator, kind });
      throw new WysiwysError(
        `This transaction asked for an unexpected token ${kind} and was not signed. OPRAI never needs to take approval or ownership of your tokens — if you expected this action, please report it.`,
      );
    }
  }
}
