/**
 * Decode SPL-Token / Token-2022 transfer destinations from a (legacy or
 * versioned) transaction, for the SPL arm of the WYSIWYS recipient check in
 * SolanaActionService. Kept as a pure function so it can be unit-tested against
 * mock transactions without instantiating the whole service.
 *
 * Instruction layouts (first data byte is the discriminator):
 *   Transfer(3):        accounts [source, dest, authority]        data [3, amount:u64]
 *   TransferChecked(12): accounts [source, mint, dest, authority] data [12, amount:u64, decimals]
 */

export const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
export const TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';

export interface SplTransferDest {
  /** The token account receiving the transfer (an ATA, or any token account). */
  dest: string;
  /** The mint, when the instruction carries it (TransferChecked); else null. */
  mint: string | null;
  /** Transferred amount in base units. */
  amount: bigint;
}

export function splTransferDestinations(tx: any, isVersioned: boolean): SplTransferDest[] {
  const out: SplTransferDest[] = [];
  const decode = (data: Uint8Array, acc: (i: number) => string | undefined) => {
    if (data.length < 1) return;
    const dv = new DataView(data.buffer, data.byteOffset, data.length);
    const amount = data.length >= 9 ? dv.getBigUint64(1, true) : 0n;
    if (data[0] === 3) {
      const dest = acc(1); // Transfer: [source, dest, authority]
      if (dest) out.push({ dest, mint: null, amount });
    } else if (data[0] === 12) {
      const dest = acc(2); // TransferChecked: [source, mint, dest, authority]
      if (dest) out.push({ dest, mint: acc(1) ?? null, amount });
    }
  };
  if (isVersioned) {
    const msg = tx?.message;
    const keys: string[] = (msg?.staticAccountKeys ?? []).map((k: any) => k.toBase58());
    for (const ci of msg?.compiledInstructions ?? []) {
      const pid = keys[ci.programIdIndex];
      if (pid !== TOKEN_PROGRAM && pid !== TOKEN_2022_PROGRAM) continue;
      decode(ci.data as Uint8Array, (i) => {
        const idx = ci.accountKeyIndexes?.[i];
        return idx != null ? keys[idx] : undefined;
      });
    }
  } else {
    for (const ix of tx?.instructions ?? []) {
      const pid = ix.programId?.toBase58?.();
      if (pid !== TOKEN_PROGRAM && pid !== TOKEN_2022_PROGRAM) continue;
      decode(new Uint8Array(ix.data), (i) => ix.keys?.[i]?.pubkey?.toBase58?.());
    }
  }
  return out;
}

/** The single largest transfer (the recipient leg; smaller legs are fees). */
export function mainSplTransfer(dests: SplTransferDest[]): SplTransferDest | null {
  if (dests.length === 0) return null;
  return dests.reduce((a, b) => (b.amount > a.amount ? b : a));
}
