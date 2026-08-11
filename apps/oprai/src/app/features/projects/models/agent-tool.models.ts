/**
 * Agent Tool/Capability Definitions
 *
 * All available tools that can be assigned to custom agents.
 * These map to actual DeFi functionalities in the system.
 */

export type ToolCategory =
  | 'swap'
  | 'lend'
  | 'borrow'
  | 'stake'
  | 'unstake'
  | 'liquidity'
  | 'perp'
  | 'nft'
  | 'bridge'
  | 'portfolio'
  | 'analytics'
  | 'alerts'
  | 'automation'
  | 'security';

export interface AgentTool {
  id: string;
  name: string;
  description: string;
  category: ToolCategory;
  icon: string;
  protocols: string[];
  riskLevel: 'low' | 'medium' | 'high';
  requiresApproval: boolean;
  params?: ToolParam[];
}

export interface ToolParam {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'select';
  required: boolean;
  default?: any;
  options?: string[];
  description?: string;
}

export interface AgentCapability {
  toolId: string;
  enabled: boolean;
  customParams?: Record<string, any>;
}

export interface AgentTrigger {
  type: 'price' | 'time' | 'apy' | 'volume' | 'whale' | 'custom';
  condition: string;
  value: number;
  expression?: string;
}

export interface AgentSchedule {
  enabled: boolean;
  interval?: 'hourly' | 'daily' | 'weekly' | 'custom';
  customCron?: string;
  timezone?: string;
}

export interface AgentRiskConfig {
  maxSlippage: number;
  maxGasFee: number;
  maxPositionSize: number;
  autoStopLoss?: number;
  takeProfitLevel?: number;
  allowedProtocols: string[];
  blockedTokens: string[];
}

export interface AgentNotificationConfig {
  enabled: boolean;
  channels: ('in_app' | 'telegram' | 'discord' | 'email')[];
  onExecute: boolean;
  onError: boolean;
  onProfit: boolean;
  onLoss: boolean;
  quietHours?: { start: string; end: string };
  minProfitThreshold?: number;
}

export interface AgentConfig {
  // Basic Info
  name: string;
  description: string;
  category: AgentCategory;
  icon: string;
  accentColor: string;

  // Capabilities
  capabilities: AgentCapability[];

  // Execution
  autoExecute: boolean;
  maxConcurrentActions: number;
  approvalRequiredAbove: number;

  // Risk
  risk: AgentRiskConfig;

  // Schedule
  schedule: AgentSchedule;

  // Triggers
  triggers: AgentTrigger[];

  // Notifications
  notifications: AgentNotificationConfig;

  // Instructions
  systemPrompt: string;
  temperature: number;
  maxTokens: number;
}

export type AgentCategory =
  | 'trading'
  | 'defi'
  | 'analytics'
  | 'nft'
  | 'security'
  | 'utility'
  | 'custom';

// ─── Tool Definitions ────────────────────────────────────────────────────────

