/**
 * AUTO-GENERATED FROM shared/tokens.json — DO NOT EDIT BY HAND.
 * Run `node scripts/sync-tokens.mjs` after editing the JSON.
 * CI enforces that the JSON and these generated files stay in sync.
 */

export interface VerifiedToken {
  readonly address: string;
  readonly symbol: string;
  readonly name: string;
  readonly decimals: number;
  readonly logoURI: string | null;
  readonly aliases?: readonly string[];
  readonly tags?: readonly string[];
}

export const VERIFIED_TOKENS: readonly VerifiedToken[] = [
  { address: "So11111111111111111111111111111111111111112", symbol: "SOL", name: "Solana", decimals: 9, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png", tags: ["native","stake"] },
  { address: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", symbol: "USDC", name: "USD Coin", decimals: 6, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png", tags: ["stable"] },
  { address: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", symbol: "USDT", name: "Tether USD", decimals: 6, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.svg", tags: ["stable"] },
  { address: "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA", symbol: "USDS", name: "USDS", decimals: 6, logoURI: "https://img-v1.raydium.io/icon/USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA.png", tags: ["stable"] },
  { address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", symbol: "BONK", name: "Bonk", decimals: 5, logoURI: "https://arweave.net/hQiPZOsRZXGXBJd_82PhVdlM_hACsT_q6wqwf5cSY7I", tags: ["meme"] },
  { address: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", symbol: "JUP", name: "Jupiter", decimals: 6, logoURI: "https://static.jup.ag/jup/icon.png" },
  { address: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", symbol: "WIF", name: "dogwifhat", decimals: 6, logoURI: null, tags: ["meme"] },
  { address: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", symbol: "RAY", name: "Raydium", decimals: 6, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/logo.png" },
  { address: "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", symbol: "ORCA", name: "Orca", decimals: 6, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE/logo.png" },
  { address: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", symbol: "mSOL", name: "Marinade staked SOL", decimals: 9, logoURI: "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So/logo.png", tags: ["lst"] },
  { address: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", symbol: "JitoSOL", name: "Jito Staked SOL", decimals: 9, logoURI: "https://storage.googleapis.com/token-metadata/JitoSOL-256.png", tags: ["lst"] },
  { address: "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v", symbol: "jupSOL", name: "Jupiter Staked SOL", decimals: 9, logoURI: "https://static.jup.ag/jupSOL/icon.png", tags: ["lst"] },
  { address: "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1", symbol: "bSOL", name: "BlazeStake Staked SOL", decimals: 9, logoURI: null, tags: ["lst"] },
  { address: "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs", symbol: "ETH", name: "Ethereum (Wormhole)", decimals: 8, logoURI: null, aliases: ["WETH"], tags: ["wrapped"] },
  { address: "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", symbol: "WBTC", name: "Wrapped BTC (Wormhole)", decimals: 8, logoURI: null, tags: ["wrapped"] },
  { address: "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", symbol: "PYTH", name: "Pyth Network", decimals: 6, logoURI: null },
  { address: "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof", symbol: "RENDER", name: "Render Token", decimals: 8, logoURI: null },
  { address: "SHDWyBxihqiCj6YekG2GUr7wqKLeLAMK1gHZck9pL6y", symbol: "SHDW", name: "Shadow Token", decimals: 9, logoURI: null },
  { address: "mb1eu7TzEc71KxDpsmsKoucSSuuoGLv1drys1oP2jh6", symbol: "MOBILE", name: "Helium Mobile", decimals: 6, logoURI: null },
] as const;

const _byAddress = new Map<string, VerifiedToken>(VERIFIED_TOKENS.map(t => [t.address, t]));
const _bySymbol = new Map<string, VerifiedToken>(VERIFIED_TOKENS.map(t => [t.symbol.toUpperCase(), t]));

/** Look up a verified token by mint address (returns null if not in the registry). */
export function getVerifiedTokenByAddress(addr: string): VerifiedToken | null {
  return _byAddress.get(addr) ?? null;
}

/** Look up a verified token by symbol (case-insensitive). */
export function getVerifiedTokenBySymbol(sym: string): VerifiedToken | null {
  return _bySymbol.get(sym?.toUpperCase()) ?? null;
}
