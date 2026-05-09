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
    fetchMiddleware: (url, options, fetch) => {
      (options as RequestInit).credentials = 'include';
      return fetch(url, options);
    },
  });
}
