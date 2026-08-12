import { Connection, Commitment } from '@solana/web3.js';
import { environment } from '@env/environment';

/**
 * Supplies the in-memory JWT for outbound RPC requests. AuthService registers
 * this at construction; it stays null until then and whenever the user is
 * signed out. Kept as a module-level callback rather than a service injection
 * because `createSolanaConnection` is a plain factory called from dozens of
 * places, none of which should have to thread the token through.
 */
let rpcAuthTokenProvider: (() => string | null) | null = null;
export function setRpcAuthTokenProvider(provider: () => string | null): void {
  rpcAuthTokenProvider = provider;
}

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
    // The /api/rpc proxy admits first-party callers only. Two signals carry that:
    //   - the HttpOnly auth cookie (credentials: 'include' below); web3.js' bare
    //     fetch would omit it, and the browser doesn't always attach it reliably
    //     to these Connection fetches — which is exactly why the proxy can't be
    //     cookie-gated outright.
    //   - the JWT as a Bearer header, which we attach here from memory. Unlike
    //     the Origin header, a Bearer can't be forged by a third party, so this
    //     is what lifts a signed-in user above the Origin check and closes the
    //     Origin-spoofing gap. When there's no token yet (pre-sign-in, or the
    //     cookie-only state after a page refresh) we send neither and the proxy
    //     falls back to the Origin/Referer check — so nothing breaks, this only
    //     ever adds trust.
    // Guard against an undefined init (web3.js can call the middleware without one).
    fetchMiddleware: (url, options, fetch) => {
      const init = (options ?? {}) as RequestInit;
      init.credentials = 'include';
      const token = rpcAuthTokenProvider?.();
      if (token) {
        const headers = new Headers(init.headers as HeadersInit | undefined);
        headers.set('Authorization', `Bearer ${token}`);
        init.headers = headers;
      }
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
