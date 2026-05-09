const _origin = typeof window !== 'undefined' ? window.location.origin : '';
export const environment = {
  production: true,
  apiBase: '/api',
  apiUrl: '/api',
  adminApiBase: '/admin-api',
  solanaNetwork: 'mainnet-beta' as const,
  // Solana RPC routed through gateway — Helius API key stays server-side (HELIUS_API_KEY env var)
  // Must be absolute so Solana web3.js Connection accepts it over HTTPS (Cloudflare, production domain).
  solanaRpc: `${_origin}/api/rpc`,
  heliusRpcUrl: `${_origin}/api/rpc`,
  // API keys removed from client bundle — all external calls go through gateway endpoints
};
