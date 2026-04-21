export interface TokenAccount {
  mint: string;
  symbol: string;
  name: string;
  logoUri: string | null;
  balance: number;
  decimals: number;
  usdPrice: number | null;
  usdValue: number | null;
}

export interface EnhancedTokenAccount extends TokenAccount {
  priceChange24h: number | null;
  allocationPercent: number;
  isLiquidStaking: boolean;
  protocol: string | null;
}

export type TokenSortField = 'value' | 'name' | 'change24h' | 'allocation';
export type SortDirection = 'asc' | 'desc';

export interface SolBalance {
  lamports: number;
  sol: number;
  usdPrice: number | null;
  usdValue: number | null;
  priceChange24h: number | null;
  allocationPercent: number;
}

export interface PortfolioSummary {
  walletAddress: string;
  solBalance: SolBalance;
  tokens: EnhancedTokenAccount[];
  totalUsdValue: number;
}

export interface StakePosition {
  pubkey: string;
  validatorVoteAccount: string;
  stakedLamports: number;
  stakedSol: number;
  usdValue: number | null;
  status: 'active' | 'activating' | 'deactivating' | 'inactive';
}

export interface DefiPositions {
  stakePositions: StakePosition[];
  totalStakedSol: number;
  totalStakedUsdValue: number | null;
}

export interface RecentTransaction {
  signature: string;
  blockTime: number | null;
  success: boolean;
  memo: string | null;
}

// ──── NFTs ────

export interface NftAsset {
  id: string;
  name: string;
  imageUri: string | null;
  collectionName: string | null;
  collectionId: string | null;
  floorPrice: number | null;
  compressed: boolean;
}

export interface NftCollection {
  id: string;
  name: string;
  imageUri: string | null;
  floorPrice: number | null;
  items: NftAsset[];
}

// ──── Enhanced Transactions ────

export type TransactionType =
  | 'transfer'
  | 'swap'
  | 'stake'
  | 'unstake'
  | 'nft-sale'
  | 'nft-purchase'
  | 'nft-mint'
  | 'token-mint'
  | 'burn'
  | 'vote'
  | 'unknown';

export interface TransactionDetail {
  fromToken: string | null;
  toToken: string | null;
  fromAmount: number | null;
  toAmount: number | null;
  counterparty: string | null;
  programName: string | null;
  fromAddress: string | null;
  toAddress: string | null;
  tokenMint: string | null;
  tokenSymbol: string | null;
  tokenLogoUri: string | null;
  usdValue: number | null;
}

export interface EnhancedTransaction {
  signature: string;
  blockTime: number | null;
  success: boolean;
  type: TransactionType;
  description: string;
  details: TransactionDetail | null;
  platform: string | null;  // Helius source field (Jupiter, Raydium, Orca, etc.)
}

// ──── Protocol Positions ────

export type ProtocolCategory =
  | 'native-staking'
  | 'liquid-staking'
  | 'liquidity-pool'
  | 'lending'
  | 'borrowing'
  | 'perpetuals'
  | 'streaming';

export interface PositionItem {
  label: string;
  tokens: Array<{ symbol: string; amount: number; logoUri: string | null }>;
  totalUsdValue: number | null;
  metadata: Record<string, string | number | null>;
}

export interface ProtocolPosition {
  protocolId: string;
  protocolName: string;
  protocolLogoUri: string | null;
  category: ProtocolCategory;
  positions: PositionItem[];
  totalUsdValue: number;
}

// ──── Portfolio Value Change ────

export interface PortfolioValueChange {
  change24hUsd: number;
  change24hPercent: number;
}

// ──── Tabs ────

export type PortfolioTab = 'tokens' | 'defi' | 'nfts' | 'history';

// ──── Existing Types ────

export interface JupiterToken {
  address: string;
  symbol: string;
  name: string;
  decimals: number;
  logoURI: string | null;
}

export type LoadingState = 'idle' | 'loading' | 'loaded' | 'error';

// DeBank-style protocol summary
export interface ProtocolCard {
  id: string;
  name: string;
  iconUrl: string | null;
  usdValue: number;
}
