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
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { createSolanaConnection } from '@core/utils/solana-connection';
import { Keypair } from '@solana/web3.js';
import type { Transaction, VersionedTransaction } from '@solana/web3.js';

export interface ValidatorInfo {
  voteAccount: string;
  commission: number;
  activatedStakeSol: number;
  apyEstimatePct: number;
  epochCreditsRecent: number;
}

export interface ActionCallbacks {
  onQuote?: () => void;
  onSign?: () => void;
  onSubmit?: (signature: string) => void;
  /** Called when action completes. For data-only queries, `result` contains the query description. */
  onConfirm?: (result?: string) => void;
  /** Called during chain execution to report progress. */
  onStatus?: (status: string) => void;
  /**
   * Keep-alive ping for long multi-step flows (e.g. a borrow that needs a
   * separate collateral-setup approval + on-chain confirmation before the main
   * tx). Lets the card reset its stall timeout so the flow isn't killed while
   * legitimately waiting on a second wallet approval or a confirmation poll.
   */
  onProgress?: () => void;
  /**
   * Called for token launches with the browser-generated mint (base58) as soon as
   * it's created — before signing. Lets the card persist the new token's contract
   * address into chat history so later "sell this / sell HOOD4" turns can resolve it.
   */
  onMintGenerated?: (mint: string) => void;
  /**
   * The transaction landed on chain and reverted.
   *
   * There was no way to report this: a submitted transaction either reached
   * `onConfirm` or silently stayed "submitted" forever, and the launch path
   * called `onConfirm` the moment the wallet returned a signature. A user
   * whose launch failed with a slippage revert was shown a green card.
   *
   * A signature is a receipt of submission. Only the chain decides success.
   */
  onFail?: (message: string, signature: string) => void;
}

interface QuoteResponse {
  quoteId: string;
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  priceImpact: string;
  // Backend-attached mint provenance from `services::mint_security`. Lets the
  // UI tell the user "you're swapping for a token Jupiter recognises but we
  // haven't verified" so a vanity-prefix look-alike can't ride on the symbol
  // alone.
  mintProvenance?: {
    input?: MintProvenance;
    output?: MintProvenance;
    warnUser?: boolean;
  };
  /** Backend's USD estimate. Use this for the daily-cap commit so the
   *  number on the counter matches the number that gated the /quote. */
  estUsd?: number;
}

export type MintProvenance =
  | { trust: 'verified'; symbol: string; name: string; decimals: number }
  | { trust: 'jupiter_known'; symbol: string; name: string; decimals: number }
  | { trust: 'unknown' };

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
  // Read-only / batch / multi-tx payload. For token launches with an initial buy,
  // `data.initialBuy` tells the frontend to perform the dev-buy as a follow-up
  // (via `pumpfun_initial_buy`) after the create tx confirms. For streamflow batch,
  // `data.transactions` is the full array.
  data?: {
    initialBuy?: { mint: string; amountSol: number };
    transactions?: string[];
    [k: string]: unknown;
  };
}

// Action types that return data instead of a transaction (transaction: null from backend)
// Exposed as a static Set so other components (e.g. action-card) can reference it.
const DATA_ONLY_ACTION_TYPES_LIST: string[] = [
  // Magic Eden data queries
  'me_collection_info', 'me_nft_info', 'me_wallet_nfts',
  'me_collection_activity', 'me_listings', 'me_offers', 'me_collection_nfts',
  // MarginFi data queries
  'marginfi_account_info', 'marginfi_banks', 'marginfi_bank_detail', 'marginfi_health',
  'marginfi_points', 'marginfi_user_accounts',
  // Solend data queries
  'solend_user_info', 'solend_reserves', 'solend_market',
  // Jito — bundle submits directly to Jito API; transaction field is null from Rust
  'jito_bundle',
  // Jito status check
  'jito_bundle_status',
  // Squid status check (returns data, no transaction)
  'squid_status',
  // Tensor NFT data queries
  'tensor_collection_info', 'tensor_nft_info', 'tensor_wallet_nfts', 'tensor_listings',
  // Pump.fun data queries (no transaction — backend returns JSON data in preview.description)
  'pumpfun_token_info', 'pumpfun_trending', 'pumpfun_new', 'pumpfun_graduating',
  'pumpfun_search', 'pumpfun_koth', 'pumpfun_comments', 'pumpfun_user', 'pumpfun_bonding_curve',
  // PumpSwap AMM data queries
  'pumpswap_pool_info',
  // Streamflow — stream details fetch (returns data, no transaction)
  'streamflow_get_one', 'streamflow_list',
  // Jupiter data queries
  'jup_dca_orders', 'jup_limit_orders', 'jup_price', 'jup_token_search',
  'jup_tokens_tag', 'jup_tokens_recent', 'jup_tokens_trending',
  'jup_portfolio_positions', 'jup_staked_jup', 'jup_lend_positions',
  'jup_lend_earnings', 'jup_pending_invites', 'jup_lend_markets', 'jup_platforms',
  // Raydium data queries
  'raydium_get_pools', 'raydium_search_pools', 'raydium_swap_quote',
  'raydium_get_pool_info', 'raydium_get_user_positions', 'raydium_get_clmm_positions',
  'raydium_get_token_info', 'raydium_get_platform_stats', 'raydium_get_clmm_configs',
  'raydium_get_pools_by_lp', 'raydium_get_pools_v2', 'raydium_get_pool_keys',
  'raydium_get_pool_liquidity_history', 'raydium_get_pool_position_history',
  'raydium_get_token_list', 'raydium_get_token_prices', 'raydium_get_farm_info',
  'raydium_get_farm_by_lp', 'raydium_get_farm_keys', 'raydium_get_ido_keys',
  'raydium_get_main_version', 'raydium_get_rpcs', 'raydium_get_chain_time',
  'raydium_get_stake_pools', 'raydium_get_migrate_lp', 'raydium_get_auto_fee',
  'raydium_get_cpmm_configs',
  // Orca data queries
  'orca_get_pools', 'orca_search_pools', 'orca_get_pool', 'orca_get_locked_liquidity',
  'orca_get_protocol_stats', 'orca_get_orca_token', 'orca_get_circulating_supply',
  'orca_get_total_supply', 'orca_get_tokens', 'orca_search_tokens', 'orca_get_token',
  'orca_get_user_positions', 'orca_get_pool_positions',
  // Meteora DLMM data queries
  'meteora_dlmm_get_pairs', 'meteora_dlmm_get_pair', 'meteora_dlmm_get_user_positions',
  'meteora_dlmm_get_active_bin', 'meteora_dlmm_get_pool_groups',
  'meteora_dlmm_get_pool_group', 'meteora_dlmm_get_pool_ohlcv',
  'meteora_dlmm_get_pool_volume_history', 'meteora_dlmm_get_protocol_stats',
  // Meteora DAMM v2 data queries
  'meteora_dammv2_get_pools', 'meteora_dammv2_get_pool_groups',
  'meteora_dammv2_get_pool_group', 'meteora_dammv2_get_pool',
  'meteora_dammv2_get_pool_ohlcv', 'meteora_dammv2_get_pool_volume_history',
  'meteora_dammv2_get_protocol_metrics',
  'meteora_dammv2_get_user_positions',
  // Meteora DAMM v1 data queries
  'meteora_dammv1_get_pools', 'meteora_dammv1_get_pool_configs',
  'meteora_dammv1_search_pools', 'meteora_dammv1_get_farms',
  'meteora_dammv1_get_pools_metrics', 'meteora_dammv1_get_alpha_vaults',
  'meteora_dammv1_get_alpha_vault_configs', 'meteora_dammv1_get_pools_by_vault_lp',
  'meteora_dammv1_get_fee_config',
  // Meteora Dynamic Vault data queries
  'meteora_vault_get_info', 'meteora_vault_get_addresses', 'meteora_vault_get_state',
  'meteora_vault_get_apy', 'meteora_vault_get_apy_history', 'meteora_vault_get_virtual_price',
  // Meteora Stake2Earn data queries
  'meteora_s2e_get_analytics', 'meteora_s2e_get_all_vaults',
  'meteora_s2e_filter_vaults', 'meteora_s2e_get_vault',
  // Kamino data queries — market/lending
  'kamino_vaults', 'kamino_markets', 'kamino_market_reserves',
  'kamino_user_vault_positions', 'kamino_user_obligations',
  'kamino_oracle_prices', 'kamino_usd_benchmark_rates',
  'kamino_market_metrics_history', 'kamino_reserve_borrow_apy_history',
  'kamino_reserve_borrow_apy_median', 'kamino_obligation_interest_earned',
  'kamino_obligation_interest_paid', 'kamino_obligation_transactions',
  'kamino_user_klend_transactions_all', 'kamino_user_klend_transactions',
  'kamino_borrow_order_fills', 'kamino_open_borrow_orders',
  'kamino_yield_history', 'kamino_principal_token_yields',
  'kamino_airdrop_allocations', 'kamino_airdrop_metrics',
  'kamino_staking_yields', 'kamino_staking_yields_median', 'kamino_staking_yields_mean',
  'kamino_user_staking_boosts', 'kamino_season_rewards_user',
  'kamino_season_rewards_vesting_pool', 'kamino_private_credit_metrics',
  'kamino_private_credit_metrics_history', 'kamino_user_farm_transactions',
  'kamino_farm_transactions',
  // Kamino data queries — earn vault
  'kamino_vault_detail', 'kamino_vault_metrics', 'kamino_vault_metrics_history',
  'kamino_vault_allocation_history', 'kamino_vaults_rewards', 'kamino_vaults_summary',
  'kamino_vault_mint_metadata', 'kamino_vault_mint_image',
  'kamino_user_metrics_history', 'kamino_user_transactions', 'kamino_user_kvault_rewards',
  'kamino_user_vault_position', 'kamino_user_vault_metrics_history',
  'kamino_user_vault_pnl', 'kamino_user_vault_pnl_history', 'kamino_vault_transactions',
  'kamino_vault_deposit_instructions', 'kamino_vault_withdraw_instructions',
  // Kamino data queries — borrow market
  'kamino_market_detail', 'kamino_market_reserve_history',
  'kamino_market_leverage_metrics', 'kamino_market_reserves_account',
  'kamino_user_rewards', 'kamino_loan_detail', 'kamino_obligation_pnl',
  'kamino_obligation_metrics_history', 'kamino_rewards_list', 'kamino_rewards_history',
  'kamino_borrow_instructions', 'kamino_repay_instructions',
  'kamino_deposit_instructions', 'kamino_withdraw_instructions',
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
  'orca_create_pool',
  'orca_add_liquidity',
  'orca_remove_liquidity',
  'orca_open_position',
  'orca_close_position',
  'orca_increase_position',
  'orca_decrease_position',
  'orca_collect_fees',
  'orca_collect_rewards',
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
  'kamino_kswap',
  // Jito Finance API — all Jito TXs are legacy bincode (Transaction::new_unsigned),
  // so none are listed here. jito_bundle goes through DATA_ONLY (Jito API direct).
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
  // Meteora DAMM v1 — build_vtx_b64 versioned (swap routes through Jupiter)
  'meteora_dammv1_swap',
  'meteora_dammv1_deposit',
  'meteora_dammv1_withdraw',
  // Meteora DAMM v2 — build_vtx_b64 versioned (swap routes through Jupiter)
  'meteora_dammv2_swap',
  'meteora_dammv2_add_liquidity',
  'meteora_dammv2_remove_liquidity',
  'meteora_dammv2_claim_fee',
  'meteora_dammv2_close_position',
  // Meteora Dynamic Vault — build_vtx_b64 versioned
  'meteora_vault_deposit',
  'meteora_vault_withdraw',
  // Meteora Stake-to-Earn (m3m3) — build_vtx_b64 versioned
  'meteora_s2e_stake',
  'meteora_s2e_unstake',
  'meteora_s2e_claim_fee',
  'meteora_s2e_cancel_unstake',
  'meteora_s2e_withdraw',
];

// Actions where the Rust backend embeds partial signatures (e.g. mint keypair).
// Pre-flight simulation would fail because the blockhash and partial sigs cannot be
// reproduced locally — skip it entirely and let the RPC node validate on submit.
// launch_* sign post-build with a mint keypair; perp_* are multi-signer and are
// only complete after Jupiter's execute endpoint adds the keeper signatures, so
// a local pre-sign simulation of the wallet-only tx would be misleading.
const SKIP_SIMULATION_TYPES = new Set(['launch_token', 'token_launch', 'pumpfun_launch', 'perp_open', 'perp_close']);

// Actions handled locally by Angular services (not through the Rust backend)
// These build transactions on the frontend and sign+submit directly,
// OR are purely local (config stored in localStorage, no on-chain TX).

/**
 * Safely parse a boolean param that may arrive as a native JS boolean (from Python's
 * preserve-types fix) or as the legacy strings 'true'/'false'.
 * `defaultWhenAbsent` applies when the value is undefined / null / unrecognised.
 */
function parseBoolParam(val: unknown, defaultWhenAbsent = false): boolean {
  if (val === true  || val === 'true')  return true;
  if (val === false || val === 'false') return false;
  return defaultWhenAbsent;
}

