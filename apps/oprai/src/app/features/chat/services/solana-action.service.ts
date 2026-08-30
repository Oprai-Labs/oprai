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
import { Keypair, PublicKey } from '@solana/web3.js';
import type { Transaction, VersionedTransaction } from '@solana/web3.js';

export interface ValidatorInfo {
  voteAccount: string;
  commission: number;
  activatedStakeSol: number;
  /** Absent when nobody has measured it — the card then shows no yield rather
   *  than the constant-times-commission figure it used to print as fact. */
  apyEstimatePct?: number;
  epochCreditsRecent: number;
  name?: string;
  icon?: string;
  uptimePct?: number;
  /** The MEV share of `apyEstimatePct`, when there is one. */
  jitoApyPct?: number;
  isJito?: boolean;
}

/** A Marinade delayed-unstake ticket waiting to be claimed. */
export interface MarinadeTicket {
  address: string;
  solAmount: number;
  createdEpoch: number;
  claimableEpoch: number;
  claimable: boolean;
}

/** One of the user's own stake accounts, as the picker needs it. */
export interface StakeAccountInfo {
  address: string;
  solAmount: number;
  /** 'active' | 'activating' | 'deactivating' | 'inactive' */
  state: string;
  voteAccount: string | null;
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
  preview?: {
    description?: string;
    params?: Record<string, unknown>;
    /**
     * What this action takes from the wallet, as the backend measured it by
     * simulating the transaction it built — e.g. "~0.0101 SOL". Declared here
     * because the affordability check reads it: the backend has always sent
     * it, and the type omitting it is why the check could only see the
     * network fee.
     */
    estimatedFee?: string;
  };
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
//   Legacy: solend, marinade, bonkfun, magic_eden (all use Transaction::new_unsigned)
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

/**
 * Actions whose transaction needs a second signer that is not the user — a
 * token mint, a position NFT, a stake account — mapped to the build param the
 * backend expects its public key under.
 *
 * The key is generated HERE, in the browser, and its signature is added AFTER
 * the wallet has signed. Phantom's guidance for multi-signer transactions is
 * to sign with the wallet first and collect the rest afterwards; a transaction
 * that reaches the wallet already carrying a stranger's signature is a shape
 * its scanner cannot vouch for. Generating server-side and pre-signing — what
 * every one of these used to do — makes that order impossible.
 *
 * A type absent from this map still works: the backend falls back to
 * generating and pre-signing the key itself. That fallback is the old
 * behaviour, kept only so a stale client keeps working, and every entry added
 * here retires one more of them.
 */
const EPHEMERAL_SIGNER_PARAM: Record<string, string> = {
  launch_token: 'mintPubkey',
  pumpfun_launch: 'mintPubkey',
  raydium_open_position: 'positionNftMint',
  // Opening a DAMM v2 position mints its own NFT. Adding to an EXISTING
  // position does not — the backend only reads the key on the open path, so
  // sending it on an add is harmless and the type is one action either way.
  meteora_dammv2_add_liquidity: 'positionNftMint',
  // A new stake account signs its own creation.
  native_stake: 'stakeAccountPubkey',
  native_stake_split: 'newStakeAccountPubkey',
  // Only the non-instant unstake creates an account; the instant path pays
  // out from the reserve and the backend ignores the key it never needs.
  unstake: 'stakeAccountPubkey',
};

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

/**
 * First value that is actually a number, as a string.
 *
 * These amounts were selected with `p['amountX'] ?? p['amountA'] ?? '0'`, and
 * `??` only steps past null and undefined. An `amountX` holding `""` or a
 * surviving `50%` sentinel therefore OUTRANKED the real number sitting in
 * `amountA` and went to the backend, which parses it as f64 and rejects the
 * build — surfacing to the user as a generic "something went wrong".
 * A value that is not a number is not an answer; fall through to one that is.
 */
function firstNumericParam(...vals: Array<string | undefined>): string {
  for (const v of vals) {
    if (v === undefined || v === null) continue;
    const s = String(v).trim();
    if (s === '') continue;
    if (Number.isFinite(Number(s))) return s;
  }
  return '0';
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
  /**
   * Follow a bridge to the chain it is going to.
   *
   * A bridge has two halves and the origin transaction is only the first. It
   * can land perfectly and the funds still never arrive — the solver fails, the
   * route is refunded, the destination fill reverts — so treating the deposit
   * as the outcome reports success for a bridge that did not happen. Relay
   * tracks the whole intent; this asks it until it has an answer.
   *
   * Silence is not failure. If it never resolves, the card stays submitted with
   * its explorer link rather than being told either result, which is the same
   * rule the Solana watcher follows when the chain has no answer.
   */
  private async watchRelayIntent(
    requestId: string,
    callbacks: ActionCallbacks,
    originTx: string,
  ): Promise<void> {
    const deadline = Date.now() + 10 * 60_000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 4_000));
      let status: { status?: string; details?: string } | null = null;
      try {
        status = await firstValueFrom(
          this.api.get<any>('/actions/relay/intent-status', { requestId }),
        );
      } catch {
        continue; // A failed poll says nothing about the bridge.
      }
      switch ((status?.status ?? '').toLowerCase()) {
        case 'success':
          // Book the trade's economics so it feeds per-chain rewards. The server
          // re-verifies the fill and takes the amounts from Relay itself, so this
          // is fire-and-forget — a failed record must never block the receipt.
          this.api.post('/actions/relay/record', { requestId }).subscribe({
            error: () => { /* recorded best-effort; server is authoritative */ },
          });
          callbacks.onConfirm?.(originTx);
          return;
        case 'refunded':
          callbacks.onFail?.(
            'The bridge could not be completed, so the funds were returned on the chain they left from.',
            originTx,
          );
          return;
        case 'failure':
          callbacks.onFail?.(
            'The bridge did not complete. The funds are either back on the origin chain or recoverable through Relay — nothing further was signed.',
            originTx,
          );
          return;
        default:
          break; // waiting, depositing, pending, submitted, delayed
      }
    }
    console.warn('[relay] intent unresolved after 10 minutes', requestId);
  }

  /**
   * Make the bridge's completion mean the far side, not the near one.
   *
   * Wrapping rather than editing each path: the EVM origin finishes in the
   * cross-chain branch and the Solana origin finishes through `watchSettlement`
   * like every other action, and both used to call `onConfirm` the moment the
   * origin transaction was accepted. One wrapper puts the same, later question
   * in front of both.
   */
  /**
   * Wait for a same-chain EVM swap's own transaction to settle, polling the
   * connected provider for the receipt. Returns true on a mined success (status
   * 0x1), false on a revert (0x0), and null if the receipt never appears within
   * the window — in which case the caller treats it like the Solana watcher's
   * "no answer": confirm optimistically rather than claim a failure that may not
   * have happened. Same-chain fills mine in seconds; the long deadline only
   * covers a congested block.
   */
  private async watchEvmReceipt(
    provider: { request: (a: { method: string; params?: unknown[] }) => Promise<any> },
    txHash: string,
  ): Promise<boolean | null> {
    const deadline = Date.now() + 3 * 60_000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 3_000));
      let receipt: { status?: string } | null = null;
      try {
        receipt = await provider.request({
          method: 'eth_getTransactionReceipt',
          params: [txHash],
        });
      } catch {
        continue; // A failed poll says nothing about the transaction.
      }
      if (!receipt) continue; // Not mined yet.
      // status is a hex quantity: 0x1 success, 0x0 revert.
      const status = String(receipt.status ?? '').toLowerCase();
      if (status === '0x1' || status === '0x01') return true;
      if (status === '0x0' || status === '0x00') return false;
      return true; // Mined but no status field (pre-Byzantium shape) — treat as landed.
    }
    console.warn('[relay] same-chain receipt unresolved', txHash);
    return null;
  }

  private wrapRelaySettlement(callbacks: ActionCallbacks, requestId: string): ActionCallbacks {
    let originTx = '';
    return {
      ...callbacks,
      onSubmit: (sig: string) => { originTx = sig; callbacks.onSubmit?.(sig); },
      onConfirm: (result?: string) => {
        void this.watchRelayIntent(requestId, callbacks, originTx || result || '');
      },
    };
  }

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

  /**
   * Marinade tickets this wallet is owed SOL on.
   *
   * `marinade_claim_ticket` asks for a ticket pubkey — an address the user
   * never sees, handed back by a delayed unstake days earlier and gone from
   * the chat by the time it matures. Read from chain by beneficiary, which is
   * the field the program itself checks.
   *
   * TicketAccountData: discriminant(8) state(32) beneficiary(32)
   * lamports(8) createdEpoch(8) = 88 bytes.
   */
  async getMarinadeTickets(owner: string): Promise<MarinadeTicket[]> {
    try {
      const conn = createSolanaConnection('confirmed');
      const [accounts, epochInfo] = await Promise.all([
        conn.getProgramAccounts(new PublicKey('MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD'), {
          filters: [{ dataSize: 88 }, { memcmp: { offset: 40, bytes: owner } }],
        }),
        conn.getEpochInfo(),
      ]);
      return accounts
        .map(a => {
          const d = a.account.data;
          const lamports = Number(d.readBigUInt64LE(72));
          const createdEpoch = Number(d.readBigUInt64LE(80));
          // Marinade releases the SOL once the epoch after the one it was
          // ordered in has ended.
          const claimableEpoch = createdEpoch + 2;
          return {
            address: a.pubkey.toBase58(),
            solAmount: lamports / 1e9,
            createdEpoch,
            claimableEpoch,
            claimable: epochInfo.epoch >= claimableEpoch,
          };
        })
        .sort((a, b) => Number(b.claimable) - Number(a.claimable) || b.solAmount - a.solAmount);
    } catch {
      return [];
    }
  }

  /**
   * The stake accounts this wallet can actually act on.
   *
   * Every stake action except the first one — deactivate, withdraw, split,
   * merge — asked the user to paste a stake account address. Nobody knows
   * their stake account addresses; they are derived, never chosen, and never
   * shown anywhere else. Read straight from chain by withdraw authority, which
   * is what the program checks when the transaction lands.
   */
  async getStakeAccounts(owner: string): Promise<StakeAccountInfo[]> {
    try {
      const conn = createSolanaConnection('confirmed');
      const accounts = await conn.getParsedProgramAccounts(
        new PublicKey('Stake11111111111111111111111111111111111111'),
        {
          filters: [
            { dataSize: 200 },
            // Withdraw authority lives at offset 44 of a stake account.
            { memcmp: { offset: 44, bytes: owner } },
          ],
        },
      );
      const rows: StakeAccountInfo[] = [];
      for (const a of accounts) {
        const info = (a.account.data as any)?.parsed?.info;
        const delegation = info?.stake?.delegation;
        rows.push({
          address: a.pubkey.toBase58(),
          solAmount: (a.account.lamports ?? 0) / 1e9,
          state: (a.account.data as any)?.parsed?.type === 'delegated'
            ? (delegation?.deactivationEpoch && delegation.deactivationEpoch !== '18446744073709551615'
                ? 'deactivating' : 'active')
            : 'inactive',
          voteAccount: delegation?.voter ?? null,
        });
      }
      return rows.sort((a, b) => b.solAmount - a.solAmount);
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
  /** A bridge whose ORIGIN is an EVM chain (signed with window.ethereum, no
   *  Solana wallet needed). Origin is EVM when it isn't one of Relay's Solana
   *  chain ids. Accepts a chain NAME too (the LLM may pass "ethereum"). */
  private bridgeOriginIsEvm(p: Record<string, string>): boolean {
    // A generic `bridge` action carries the origin as a chain NAME in fromChain
    // (it's remapped to a numeric originChainId only later, AFTER this guard runs),
    // so fall back to it — otherwise every EVM-origin `bridge` looked Solana and
    // an EVM-only session was wrongly told to "connect a Solana wallet".
    const raw = String(p['originChainId'] ?? p['fromChain'] ?? '').toLowerCase().trim();
    if (!raw) return false;
    // 7565164 is the Squid/deBridge Solana chain id; 'sol' the shorthand name.
    const solana = new Set(['792703809', '900', '7565164', 'solana', 'sol', '11111111111111111111111111111111']);
    if (solana.has(raw)) return false;
    const n = Number(raw);
    // A numeric non-Solana chain id, or a non-Solana chain name → EVM origin.
    return Number.isFinite(n) ? n !== 0 : raw.length > 0;
  }

  async execute(
    action: ParsedAction,
    callbacks: ActionCallbacks = {}
  ): Promise<string> {
    // Uniswap same-chain EVM swaps run entirely on the EVM side (window.ethereum),
    // so they must NOT hit the Solana-wallet guard below — route them out first.
    if (action.type === 'uniswap_swap') {
      return this.executeUniswapSwap(action, callbacks);
    }
    if (action.type === 'uniswap_add_liquidity') {
      return this.executeUniswapAddLiquidity(action, callbacks);
    }
    // pools.trade launchpad native buy/sell/launch — EVM (Robinhood chain 4663),
    // no Solana wallet, so route out before the Solana guard.
    if (action.type === 'pools_buy' || action.type === 'pools_sell' || action.type === 'pools_launch'
        || action.type === 'pons_buy' || action.type === 'pons_sell' || action.type === 'pons_launch') {
      return this.executePoolsTrade(action, callbacks);
    }
    // Lighter perps (Robinhood Chain Lighter domain). Onboarding is a single EVM
    // personal_sign; the trades themselves are gas-free (backend signs with the
    // delegated agent key), so there is NO Solana wallet and NO per-trade EVM tx.
    // Route them all out before the Solana-wallet guard.
    if (action.type === 'lighter_onboard') {
      return this.executeLighterOnboard(action, callbacks);
    }
    if (action.type === 'lighter_deposit') {
      return this.executeLighterDeposit(action, callbacks);
    }
    if (action.type === 'lighter_open' || action.type === 'lighter_close' || action.type === 'lighter_leverage'
        || action.type === 'lighter_withdraw') {
      return this.executeLighterPerp(action, callbacks);
    }
    // Morpho Blue lending — EVM (Robinhood Chain 4663). Backend returns unsigned
    // txs (approval? + the Morpho call); the user's own wallet signs. No Solana
    // wallet, so route out before the Solana-wallet guard.
    if (action.type.startsWith('morpho_')) {
      return this.executeMorpho(action, callbacks);
    }
    // SushiSwap — EVM (Robinhood Chain 4663). Swap + V3 add-liquidity; backend
    // returns unsigned txs (approval? + the call). Route out before the Solana guard.
    if (action.type === 'sushi_swap' || action.type === 'sushi_add_liquidity') {
      return this.executeSushi(action, callbacks);
    }
    // OpenSea NFT marketplace — EVM (Robinhood Chain 4663). Buy/accept-offer are
    // unsigned Seaport txs; list/make-offer are gasless EIP-712 signed orders.
    if (action.type === 'opensea_buy' || action.type === 'opensea_accept_offer') {
      return this.executeOpenseaFulfill(action, callbacks);
    }
    if (action.type === 'opensea_list' || action.type === 'opensea_make_offer') {
      return this.executeOpenseaOrder(action, callbacks);
    }

    const wallet = this.walletService.publicKey();
    // A cross-chain bridge FROM an EVM chain (e.g. Ethereum → Robinhood) is
    // signed with the connected EVM wallet (window.ethereum), not Solana — so it
    // must not hit the Solana-wallet guard. Only a Solana-ORIGIN bridge needs a
    // Solana wallet.
    const isEvmOriginBridge =
      (action.type === 'relay_bridge' || action.type === 'cross_chain_swap' || action.type === 'bridge'
        || action.type === 'squid_bridge' || action.type === 'debridge')
      && this.bridgeOriginIsEvm(action.params);
    if (!wallet && !isEvmOriginBridge) {
      // The user may be signed in through an EVM (SIWE) session with no Solana
      // wallet connected — chat history and rewards work account-wide, but every
      // Solana action OPRAI executes settles on Solana and needs a Solana wallet
      // to sign. Without this, the builder was still called with the 0x address
      // and came back with a raw "Invalid wallet address" 400; say plainly what
      // to do instead. (String is a translation key — see i18n/translations.ts.)
      throw new Error('This action runs on Solana. Connect a Solana wallet to continue.');
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
    // When the AI generates [ACTION:lend] protocol=kamino, route to kamino_deposit, etc.
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
            // squidFromAddress is the SOLANA pubkey (empty for an EVM-only session);
            // an EVM-origin squid bridge sends from the EVM wallet, so prefer an
            // explicit fromAddress/sender and only fall back to the Solana one.
            fromAddress:        action.params['fromAddress'] ?? action.params['sender'] ?? squidFromAddress,
            // Carry through all optional Squid params if provided
            ...(action.params['sender']                  && { sender: action.params['sender'] }),
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
            // A fresh params object drops everything not listed — so carry through
            // the EVM sender/recipient the same way the direct relay_bridge path
            // does, or an EVM-origin bridge 400s with "needs an EVM wallet to send
            // from" (the sender never reached the build).
            ...(action.params['sender']     && { sender: action.params['sender'] }),
            ...(action.params['recipient']  && { recipient: action.params['recipient'] }),
            ...(action.params['refundTo']   && { refundTo: action.params['refundTo'] }),
            ...(action.params['refundType'] && { refundType: action.params['refundType'] }),
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
    const ephemeralParam = EPHEMERAL_SIGNER_PARAM[action.type];
    // A launch takes its key from the vanity pool (a real `…pump` address);
    // everything else just needs a throwaway.
    const ephemeralSigner = ephemeralParam
      ? (isLaunchAction ? await this.acquireLaunchMintKeypair() : Keypair.generate())
      : null;
    // Surface the new token's mint (contract) so the card can persist it into chat
    // history — enables later "sell this / sell HOOD4" to resolve the address.
    if (ephemeralSigner && isLaunchAction) callbacks.onMintGenerated?.(ephemeralSigner.publicKey.toBase58());

    const buildBody: Record<string, unknown> = {
      type: action.type,
      params: ephemeralSigner && ephemeralParam
        ? { ...this.normalizeParams(action), [ephemeralParam]: ephemeralSigner.publicKey.toBase58() }
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

    // A bridge is not finished when its first transaction is. Both origins go
    // through this from here on, so neither can report success early.
    const relayRawRequestId = buildResult.isCrossChain
      ? (buildResult.quote as any)?.requestId ?? (buildResult.quote as any)?.request_id
      : null;
    // A SAME-chain EVM swap has no far side to wait for: the origin transaction
    // IS the settlement, so Relay's cross-chain intent-status never reports
    // 'success' for it and the card spins forever. Detect it here and confirm on
    // the EVM receipt instead (below), rather than routing through the intent
    // watch. Origin is EVM when it isn't one of Relay's Solana chain ids.
    const SOLANA_RELAY_IDS = new Set([792703809, 900]);
    const originChainNum = Number(action.params['originChainId'] ?? 0);
    const destChainNum = Number(action.params['destinationChainId'] ?? 0);
    const sameChainEvm =
      originChainNum !== 0 &&
      originChainNum === destChainNum &&
      !SOLANA_RELAY_IDS.has(originChainNum);
    // Wrap with the intent watch only for a genuine cross-chain hop.
    const relayRequestId = sameChainEvm ? null : relayRawRequestId;
    if (relayRequestId) {
      callbacks = this.wrapRelaySettlement(callbacks, String(relayRequestId));
    }

    // Step 3a: Cross-chain swap — sign EVM transaction via window.ethereum.
    //
    // Unless the origin is Solana. Then the deposit is a Solana transaction,
    // the service has already built it, and it signs and settles like any
    // other — falling through to the EVM branch is how a complete quote ended
    // at "no transaction data returned from backend".
    if ((buildResult.isCrossChain || action.type === 'cross_chain_swap') && !buildResult.transaction) {
      const provider = action.params['provider'] ?? 'relay';
      const steps = buildResult.executionSteps ?? [];

      // For relay: use execution steps (existing behavior)
      // For wormhole/debridge: quote contains tx_data directly
      let txData: any = null;

      if (provider === 'relay' && steps.length > 0) {
        // Relay puts what to sign under `data`, not `transaction` — and on a
        // Solana origin that is a list of instructions, which the service has
        // already assembled into a transaction for us. Reaching here with one
        // in hand means this is an EVM origin.
        const depositStep = steps.find((s: any) => s.type === 'deposit' || s.kind === 'transaction' || s.items?.length);
        const item = depositStep?.items?.[0] as any;
        txData = item?.data ?? item?.transaction;
      } else if (provider === 'wormhole' || provider === 'debridge') {
        // Wormhole/Debridge: tx_data is in quote
        const quote = buildResult.quote;
        if (quote && typeof quote === 'object') {
          txData = (quote as any).txData || (quote as any).tx_data;
        }
      } else {
        // Fallback to relay behavior
        const depositStep = steps.find(s => s.type === 'deposit' || s.items?.length);
        const item = depositStep?.items?.[0] as any;
        txData = item?.data ?? item?.transaction;
      }

      if (!txData?.to) {
        throw new Error(`Cross-chain (${provider}): no transaction data returned from backend`);
      }
      const ethereum = await this.walletService.resolveEvmProvider();
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

      // The quote was priced for one specific address, and the wallet that
      // opens is whichever one holds window.ethereum with whichever account is
      // active. Sending from a different one spends the wrong wallet's money
      // on a route quoted for someone else.
      const quotedSender = String(action.params['sender'] ?? '').trim();
      if (quotedSender && quotedSender.toLowerCase() !== evmAccount.toLowerCase()) {
        throw new Error(
          `This bridge was priced for ${quotedSender.slice(0, 6)}…${quotedSender.slice(-4)}, but your wallet is offering ${evmAccount.slice(0, 6)}…${evmAccount.slice(-4)}. Switch accounts in the wallet, or reconnect the card to the one you want to send from.`
        );
      }

      // And on the right chain. A wallet signs for whatever network it happens
      // to be on without mentioning it, so an origin of BNB with the wallet
      // left on Ethereum would broadcast an unrelated transaction there.
      const originChain = Number(action.params['originChainId'] ?? 0);
      if (originChain) {
        const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
        if (onChain !== originChain) {
          try {
            await withTimeout(
              ethereum.request({
                method: 'wallet_switchEthereumChain',
                params: [{ chainId: `0x${originChain.toString(16)}` }],
              }),
              60_000,
              'network switch',
            );
          } catch {
            throw new Error(
              'Your wallet is on a different network than this bridge leaves from. Switch it and try again — nothing was signed.'
            );
          }
        }
      }
      callbacks.onSign?.();
      // Relay returns value/gas/fees as DECIMAL strings ("82839763666438"), but
      // eth_sendTransaction expects hex quantities. Passing the decimal verbatim
      // made the wallet read "82839763666438" as hex — a value ~440× too large —
      // which showed as a network-fee / insufficient-funds warning for a swap
      // that was actually a few cents. Also forward Relay's own gas + EIP-1559
      // fees so the wallet doesn't self-estimate (which warns on an L2 like
      // Robinhood whose gas oracle the wallet doesn't know).
      const toHexQty = (v: unknown): string | undefined => {
        if (v == null) return undefined;
        const s = String(v).trim();
        if (s === '') return undefined;
        if (/^0x[0-9a-fA-F]*$/.test(s)) return s; // already hex
        try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
      };
      const gasHex = toHexQty(txData.gas ?? txData.gasLimit);
      const maxFeeHex = toHexQty(txData.maxFeePerGas);
      const maxPrioHex = toHexQty(txData.maxPriorityFeePerGas);
      const evmTxHash = await withTimeout(
        ethereum.request({
          method: 'eth_sendTransaction',
          params: [{
            from: evmAccount,
            to: txData.to,
            data: txData.data ?? '0x',
            value: toHexQty(txData.value) ?? '0x0',
            ...(gasHex ? { gas: gasHex } : {}),
            // Pass 1559 fees only as a pair; a lone maxFeePerGas confuses wallets.
            ...(maxFeeHex && maxPrioHex ? { maxFeePerGas: maxFeeHex, maxPriorityFeePerGas: maxPrioHex } : {}),
          }],
        }),
        120_000,
        'transaction sign'
      );
      callbacks.onSubmit?.(evmTxHash as string);
      // A same-chain EVM swap finishes on THIS chain — there is no far side and
      // no cross-chain intent to poll (which is why the intent watch spun
      // forever). Wait for the origin transaction's own receipt: a mined success
      // is the settlement, a revert is a failure. A hash alone is neither.
      if (sameChainEvm) {
        const ok = await this.watchEvmReceipt(ethereum, evmTxHash as string);
        if (ok === false) {
          callbacks.onFail?.(
            'The swap transaction reverted on-chain — nothing was swapped. Your funds are safe minus the network fee.',
            evmTxHash as string,
          );
          return evmTxHash as string;
        }
        // Book economics best-effort (server re-verifies the fill from Relay).
        if (relayRawRequestId) {
          this.api.post('/actions/relay/record', { requestId: String(relayRawRequestId) }).subscribe({
            error: () => { /* recorded best-effort; server is authoritative */ },
          });
        }
        callbacks.onConfirm?.(evmTxHash as string);
        return evmTxHash as string;
      }
      // Wrapped for a cross-chain bridge, so this hands off to the intent watch
      // rather than declaring the bridge done. A hash is a receipt for the
      // deposit, not for the arrival.
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
    //
    // Jupiter Perps are EXEMPT: their tx is pre-built by Jupiter (its own compute
    // budget + lookup table) and must be signed exactly as-built, then handed
    // UNCHANGED to /actions/perp-execute for the keeper signatures. Rewriting it
    // through helius_smart_send corrupts the versioned tx, so the wallet fails to
    // sign it (JSON-RPC -32603) or Jupiter's execute rejects it.
    const HELIUS_EXEMPT = new Set(['perp_open', 'perp_close']);
    if (this.heliusOptimizationEnabled && buildResult.transaction && !HELIUS_EXEMPT.has(action.type)) {
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

    // WYSIWYS: for a plain SOL transfer, the address receiving the LARGEST amount
    // in the tx we're about to sign must be the recipient the card showed —
    // otherwise a swapped-out tx could redirect the funds. Fails OPEN on anything
    // it can't cleanly decode (SPL/ATA, unusual shapes) so it never blocks a
    // legitimate action; it only stops a clear redirection.
    this.verifyTransferWysiwys(deserializedTx, isVersioned, action, web3);

    // The final step of a sequential action was built before the earlier steps
    // ran, so its blockhash is as old as their confirmations — refresh it here
    // or the submit fails on expiry after the user has already signed.
    if (finalStepTx && isVersioned) {
      await this.refreshBlockhash(deserializedTx as VersionedTransaction, connection);
    }

    // Get the wallet's SOL balance and the exact tx fee side-by-side
    const [solBalance, feeResponse] = await Promise.all([
      connection.getBalance(new web3.PublicKey(wallet!)),
      isVersioned
        ? connection.getFeeForMessage((deserializedTx as VersionedTransaction).message).catch(() => ({ value: null }))
        : connection.getFeeForMessage(
            (deserializedTx as Transaction).compileMessage()
          ).catch(() => ({ value: null })),
    ]);

    // Determine the fee: use the exact fee from the RPC when available, otherwise a safe floor
    const networkFee = (feeResponse as { value: number | null }).value ?? 200_000;

    // The network fee is the small half of what an action costs. Opening a
    // position also pays rent on the accounts it creates — measured, 0.0101
    // SOL on Orca and 0.0474 on a DLMM pool against a network fee of about
    // 0.000005. Checking only the fee let a wallet through that could not
    // afford the action, and the failure then surfaced from the simulator as
    // "Not enough balance to complete this", with no figure and pointing at
    // the deposit, which was never the problem.
    //
    // The backend now measures the real cost by simulating what it built, so
    // check against that and name the number.
    // Most builders format the fee as SOL ("~0.0101 SOL"), but a few (Jupiter
    // perps / lend) return a BARE LAMPORTS integer ("15000"). Reading the latter
    // as SOL and multiplying by 1e9 turned a 15000-lamport fee into "15000 SOL"
    // and blocked a wallet that had ample SOL. Treat a value that carries a
    // "SOL" unit or a decimal point as SOL; a bare integer is already lamports.
    const quoted = buildResult.preview?.estimatedFee;
    const quotedRaw = typeof quoted === 'string' ? quoted : String(quoted ?? '');
    const quotedNum = parseFloat(quotedRaw.replace(/[^\d.]/g, ''));
    const quotedIsSol = /sol/i.test(quotedRaw) || quotedRaw.includes('.');
    const quotedLamports = Number.isFinite(quotedNum) && quotedNum > 0
      ? (quotedIsSol ? Math.ceil(quotedNum * 1e9) : Math.ceil(quotedNum))
      : 0;
    const estimatedFee = Math.max(networkFee, quotedLamports);

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
    // Step 3b (launch only): the browser-generated mint keypair signs, then the
    // wallet sends.
    //
    // `skipPreflight: true` does NOT stop the wallet simulating — it is an RPC
    // send option and has no bearing on Phantom's own scan, which runs before
    // the signature prompt either way. A comment here used to claim otherwise,
    // and that claim was load-bearing for nobody: it just meant we skipped the
    // node's preflight and lost the last chance to catch a transaction that
    // was going to revert. Kept true because a launch races snipers and the
    // extra round trip costs more than it saves, not because it hides anything
    // from the wallet.
    let signature: string;

    // The wallet signs FIRST, and any ephemeral key we generated signs after.
    //
    // This used to run the other way round for launches: sign with the mint,
    // then hand a transaction that already carried a stranger's signature to
    // `signAndSendTransaction`. Phantom's multi-signer guidance asks for the
    // opposite order, and `signAndSendTransaction` cannot support it at all —
    // it signs and submits in one step, leaving no window to add a second
    // signature. So a transaction with an ephemeral signer always takes the
    // `signTransaction` path, and we submit it ourselves.
    const signedTx = (await Promise.race([
      this.walletService.signTransaction(deserializedTx),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), SIGN_TIMEOUT_MS)
      ),
    ])) as {
      serialize(): Uint8Array;
      partialSign?: (...signers: Keypair[]) => void;
      sign?: (signers: Keypair[]) => void;
    };

    // Now the ephemeral key. How it signs depends on the transaction version:
    // a launch with a dev-buy is v0 — create and buy ride together so nobody
    // can snipe the gap — and `partialSign` does not exist there. Checking
    // only for `partialSign` would skip the signature in silence and the
    // chain would reject the transaction for one it never got.
    if (ephemeralSigner) {
      try {
        if (typeof signedTx.partialSign === 'function') {
          signedTx.partialSign(ephemeralSigner);
        } else if (typeof signedTx.sign === 'function') {
          signedTx.sign([ephemeralSigner]);
        }
      } catch {
        /* an older backend pre-signed with its own key — nothing to add */
      }
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

    // Launch: perform the initial dev-buy in the background after the create
    // tx confirms. Legacy — a launch with a dev-buy is one atomic transaction
    // now, so the backend stops returning `initialBuy` and this rarely fires;
    // kept so an older card mid-flight completes rather than losing its buy.
    // Not awaited.
    if (isLaunchAction) {
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
        kamino:   'kamino_deposit',
      },
      withdraw_lend: {
        kamino:   'kamino_withdraw',
      },
      borrow: {
        kamino:   'kamino_borrow',
      },
      repay: {
        kamino:   'kamino_repay',
      },
    };

    const remappedType = REMAP[action.type]?.[protocol];
    if (!remappedType) return action;

    // Normalize params: kamino uses `token`, solend uses `asset`
    const token = action.params['token'] ?? action.params['asset'] ?? action.params['bank'] ?? action.params['reserve'];
    const extraParams: Record<string, string> = {};
    if (protocol === 'kamino' && token) extraParams['token'] = token;
    if (protocol === 'solend' && token) extraParams['asset'] = token;

    return {
      ...action,
      type: remappedType,
      params: { ...action.params, ...extraParams },
    };
  }

  // ─── Frontend Action Dispatcher ────────────────────────────────────────────

  /**
   * Uniswap same-chain EVM swap (Phase 1). Multi-step because Uniswap uses
   * Permit2: quote → (ERC20 only) approve the token to Permit2 → sign the EIP-712
   * permit → get the swap calldata → send → watch → record. The Uniswap API key
   * lives in the backend, so quote/swap/record all go through our gateway. Native
   * (ETH) input skips the approval + permit steps entirely.
   */
  private async executeUniswapSwap(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const chainId = Number(p['originChainId'] ?? p['chainId'] ?? 0);
    if (!chainId) throw new Error('Uniswap: no chain specified for this swap.');

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) {
      throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to swap on Uniswap.');
    }
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> => {
      const timer = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error(`Uniswap: ${label} timed out after ${ms / 1000}s`)), ms),
      );
      return Promise.race([promise, timer]);
    };
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s; // already hex (Uniswap returns value in hex)
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(
      ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');

    // Make sure the wallet is on the swap's chain before anything is signed.
    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(
          ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }),
          60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network than this swap. Switch it and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    // 1. Quote (backend injects the API key + the swapper from the auth header).
    const q = await firstValueFrom(this.api.post<any>('/actions/uniswap/quote', {
      originChainId: chainId,
      destinationChainId: chainId,
      originCurrency: p['originCurrency'] ?? p['inputMint'] ?? p['tokenIn'] ?? p['fromToken'],
      destinationCurrency: p['destinationCurrency'] ?? p['outputMint'] ?? p['tokenOut'] ?? p['toToken'],
      amount: p['amount'],
      tradeType: p['tradeType'] ?? 'EXACT_INPUT',
      // The connected EVM wallet is the swapper (the OPRAI session may be Solana).
      sender: account,
      ...(p['slippageBps'] ? { slippageBps: p['slippageBps'] } : {}),
    }));

    // 2. Permit2 approval (ERC20 input only) — a one-time approve of the token to
    // the Permit2 contract. Must land before the permit signature is valid.
    if (q?.approval?.to) {
      const approvalHash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{ from: account, to: q.approval.to, data: q.approval.data ?? '0x', value: toHexQty(q.approval.value) ?? '0x0' }],
      }), 120_000, 'token approval');
      const ok = await this.watchEvmReceipt(ethereum, approvalHash as string);
      if (ok === false) throw new Error('The token approval reverted on-chain — nothing was swapped.');
    }

    // 3. Permit2 signature (ERC20 input only). eth_signTypedData_v4 needs the
    // EIP712Domain type, which Uniswap omits — add it from the domain's fields.
    let signature: string | undefined;
    if (q?.permitData?.domain) {
      const typedData = {
        domain: q.permitData.domain,
        types: { EIP712Domain: this.eip712DomainFields(q.permitData.domain), ...q.permitData.types },
        primaryType: 'PermitSingle',
        message: q.permitData.values,
      };
      signature = await withTimeout(
        ethereum.request({ method: 'eth_signTypedData_v4', params: [account, JSON.stringify(typedData)] }),
        120_000, 'permit sign') as string;
    }

    callbacks.onSign?.();
    // 4. Swap calldata (backend → Uniswap /swap with the signed permit).
    const swapResp = await firstValueFrom(this.api.post<any>('/actions/uniswap/swap', {
      quote: q.quote,
      permitData: q.permitData ?? null,
      signature: signature ?? null,
    }));
    const tx = swapResp?.transaction;
    if (!tx?.to) throw new Error('Uniswap: no swap transaction was returned.');

    // 5. Send the swap.
    const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
    const maxFeeHex = toHexQty(tx.maxFeePerGas);
    const maxPrioHex = toHexQty(tx.maxPriorityFeePerGas);
    const txHash = await withTimeout(ethereum.request({
      method: 'eth_sendTransaction',
      params: [{
        from: account,
        to: tx.to,
        data: tx.data ?? '0x',
        value: toHexQty(tx.value) ?? '0x0',
        ...(gasHex ? { gas: gasHex } : {}),
        ...(maxFeeHex && maxPrioHex ? { maxFeePerGas: maxFeeHex, maxPriorityFeePerGas: maxPrioHex } : {}),
      }],
    }), 120_000, 'swap sign') as string;
    callbacks.onSubmit?.(txHash);

    // 6. Watch this chain's receipt (same-chain: the mined tx IS the settlement).
    const ok = await this.watchEvmReceipt(ethereum, txHash);
    if (ok === false) {
      callbacks.onFail?.(
        'The swap reverted on-chain — nothing was swapped. Your funds are safe minus the network fee.',
        txHash,
      );
      return txHash;
    }
    // Book economics best-effort (server re-derives the USD notional itself).
    this.api.post('/actions/uniswap/record', {
      chainId,
      txHash,
      inputToken: q.input?.token,
      outputToken: q.output?.token,
      inputAmount: q.input?.amount,
      outputAmount: q.output?.amount,
      inputSymbol: q.input?.symbol,
      outputSymbol: q.output?.symbol,
    }).subscribe({ error: () => { /* best-effort; server is authoritative */ } });
    callbacks.onConfirm?.(txHash);
    return txHash;
  }

  // ── Lighter perps (Robinhood Chain Lighter domain) ──────────────────────────
  /** Resolve the connected EVM account (prompts a connect if needed). */
  private async lighterEvmAccount(): Promise<string> {
    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) {
      throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to trade Lighter perps.');
    }
    const accounts: string[] = await ethereum.request({ method: 'eth_requestAccounts' });
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available. Unlock your wallet and try again.');
    return account;
  }

  /**
   * Lighter onboarding — one-time, non-custodial. The user's EVM wallet does a
   * single personal_sign of a server-provided message that authorises OPRAI's
   * delegated agent key on the Lighter account. After this, every trade is
   * gas-free and signed by the agent key server-side (no wallet popup per trade).
   * The L1 private key never leaves the wallet, and the agent key cannot
   * withdraw to an arbitrary address.
   */
  private async executeLighterOnboard(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const ethereum = await this.walletService.resolveEvmProvider();
    const account = await this.lighterEvmAccount();

    callbacks.onQuote?.();
    // 1. Server mints an agent keypair (held encrypted server-side) and returns
    //    the exact change-pubkey message the wallet must personal_sign.
    const build = await firstValueFrom(this.api.post<any>('/actions/lighter/onboard/build', {
      wallet: account,
    }));
    const message: string = build?.message_to_sign ?? build?.messageToSign;
    const sessionId: string = build?.session_id ?? build?.sessionId;
    if (!message) throw new Error('Lighter: onboarding message was not returned.');

    callbacks.onSign?.();
    // 2. Wallet personal_sign (EIP-191). Params order is [message, address].
    const signature: string = await ethereum.request({
      method: 'personal_sign',
      params: [message, account],
    });
    if (!signature) throw new Error('Lighter: the authorisation was not signed.');

    callbacks.onProgress?.();
    // 3. Server injects the L1 signature and broadcasts the change-pubkey tx,
    //    registering the agent key on the account.
    const submit = await firstValueFrom(this.api.post<any>('/actions/lighter/onboard/submit', {
      wallet: account,
      session_id: sessionId,
      signature,
    }));
    if (submit && submit.ok === false) {
      throw new Error(submit.error || 'Lighter onboarding failed.');
    }
    callbacks.onConfirm?.();
    return '';
  }

  /**
   * USDC collateral deposit into Lighter. This is the one on-chain step (the
   * trades are gas-free). The backend builds the ordered EVM transaction(s);
   * we switch the wallet to the deposit chain and send each in sequence.
   */
  private async executeLighterDeposit(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const ethereum = await this.walletService.resolveEvmProvider();
    const account = await this.lighterEvmAccount();
    const p = action.params;

    callbacks.onQuote?.();
    const build = await firstValueFrom(this.api.post<any>('/actions/lighter/deposit/build', {
      wallet: account,
      amount: p['amount'],
      token: p['token'] ?? 'USDG',
      ...(p['chainId'] ? { chainId: Number(p['chainId']) } : {}),
    }));
    // Deposit not available / needs manual action — surface the backend message.
    if (build && build.ok === false) {
      throw new Error(build.error || 'Lighter deposit is unavailable right now.');
    }
    // Backend-managed deposit (no EVM tx to sign) — treat as done.
    if (build && Array.isArray(build.transactions) === false && build.ok !== false) {
      callbacks.onConfirm?.();
      return '';
    }
    const txs: Array<{ to: string; data?: string; value?: string; chainId?: number }> = build?.transactions ?? [];
    if (!txs.length) throw new Error('Lighter: no deposit transaction was returned.');

    const chainId = Number(txs[0].chainId ?? build?.chainId ?? p['chainId'] ?? 0);
    if (chainId) {
      const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
      if (onChain !== chainId) {
        try {
          await ethereum.request({
            method: 'wallet_switchEthereumChain',
            params: [{ chainId: `0x${chainId.toString(16)}` }],
          });
        } catch {
          throw new Error('Your wallet is on a different network than this deposit. Switch it and try again — nothing was signed.');
        }
      }
    }
    const toHexQty = (v: unknown): string => {
      const s = String(v ?? '').trim();
      if (!s) return '0x0';
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return '0x0'; }
    };

    callbacks.onSign?.();
    let lastHash = '';
    for (const tx of txs) {
      lastHash = await ethereum.request({
        method: 'eth_sendTransaction',
        params: [{ from: account, to: tx.to, data: tx.data ?? '0x', value: toHexQty(tx.value) }],
      });
      callbacks.onSubmit?.(lastHash);
      const ok = await this.watchEvmReceipt(ethereum, lastHash);
      if (ok === false) {
        callbacks.onFail?.('The deposit reverted on-chain — nothing was deposited. Your funds are safe minus the network fee.', lastHash);
        return lastHash;
      }
      callbacks.onProgress?.();
    }
    callbacks.onConfirm?.(lastHash);
    return lastHash;
  }

  /**
   * Lighter open / close / set-leverage — gas-free. The delegated agent key
   * signs server-side, so there is NO wallet popup and NO on-chain tx hash. We
   * only need the connected EVM address to identify the Lighter account. If the
   * account has not been onboarded, the backend rejects with a clear error and
   * the card surfaces it (the user runs "Enable Lighter trading" first).
   */
  private async executeLighterPerp(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const account = await this.lighterEvmAccount();
    const p = action.params;
    const symbol = String(p['symbol'] ?? p['market'] ?? '').toUpperCase();

    callbacks.onQuote?.();
    let path: string;
    let body: Record<string, unknown>;
    if (action.type === 'lighter_open') {
      path = '/actions/lighter/open';
      const orderType = String(p['orderType'] ?? 'market').toLowerCase() === 'limit' ? 'limit' : 'market';
      body = {
        wallet: account,
        symbol,
        side: (String(p['side'] ?? 'long').toLowerCase() === 'short') ? 'short' : 'long',
        collateralUsd: Number(p['collateralUsd'] ?? p['collateralAmount'] ?? p['sizeUsd'] ?? 0),
        leverage: Number(p['leverage'] ?? 1),
        orderType,
        ...(orderType === 'limit' && Number(p['limitPrice']) > 0 ? { limitPrice: Number(p['limitPrice']) } : {}),
        ...(String(p['reduceOnly']) === 'true' ? { reduceOnly: true } : {}),
      };
    } else if (action.type === 'lighter_close') {
      path = '/actions/lighter/close';
      body = {
        wallet: account,
        symbol,
        side: String(p['side'] ?? 'long').toLowerCase(),
        baseAmount: Number(p['baseAmount'] ?? p['amount'] ?? 0),
      };
    } else if (action.type === 'lighter_withdraw') {
      // Withdraw USDG collateral back to the user's own wallet (owner-gated,
      // agent-signed, gas-free — no on-chain tx to sign).
      path = '/actions/lighter/withdraw';
      body = { wallet: account, amount: Number(p['amount'] ?? 0) };
    } else {
      path = '/actions/lighter/leverage';
      body = { wallet: account, symbol, leverage: Number(p['leverage'] ?? 1) };
    }

    callbacks.onSign?.();
    let resp = await firstValueFrom(this.api.post<any>(path, body));
    // First trade on a fresh account: the delegated agent key isn't authorised
    // yet, so the backend rejects with an onboarding message. Rather than send
    // the user away to "Enable Lighter trading" and back, run the one-time
    // authorisation inline (a wallet signature, no on-chain tx) and retry the
    // trade — so "long CASHCAT" just works even before onboarding.
    const needsOnboard = resp && (resp.ok === false || resp.error)
      && /onboard|connect lighter|authoris/i.test(String(resp.error ?? ''));
    if (needsOnboard) {
      await this.executeLighterOnboard(
        { type: 'lighter_onboard', params: {}, raw: '' } as ParsedAction,
        { onSign: callbacks.onSign, onProgress: callbacks.onProgress },
      );
      callbacks.onProgress?.();
      resp = await firstValueFrom(this.api.post<any>(path, body));
    }
    if (resp && (resp.ok === false || resp.error)) {
      throw new Error(resp.error || 'Lighter: the trade was rejected.');
    }
    callbacks.onConfirm?.();
    return '';
  }

  /**
   * pools.trade native buy/sell on Robinhood Chain (4663). The backend proxies
   * trade.prepareBuy / trade.prepareSell, which return an ordered list of EVM
   * transactions ({to, data, value}) to sign and send in sequence (an approval
   * may precede the trade). We switch the wallet to Robinhood, send each,
   * waiting for each to land, and return the final tx hash.
   */
  /** Decimal ETH → wei integer string (no float loss). */
  private ethToWeiStr(v: string): string {
    const s = String(v ?? '').trim();
    if (!s || !/^\d*\.?\d*$/.test(s)) return '0';
    const [intPart = '0', fracRaw = ''] = s.split('.');
    const frac = (fracRaw + '0'.repeat(18)).slice(0, 18);
    return ((intPart.replace(/\D/g, '') || '0') + frac).replace(/^0+/, '') || '0';
  }

  private async executePoolsTrade(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const isPons = action.type.startsWith('pons');
    const isSell = action.type === 'pools_sell' || action.type === 'pons_sell';
    const isPonsLaunch = action.type === 'pons_launch';

    // A graduated Pons token is a normal Uniswap pool on Robinhood — trade it
    // through our own Uniswap swap, not the (closed) curve.
    if (isPons && !isPonsLaunch && p['graduated'] === 'true') {
      const t = String(p['tokenAddress'] ?? '');
      const swapParams = isSell
        ? { originChainId: '4663', destinationChainId: '4663', originCurrency: t, destinationCurrency: 'ETH', amount: String(p['amountTokens'] ?? ''), tradeType: 'EXACT_INPUT' }
        : { originChainId: '4663', destinationChainId: '4663', originCurrency: 'ETH', destinationCurrency: t, amount: String(p['amountEth'] ?? ''), tradeType: 'EXACT_INPUT' };
      return this.executeUniswapSwap({ type: 'uniswap_swap', params: swapParams, raw: '' } as ParsedAction, callbacks);
    }
    const isLaunch = action.type === 'pools_launch';
    const chainId = 4663;
    const token = String(p['tokenAddress'] ?? '');
    if (!isLaunch && !isPonsLaunch && !token && !p['curve']) throw new Error('no token specified.');

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to trade on pools.trade.');
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> =>
      Promise.race([promise, new Promise<never>((_, r) => setTimeout(() => r(new Error(`pools.trade: ${label} timed out`)), ms))]);
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');

    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }), 60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network. Switch it to Robinhood Chain and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    let endpoint: string;
    let reqBody: Record<string, unknown>;
    if (isLaunch) {
      // Create a pools.trade token. pools.trade pins the metadata (name/symbol/
      // description/image) during prepareLaunch and returns the create tx(s).
      const name = String(p['tokenName'] ?? '').trim();
      const symbol = String(p['tokenSymbol'] ?? '').trim();
      if (!name || !symbol) throw new Error('pools.trade: a token name and symbol are required to launch.');
      reqBody = {
        // "crowd" = 4h Continuous Clearing Auction; "instant" = bonding curve.
        mode: String(p['mode'] ?? 'instant').toLowerCase() === 'crowd' ? 'crowd' : 'instant',
        tokenName: name,
        tokenSymbol: symbol,
        description: String(p['description'] ?? '').trim(),
        walletAddress: account,
        // pools.trade wants the image inline as a PNG/WebP base64 data URI — it
        // decodes and pins it server-side. (https/ipfs URLs are rejected.)
        ...(p['imageUrl'] && /^data:image\/(png|webp);base64,/.test(String(p['imageUrl'])) ? { imageUrl: String(p['imageUrl']) } : {}),
        ...(p['xUrl'] ? { xUrl: String(p['xUrl']) } : {}),
        ...(p['website'] ? { website: String(p['website']) } : {}),
        ...(p['creatorFee'] === 'true' ? { creatorFee: true } : {}),
      };
      endpoint = '/actions/uniswap/launch/create';
    } else if (isPonsLaunch) {
      // Pons launch: name/symbol/logo/description/socials go on-chain; creator
      // fee maps to a 1% creatorTax. The backend ABI-encodes factory.launchToken.
      const name = String(p['name'] ?? '').trim();
      const symbol = String(p['symbol'] ?? '').trim();
      if (!name || !symbol) throw new Error('Pons: a token name and symbol are required to launch.');
      reqBody = {
        walletAddress: account,
        version: String(p['version'] ?? 'v2'),
        name,
        symbol,
        ...(p['logo'] ? { logo: String(p['logo']) } : {}),
        ...(p['description'] ? { description: String(p['description']) } : {}),
        ...(p['twitter'] ? { twitter: String(p['twitter']) } : {}),
        ...(p['telegram'] ? { telegram: String(p['telegram']) } : {}),
        ...(p['website'] ? { website: String(p['website']) } : {}),
        creatorTaxBps: p['creatorFee'] === 'true' ? '100' : '0',
      };
      endpoint = '/actions/pons/launch';
    } else {
      // pools.trade's own UI has no slippage control — a swap uses a generous
      // auto-tolerance, and a crowd-launch bid a high max-price ceiling (the
      // uniform-clearing auction charges the clearing price regardless, so a
      // high ceiling only ensures the bid fills through graduation, no overpay).
      const slippagePct = 15;
      const isCca = !isSell && p['kind'] === 'cca' && p['ccaStatus'] !== 'graduated';
      if (isCca) {
        const auction = String(p['auctionAddress'] ?? '');
        if (!auction) throw new Error('pools.trade: could not resolve this crowd launch — try again from the launch page.');
        const clr = String(p['clearingPriceQ96'] ?? '');
        // Ceiling = clearing × how much the price can still rise to graduation
        // (target/fdv), with headroom. Clamped so it's always a sane multiple.
        const fdv = Number(p['fdvUsd']), target = Number(p['graduationTargetUsd']);
        let mult = 5;
        if (fdv > 0 && target > fdv) mult = Math.min(30, Math.max(2, Math.ceil((target / fdv) * 1.3)));
        let maxPriceQ96 = clr;
        try { if (clr) maxPriceQ96 = (BigInt(clr) * BigInt(mult)).toString(); } catch { /* keep clearing */ }
        reqBody = { auctionAddress: auction, walletAddress: account, amountUsd: Number(p['amountUsd'] ?? 0), maxPriceQ96 };
        endpoint = '/actions/uniswap/launch/bid';
      } else if (isPons) {
        // Pons is its own on-chain bonding curve: buy pays ETH directly (wei),
        // sell burns token base units. The backend ABI-encodes the curve tx.
        reqBody = { tokenAddress: token, walletAddress: account, slippagePct: Number(p['slippagePct'] ?? 15) };
        if (p['curve']) reqBody['curve'] = String(p['curve']);
        if (isSell) reqBody['amountInWei'] = String(p['amountInWei'] ?? '0');
        else reqBody['amountWei'] = String(p['amountWei'] ?? '0');
        endpoint = isSell ? '/actions/pons/sell' : '/actions/pons/buy';
      } else {
        reqBody = { tokenAddress: token, walletAddress: account, slippagePct };
        if (isSell) reqBody['amountInWei'] = String(p['amountInWei'] ?? '0');
        else reqBody['amountUsd'] = Number(p['amountUsd'] ?? 0);
        endpoint = isSell ? '/actions/uniswap/launch/sell' : '/actions/uniswap/launch/buy';
      }
    }

    const prep = await firstValueFrom(this.api.post<any>(endpoint, reqBody));
    const txs: any[] = Array.isArray(prep?.transactions) ? prep.transactions : [];
    if (txs.length === 0) throw new Error('no transaction was returned.');

    callbacks.onSign?.();
    let lastHash = '';
    for (let i = 0; i < txs.length; i++) {
      const tx = txs[i];
      if (!tx?.to) continue;
      const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
      const verb = isLaunch ? 'launch' : isSell ? 'sell' : 'buy';
      const hash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{
          from: account,
          to: tx.to,
          data: tx.data ?? '0x',
          value: toHexQty(tx.value) ?? '0x0',
          ...(gasHex ? { gas: gasHex } : {}),
        }],
      }), 120_000, i < txs.length - 1 ? 'approval' : verb) as string;
      lastHash = hash;
      if (i === 0) callbacks.onSubmit?.(hash);
      const ok = await this.watchEvmReceipt(ethereum, hash);
      if (ok === false) {
        callbacks.onFail?.(`The ${verb} reverted on-chain — your funds are safe minus the network fee.`, hash);
        return hash;
      }
    }

    // Pons "Developer buy": the token is live on its curve immediately, so read
    // the launch receipt for the TokenLaunched event (token+curve), then do a
    // follow-on native curve buy. Best-effort — a failed dev buy never fails the
    // launch itself.
    if (isPonsLaunch && Number(p['devBuyEth']) > 0 && lastHash) {
      try {
        const isV1 = String(p['version'] ?? 'v2') === 'v1';
        const rc: any = await withTimeout(ethereum.request({ method: 'eth_getTransactionReceipt', params: [lastHash] }), 30_000, 'receipt');
        if (isV1) {
          // V1 token → normal Uniswap V3 pool; buy it via our uniswap swap.
          const TL_V1 = '0xdb51ea9ad51ab453a65a4cb7e60c3cb378c9501bb002609f8f97778fb6c4235a';
          const f1 = '0xa5aab3f0c6eeadf30ef1d3eb997108e976351feb';
          const log = (rc?.logs ?? []).find((l: any) => (l.topics?.[0] ?? '').toLowerCase() === TL_V1 && (l.address ?? '').toLowerCase() === f1);
          const newToken = log?.topics?.[1] ? '0x' + String(log.topics[1]).slice(26) : '';
          if (newToken) {
            await this.executeUniswapSwap({ type: 'uniswap_swap', params: {
              originChainId: '4663', destinationChainId: '4663', originCurrency: 'ETH',
              destinationCurrency: newToken, amount: String(p['devBuyEth']), tradeType: 'EXACT_INPUT',
            }, raw: '' } as ParsedAction, {});
          }
        } else {
          // V2 token → bonding curve; buy on the curve.
          const wei = this.ethToWeiStr(String(p['devBuyEth']));
          const TL = '0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607';
          const factory = '0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e';
          const log = (rc?.logs ?? []).find((l: any) => (l.topics?.[0] ?? '').toLowerCase() === TL && (l.address ?? '').toLowerCase() === factory);
          const curve = log?.topics?.[2] ? '0x' + String(log.topics[2]).slice(26) : '';
          if (curve && wei !== '0') {
            const buyPrep = await firstValueFrom(this.api.post<any>('/actions/pons/buy', {
              curve, walletAddress: account, amountWei: wei, slippagePct: 20,
            }));
            for (const tx of (buyPrep?.transactions ?? [])) {
              if (!tx?.to) continue;
              const gh = toHexQty(tx.gas ?? tx.gasLimit);
              const h = await withTimeout(ethereum.request({
                method: 'eth_sendTransaction',
                params: [{ from: account, to: tx.to, data: tx.data ?? '0x', value: toHexQty(tx.value) ?? '0x0', ...(gh ? { gas: gh } : {}) }],
              }), 120_000, 'developer buy') as string;
              await this.watchEvmReceipt(ethereum, h);
            }
          }
        }
      } catch { /* launch already succeeded; the optional dev buy failed — ignore */ }
    }

    // Instant launch + "Buy at launch": once the token is live (bonding curve is
    // tradable immediately), do a follow-on native buy of the just-launched token.
    if (isLaunch && p['buyAtLaunch'] === 'true') {
      const buyUsd = Number(p['buyAmountUsd'] ?? 0);
      const newToken = prep?.predictedTokenAddress as string | undefined;
      if (buyUsd > 0 && newToken) {
        try {
          const buyPrep = await firstValueFrom(this.api.post<any>('/actions/uniswap/launch/buy', {
            tokenAddress: newToken, walletAddress: account, amountUsd: buyUsd, slippagePct: 10,
          }));
          for (const tx of (buyPrep?.transactions ?? [])) {
            if (!tx?.to) continue;
            const gh = toHexQty(tx.gas ?? tx.gasLimit);
            const h = await withTimeout(ethereum.request({
              method: 'eth_sendTransaction',
              params: [{ from: account, to: tx.to, data: tx.data ?? '0x', value: toHexQty(tx.value) ?? '0x0', ...(gh ? { gas: gh } : {}) }],
            }), 120_000, 'buy at launch') as string;
            await this.watchEvmReceipt(ethereum, h);
          }
        } catch {
          // The token launched fine; only the optional bundled buy failed. Don't
          // fail the whole action — the launch already succeeded.
        }
      }
    }
    callbacks.onConfirm?.(lastHash);
    return lastHash;
  }

  /**
   * Morpho Blue lending (Robinhood Chain 4663). The backend ABI-encodes the
   * Morpho call against the singleton and returns unsigned txs — an ERC-20
   * approval first when the allowance is short, then the supply/borrow/repay/
   * withdraw call. We switch the wallet to Robinhood Chain and sign each tx in
   * order (waiting for every receipt), exactly like the pools.trade EVM flow.
   * All amounts/flags come from the card, which knows each token's decimals from
   * the market list, so it can pass base-unit amounts directly.
   */
  private async executeMorpho(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    // Morpho is multichain (Ethereum/Base/Arbitrum/Optimism/Polygon/Unichain/
    // Robinhood) — the market carries its chain; default Robinhood (4663).
    const chainId = this.morphoChainId(String(p['chain'] ?? p['chainId'] ?? '')) || 4663;
    const kind = action.type.replace('morpho_', ''); // supply | borrow | repay | withdraw
    const marketId = String(p['marketId'] ?? '').trim();
    if (!marketId) throw new Error('Morpho: pick a market first.');

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to use Morpho on Robinhood Chain.');
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> =>
      Promise.race([promise, new Promise<never>((_, r) => setTimeout(() => r(new Error(`Morpho: ${label} timed out`)), ms))]);
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');

    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }), 60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network. Switch it to Robinhood Chain and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    const reqBody: Record<string, unknown> = { marketId, walletAddress: account, chain: String(chainId) };
    // Forward whichever amount/flag fields the card set; the backend accepts both
    // human `amount` and `amountBaseUnits` (card prefers base units).
    const fwd = (k: string) => { if (p[k] != null && String(p[k]) !== '') reqBody[k] = String(p[k]); };
    if (kind === 'supply') {
      fwd('amount'); fwd('amountBaseUnits');
    } else if (kind === 'borrow') {
      fwd('borrowAmount'); fwd('borrowBaseUnits'); fwd('collateralAmount'); fwd('collateralBaseUnits');
    } else if (kind === 'repay') {
      fwd('amount'); fwd('amountBaseUnits'); if (String(p['max']) === 'true') reqBody['max'] = true;
    } else if (kind === 'withdraw') {
      fwd('amount'); fwd('amountBaseUnits'); reqBody['target'] = String(p['target'] ?? 'supply');
      if (String(p['max']) === 'true') reqBody['max'] = true;
    }
    const endpoint = `/actions/morpho/${kind}`;

    const prep = await firstValueFrom(this.api.post<any>(endpoint, reqBody));
    const txs: any[] = Array.isArray(prep?.transactions) ? prep.transactions : [];
    if (txs.length === 0) throw new Error('Morpho: no transaction was returned.');

    callbacks.onSign?.();
    let lastHash = '';
    for (let i = 0; i < txs.length; i++) {
      const tx = txs[i];
      if (!tx?.to) continue;
      const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
      const isApproval = i < txs.length - 1; // last tx is the Morpho call
      const hash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{
          from: account,
          to: tx.to,
          data: tx.data ?? '0x',
          value: toHexQty(tx.value) ?? '0x0',
          ...(gasHex ? { gas: gasHex } : {}),
        }],
      }), 120_000, isApproval ? 'approval' : kind) as string;
      lastHash = hash;
      if (i === 0) callbacks.onSubmit?.(hash);
      const ok = await this.watchEvmReceipt(ethereum, hash);
      // An approval MUST be confirmed on-chain before the dependent Morpho call —
      // otherwise the call's transferFrom races an unmined allowance and reverts.
      // ok===null (unconfirmed within the poll window) also stops here: retry then
      // sees the landed allowance and skips straight to the call.
      if (isApproval) {
        if (ok !== true) {
          callbacks.onFail?.('The token approval didn’t confirm on-chain yet — tap Confirm again in a moment (your approval is on its way).', hash);
          return hash;
        }
      } else if (ok === false) {
        callbacks.onFail?.(`The ${kind} reverted on-chain — your funds are safe minus the network fee.`, hash);
        return hash;
      }
    }
    callbacks.onConfirm?.(lastHash);
    return lastHash;
  }

  /**
   * OpenSea NFT buy (Robinhood Chain 4663). Backend calls OpenSea's
   * fulfillment_data and ABI-encodes the Seaport tx; here we switch the wallet to
   * Robinhood Chain and sign the single fulfillment tx.
   */
  private async executeOpenseaFulfill(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const chainId = 4663;
    const isAccept = action.type === 'opensea_accept_offer';
    const orderHash = String(p['orderHash'] ?? '').trim();
    if (!orderHash) throw new Error('OpenSea: no order selected.');

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to trade on OpenSea.');
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> =>
      Promise.race([promise, new Promise<never>((_, r) => setTimeout(() => r(new Error(`OpenSea: ${label} timed out`)), ms))]);
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');
    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }), 60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network. Switch it to Robinhood Chain and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    const reqBody: Record<string, unknown> = { orderHash, walletAddress: account };
    if (p['protocolAddress']) reqBody['protocolAddress'] = String(p['protocolAddress']);
    if (isAccept) { reqBody['token'] = String(p['token'] ?? ''); reqBody['tokenId'] = String(p['tokenId'] ?? ''); }
    const endpoint = isAccept ? '/actions/opensea/accept-offer' : '/actions/opensea/buy';
    const prep = await firstValueFrom(this.api.post<any>(endpoint, reqBody));
    const txs: any[] = Array.isArray(prep?.transactions) ? prep.transactions : [];
    if (txs.length === 0) throw new Error('OpenSea: no transaction was returned.');

    callbacks.onSign?.();
    const verb = isAccept ? 'accept' : 'buy';
    let lastHash = '';
    for (let i = 0; i < txs.length; i++) {
      const tx = txs[i];
      if (!tx?.to) continue;
      const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
      const isApproval = i < txs.length - 1;
      const hash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{ from: account, to: tx.to, data: tx.data ?? '0x', value: toHexQty(tx.value) ?? '0x0', ...(gasHex ? { gas: gasHex } : {}) }],
      }), 120_000, isApproval ? 'approval' : verb) as string;
      lastHash = hash;
      if (i === 0) callbacks.onSubmit?.(hash);
      const ok = await this.watchEvmReceipt(ethereum, hash);
      // An approval must confirm before the dependent call — else it races the
      // unmined allowance and reverts. null (unconfirmed) also stops here.
      if (isApproval) {
        if (ok !== true) {
          callbacks.onFail?.('The token approval didn’t confirm on-chain yet — tap Confirm again in a moment.', hash);
          return hash;
        }
      } else if (ok === false) {
        callbacks.onFail?.(`The ${verb} reverted on-chain — your funds are safe minus the network fee.`, hash);
        return hash;
      }
    }
    callbacks.onConfirm?.(lastHash);
    return lastHash;
  }

  /**
   * OpenSea LIST (sell) / MAKE OFFER — gasless Seaport orders. Backend builds the
   * order + EIP-712 typed data; the wallet signs it (signTypedData_v4, no gas);
   * the backend submits the signed order to OpenSea. make-offer first sends a
   * one-time WETH approval to the conduit.
   */
  private async executeOpenseaOrder(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const chainId = 4663;
    const isOffer = action.type === 'opensea_make_offer';
    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to trade on OpenSea.');
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> =>
      Promise.race([promise, new Promise<never>((_, r) => setTimeout(() => r(new Error(`OpenSea: ${label} timed out`)), ms))]);
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');
    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }), 60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network. Switch it to Robinhood Chain and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    const buildBody: Record<string, unknown> = {
      token: String(p['token'] ?? p['contract'] ?? ''),
      tokenId: String(p['tokenId'] ?? p['identifier'] ?? ''),
      priceEth: String(p['priceEth'] ?? ''),
      walletAddress: account,
    };
    if (p['slug']) buildBody['slug'] = String(p['slug']);
    if (p['durationDays']) buildBody['durationDays'] = String(p['durationDays']);
    const built = await firstValueFrom(this.api.post<any>(isOffer ? '/actions/opensea/make-offer' : '/actions/opensea/list', buildBody));
    const typedData = built?.typedData;
    const parameters = built?.parameters;
    if (!typedData || !parameters) throw new Error('OpenSea: could not build the order.');

    callbacks.onSign?.();
    // make-offer: one-time WETH approval to the conduit so the bid can be pulled.
    if (isOffer && built?.wethApprove?.to) {
      const a = built.wethApprove;
      const h = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{ from: account, to: a.to, data: a.data ?? '0x', value: '0x0' }],
      }), 120_000, 'WETH approval') as string;
      await this.watchEvmReceipt(ethereum, h);
    }
    // Sign the Seaport order (EIP-712, gasless).
    const signature = await withTimeout(ethereum.request({
      method: 'eth_signTypedData_v4',
      params: [account, JSON.stringify(typedData)],
    }), 120_000, 'order signature') as string;

    const res = await firstValueFrom(this.api.post<any>('/actions/opensea/order/submit', {
      parameters, signature, kind: isOffer ? 'offer' : 'listing',
      protocolAddress: built?.protocolAddress,
    }));
    if (!res?.ok) throw new Error('OpenSea rejected the order.');
    // Off-chain order — no tx hash. Use the order hash as the receipt id.
    const oh = String(res?.orderHash ?? '');
    callbacks.onConfirm?.(oh);
    return oh;
  }

  /** Resolve a Morpho chain hint (numeric id or name) to a chain id. */
  private morphoChainId(v: string): number {
    const s = (v || '').trim().toLowerCase();
    if (!s) return 0;
    const n = Number(s);
    if (Number.isFinite(n) && n > 0) return n;
    const map: Record<string, number> = {
      ethereum: 1, eth: 1, mainnet: 1, base: 8453, arbitrum: 42161, arb: 42161,
      optimism: 10, op: 10, polygon: 137, matic: 137, unichain: 130, robinhood: 4663, rh: 4663,
    };
    return map[s] ?? 0;
  }

  /**
   * SushiSwap (Robinhood Chain 4663) — swap (via Sushi's aggregator API) and V3
   * add-liquidity (NonfungiblePositionManager.mint). Backend returns unsigned txs
   * (an ERC-20 approval to RedSnwapper/NPM when needed, then the call); we switch
   * the wallet to Robinhood Chain and sign each in order, like the Morpho flow.
   */
  private async executeSushi(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const chainId = 4663;
    const isSwap = action.type === 'sushi_swap';

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to use Sushi on Robinhood Chain.');
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> =>
      Promise.race([promise, new Promise<never>((_, r) => setTimeout(() => r(new Error(`Sushi: ${label} timed out`)), ms))]);
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');

    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }), 60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network. Switch it to Robinhood Chain and try again — nothing was signed.');
      }
    }

    callbacks.onQuote?.();
    let endpoint: string;
    const reqBody: Record<string, unknown> = { walletAddress: account };
    const fwd = (k: string) => { if (p[k] != null && String(p[k]) !== '') reqBody[k] = String(p[k]); };
    if (isSwap) {
      reqBody['tokenIn'] = String(p['tokenIn'] ?? '');
      reqBody['tokenOut'] = String(p['tokenOut'] ?? '');
      fwd('amount'); fwd('amountBaseUnits'); fwd('slippagePct');
      endpoint = '/actions/sushi/swap';
    } else {
      fwd('poolAddress'); fwd('inputToken'); fwd('amount'); fwd('amountBaseUnits');
      fwd('rangePercent'); fwd('slippagePct');
      endpoint = '/actions/sushi/add-liquidity';
    }

    const prep = await firstValueFrom(this.api.post<any>(endpoint, reqBody));
    const txs: any[] = Array.isArray(prep?.transactions) ? prep.transactions : [];
    if (txs.length === 0) throw new Error('Sushi: no transaction was returned.');

    callbacks.onSign?.();
    const verb = isSwap ? 'swap' : 'add liquidity';
    let lastHash = '';
    for (let i = 0; i < txs.length; i++) {
      const tx = txs[i];
      if (!tx?.to) continue;
      const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
      const isApproval = i < txs.length - 1;
      const hash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{
          from: account,
          to: tx.to,
          data: tx.data ?? '0x',
          value: toHexQty(tx.value) ?? '0x0',
          ...(gasHex ? { gas: gasHex } : {}),
        }],
      }), 120_000, isApproval ? 'approval' : verb) as string;
      lastHash = hash;
      if (i === 0) callbacks.onSubmit?.(hash);
      const ok = await this.watchEvmReceipt(ethereum, hash);
      // The ERC-20 approval must confirm before the swap/LP call — else it races
      // the unmined allowance and reverts. null (unconfirmed) also stops here.
      if (isApproval) {
        if (ok !== true) {
          callbacks.onFail?.('The token approval didn’t confirm on-chain yet — tap Confirm again in a moment.', hash);
          return hash;
        }
      } else if (ok === false) {
        callbacks.onFail?.(`The ${verb} reverted on-chain — your funds are safe minus the network fee.`, hash);
        return hash;
      }
    }
    callbacks.onConfirm?.(lastHash);
    return lastHash;
  }

  /**
   * Open a Uniswap V3 liquidity position. Backend reads the pool, computes the
   * tick range, and returns the approval txs + the create tx; here we switch the
   * wallet to the chain, send each approval (waiting for it to land), then send
   * the create tx and watch its receipt. EVM, so window.ethereum like the swap.
   */
  private async executeUniswapAddLiquidity(action: ParsedAction, callbacks: ActionCallbacks): Promise<string> {
    const p = action.params;
    const chainId = Number(p['chainId'] ?? p['originChainId'] ?? 0)
      || this.evmChainIdFromName(p['chain']);
    if (!chainId) throw new Error('Uniswap: no chain specified for this pool.');

    const ethereum = await this.walletService.resolveEvmProvider();
    if (!ethereum) {
      throw new Error('No EVM wallet detected. Install MetaMask or another EVM wallet to add liquidity on Uniswap.');
    }
    const withTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> => {
      const timer = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error(`Uniswap: ${label} timed out after ${ms / 1000}s`)), ms));
      return Promise.race([promise, timer]);
    };
    const toHexQty = (v: unknown): string | undefined => {
      if (v == null) return undefined;
      const s = String(v).trim();
      if (s === '') return undefined;
      if (/^0x[0-9a-fA-F]*$/.test(s)) return s;
      try { return '0x' + BigInt(s).toString(16); } catch { return undefined; }
    };

    const accounts: string[] = await withTimeout(
      ethereum.request({ method: 'eth_requestAccounts' }), 30_000, 'wallet connect');
    const account = accounts?.[0];
    if (!account) throw new Error('No EVM account available.');

    const onChain = Number(await ethereum.request({ method: 'eth_chainId' }));
    if (onChain !== chainId) {
      try {
        await withTimeout(
          ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] }),
          60_000, 'network switch');
      } catch {
        throw new Error('Your wallet is on a different network than this pool. Switch it and try again — nothing was signed.');
      }
    }

    const buildBody = {
      chain: p['chain'] ?? String(chainId),
      poolAddress: p['poolAddress'] ?? p['pool'] ?? p['whirlpool'],
      version: p['version'] ?? 'v3',
      inputToken: p['inputToken'] ?? p['token0'] ?? p['token'],
      amount: p['amount'],
      token0: p['token0'] ?? '',
      token1: p['token1'] ?? '',
      ...(p['rangePercent'] ? { rangePercent: Number(p['rangePercent']) } : {}),
    };

    callbacks.onQuote?.();
    // 1. Build (backend reads the pool + computes ticks + returns approve/create txs).
    let build = await firstValueFrom(this.api.post<any>('/actions/uniswap/lp/build', buildBody));

    // 2. Approvals (ERC20 → position manager). Each MUST fully confirm before the
    // create — the create pulls the tokens, so an unconfirmed allowance makes the
    // wallet's gas estimation revert ("insufficient funds"/"transfer exceeds
    // allowance"). Treat an unresolved receipt as not-confirmed, not success.
    const approvals: any[] = Array.isArray(build?.approvals) ? build.approvals : [];
    let didApprove = false;
    for (const ap of approvals) {
      if (!ap?.to) continue;
      callbacks.onProgress?.();
      const apHash = await withTimeout(ethereum.request({
        method: 'eth_sendTransaction',
        params: [{ from: account, to: ap.to, data: ap.data ?? '0x', value: toHexQty(ap.value) ?? '0x0' }],
      }), 120_000, 'token approval') as string;
      const ok = await this.watchEvmReceipt(ethereum, apHash);
      if (ok === false) throw new Error('A token approval reverted on-chain — no liquidity was added.');
      if (ok === null) throw new Error('The token approval didn’t confirm in time — try again once it lands.');
      didApprove = true;
    }

    // 3. After approving, RE-BUILD so the create tx carries a fresh deadline and
    // is priced against the just-set allowance (the first build's create is stale
    // by two signatures' worth of time).
    if (didApprove) {
      callbacks.onProgress?.();
      // Small settle so the wallet's node sees the new allowance before it
      // estimates gas for the create.
      await new Promise(r => setTimeout(r, 2_000));
      build = await firstValueFrom(this.api.post<any>('/actions/uniswap/lp/build', buildBody));
    }

    // 3b. V4 pulls the ERC-20 legs through Permit2, so the create only succeeds
    // with a signed batch permit. Sign it, then re-build to get the finalized
    // create tx carrying the signature.
    if (build?.needsPermit && build?.permitData?.domain) {
      const pd = build.permitData;
      const typedData = {
        domain: { ...pd.domain, chainId },  // API sends chainId as a name ("ROBINHOOD"); the signer needs the number
        types: {
          EIP712Domain: this.eip712DomainFields(pd.domain),
          ...this.normalizePermitTypes(pd.types),
        },
        primaryType: 'PermitBatch',
        message: pd.values,
      };
      callbacks.onSign?.();
      const signature = await withTimeout(
        ethereum.request({ method: 'eth_signTypedData_v4', params: [account, JSON.stringify(typedData)] }),
        120_000, 'permit sign') as string;
      build = await firstValueFrom(this.api.post<any>('/actions/uniswap/lp/build', {
        ...buildBody, permitData: pd, permitSignature: signature,
      }));
    }

    // 4. Create the position.
    const tx = build?.create;
    if (!tx?.to) throw new Error('Uniswap: no create transaction was returned.');
    callbacks.onSign?.();
    const gasHex = toHexQty(tx.gas ?? tx.gasLimit);
    const maxFeeHex = toHexQty(tx.maxFeePerGas);
    const maxPrioHex = toHexQty(tx.maxPriorityFeePerGas);
    const txHash = await withTimeout(ethereum.request({
      method: 'eth_sendTransaction',
      params: [{
        from: account,
        to: tx.to,
        data: tx.data ?? '0x',
        value: toHexQty(tx.value) ?? '0x0',
        ...(gasHex ? { gas: gasHex } : {}),
        ...(maxFeeHex && maxPrioHex ? { maxFeePerGas: maxFeeHex, maxPriorityFeePerGas: maxPrioHex } : {}),
      }],
    }), 120_000, 'create position sign') as string;
    callbacks.onSubmit?.(txHash);

    // 4. Watch the receipt — the mined tx IS the settlement.
    const ok = await this.watchEvmReceipt(ethereum, txHash);
    if (ok === false) {
      callbacks.onFail?.(
        'The position transaction reverted on-chain — no liquidity was added. Your funds are safe minus the network fee.',
        txHash,
      );
      return txHash;
    }
    callbacks.onConfirm?.(txHash);
    return txHash;
  }

  /** Uniswap's LP API returns EIP-712 types wrapped as `{ Type: { fields: [...] } }`;
   *  eth_signTypedData_v4 wants the plain `{ Type: [...] }` shape. Unwrap it. */
  private normalizePermitTypes(types: any): Record<string, Array<{ name: string; type: string }>> {
    const out: Record<string, Array<{ name: string; type: string }>> = {};
    for (const [k, v] of Object.entries(types ?? {})) {
      out[k] = Array.isArray(v) ? (v as any) : ((v as any)?.fields ?? []);
    }
    return out;
  }

  /** Map a chain name/slug to its EVM chain id (for LP params that carry a name). */
  private evmChainIdFromName(name?: string): number {
    switch ((name ?? '').trim().toLowerCase()) {
      case 'ethereum': case 'eth': case 'mainnet': return 1;
      case 'base': return 8453;
      case 'arbitrum': case 'arb': return 42161;
      case 'optimism': case 'op': return 10;
      case 'polygon': case 'matic': return 137;
      case 'bsc': case 'bnb': return 56;
      case 'robinhood': return 4663;
      default: return 0;
    }
  }

  /** EIP712Domain field descriptors matching whichever domain fields are present
   * (Uniswap's Permit2 domain carries name + chainId + verifyingContract). */
  private eip712DomainFields(domain: any): Array<{ name: string; type: string }> {
    const f: Array<{ name: string; type: string }> = [];
    if (domain?.name != null) f.push({ name: 'name', type: 'string' });
    if (domain?.version != null) f.push({ name: 'version', type: 'string' });
    if (domain?.chainId != null) f.push({ name: 'chainId', type: 'uint256' });
    if (domain?.verifyingContract != null) f.push({ name: 'verifyingContract', type: 'address' });
    if (domain?.salt != null) f.push({ name: 'salt', type: 'bytes32' });
    return f;
  }

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
          amountX: firstNumericParam(p['amountX'], p['amountA']),
          amountY: firstNumericParam(p['amountY'], p['amountB']),
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
          amountX: firstNumericParam(p['amountX'], p['amountA']),
          amountY: firstNumericParam(p['amountY'], p['amountB']),
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
          amountX: firstNumericParam(p['amountX'], p['amountA']),
          amountY: firstNumericParam(p['amountY'], p['amountB']),
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
          // EVM-origin routes need the sending address the same way relay_bridge does
          // (see that case): the login wallet is Solana, so the backend can't infer it.
          ...(p['sender'] ? { sender: p['sender'] } : {}),
          ...(p['fromAddress'] ? { fromAddress: p['fromAddress'] } : {}),
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
          amountX: firstNumericParam(p['amountX'], p['amountA']),
          amountY: firstNumericParam(p['amountY'], p['amountB']),
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
          // EVM-origin squid bridges send from the EVM wallet — forward it the
          // same way cross_chain_swap/relay_bridge do, else the build defaults to
          // the (empty) Solana sender.
          ...(p['fromAddress'] ? { fromAddress: p['fromAddress'] } : {}),
          ...(p['sender'] ? { sender: p['sender'] } : {}),
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
          // The sending wallet. On an EVM origin the backend's resolve_bridge_sender
          // REQUIRES this — the logged-in wallet is Solana, so without it the build
          // 400s with "This bridge leaves an EVM chain, so it needs an EVM wallet to
          // send from." The quote path (relay_get_quote) already forwards sender and
          // placeholders a missing one; execute must forward the real address too.
          ...(p['sender'] ? { sender: p['sender'] } : {}),
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

  /**
   * WYSIWYS for a plain SOL transfer: abort signing if the largest SystemProgram
   * transfer in the tx does NOT land on the recipient the card displayed. Native
   * SOL only (SPL transfers land on an ATA, not the wallet — handled separately).
   * Fails OPEN: any decode uncertainty proceeds, so it never blocks a legit tx —
   * it only stops a clear fund-redirection.
   */
  private verifyTransferWysiwys(
    tx: VersionedTransaction | Transaction,
    isVersioned: boolean,
    action: { type: string; params: Record<string, any> },
    web3: any,
  ): void {
    if (action.type !== 'transfer') return;
    const intended = String(
      action.params?.['to'] ?? action.params?.['recipient'] ?? action.params?.['destination'] ?? '',
    ).trim();
    if (!intended) return; // nothing to check against → fail-open
    // SPL token transfers go to the recipient's associated token account, not the
    // wallet address, so the wallet won't appear as a destination — skip those.
    const token = String(action.params?.['token'] ?? '').trim().toUpperCase();
    if (token && token !== 'SOL') return;

    let dests: { to: string; lamports: bigint }[];
    try {
      dests = this.systemTransferDestinations(tx, isVersioned);
    } catch {
      return; // couldn't decode → fail-open, never block a legit transfer
    }
    if (dests.length === 0) return; // no plain SOL transfer found → fail-open
    // The recipient gets the bulk; the only other SystemProgram transfer is the
    // small OPRAI fee. Require the LARGEST to be the displayed recipient.
    const largest = dests.reduce((a, b) => (b.lamports > a.lamports ? b : a));
    if (largest.to !== intended) {
      throw new Error(
        'Bu işlem gösterilen alıcıya gitmiyor — güvenlik için imzalanmadı. Lütfen tekrar deneyin.',
      );
    }
  }

  /** SystemProgram.transfer (index 2) destinations + lamports in a tx. */
  private systemTransferDestinations(
    tx: VersionedTransaction | Transaction,
    isVersioned: boolean,
  ): { to: string; lamports: bigint }[] {
    const SYS = '11111111111111111111111111111111';
    const out: { to: string; lamports: bigint }[] = [];
    const readTransfer = (data: Uint8Array): bigint | null => {
      if (data.length < 12) return null;
      const dv = new DataView(data.buffer, data.byteOffset, data.length);
      if (dv.getUint32(0, true) !== 2) return null; // 2 = Transfer
      return dv.getBigUint64(4, true);
    };
    if (isVersioned) {
      const msg = (tx as VersionedTransaction).message as any;
      const keys: string[] = msg.staticAccountKeys.map((k: any) => k.toBase58());
      for (const ci of msg.compiledInstructions) {
        if (keys[ci.programIdIndex] !== SYS) continue;
        const lamports = readTransfer(ci.data as Uint8Array);
        if (lamports == null) continue;
        const toIdx = ci.accountKeyIndexes?.[1];
        const to = toIdx != null ? keys[toIdx] : undefined;
        if (to) out.push({ to, lamports });
      }
    } else {
      for (const ix of (tx as Transaction).instructions) {
        if (ix.programId.toBase58() !== SYS) continue;
        const lamports = readTransfer(new Uint8Array(ix.data));
        if (lamports == null) continue;
        const to = ix.keys?.[1]?.pubkey?.toBase58();
        if (to) out.push({ to, lamports });
      }
    }
    return out;
  }

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
