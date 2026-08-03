import { Connection, Commitment } from '@solana/web3.js';
import { environment } from '@env/environment';

/**
 * Create a Solana Connection that routes through the gateway's /api/rpc proxy.
 * Includes X-Requested-With and credentials so the gateway's CSRF middleware passes.
 */
export function createSolanaConnection(commitment: Commitment = 'confirmed'): Connection {
  return new Connection(environment.solanaRpc, {
    commitment,
    httpHeaders: {
      'X-Requested-With': 'XMLHttpRequest',
    },
    // The /api/rpc proxy is gated by the gateway's RequireWallet middleware,
    // which reads the HttpOnly auth cookie. web3.js' bare fetch would omit it,
    // so force credentials: 'include' on every RPC request. Guard against an
    // undefined init (web3.js can call the middleware without one).
    fetchMiddleware: (url, options, fetch) => {
      const init = (options ?? {}) as RequestInit;
      init.credentials = 'include';
      return fetch(url, init);
    },
  });
}

/**
 * Wait for a signature to settle, by asking rather than subscribing.
 *
 * `connection.confirmTransaction()` waits on a websocket notification, and
 * web3.js derives that websocket's address from the HTTP one — here that means
 * `wss://app.oprai.xyz/api/rpc`, which the gateway answers with 405. The
 * subscription therefore never connects, web3.js retries it forever, and the
 * caller waits out the whole block-height window before falling back. It is
 * why a landed transaction could be written off as failed, and why the tab
 * looks permanently busy.
 *
 * Polling has none of that. It is also the only mechanism that works through a
 * proxy that speaks HTTP and nothing else, which ours does by design — the
 * Helius key stays on the server.
 *
 * Resolves with the on-chain error (null when the transaction succeeded), or
 * `undefined` if the chain never answered within `timeoutMs`.
 */
export async function awaitSignature(
  connection: Connection,
  signature: string,
  timeoutMs = 90_000,
  pollMs = 1_500,
): Promise<unknown | undefined> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const { value } = await connection.getSignatureStatus(signature, {
        searchTransactionHistory: true,
      });
      if (value && (value.confirmationStatus === 'confirmed' || value.confirmationStatus === 'finalized')) {
        return value.err ?? null;
      }
    } catch {
      // A failed poll is not a verdict; keep asking until the deadline.
    }
    await new Promise(r => setTimeout(r, pollMs));
  }
  return undefined;
}
