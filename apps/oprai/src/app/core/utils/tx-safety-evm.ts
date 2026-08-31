/**
 * WYSIWYS for EVM EIP-712 blind-signing.
 *
 * On EVM, OPRAI asks the wallet to sign structured data (`eth_signTypedData_v4`)
 * that the BACKEND builds — a Uniswap Permit2 permit, or an OpenSea Seaport
 * order. Unlike a transaction, a typed-data signature shows the wallet a struct,
 * not a transfer, and users approve it blindly. The signature is only ever valid
 * against the contract named in `domain.verifyingContract`: change that address
 * and the very same "permit"-shaped bytes become a grant against a contract the
 * attacker controls. A compromised or buggy builder that swaps the verifying
 * contract turns a benign-looking permit into an asset drain, and the card still
 * reads "swap".
 *
 * This enforces the one invariant that holds for every legitimate OPRAI EIP-712
 * payload and is exactly what such an attack must break: a Permit2 permit is
 * signed ONLY against Uniswap's canonical Permit2, and a Seaport order ONLY
 * against canonical Seaport. Both are protocol constants deployed at the same
 * deterministic address on every chain (verified on-chain to exist on Robinhood
 * Chain 4663, the primary EVM chain), so requiring them is false-positive-free.
 *
 * Fail-OPEN on any payload we don't model (unknown primaryType / missing domain /
 * unparseable): a gap here must never brick a legitimate signature. A positive
 * mismatch (a permit/order pointed at the wrong contract) is a hard throw.
 *
 * Deliberately NOT checked (needs the displayed intent, too false-positive-prone
 * for a hard block): the Permit2 `spender`, the permitted `amount`/expiration,
 * and a Seaport order's offer/consideration. A genuine-Permit2 permit with a
 * hostile spender is not caught here — the wallet's own preview and the
 * per-action flow remain the backstop for that.
 */

import { WysiwysError } from './tx-safety';

/** Uniswap Permit2 — one canonical CREATE2 address on every chain. */
const PERMIT2 = '0x000000000022d473030f116ddee9f6b43ac78ba3';

/** Canonical Seaport deployments OPRAI orders are signed against. */
const SEAPORT = new Set<string>([
  '0x0000000000000068f116a894984e2db1123eb395', // Seaport 1.6 (current)
  '0x00000000000000adc04c56bf30ac9d3c0aaf14dc', // Seaport 1.5 (older orders)
]);

/** EIP-712 primaryTypes that are Permit2 permits. */
const PERMIT2_TYPES = new Set(['PermitSingle', 'PermitBatch', 'PermitTransferFrom', 'PermitBatchTransferFrom']);

/** EIP-712 primaryTypes that are Seaport orders. */
const SEAPORT_TYPES = new Set(['OrderComponents', 'BulkOrder']);

/**
 * Assert an EIP-712 typed-data payload is safe to sign. Accepts the object or
 * its JSON string (whichever the sign site has on hand). Throws
 * {@link WysiwysError} when a recognised permit/order is pointed at a non-canonical
 * verifying contract; returns silently when safe OR when the payload isn't one we
 * model (fail-open).
 */
export function assertEip712SignSafety(typedData: unknown, ownerAddress: string | null): void {
  if (!ownerAddress) return; // no connected wallet to sign as

  let td: any = typedData;
  if (typeof td === 'string') {
    try {
      td = JSON.parse(td);
    } catch {
      return; // unparseable → fail open
    }
  }

  const primaryType = td?.primaryType;
  const verifyingContract = td?.domain?.verifyingContract;
  if (typeof primaryType !== 'string' || typeof verifyingContract !== 'string') return; // unknown shape → fail open
  const vc = verifyingContract.toLowerCase();

  if (PERMIT2_TYPES.has(primaryType)) {
    if (vc !== PERMIT2) {
      console.error('[wysiwys-evm] permit verifying contract is not canonical Permit2', { verifyingContract });
      throw new WysiwysError(
        'This permit would be valid for a contract that is not Uniswap Permit2, so it was not signed. If you expected this action, please report it.',
      );
    }
    return;
  }

  const domainName = String(td?.domain?.name ?? '').toLowerCase();
  if (SEAPORT_TYPES.has(primaryType) || domainName === 'seaport') {
    if (!SEAPORT.has(vc)) {
      console.error('[wysiwys-evm] order verifying contract is not canonical Seaport', { verifyingContract });
      throw new WysiwysError(
        'This order would be valid for a contract that is not OpenSea Seaport, so it was not signed. If you expected this action, please report it.',
      );
    }
    return;
  }

  // Any other EIP-712 payload is not modelled here → fail open.
}
