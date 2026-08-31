import { assertTxSignSafety, WysiwysError } from './tx-safety';

const OWNER = 'OwnerWa11etPubKey1111111111111111111111111';
const OTHER = 'AttackerPubKey2222222222222222222222222222';
const TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const SYS = '11111111111111111111111111111111';

const pk = (s: string) => ({ toBase58: () => s });

// VersionedTransaction static keys: [0]=feePayer(owner), [1]=System, [2]=Token,
// [3]=OTHER (a stand-in destination that is NOT the owner). Indexes >= 4 model an
// account/program loaded from an address lookup table (not present in static keys).
const V_KEYS = (feePayer: string) => [pk(feePayer), pk(SYS), pk(TOKEN), pk(OTHER)];

/** Mimic a VersionedTransaction. `accIdx` = compiled instruction accountKeyIndexes. */
const versioned = (
  feePayer: string,
  ixs: Array<{ pidIdx: number; data: number[]; accIdx?: number[] }>,
) => ({
  message: {
    staticAccountKeys: V_KEYS(feePayer),
    compiledInstructions: ixs.map((i) => ({
      programIdIndex: i.pidIdx,
      accountKeyIndexes: i.accIdx,
      data: new Uint8Array(i.data),
    })),
  },
});

/** Mimic a legacy Transaction. `keys` = instruction account metas. */
const legacy = (
  feePayer: string,
  ixs: Array<{ pid: string; data: number[]; keys?: string[] }>,
) => ({
  feePayer: pk(feePayer),
  instructions: ixs.map((i) => ({
    programId: pk(i.pid),
    keys: i.keys?.map((k) => ({ pubkey: pk(k) })),
    data: new Uint8Array(i.data),
  })),
});

describe('assertTxSignSafety (WYSIWYS)', () => {
  // ── legitimate transactions must never be blocked (no false positives) ──
  it('allows a versioned tx paid by the owner with a plain system transfer', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 1, data: [2, 0, 0, 0] }]), OWNER)).not.toThrow();
  });

  it('allows a normal SPL token Transfer', () => {
    expect(() => assertTxSignSafety(legacy(OWNER, [{ pid: TOKEN, data: [3, 0] }]), OWNER)).not.toThrow();
  });

  it('allows CloseAccount that unwraps wSOL back to the owner', () => {
    // accounts = [account, destination, owner]; destination index 0 == owner
    expect(() =>
      assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [9], accIdx: [2, 0, 0] }]), OWNER),
    ).not.toThrow();
    expect(() =>
      assertTxSignSafety(legacy(OWNER, [{ pid: TOKEN, data: [9], keys: [TOKEN, OWNER, OWNER] }]), OWNER),
    ).not.toThrow();
  });

  it('allows CloseAccount whose destination cannot be determined (fail-open on that check)', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [9] }]), OWNER)).not.toThrow();
  });

  // ── malicious transactions must be caught ──
  it('rejects a tx whose fee payer is not the connected wallet', () => {
    expect(() => assertTxSignSafety(versioned(OTHER, [{ pidIdx: 1, data: [2] }]), OWNER)).toThrowError(WysiwysError);
    expect(() => assertTxSignSafety(legacy(OTHER, [{ pid: SYS, data: [2] }]), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects an SPL Token SetAuthority instruction', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [6, 1] }]), OWNER)).toThrowError(WysiwysError);
    expect(() => assertTxSignSafety(legacy(OWNER, [{ pid: TOKEN, data: [6] }]), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects SPL Token Approve / ApproveChecked (delegate spend authority)', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [4, 1] }]), OWNER)).toThrowError(WysiwysError);
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [13] }]), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects a program id loaded from an address lookup table (masking)', () => {
    // programIdIndex 4 is beyond the 4 static keys -> the program (which could be
    // the Token program with a hidden Approve/SetAuthority) is LUT-supplied.
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 4, data: [4] }]), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects CloseAccount whose destination is not the connected wallet (drain)', () => {
    // destination index 3 == OTHER (attacker)
    expect(() =>
      assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [9], accIdx: [2, 3, 0] }]), OWNER),
    ).toThrowError(WysiwysError);
    expect(() =>
      assertTxSignSafety(legacy(OWNER, [{ pid: TOKEN, data: [9], keys: [TOKEN, OTHER, OWNER] }]), OWNER),
    ).toThrowError(WysiwysError);
  });

  it('rejects CloseAccount whose destination is hidden in a lookup table', () => {
    // destination index 4 is beyond the static keys -> hidden in a LUT
    expect(() =>
      assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [9], accIdx: [2, 4, 0] }]), OWNER),
    ).toThrowError(WysiwysError);
  });

  // ── safety of the scanner itself ──
  it('fails open on an unrecognised transaction shape (never bricks signing)', () => {
    expect(() => assertTxSignSafety({ foo: 1 }, OWNER)).not.toThrow();
  });

  it('skips the scan when there is no connected wallet', () => {
    expect(() => assertTxSignSafety(versioned(OTHER, [{ pidIdx: 2, data: [6] }]), null)).not.toThrow();
  });
});
