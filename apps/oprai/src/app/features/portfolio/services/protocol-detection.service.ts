import { Injectable } from '@angular/core';

const SOL_LOGO = 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png';

interface LstInfo {
  mint: string;
  symbol: string;
  name: string;
  protocol: string;
  logoUri: string;
}

const LST_REGISTRY: LstInfo[] = [
  { mint: 'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So', symbol: 'mSOL', name: 'Marinade Staked SOL', protocol: 'Marinade', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So/logo.png' },
  { mint: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', symbol: 'JitoSOL', name: 'Jito Staked SOL', protocol: 'Jito', logoUri: 'https://storage.googleapis.com/token-metadata/JitoSOL-256.png' },
  { mint: '7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj', symbol: 'stSOL', name: 'Lido Staked SOL', protocol: 'Lido', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj/logo.png' },
  { mint: 'he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A', symbol: 'hSOL', name: 'Helius Staked SOL', protocol: 'Helius', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A/logo.png' },
  { mint: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v', symbol: 'jupSOL', name: 'Jupiter Staked SOL', protocol: 'Jupiter', logoUri: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v/logo.png' },
];

const PROTOCOL_LOGOS: Record<string, string> = {
  'solana-staking': SOL_LOGO,
  'marinade': 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So/logo.png',
  'jito': 'https://storage.googleapis.com/token-metadata/JitoSOL-256.png',
  'lido': 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj/logo.png',
  'helius': 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A/logo.png',
  'jupiter': 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v/logo.png',
  'raydium': 'https://img.raydium.io/icon/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/logo.png',

  'kamino': 'https://app.kamino.finance/favicon.ico',
  'marginfi': 'https://app.marginfi.com/favicon.ico',
  'orca': 'https://www.orca.so/favicon.ico',
  'meteora': 'https://app.meteora.ag/favicon.ico',
  'drift': 'https://app.drift.trade/favicon.ico',
  'streamflow': 'https://app.streamflow.finance/favicon.ico',
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
}
