export interface Agent {
  id: string;
  name: string;
  description: string;
  category: AgentCategory;
  creator: string;
  rating: number;
  usageCount: number;
  icon: string;
  accent: string;
  featured?: boolean;
  trending?: boolean;
  /** Internal route to navigate on card click (relative to /agents) */
  route?: string;
}

export type AgentCategory =
  | 'top-picks'
  | 'trading'
  | 'defi'
  | 'analytics'
  | 'nft'
  | 'security'
  | 'utility';

export interface CategoryTab {
  id: AgentCategory | 'all';
  label: string;
  icon: string;
}

export const CATEGORY_TABS: CategoryTab[] = [
  { id: 'all', label: 'Top Picks', icon: 'sparkles' },
  { id: 'trading', label: 'Trading', icon: 'candlestick-chart' },
  { id: 'defi', label: 'DeFi', icon: 'coins' },
  { id: 'analytics', label: 'Analytics', icon: 'bar-chart-3' },
  { id: 'nft', label: 'NFT', icon: 'image' },
  { id: 'security', label: 'Security', icon: 'shield-check' },
  { id: 'utility', label: 'Utility', icon: 'wrench' },
];

export const AGENTS: Agent[] = [
  {
    id: 'yield-optimizer',
    name: 'Yield Optimizer',
    description: 'AI-powered yield analysis across Solana DeFi protocols. Scans Kamino, MarginFi, Jito, Marinade and more — surfaces the best APY moves for your portfolio.',
    category: 'defi',
    creator: 'OPRAI',
    rating: 4.9,
    usageCount: 8100,
    icon: 'bot',
    accent: '#06B6D4',
    featured: true,
    trending: true,
    route: 'yield-optimizer',
  },
  {
    id: 'perp-dex',
    name: 'Perp DEX Agent',
    description: 'Automated leveraged trading across Solana perp DEXes with position sizing, stop-losses, and cross-margin management.',
    category: 'trading',
    creator: 'OPRAI',
    rating: 4.8,
    usageCount: 12400,
    icon: 'candlestick-chart',
    accent: '#F59E0B',
    featured: true,
    trending: true,
  },
  {
    id: 'defi-strategy',
    name: 'DeFi Strategy Agent',
    description: 'Yield farming, liquidity provision, and reward compounding across top Solana DeFi protocols with risk-adjusted allocation.',
    category: 'defi',
    creator: 'OPRAI',
    rating: 4.9,
    usageCount: 18200,
    icon: 'brain',
    accent: '#A855F7',
    featured: true,
    trending: true,
  },
  {
    id: 'stop-limit',
    name: 'Stop/Limit Agent',
    description: 'Conditional orders — stop-loss, take-profit, limit buys, and trailing stops that execute automatically at your price targets.',
    category: 'trading',
    creator: 'OPRAI',
    rating: 4.7,
    usageCount: 9800,
    icon: 'shield-alert',
    accent: '#3B82F6',
    featured: true,
    trending: true,
  },
  {
    id: 'token-analysis',
    name: 'Token Analysis Agent',
    description: 'Real-time on-chain analytics — holder distribution, whale movements, liquidity depth, and smart money flow tracking.',
    category: 'analytics',
    creator: 'OPRAI',
    rating: 4.6,
    usageCount: 15600,
    icon: 'scan-search',
    accent: '#10B981',
    featured: true,
    trending: true,
  },
  {
    id: 'nft-sniper',
    name: 'NFT Sniper',
    description: 'Auto-snipe underpriced NFTs on Magic Eden & Tensor with floor tracking, rarity filters, and instant buy execution.',
    category: 'nft',
    creator: 'SolanaLabs',
    rating: 4.5,
    usageCount: 7300,
    icon: 'crosshair',
    accent: '#EC4899',
    trending: true,
  },
  {
    id: 'whale-tracker',
    name: 'Whale Tracker',
    description: 'Monitor large wallet movements in real-time. Get alerts on whale buys, sells, and token accumulation patterns.',
    category: 'analytics',
    creator: 'DefiPulse',
    rating: 4.4,
    usageCount: 11200,
    icon: 'eye',
    accent: '#06B6D4',
    trending: true,
  },
  {
    id: 'airdrop-farmer',
    name: 'Airdrop Farmer',
    description: 'Automate protocol interactions to maximize airdrop eligibility. Tracks criteria, manages wallets, and optimizes activity.',
    category: 'defi',
    creator: 'CryptoNative',
    rating: 4.3,
    usageCount: 21000,
    icon: 'gift',
    accent: '#8B5CF6',
  },
  {
    id: 'mev-protector',
    name: 'MEV Protector',
    description: 'Shield your transactions from sandwich attacks and MEV extraction using Jito bundles and private transaction routing.',
    category: 'security',
    creator: 'OPRAI',
    rating: 4.7,
    usageCount: 8900,
    icon: 'shield-check',
    accent: '#EF4444',
  },
  {
    id: 'portfolio-rebalancer',
    name: 'Portfolio Rebalancer',
    description: 'Automatically rebalance your portfolio to target allocations with customizable thresholds and DCA strategies.',
    category: 'utility',
    creator: 'OPRAI',
    rating: 4.5,
    usageCount: 6400,
    icon: 'pie-chart',
    accent: '#14B8A6',
  },
  {
    id: 'copy-trader',
    name: 'Copy Trade Agent',
    description: 'Mirror trades from top-performing wallets in real-time. Filter by PnL, win rate, and trading style.',
    category: 'trading',
    creator: 'AlphaDAO',
    rating: 4.6,
    usageCount: 13500,
    icon: 'users',
    accent: '#F97316',
  },
  {
    id: 'lending-optimizer',
    name: 'Lending Optimizer',
    description: 'Find the best lending/borrowing rates across MarginFi, Kamino, and Solend. Auto-migrate for optimal yields.',
    category: 'defi',
    creator: 'YieldMax',
    rating: 4.4,
    usageCount: 5200,
    icon: 'landmark',
    accent: '#6366F1',
  },
  {
    id: 'rug-detector',
    name: 'Rug Pull Detector',
    description: 'Analyze token contracts for rug pull indicators — mint authority, LP locks, holder concentration, and suspicious patterns.',
    category: 'security',
    creator: 'OPRAI',
    rating: 4.8,
    usageCount: 19300,
    icon: 'alert-triangle',
    accent: '#DC2626',
  },
];
