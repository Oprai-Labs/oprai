export const environment = {
  production: true,
  apiBase: '/api',
  apiUrl: '/api',
  adminApiBase: '/admin-api',
  solanaNetwork: 'mainnet-beta' as const,
  // Solana RPC routed through gateway — Helius API key stays server-side (HELIUS_API_KEY env var)
  solanaRpc: '/api/rpc',
  heliusRpcUrl: '/api/rpc',
  // API keys removed from client bundle — all external calls go through gateway endpoints
};
