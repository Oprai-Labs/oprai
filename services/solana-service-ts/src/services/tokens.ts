// Common token registry + Jupiter token list resolver

export const SOL_MINT = "So11111111111111111111111111111111111111112";
export const WSOL_MINT = SOL_MINT;
export const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
export const USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB";

const WELL_KNOWN: Record<string, { mint: string; decimals: number; name: string }> = {
  SOL:   { mint: SOL_MINT, decimals: 9,  name: "Solana" },
  WSOL:  { mint: WSOL_MINT, decimals: 9, name: "Wrapped SOL" },
  USDC:  { mint: USDC_MINT, decimals: 6, name: "USD Coin" },
  USDT:  { mint: USDT_MINT, decimals: 6, name: "Tether USD" },
  BONK:  { mint: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", decimals: 5, name: "Bonk" },
  JUP:   { mint: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", decimals: 6, name: "Jupiter" },
  RAY:   { mint: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", decimals: 6, name: "Raydium" },
  ORCA:  { mint: "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", decimals: 6, name: "Orca" },
  MSOL:  { mint: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", decimals: 9, name: "Marinade SOL" },
  JITOSOL: { mint: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", decimals: 9, name: "Jito SOL" },
  JUPSOL: { mint: "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v", decimals: 9, name: "Jupiter SOL" },
  MNDE:  { mint: "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey", decimals: 9, name: "Marinade" },
  KMNO:  { mint: "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS", decimals: 6, name: "Kamino" },
  DRIFT: { mint: "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7", decimals: 6, name: "Drift" },
  WIF:   { mint: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", decimals: 6, name: "dogwifhat" },
  PYTH:  { mint: "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", decimals: 6, name: "Pyth Network" },
  W:     { mint: "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ", decimals: 6, name: "Wormhole" },
  POPCAT: { mint: "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", decimals: 9, name: "Popcat" },
};

/** Resolve a token symbol or mint address → { mint, decimals }. */
export function resolveToken(symbolOrMint: string): { mint: string; decimals: number; name?: string } | null {
  const upper = symbolOrMint.toUpperCase();
  if (WELL_KNOWN[upper]) return WELL_KNOWN[upper];
  // Treat as mint address if it's long enough
  if (symbolOrMint.length >= 32) return { mint: symbolOrMint, decimals: 9 };
  return null;
}

/** Fetch token info from Jupiter token list (fallback). */
export async function fetchTokenInfo(
  mint: string
): Promise<{ mint: string; decimals: number; symbol: string; name: string } | null> {
  try {
    const res = await fetch(
      `https://tokens.jup.ag/token/${mint}`
    );
    if (!res.ok) return null;
    const data = await res.json() as { address: string; decimals: number; symbol: string; name: string };
    return {
      mint: data.address,
      decimals: data.decimals,
      symbol: data.symbol,
      name: data.name,
    };
  } catch {
    return null;
  }
}
