import { Injectable } from '@angular/core';

const SOL_LOGO = 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png';

interface LstInfo {
  mint: string;
  symbol: string;
  name: string;
  protocol: string;
  logoUri: string;
  // Default APY shown as a badge next to the LST in the token list. These
  // are stable per-LST signals (not real-time); a live refresh is a separate
  // pipeline (chat-service-py `yield` query) that overwrites this in-place.
  defaultApy: number;
}

const LST_REGISTRY: LstInfo[] = [
  { mint: 'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So', symbol: 'mSOL', name: 'Marinade Staked SOL', protocol: 'Marinade', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So/logo.png', defaultApy: 5.95 },
  { mint: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', symbol: 'JitoSOL', name: 'Jito Staked SOL', protocol: 'Jito', logoUri: 'https://storage.googleapis.com/token-metadata/JitoSOL-256.png', defaultApy: 5.80 },
  { mint: '7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj', symbol: 'stSOL', name: 'Lido Staked SOL', protocol: 'Lido', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj/logo.png', defaultApy: 5.50 },
  { mint: 'he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A', symbol: 'hSOL', name: 'Helius Staked SOL', protocol: 'Helius', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A/logo.png', defaultApy: 6.05 },
  { mint: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v', symbol: 'jupSOL', name: 'Jupiter Staked SOL', protocol: 'Jupiter', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v/logo.png', defaultApy: 6.33 },
];

// Protocol logos resolved from public CDNs that serve CORS-friendly assets.
// solana-labs/token-list raw GitHub URLs were deprecated in early 2026 and
// now return 404 — prefer protocol-owned domains.
const PROTOCOL_LOGOS: Record<string, string> = {
  'solana-staking': SOL_LOGO,
  'marinade': 'https://marinade.finance/favicon.ico',
  'jito': 'https://storage.googleapis.com/token-metadata/JitoSOL-256.png',
  'lido': 'https://lido.fi/favicon.ico',
  'helius': 'https://www.helius.dev/favicon.ico',
  'jupiter': 'https://jup.ag/favicon.ico',
  'raydium': 'https://img.raydium.io/icon/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/logo.png',
  'kamino': 'https://app.kamino.finance/favicon.ico',
  'marginfi': 'https://app.marginfi.com/favicon.ico',
  'orca': 'https://www.orca.so/favicon.ico',
  // app.meteora.ag/favicon.ico ships an .ico that browsers crop oddly in our
  // 22px circle. Use their CDN-hosted PNG logo instead.
  'meteora': 'https://www.meteora.ag/icons/logo.svg',
  'drift': 'https://app.drift.trade/favicon.ico',
  'streamflow': 'https://app.streamflow.finance/favicon.ico',
  'pumpfun': 'https://pump.fun/_next/image?url=%2Flogo.png&w=64&q=75',
  // Jupiter Portfolio API platformIds we know about — keep this lower-cased
  // because the API returns kebab/dash IDs ("jupiter-exchange"). Falls back
  // to the generic 'jupiter' logo for any unmapped ID, then to platformLogo
  // surfaced by the platforms endpoint.
  'jupiter-exchange': 'https://jup.ag/favicon.ico',
  'jupiter-perpetuals': 'https://jup.ag/favicon.ico',
  'jupiter-lend': 'https://jup.ag/favicon.ico',
  'jupiter-dca': 'https://jup.ag/favicon.ico',
  'jupiter-limit-orders': 'https://jup.ag/favicon.ico',
  'jupiter-governance': 'https://jup.ag/favicon.ico',
};

const KNOWN_PROGRAMS: Record<string, string> = {
  '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'Raydium AMM v4',
  'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc': 'Orca Whirlpool',
  'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo': 'Meteora DLMM',
  'MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA': 'MarginFi v2',
  'KLend2g3cP87ezdnapCai5XhzGSHM4D6M5TqNmhxUt': 'Kamino Lending',
};

const LST_MINT_SET = new Set(LST_REGISTRY.map((l) => l.mint));
const LST_MINT_MAP = new Map(LST_REGISTRY.map((l) => [l.mint, l]));

@Injectable({ providedIn: 'root' })
export class ProtocolDetectionService {
  isLiquidStakingToken(mint: string): boolean {
    return LST_MINT_SET.has(mint);
  }

  getLstProtocol(mint: string): string | null {
    return LST_MINT_MAP.get(mint)?.protocol ?? null;
  }

  getLstInfo(mint: string): LstInfo | null {
    return LST_MINT_MAP.get(mint) ?? null;
  }

  getAllLstMints(): string[] {
    return LST_REGISTRY.map((l) => l.mint);
  }

  classifyToken(mint: string): { isLst: boolean; protocol: string | null } {
    if (LST_MINT_SET.has(mint)) {
      return { isLst: true, protocol: LST_MINT_MAP.get(mint)!.protocol };
    }
    return { isLst: false, protocol: null };
  }

  getProgramName(programId: string): string | null {
    return KNOWN_PROGRAMS[programId] ?? null;
  }

  getProtocolLogo(protocolId: string): string | null {
    return PROTOCOL_LOGOS[protocolId] ?? null;
  }

  getSolLogo(): string {
    return SOL_LOGO;
  }

  /**
   * Default APY for a known LST. Returns null when the mint isn't an LST.
   * Used by token-list as a fallback when DefiLlama doesn't have the
   * project — the inline "5.66%" badge prefers the live rate.
   */
  getLstDefaultApy(mint: string): number | null {
    return LST_MINT_MAP.get(mint)?.defaultApy ?? null;
  }
}
