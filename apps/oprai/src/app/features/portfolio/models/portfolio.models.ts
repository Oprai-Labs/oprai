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
  // Heuristic flag — true when the token shows known spam signatures:
  // invisible-only symbol (Hangul filler etc.), promotional keywords in
  // name/symbol, embedded URL, or duplicate symbol across multiple mints.
  // Defaults to a hidden state in the token list with a "Show spam" toggle.
  isSuspectedSpam?: boolean;
  // The raw reason string is exposed in tooltip / dev mode so users (and
  // we) can sanity-check why a token was flagged. Kept short to render
  // inline.
  spamReason?: string;
  // A DeFi position-receipt token (LP token, vault share) that already appears
  // in the Active Positions panel. Hidden from the wallet token list so it
  // doesn't double-render as an "Unknown Token".
  isDefiPositionToken?: boolean;
  // Native APY when the holding itself yields (LSTs: jitoSOL ~5.6%, jupSOL ~6.1%).
  // Renders as a small green badge next to the symbol — purely informational.
  // Live-fetched from DefiLlama via LiveYieldsService.
  apy?: number | null;
  // All-time PnL for this holding. Populated in PR 2 from
  // wallet_token_costbasis + current price; null when no cost-basis exists.
  pnlAllTimeUsd?: number | null;
  pnlAllTimePct?: number | null;
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
  // Realized + unrealized PnL on native SOL — same source (cost basis
  // table) as `EnhancedTokenAccount.pnlAllTime*`, just lifted up to the
  // dedicated SOL row so its column has data on first paint.
  pnlAllTimeUsd?: number | null;
  pnlAllTimePct?: number | null;
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
  // Two-leg metadata for swaps. Populated when both legs are decoded so the
  // history table can render "−1.5 SOL → +120 USDC" with logos on each side.
  fromSymbol?: string | null;
  fromLogoUri?: string | null;
  fromUsdValue?: number | null;
  toSymbol?: string | null;
  toLogoUri?: string | null;
  toUsdValue?: number | null;
}

export interface EnhancedTransaction {
  signature: string;
  blockTime: number | null;
  success: boolean;
  type: TransactionType;
  description: string;
  details: TransactionDetail | null;
  platform: string | null;  // Helius source field (Jupiter, Raydium, Orca, etc.)
  // Raw Helius transaction type (e.g. "ADD_LIQUIDITY", "CREATE_POOL",
  // "DEPOSIT"). Our `type` enum collapses everything it doesn't model to
  // 'unknown'; keeping the raw string lets the UI label a DeFi action
  // ("Add Liquidity") instead of a meaningless "ACTION".
  heliusType?: string | null;
}

// ──── Protocol Positions ────

export type ProtocolCategory =
  | 'native-staking'
  | 'liquid-staking'
  | 'liquidity-pool'
  | 'lending'
  | 'borrowing'
  | 'perpetuals'
  | 'streaming'
  // Open Jupiter DCA / limit orders
  | 'orders'
  // Pump.fun creator rewards, JUP unstake claimable, etc.
  | 'rewards';

export interface PositionItem {
  label: string;
  // `mint` lets the post-processor look up a USD price for any token leg
  // whose protocol API didn't surface one — the path that finally lets
  // Jupiter Lend / Orca / Raydium CLMM / Streamflow show real dollar values
  // in the portfolio total.
  // `kind` labels a leg in a multi-token position so the Tokens column can say
  // which side is collateral vs borrowed (e.g. a Morpho borrow). Optional — most
  // positions leave it unset and render the amount alone.
  tokens: Array<{ symbol: string; amount: number; logoUri: string | null; mint?: string; kind?: string }>;
  totalUsdValue: number | null;
  metadata: Record<string, string | number | null>;
  // First-class fields surfaced as their own columns in defi-positions.
  // metadata still holds protocol-specific extras; these are the cross-cutting
  // signals every protocol can populate.
  apy?: number | null;
  claimableUsd?: number | null;
  feesUsd?: number | null;
  pnlUsd?: number | null;
  pnlPct?: number | null;
}

export interface ProtocolPosition {
  protocolId: string;
  protocolName: string;
  protocolLogoUri: string | null;
  category: ProtocolCategory;
  positions: PositionItem[];
  totalUsdValue: number;
  // Aggregate claimable across all positions in this protocol — drives the
  // "X Reclaim" header badge ("72 Reclaim" in the Jupiter UI). Both are
  // recomputed in priceAllPositions so callers don't need to maintain them.
  totalClaimableUsd?: number | null;
  claimableCount?: number;
}

// ──── Portfolio Value Change ────

export interface PortfolioValueChange {
  change24hUsd: number;
  change24hPercent: number;
}

// ──── Jupiter Portfolio API ────
// api.jup.ag/portfolio/v1/* response shapes. Scope is Jupiter products only
// (DCA, limit, perp, lend, JUP/JupSOL stake, LP). The frontend never hits
// Jupiter directly — every call goes through the gateway proxy at
// /v1/market/jupiter/portfolio/* so the x-api-key stays server-side.

export type JupiterPortfolioElementType =
  | 'liquidity'
  | 'borrowlend'
  | 'leverage'
  | 'trade'
  | 'multiple';

export interface JupiterPortfolioElement {
  type: JupiterPortfolioElementType;
  platformId: string;
  label: string;
  // USD value the fetcher computed for this element. Optional because some
  // fetchers (e.g. open limit orders) don't have a meaningful USD until fill.
  value?: number;
  // Per-element shape varies by `type`; we keep the raw payload here and let
  // the consumer narrow it. Documenting every variant in code would chain
  // breakage to upstream changes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data?: any;
  // Surface fee / claimable signals when fetcher provides them; not all do.
  apy?: number | null;
  claimableUsd?: number | null;
  feesUsd?: number | null;
  pnlUsd?: number | null;
}

export interface JupiterPortfolioTokenInfo {
  symbol?: string;
  decimals?: number;
  logoURI?: string;
}

export interface JupiterPortfolioResponse {
  date: number;
  owner: string;
  duration: number;
  elements: JupiterPortfolioElement[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  fetcherReports: any[];
  tokenInfo: Record<string, JupiterPortfolioTokenInfo>;
}

export interface JupiterStakedJupResponse {
  stakedAmount: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  unstaking: any[];
}

export interface JupiterPlatform {
  id: string;
  name: string;
  image?: string;
  description?: string;
  defiLlamaId?: string;
  isDeprecated?: boolean;
  tokens?: string[];
}

// ──── Pump.fun creator rewards ────
// PR 1 contract — the gateway currently stubs this; PR 3 fills it via
// on-chain creator-vault PDA decode. Frontend handles both shapes already.

export interface PumpfunCreatorRewards {
  wallet: string;
  claimableLamports: number;
  claimableSol: number;
  claimableUsd: number;
  rewards: Array<{
    mint: string;
    symbol: string;
    claimableLamports: number;
    claimableUsd: number;
  }>;
}

// ──── Tabs ────

export type PortfolioTab = 'portfolio' | 'nfts' | 'history';

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
