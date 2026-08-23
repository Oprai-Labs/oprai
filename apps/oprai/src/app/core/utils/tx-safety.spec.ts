import { assertTxSignSafety, WysiwysError } from './tx-safety';

const OWNER = 'OwnerWa11etPubKey1111111111111111111111111';
const OTHER = 'AttackerPubKey2222222222222222222222222222';
const TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const SYS = '11111111111111111111111111111111';

const pk = (s: string) => ({ toBase58: () => s });

/** Mimic a VersionedTransaction: static keys [feePayer, System, Token]. */
const versioned = (feePayer: string, ixs: Array<{ pidIdx: number; data: number[] }>) => ({
  message: {
    staticAccountKeys: [pk(feePayer), pk(SYS), pk(TOKEN)],
    compiledInstructions: ixs.map((i) => ({ programIdIndex: i.pidIdx, data: new Uint8Array(i.data) })),
  },
});

/** Mimic a legacy Transaction. */
const legacy = (feePayer: string, ixs: Array<{ pid: string; data: number[] }>) => ({
  feePayer: pk(feePayer),
  instructions: ixs.map((i) => ({ programId: pk(i.pid), data: new Uint8Array(i.data) })),
});

describe('assertTxSignSafety (WYSIWYS)', () => {
  it('allows a versioned tx paid by the owner with a plain system transfer', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 1, data: [2, 0, 0, 0] }]), OWNER)).not.toThrow();
  });

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

  it('allows CloseAccount (wSOL unwrap uses it legitimately)', () => {
    expect(() => assertTxSignSafety(versioned(OWNER, [{ pidIdx: 2, data: [9] }]), OWNER)).not.toThrow();
  });

  it('allows a normal SPL token Transfer', () => {
    expect(() => assertTxSignSafety(legacy(OWNER, [{ pid: TOKEN, data: [3, 0] }]), OWNER)).not.toThrow();
  });

  it('fails open on an unrecognised transaction shape (never bricks signing)', () => {
    expect(() => assertTxSignSafety({ foo: 1 }, OWNER)).not.toThrow();
  });

  it('skips the scan when there is no connected wallet', () => {
    expect(() => assertTxSignSafety(versioned(OTHER, [{ pidIdx: 2, data: [6] }]), null)).not.toThrow();
  });
});
