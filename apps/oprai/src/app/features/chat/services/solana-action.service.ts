import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout, TimeoutError } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import { WalletService } from '@core/services/wallet.service';
import { TransactionTrackerService } from '@core/services/transaction-tracker.service';
import { SpendingLimitService } from '@core/services/spending-limit.service';
import { RiskWarningService } from '@core/services/risk-warning.service';
import { ParsedAction } from './intent-parser.service';
import { JupiterLendService } from '@core/services/market/jupiter-lend.service';
import { PumpFunService } from '@core/services/market/pumpfun.service';
import { JitoService } from '@core/services/market/jito.service';
import { environment } from '../../../../environments/environment';
import type { Transaction, VersionedTransaction } from '@solana/web3.js';

export interface ActionCallbacks {
  onQuote?: () => void;
  onSign?: () => void;
  onSubmit?: (signature: string) => void;
  /** Called when action completes. For data-only queries, `result` contains the query description. */
  onConfirm?: (result?: string) => void;
  /** Called during chain execution to report progress. */
  onStatus?: (status: string) => void;
}

interface QuoteResponse {
  quoteId: string;
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  priceImpact: string;
}

interface BuildResponse {
  transaction?: string | null; // base64-encoded serialized transaction (null for data-only actions)
  preview?: { description?: string; params?: Record<string, unknown> };
  quoteId?: string;
  // Cross-chain swap fields (Relay protocol)
  isCrossChain?: boolean;
  executionSteps?: Array<{
    id?: string;
    type?: string;
    items?: Array<{
      transaction?: {
        data?: string;
        to?: string;
        gasLimit?: string;
        value?: string;
        chainId?: number;
      };
    }>;
  }>;
  // For Wormhole/Debridge: quote data contains txData directly
  quote?: unknown;
}

// Action types that return data instead of a transaction (transaction: null from backend)
// Exposed as a static Set so other components (e.g. action-card) can reference it.
const DATA_ONLY_ACTION_TYPES_LIST: string[] = [
  // Magic Eden data queries
  'me_collection_info', 'me_nft_info', 'me_wallet_nfts',
  'me_collection_activity', 'me_listings', 'me_offers', 'me_collection_nfts',
  // MarginFi data queries
  'marginfi_account_info', 'marginfi_banks', 'marginfi_health', 'marginfi_points',
  // Solend data queries
  'solend_user_info', 'solend_reserves', 'solend_market',
  // Jito status check
  'jito_bundle_status',
  // Squid status check (returns data, no transaction)
  'squid_status',
  // Tensor NFT data queries
  'tensor_collection_info', 'tensor_nft_info', 'tensor_wallet_nfts', 'tensor_listings',
  // Generic governance & reward actions (return protocol-specific guidance, no transaction)
  'claim', 'vote',
  // Pump.fun data queries (no transaction — backend returns JSON data in preview.description)
  'pumpfun_token_info', 'pumpfun_trending', 'pumpfun_new', 'pumpfun_graduating',
  'pumpfun_search', 'pumpfun_koth', 'pumpfun_comments', 'pumpfun_user', 'pumpfun_bonding_curve',
  // PumpSwap AMM data queries
  'pumpswap_pool_info',
];

// Actions that get a quote step first (swap uses Jupiter quote API)
const SWAP_ACTION_TYPES = ['swap'];

// Actions that return versioned transactions (V0) from the backend
// Jupiter Trigger API and Recurring API return versioned transactions.
// Raydium Transaction API also returns versioned transactions.
// Orca Whirlpool API also returns versioned transactions.
// Kamino Finance API also returns versioned transactions.
// Jito Finance API also returns versioned transactions.
// Versioned (V0) transactions: returned by external DEX/protocol APIs via the Rust backend.
// Legacy transactions (bincode-serialized): deserialized with Transaction.from() — NOT listed here.
//   Legacy: marginfi, solend, marinade, bonkfun, magic_eden (all use Transaction::new_unsigned)
//   Stub (JSON placeholder): meteora — listed here so when real SDK is wired they work correctly.
const VERSIONED_TX_TYPES: string[] = [
  // Jupiter Trigger / Recurring API → versioned
  'limit_order',
  'cancel_limit_order',
  'cancel_all_limit_orders',
  'dca',
  'cancel_dca',
  // Jupiter Stake API → versioned swap
  'jupsol_stake',
  'jupsol_unstake',
  // Jupiter Perpetuals (JLP) → versioned
  'perp_open',
  'perp_close',
  'jlp_add',
  'jlp_remove',
  // Raydium Transaction API → versioned
  'raydium_swap',
  'raydium_add_liquidity',
  'raydium_remove_liquidity',
  'raydium_create_pool',
  'raydium_open_position',
  'raydium_close_position',
  'raydium_increase_position',
  'raydium_decrease_position',
  // Orca Whirlpool API → versioned
  'orca_swap',
  'orca_add_liquidity',
  'orca_remove_liquidity',
  'orca_open_position',
  'orca_close_position',
  'orca_increase_position',
  'orca_decrease_position',
  'orca_collect_fees',
  'orca_collect_rewards',
  // Kamino Finance API → versioned
  'kamino_deposit',
  'kamino_withdraw',
  'kamino_borrow',
  'kamino_repay',
  'kamino_add_collateral',
  'kamino_withdraw_collateral',
  'kamino_multiply_open',
  'kamino_multiply_add',
  'kamino_multiply_withdraw',
  'kamino_multiply_close',
  'kamino_long_open',
  'kamino_short_open',
  'kamino_position_close',
  'kamino_vault_deposit',
  'kamino_vault_withdraw',
  'kamino_stake',
  'kamino_unstake',
  // Jito Finance API → versioned
  'jito_stake',
  'jito_unstake',
  'jito_tip',
  // ── Tensor NFT (stub until SDK integrated)
  'tensor_buy',
  'tensor_list',
  'tensor_cancel_listing',
  'tensor_make_offer',
  'tensor_cancel_offer',
  // Meteora DLMM (stub — will use versioned tx when SDK integrated)
  'meteora_swap',
  'meteora_add_liquidity',
  'meteora_remove_liquidity',
  'meteora_create_pool',
  'meteora_open_position',
  'meteora_close_position',
  'meteora_add_to_position',
  'meteora_claim_fees',
  'meteora_claim_rewards',
  'meteora_stake',
  'meteora_unstake',
  'meteora_harvest',
];

// Actions handled locally by Angular services (not through the Rust backend)
// These build transactions on the frontend and sign+submit directly,
// OR are purely local (config stored in localStorage, no on-chain TX).
const FRONTEND_ACTION_TYPES = [
  'lend', 'withdraw_lend', 'borrow', 'repay',
  // pump.fun bonding curve trades (direct pumpportal.fun API)
  'pumpfun_buy', 'pumpfun_sell',
  // Bridge — remapped to cross_chain_swap (Relay) at execution time
  'bridge',
];

@Injectable({ providedIn: 'root' })
export class SolanaActionService {
  /** Action types that fetch data but produce no on-chain transaction. */
  static readonly DATA_ONLY_TYPES = new Set<string>(DATA_ONLY_ACTION_TYPES_LIST);

  private readonly api = inject(ApiService);
  private readonly walletService = inject(WalletService);
  private readonly tracker = inject(TransactionTrackerService);
  private readonly spendingLimit = inject(SpendingLimitService);
  private readonly riskWarning = inject(RiskWarningService);
  private readonly lendService = inject(JupiterLendService);
  private readonly pumpFunService = inject(PumpFunService);
  private readonly jitoService = inject(JitoService);

  /**
   * Idempotency guard: tracks actions currently being executed.
   * Key = "<type>:<amount>:<primaryParam>" — prevents double-submit if
   * the user clicks Confirm twice before the first TX is broadcast.
   */
  private readonly _pendingActions = new Set<string>();

  // ── MEV protection preferences (user-controlled, stored in localStorage) ──
  get heliusOptimizationEnabled(): boolean {
    return localStorage.getItem('oprai_helius_optimize') === 'true';
  }
  get jitoAutoRoutingEnabled(): boolean {
    return localStorage.getItem('oprai_jito_routing') === 'true';
  }
  setHeliusOptimization(enabled: boolean): void {
    localStorage.setItem('oprai_helius_optimize', enabled ? 'true' : 'false');
  }
  setJitoAutoRouting(enabled: boolean): void {
    localStorage.setItem('oprai_jito_routing', enabled ? 'true' : 'false');
  }

  /** Optimize tx compute units + priority fee via Helius. Falls back to original on error. */
  private async optimizeWithHelius(txBase64: string): Promise<string> {
    try {
      const result = await firstValueFrom(
        this.api.post<BuildResponse>('/actions/build', {
          type: 'helius_smart_send',
          params: { transaction: txBase64, priority_level: 'MEDIUM' },
        }).pipe(timeout(10_000))
      );
      return result.transaction ?? txBase64;
    } catch {
      return txBase64;
    }
  }

