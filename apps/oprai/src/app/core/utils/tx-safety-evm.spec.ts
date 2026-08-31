import { assertEip712SignSafety } from './tx-safety-evm';
import { WysiwysError } from './tx-safety';

const OWNER = '0x1111111111111111111111111111111111111111';
const PERMIT2 = '0x000000000022D473030F116dDEE9F6B43aC78BA3'; // canonical (checksummed)
const SEAPORT = '0x0000000000000068F116a894984e2DB1123eB395'; // Seaport 1.6
const EVIL = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef';

const permit = (verifyingContract: string, primaryType = 'PermitSingle') => ({
  domain: { name: 'Permit2', chainId: 4663, verifyingContract },
  types: { PermitSingle: [] },
  primaryType,
  message: { spender: EVIL, sigDeadline: '123' },
});
const order = (verifyingContract: string, primaryType = 'OrderComponents', name = 'Seaport') => ({
  domain: { name, version: '1.6', chainId: 4663, verifyingContract },
  types: { OrderComponents: [] },
  primaryType,
  message: {},
});

describe('assertEip712SignSafety (EVM WYSIWYS)', () => {
  // ── legitimate payloads must never be blocked ──
  it('allows a Permit2 PermitSingle against canonical Permit2', () => {
    expect(() => assertEip712SignSafety(permit(PERMIT2), OWNER)).not.toThrow();
  });

  it('allows a Permit2 PermitBatch against canonical Permit2', () => {
    expect(() => assertEip712SignSafety(permit(PERMIT2, 'PermitBatch'), OWNER)).not.toThrow();
  });

  it('allows a Seaport order against canonical Seaport', () => {
    expect(() => assertEip712SignSafety(order(SEAPORT), OWNER)).not.toThrow();
  });

  it('allows a canonical payload passed as a JSON string', () => {
    expect(() => assertEip712SignSafety(JSON.stringify(permit(PERMIT2)), OWNER)).not.toThrow();
  });

  it('matches the verifying contract case-insensitively', () => {
    expect(() => assertEip712SignSafety(permit(PERMIT2.toLowerCase()), OWNER)).not.toThrow();
  });

  // ── malicious payloads must be caught ──
  it('rejects a Permit2 permit pointed at a non-canonical contract', () => {
    expect(() => assertEip712SignSafety(permit(EVIL), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects a Seaport order pointed at a non-canonical contract', () => {
    expect(() => assertEip712SignSafety(order(EVIL), OWNER)).toThrowError(WysiwysError);
  });

  it('rejects a Seaport-named order at a wrong contract even under an unknown primaryType', () => {
    expect(() => assertEip712SignSafety(order(EVIL, 'BulkOrder'), OWNER)).toThrowError(WysiwysError);
    expect(() => assertEip712SignSafety(order(EVIL, 'SomethingElse', 'Seaport'), OWNER)).toThrowError(WysiwysError);
  });

  // ── the scanner itself must never brick a legit signature ──
  it('fails open on an EIP-712 payload it does not model', () => {
    expect(() =>
      assertEip712SignSafety({ domain: { name: 'Lighter', verifyingContract: EVIL }, primaryType: 'Order', types: {}, message: {} }, OWNER),
    ).not.toThrow();
  });

  it('fails open when the domain / verifying contract is missing', () => {
    expect(() => assertEip712SignSafety({ primaryType: 'PermitSingle', types: {}, message: {} }, OWNER)).not.toThrow();
  });

  it('fails open on an unparseable string', () => {
    expect(() => assertEip712SignSafety('not json', OWNER)).not.toThrow();
  });

  it('skips the scan when there is no connected wallet', () => {
    expect(() => assertEip712SignSafety(permit(EVIL), null)).not.toThrow();
  });
});