const FRONTEND_ACTION_TYPES = [
  // Jupiter Lend SDK — Rust only returns preview, frontend builds actual TX
  'lend', 'withdraw_lend', 'borrow', 'repay',
  // pump.fun bonding curve trades, built here from the curve account
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
  private readonly priceFeed = inject(PriceFeedService);

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

  /** Pick a Helius priority level based on what we're doing.
   *  Time-sensitive / MEV-prone actions get HIGH; routine ones get MEDIUM;
   *  no-rush data-only actions can use LOW. The level is forwarded to
   *  Helius `getPriorityFeeEstimate` which prices it against current
   *  mempool congestion, so MEDIUM at peak-load > MEDIUM at idle.
   */
  private pickHeliusPriorityLevel(actionType: string, params: Record<string, string> = {}): 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH' {
    const t = actionType.toLowerCase();
    // Time-sensitive: liquidations, perp closes, MEV-prone swaps on volatile tokens.
    if (t.includes('liquidate') || t.includes('perp_close') || t.includes('emergency')) {
      return 'VERY_HIGH';
    }
    // Volatile-token swaps need to land before the price moves.
    if (t === 'swap' && this.isVolatileToken(params['outputMint'] ?? params['inputMint'] ?? '')) {
      return 'HIGH';
    }
    // Pump.fun / launch / sniping — competitive blockspace.
    if (t.includes('pumpfun') || t.includes('launch')) {
      return 'HIGH';
    }
    // Bridges + cross-chain — long-tail, retry-friendly.
    if (t.includes('bridge') || t.includes('relay_')) {
      return 'LOW';
    }
    return 'MEDIUM';
  }

  /** Optimize tx compute units + priority fee via Helius. Falls back to original on error. */
  private async optimizeWithHelius(
    txBase64: string,
    actionType: string,
    params: Record<string, string> = {},
  ): Promise<string> {
    try {
      const priority_level = this.pickHeliusPriorityLevel(actionType, params);
      const result = await firstValueFrom(
        this.api.post<BuildResponse>('/actions/build', {
          type: 'helius_smart_send',
          params: { transaction: txBase64, priority_level },
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

  /**
   * Poll a signature until it reaches `confirmed`/`finalized` (or errors / times out).
   * Returns true on success, false on on-chain error or timeout. Never throws —
   * transient RPC hiccups are swallowed and retried.
   */
  private async waitForSignatureConfirmation(
    connection: { getSignatureStatus(sig: string, opts?: unknown): Promise<{ value: { confirmationStatus?: string; err: unknown } | null }> },
    sig: string,
    timeoutMs: number,
  ): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try {
        const st = await connection.getSignatureStatus(sig, { searchTransactionHistory: false });
        const s = st?.value;
        if (s?.err) return false;
        if (s && (s.confirmationStatus === 'confirmed' || s.confirmationStatus === 'finalized')) return true;
      } catch { /* transient RPC error — keep polling */ }
      await new Promise(r => setTimeout(r, 1500));
    }
    return false;
  }

  /**
   * Sign + submit + confirm the ordered setup transactions that must land BEFORE
   * the main action tx. Used by Kamino Multiply: opening a leveraged position on
   * a pair the user hasn't used before needs their per-position address lookup
   * table created and extended (and warmed up one slot) first, so the leveraged
   * deposit can compress against it and fit the 1232-byte limit.
   *
   * No-op unless the build returned a `transactions[]` with a non-zero
   * `actionTxIndex`. Each setup tx is a separate wallet approval; we wait for
   * confirmation between them (the LUT must exist before the next extend/deposit).
   */
  /**
   * Sign and confirm every step that must land BEFORE the action's own
   * transaction, in order, and return the transaction the caller should treat
   * as the action itself (null = use buildResult.transaction unchanged).
   *
   * Two shapes arrive here:
   *   - `transactions[] + actionTxIndex` (Kamino Multiply's lookup-table setup)
   *   - `executionSteps.type === 'sequential'` (Meteora close: remove+claim,
   *     then close the account)
   * The second was never handled: the flow signed buildResult.transaction and
   * stopped, so closing a position removed its liquidity, left the account
   * open with its rent locked, and still reported success.
   */
  private async signAndConfirmSetupTxs(
    buildResult: BuildResponse,
    web3: typeof import('@solana/web3.js'),
    connection: ReturnType<typeof createSolanaConnection>,
    callbacks: ActionCallbacks,
  ): Promise<string | null> {
    let steps: Array<{ tx: string; label?: string }> = [];

    const ordered = (buildResult as { transactions?: string[] }).transactions;
    const actionTxIndex = (buildResult as { actionTxIndex?: number }).actionTxIndex ?? 0;
    if (Array.isArray(ordered) && actionTxIndex > 0) {
      steps = ordered.map(tx => ({ tx }));
    } else {
      const es = (buildResult as { executionSteps?: any }).executionSteps
        ?? (buildResult as { execution_steps?: any }).execution_steps;
      if (es?.type === 'sequential' && Array.isArray(es.transactions)) {
        steps = es.transactions
          .map((st: any) => ({ tx: typeof st === 'string' ? st : st?.transaction, label: st?.label }))
          .filter((st: { tx?: string }) => !!st.tx);
      }
    }
    if (steps.length < 2) return null;

    // Everything but the last is a prerequisite; the last IS the action.
    const setup = steps.slice(0, -1);
    for (let i = 0; i < setup.length; i++) {
      callbacks.onStatus?.(setup[i].label
        ? `${setup[i].label} (${i + 1}/${steps.length})…`
        : `Step ${i + 1} of ${steps.length}…`);
      callbacks.onSign?.();
      const tx = web3.VersionedTransaction.deserialize(this.base64ToUint8Array(setup[i].tx));
      await this.refreshBlockhash(tx, connection);
      const signed = (await Promise.race([
        this.walletService.signTransaction(tx),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), 120_000),
        ),
      ])) as { serialize(): Uint8Array };
      const sig = await connection.sendRawTransaction(signed.serialize(), {
        skipPreflight: false,
        preflightCommitment: 'confirmed',
      });
      const ok = await this.waitForSignatureConfirmation(connection, sig, 90_000);
      if (!ok) throw new Error(`Step ${i + 1} didn\u2019t confirm \u2014 please try again.`);
    }
    const final = steps[steps.length - 1];
    callbacks.onStatus?.(final.label ? `${final.label}\u2026` : 'Finishing\u2026');
    return final.tx;
  }

  /**
   * Stamp a fresh blockhash on an unsigned transaction. Every step of a
   * sequential action is built at the same moment, but the later ones are
   * signed only after the earlier ones confirm — up to 90s each. By then the
   * blockhash they were built with can be gone, and the step fails with an
   * expiry error that has nothing to do with what the user did.
   */
  private async refreshBlockhash(
    tx: VersionedTransaction,
    connection: ReturnType<typeof createSolanaConnection>,
  ): Promise<void> {
    try {
      const { blockhash } = await connection.getLatestBlockhash('confirmed');
      tx.message.recentBlockhash = blockhash;
    } catch {
      // Keep the original blockhash — often still valid, and failing here
      // would block a step that would otherwise land.
    }
  }

  /**
   * Perform a token launch's initial dev-buy as a follow-up, AFTER the create tx
   * confirms (the token/curve must exist on-chain first).
   *
   * Built by the backend from the bonding curve account, which exists the
   * moment the create transaction lands. It used to go to PumpPortal, who take
   * 0.5%, because our own buy read the curve from pump.fun's API and that API
   * 404s on a token it has not indexed yet. It reads the chain now.
   *
   * It is a second wallet approval. Any failure leaves the token created, just
   * without the dev buy, and is logged rather than thrown.
   */
  /**
   * Acquire the mint keypair for a pump.fun launch. Prefers a pre-ground
   * "…pump" vanity address from the backend pool so the new token gets an
   * authentic pump.fun-style contract address with no per-launch wait. Falls
   * back to a locally-generated random keypair if the endpoint is unavailable
   * or the pool is momentarily empty — a launch must never block on this.
   *
   * The mint keypair is a throwaway (it controls nothing after create), so
   * receiving its secret from our own backend is the same trust as generating
   * it here.
   */
  private async acquireLaunchMintKeypair(): Promise<Keypair> {
    try {
      const res = await firstValueFrom(
        this.api
          .post<{ publicKey: string; secretKey: number[]; vanity?: boolean }>('/actions/vanity-mint', {})
          .pipe(timeout(8_000)),
      );
      if (res?.secretKey?.length === 64) {
        return Keypair.fromSecretKey(Uint8Array.from(res.secretKey));
      }
    } catch {
      /* pool cold or endpoint down — fall back to a random mint below */
    }
    return Keypair.generate();
  }

  private async submitLaunchInitialBuy(
    connection: any,
    initialBuy: { mint: string; amountSol: number },
    createSig: string,
    opts?: { slippage?: string; priorityFee?: string },
  ): Promise<void> {
    try {
      const web3 = await import('@solana/web3.js');
      // 1) Wait for the create tx to confirm so the token exists on-chain.
      const confirmed = await this.waitForSignatureConfirmation(connection, createSig, 45_000);
      if (!confirmed) {
        console.warn('[launch_token] create tx not confirmed in time — skipping initial buy');
        return;
      }

      // 2) Build the buy via the backend, from the bonding curve account. The
      //    retry is vestigial now that nothing waits on a third party's
      //    indexing, but a launch is not the place to find out otherwise.
      // slippage/priorityFee MUST be numbers — the backend PumpFunTradeParams
      // deserializes them as f64 and rejects strings ("invalid type: string ...").
      const slip = opts?.slippage != null ? Number(opts.slippage) : NaN;
      const prio = opts?.priorityFee != null ? Number(opts.priorityFee) : NaN;
      const buildBody = {
        type: 'pumpfun_initial_buy',
        params: {
          mint: initialBuy.mint,
          amount: String(initialBuy.amountSol),
          denominatedInSol: true,
          ...(Number.isFinite(slip) ? { slippage: slip } : {}),
          ...(Number.isFinite(prio) ? { priorityFee: prio } : {}),
        },
      };
      let build: BuildResponse | null = null;
      for (let i = 0; i < 3; i++) {
        try {
          build = await firstValueFrom(
            this.api.post<BuildResponse>('/actions/build', buildBody).pipe(timeout(20_000)),
          );
          if (build?.transaction) break;
        } catch (e) {
          if (i === 2) { console.warn('[launch_token] initial buy build failed:', e); return; }
          await new Promise(r => setTimeout(r, 2000));
        }
      }
      if (!build?.transaction) { console.warn('[launch_token] initial buy: no tx from backend'); return; }

      // 3) Deserialize, sign and submit.
      const buf = this.base64ToUint8Array(build.transaction);
      const tx = this.isVersionedTxBytes(buf)
        ? web3.VersionedTransaction.deserialize(buf)
        : web3.Transaction.from(buf);
      try {
        const directSig = await this.walletService.signAndSendTransaction(tx as any, { skipPreflight: true });
        if (directSig) { this.tracker.track(directSig, 'pumpfun_buy', {}).catch(() => {}); return; }
      } catch (e: any) {
        if (/reject|denied|cancel|declined|user refused/i.test(e?.message ?? '')) {
          console.warn('[launch_token] user declined initial buy — token created without dev buy');
          return;
        }
        // Wallet lacks signAndSendTransaction — fall through to manual sign+send.
      }
      const signed = await this.walletService.signTransaction(tx as any) as { serialize(): Uint8Array };
      const raw = signed.serialize();
      let sig: string;
      try { sig = await this.submitViaJito(raw); }
      catch { sig = await connection.sendRawTransaction(raw, { skipPreflight: false, preflightCommitment: 'confirmed' }); }
      this.tracker.track(sig, 'pumpfun_buy', {}).catch(() => {});
    } catch (e) {
      console.warn('[launch_token] initial buy failed — token created without dev buy:', e);
    }
  }

  private static readonly SOL_MINT = 'So11111111111111111111111111111111111111112';
  private static readonly TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';

  /** Map simulation errors to user-readable messages. Returns a string prefixed with "sim:". */
  static parseSimulationError(err: unknown, logs: string[]): string {
    const logsStr = logs.join('\n').toLowerCase();
    const errStr = JSON.stringify(err);

    // Diagnostic: dump full sim error to browser console so root cause is
    // visible during debugging. Front-end users won't see it; devs can read
    // exact program-id + error code + program logs in DevTools.
    try {
      // eslint-disable-next-line no-console
      console.warn('[oprai] simulation_failed', {
        err,
        last_logs: logs.slice(-15),
      });
    } catch {
      /* console may be sandboxed */
    }

    // Custom program error code. Two wire forms reach us: the structured
    // `{"InstructionError":[i,{"Custom":N}]}` from preflight sim, and the raw
    // `custom program error: 0x1771` text thrown by web3.js SendTransactionError
    // at submit time. Parse both (hex text → decimal) so classification works
    // regardless of which path surfaced the error.
    const structMatch = errStr.match(/"InstructionError":\s*\[\d+,\s*\{"Custom":\s*(\d+)\}\]/);
    let errorCode = structMatch ? parseInt(structMatch[1]) : null;
    if (errorCode === null) {
      const hexText = (errStr + ' ' + logsStr).match(/custom program error:\s*0x([0-9a-f]+)/i);
      if (hexText) errorCode = parseInt(hexText[1], 16);
    }

    // Token account has insufficient balance (SPL token error 0x1 = InsufficientFunds)
    if (errorCode === 1 || logsStr.includes('insufficient funds') || logsStr.includes('insufficient balance')) {
      return 'sim:insufficient_tokens';
    }

    // Jupiter slippage: 6001 (0x1771) SlippageToleranceExceeded + 6003 (some
    // routes report the latter). Both mean the realized out fell below min-out.
    if (errorCode === 6001 || errorCode === 6003) {
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

    // Anchor 3012 (AccountNotInitialized) — surface the exact offending account
    // name from the program logs so a missing token-account/position is obvious
    // rather than a misleading "amount below minimum" fallback. Anchor logs:
    //   "AnchorError caused by account: signer_borrow_token_account. Error
    //    Code: AccountNotInitialized. Error Number: 3012. …"
    if (errorCode === 3012 || logsStr.includes('accountnotinitialized')) {
      const acctMatch = logs.join('\n').match(/AnchorError caused by account:\s*([A-Za-z0-9_]+)/i);
      const acct = acctMatch ? acctMatch[1] : 'unknown';
      return `sim:account_not_init:${acct}`;
    }

    return `sim:generic:${errorCode ?? errStr.substring(0, 80)}`;
  }

  /**
   * Ask the chain what actually happened to a transaction.
   *
   * Returns `{ok: true}` when it landed and succeeded, `{ok: false}` with a
   * user-readable reason when it reverted, and `null` when the chain cannot
   * say yet — a transaction can be a second ahead of the indexer.
   *
   * The distinction is the whole point. The first version asked only "why did
   * this fail?", assumed the answer existed, and when it did not, produced
   * "The transaction simulation failed before signing" for a launch that had
   * succeeded and was already live on pump.fun. A tracker that gives up is
   * reporting on itself, not on the transaction.
   */
  private async readChainOutcome(
    signature: string,
  ): Promise<{ ok: true } | { ok: false; reason: string } | null> {
    const connection = createSolanaConnection('confirmed');
    // The indexer lags the ledger by a moment; a single miss is not a verdict.
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const tx = await connection.getTransaction(signature, {
          maxSupportedTransactionVersion: 0,
          commitment: 'confirmed',
        });
        if (tx?.meta) {
          if (!tx.meta.err) return { ok: true };
          return {
            ok: false,
            reason: SolanaActionService.parseSimulationError(tx.meta.err, tx.meta.logMessages ?? []),
          };
        }
      } catch {
        /* fall through to the retry */
      }
      if (attempt < 2) await new Promise(r => setTimeout(r, 2000));
    }
    return null;
  }


  /**
   * Follow a submitted transaction until the chain settles it, then report.
   *
   * Every submit path used to fire-and-forget: on success the tracker called
   * `onConfirm`, on failure it unsubscribed in silence, and if the tracker
   * itself threw, `onConfirm` was called anyway. So a reverted transaction
   * showed as either a permanently spinning card or an outright success.
   *
   * Confirmation is not a detail of one action type, so it lives in one place
   * and every path routes through it.
   */
  private watchSettlement(
    signature: string,
    action: string,
    callbacks: ActionCallbacks,
    options: Parameters<TransactionTrackerService['track']>[2] = {},
  ): void {
    // The tracker saying "failed" is a prompt to go and look, not a verdict.
    // It marks a transaction failed when its own confirmation loop gives up —
    // an RPC hiccup, a missed notification, an expired polling window — none
    // of which say anything about the transaction. So confirm against the
    // chain before telling the user their launch failed.
    const settleFromChain = async () => {
      const outcome = await this.readChainOutcome(signature);
      if (outcome?.ok) { callbacks.onConfirm?.(signature); return; }
      if (outcome) { callbacks.onFail?.(outcome.reason, signature); return; }
      // The chain has no answer. Leave the card submitted with its explorer
      // link rather than invent either result.
      console.warn('[settlement] chain could not resolve', signature);
    };

    this.tracker
      .track(signature, action, options)
      .then(txId => {
        const sub = this.tracker.transactions$.subscribe(map => {
          const tx = map.get(txId);
          if (tx?.status === 'confirmed') {
            sub.unsubscribe();
            callbacks.onConfirm?.(signature);
          } else if (tx?.status === 'failed') {
            sub.unsubscribe();
            void settleFromChain();
          }
        });
      })
      .catch(async () => {
        // The tracker couldn't record the transaction — a database or auth
        // problem, which says nothing about the transaction. Read the chain
        // directly rather than assuming the optimistic answer.
        await settleFromChain();
      });
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
    'jito_stake':      { token: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', symbol: 'jitoSOL' },
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
  async getTopValidators(): Promise<ValidatorInfo[]> {
    try {
      const resp = await firstValueFrom(
        this.api.get<{ validators: ValidatorInfo[] }>('/validators/top').pipe(timeout(15_000))
      );
      return resp.validators ?? [];
    } catch {
      return [];
    }
  }

  async getTokenBalance(mint: string): Promise<number> {
    const wallet = this.walletService.publicKey();
    if (!wallet) return 0;
    const { PublicKey } = await import('@solana/web3.js');
    const connection = createSolanaConnection('confirmed');
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

    // ── nft_mint: Remap to launch_token for legacy compatibility ──
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
    const amountUsd = await this.estimateAmountUsd(action);

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

    // ── Resolve amount=all / amount=max / amount="X%" before building ────
    const rawAmount = action.params['amount'];
    const pctMatch = typeof rawAmount === 'string' ? rawAmount.trim().match(/^(\d+(?:\.\d+)?)\s*%$/) : null;
    // Jupiter Lend Earn withdrawal drains the DEPOSITED position, not the wallet
    // balance — "withdraw all WSOL" means the full Earn deposit (which is often
    // larger than any wSOL sitting loose in the wallet). Resolve against the
    // live earn position for this asset so "all"/"max"/"X%" targets the deposit.
    if ((rawAmount === 'all' || rawAmount === 'max' || pctMatch) && action.type === 'withdraw_lend') {
      const wallet = this.walletService.publicKey();
      const positions = wallet ? await this.lendService.getAllEarnPositions(wallet) : [];
      const ref = (action.params['token'] ?? '').toUpperCase();
      const solAlias = ref === 'SOL' || ref === 'WSOL';
      const pos = positions.find(p => {
        const sym = p.asset.symbol.toUpperCase();
        return sym === ref || (solAlias && (sym === 'SOL' || sym === 'WSOL'));
      });
      let deposited = pos?.depositedAmount ?? 0;
      // Also consider the borrow-market SUPPLY ("Lending") position — often the
      // real one, with the Earn deposit being leftover dust. Resolve the
      // sentinel against whichever is larger so "withdraw all" targets the money.
      if (wallet) {
        const target = await this.lendService.getSupplyWithdrawTarget(wallet, action.params['token'] ?? '');
        if (target && target.supplyAmount > deposited) deposited = target.supplyAmount;
      }
      const adjusted = pctMatch ? deposited * (parseFloat(pctMatch[1]) / 100) : deposited;
      if (deposited <= 0) {
        throw new Error(`No ${action.params['token'] ?? 'that asset'} deposit found in Jupiter Lend to withdraw.`);
      }
      action = { ...action, params: { ...action.params, amount: adjusted.toString() } };
    } else if (rawAmount === 'all' || rawAmount === 'max' || pctMatch) {
      // jlp_remove spends JLP (the `token` param is the RECEIVE token), so the
      // sentinel must resolve against the JLP balance — not the receive token.
      const JLP_MINT = '27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4';
      const mint = action.type === 'jlp_remove'
        ? JLP_MINT
        : action.params['inputMint'] ?? action.params['mint'] ?? action.params['token'] ?? '';
      const resolved = await this.getTokenBalance(mint);
      // SOL needs to keep a small reserve for the tx fee + any token-account
      // rent we'll create in this transaction. Without this, "swap all SOL"
      // turns into "swap all but 0 SOL" → the tx fails simulation. The
      // reserve is intentionally generous: 0.005 SOL covers the priority
      // fee at urgent levels, and 0.00204 SOL × 2 covers two new ATAs.
      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      const isSol = !mint || mint === 'SOL' || mint === SOL_MINT;
      const SOL_RESERVE = 0.01; // ~$1.5 today; safer than minimums.
      // A percentage keeps the remainder, so no fee reserve is needed — only a
      // full drain ("all"/"max") must hold SOL back for fees.
      const adjusted = pctMatch
        ? resolved * (parseFloat(pctMatch[1]) / 100)
        : (isSol ? Math.max(resolved - SOL_RESERVE, 0) : resolved);
      if (!pctMatch && isSol && resolved <= SOL_RESERVE) {
        throw new Error(
          `Not enough SOL to cover transaction fee + rent (you have ${resolved.toFixed(4)} SOL, ` +
          `need at least ${SOL_RESERVE} SOL reserved). Top up your wallet first.`,
        );
      }
      action = { ...action, params: { ...action.params, amount: adjusted.toString() } };
    }

    // ── Backend-handled actions (Rust service) ───────────────────────────
    const isSwap = SWAP_ACTION_TYPES.includes(action.type);

    // Step 1: Get quote (swap only)
    let quoteId: string | undefined;
    // Backend-computed USD estimate from /quote. Forwarded into
    // /transactions on broadcast so the daily-cap counter stays in sync
    // with the number the gateway gated on. For non-swap actions we fall
    // back to the frontend's `amountUsd` estimate.
    let backendEstUsd: number | undefined;
    if (isSwap) {
      callbacks.onQuote?.();
      try {
        const quote = await firstValueFrom(
          this.api.post<QuoteResponse>('/actions/quote', {
            input_mint: action.params['inputMint'] ?? action.params['input_mint'],
            output_mint: action.params['outputMint'] ?? action.params['output_mint'],
            amount: action.params['amount'],
            slippage_bps: parseInt(action.params['slippageBps'] ?? '50', 10),
            ...(action.params['swapMode']                ? { swap_mode: action.params['swapMode'] } : {}),
            ...(action.params['onlyDirectRoutes'] != null ? { only_direct_routes: parseBoolParam(action.params['onlyDirectRoutes']) } : {}),
            ...(action.params['restrictIntermediateTokens'] != null ? { restrict_intermediate_tokens: parseBoolParam(action.params['restrictIntermediateTokens']) } : {}),
          }).pipe(timeout(30_000))
        );
        quoteId = quote.quoteId;
        backendEstUsd = quote.estUsd;
        // Dynamically raise slippage if price impact is significant
        action = this.adjustSlippageForImpact(action, quote);

        // Force the user to acknowledge a mint we cannot verify against our
        // compile-time registry. Jupiter knows the token, so it's tradeable,
        // but we haven't whitelisted it — this is the line of defence that
        // catches a vanity-prefix imposter ("JitoSOL"-lookalike etc.).
        if (quote.mintProvenance?.warnUser) {
          const { input, output } = quote.mintProvenance;
          const unverified =
            input?.trust === 'jupiter_known'  ? input
            : output?.trust === 'jupiter_known' ? output
            : null;
          if (unverified && unverified.trust === 'jupiter_known') {
            const unverifiedMint = unverified === input
              ? (action.params['inputMint'] ?? action.params['input_mint'])
              : (action.params['outputMint'] ?? action.params['output_mint']);
            const confirmed = await this.riskWarning.confirmUnverifiedMint(
              unverified.symbol,
              unverified.name,
              unverifiedMint,
            );
            if (!confirmed) {
              throw new Error('Swap cancelled — token is not in the verified registry.');
            }
          }
        }
      } catch (err: any) {
        if (err instanceof TimeoutError) {
          throw new Error('Quote request timed out. Check your connection and try again.');
        }
        const serverMsg = err?.error?.error ?? err?.error?.message;
        if (serverMsg) throw new Error(serverMsg);
        throw err;
      }
    }

    // Step 2: Build transaction via Rust backend
    // For launch_token: obtain the mint keypair (from the backend's pre-ground
    // "…pump" vanity pool, falling back to a random one). The mint pubkey is sent
    // to the backend; the backend builds the transaction WITHOUT signing it with
    // the mint keypair. After the user signs via Phantom, we add the mint
    // signature client-side. This way Phantom simulates the transaction with NO
    // pre-existing partial signatures — avoiding the stale-blockhash
    // invalidation that caused the simulation warning.
    const isLaunchAction = action.type === 'launch_token' || action.type === 'pumpfun_launch';
    const mintKeypair = isLaunchAction ? await this.acquireLaunchMintKeypair() : null;
    // Surface the new token's mint (contract) so the card can persist it into chat
    // history — enables later "sell this / sell HOOD4" to resolve the address.
    if (mintKeypair) callbacks.onMintGenerated?.(mintKeypair.publicKey.toBase58());

    const buildBody: Record<string, unknown> = {
      type: action.type,
      params: mintKeypair
        ? { ...this.normalizeParams(action), mintPubkey: mintKeypair.publicKey.toBase58() }
        : this.normalizeParams(action),
    };
    if (quoteId) {
      buildBody['quote_id'] = quoteId;
    }

    let buildResult: BuildResponse;
    try {
      buildResult = await firstValueFrom(
        this.api.post<BuildResponse>('/actions/build', buildBody).pipe(timeout(30_000))
      );
    } catch (err: any) {
      if (err instanceof TimeoutError) {
        throw new Error('Build request timed out. Check your connection and try again.');
      }
      const serverMsg = err?.error?.error ?? err?.error?.message;
      if (serverMsg) throw new Error(serverMsg);
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

    // Step 3: Optional Helius CU/fee optimization (silent, fallback-safe).
    // Priority level is picked dynamically per action type so liquidations
    // and volatile swaps don't sit in mempool while MEDIUM-tier txs land.
    if (this.heliusOptimizationEnabled && buildResult.transaction) {
      buildResult = {
        ...buildResult,
        transaction: await this.optimizeWithHelius(buildResult.transaction, action.type, action.params ?? {}),
      };
    }

    // Step 3b: Deserialize tx + precise fee check — must happen before wallet dialog
    const web3 = await import('@solana/web3.js');
    const connection = createSolanaConnection('confirmed');

    // Some actions return an ordered transactions[] where everything BEFORE
    // actionTxIndex is one-time setup that must land — and warm up — before the
    // main tx can reference it (Kamino Multiply's per-user lookup table: create +
    // extend, then the leveraged deposit compresses against it). Sign + confirm
    // those first, in order; the main tx (buildResult.transaction) follows below.
    const finalStepTx = await this.signAndConfirmSetupTxs(buildResult, web3, connection, callbacks);

    const txBuffer = this.base64ToUint8Array(finalStepTx ?? buildResult.transaction!);
    // Detect versioned-vs-legacy from the bytes themselves, not just the static
    // type list. Some actions (e.g. pumpfun_buy/sell) return a LEGACY bonding-curve
    // tx OR a VERSIONED Jupiter tx (graduated tokens routed through the aggregator)
    // depending on runtime state — the type alone can't tell them apart.
    const isVersioned = isSwap
      || VERSIONED_TX_TYPES.includes(action.type)
      || this.isVersionedTxBytes(txBuffer);

    // Deserialize here so we can inspect the fee before asking the wallet to sign
    let deserializedTx: VersionedTransaction | Transaction;
    if (isVersioned) {
      deserializedTx = web3.VersionedTransaction.deserialize(txBuffer);
    } else {
      deserializedTx = web3.Transaction.from(txBuffer);
    }

    // The final step of a sequential action was built before the earlier steps
    // ran, so its blockhash is as old as their confirmations — refresh it here
    // or the submit fails on expiry after the user has already signed.
    if (finalStepTx && isVersioned) {
      await this.refreshBlockhash(deserializedTx as VersionedTransaction, connection);
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

    // Pre-flight simulation — mandatory before sign, with backend fallback.
    // Order: local RPC simulate → if RPC errors, fall back to backend
    // /actions/simulate so a single-provider hiccup can't bypass simulation.
    // Skipped only for launch_token (mint keypair signs post-build).
    if (!SKIP_SIMULATION_TYPES.has(action.type)) {
      let simErr: { err: unknown; logs: string[] } | null = null;
      let rpcFailed = false;

      try {
        let sim: Awaited<ReturnType<typeof connection.simulateTransaction>>;
        if (isVersioned) {
          sim = await connection.simulateTransaction(
            deserializedTx as VersionedTransaction,
            { sigVerify: false, replaceRecentBlockhash: true }
          );
        } else {
          const legacyTx = deserializedTx as Transaction;
          sim = await connection.simulateTransaction(legacyTx.compileMessage());
        }
        if (sim.value.err) simErr = { err: sim.value.err, logs: sim.value.logs ?? [] };
      } catch {
        rpcFailed = true;
      }

      // Local RPC failed — try backend simulation as defense-in-depth.
      if (rpcFailed) {
        try {
          const resp = await firstValueFrom(
            this.api.post<{ success: boolean; logs?: string[]; error?: unknown; errorMessage?: string }>(
              '/actions/simulate',
              { transaction: buildResult.transaction! }
            ).pipe(timeout(15_000))
          );
          if (!resp.success) simErr = { err: resp.error ?? resp.errorMessage ?? 'unknown', logs: resp.logs ?? [] };
          rpcFailed = false;
        } catch (err: any) {
          // Both local + backend simulation unavailable — fail closed BUT
          // surface the underlying reason so the user / debugger can act on
          // it. Previously the bare catch collapsed everything to a flat
          // "sim:unavailable" with no signal about whether the gateway 404'd,
          // the backend 500'd, the JWT expired, or the request timed out.
          const status = err?.status ?? err?.error?.status;
          const detail = err?.error?.error ?? err?.error?.message ?? err?.message ?? 'unknown';
          console.warn('[simulate] backend fallback failed', { status, detail, err });
          throw new Error(`sim:unavailable (${status ?? 'no-status'}: ${String(detail).slice(0, 120)})`);
        }
      }

      if (simErr) {
        throw new Error(SolanaActionService.parseSimulationError(simErr.err, simErr.logs));
      }
    }

    callbacks.onSign?.();

    // Wallet sign with a 2-minute timeout — prevents a hung wallet dialog from
    // leaving the action permanently in "signing" state.
    const SIGN_TIMEOUT_MS = 120_000;
    // Step 3b (launch only): for launch_token, partial-sign with the browser-generated
    // mint keypair BEFORE handing to Phantom, then use signAndSendTransaction with
    // skipPreflight=true so Phantom never runs its internal simulation.
    let signature: string;
    if (mintKeypair) {
      // The mint has to sign, and how depends on the transaction version.
      // A launch WITH a dev-buy is v0 now — create and buy ride together so
      // nobody can snipe the gap between them — and `partialSign` does not
      // exist there. Checking only for `partialSign` would have skipped the
      // mint signature in silence and the chain would have rejected the
      // launch for a signature it never got.
      const tx = deserializedTx as {
        partialSign?: (...signers: Keypair[]) => void;
        sign?: (signers: Keypair[]) => void;
        version?: unknown;
      };
      try {
        if (typeof tx.partialSign === 'function') {
          tx.partialSign(mintKeypair);
        } else if (typeof tx.sign === 'function') {
          tx.sign([mintKeypair]);
        }
      } catch {
        /* backend signed with its own keypair — skip */
      }

      const directSig = await Promise.race([
        this.walletService.signAndSendTransaction(deserializedTx, { skipPreflight: true }),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), SIGN_TIMEOUT_MS)
        ),
      ]);

      if (directSig) {
        // Wallet signed + sent atomically — skip the separate sendRawTransaction step.
        callbacks.onSubmit?.(directSig);
        if (amountUsd > 0) this.spendingLimit.record(amountUsd);
        // Watch it land. This used to call onConfirm right here, which reported
        // a launch as successful the instant the wallet handed back a
        // signature — including the launches that reverted on chain.
        this.watchSettlement(directSig, action.type, callbacks, {
          protocol: action.params['protocol'],
          params: action.params as Record<string, unknown>,
          estUsd: backendEstUsd ?? (amountUsd > 0 ? amountUsd : undefined),
        });
        // Legacy path. A launch with a dev-buy is one atomic transaction now,
        // so the backend stops returning `initialBuy` and this never fires —
        // kept only so an older card mid-flight still completes rather than
        // losing its buy.
        const ib = buildResult.data?.initialBuy;
        if (ib) {
          void this.submitLaunchInitialBuy(connection, ib, directSig, {
            slippage: action.params?.['slippage'],
            priorityFee: action.params?.['priorityFee'],
          });
        }
        return directSig;
      }
      // Wallet doesn't support signAndSendTransaction — fall through to standard flow.
    }

    const signedTx = (await Promise.race([
      this.walletService.signTransaction(deserializedTx),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), SIGN_TIMEOUT_MS)
      ),
    ])) as { serialize(): Uint8Array; partialSign?: (...signers: Keypair[]) => void };

    // Fallback: add mint signature after wallet signing (for wallets without signAndSendTransaction).
    if (mintKeypair && typeof signedTx.partialSign === 'function') {
      try { signedTx.partialSign(mintKeypair); } catch { /* backend signed with its own keypair — skip */ }
    }

    // Step 4: Submit — via Jito block engine (MEV protection) or standard RPC.
    // Jito is forced ON for MEV-sensitive types regardless of the user toggle:
    // sandwich risk on volatile swaps and pump.fun trades is high enough that
    // a sub-second front-run can wipe the slippage buffer. The toggle still
    // governs the routine path (regular swaps, transfers).
    const mevSensitive =
      action.type === 'pumpfun_buy' ||
      action.type === 'pumpfun_sell' ||
      action.type === 'launch_token' ||
      action.type === 'liquidate' ||
      (action.type === 'swap' && this.isVolatileToken((action.params ?? {})['outputMint'] ?? ''));
    const useJito = this.jitoAutoRoutingEnabled || mevSensitive;

    // Blockhashes expire ~60s after build. If the user reads/thinks for a
    // while before signing, preflight at submit fails with "Blockhash not
    // found" / "block height exceeded". Surface those uniformly so the
    // action card shows a Retry-friendly message instead of the raw RPC dump.
    const submitOrThrow = async (raw: Uint8Array): Promise<string> => {
      try {
        return await connection.sendRawTransaction(raw, {
          skipPreflight: false,
          preflightCommitment: 'confirmed',
        });
      } catch (err: any) {
        const m = err?.message ?? String(err ?? '');
        if (/blockhash not found|block height exceeded/i.test(m)) {
          throw new Error('BLOCKHASH_EXPIRED');
        }
        throw err;
      }
    };

    if (action.type === 'perp_open' || action.type === 'perp_close') {
      // Jupiter Perps txs are multi-signer: the user signs one slot, but the
      // keeper (`perpSnt…`) + per-request signatures can only be added by
      // Jupiter's backend. A direct RPC submit would be rejected as missing
      // signatures and never land — so hand the user-signed tx to Jupiter's
      // execute endpoint (via our gateway), which fills the rest and submits.
      const signedB64 = this.uint8ArrayToBase64(signedTx.serialize());
      const jupAction = action.type === 'perp_open' ? 'increase-position' : 'decrease-position';
      try {
        const execRes = await firstValueFrom(
          this.api
            .post<{ txid?: string; signature?: string }>('/actions/perp-execute', {
              action: jupAction,
              serializedTxBase64: signedB64,
            })
            .pipe(timeout(30_000)),
        );
        signature = execRes.txid ?? execRes.signature ?? '';
        if (!signature) throw new Error('Jupiter Perps execute returned no transaction id.');
      } catch (err: any) {
        const m = err?.error?.error ?? err?.error?.message ?? err?.message ?? 'unknown';
        throw new Error(`Perp submit failed: ${String(m).slice(0, 160)}`);
      }
    } else if (useJito) {
      try {
        signature = await this.submitViaJito(signedTx.serialize());
      } catch (jitoErr: any) {
        const m = jitoErr?.message ?? String(jitoErr ?? '');
        if (/blockhash not found|block height exceeded/i.test(m)) {
          throw new Error('BLOCKHASH_EXPIRED');
        }
        signature = await submitOrThrow(signedTx.serialize());
      }
    } else {
      signature = await submitOrThrow(signedTx.serialize());
    }

    callbacks.onSubmit?.(signature);

    // Record spend on successful submission
    if (amountUsd > 0) {
      this.spendingLimit.record(amountUsd);
    }

    // Launch (fallback sign path): perform the initial dev-buy in the background
    // after the create tx confirms. `mintKeypair` is only set for
    // launch_token/pumpfun_launch. Not awaited — see the atomic path above.
    if (mintKeypair) {
      const ib = buildResult.data?.initialBuy;
      if (ib) {
        void this.submitLaunchInitialBuy(connection, ib, signature, {
          slippage: action.params?.['slippage'],
          priorityFee: action.params?.['priorityFee'],
        });
      }
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

    // For streamflow_create_multiple: sign and submit all stream transactions sequentially.
    // The Rust backend returns data.transactions = [tx0, tx1, ...] (all streams);
    // buildResult.transaction == tx0 (already signed above). Sign tx1..txN here.
    if (action.type === 'streamflow_create_multiple') {
      const allTxs: string[] = (buildResult as any).data?.['transactions'] ?? [];
      const extraTxs = allTxs.slice(1); // skip tx0, already handled above
      for (let i = 0; i < extraTxs.length; i++) {
        try {
          const buf = this.base64ToUint8Array(extraTxs[i]);
          const streamTx = web3.Transaction.from(buf);
          streamTx.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
          const signedStream = (await Promise.race([
            this.walletService.signTransaction(streamTx),
            new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Sign timeout')), 60_000)),
          ])) as { serialize(): Uint8Array };
          const sig = await connection.sendRawTransaction(signedStream.serialize(), {
            skipPreflight: false,
            preflightCommitment: 'confirmed',
          });
          this.tracker.track(sig, 'streamflow_create', {}).catch(() => {});
        } catch (e) {
          console.warn(`[streamflow_create_multiple] Failed to sign/submit stream ${i + 1}:`, e);
        }
      }
    }

    // Step 5: follow it to a settled state — confirmed or reverted.
    this.watchSettlement(signature, action.type, callbacks, {
      protocol: action.params['protocol'],
      params: action.params as Record<string, unknown>,
      estUsd: backendEstUsd ?? (amountUsd > 0 ? amountUsd : undefined),
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
  private async estimateAmountUsd(action: ParsedAction): Promise<number> {
    if (SolanaActionService.DATA_ONLY_TYPES.has(action.type)) return 0;
    const p = action.params;
    const SOL_MINT = 'So11111111111111111111111111111111111111112';
    const _NFT_PRICE_ACTIONS = new Set([
      'me_buy', 'me_buy_now', 'tensor_buy', 'me_make_offer', 'me_accept_offer',
      'tensor_make_offer', 'me_list', 'tensor_list',
    ]);
    const raw = _NFT_PRICE_ACTIONS.has(action.type)
      ? (p['price'] ?? p['amount'] ?? '0')
      : (p['amount'] ?? p['amountUsd'] ?? p['collateralAmount'] ?? p['collateral']
        ?? p['inputAmount'] ?? p['amountIn'] ?? p['totalAmount'] ?? p['amountA']
        ?? p['amountB'] ?? p['lpAmount'] ?? p['liquidity'] ?? '0');
    const n = parseFloat(String(raw));
    if (!isFinite(n) || n <= 0) return 0;

    // Resolve the mint to look up a real-time price
    const tokenParam = (p['token'] ?? p['inputMint'] ?? p['mint'] ?? '').toUpperCase();
    const isUsdc = /^(USD|USDC|USDT|DAI)/i.test(tokenParam);
    if (isUsdc) return n;

    const isSol = tokenParam === 'SOL' || tokenParam === SOL_MINT
      || _NFT_PRICE_ACTIONS.has(action.type)
      || ((action.type === 'pumpfun_buy' || action.type === 'pumpswap_buy')
          && parseBoolParam(p['denominatedInSol'], true));

    const mintForPrice = isSol ? SOL_MINT
      : (tokenParam.length > 20 ? tokenParam : null); // only look up real mints, not symbols

    if (mintForPrice) {
      try {
        const price = await this.priceFeed.getPrice(mintForPrice);
        if (price && price > 0) return n * price;
      } catch { /* fall through to fallback */ }
    }

    // Couldn't price the token (e.g. a brand-new pump.fun token not yet on the
    // price feed, or an unknown symbol). Do NOT treat the raw token COUNT as a
    // USD value — a 3.5M-token meme sell is worth ~$20, not $3.5M, and the old
    // 1:1 guess fired a bogus "Large Transaction — worth $3,564,784" warning on
    // an ordinary sell. Unknown value → 0 (skip the value-based warning) is far
    // safer than fabricating one. Stablecoins already returned above via the
    // isUsdc path, and SOL/USDC always resolve a real price.
    return 0;
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
    let transaction: Transaction | VersionedTransaction;

    switch (action.type) {
      // ── Jupiter Lend: Earn ──────────────────────────────────────────────
      case 'lend': {
        const asset = await this.lendService.resolveAsset(action.params['token'] ?? 'USDC');
        if (!asset) throw new Error(`Unsupported lend asset: ${action.params['token']}`);
        const result = await this.lendService.buildDepositTransaction(
          asset,
          parseFloat(action.params['amount'] ?? '0')
        );
        transaction = result.transaction;
        break;
      }
      case 'withdraw_lend': {
        const asset = await this.lendService.resolveAsset(action.params['token'] ?? 'USDC');
        if (!asset) throw new Error(`Unsupported lend asset: ${action.params['token']}`);
        const amount = parseFloat(action.params['amount'] ?? '0');
        // The user's Jupiter Lend position may live in the borrow-market SUPPLY
        // (Jupiter's "Lending" — collateral supplied to a borrow vault, borrow
        // may be 0), NOT the Earn/Vault product. Those are withdrawn via the
        // borrow-vault op, not the Earn SDK — routing an Earn withdraw against a
        // supply position drains the wrong (usually empty) balance. Prefer the
        // supply position whenever one exists for this asset.
        const wallet = this.walletService.publicKey();
        const supplyTarget = wallet
          ? await this.lendService.getSupplyWithdrawTarget(wallet, action.params['token'] ?? asset.mint)
          : null;
        if (supplyTarget && supplyTarget.supplyAmount > 0) {
          const result = await this.lendService.buildBorrowOperateTransaction(
            supplyTarget.vaultId,
            supplyTarget.positionId,
            -Math.abs(amount), // negative = withdraw collateral; snaps to MAX_WITHDRAW at ≥99% of supply
            0,                 // no debt change
            supplyTarget.colAsset,
            supplyTarget.debtAsset,
          );
          transaction = result.transaction;
          break;
        }
        const result = await this.lendService.buildWithdrawTransaction(asset, amount);
        transaction = result.transaction;
        break;
      }

      // ── Jupiter Lend: Borrow / Repay ────────────────────────────────────
      case 'borrow':
      case 'repay': {
        const colAsset = (await this.lendService.resolveAsset(action.params['collateral'] ?? 'SOL')) ??
          { symbol: 'SOL', mint: 'So11111111111111111111111111111111111111112', decimals: 9 };
        const debtAsset = (await this.lendService.resolveAsset(action.params['token'] ?? 'USDC')) ??
          { symbol: 'USDC', mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', decimals: 6 };
        const rawAmount = parseFloat(action.params['amount'] ?? '0');
        const debtDelta = action.type === 'borrow' ? rawAmount : -rawAmount;
        const result = await this.lendService.buildBorrowOperateTransaction(
          parseInt(action.params['vaultId'] ?? '-1'),  // -1 = auto-select best vault
          parseInt(action.params['positionId'] ?? '0'),
          parseFloat(action.params['collateralAmount'] ?? '0'),
          debtDelta,
          colAsset,
          debtAsset,
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
        let rawAmt = action.params['amount'] ?? '0';
        if (rawAmt === 'all' || rawAmt === 'max') {
          const bal = await this.getTokenBalance(mint);
          rawAmt = bal.toString();
        }
        const amount = parseFloat(rawAmt);
        if (!isFinite(amount) || amount <= 0) {
          throw new Error('Invalid amount. Please specify a positive number.');
        }
        const denominatedInSol = action.type === 'pumpfun_buy'
          ? parseBoolParam(action.params['denominatedInSol'], true)   // buy: default true (SOL-denominated)
          : parseBoolParam(action.params['denominatedInSol'], false);  // sell: default false (token-denominated)
        const slippage = action.params['slippage'] ? parseFloat(action.params['slippage']) : 10;
        const priorityFee = action.params['priorityFee'] ? parseFloat(action.params['priorityFee']) : 0.0005;

        // The backend `/actions/build` handles ALL routing internally: bonding-curve
        // tokens → a legacy pump.fun tx; graduated tokens (Raydium OR PumpSwap) →
        // a Jupiter versioned tx. We don't branch on graduation here — we just
        // build and deserialize based on the actual tx format returned.
        const resp = action.type === 'pumpfun_buy'
          ? await this.pumpFunService.buildBuy(wallet, mint, amount, denominatedInSol, slippage, priorityFee)
          : await this.pumpFunService.buildSell(wallet, mint, amount, denominatedInSol, slippage, priorityFee);
        if (!resp?.transaction) throw new Error(`Failed to build pump.fun transaction for ${mint.slice(0, 8)}`);

        const pumpTxBytes = this.base64ToUint8Array(resp.transaction);
        if (this.isVersionedTxBytes(pumpTxBytes)) {
          // Graduated token routed through Jupiter → versioned tx. Sign + submit
          // inline (the shared legacy tail below can't handle a VersionedTransaction).
          const { VersionedTransaction } = await import('@solana/web3.js');
          const vtx = VersionedTransaction.deserialize(pumpTxBytes);
          callbacks.onSign?.();
          const signedVtx = await this.walletService.signTransaction(vtx) as InstanceType<typeof VersionedTransaction>;
          const rpcConn = createSolanaConnection('confirmed');
          const sig = await rpcConn.sendRawTransaction(signedVtx.serialize(), { skipPreflight: false, preflightCommitment: 'confirmed' });
          callbacks.onSubmit?.(sig);
          this.watchSettlement(sig, action.type, callbacks, { protocol: action.params['protocol'] });
          return sig;
        }

        // Bonding-curve legacy tx → fall through to the shared sim + sign tail.
        const { Transaction } = await import('@solana/web3.js');
        transaction = Transaction.from(pumpTxBytes);
        break;
      }

      default:
        throw new Error(`Unknown frontend action type: ${action.type}`);
    }

    // Pre-flight simulation (frontend actions — legacy or v0 TX)
    try {
      const web3 = await import('@solana/web3.js');
      const feCon = createSolanaConnection('confirmed');
      let sim;
      if (transaction instanceof web3.VersionedTransaction) {
        // v0 tx already carries a blockhash from build; simulate it directly and
        // let the RPC swap in a fresh blockhash so a stale one doesn't fail sim.
        sim = await feCon.simulateTransaction(transaction, { replaceRecentBlockhash: true, sigVerify: false });
      } else {
        const { blockhash } = await feCon.getLatestBlockhash();
        transaction.recentBlockhash = blockhash;
        sim = await feCon.simulateTransaction(transaction.compileMessage());
      }
      if (sim.value.err) {
        console.error('[Sim] err:', JSON.stringify(sim.value.err), '\nlogs:', sim.value.logs);
        throw new Error(SolanaActionService.parseSimulationError(sim.value.err, sim.value.logs ?? []));
      }
      await this.assertSpendMatchesClaim(action, transaction, feCon, web3);
    } catch (simErr: any) {
      if (simErr.message?.startsWith('sim:')) throw simErr;
      if (simErr.message?.startsWith('guard:')) throw simErr;
      // RPC error — skip simulation, don't block the transaction
    }

    // Sign & submit
    callbacks.onSign?.();
    const signature = await this.lendService.signAndSubmit(transaction);
    callbacks.onSubmit?.(signature);

    this.watchSettlement(signature, action.type, callbacks, { protocol: action.params['protocol'] });

    return signature;
  }

  /**
   * The most SOL this action is allowed to take out of the wallet, in SOL.
   *
   * Returns null when we cannot state a number, and the check is skipped —
   * a guard that guesses a ceiling would block real transactions, which
   * teaches people to distrust it.
   */
  private static claimedMaxOutflow(action: ParsedAction): number | null {
    const num = (v: unknown) => {
      const n = parseFloat(String(v ?? ''));
      return Number.isFinite(n) && n > 0 ? n : null;
    };
    const t = action.type;
    const p = action.params;

    // Buying: the ask, plus room for the taker fee and network fees.
    if (/^(me_buy|me_buy_now|me_buy_instruction|me_buy_now_transfer_nft|tensor_buy)$/.test(t)) {
      const price = num(p['price']);
      return price === null ? null : price * 1.05 + 0.02;
    }
    // Bidding and escrow deposits: the amount leaves the wallet.
    if (/^(me_make_offer|me_deposit|me_mmm_sol_deposit_buy)$/.test(t)) {
      const amt = num(p['price']) ?? num(p['amount']) ?? num(p['paymentAmount']);
      return amt === null ? null : amt * 1.02 + 0.02;
    }
    // Listing, delisting, repricing, withdrawing, accepting a bid: none of
    // these buy anything. They cost rent and fees and nothing else — an
    // outflow beyond that means the transaction is not what it says it is.
    if (/^(me_list|me_sell|me_cancel_listing|me_sell_cancel|me_sell_change_price|me_buy_change_price|me_cancel_offer|me_buy_cancel|me_accept_offer|me_sell_now|me_withdraw|me_mmm_sol_withdraw_buy|me_mmm_sol_close_pool)$/.test(t)) {
      return 0.05;
    }
    return null;
  }

  /**
   * Refuse to hand over a transaction that spends more than the card said.
   *
   * We display a price we composed from our own parameters, next to bytes a
   * marketplace API composed from its own. Nothing checked that the two
   * agreed — so a crossed parameter, a changed price, an API having a bad
   * day, or a bug of mine would all reach the wallet looking exactly like a
   * correct request, and the user would find out afterwards.
   *
   * The simulation already runs; this reads what it did. If more SOL leaves
   * the wallet than the action could justify, nothing gets signed.
   */
  private async assertSpendMatchesClaim(
    action: ParsedAction,
    transaction: any,
    connection: any,
    web3: any,
  ): Promise<void> {
    const allowed = SolanaActionService.claimedMaxOutflow(action);
    if (allowed === null) return;

    const address = this.walletService.publicKey();
    if (!address) return;

    const owner = new web3.PublicKey(String(address));
    const before = await connection.getBalance(owner);

    const opts = {
      replaceRecentBlockhash: true,
      sigVerify: false,
      accounts: { encoding: 'base64', addresses: [address] },
    };
    const sim = transaction instanceof web3.VersionedTransaction
      ? await connection.simulateTransaction(transaction, opts)
      : await connection.simulateTransaction(transaction.compileMessage(), opts);

    const after = sim?.value?.accounts?.[0]?.lamports;
    if (typeof after !== 'number' || sim.value.err) return; // can't tell — don't block

    const spentSol = (before - after) / 1e9;
    if (spentSol <= allowed) return;

    console.error('[Guard] outflow', spentSol, 'SOL exceeds claim', allowed, 'for', action.type);
    throw new Error(
      `guard:This transaction would take ${spentSol.toFixed(4)} SOL from your wallet, `
      + `but this action should cost at most ${allowed.toFixed(4)} SOL. `
      + `Nothing has been signed — ask again to rebuild it.`,
    );
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
          ...(p['onlyDirectRoutes'] != null ? { onlyDirectRoutes: parseBoolParam(p['onlyDirectRoutes']) } : {}),
          ...(p['priorityFee'] ? { priorityFee: p['priorityFee'] } : {}),
          ...(p['restrictIntermediateTokens'] != null ? { restrictIntermediateTokens: parseBoolParam(p['restrictIntermediateTokens']) } : {}),
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
          minPrice: p['minPrice'] ? String(p['minPrice']) : undefined,
          maxPrice: p['maxPrice'] ? String(p['maxPrice']) : undefined,
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
          instantUnstake: parseBoolParam(p['instantUnstake']),
        };
      // ── Native Validator Staking (direct SOL stake, no LST) ─────────────────
      case 'native_stake':
        return {
          amount: p['amount'],
          validatorVoteAccount: p['validatorVoteAccount'] ?? p['validator'] ?? p['voteAccount'],
        };
      case 'native_stake_deactivate':
        return { stakeAccount: p['stakeAccount'] ?? p['account'] };
      case 'native_stake_withdraw':
        return {
          stakeAccount: p['stakeAccount'] ?? p['account'],
          amount: p['amount'] ?? 'all',
        };
      case 'native_stake_split':
        return {
          stakeAccount: p['stakeAccount'] ?? p['account'],
          amount: p['amount'],
        };
      case 'native_stake_merge':
        return {
          destinationStakeAccount: p['destinationStakeAccount'] ?? p['destinationStake'] ?? p['destAccount'],
          sourceStakeAccount: p['sourceStakeAccount'] ?? p['sourceStake'] ?? p['srcAccount'],
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
          // Pool-ID mode is single-sided: Raydium takes the pool + ONE input
          // token + its amount and computes the paired side from the pool
          // ratio. The pool card carries tokenA/amountA (+ auto-balanced
          // amountB) but no `amount`/`inputMint`, so derive the single input
          // from side A — falling back to side B — instead of sending the
          // (empty) `amount`/`inputMint` keys, which tripped the backend's
          // "provide either (poolId + inputMint + amount) or …" guard.
          const inputMint = p['inputMint'] ?? (p['amountA'] ? p['tokenA'] : p['amountB'] ? p['tokenB'] : p['tokenA']);
          const amount = p['amount'] ?? p['amountA'] ?? p['amountB'];
          return {
            poolId: p['poolId'],
            amount,
            inputMint,
            slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
            ...(p['baseIn'] !== undefined ? { baseIn: parseBoolParam(p['baseIn']) } : {}),
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
          ...(p['baseIn'] !== undefined ? { baseIn: parseBoolParam(p['baseIn']) } : {}),
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
      case 'raydium_open_position': {
        // The backend builder takes a single-sided `inputMint`/`inputAmount`
        // and computes liquidity from that side alone. Pick whichever
        // amount the user actually filled in — fall back to the
        // range-orientation heuristic only if BOTH are empty (defensive).
        //   range fully BELOW current → position is 100% Token B
        //   otherwise → A side is the input
        const amtA = parseFloat(p['amountA'] ?? '');
        const amtB = parseFloat(p['amountB'] ?? '');
        const cur  = parseFloat(p['currentPrice'] ?? '');
        const hi   = parseFloat(p['maxPrice'] ?? '');
        const heuristicB = Number.isFinite(cur) && Number.isFinite(hi) && cur > hi;
        // Whichever side has a real non-zero amount wins; if both are filled,
        // prefer A; if neither, fall back to the heuristic.
        const useB =
          Number.isFinite(amtB) && amtB > 0 && !(Number.isFinite(amtA) && amtA > 0)
            ? true
            : Number.isFinite(amtA) && amtA > 0
              ? false
              : heuristicB;
        const inputMint   = p['inputMint']   ?? (useB ? p['tokenB']  : p['tokenA']);
        const inputAmount = p['inputAmount'] ?? (useB ? p['amountB'] : p['amountA']);
        return {
          // Pool discovery — either poolId or tokenA+tokenB
          ...(p['poolId']  ? { poolId:  p['poolId']  } : {}),
          ...(p['tokenA']  ? { tokenA:  p['tokenA']  } : {}),
          ...(p['tokenB']  ? { tokenB:  p['tokenB']  } : {}),
          // Deposit token — chosen above based on range orientation
          inputMint,
          inputAmount,
          // Price range (preferred) — backend converts to ticks
          ...(p['minPrice'] ? { minPrice: parseFloat(p['minPrice']) } : {}),
          ...(p['maxPrice'] ? { maxPrice: parseFloat(p['maxPrice']) } : {}),
          // Tick range (advanced, direct)
          ...(p['tickLower'] ? { tickLower: parseInt(p['tickLower']) } : {}),
          ...(p['tickUpper'] ? { tickUpper: parseInt(p['tickUpper']) } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      }
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
      // ── Raydium data queries — page/pageSize must be int (Option<u32> in Rust) ──
      case 'raydium_get_pools':
      case 'raydium_get_pools_v2':
        return {
          ...p,
          ...(p['page'] ? { page: parseInt(p['page'] as string) } : {}),
          ...(p['pageSize'] ? { pageSize: parseInt(p['pageSize'] as string) } : {}),
        };
      case 'raydium_search_pools':
        return {
          ...p,
          ...(p['pageSize'] ? { pageSize: parseInt(p['pageSize'] as string) } : {}),
        };
      case 'raydium_get_farm_by_lp':
        return {
          ...p,
          ...(p['page'] ? { page: parseInt(p['page'] as string) } : {}),
          ...(p['pageSize'] ? { pageSize: parseInt(p['pageSize'] as string) } : {}),
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
      case 'orca_create_pool':
        return {
          tokenA: p['tokenA'] ?? p['token_a'] ?? p['tokenIn'],
          tokenB: p['tokenB'] ?? p['token_b'] ?? p['tokenOut'],
          initialPrice: p['initialPrice'] ? parseFloat(p['initialPrice']) : undefined,
          tickSpacing: p['tickSpacing'] ? parseInt(p['tickSpacing']) : undefined,
        };
      case 'orca_open_position': {
        // The card collects two amounts and a price band. The backend wants
        // ONE input side plus the band, and derives the pair from the pool —
        // so send whichever side the user actually filled.
        const aAmt = parseFloat(p['amountA'] ?? p['inputAmount'] ?? '') || 0;
        const bAmt = parseFloat(p['amountB'] ?? '') || 0;
        const useA = aAmt > 0 || bAmt <= 0;
        return {
          whirlpool: p['whirlpool'] ?? p['poolId'],
          inputMint: useA ? (p['tokenA'] ?? p['inputMint']) : p['tokenB'],
          inputAmount: String(useA ? aAmt : bAmt),
          ...(p['tickLower'] ? { tickLower: parseInt(p['tickLower']) } : {}),
          ...(p['tickUpper'] ? { tickUpper: parseInt(p['tickUpper']) } : {}),
          tokenA: p['tokenA'],
          tokenB: p['tokenB'],
          minPrice: p['minPrice'] ? parseFloat(p['minPrice']) : undefined,
          maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      }
      case 'orca_close_position':
        return {
          position: p['position'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'orca_increase_position': {
        // Same shape as open: the panel has two amount fields, the backend
        // takes one side and prices the other from the position's range.
        const aAmt = parseFloat(p['amountA'] ?? p['inputAmount'] ?? '') || 0;
        const bAmt = parseFloat(p['amountB'] ?? '') || 0;
        const useA = aAmt > 0 || bAmt <= 0;
        return {
          position: p['position'] ?? p['positionAddress'],
          inputMint: useA ? (p['tokenA'] ?? p['inputMint']) : p['tokenB'],
          inputAmount: String(useA ? aAmt : bAmt),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      }
      case 'orca_decrease_position': {
        // The panel drives a PERCENTAGE — nobody can reason about a raw
        // liquidity constant. Convert it here against the position's own
        // liquidity, which the positions row passes along.
        const bps = parseInt(p['bpsToRemove'] ?? '10000', 10);
        const total = p['liquidity'] ?? '';
        let liquidity = total;
        if (total && Number.isFinite(bps) && bps < 10_000) {
          try {
            liquidity = ((BigInt(total) * BigInt(bps)) / 10_000n).toString();
          } catch {
            liquidity = total;   // non-integer liquidity: withdraw it all
          }
        }
        return {
          position: p['position'] ?? p['positionAddress'],
          ...(liquidity ? { liquidity } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      }
      case 'orca_collect_fees':
        return {
          position: p['position'] ?? p['positionAddress'],
        };
      case 'orca_collect_rewards':
        return {
          position: p['position'] ?? p['positionAddress'],
          rewardIndex: p['rewardIndex'] ? parseInt(p['rewardIndex']) : 0,
        };
      // ── Kamino Finance Actions ─────────────────────────────────────────────────
      case 'kamino_deposit':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
        };
      case 'kamino_withdraw':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
        };
      case 'kamino_borrow':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
        };
      case 'kamino_repay':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
        };
      case 'kamino_add_collateral':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
        };
      case 'kamino_withdraw_collateral':
        return {
          reserve: p['reserve'] ?? p['token'],
          amount: p['amount'],
          market: p['market'],
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
          ...(p['market'] ? { market: p['market'] } : {}),
          sizeUsd: p['sizeUsd'] ? parseFloat(p['sizeUsd']) : undefined,
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'kamino_short_open':
        return {
          collateralToken: p['collateralToken'] ?? p['token'],
          collateralAmount: p['collateralAmount'] ?? p['amount'],
          leverage: p['leverage'] ? parseFloat(p['leverage']) : 2.0,
          debtToken: p['debtToken'],
          ...(p['market'] ? { market: p['market'] } : {}),
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
          ...(p['instant'] !== undefined ? { instant: parseBoolParam(p['instant']) } : {}),
        };
      case 'jito_tip':
        return {
          amount: p['amount'],
          transaction: p['transaction'],
        };
      case 'jito_bundle': {
        const rawTxs = p['transactions'];
        let bundleTxs: string[] = [];
        if (Array.isArray(rawTxs)) {
          bundleTxs = rawTxs.map(String);
        } else if (typeof rawTxs === 'string' && rawTxs.trim()) {
          if (rawTxs.startsWith('[')) {
            try { bundleTxs = JSON.parse(rawTxs); } catch { /* malformed */ }
          } else {
            bundleTxs = rawTxs.split(',').map(t => t.trim()).filter(Boolean);
          }
        }
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
      case 'meteora_add_liquidity': {
        // The form uses `poolId` / `amountA` / `amountB`; older LLM outputs
        // and the QueryCard "Use this pool" CTA also pass `pool` / `amountX`
        // / `amountY`. Accept either so a form-submit and a model-emitted
        // action both reach the Rust handler with the right keys.
        //
        // Range resolution priority (this order matters):
        //   1. Explicit minBinId+maxBinId (LLM's most precise input)
        //   2. binSpread + activeBinId (the form's current value — always
        //      present as the user-visible "± N bins" control). This wins
        //      over an inherited minPrice/maxPrice from a prior LLM turn,
        //      because the form is the user's latest decision.
        //   3. minPrice + maxPrice (last resort: a wide range here can
        //      explode into thousands of bins for low bin_step pools and
        //      blow past Solana's 1232 B tx-size limit).
        let minBinId = p['minBinId'] ? parseInt(p['minBinId']) : undefined;
        let maxBinId = p['maxBinId'] ? parseInt(p['maxBinId']) : undefined;
        if (minBinId === undefined && maxBinId === undefined) {
          const active = parseInt(p['activeBinId'] ?? '');
          const spread = parseInt(p['binSpread'] ?? '15');
          if (Number.isFinite(active) && Number.isFinite(spread) && spread > 0) {
            minBinId = active - spread;
            maxBinId = active + spread;
          }
        }
        // If we resolved the range from binSpread, drop any LLM-supplied
        // price hints so the Rust side doesn't see both and pick the wrong
        // one.
        const resolvedFromSpread = minBinId !== undefined && maxBinId !== undefined;
        return {
          pool: p['pool'] ?? p['poolId'],
          // A single-sided range legitimately deposits nothing on one side —
          // the card disables that input, so the key would otherwise vanish
          // from the JSON and the backend would reject the whole action for a
          // missing field. Zero is the answer, not absence.
          amountX: p['amountX'] ?? p['amountA'] ?? '0',
          amountY: p['amountY'] ?? p['amountB'] ?? '0',
          minBinId,
          maxBinId,
          ...(resolvedFromSpread ? {} : {
            minPrice: p['minPrice'] ? parseFloat(p['minPrice']) : undefined,
            maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : undefined,
          }),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          strategy: p['strategy'],
        };
      }
      case 'meteora_remove_liquidity': {
        let parsedBinIds: number[] | undefined;
        const rawBinIds = p['binIds'];
        if (Array.isArray(rawBinIds)) {
          parsedBinIds = rawBinIds.map(Number);
        } else if (rawBinIds) {
          const s = String(rawBinIds).trim();
          if (s.startsWith('[')) {
            try { parsedBinIds = JSON.parse(s); } catch { /* ignore */ }
          } else if (s) {
            parsedBinIds = s.split(',').map(v => parseInt(v.trim())).filter(n => !isNaN(n));
          }
        }
        return {
          position: p['position'],
          ...(parsedBinIds !== undefined ? { binIds: parsedBinIds } : {}),
          ...(p['bpsToRemove'] ? { bpsToRemove: parseInt(p['bpsToRemove'] as string) } : {}),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps'] as string) : 100,
        };
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
      case 'meteora_open_position': {
        // Same range derivation as meteora_add_liquidity: prefer explicit
        // bin ids, fall back to (activeBinId ± binSpread), then prices.
        // Range resolution priority — same as meteora_add_liquidity above:
        // explicit ids > form's binSpread (current user choice) > LLM's
        // price hints. Skipping the form's binSpread when the LLM emitted
        // wide minPrice/maxPrice translates to thousands of bins on small
        // bin_step pools and a 25 KB tx that the RPC rejects.
        let minBinId = p['minBinId'] ? parseInt(p['minBinId']) : undefined;
        let maxBinId = p['maxBinId'] ? parseInt(p['maxBinId']) : undefined;
        if (minBinId === undefined && maxBinId === undefined) {
          const active = parseInt(p['activeBinId'] ?? '');
          const spread = parseInt(p['binSpread'] ?? '15');
          if (Number.isFinite(active) && Number.isFinite(spread) && spread > 0) {
            minBinId = active - spread;
            maxBinId = active + spread;
          }
        }
        const resolvedFromSpread = minBinId !== undefined && maxBinId !== undefined;
        return {
          pool: p['pool'] ?? p['poolId'],
          // A single-sided range legitimately deposits nothing on one side —
          // the card disables that input, so the key would otherwise vanish
          // from the JSON and the backend would reject the whole action for a
          // missing field. Zero is the answer, not absence.
          amountX: p['amountX'] ?? p['amountA'] ?? '0',
          amountY: p['amountY'] ?? p['amountB'] ?? '0',
          minBinId,
          maxBinId,
          ...(resolvedFromSpread ? {} : {
            minPrice: p['minPrice'] ? parseFloat(p['minPrice']) : undefined,
            maxPrice: p['maxPrice'] ? parseFloat(p['maxPrice']) : undefined,
          }),
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          strategy: p['strategy'],
        };
      }
      case 'meteora_close_position':
        return {
          position: p['position'],
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
        };
      case 'meteora_add_to_position':
        return {
          position: p['position'],
          // A single-sided range legitimately deposits nothing on one side —
          // the card disables that input, so the key would otherwise vanish
          // from the JSON and the backend would reject the whole action for a
          // missing field. Zero is the answer, not absence.
          amountX: p['amountX'] ?? p['amountA'] ?? '0',
          amountY: p['amountY'] ?? p['amountB'] ?? '0',
          slippageBps: p['slippageBps'] ? parseInt(p['slippageBps']) : 100,
          strategy: p['strategy'],
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
          cashback:         p['cashback'] ?? undefined,
          tokenizedAgent:   p['tokenizedAgent'] ?? undefined,
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
          closeMint: parseBoolParam(p['closeMint']),
        };
      case 'close_accounts': {
        const mints = p['mints'];
        return {
          mints: typeof mints === 'string'
            ? mints.split(',').map((m: string) => m.trim()).filter(Boolean)
            : Array.isArray(mints) ? mints : [mints].filter(Boolean),
        };
      }
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
      case 'marinade_claim_ticket':
      case 'marinade_claim':
        return {
          ticketAccount: p['ticketAccount'] ?? p['ticket_account'] ?? p['ticket'],
        };
      // ── marginfi Protocol Actions ────────────────────────────────────────────────
      case 'marginfi_create_account':
        return {
          referralCode: p['referralCode'],
        };
      case 'marginfi_create_account_pda':
        return {
          ...(p['accountIndex'] !== undefined ? { accountIndex: parseInt(p['accountIndex'] as string) } : {}),
          ...(p['thirdPartyId'] !== undefined ? { thirdPartyId: parseInt(p['thirdPartyId'] as string) } : {}),
        };
      case 'marginfi_deposit':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
          depositUpToLimit: p['depositUpToLimit'] !== undefined ? parseBoolParam(p['depositUpToLimit']) : undefined,
        };
      case 'marginfi_withdraw':
        return {
          bank: p['bank'] ?? p['token'],
          amount: p['amount'],
          account: p['account'],
          withdrawAll: p['withdrawAll'] !== undefined ? parseBoolParam(p['withdrawAll']) : undefined,
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
          repayAll: p['repayAll'] !== undefined ? parseBoolParam(p['repayAll']) : undefined,
        };
      case 'marginfi_add_collateral':
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
      case 'marginfi_liquidate':
        return {
          account: p['account'],
          liquidateeAccount: p['liquidateeAccount'] ?? p['liquidatee'],
          assetBank: p['assetBank'],
          liabBank: p['liabBank'],
          assetAmount: p['assetAmount'] ?? p['amount'],
        };
      case 'marginfi_bank_detail':
        return { bank: p['bank'] ?? p['token'] };
      case 'marginfi_user_accounts':
        return {
          wallet: p['wallet'],
          maxIndex: p['maxIndex'] ? parseInt(p['maxIndex']) : undefined,
        };
      // ── MarginFi advanced operations (arrays + integer params) ────────────────
      case 'marginfi_flashloan_start':
        return {
          ...(p['account'] ? { account: p['account'] } : {}),
          endIndex: parseInt(p['endIndex'] ?? p['end_index'] ?? '0'),
        };
      case 'marginfi_place_order': {
        const banks = p['banks'];
        return {
          ...(p['account'] ? { account: p['account'] } : {}),
          limit: p['limit'],
          banks: typeof banks === 'string'
            ? banks.split(',').map((b: string) => b.trim()).filter(Boolean)
            : Array.isArray(banks) ? banks : [],
          maxDebtCoverage: p['maxDebtCoverage'] ?? p['max_debt_coverage'],
          orderSide: parseInt(p['orderSide'] ?? p['order_side'] ?? '0'),
        };
      }
      case 'marginfi_set_keeper_flags': {
        const kfBanks = p['banks'];
        return {
          ...(p['account'] ? { account: p['account'] } : {}),
          ...(kfBanks !== undefined ? {
            banks: typeof kfBanks === 'string'
              ? kfBanks.split(',').map((b: string) => b.trim()).filter(Boolean)
              : Array.isArray(kfBanks) ? kfBanks : [],
          } : {}),
        };
      }
      // ── MarginFi additional TX actions ───────────────────────────────────────
      case 'marginfi_claim_emissions':
      case 'marginfi_settle_emissions':
      case 'marginfi_clear_emissions':
        return {
          bank: p['bank'] ?? p['token'],
          ...(p['account'] ? { account: p['account'] } : {}),
          ...(p['emissionsMint'] ? { emissionsMint: p['emissionsMint'] } : {}),
        };
      case 'marginfi_withdraw_emissions_permissionless':
        return {
          account: p['account'],
          bank: p['bank'] ?? p['token'],
          ...(p['emissionsMint'] ? { emissionsMint: p['emissionsMint'] } : {}),
        };
      case 'marginfi_update_emissions_destination':
        return {
          destination: p['destination'],
          ...(p['account'] ? { account: p['account'] } : {}),
        };
      case 'marginfi_close_balance':
        return {
          bank: p['bank'] ?? p['token'],
          ...(p['account'] ? { account: p['account'] } : {}),
        };
      case 'marginfi_transfer_account':
        return {
          sourceAccount: p['sourceAccount'] ?? p['source_account'],
          destinationAccount: p['destinationAccount'] ?? p['destination_account'],
        };
      case 'marginfi_flashloan_end':
        return {
          ...(p['account'] ? { account: p['account'] } : {}),
        };
      case 'marginfi_close_order':
        return {
          order: p['order'],
          feeRecipient: p['feeRecipient'] ?? p['fee_recipient'],
          ...(p['account'] ? { account: p['account'] } : {}),
        };
      // ── Relay advanced TX actions ────────────────────────────────────────────
      case 'relay_claim_app_fees':
        return {
          chainId: p['chainId'] ? parseInt(p['chainId']) : undefined,
          currency: p['currency'],
          recipient: p['recipient'],
          ...(p['wallet'] ? { wallet: p['wallet'] } : {}),
          ...(p['amount'] ? { amount: String(p['amount']) } : {}),
        };
      case 'relay_single_transaction':
        return {
          requestId: p['requestId'],
          chainId: p['chainId'],
          tx: p['tx'],
        };
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
          maxPrice: p['maxPrice'] ?? '0',
        };
      case 'tensor_list':
        return {
          mintAddress: p['mintAddress'] ?? p['mint'],
          price: p['price'] ?? '0',
        };
      case 'tensor_cancel_listing':
        return {
          mintAddress: p['mintAddress'] ?? p['mint'],
        };
      case 'tensor_make_offer':
        return {
          collectionSlug: p['collectionSlug'] ?? p['collection'] ?? p['slug'],
          price: p['price'] ?? '0',
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
          price: p['price'] ?? '0',
          expiry: p['expiry'] ? parseInt(p['expiry']) : undefined,
        };
      case 'me_buy':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ?? '0',
          tokenAddress: p['tokenAddress'],
          seller: p['seller'],
        };
      case 'me_cancel_listing':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ?? '0',
          tokenAddress: p['tokenAddress'],
        };
      case 'me_make_offer':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ?? '0',
          expiry: p['expiry'] ? parseInt(p['expiry']) : undefined,
        };
      case 'me_accept_offer':
        return {
          mintAddress: p['mintAddress'],
          buyer: p['buyer'],
          price: p['price'],
        };
      case 'me_cancel_offer':
        return {
          mintAddress: p['mintAddress'],
          price: p['price'] ?? '0',
        };
      case 'me_collection_info':
        return {
          symbol: p['collectionSymbol'] ?? p['symbol'],
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
          symbol: p['collectionSymbol'] ?? p['symbol'],
          limit: p['limit'] ? parseInt(p['limit']) : 20,
        };
      case 'me_listings':
        return {
          symbol: p['collectionSymbol'] ?? p['symbol'],
          limit: p['limit'] ? parseInt(p['limit']) : 20,
        };
      case 'me_offers':
        return {
          mintAddress: p['mintAddress'],
          collectionSymbol: p['collectionSymbol'] ?? p['symbol'],
        };
      case 'me_collection_nfts':
        return {
          symbol: p['collectionSymbol'] ?? p['symbol'],
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
          // Squid optional bool flags
          if (p['enableExpress'] !== undefined) base['enableExpress'] = parseBoolParam(p['enableExpress']);
          if (p['receiveGasOnDestination'] !== undefined) base['receiveGasOnDestination'] = parseBoolParam(p['receiveGasOnDestination']);
          if (p['quoteOnly'] !== undefined) base['quoteOnly'] = parseBoolParam(p['quoteOnly']);
          if (p['enableBoost'] !== undefined) base['enableBoost'] = parseBoolParam(p['enableBoost'], true);
          // Squid optional Vec<String> fields (prefer / bypass bridge types)
          const parseStrArray = (v: unknown): string[] | undefined => {
            if (Array.isArray(v)) return v.map(String);
            if (typeof v === 'string' && v.trim()) return v.split(',').map(s => s.trim()).filter(Boolean);
            return undefined;
          };
          const prefer = parseStrArray(p['prefer']);
          const bypass  = parseStrArray(p['bypass']);
          if (prefer) base['prefer'] = prefer;
          if (bypass)  base['bypass']  = bypass;
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
          denominatedInSol: parseBoolParam(p['denominatedInSol'], true),
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      case 'pumpfun_sell':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: parseBoolParam(p['denominatedInSol'], false),
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      // ── PumpSwap AMM buy/sell (graduated tokens) ─────────────────────────────
      case 'pumpswap_buy':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: parseBoolParam(p['denominatedInSol'], true),
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };
      case 'pumpswap_sell':
        return {
          mint: p['mint'],
          amount: p['amount'],
          denominatedInSol: parseBoolParam(p['denominatedInSol'], false),
          slippage: p['slippage'] ? parseFloat(p['slippage']) : 10,
          priorityFee: p['priorityFee'] ? parseFloat(p['priorityFee']) : 0.0005,
        };

      // ── SNS (Solana Name Service) write actions ───────────────────────────────
      case 'sns_register':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          space: p['space'] ? parseInt(p['space']) : 1,
          token: p['token'] ?? 'USDC',
        };
      case 'sns_transfer':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          newOwner: p['newOwner'],
        };
      case 'sns_buy':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          token: p['token'] ?? 'USDC',
        };
      case 'sns_make_offer':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          amount: p['amount'] ? parseFloat(p['amount']) : 0,
          token: p['token'] ?? 'USDC',
        };
      case 'sns_accept_offer':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          offerKey: p['offerKey'],
          token: p['token'] ?? 'USDC',
        };
      case 'sns_cancel_offer':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          offerKey: p['offerKey'],
          token: p['token'] ?? 'USDC',
        };
      case 'sns_set_record':
        return {
          domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          record: p['record'] ?? p['type'] ?? p['key'],
          value: p['value'],
        };
      case 'sns_delete':
        return { domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase() };
      case 'sns_create_subdomain':
        return {
          subdomain: p['subdomain'] ?? (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
        };
      case 'sns_set_favorite':
        return { domain: (p['domain'] as string)?.replace(/\.sol$/i, '').toLowerCase() };

      // ── Streamflow token streaming & vesting ──────────────────────────────────
      case 'streamflow_create':
        return {
          recipient: p['recipient'],
          mint: p['mint'],
          amount: p['amount'],
          period: p['period'] ? parseInt(p['period']) : 86400,
          amountPerPeriod: p['amountPerPeriod'],
          ...(p['start'] ? { start: parseInt(p['start']) } : {}),
          ...(p['cliff'] ? { cliff: parseInt(p['cliff']) } : {}),
          ...(p['cliffAmount'] ? { cliffAmount: p['cliffAmount'] } : {}),
          ...(p['name'] ? { name: p['name'] } : {}),
          ...(p['canTopup'] !== undefined ? { canTopup: parseBoolParam(p['canTopup']) } : {}),
          ...(p['cancelableBySender'] !== undefined ? { cancelableBySender: parseBoolParam(p['cancelableBySender'], true) } : {}),
          ...(p['cancelableByRecipient'] !== undefined ? { cancelableByRecipient: parseBoolParam(p['cancelableByRecipient']) } : {}),
          ...(p['transferableBySender'] !== undefined ? { transferableBySender: parseBoolParam(p['transferableBySender'], true) } : {}),
          ...(p['transferableByRecipient'] !== undefined ? { transferableByRecipient: parseBoolParam(p['transferableByRecipient']) } : {}),
          ...(p['automaticWithdrawal'] !== undefined ? { automaticWithdrawal: parseBoolParam(p['automaticWithdrawal']) } : {}),
          ...(p['withdrawalFrequency'] ? { withdrawalFrequency: parseInt(p['withdrawalFrequency']) } : {}),
          ...(p['partner'] ? { partner: p['partner'] } : {}),
          ...(p['isNative'] !== undefined ? { isNative: parseBoolParam(p['isNative']) } : {}),
        };
      case 'streamflow_create_multiple':
        return {
          recipients: (() => {
            const r = p['recipients'];
            if (Array.isArray(r)) return r;
            if (typeof r === 'string' && r.startsWith('[')) {
              try { return JSON.parse(r); } catch { return []; }
            }
            return [];
          })(),
          mint: p['mint'],
          period: p['period'] ? parseInt(p['period']) : 86400,
          ...(p['start'] ? { start: parseInt(p['start']) } : {}),
          ...(p['cliff'] ? { cliff: parseInt(p['cliff']) } : {}),
          ...(p['canTopup'] !== undefined ? { canTopup: parseBoolParam(p['canTopup']) } : {}),
          ...(p['cancelableBySender'] !== undefined ? { cancelableBySender: parseBoolParam(p['cancelableBySender'], true) } : {}),
          ...(p['cancelableByRecipient'] !== undefined ? { cancelableByRecipient: parseBoolParam(p['cancelableByRecipient']) } : {}),
          ...(p['transferableBySender'] !== undefined ? { transferableBySender: parseBoolParam(p['transferableBySender'], true) } : {}),
          ...(p['transferableByRecipient'] !== undefined ? { transferableByRecipient: parseBoolParam(p['transferableByRecipient']) } : {}),
          ...(p['automaticWithdrawal'] !== undefined ? { automaticWithdrawal: parseBoolParam(p['automaticWithdrawal']) } : {}),
          ...(p['withdrawalFrequency'] ? { withdrawalFrequency: parseInt(p['withdrawalFrequency']) } : {}),
          ...(p['partner'] ? { partner: p['partner'] } : {}),
          ...(p['isNative'] !== undefined ? { isNative: parseBoolParam(p['isNative']) } : {}),
        };
      case 'streamflow_cancel':
        return { streamId: p['streamId'] };
      case 'streamflow_withdraw':
        return {
          streamId: p['streamId'],
          ...(p['amount'] ? { amount: p['amount'] } : {}),
        };
      case 'streamflow_transfer':
        return { streamId: p['streamId'], newRecipient: p['newRecipient'] };
      case 'streamflow_topup':
        return { streamId: p['streamId'], amount: p['amount'] };
      case 'streamflow_update':
        return {
          streamId: p['streamId'],
          ...(p['automaticWithdrawal'] !== undefined ? { automaticWithdrawal: parseBoolParam(p['automaticWithdrawal']) } : {}),
          ...(p['withdrawalFrequency'] ? { withdrawalFrequency: parseInt(p['withdrawalFrequency']) } : {}),
          ...(p['transferableBySender'] !== undefined ? { transferableBySender: parseBoolParam(p['transferableBySender'], true) } : {}),
          ...(p['transferableByRecipient'] !== undefined ? { transferableByRecipient: parseBoolParam(p['transferableByRecipient']) } : {}),
        };

      // ── Meteora DAMM v1 ───────────────────────────────────────────────────────
      case 'meteora_dammv1_swap':
        return {
          inputMint: p['inputMint'],
          outputMint: p['outputMint'],
          amount: p['amount'],
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
          ...(p['pool'] ? { pool: p['pool'] } : {}),
        };
      case 'meteora_dammv1_deposit':
        return {
          pool: p['pool'] ?? p['poolId'],
          tokenAAmount: p['tokenAAmount'] ?? p['amountA'],
          tokenBAmount: p['tokenBAmount'] ?? p['amountB'],
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
        };
      case 'meteora_dammv1_withdraw':
        return {
          pool: p['pool'] ?? p['poolId'],
          lpAmount: p['lpAmount'] ?? p['amount'],
          ...(p['minAAmount'] ? { minAAmount: p['minAAmount'] } : {}),
          ...(p['minBAmount'] ? { minBAmount: p['minBAmount'] } : {}),
        };

      // ── Meteora DAMM v2 ───────────────────────────────────────────────────────
      // Built by @meteora-ag/cp-amm-sdk in the TS service. The pool decides the
      // output side of a swap and the ratio of a deposit, so neither is a
      // parameter here — sending an outputMint or a second amount would only
      // give the SDK something to disagree with.
      case 'meteora_dammv2_swap':
        return {
          pool: p['pool'] ?? p['poolId'],
          inputMint: p['inputMint'] ?? p['tokenA'],
          amount: p['amount'] ?? p['amountA'] ?? p['amountX'],
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
        };
      case 'meteora_dammv2_add_liquidity':
        return {
          pool: p['pool'] ?? p['poolId'],
          // One side is enough; the other follows from the pool's ratio.
          amountX: p['amountX'] ?? p['amountA'] ?? '0',
          amountY: p['amountY'] ?? p['amountB'] ?? '0',
          ...(p['position'] ? { position: p['position'] } : {}),
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
        };
      case 'meteora_dammv2_remove_liquidity':
        return {
          position: p['position'] ?? p['positionId'],
          ...(p['bpsToRemove'] ? { bpsToRemove: parseInt(p['bpsToRemove']) } : {}),
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
        };
      case 'meteora_dammv2_claim_fee':
      case 'meteora_dammv2_close_position':
        return { position: p['position'] ?? p['positionId'] };

      // ── Meteora Dynamic Vault ─────────────────────────────────────────────────
      case 'meteora_vault_deposit':
        return {
          tokenMint: p['tokenMint'] ?? p['mint'] ?? p['token'],
          amount: p['amount'],
        };
      case 'meteora_vault_withdraw':
        return {
          tokenMint: p['tokenMint'] ?? p['mint'] ?? p['token'],
          unmintAmount: p['unmintAmount'] ?? p['lpAmount'] ?? p['amount'],
        };

      // ── Meteora Stake-to-Earn (m3m3) ──────────────────────────────────────────
      case 'meteora_s2e_stake':
        return { vault: p['vault'], amount: p['amount'] };
      case 'meteora_s2e_unstake':
        return { vault: p['vault'], amount: p['amount'] };
      case 'meteora_s2e_claim_fee':
        return {
          vault: p['vault'],
          ...(p['maxAmount'] ? { maxAmount: p['maxAmount'] } : {}),
        };
      case 'meteora_s2e_cancel_unstake':
        return { vault: p['vault'], escrow: p['escrow'] };
      case 'meteora_s2e_withdraw':
        return { vault: p['vault'], escrow: p['escrow'] };

      // ── Solend lending protocol ───────────────────────────────────────────────
      case 'solend_deposit':
      case 'solend_withdraw':
      case 'solend_borrow':
      case 'solend_repay':
        return {
          token: p['token'] ?? p['mint'],
          amount: p['amount'],
        };
      case 'solend_add_collateral':
      case 'solend_withdraw_collateral':
        return {
          token: p['token'] ?? p['mint'],
          amount: p['amount'] ?? p['collateral'],
        };

      // ── Kamino data query normalisations ─────────────────────────────────────
      case 'kamino_loan_detail':
        // Rust uses `loan` (not `obligation`) as the required field name
        return { loan: p['loan'] ?? p['obligation'], ...(p['market'] ? { market: p['market'] } : {}) };
      case 'kamino_vaults':
        return { ...(p['limit'] ? { limit: parseInt(p['limit'] as string) } : {}) };

      // ── Kamino KSwap ──────────────────────────────────────────────────────────
      case 'kamino_kswap':
        return {
          tokenIn: p['tokenIn'],
          tokenOut: p['tokenOut'],
          amountIn: p['amountIn'],
          maxSlippageBps: p['maxSlippageBps'] ? parseInt(p['maxSlippageBps']) : 50,
          ...(p['includeSetupIxs'] !== undefined ? { includeSetupIxs: parseBoolParam(p['includeSetupIxs']) } : {}),
          ...(p['wrapAndUnwrapSol'] !== undefined ? { wrapAndUnwrapSol: parseBoolParam(p['wrapAndUnwrapSol']) } : {}),
        };

      // ── Cross-chain bridges ───────────────────────────────────────────────────
      case 'debridge':
        return {
          originChainId: p['originChainId'] ? parseInt(p['originChainId']) : 7565164,
          destinationChainId: p['destinationChainId'] ? parseInt(p['destinationChainId']) : 1,
          originCurrency: p['originCurrency'] ?? p['fromToken'] ?? p['token'],
          destinationCurrency: p['destinationCurrency'] ?? p['toToken'],
          amount: p['amount'],
          ...(p['recipient'] ? { recipient: p['recipient'] } : {}),
          ...(p['slippageBps'] ? { slippageBps: parseInt(p['slippageBps']) } : {}),
        };
      case 'squid':
      case 'squid_bridge': {
        const squidParseArr = (v: unknown): string[] | undefined => {
          if (Array.isArray(v)) return v.map(String);
          if (typeof v === 'string' && v.trim()) return v.split(',').map(s => s.trim()).filter(Boolean);
          return undefined;
        };
        const squidPrefer = squidParseArr(p['prefer']);
        const squidBypass = squidParseArr(p['bypass']);
        return {
          originChainId: p['originChainId'] ? parseInt(p['originChainId']) : 0,
          destinationChainId: p['destinationChainId'] ? parseInt(p['destinationChainId']) : 1,
          originToken: p['originToken'] ?? p['fromToken'] ?? p['token'],
          destinationToken: p['destinationToken'] ?? p['toToken'],
          amount: p['amount'],
          ...(p['recipient'] ? { recipient: p['recipient'] } : {}),
          ...(p['slippage'] ? { slippage: parseFloat(p['slippage']) } : {}),
          ...(p['enableExpress'] !== undefined ? { enableExpress: parseBoolParam(p['enableExpress']) } : {}),
          ...(p['receiveGasOnDestination'] !== undefined ? { receiveGasOnDestination: parseBoolParam(p['receiveGasOnDestination']) } : {}),
          ...(p['enableBoost'] !== undefined ? { enableBoost: parseBoolParam(p['enableBoost']) } : {}),
          ...(p['quoteOnly'] !== undefined ? { quoteOnly: parseBoolParam(p['quoteOnly']) } : {}),
          ...(squidPrefer ? { prefer: squidPrefer } : {}),
          ...(squidBypass ? { bypass: squidBypass } : {}),
          ...(p['postHook'] !== undefined ? { postHook: p['postHook'] } : {}),
        };
      }
      case 'relay_bridge':
        return {
          originChainId: p['originChainId'] ? parseInt(p['originChainId']) : 900,
          destinationChainId: p['destinationChainId'] ? parseInt(p['destinationChainId']) : 1,
          originCurrency: p['originCurrency'] ?? p['fromToken'] ?? p['token'],
          destinationCurrency: p['destinationCurrency'] ?? p['toToken'],
          amount: p['amount'],
          tradeType: p['tradeType'] ?? 'EXACT_INPUT',
          ...(p['recipient'] ? { recipient: p['recipient'] } : {}),
          ...(p['refundTo'] ? { refundTo: p['refundTo'] } : {}),
          ...(p['refundType'] ? { refundType: p['refundType'] } : {}),
          ...(p['slippageTolerance'] ? { slippageTolerance: parseInt(p['slippageTolerance']) } : {}),
          ...(p['topupGas'] !== undefined ? { topupGas: parseBoolParam(p['topupGas']) } : {}),
          ...(p['topupGasAmount'] ? { topupGasAmount: String(p['topupGasAmount']) } : {}),
          ...(p['subsidizeFees'] !== undefined ? { subsidizeFees: parseBoolParam(p['subsidizeFees']) } : {}),
          ...(p['referrer'] ? { referrer: String(p['referrer']) } : {}),
          ...(p['referrerAddress'] ? { referrerAddress: String(p['referrerAddress']) } : {}),
          ...(p['useDepositAddress'] !== undefined ? { useDepositAddress: parseBoolParam(p['useDepositAddress']) } : {}),
          ...(p['disableOriginSwaps'] !== undefined ? { disableOriginSwaps: parseBoolParam(p['disableOriginSwaps']) } : {}),
          ...(p['forceSolverExecution'] !== undefined ? { forceSolverExecution: parseBoolParam(p['forceSolverExecution']) } : {}),
          ...(p['fixedRate'] !== undefined ? { fixedRate: parseBoolParam(p['fixedRate']) } : {}),
          ...(p['strict'] !== undefined ? { strict: parseBoolParam(p['strict']) } : {}),
          ...(p['maxRouteLength'] ? { maxRouteLength: parseInt(p['maxRouteLength']) } : {}),
          ...(p['overridePriceImpact'] !== undefined ? { overridePriceImpact: parseBoolParam(p['overridePriceImpact']) } : {}),
          ...(p['includeComputeUnitLimit'] !== undefined ? { includeComputeUnitLimit: parseBoolParam(p['includeComputeUnitLimit']) } : {}),
        };
      // ── Jupiter data queries — limit must be int not string for Rust u32 ────
      case 'jup_price':
        return { tokens: p['tokens'] ?? p['ids'] ?? p['token'] };
      case 'jup_token_search':
        return {
          query: p['query'] ?? p['q'] ?? p['search'],
          ...(p['limit'] ? { limit: parseInt(p['limit'] as string) } : {}),
        };
      case 'jup_tokens_tag':
        return {
          tag: p['tag'],
          ...(p['limit'] ? { limit: parseInt(p['limit'] as string) } : {}),
        };
      case 'jup_tokens_recent':
        return { ...(p['limit'] ? { limit: parseInt(p['limit'] as string) } : {}) };
      case 'jup_tokens_trending':
        return {
          ...(p['category'] ? { category: p['category'] } : {}),
          ...(p['interval'] ? { interval: p['interval'] } : {}),
          ...(p['limit'] ? { limit: parseInt(p['limit'] as string) } : {}),
        };
      case 'jup_dca_orders':
        return {
          ...(p['status'] ? { status: p['status'] } : {}),
          ...(p['wallet'] ? { wallet: p['wallet'] } : {}),
          ...(p['inputToken'] ? { inputToken: p['inputToken'] } : {}),
          ...(p['outputToken'] ? { outputToken: p['outputToken'] } : {}),
        };
      case 'jup_limit_orders':
        return {
          ...(p['status'] ? { status: p['status'] } : {}),
          ...(p['wallet'] ? { wallet: p['wallet'] } : {}),
          ...(p['inputToken'] ? { inputToken: p['inputToken'] } : {}),
          ...(p['outputToken'] ? { outputToken: p['outputToken'] } : {}),
        };
      case 'jup_pending_invites':
        return {
          ...(p['wallet'] ? { wallet: p['wallet'] } : {}),
          ...(p['page'] ? { page: parseInt(p['page'] as string) } : {}),
        };
      // ── Meteora data queries — page/pageSize/startTime/endTime must be int ──
      case 'meteora_dlmm_get_pairs':
      case 'meteora_dlmm_get_pool_groups':
      case 'meteora_dlmm_get_pool_group':
      case 'meteora_dammv2_get_pools':
      case 'meteora_dammv2_get_pool_groups':
      case 'meteora_dammv2_get_pool_group':
      case 'meteora_dammv1_get_pools':
      case 'meteora_dammv1_get_pool_configs':
        return {
          ...p,
          ...(p['page'] ? { page: parseInt(p['page'] as string) } : {}),
          ...(p['pageSize'] ? { pageSize: parseInt(p['pageSize'] as string) } : {}),
        };
      case 'meteora_dammv1_search_pools':
        return {
          ...p,
          page: p['page'] !== undefined ? parseInt(p['page'] as string) : 0,
          size: p['size'] ? parseInt(p['size'] as string) : (p['pageSize'] ? parseInt(p['pageSize'] as string) : 10),
        };
      case 'meteora_dlmm_get_pool_ohlcv':
      case 'meteora_dlmm_get_pool_volume_history':
      case 'meteora_dammv2_get_pool_ohlcv':
      case 'meteora_dammv2_get_pool_volume_history':
        return {
          ...p,
          ...(p['startTime'] ? { startTime: parseInt(p['startTime'] as string) } : {}),
          ...(p['endTime'] ? { endTime: parseInt(p['endTime'] as string) } : {}),
        };
      // ── SNS subdomain operations ───────────────────────────────────────────
      case 'sns_subdomains':
        return {
          parentDomain: (p['parentDomain'] ?? p['domain'] ?? p['parent'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
        };
      case 'sns_transfer_subdomain':
        return {
          subdomain: (p['subdomain'] as string)?.replace(/\.sol$/i, '').toLowerCase(),
          newOwner: p['newOwner'],
          ...(p['parentOwnerSigns'] !== undefined ? { parentOwnerSigns: parseBoolParam(p['parentOwnerSigns']) } : {}),
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

  /** Base64-encode raw bytes (chunked to stay under the arg-count limit of
   *  String.fromCharCode for large transactions). */
  private uint8ArrayToBase64(bytes: Uint8Array): string {
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  /**
   * Detect whether serialized tx bytes are a VersionedTransaction (v0+) vs a
   * legacy Transaction, without deserializing. Layout: [compact-u16 sigCount]
   * [sigCount × 64-byte sigs][message…]. In a versioned tx the first message
   * byte has its high bit set (0x80 | version); a legacy message starts with
   * `numRequiredSignatures` (always < 0x80). sigCount is always small, so its
   * compact-u16 fits in one byte.
   */
  private isVersionedTxBytes(bytes: Uint8Array): boolean {
    if (bytes.length < 1) return false;
    const sigCount = bytes[0];
    if (sigCount >= 0x80) return false; // multi-byte compact-u16 → not a normal tx
    const msgOffset = 1 + sigCount * 64;
    if (msgOffset >= bytes.length) return false;
    return (bytes[msgOffset] & 0x80) !== 0;
  }
}