  /** Submit signed transaction via Jito block engine for MEV protection. */
  private async submitViaJito(serializedTx: Uint8Array): Promise<string> {
    const base64Tx = btoa(String.fromCharCode(...Array.from(serializedTx)));
    const body = JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'sendTransaction',
      params: [base64Tx, { encoding: 'base64', skipPreflight: false, preflightCommitment: 'confirmed' }],
    });
    const endpoints = [
      'https://ny.mainnet.block-engine.jito.labs.io/api/v1/transactions',
      'https://amsterdam.mainnet.block-engine.jito.labs.io/api/v1/transactions',
      'https://frankfurt.mainnet.block-engine.jito.labs.io/api/v1/transactions',
    ];
    for (const endpoint of endpoints) {
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
          signal: AbortSignal.timeout(8_000),
        });
        const data: any = await res.json();
        if (data?.result) return data.result as string;
        if (data?.error) throw new Error(data.error.message ?? 'Jito error');
      } catch (e: any) {
        if (endpoint === endpoints[endpoints.length - 1]) throw e;
        // else try next endpoint
      }
    }
    throw new Error('All Jito endpoints failed');
  }

  private static readonly SOL_MINT = 'So11111111111111111111111111111111111111112';
  private static readonly TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';

  /** Map simulation errors to user-readable messages. Returns a string prefixed with "sim:". */
  static parseSimulationError(err: unknown, logs: string[]): string {
    const logsStr = logs.join('\n').toLowerCase();
    const errStr = JSON.stringify(err);

    // Custom program error code (hex)
    const hexMatch = errStr.match(/"InstructionError":\s*\[\d+,\s*\{"Custom":\s*(\d+)\}\]/);
    const errorCode = hexMatch ? parseInt(hexMatch[1]) : null;

    // Token account has insufficient balance (SPL token error 0x1 = InsufficientFunds)
    if (errorCode === 1 || logsStr.includes('insufficient funds') || logsStr.includes('insufficient balance')) {
      return 'sim:insufficient_tokens';
    }

    // Jupiter-specific: 6003 = slippage exceeded
    if (errorCode === 6003) {
      return 'sim:slippage_exceeded';
    }

    // Jupiter 6024 (0x1788) = not enough input token balance
    if (errorCode === 6024) {
      return 'sim:insufficient_tokens';
    }

    // Generic "not enough tokens" from wallet simulation
    if (logsStr.includes('not enough') || logsStr.includes('insufficient')) {
      return 'sim:insufficient_tokens';
    }

    return `sim:generic:${errorCode ?? errStr.substring(0, 80)}`;
  }

  // ─── Dynamic Slippage Management ─────────────────────────────────────────────

  /** Slippage strategy based on action type and conditions */
  static readonly SLIPPAGE_STRATEGY = {
    // Safe swaps - stable pairs
    STABLE_SWAP: { base: 10, max: 50 },
    // Regular DEX swaps
    REGULAR_SWAP: { base: 50, max: 300 },
    // High volatility or low liquidity
    VOLATILE_SWAP: { base: 100, max: 500 },
    // Perp positions need more buffer
    PERP_OPEN: { base: 200, max: 1000 },
    PERP_CLOSE: { base: 100, max: 500 },
    // Lending operations
    LEND: { base: 50, max: 200 },
    BORROW: { base: 100, max: 300 },
    // Cross-chain bridges
    BRIDGE: { base: 300, max: 1000 },
    // Default for unknown types
    DEFAULT: { base: 50, max: 500 },
  } as const;

  /** Get slippage strategy for action type */
  private getSlippageStrategy(actionType: string, params: Record<string, string> = {}): { base: number; max: number } {
    const type = actionType.toLowerCase();

    // Stable swaps
    if (type === 'swap' && this.isStablePair(params)) {
      return { base: 10, max: 50 };
    }

    // Perp actions
    if (type.includes('perp_open') || type.includes('perp_')) {
      return type.includes('open')
        ? { base: 200, max: 1000 }
        : { base: 100, max: 500 };
    }

    // Lending
    if (type.includes('lend') || type.includes('borrow') || type.includes('stake')) {
      return { base: 50, max: 200 };
    }

    // Cross-chain
    if (type.includes('bridge') || type.includes('cross_chain')) {
      return { base: 300, max: 1000 };
    }

    // Volatile tokens (memecoins, new listings)
    if (this.isVolatileToken(params['outputMint'] ?? '')) {
      return { base: 100, max: 500 };
    }

    // Regular swap
    if (type === 'swap' || type.includes('swap')) {
      return { base: 50, max: 300 };
    }

    return { base: 50, max: 500 };
  }

  /** Check if swap is between stable tokens */
  private isStablePair(params: Record<string, string>): boolean {
    const STABLECOINS = new Set(['USDC', 'USDT', 'DAI', 'FRAX', 'EURC', 'USDH', 'UXD', 'USDY']);
    const input = params['inputMint']?.toUpperCase() ?? '';
    const output = params['outputMint']?.toUpperCase() ?? '';
    return STABLECOINS.has(input) && STABLECOINS.has(output);
  }

  /** Check if token is volatile (memecoin, new listing, low market cap) */
  private isVolatileToken(mint: string): boolean {
    // Memecoin detection - these typically need higher slippage
    const MEME_PATTERNS = ['pepe', 'dog', 'cat', 'frog', 'shib', 'elon', 'moon', 'safe', 'floki'];
    const addr = mint.toLowerCase();

    const KNOWN_MEMECOINS = new Set([
      'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',           // BONK
      'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm',           // WIF
      'MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5',             // MEW
      'A8C3xuqscfmyLrte3VmTqrAq8kgMASius9AFNANwpump',            // FWOG
      'ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82',            // BOME
    ]);

    return KNOWN_MEMECOINS.has(addr) || MEME_PATTERNS.some(p => addr.includes(p));
  }

  /** Calculate dynamic slippage based on multiple factors */
  calculateDynamicSlippage(
    actionType: string,
    params: Record<string, string>,
    priceImpact: number = 0,
    volume24h: number = 0
  ): number {
    const strategy = this.getSlippageStrategy(actionType, params);

    // Base slippage from strategy
    let slippage = strategy.base;

    // Adjust for price impact
    if (priceImpact > 0.1) {
      const impactMultiplier = Math.min(priceImpact / 10, 2); // Cap at 2x
      slippage = Math.max(slippage, Math.floor(strategy.base * (1 + impactMultiplier)));
    }

    // Adjust for low liquidity (volume-based)
    if (volume24h > 0 && volume24h < 10000) {
      // Very low volume - add buffer
      slippage = Math.max(slippage, Math.floor(strategy.base * 1.5));
    } else if (volume24h > 0 && volume24h < 100000) {
      // Low volume - small buffer
      slippage = Math.max(slippage, Math.floor(strategy.base * 1.2));
    }

    // Time-based adjustment (UTC hours)
    const utcHour = new Date().getUTCHours();
    const isVolatileHour = utcHour >= 14 && utcHour <= 18; // US market open
    if (isVolatileHour && !this.isStablePair(params)) {
      slippage = Math.max(slippage, Math.floor(slippage * 1.25));
    }

    // Cap at max
    return Math.min(slippage, strategy.max);
  }

  /** Suggest slippage BPS based on price impact percentage (legacy) */
  static suggestSlippage(priceImpactPct: number): number {
    if (priceImpactPct < 0.1) return 50;
    if (priceImpactPct < 0.5) return 100;
    if (priceImpactPct < 1.0) return 200;
    if (priceImpactPct < 3.0) return 300;
    return 500;
  }

  /** Adjust swap action slippage if price impact warrants it */
  private adjustSlippageForImpact(action: ParsedAction, quoteResponse: QuoteResponse): ParsedAction {
    const impact = parseFloat(quoteResponse.priceImpact) || 0;

    // Use dynamic slippage calculation
    const suggested = this.calculateDynamicSlippage(
      action.type,
      action.params,
      impact
    );

    const current = parseInt(action.params['slippageBps'] ?? '50', 10);

    // Only increase slippage, never decrease (safety first)
    if (suggested > current) {
      return { ...action, params: { ...action.params, slippageBps: suggested.toString() } };
    }

    return action;
  }

  /** Get user-friendly slippage recommendation message */
  getSlippageRecommendation(actionType: string, params: Record<string, string>): string {
    const strategy = this.getSlippageStrategy(actionType, params);
    const minBps = strategy.base;
    const maxBps = strategy.max;
    const type = actionType.toLowerCase();

    if (this.isStablePair(params)) {
      return `Recommended: ${minBps}-${maxBps} bps (stable pair)`;
    }
    if (type.includes('bridge') || type.includes('cross_chain')) {
      return `Recommended: ${minBps}-${maxBps} bps (cross-chain may take time)`;
    }
    if (this.isVolatileToken(params['outputMint'] ?? '')) {
      return `Recommended: ${minBps}-${maxBps} bps (volatile token - high slippage advised)`;
    }
    if (type.includes('perp_open')) {
      return `Recommended: ${minBps}-${maxBps} bps (perpetual position)`;
    }

    return `Recommended: ${minBps}-${maxBps} bps`;
  }

  /**
   * Known output → input mappings for chained actions.
   * When action B chains from action A, B's input is derived from A's output.
   */
  private readonly CHAIN_OUTPUTS: Record<string, { token: string; symbol: string }> = {
    // Stake outputs → can be used as input for chained stake actions
    // Addresses verified against on-chain program registries.
    'jupsol_stake':    { token: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v', symbol: 'JupSOL'  },
    'jito_stake':      { token: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kongC', symbol: 'jitoSOL' },
    'marinade_stake':  { token: 'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So', symbol: 'mSOL'    },
  };

  /**
   * Execute a chain of actions sequentially.
   * Each action's output can feed into the next action's input if chainFromPrevious=true.
   *
   * Example: "Swap SOL to USDC and stake on Jito"
   * - Action 1: swap (SOL → jitoSOL)
   * - Action 2: jito_stake with chainFromPrevious=true
   */
  async executeChain(
    actions: ParsedAction[],
    callbacks: ActionCallbacks = {}
  ): Promise<string[]> {
    if (actions.length === 0) return [];
    if (actions.length === 1) {
      const result = await this.execute(actions[0], callbacks);
      return [result];
    }

    const results: string[] = [];
    let lastOutputMint: string | null = null;

    for (let i = 0; i < actions.length; i++) {
      let actionToRun = actions[i];

      // Chain from previous action's output
      if (actionToRun.chainFromPrevious && lastOutputMint) {
        // Override input token with previous action's output
        actionToRun = {
          ...actionToRun,
          params: {
            ...actionToRun.params,
            inputMint: lastOutputMint,
            // For stake, amount is usually "all" or the full amount from swap
            amount: actionToRun.params['amount'] ?? 'all',
          },
        };
      }

      // Show progress
      if (callbacks.onStatus) {
        callbacks.onStatus(`Executing ${i + 1}/${actions.length}: ${actionToRun.type}`);
      }

      try {
        const result = await this.execute(actionToRun, callbacks);
        results.push(result);

        // Track output for chaining
        const outputInfo = this.CHAIN_OUTPUTS[actionToRun.type];
        if (outputInfo) {
          lastOutputMint = outputInfo.token;
        }

        // If this action produces a token (like stake → LST), track it
        if (actionToRun.type.includes('stake') && !actionToRun.type.includes('unstake')) {
          const mint = this.getStakeOutputMint(actionToRun.type);
          if (mint) lastOutputMint = mint;
        }

      } catch (error: any) {
        if (callbacks.onStatus) {
          callbacks.onStatus(`Step ${i + 1} failed: ${error.message}`);
        }
        // Continue to next action or stop? Let's stop on failure for safety
        throw error;
      }
    }

    return results;
  }

  /** Get the output mint for stake actions */
  private getStakeOutputMint(actionType: string): string | null {
    // Mirror of CHAIN_OUTPUTS — keep in sync.
    return this.CHAIN_OUTPUTS[actionType]?.token ?? null;
  }

  /**
   * Resolve the wallet's full balance for a given mint.
   * Used when `amount=all` / `amount=max` is specified.
   */
  async getTokenBalance(mint: string): Promise<number> {
    const wallet = this.walletService.publicKey();
    if (!wallet) return 0;
    const { Connection, PublicKey } = await import('@solana/web3.js');
    const connection = new Connection(environment.solanaRpc, 'confirmed');
    const isSol = !mint || mint === 'SOL' || mint === SolanaActionService.SOL_MINT;
    if (isSol) {
      return (await connection.getBalance(new PublicKey(wallet))) / 1e9;
    }
    const walletPk = new PublicKey(wallet);
    // Query both token programs in parallel (standard SPL + Token-2022)
    const TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';
    const [r1, r2] = await Promise.all([
      connection.getParsedTokenAccountsByOwner(walletPk, { programId: new PublicKey(SolanaActionService.TOKEN_PROGRAM_ID) }),
      connection.getParsedTokenAccountsByOwner(walletPk, { programId: new PublicKey(TOKEN_2022) }),
    ]);
    for (const account of [...r1.value, ...r2.value]) {
      const info = (account.account.data as any).parsed?.info;
      if (info?.mint === mint) {
        return info.tokenAmount?.uiAmount ?? 0;
      }
    }
    return 0;
  }

  /**
   * Get dynamic priority fee based on action type and urgency.
   * Uses real-time Jito tip floor data.
   */
  async getDynamicPriorityFee(actionType: string, urgency: 'low' | 'medium' | 'high' = 'medium'): Promise<number> {
    // Base fees for different action types
    const BASE_FEES: Record<string, number> = {
      'transfer': 0.000005,
      'swap': 0.00001,
      'jupsol_stake': 0.00001,
      'jito_stake': 0.00001,
      'marinade_stake': 0.00001,
      'perp_open': 0.00002,
      'perp_close': 0.00002,
      'dca': 0.00002,
      'limit_order': 0.000015,
      'cancel_limit_order': 0.00001,
      'cancel_all_limit_orders': 0.00002,
      'pumpfun_buy': 0.0005,  // Higher for sniping
    };

    const baseFee = BASE_FEES[actionType] ?? 0.00001;

    // Get real-time Jito tip
    let jitoTip = 0.0005; // Default
    try {
      jitoTip = await this.jitoService.getSuggestedTip(urgency);
    } catch (e) {
      // Use default if Jito fails
    }

    // Apply urgency multiplier
    const urgencyMultiplier = urgency === 'high' ? 2 : urgency === 'low' ? 0.5 : 1;

    return baseFee + (jitoTip * urgencyMultiplier);
  }

  /**
   * Execute a Solana action through the full pipeline.
   *
   * Backend actions (via Rust service):
   *   swap, transfer, stake, unstake, jupsol_stake, jupsol_unstake,
   *   limit_order, cancel_limit_order, dca, cancel_dca,
   *   launch_token, cross_chain_swap
   *
   * Frontend actions (Angular services, no Rust backend involved):
   *   lend, withdraw_lend, borrow, repay
   */
  async execute(
    action: ParsedAction,
    callbacks: ActionCallbacks = {}
  ): Promise<string> {
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      throw new Error('No wallet connected');
    }

    // ── Guard: wallet param in action must not be a different address ──────
    // The action's wallet/to field is resolved from WalletService, not from
    // LLM output. If somehow an LLM-injected wallet address slipped through
    // that doesn't match the signed-in user, reject it here.
    const actionWallet = action.params['wallet'];
    if (actionWallet && actionWallet !== 'self' && actionWallet !== wallet) {
      throw new Error('Action wallet address does not match your connected wallet.');
    }

    // ── Guard: slippage must be within safe range (0–2000 bps = 0–20%) ────
    const slippageRaw = action.params['slippageBps'];
    if (slippageRaw !== undefined) {
      const slippageBps = parseInt(slippageRaw, 10);
      if (isNaN(slippageBps) || slippageBps < 0 || slippageBps > 5000) {
        throw new Error(`Invalid slippage: ${slippageRaw} bps. Must be 0–5000 (0%–50%).`);
      }
    }

    // ── Idempotency guard: block duplicate submits ─────────────────────────
    // Key includes all discriminating params so two swap-100-SOL and swap-100-BONK
    // do NOT collide — only identical actions are blocked.
    const actionKey = [
      action.type,
      action.params['amount'] ?? '',
      action.params['inputMint'] ?? action.params['mint'] ?? '',
      action.params['outputMint'] ?? '',
      action.params['to'] ?? '',
      action.params['token'] ?? '',
    ].join(':');
    if (this._pendingActions.has(actionKey)) {
      throw new Error('This action is already being processed. Please wait.');
    }
    this._pendingActions.add(actionKey);
    const releasePending = () => this._pendingActions.delete(actionKey);

    // ── Remap generic lend/borrow/repay/withdraw_lend to protocol-specific types ──
    // When the AI generates [ACTION:lend] protocol=marginfi, route to marginfi_deposit, etc.
    // Only protocol=jupiter (or unspecified) stays on the frontend Jupiter Lend path.
    action = this.remapLendingAction(action);

    // ── Remap nft_buy → tensor_buy or me_buy based on marketplace ──
    if (action.type === 'nft_buy') {
      const marketplace = (action.params['marketplace'] ?? 'tensor').toLowerCase();
      if (marketplace === 'magic-eden' || marketplace === 'magiceden' || marketplace === 'me') {
        action = { ...action, type: 'me_buy' };
      } else {
        action = { ...action, type: 'tensor_buy' };
      }
    }

    // ── Remap nft_list → tensor_list or me_list based on marketplace ──
    if (action.type === 'nft_list') {
      const marketplace = (action.params['marketplace'] ?? 'tensor').toLowerCase();
      if (marketplace === 'magic-eden' || marketplace === 'magiceden' || marketplace === 'me') {
        action = { ...action, type: 'me_list' };
      } else {
        action = { ...action, type: 'tensor_list' };
      }
    }

    // ── nft_mint: Remap to launch_token for token launches ──
    // For NFT minting, we'd need Candy Machine integration - for now map to launch_token
    if (action.type === 'nft_mint') {
      action = {
        ...action,
        type: 'launch_token',
        params: {
          name: action.params['name'] ?? action.params['collectionName'] ?? '',
          symbol: action.params['symbol'] ?? '',
          description: action.params['description'] ?? action.params['collectionDescription'] ?? '',
          imageUrl: action.params['imageUrl'] ?? action.params['image'] ?? '',
        },
      };
    }

    // ── Remap bridge → appropriate cross-chain action ──
    // bridge uses human-readable chain names; downstream actions use numeric chain IDs.
    // Provider: relay (default), wormhole, debridge, mayan → cross_chain_swap (Relay)
    //           squid → squid_bridge (Squid Router v2, direct Rust handler)
    if (action.type === 'bridge') {
      // Shared chain name → numeric ID map (Relay IDs for relay/wormhole/mayan, Squid for squid)
      const chainIdMap: Record<string, string> = {
        ethereum: '1', eth: '1',
        bsc: '56', bnb: '56',
        polygon: '137', matic: '137',
        arbitrum: '42161', arb: '42161',
        optimism: '10', op: '10',
        base: '8453',
        solana: '900', sol: '900',
        avalanche: '43114', avax: '43114',
        fantom: '250', ftm: '250',
        celo: '42220',
        moonbeam: '1284',
        aurora: '1313161554',
        klaytn: '8217',
        arbitrum_nova: '42170',
        polygon_zkevm: '1101',
        zkevm: '1101',
        linea: '59144',
        scroll: '534352',
        base_sepolia: '84532',
        sepolia: '11155111',
        avalanche_fuji: '43113',
      };
      const fromChain = (action.params['fromChain'] ?? '').toLowerCase();
      const toChain   = (action.params['toChain']   ?? '').toLowerCase();
      const token     = action.params['token'] ?? '';
      const provider  = (action.params['provider'] ?? 'relay').toLowerCase();

      if (provider === 'squid') {
        // Squid uses its own Solana chain ID (7565164) and routes directly to squid Rust handler.
        const squidChainMap: Record<string, string> = {
          ...chainIdMap,
          solana: '7565164', sol: '7565164',
        };
        const resolveSquidChain = (chain: string): string => squidChainMap[chain] ?? chain;

        const squidFromAddress = this.walletService.publicKey()?.toString() ?? '';
        action = {
          ...action,
          type: 'squid_bridge',
          params: {
            originChainId:      resolveSquidChain(fromChain),
            destinationChainId: resolveSquidChain(toChain),
            originToken:        action.params['originToken'] ?? action.params['fromToken'] ?? token,
            destinationToken:   action.params['destinationToken'] ?? action.params['toToken'] ?? token,
            amount:             action.params['amount'] ?? '0',
            fromAddress:        action.params['fromAddress'] ?? squidFromAddress,
            // Carry through all optional Squid params if provided
            ...(action.params['recipient']               && { recipient: action.params['recipient'] }),
            ...(action.params['slippage'] != null        && { slippage: action.params['slippage'] }),
            ...(action.params['enableExpress'] != null   && { enableExpress: action.params['enableExpress'] }),
            ...(action.params['receiveGasOnDestination'] != null && { receiveGasOnDestination: action.params['receiveGasOnDestination'] }),
            ...(action.params['prefer']                  && { prefer: action.params['prefer'] }),
            ...(action.params['bypass']                  && { bypass: action.params['bypass'] }),
            ...(action.params['enableBoost'] != null     && { enableBoost: action.params['enableBoost'] }),
            ...(action.params['collectFees']             && { collectFees: action.params['collectFees'] }),
            ...(action.params['postHook']                && { postHook: action.params['postHook'] }),
          },
        };
      } else {
        // Relay / Wormhole / DeBridge / Mayan → cross_chain_swap
        const providerMap: Record<string, string> = {
          relay: 'relay',
          wormhole: 'wormhole',
          debridge: 'debridge',
          mayan: 'mayan',
        };

        // DeBridge uses its own Solana chain ID (7565164), not Relay's (900)
        const debridgeSolanaId = '7565164';
        const resolveChainId = (chain: string): string => {
          const relayId = chainIdMap[chain] ?? chain;
          if (provider === 'debridge' && relayId === '900') return debridgeSolanaId;
          return relayId;
        };

        action = {
          ...action,
          type: 'cross_chain_swap',
          params: {
            originChainId:       resolveChainId(fromChain),
            destinationChainId:  resolveChainId(toChain),
            originCurrency:      token,
            destinationCurrency: token,
            amount: action.params['amount'] ?? '0',
            provider: providerMap[provider] ?? 'relay',
            slippageBps: action.params['slippageBps'] ?? '50',
          },
        };
      }
    }

    // ── Pre-flight: spending limit + risk warning ──────────────────────────
    const amountUsd = this.estimateAmountUsd(action);

    try {
    // 1. Spending limit check (hard block)
    const limitCheck = this.spendingLimit.check(amountUsd);
    if (!limitCheck.allowed) {
      const reason = limitCheck.reason === 'per_tx'
        ? `Transaction exceeds your per-transaction limit of $${limitCheck.limitUsd?.toFixed(0)}.`
        : `Daily spending limit of $${limitCheck.limitUsd?.toFixed(0)} reached (spent today: $${limitCheck.currentDailyUsd?.toFixed(0)}).`;
      throw new Error(reason);
    }

    // 1b. Slippage validation (warning)
    const userSlippage = parseInt(action.params['slippageBps'] ?? '50', 10);
    const recommendedSlippage = this.calculateDynamicSlippage(action.type, action.params);
    if (userSlippage < recommendedSlippage) {
      console.warn(
        `[Slippage] Low slippage detected: user=${userSlippage}bps, recommended=${recommendedSlippage}bps. ` +
        `Action: ${action.type}. ${this.getSlippageRecommendation(action.type, action.params)}`
      );
      // Could emit a warning to the UI here if needed
    }

    // 2. Risk warning (user must confirm)
    const confirmed = await this.riskWarning.confirm(action, amountUsd);
    if (!confirmed) {
      throw new Error('cancelled_by_user');
    }

    // ── Frontend-handled actions ─────────────────────────────────────────
    if (FRONTEND_ACTION_TYPES.includes(action.type)) {
      return this.executeFrontendAction(action, callbacks);
    }

    // ── Resolve amount=all / amount=max before building ──────────────────
    const rawAmount = action.params['amount'];
    if (rawAmount === 'all' || rawAmount === 'max') {
      const mint = action.params['inputMint'] ?? action.params['mint'] ?? action.params['token'] ?? '';
      const resolved = await this.getTokenBalance(mint);
      action = { ...action, params: { ...action.params, amount: resolved.toString() } };
    }

    // ── Backend-handled actions (Rust service) ───────────────────────────
    const isSwap = SWAP_ACTION_TYPES.includes(action.type);

    // Step 1: Get quote (swap only)
    let quoteId: string | undefined;
    if (isSwap) {
      callbacks.onQuote?.();
      try {
        const quote = await firstValueFrom(
          this.api.post<QuoteResponse>('/actions/quote', {
            input_mint: action.params['inputMint'],
            output_mint: action.params['outputMint'],
            amount: action.params['amount'],
            slippage_bps: action.params['slippageBps'] ?? 50,
            ...(action.params['swapMode']                ? { swap_mode: action.params['swapMode'] } : {}),
            ...(action.params['onlyDirectRoutes'] != null ? { only_direct_routes: action.params['onlyDirectRoutes'] === 'true' } : {}),
            ...(action.params['restrictIntermediateTokens'] != null ? { restrict_intermediate_tokens: action.params['restrictIntermediateTokens'] === 'true' } : {}),
          }).pipe(timeout(30_000))
        );
        quoteId = quote.quoteId;
        // Dynamically raise slippage if price impact is significant
        action = this.adjustSlippageForImpact(action, quote);
      } catch (err) {
        if (err instanceof TimeoutError) {
          throw new Error('Quote request timed out. Check your connection and try again.');
        }
        throw err;
      }
    }

    // Step 2: Build transaction via Rust backend
    const buildBody: Record<string, unknown> = {
      type: action.type,
      params: this.normalizeParams(action),
    };
    if (quoteId) {
      buildBody['quote_id'] = quoteId;
    }

    let buildResult: BuildResponse;
    try {
      buildResult = await firstValueFrom(
        this.api.post<BuildResponse>('/actions/build', buildBody).pipe(timeout(30_000))
      );
    } catch (err) {
      if (err instanceof TimeoutError) {
        throw new Error('Build request timed out. Check your connection and try again.');
      }
      throw err;
    }

    // Step 3a: Cross-chain swap — sign EVM transaction via window.ethereum
    if (buildResult.isCrossChain || action.type === 'cross_chain_swap') {
      const provider = action.params['provider'] ?? 'relay';
      const steps = buildResult.executionSteps ?? [];

      // For relay: use execution steps (existing behavior)
      // For wormhole/debridge: quote contains tx_data directly
      let txData: any = null;

      if (provider === 'relay' && steps.length > 0) {
        // Relay uses execution steps
        const depositStep = steps.find(s => s.type === 'deposit' || s.items?.length);
        txData = depositStep?.items?.[0]?.transaction;
      } else if (provider === 'wormhole' || provider === 'debridge') {
        // Wormhole/Debridge: tx_data is in quote
        const quote = buildResult.quote;
        if (quote && typeof quote === 'object') {
          txData = (quote as any).txData || (quote as any).tx_data;
        }
      } else {
        // Fallback to relay behavior
        const depositStep = steps.find(s => s.type === 'deposit' || s.items?.length);
        txData = depositStep?.items?.[0]?.transaction;
      }

      if (!txData?.to) {
        throw new Error(`Cross-chain (${provider}): no transaction data returned from backend`);
      }
      const ethereum = (window as any).ethereum;
      if (!ethereum) {
        throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to execute cross-chain swaps.');
      }

      // Wrap all ethereum.request calls with a 120-second timeout to prevent infinite hangs
      const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> => {
        const timer = new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error(`Cross-chain (${provider}): ${label} timed out after ${ms / 1000}s`)), ms)
        );
        return Promise.race([promise, timer]);
      };

      const accounts: string[] = await withTimeout(
        ethereum.request({ method: 'eth_requestAccounts' }),
        30_000,
        'wallet connect'
      );
      const evmAccount = accounts?.[0];
      if (!evmAccount) throw new Error('No EVM account available');
      callbacks.onSign?.();
      const evmTxHash = await withTimeout(
        ethereum.request({
          method: 'eth_sendTransaction',
          params: [{
            from: evmAccount,
            to: txData.to,
            data: txData.data ?? '0x',
            value: txData.value ?? '0x0',
            ...(txData.gasLimit ? { gas: txData.gasLimit } : {}),
          }],
        }),
        120_000,
        'transaction sign'
      );
      callbacks.onSubmit?.(evmTxHash as string);
      callbacks.onConfirm?.();
      return evmTxHash as string;
    }

    // Step 3b: Data-only actions (no transaction to sign — backend returns null transaction)
    if (SolanaActionService.DATA_ONLY_TYPES.has(action.type) || !buildResult.transaction) {
      const desc = buildResult.preview?.description ?? `${action.type} completed`;
      callbacks.onConfirm?.(desc);
      return desc;
    }

    // Step 3: Optional Helius CU/fee optimization (silent, fallback-safe)
    if (this.heliusOptimizationEnabled && buildResult.transaction) {
      buildResult = { ...buildResult, transaction: await this.optimizeWithHelius(buildResult.transaction) };
    }

    // Step 3b: Deserialize tx + precise fee check — must happen before wallet dialog
    const web3 = await import('@solana/web3.js');
    const connection = new web3.Connection(environment.solanaRpc, 'confirmed');

    const txBuffer = this.base64ToUint8Array(buildResult.transaction!);
    const isVersioned = isSwap || VERSIONED_TX_TYPES.includes(action.type);

    // Deserialize here so we can inspect the fee before asking the wallet to sign
    let deserializedTx: VersionedTransaction | Transaction;
    if (isVersioned) {
      deserializedTx = web3.VersionedTransaction.deserialize(txBuffer);
    } else {
      deserializedTx = web3.Transaction.from(txBuffer);
    }

    // Get the wallet's SOL balance and the exact tx fee side-by-side
    const [solBalance, feeResponse] = await Promise.all([
      connection.getBalance(new web3.PublicKey(wallet)),
      isVersioned
        ? connection.getFeeForMessage((deserializedTx as VersionedTransaction).message).catch(() => ({ value: null }))
        : connection.getFeeForMessage(
            (deserializedTx as Transaction).compileMessage()
          ).catch(() => ({ value: null })),
    ]);

    // Determine the fee: use the exact fee from the RPC when available, otherwise a safe floor
    const estimatedFee = (feeResponse as { value: number | null }).value ?? 200_000;

    if (solBalance < estimatedFee) {
      const needSol = (estimatedFee / 1e9).toFixed(6);
      const haveSol = (solBalance / 1e9).toFixed(6);
      throw new Error(`insufficient_fee:need=${needSol},have=${haveSol}`);
    }

    // Pre-flight simulation — same path for versioned and legacy TX
    try {
      let sim: Awaited<ReturnType<typeof connection.simulateTransaction>>;

      if (isVersioned) {
        sim = await connection.simulateTransaction(
          deserializedTx as VersionedTransaction,
          { sigVerify: false, replaceRecentBlockhash: true }
        );
      } else {
        // Legacy Transaction — send as Message (sigVerify not supported)
        const legacyTx = deserializedTx as Transaction;
        legacyTx.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
        sim = await connection.simulateTransaction(legacyTx.compileMessage());
      }

      if (sim.value.err) {
        throw new Error(SolanaActionService.parseSimulationError(sim.value.err, sim.value.logs ?? []));
      }
    } catch (simErr: any) {
      // Only re-throw errors we intentionally created
      if (simErr.message?.startsWith('sim:')) throw simErr;
      // Ignore RPC/network errors — don't block the user
    }

    callbacks.onSign?.();

    // Wallet sign with a 2-minute timeout — prevents a hung wallet dialog from
    // leaving the action permanently in "signing" state.
    const SIGN_TIMEOUT_MS = 120_000;
    const signedTx = (await Promise.race([
      this.walletService.signTransaction(deserializedTx),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), SIGN_TIMEOUT_MS)
      ),
    ])) as { serialize(): Uint8Array };

    // Step 4: Submit — via Jito block engine (MEV protection) or standard RPC
    let signature: string;
    if (this.jitoAutoRoutingEnabled) {
      try {
        signature = await this.submitViaJito(signedTx.serialize());
      } catch {
        // Jito failed — fall back to standard RPC silently
        signature = await connection.sendRawTransaction(signedTx.serialize(), {
          skipPreflight: false,
          preflightCommitment: 'confirmed',
        });
      }
    } else {
      signature = await connection.sendRawTransaction(signedTx.serialize(), {
        skipPreflight: false,
        preflightCommitment: 'confirmed',
      });
    }

    callbacks.onSubmit?.(signature);

    // Record spend on successful submission
    if (amountUsd > 0) {
      this.spendingLimit.record(amountUsd);
    }

    // For cancel_all_limit_orders: sign and submit remaining transactions sequentially
    const remainingTxs: Array<{ transaction: string; description: string }> =
      (buildResult as any).executionSteps?.remaining_transactions ?? [];
    if (action.type === 'cancel_all_limit_orders' && remainingTxs.length > 0) {
      for (const remaining of remainingTxs) {
        try {
          const buf = this.base64ToUint8Array(remaining.transaction);
          const remainingTx = web3.VersionedTransaction.deserialize(buf);
          const signedRemaining = (await Promise.race([
            this.walletService.signTransaction(remainingTx),
            new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Sign timeout')), 60_000)),
          ])) as { serialize(): Uint8Array };
          const sig = await connection.sendRawTransaction(signedRemaining.serialize(), {
            skipPreflight: false,
            preflightCommitment: 'confirmed',
          });
          this.tracker.track(sig, 'cancel_limit_order', {}).catch(() => {});
        } catch (e) {
          // Log but continue — partial cancel is still useful
          console.warn('[cancel_all] Failed to cancel one order:', e);
        }
      }
    }

    // Step 5: Track via TransactionTracker (retry + DB sync)
    this.tracker
      .track(signature, action.type, {
        protocol: action.params['protocol'],
        params: action.params as Record<string, unknown>,
      })
      .then(txId => {
        // Call onConfirm callback when tracker confirms
        const sub = this.tracker.transactions$.subscribe(map => {
          const tx = map.get(txId);
          if (tx?.status === 'confirmed') {
            callbacks.onConfirm?.();
            sub.unsubscribe();
          } else if (tx?.status === 'failed') {
            sub.unsubscribe();
          }
        });
      })
      .catch(() => {
        // If tracker fails, at least call the callback
        callbacks.onConfirm?.();
      });

    return signature;
    } finally {
      releasePending();
    }
  }

  // ─── Lending Protocol Remap ───────────────────────────────────────────────

  /**
   * Remap generic lend/borrow/repay/withdraw_lend actions to their
   * protocol-specific counterparts based on the `protocol` param.
   * Only protocol=jupiter (or absent) stays on the frontend Jupiter Lend path.
   */
  /**
   * Best-effort USD amount estimate for spending limits and risk warnings.
   * Uses the 'amount' param; returns 0 when amount is unknown or non-numeric.
   * For data-only actions returns 0 (they have no on-chain value transfer).
   */
  private estimateAmountUsd(action: ParsedAction): number {
    if (SolanaActionService.DATA_ONLY_TYPES.has(action.type)) return 0;
    const raw = action.params['amount'] ?? action.params['amountUsd'] ?? action.params['collateral'] ?? '0';
    const n = parseFloat(String(raw));
    if (!isFinite(n) || n <= 0) return 0;
    // If the token is SOL, apply a rough ~$150 multiplier for limit purposes.
    // Real USD value is only known after getting a quote; this is a conservative gate.
    // Pump.fun buy / PumpSwap buy — amount is in SOL when denominatedInSol != false
    if ((action.type === 'pumpfun_buy' || action.type === 'pumpswap_buy') &&
        action.params['denominatedInSol'] !== 'false') {
      return n * 150;
    }
    const token = (action.params['token'] ?? action.params['inputMint'] ?? '').toUpperCase();
    if (token === 'SOL' || token === 'So11111111111111111111111111111111111111112') {
      return n * 150; // rough SOL price floor for limit checking
    }
    // USDC/USDT/USD* tokens → 1:1
    if (/^USD|USDC|USDT|DAI/i.test(token)) return n;
    // Unknown token → assume non-trivial only if amount is large-ish
    return n;
  }

  private remapLendingAction(action: ParsedAction): ParsedAction {
    const protocol = (action.params['protocol'] ?? 'jupiter').toLowerCase();
    if (protocol === 'jupiter') return action;

    // Map: (generic action type, protocol) → backend action type
    const REMAP: Record<string, Record<string, string>> = {
      lend: {
        marginfi: 'marginfi_deposit',
        kamino:   'kamino_deposit',
      },
      withdraw_lend: {
        marginfi: 'marginfi_withdraw',
        kamino:   'kamino_withdraw',
      },
      borrow: {
        marginfi: 'marginfi_borrow',
        kamino:   'kamino_borrow',
      },
      repay: {
        marginfi: 'marginfi_repay',
        kamino:   'kamino_repay',
      },
    };

    const remappedType = REMAP[action.type]?.[protocol];
    if (!remappedType) return action;

    // Normalize params: marginfi uses `bank`, kamino uses `token`, solend uses `asset`
    const token = action.params['token'] ?? action.params['asset'] ?? action.params['bank'] ?? action.params['reserve'];
    const extraParams: Record<string, string> = {};
    if (protocol === 'marginfi' && token) extraParams['bank'] = token;
    if (protocol === 'kamino' && token) extraParams['token'] = token;
    if (protocol === 'solend' && token) extraParams['asset'] = token;

    return {
      ...action,
      type: remappedType,
      params: { ...action.params, ...extraParams },
    };
  }

  // ─── Frontend Action Dispatcher ────────────────────────────────────────────

  private async executeFrontendAction(
    action: ParsedAction,
    callbacks: ActionCallbacks
  ): Promise<string> {
    let transaction: Transaction;

    switch (action.type) {
      // ── Jupiter Lend: Earn ──────────────────────────────────────────────
      case 'lend': {
        const asset = this.lendService.findAsset(action.params['token'] ?? 'USDC');
        if (!asset) throw new Error(`Unsupported lend asset: ${action.params['token']}`);
        const result = await this.lendService.buildDepositTransaction(
          asset,
          parseFloat(action.params['amount'] ?? '0')
        );
        transaction = result.transaction;
        break;
      }
      case 'withdraw_lend': {
        const asset = this.lendService.findAsset(action.params['token'] ?? 'USDC');
        if (!asset) throw new Error(`Unsupported lend asset: ${action.params['token']}`);
        const result = await this.lendService.buildWithdrawTransaction(
          asset,
          parseFloat(action.params['amount'] ?? '0')
        );
        transaction = result.transaction;
        break;
      }

      // ── Jupiter Lend: Borrow / Repay ────────────────────────────────────
      case 'borrow':
      case 'repay': {
        const colAsset = this.lendService.findAsset(action.params['collateral'] ?? 'SOL') ??
          { symbol: 'SOL', mint: 'So11111111111111111111111111111111111111112', decimals: 9 };
        const debtAsset = this.lendService.findAsset(action.params['token'] ?? 'USDC') ??
          { symbol: 'USDC', mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', decimals: 6 };
        const rawAmount = parseFloat(action.params['amount'] ?? '0');
        const debtDelta = action.type === 'borrow' ? rawAmount : -rawAmount;
        const result = await this.lendService.buildBorrowOperateTransaction(
          parseInt(action.params['vaultId'] ?? '-1'),  // -1 = auto-select best vault
          parseInt(action.params['positionId'] ?? '0'),
          parseFloat(action.params['collateralAmount'] ?? '0'),
          debtDelta,
          colAsset,
          debtAsset
        );
        transaction = result.transaction;
        break;
      }

      // ── pump.fun Bonding Curve Buy / Sell ────────────────────────────────
      case 'pumpfun_buy':
      case 'pumpfun_sell': {
        const wallet = this.walletService.publicKey();
        if (!wallet) throw new Error('No wallet connected');

        const mint = (action.params['mint'] ?? '').trim();
        if (!mint || !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) {
          throw new Error('Invalid or missing mint address. Please provide a valid Solana token address.');
        }
        const amount = parseFloat(action.params['amount'] ?? '0');
        if (!isFinite(amount) || amount <= 0) {
          throw new Error('Invalid amount. Please specify a positive number.');
        }
        const denominatedInSol = action.type === 'pumpfun_buy'
          ? action.params['denominatedInSol'] !== 'false'  // buy: default true (SOL-denominated)
          : action.params['denominatedInSol'] === 'true';  // sell: default false (token-denominated)
        const slippage = action.params['slippage'] ? parseFloat(action.params['slippage']) : 10;
        const priorityFee = action.params['priorityFee'] ? parseFloat(action.params['priorityFee']) : 0.0005;

        // Check if token has graduated
        const tokenInfo = await this.pumpFunService.getToken(mint).catch(() => null);
        if (tokenInfo?.complete) {
          const isBuy = action.type === 'pumpfun_buy';
          if (tokenInfo.raydium_pool) {
            // Old graduation path — token is on Raydium, route via Jupiter
            callbacks.onStatus?.(`Token $${tokenInfo.symbol} is on Raydium. Routing through Jupiter...`);
            const solMint = 'So11111111111111111111111111111111111111112';
            const inputMint = isBuy ? solMint : mint;
            const outputMint = isBuy ? mint : solMint;
            const swapAmount = isBuy
              ? Math.round(amount * 1e9) // SOL → lamports
              : Math.round(amount);      // token base units
            // Step 1: get quote
            const quoteResp = await firstValueFrom(
              this.api.post<{ quoteId: string }>('/actions/quote', {
                input_mint: inputMint,
                output_mint: outputMint,
                amount: String(swapAmount),
                slippage_bps: Math.round(slippage * 100),
              }).pipe(timeout(30_000))
            );
            // Step 2: build swap transaction via backend
            const buildResp = await firstValueFrom(
              this.api.post<BuildResponse>('/actions/build', {
                type: 'swap',
                params: { inputMint, outputMint, amount: String(swapAmount), slippageBps: Math.round(slippage * 100) },
                ...(quoteResp?.quoteId ? { quote_id: quoteResp.quoteId } : {}),
              }).pipe(timeout(30_000))
            );
            if (!buildResp?.transaction) throw new Error(`Failed to build Jupiter swap for graduated token ${tokenInfo.symbol}`);
            const { VersionedTransaction, Connection } = await import('@solana/web3.js');
            const txBytes = this.base64ToUint8Array(buildResp.transaction);
            const vtx = VersionedTransaction.deserialize(txBytes);
            callbacks.onSign?.();
            const signedVtx = await this.walletService.signTransaction(vtx) as InstanceType<typeof VersionedTransaction>;
            const rpcConn = new Connection(environment.solanaRpc, 'confirmed');
            const sig = await rpcConn.sendRawTransaction(signedVtx.serialize(), { skipPreflight: false, preflightCommitment: 'confirmed' });
            callbacks.onSubmit?.(sig);
            this.tracker
              .track(sig, action.type, { protocol: action.params['protocol'] })
              .then(txId => {
                const sub = this.tracker.transactions$.subscribe(map => {
                  const tx = map.get(txId);
                  if (tx?.status === 'confirmed') { callbacks.onConfirm?.(); sub.unsubscribe(); }
                  else if (tx?.status === 'failed') { sub.unsubscribe(); }
                });
              })
              .catch(() => callbacks.onConfirm?.());
            return sig;
          } else {
            // New graduation path — token is on PumpSwap AMM, route via PumpPortal pool: "pump-amm"
            callbacks.onStatus?.(`Token $${tokenInfo.symbol} has graduated to PumpSwap AMM. Routing through PumpSwap...`);
            const pumpswapResp = await this.pumpFunService.buildPumpSwap(
              wallet, mint, amount, denominatedInSol, slippage, priorityFee, isBuy ? 'buy' : 'sell'
            );
            if (!pumpswapResp?.transaction) throw new Error(`Failed to build PumpSwap transaction for ${tokenInfo.symbol}`);
            const { Transaction } = await import('@solana/web3.js');
            const txBytes = Buffer.from(pumpswapResp.transaction, 'base64');
            transaction = Transaction.from(txBytes);
          }
          break;
        }

        // Token still on bonding curve — use backend build endpoint
        const resp = action.type === 'pumpfun_buy'
          ? await this.pumpFunService.buildBuy(wallet, mint, amount, denominatedInSol, slippage, priorityFee)
          : await this.pumpFunService.buildSell(wallet, mint, amount, denominatedInSol, slippage, priorityFee);

        if (!resp?.transaction) throw new Error('Failed to build pump.fun transaction');
        const { Transaction } = await import('@solana/web3.js');
        const txBytes = Buffer.from(resp.transaction, 'base64');
        transaction = Transaction.from(txBytes);
        break;
      }

      default:
        throw new Error(`Unknown frontend action type: ${action.type}`);
    }

    // Pre-flight simulation (frontend actions — legacy TX)
    try {
      const { Connection } = await import('@solana/web3.js');
      const feCon = new Connection(environment.solanaRpc, 'confirmed');
      const { blockhash } = await feCon.getLatestBlockhash();
      transaction.recentBlockhash = blockhash;
      const sim = await feCon.simulateTransaction(transaction.compileMessage());
      if (sim.value.err) {
        throw new Error(SolanaActionService.parseSimulationError(sim.value.err, sim.value.logs ?? []));
      }
    } catch (simErr: any) {
      if (simErr.message?.startsWith('sim:')) throw simErr;
      // RPC error — skip simulation, don't block the transaction
    }

    // Sign & submit
    callbacks.onSign?.();
    const signature = await this.lendService.signAndSubmit(transaction);
    callbacks.onSubmit?.(signature);

    // Tracker'a bildir
    this.tracker
      .track(signature, action.type, { protocol: action.params['protocol'] })
      .then(txId => {
        const sub = this.tracker.transactions$.subscribe(map => {
          const tx = map.get(txId);
          if (tx?.status === 'confirmed') { callbacks.onConfirm?.(); sub.unsubscribe(); }
          else if (tx?.status === 'failed') { sub.unsubscribe(); }
        });
      })
      .catch(() => callbacks.onConfirm?.());

    return signature;
  }

  // ─── Param Normalization ──────────────────────────────────────────────────

  /**
   * Normalize action params for the Rust backend.
   * Intent-parser produces camelCase params; backend expects camelCase JSON.
   */
  private normalizeParams(action: ParsedAction): Record<string, unknown> {
    const p = action.params;
    switch (action.type) {
      case 'swap':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          ...(p['swapMode'] ? {
            swapMode: (p['swapMode'] === 'out' || p['swapMode'] === 'ExactOut') ? 'ExactOut' : 'ExactIn'
          } : {}),
          ...(p['onlyDirectRoutes'] != null ? { onlyDirectRoutes: p['onlyDirectRoutes'] === 'true' } : {}),
          ...(p['priorityFee'] ? { priorityFee: p['priorityFee'] } : {}),
          ...(p['restrictIntermediateTokens'] != null ? { restrictIntermediateTokens: p['restrictIntermediateTokens'] === 'true' } : {}),
        };
      case 'limit_order': {
        // Normalize legacy param names from older LLM outputs:
        // price → targetPrice, expiration (unix) → expirySeconds (duration)
        const targetPrice = p['targetPrice'] ?? p['price'];
        const rawExpiry = p['expirySeconds'] ?? p['expiration'];
        let expirySeconds: number | undefined;
        if (rawExpiry) {
          const v = parseInt(rawExpiry);
          if (!isNaN(v)) {
            // If value looks like a unix timestamp (> 1e9), convert to duration
            expirySeconds = v > 1_000_000_000
              ? Math.max(0, v - Math.floor(Date.now() / 1000))
              : v;
          }
        }
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          targetPrice,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          ...(expirySeconds !== undefined ? { expirySeconds } : {}),
        };
      }
      case 'cancel_limit_order':
        return { order: p['order'] ?? p['orderId'] };
      case 'cancel_all_limit_orders':
        return {};
      case 'dca':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          totalAmount: p['totalAmount'] ?? p['amount'],
          numberOfOrders: parseInt(p['numberOfOrders'] ?? p['duration'] ?? '10'),
          intervalSeconds: this.parseInterval(p['intervalSeconds'] ?? p['frequency'] ?? p['interval'] ?? '86400'),
          startAt: p['startAt'] ? parseInt(p['startAt']) : (p['startTime'] ? parseInt(p['startTime']) : undefined),
          minOutPerCycle: p['minOutPerCycle'] ? String(p['minOutPerCycle']) : undefined,
          maxOutPerCycle: p['maxOutPerCycle'] ? String(p['maxOutPerCycle']) : undefined,
        };
      case 'cancel_dca':
        return { order: p['order'] ?? p['orderId'] };
      // ── Jupiter Lend ─────────────────────────────────────────────────────────
      case 'lend':
      case 'withdraw_lend':
        return {
          token: p['token'] ?? p['asset'] ?? p['mint'],
          amount: p['amount'],
          protocol: p['protocol'] ?? 'jupiter',
        };
      case 'borrow':
      case 'repay':
        return {
          token: p['token'] ?? p['asset'],
          amount: p['amount'],
          protocol: p['protocol'] ?? 'jupiter',
          ...(p['collateral'] ? { collateral: p['collateral'] } : {}),
          ...(p['vaultId'] ? { vaultId: p['vaultId'] } : {}),
          ...(p['collateralAmount'] ? { collateralAmount: p['collateralAmount'] } : {}),
          ...(p['positionId'] ? { positionId: p['positionId'] } : {}),
        };
      case 'jupsol_stake':
      case 'jupsol_unstake':
        return {
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
        };
      case 'stake':
      case 'unstake':
        return {
          amount: p['amount'],
          protocol: p['protocol'] ?? 'marinade',
          instantUnstake: p['instantUnstake'] === 'true',
        };
      // ── Raydium Actions ─────────────────────────────────────────────────────
      case 'raydium_swap':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          swapMode: (p['swapMode'] === 'out' || p['swapMode'] === 'ExactOut') ? 'ExactOut' : 'ExactIn',
        };
      case 'raydium_add_liquidity': {
        const hasPoolId = !!p['poolId'];
        const hasTokenPair = !!(p['tokenA'] && p['tokenB']);
        if (hasPoolId) {
          // Pool ID mode: poolId + amount + inputMint (single-sided)
          return {
            poolId: p['poolId'],
            amount: p['amount'],
            inputMint: p['inputMint'],
            slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          };
        } else if (hasTokenPair) {
          // Token-pair mode: tokenA + tokenB + amountA + amountB
          return {
            tokenA: p['tokenA'],
            tokenB: p['tokenB'],
            amountA: p['amountA'],
            amountB: p['amountB'],
            slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          };
        }
        // Fallback — pass everything and let Rust validate
        return {
          poolId: p['poolId'],
          tokenA: p['tokenA'],
          tokenB: p['tokenB'],
          amount: p['amount'],
          amountA: p['amountA'],
          amountB: p['amountB'],
          inputMint: p['inputMint'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      }
      case 'raydium_remove_liquidity':
        return {
          poolId: p['poolId'],
          lpAmount: p['lpAmount'] ?? p['liquidity'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'raydium_create_pool':
        return {
          mintA: p['mintA'] ?? p['tokenA'],
          mintB: p['mintB'] ?? p['tokenB'],
          amountA: p['amountA'],
          amountB: p['amountB'],
          startTime: p['startTime'] ? parseInt(p['startTime']) : 0,
        };
      case 'raydium_open_position':
        return {
          // Pool discovery — either poolId or tokenA+tokenB
          ...(p['poolId']  ? { poolId:  p['poolId']  } : {}),
          ...(p['tokenA']  ? { tokenA:  p['tokenA']  } : {}),
          ...(p['tokenB']  ? { tokenB:  p['tokenB']  } : {}),
          // Deposit token — inputMint (alias: tokenA) and amount
          inputMint:   p['inputMint'] ?? p['tokenA'],
          inputAmount: p['inputAmount'] ?? p['amountA'],
          // Price range (preferred) — backend converts to ticks
          ...(p['minPrice'] ? { minPrice: parseFloat(p['minPrice']) } : {}),
          ...(p['maxPrice'] ? { maxPrice: parseFloat(p['maxPrice']) } : {}),
          // Tick range (advanced, direct)
          ...(p['tickLower'] ? { tickLower: parseInt(p['tickLower']) } : {}),
          ...(p['tickUpper'] ? { tickUpper: parseInt(p['tickUpper']) } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          // Never pass amountB — CLMM positions are single-sided
        };
      case 'raydium_close_position':
        return {
          positionId: p['positionId'],
        };
      case 'raydium_increase_position':
        return {
          positionId: p['positionId'],
          inputMint: p['inputMint'],
          inputAmount: p['inputAmount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'raydium_decrease_position':
        return {
          positionId: p['positionId'],
          liquidity: p['liquidity'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      // ── Orca Whirlpools Actions ─────────────────────────────────────────────────
      case 'orca_swap':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          swapMode: p['swapMode'] ?? 'in',
          whirlpool: p['whirlpool'],
        };
      case 'orca_add_liquidity':
        return {
          whirlpool: p['whirlpool'],
          amountA: p['amountA'],
          amountB: p['amountB'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_remove_liquidity':
        return {
          whirlpool: p['whirlpool'],
          liquidity: p['liquidity'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_open_position':
        return {
          // Direct mode
          whirlpool: p['whirlpool'],
          inputMint: p['inputMint'],
          inputAmount: p['inputAmount'],
          tickLower: p['tickLower'] ? parseInt(p['tickLower']) : undefined,
          tickUpper: p['tickUpper'] ? parseInt(p['tickUpper']) : undefined,
          // Human-friendly mode (LLM-generated)
          tokenA: p['tokenA'],
          tokenB: p['tokenB'],
          amountA: p['amountA'],
          minPrice: p['minPrice'] ? parseFloat(p['minPrice']) : undefined,
          maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_close_position':
        return {
          position: p['position'],
        };
      case 'orca_increase_position':
        return {
          position: p['position'],
          inputMint: p['inputMint'],
          inputAmount: p['inputAmount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_decrease_position':
        return {
          position: p['position'],
          liquidity: p['liquidity'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_collect_fees':
        return {
          position: p['position'],
        };
      case 'orca_collect_rewards':
        return {
          position: p['position'],
          rewardIndex: p['rewardIndex'] ? parseInt(p['rewardIndex']) : 0,
        };
      // ── Kamino Finance Actions ─────────────────────────────────────────────────
      case 'kamino_deposit':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_withdraw':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_borrow':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_repay':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_add_collateral':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_withdraw_collateral':
        return {
          token: p['token'] ?? p['reserve'],
          amount: p['amount'],
          market: p['market'],
          obligation: p['obligation'],
        };
      case 'kamino_multiply_open':
        return {
          strategy: p['strategy'] ?? p['vault'],
          amount: p['amount'] ?? p['collateralAmount'],
          token: p['token'],
          leverage: p['leverage'] ? parseFloat(p['leverage']) : 2.0,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_multiply_add':
        return {
          position: p['position'],
          amount: p['amount'],
          token: p['token'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_multiply_withdraw':
        return {
          position: p['position'],
          percent: p['percent'] ? parseFloat(p['percent']) : (p['amount'] ? parseFloat(p['amount']) : 100),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_multiply_close':
        return {
          position: p['position'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_long_open':
        return {
          collateralToken: p['collateralToken'] ?? p['token'],
          collateralAmount: p['collateralAmount'] ?? p['amount'],
          leverage: p['leverage'] ? parseFloat(p['leverage']) : 2.0,
          debtToken: p['debtToken'],
          sizeUsd: p['sizeUsd'] ? parseFloat(p['sizeUsd']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_short_open':
        return {
          collateralToken: p['collateralToken'] ?? p['token'],
          collateralAmount: p['collateralAmount'] ?? p['amount'],
          leverage: p['leverage'] ? parseFloat(p['leverage']) : 2.0,
          debtToken: p['debtToken'],
          sizeUsd: p['sizeUsd'] ? parseFloat(p['sizeUsd']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_position_close':
        return {
          position: p['position'],
          percent: p['percent'] ? parseFloat(p['percent']) : (p['closePercent'] ? parseFloat(p['closePercent']) : undefined),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_vault_deposit':
        return {
          vault: p['vault'] ?? p['vaultName'],
          amount: p['amount'],
          token: p['token'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_vault_withdraw':
        return {
          vault: p['vault'] ?? p['vaultName'],
          ktokenAmount: p['ktokenAmount'] ?? p['shares'] ?? p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_stake':
        return {
          amount: p['amount'],
          lockupSeconds: p['lockupSeconds'] ? parseInt(p['lockupSeconds']) : undefined,
        };
      case 'kamino_unstake':
        return {
          amount: p['amount'],
        };
      // ── Jito Finance Actions ─────────────────────────────────────────────────
      case 'jito_stake':
        return {
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
        };
      case 'jito_unstake':
        return {
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
        };
      case 'jito_tip':
        return {
          amount: p['amount'],
          transaction: p['transaction'],
        };
      case 'jito_bundle': {
        let bundleTxs: unknown[] = [];
        try { bundleTxs = p['transactions'] ? JSON.parse(p['transactions'] as string) : []; } catch { /* malformed JSON → send empty */ }
        return { transactions: bundleTxs, tipAmount: p['tipAmount'] };
      }
      case 'jito_bundle_status':
        return {
          bundleId: p['bundleId'],
        };
      // ── Meteora DLMM Actions ─────────────────────────────────────────────────
      case 'meteora_swap':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          swapMode: p['swapMode'] ?? 'in',
          pool: p['pool'],
        };
      case 'meteora_add_liquidity':
        return {
          pool: p['pool'],
          amountX: p['amountX'],
          amountY: p['amountY'],
          minBinId: p['minBinId'] ? parseInt(p['minBinId']) : undefined,
          maxBinId: p['maxBinId'] ? parseInt(p['maxBinId']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'meteora_remove_liquidity': {
        let parsedBinIds: number[] | undefined;
        try { parsedBinIds = p['binIds'] ? JSON.parse(p['binIds'] as string) : undefined; } catch { /* ignore */ }
        return { position: p['position'], binIds: parsedBinIds, slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100 };
      }
      case 'meteora_create_pool':
        return {
          tokenXMint: p['tokenXMint'],
          tokenYMint: p['tokenYMint'],
          binStep: p['binStep'] ? parseInt(p['binStep']) : 10,
          initialPrice: p['initialPrice'] ? parseFloat(p['initialPrice']) : 1.0,
          amountX: p['amountX'],
          amountY: p['amountY'],
          baseFee: p['baseFee'] ? parseFloat(p['baseFee']) : 0.01,
        };
      case 'meteora_open_position':
        return {
          pool: p['pool'],
          amountX: p['amountX'],
          amountY: p['amountY'],
          minBinId: p['minBinId'] ? parseInt(p['minBinId']) : undefined,
          maxBinId: p['maxBinId'] ? parseInt(p['maxBinId']) : undefined,
          minPrice: p['minPrice'] ? parseFloat(p['minPrice']) : undefined,
          maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'meteora_close_position':
        return {
          position: p['position'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'meteora_add_to_position':
        return {
          position: p['position'],
          amountX: p['amountX'],
          amountY: p['amountY'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'meteora_claim_fees':
        return {
          position: p['position'],
        };
      case 'meteora_claim_rewards':
        return {
          position: p['position'],
          rewardIndex: p['rewardIndex'] ? parseInt(p['rewardIndex']) : 0,
        };
      case 'meteora_stake':
        return {
          farm: p['farm'],
          amount: p['amount'],
        };
      case 'meteora_unstake':
        return {
          farm: p['farm'],
          amount: p['amount'],
        };
      case 'meteora_harvest':
        return {
          farm: p['farm'],
        };
      // ── PumpFun Token Launch ──────────────────────────────────────────────────────
      case 'launch_token':
      case 'pumpfun_launch':
        return {
          name:             p['name'],
          symbol:           p['symbol'],
          description:      p['description'] ?? '',
          imageUrl:         p['imageUrl'] ?? p['image_url'] ?? p['image'] ?? undefined,
          metadataUri:      p['metadataUri'] ?? p['metadata_uri'] ?? undefined,
          initialBuyAmount: p['initialBuyAmount'] ?? p['initial_buy_amount'] ?? p['initialBuy'] ?? undefined,
          slippage:         p['slippage'] ? parseFloat(p['slippage'] as string) : undefined,
          priorityFee:      p['priorityFee'] ? parseFloat(p['priorityFee'] as string) : undefined,
          twitter:          p['twitter'] ?? undefined,
          telegram:         p['telegram'] ?? undefined,
          website:          p['website'] ?? undefined,
          bannerUrl:        p['bannerUrl'] ?? p['banner_url'] ?? undefined,
          mayhemMode:       p['mayhemMode'] ?? undefined,
          cashback:         p['cashback'] ?? undefined,
        };
      case 'pumpswap_pool_info':
      case 'pumpfun_token_info':
        return { mint: p['mint'] ?? p['token'] ?? p['address'] };
      case 'pumpfun_trending':
        return { limit: p['limit'] ? parseInt(p['limit'] as string) : 20 };
      case 'pumpfun_new':
        return { limit: p['limit'] ? parseInt(p['limit'] as string) : 20 };
      case 'pumpfun_graduating':
        return { limit: p['limit'] ? parseInt(p['limit'] as string) : 20 };
      case 'pumpfun_search':
        return { query: p['query'] ?? p['q'] ?? p['search'], limit: p['limit'] ? parseInt(p['limit'] as string) : 20 };
      case 'pumpfun_koth':
        return {};
      case 'pumpfun_comments':
        return { mint: p['mint'] ?? p['token'] ?? p['address'], limit: p['limit'] ? parseInt(p['limit'] as string) : 25, offset: p['offset'] ? parseInt(p['offset'] as string) : 0 };
      case 'pumpfun_user':
        return { wallet: p['wallet'] ?? p['address'] };
      case 'pumpfun_bonding_curve':
        return { mint: p['mint'] ?? p['token'] ?? p['address'] };
      // ── Burn & Transfer ───────────────────────────────────────────────────────────
      case 'burn':
        return {
          mint:      p['mint'] ?? p['token'],
          amount:    p['amount'],
          closeMint: p['closeMint'] === 'true',
        };
      case 'transfer':
        return {
          to:            p['to'] ?? p['recipient'] ?? p['address'],
          amount:        p['amount'],
          token:         p['token'] ?? p['mint'],
          tokenDecimals: p['tokenDecimals'] ? parseInt(p['tokenDecimals'] as string) : undefined,
        };
      // ── Marinade Finance Actions ─────────────────────────────────────────────────
      case 'marinade_stake':
        return {
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
        };
      case 'marinade_unstake':
        return {
          amount: p['amount'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
        };
      case 'marinade_delayed_unstake':
        return {
          amount: p['amount'],
        };
      // ── marginfi Protocol Actions ────────────────────────────────────────────────
      case 'marginfi_create_account':
        return {
          referralCode: p['referralCode'],
        };
      case 'marginfi_deposit':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
        };
      case 'marginfi_withdraw':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
          allowBorrow: p['allowBorrow'] === 'true' ? true : p['allowBorrow'] === 'false' ? false : undefined,
        };
      case 'marginfi_borrow':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
        };
      case 'marginfi_repay':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
        };
      case 'marginfi_deposit_collateral':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
        };
      case 'marginfi_withdraw_collateral':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
        };
      case 'marginfi_account_info':
        return { account: p['account'], wallet: p['wallet'] };
      case 'marginfi_banks':
        return { limit: p['limit'] ? parseInt(p['limit']) : undefined };
      case 'marginfi_health':
        return { account: p['account'], wallet: p['wallet'] };
      case 'marginfi_points':
        return { wallet: p['wallet'] };
      case 'marginfi_close_account':
        return { account: p['account'] };
      // ── Solend Protocol Actions (data queries only) ────────────────────────────
      case 'solend_user_info':
        return { wallet: p['wallet'] ?? p['walletAddress'] };
      case 'solend_reserves':
      case 'solend_market':
        return {};
      // ── Tensor NFT Marketplace Actions ──────────────────────────────────────────
      case 'tensor_buy':
        return {
          mintAddress: p['mintAddress'] ?? p['mint'],
          maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : 0,
        };
      case 'tensor_list':
        return {
          mintAddress: p['mintAddress'] ?? p['mint'],
          price: p['price'] ? parseFloat(p['price']) : 0,
        };
      case 'tensor_cancel_listing':
        return {
          mintAddress: p['mintAddress'] ?? p['mint'],
        };
      case 'tensor_make_offer':
        return {
          collectionSlug: p['collectionSlug'] ?? p['collection'] ?? p['slug'],
          price: p['price'] ? parseFloat(p['price']) : 0,
          quantity: p['quantity'] ? String(p['quantity']) : undefined,
        };
      case 'tensor_cancel_offer':
        return {
          bidId: p['bidId'] ?? p['bid'] ?? p['orderId'],
        };
      case 'tensor_collection_info':
        return { collectionSlug: p['collectionSlug'] ?? p['collection'] ?? p['slug'] };
      case 'tensor_nft_info':
        return { mintAddress: p['mintAddress'] ?? p['mint'] };
      case 'tensor_wallet_nfts':
        return { wallet: p['wallet'] };
      case 'tensor_listings':
        return {
          collectionSlug: p['collectionSlug'] ?? p['collection'] ?? p['slug'],
          limit: p['limit'] ? String(p['limit']) : undefined,
        };
      // ── Magic Eden NFT Marketplace Actions ──────────────────────────────────────
      case 'me_list':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ? parseFloat(p['price']) : 0,
          expiry: p['expiry'] ? parseInt(p['expiry']) : undefined,
        };
      case 'me_buy':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ? parseFloat(p['price']) : 0,
          tokenAddress: p['tokenAddress'],
          seller: p['seller'],
        };
      case 'me_cancel_listing':
        return {
          mintAddress: p['mintAddress'],
        };
      case 'me_make_offer':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ? parseFloat(p['price']) : 0,
          expiry: p['expiry'] ? parseInt(p['expiry']) : undefined,
        };
      case 'me_accept_offer':
        return {
          mintAddress: p['mintAddress'],
          buyer: p['buyer'],
          price: p['price'] ? parseFloat(p['price']) : undefined,
        };
      case 'me_cancel_offer':
        return {
          mintAddress: p['mintAddress'],
        };
      case 'me_collection_info':
        return {
          collectionSymbol: p['collectionSymbol'],
        };
      case 'me_nft_info':
        return {
          mintAddress: p['mintAddress'],
        };
      case 'me_wallet_nfts':
        return {
          walletAddress: p['walletAddress'],
          limit: p['limit'] ? parseInt(p['limit']) : 10,
        };
      case 'me_collection_activity':
        return {
          collectionSymbol: p['collectionSymbol'],
          limit: p['limit'] ? parseInt(p['limit']) : 20,
        };
      case 'me_listings':
        return {
          collectionSymbol: p['collectionSymbol'],
          limit: p['limit'] ? parseInt(p['limit']) : 20,
        };
      case 'me_offers':
        return {
          mintAddress: p['mintAddress'],
          collectionSymbol: p['collectionSymbol'],
        };
      case 'me_collection_nfts':
        return {
          collectionSymbol: p['collectionSymbol'],
          limit: p['limit'] ? parseInt(p['limit']) : 20,
        };
      // ── Cross-Chain Swap (Relay) Actions ────────────────────────────────────────
      case 'cross_chain_swap': {
        const xProvider = (p['provider'] ?? 'relay').toLowerCase();
        // Accept both canonical names (originChainId) and LLM/plugin shorthand (fromChain)
        const rawOriginId   = p['originChainId']      ?? p['fromChain']  ?? '1';
        const rawDestId     = p['destinationChainId'] ?? p['toChain']    ?? '42161';
        // Accept originToken (Squid) or originCurrency (Relay/deBridge) or fromToken (shorthand)
        const originCurr    = p['originCurrency']  ?? p['originToken']  ?? p['fromToken'] ?? p['token'] ?? '0x0000000000000000000000000000000000000000';
        const destCurr      = p['destinationCurrency'] ?? p['destinationToken'] ?? p['toToken'] ?? originCurr;
        // deBridge uses its own Solana chain ID (7565164); Relay uses 900
        const mapChainId = (raw: string | number): number => {
          const n = parseInt(String(raw));
          if (xProvider === 'debridge' && n === 900) return 7565164;
          return n || 1;
        };
        const base: Record<string, unknown> = {
          originChainId:      mapChainId(rawOriginId),
          destinationChainId: mapChainId(rawDestId),
          originCurrency:      originCurr,
          destinationCurrency: destCurr,
          amount: p['amount'],
          recipient: p['recipient'],
          tradeType: p['tradeType'] ?? 'EXACT_INPUT',
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 50,
          referrer: p['referrer'],
          provider: xProvider,
        };
        // Squid-specific fields: originToken / destinationToken / slippage (%)
        if (xProvider === 'squid') {
          base['originToken'] = originCurr;
          base['destinationToken'] = destCurr;
          // slippage in % (Squid) — convert from bps if needed
          const rawSlippage = p['slippage'] ?? p['slippagePct'];
          base['slippage'] = rawSlippage != null
            ? parseFloat(String(rawSlippage))
            : (base['slippageBps'] as number) / 100;
        }
        return base;
      }
      // ── Jupiter Perpetuals ───────────────────────────────────────────────────
      case 'perp_open':
        return {
          operation: 'open',
          market: p['market'] ?? 'SOL',
          side: p['side'] ?? 'long',
          // Support both collateralAmount (UI/new) and collateral (legacy LLM output)
          collateralAmount: p['collateralAmount'] ?? p['collateral'] ?? '0',
          ...(p['sizeUsd'] ? { sizeUsd: p['sizeUsd'] } : {}),
          leverage: p['leverage'] ?? '2',
          ...(p['collateralToken'] ? { collateralToken: p['collateralToken'] } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 200,
        };
      case 'perp_close':
        return {
          operation: 'close',
          market: p['market'] ?? 'SOL',
          side: p['side'] ?? 'long',
          // Must be a positive value — backend rejects 0. User must specify exact collateral amount.
          collateralAmount: p['collateralAmount'] ?? p['sizeUsd'] ?? '',
          ...(p['collateralToken'] ? { collateralToken: p['collateralToken'] } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 200,
        };
      case 'jlp_add':
        return {
          operation: 'add',
          amount: p['amount'] ?? '0',
          token: p['token'] ?? 'SOL',
        };
      case 'jlp_remove':
        return {
          operation: 'remove',
          amount: p['amount'] ?? '0',
          token: p['token'] ?? 'SOL',
        };
      // ── Pump.fun bonding curve buy/sell ────────────────────────────────────────
      case 'pumpfun_buy':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: p['denominatedInSol'] !== 'false',
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      case 'pumpfun_sell':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: p['denominatedInSol'] === 'true',
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      // ── PumpSwap AMM buy/sell (graduated tokens) ─────────────────────────────
      case 'pumpswap_buy':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: p['denominatedInSol'] !== 'false',
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      case 'pumpswap_sell':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: p['denominatedInSol'] === 'true',
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      default:
        return p as Record<string, unknown>;
    }
  }

  /**
   * Parse a frequency/interval string into seconds.
   * Accepts: "86400", "daily", "hourly", "weekly", "monthly"
   */
  private parseInterval(value: string): number {
    const lower = value.toLowerCase().trim();
    if (lower === 'minutely' || lower === '1m') return 60;
    if (lower === 'hourly' || lower === '1h') return 3600;
    if (lower === 'daily' || lower === '1d') return 86400;
    if (lower === 'weekly' || lower === '1w') return 604800;
    if (lower === 'monthly' || lower === '30d') return 2592000;
    const n = parseInt(value);
    return isNaN(n) ? 86400 : n;
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  private base64ToUint8Array(base64: string): Uint8Array {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes;
  }
}