export const AGENT_TOOLS: AgentTool[] = [
  // Swap Tools
  {
    id: 'jupiter_swap',
    name: 'Jupiter Swap',
    description: 'Swap any tokens on Solana via Jupiter aggregator with best routes',
    category: 'swap',
    icon: 'swap-horizontal',
    protocols: ['Jupiter'],
    riskLevel: 'low',
    requiresApproval: true,
    params: [
      { name: 'maxSlippage', type: 'number', required: false, default: 1, description: 'Max slippage %' },
      { name: 'useVersionedTx', type: 'boolean', required: false, default: true },
    ],
  },
  {
    id: 'orca_swap',
    name: 'Orca Swap',
    description: 'Swap tokens via Orca Whirlpools concentrated liquidity',
    category: 'swap',
    icon: 'waves',
    protocols: ['Orca'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'raydium_swap',
    name: 'Raydium Swap',
    description: 'Swap tokens via Raydium CLMM pools',
    category: 'swap',
    icon: 'zap',
    protocols: ['Raydium'],
    riskLevel: 'medium',
    requiresApproval: true,
  },

  // Lending Tools
  {
    id: 'jupiter_lend',
    name: 'Jupiter Lend',
    description: 'Lend and borrow tokens via Jupiter Earn program',
    category: 'lend',
    icon: 'landmark',
    protocols: ['Jupiter'],
    riskLevel: 'low',
    requiresApproval: true,
  },
  {
    id: 'kamino_lend',
    name: 'Kamino Lending',
    description: 'Lend/borrow on Kamino for optimized yields',
    category: 'lend',
    icon: 'building-2',
    protocols: ['Kamino'],
    riskLevel: 'low',
    requiresApproval: true,
  },
  {
    id: 'solend_lend',
    name: 'Solend Lending',
    description: 'Lend/borrow on Solend protocol',
    category: 'lend',
    icon: 'banknote',
    protocols: ['Solend'],
    riskLevel: 'low',
    requiresApproval: true,
  },

  // Staking Tools
  {
    id: 'jupsol_stake',
    name: 'JupSOL Stake',
    description: 'Stake SOL to receive JupSOL with auto-compounding',
    category: 'stake',
    icon: 'arrow-up-circle',
    protocols: ['Jupiter'],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'jito_stake',
    name: 'Jito Stake',
    description: 'Stake SOL to receive JitoSOL with MEV rewards',
    category: 'stake',
    icon: 'rocket',
    protocols: ['Jito'],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'marinade_stake',
    name: 'Marinade Stake',
    description: 'Stake SOL to receive mSOL via Marinade',
    category: 'stake',
    icon: 'leaf',
    protocols: ['Marinade'],
    riskLevel: 'low',
    requiresApproval: false,
  },
  // Liquidity Tools
  {
    id: 'orca_liquidity',
    name: 'Orca Liquidity',
    description: 'Add/remove liquidity to Orca Whirlpools',
    category: 'liquidity',
    icon: 'droplets',
    protocols: ['Orca'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'raydium_liquidity',
    name: 'Raydium Liquidity',
    description: 'Add/remove liquidity to Raydium pools and farm RAY',
    category: 'liquidity',
    icon: 'layers',
    protocols: ['Raydium'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'meteora_liquidity',
    name: 'Meteora Liquidity',
    description: 'Add/remove liquidity to Meteora DLMM pools',
    category: 'liquidity',
    icon: 'gauge',
    protocols: ['Meteora'],
    riskLevel: 'medium',
    requiresApproval: true,
  },

  // Perpetuals
  {
    id: 'jupiter_perp',
    name: 'Jupiter Perp (JLP)',
    description: 'Trade perps and provide liquidity to JLP via Jupiter',
    category: 'perp',
    icon: 'line-chart',
    protocols: ['Jupiter'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: '01_protocol_perp',
    name: '01 Exchange Perp',
    description: 'Trade perpetual futures on 01 Exchange',
    category: 'perp',
    icon: 'git-branch',
    protocols: ['01 Exchange'],
    riskLevel: 'high',
    requiresApproval: true,
  },

  // NFT Tools
  {
    id: 'magiceden_buy',
    name: 'Magic Eden NFT',
    description: 'Buy/sell NFTs on Magic Eden marketplace',
    category: 'nft',
    icon: 'shopping-bag',
    protocols: ['Magic Eden'],
    riskLevel: 'high',
    requiresApproval: true,
  },
  {
    id: 'tensor_buy',
    name: 'Tensor NFT',
    description: 'Trade NFTs on Tensor marketplace with AI',
    category: 'nft',
    icon: 'brain',
    protocols: ['Tensor'],
    riskLevel: 'high',
    requiresApproval: true,
  },

  // Bridge Tools
  {
    id: 'wormhole_bridge',
    name: 'Wormhole Bridge',
    description: 'Bridge tokens cross-chain via Wormhole',
    category: 'bridge',
    icon: 'globe',
    protocols: ['Wormhole'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'relay_bridge',
    name: 'Relay Bridge',
    description: 'Bridge tokens via Relay for fast transfers',
    category: 'bridge',
    icon: 'send',
    protocols: ['Relay'],
    riskLevel: 'low',
    requiresApproval: true,
  },
  {
    id: 'debridge_bridge',
    name: 'DeBridge',
    description: 'Cross-chain swaps via DeBridge',
    category: 'bridge',
    icon: 'link',
    protocols: ['DeBridge'],
    riskLevel: 'medium',
    requiresApproval: true,
  },

  // Portfolio Tools
  {
    id: 'portfolio_rebalance',
    name: 'Portfolio Rebalance',
    description: 'Automatically rebalance portfolio to target allocations',
    category: 'portfolio',
    icon: 'pie-chart',
    protocols: [],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'dca_strategy',
    name: 'DCA Strategy',
    description: 'Dollar-cost average into positions over time',
    category: 'portfolio',
    icon: 'calendar',
    protocols: ['Jupiter'],
    riskLevel: 'low',
    requiresApproval: false,
  },

  // Analytics Tools
  {
    id: 'token_analysis',
    name: 'Token Analysis',
    description: 'Analyze token metrics, holder distribution, liquidity',
    category: 'analytics',
    icon: 'scan-search',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'whale_tracking',
    name: 'Whale Tracker',
    description: 'Monitor large wallet movements and smart money',
    category: 'analytics',
    icon: 'eye',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'market_data',
    name: 'Market Data',
    description: 'Get real-time price, volume, and market data',
    category: 'analytics',
    icon: 'bar-chart-3',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },

  // Alerts Tools
  {
    id: 'price_alert',
    name: 'Price Alerts',
    description: 'Get notified on price movements',
    category: 'alerts',
    icon: 'bell',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'apy_alert',
    name: 'APY Alerts',
    description: 'Alert when yield opportunities meet criteria',
    category: 'alerts',
    icon: 'trending-up',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },

  // Automation Tools
  {
    id: 'limit_order',
    name: 'Limit Orders',
    description: 'Set limit orders that execute at target price',
    category: 'automation',
    icon: 'clock',
    protocols: ['Jupiter'],
    riskLevel: 'medium',
    requiresApproval: true,
  },
  {
    id: 'stop_loss',
    name: 'Stop Loss',
    description: 'Auto-sell when price drops below threshold',
    category: 'automation',
    icon: 'shield-alert',
    protocols: ['Jupiter'],
    riskLevel: 'medium',
    requiresApproval: false,
  },
  {
    id: 'take_profit',
    name: 'Take Profit',
    description: 'Auto-sell when price reaches profit target',
    category: 'automation',
    icon: 'target',
    protocols: ['Jupiter'],
    riskLevel: 'low',
    requiresApproval: false,
  },

  // Security Tools
  {
    id: 'rug_check',
    name: 'Rug Pull Detector',
    description: 'Analyze tokens for scam indicators',
    category: 'security',
    icon: 'shield-check',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'mev_protect',
    name: 'MEV Protection',
    description: 'Protect transactions from MEV extraction via Jito',
    category: 'security',
    icon: 'lock',
    protocols: ['Jito'],
    riskLevel: 'low',
    requiresApproval: false,
  },
  {
    id: 'approval_manager',
    name: 'Token Approvals',
    description: 'Review and revoke token approvals',
    category: 'security',
    icon: 'key',
    protocols: [],
    riskLevel: 'low',
    requiresApproval: true,
  },
];

// ─── Category Groups ─────────────────────────────────────────────────────────

export const TOOL_CATEGORIES: { id: ToolCategory; label: string; icon: string }[] = [
  { id: 'swap', label: 'Swaps', icon: 'swap-horizontal' },
  { id: 'lend', label: 'Lending', icon: 'landmark' },
  { id: 'stake', label: 'Staking', icon: 'arrow-up-circle' },
  { id: 'liquidity', label: 'Liquidity', icon: 'droplets' },
  { id: 'perp', label: 'Perpetuals', icon: 'line-chart' },
  { id: 'nft', label: 'NFT', icon: 'image' },
  { id: 'bridge', label: 'Bridge', icon: 'globe' },
  { id: 'portfolio', label: 'Portfolio', icon: 'pie-chart' },
  { id: 'analytics', label: 'Analytics', icon: 'bar-chart-3' },
  { id: 'alerts', label: 'Alerts', icon: 'bell' },
  { id: 'automation', label: 'Automation', icon: 'zap' },
  { id: 'security', label: 'Security', icon: 'shield' },
];

// ─── Helper Functions ────────────────────────────────────────────────────────

export function getToolsByCategory(category: ToolCategory): AgentTool[] {
  return AGENT_TOOLS.filter(t => t.category === category);
}

export function getToolById(id: string): AgentTool | undefined {
  return AGENT_TOOLS.find(t => t.id === id);
}

export function getToolsByIds(ids: string[]): AgentTool[] {
  return AGENT_TOOLS.filter(t => ids.includes(t.id));
}

export function getAllProtocols(): string[] {
  const protocols = new Set<string>();
  AGENT_TOOLS.forEach(t => t.protocols.forEach(p => protocols.add(p)));
  return Array.from(protocols).sort();
}

export function validateAgentConfig(config: AgentConfig): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  if (!config.name.trim()) errors.push('Agent name is required');
  if (!config.description.trim()) errors.push('Description is required');
  if (config.name.length > 50) errors.push('Name must be 50 characters or less');
  if (config.description.length > 500) errors.push('Description must be 500 characters or less');

  const enabledTools = config.capabilities.filter(c => c.enabled);
  if (enabledTools.length === 0) errors.push('At least one tool must be enabled');

  if (config.risk.maxSlippage < 0 || config.risk.maxSlippage > 100) {
    errors.push('Max slippage must be between 0 and 100');
  }

  if (config.risk.maxGasFee < 0) errors.push('Max gas fee must be positive');
  if (config.risk.maxPositionSize < 0) errors.push('Max position size must be positive');

  if (config.schedule.enabled && config.schedule.interval === 'custom' && !config.schedule.customCron) {
    errors.push('Custom schedule requires a cron expression');
  }

  if (config.notifications.enabled && config.notifications.channels.length === 0) {
    errors.push('At least one notification channel must be selected');
  }

  return { valid: errors.length === 0, errors };
}

export function getDefaultAgentConfig(): AgentConfig {
  return {
    name: '',
    description: '',
    category: 'defi',
    icon: 'bot',
    accentColor: '#6366F1',
    capabilities: AGENT_TOOLS.map(tool => ({
      toolId: tool.id,
      enabled: false,
    })),
    autoExecute: false,
    maxConcurrentActions: 1,
    approvalRequiredAbove: 100,
    risk: {
      maxSlippage: 1,
      maxGasFee: 0.01,
      maxPositionSize: 10000,
      allowedProtocols: ['Jupiter', 'Jito', 'Kamino'],
      blockedTokens: [],
    },
    schedule: {
      enabled: false,
      interval: 'daily',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    },
    triggers: [],
    notifications: {
      enabled: true,
      channels: ['in_app'],
      onExecute: true,
      onError: true,
      onProfit: true,
      onLoss: true,
    },
    systemPrompt: '',
    temperature: 0.7,
    maxTokens: 2048,
  };
}

export function exportAgentConfig(config: AgentConfig): string {
  return JSON.stringify(config, null, 2);
}

export function importAgentConfig(json: string): AgentConfig | null {
  try {
    const config = JSON.parse(json) as AgentConfig;
    // Validate required fields
    if (!config.name || !config.category || !config.capabilities) {
      return null;
    }
    return config;
  } catch {
    return null;
  }
}
