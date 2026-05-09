const _origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000';
export const environment = {
  production: false,
  apiBase: '/api',
  apiUrl: '/api',
  adminApiBase: '/admin-api',
  solanaNetwork: 'mainnet-beta' as const,
  solanaRpc: `${_origin}/api/rpc`,
  heliusRpcUrl: `${_origin}/api/rpc`,
};
