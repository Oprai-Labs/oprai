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
