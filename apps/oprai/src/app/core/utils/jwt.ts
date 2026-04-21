/**
 * Safe JWT payload decoder.
 *
 * Validates that the token has three dot-separated parts before attempting
 * base64 decode, avoiding the `atob(undefined)` TypeError that occurs when
 * an invalid or truncated token is passed. Returns `null` on any failure
 * instead of throwing.
 *
 * NOTE: This only decodes the payload — it does NOT verify the HMAC signature.
 * Signature verification happens server-side on every API call. This utility
 * is used solely for reading claims (exp, wallet) without a network round-trip.
 */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    return JSON.parse(atob(parts[1])) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** Returns true if the token is non-null, structurally valid, and not expired. */
export function isTokenExpired(token: string): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload) return true;
  const exp = payload['exp'];
  if (typeof exp !== 'number') return true;
  return Date.now() >= exp * 1000;
}

/** Extracts the wallet claim ("w" or "wallet") from the JWT payload. */
export function getWalletFromToken(token: string): string | null {
  const payload = decodeJwtPayload(token);
  if (!payload) return null;
  const wallet = payload['w'] ?? payload['wallet'];
  return typeof wallet === 'string' ? wallet : null;
}
