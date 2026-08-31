import { splTransferDestinations, mainSplTransfer, TOKEN_PROGRAM, TOKEN_2022_PROGRAM } from './spl-transfer';

const SOURCE = 'Sourc3TokenAccount1111111111111111111111111';
const MINT = 'Mint1111111111111111111111111111111111111111';
const DEST = 'Dest1RecipientAta111111111111111111111111111';
const DEST2 = 'Dest2RecipientAta222222222222222222222222222';
const SYS = '11111111111111111111111111111111';

const pk = (s: string) => ({ toBase58: () => s });

/** data = [disc, amount:u64 LE, (decimals for TransferChecked)]. */
const ixData = (disc: number, amount: bigint, extra: number[] = []) => {
  const d = new Uint8Array(9 + extra.length);
  d[0] = disc;
  new DataView(d.buffer).setBigUint64(1, amount, true);
  extra.forEach((b, i) => (d[9 + i] = b));
  return d;
};

/** versioned tx: static keys carry program ids + accounts; compiled ix reference them by index. */
const versioned = (keys: string[], ixs: Array<{ pidIdx: number; accIdx: number[]; data: Uint8Array }>) => ({
  message: {
    staticAccountKeys: keys.map(pk),
    compiledInstructions: ixs.map((i) => ({ programIdIndex: i.pidIdx, accountKeyIndexes: i.accIdx, data: i.data })),
  },
});

const legacy = (ixs: Array<{ pid: string; keys: string[]; data: Uint8Array }>) => ({
  instructions: ixs.map((i) => ({ programId: pk(i.pid), keys: i.keys.map((k) => ({ pubkey: pk(k) })), data: i.data })),
});

describe('splTransferDestinations', () => {
  it('decodes a versioned Transfer (dest = accounts[1])', () => {
    // keys: [0]=TOKEN, [1]=source, [2]=dest, [3]=authority
    const tx = versioned([TOKEN_PROGRAM, SOURCE, DEST, 'Auth11111111111111111111111111111111111111'],
      [{ pidIdx: 0, accIdx: [1, 2, 3], data: ixData(3, 1000n) }]);
    const d = splTransferDestinations(tx, true);
    expect(d.length).toBe(1);
    expect(d[0].dest).toBe(DEST);
    expect(d[0].mint).toBeNull();
    expect(d[0].amount).toBe(1000n);
  });

  it('decodes a versioned TransferChecked (dest = accounts[2], mint = accounts[1])', () => {
    // Transfer-checked accounts: [source, mint, dest, authority]
    const tx = versioned([TOKEN_PROGRAM, SOURCE, MINT, DEST, 'Auth11111111111111111111111111111111111111'],
      [{ pidIdx: 0, accIdx: [1, 2, 3, 4], data: ixData(12, 500n, [6]) }]);
    const d = splTransferDestinations(tx, true);
    expect(d.length).toBe(1);
    expect(d[0].dest).toBe(DEST);
    expect(d[0].mint).toBe(MINT);
    expect(d[0].amount).toBe(500n);
  });

  it('recognizes the Token-2022 program', () => {
    const tx = versioned([TOKEN_2022_PROGRAM, SOURCE, DEST, 'Auth11111111111111111111111111111111111111'],
      [{ pidIdx: 0, accIdx: [1, 2, 3], data: ixData(3, 1n) }]);
    expect(splTransferDestinations(tx, true).length).toBe(1);
  });

  it('ignores non-token-program instructions', () => {
    const tx = versioned([SYS, SOURCE, DEST], [{ pidIdx: 0, accIdx: [1, 2], data: new Uint8Array([2, 0, 0, 0]) }]);
    expect(splTransferDestinations(tx, true).length).toBe(0);
  });

  it('decodes a legacy Transfer', () => {
    const tx = legacy([{ pid: TOKEN_PROGRAM, keys: [SOURCE, DEST, 'Auth11111111111111111111111111111111111111'], data: ixData(3, 42n) }]);
    const d = splTransferDestinations(tx, false);
    expect(d[0].dest).toBe(DEST);
    expect(d[0].amount).toBe(42n);
  });
});

describe('mainSplTransfer', () => {
  it('returns null for no transfers', () => {
    expect(mainSplTransfer([])).toBeNull();
  });

  it('picks the largest-amount transfer (the recipient leg, not the fee leg)', () => {
    const dests = [
      { dest: DEST2, mint: null, amount: 5n }, // small fee leg
      { dest: DEST, mint: null, amount: 1000n }, // recipient
    ];
    expect(mainSplTransfer(dests)!.dest).toBe(DEST);
  });
});
