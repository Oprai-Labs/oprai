import { Router, Request, Response, NextFunction } from "express";
import { internalAuth } from "../middleware/auth";
import { resolveToken } from "../services/tokens";

const router = Router();
router.use(internalAuth);

const PROTOCOLS = [
  { id: "jupiter", name: "Jupiter", category: "dex", description: "Solana DEX aggregator" },
  { id: "marginfi", name: "MarginFi", category: "lending", description: "MarginFi v2 lending protocol" },
  { id: "kamino", name: "Kamino", category: "lending", description: "Kamino Finance lending & vaults" },
  { id: "raydium", name: "Raydium", category: "dex", description: "Raydium AMM / CLMM" },
  { id: "orca", name: "Orca", category: "dex", description: "Orca Whirlpools CLMM" },
  { id: "marinade", name: "Marinade", category: "staking", description: "Marinade liquid staking" },
  { id: "meteora", name: "Meteora", category: "dex", description: "Meteora DLMM" },
  { id: "jito", name: "Jito", category: "staking", description: "Jito MEV + jitoSOL staking" },
  { id: "pumpfun", name: "Pump.fun", category: "launchpad", description: "Token launch platform" },
  { id: "magic_eden", name: "Magic Eden", category: "nft", description: "NFT marketplace" },
];

const COMMON_TOKENS = [
  { symbol: "SOL", mint: "So11111111111111111111111111111111111111112", decimals: 9 },
  { symbol: "USDC", mint: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals: 6 },
  { symbol: "USDT", mint: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", decimals: 6 },
  { symbol: "BONK", mint: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", decimals: 5 },
  { symbol: "JUP", mint: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", decimals: 6 },
  { symbol: "RAY", mint: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", decimals: 6 },
  { symbol: "ORCA", mint: "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", decimals: 6 },
  { symbol: "MSOL", mint: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", decimals: 9 },
  { symbol: "JITOSOL", mint: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", decimals: 9 },
];

router.get("/protocols", (_req, res) => {
  res.json({ protocols: PROTOCOLS });
});

router.get("/protocols/:protocol", (req: Request, res: Response, next: NextFunction) => {
  const protocol = PROTOCOLS.find((p) => p.id === req.params.protocol);
  if (!protocol) {
    res.status(404).json({ error: "Protocol not found", code: "NOT_FOUND" });
    return;
  }
  res.json({ protocol });
});

router.get("/tokens", (_req, res) => {
  res.json({ tokens: COMMON_TOKENS });
});

router.get("/tokens/:symbol", (req: Request, res: Response) => {
  const token = resolveToken(req.params.symbol);
  if (!token) {
    res.status(404).json({ error: "Token not found", code: "NOT_FOUND" });
    return;
  }
  res.json({ token: { ...token, symbol: req.params.symbol.toUpperCase() } });
});

export default router;
