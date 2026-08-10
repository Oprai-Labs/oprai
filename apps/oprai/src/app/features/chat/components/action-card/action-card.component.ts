/**
 * ActionCardComponent
 *
 * Renders parsed actions (swaps, transfers, stakes, token launches, etc.)
 * with protocol-specific branding, editable fields, and execution flow.
 */
import { Component, Input, Output, EventEmitter, OnInit, OnChanges, OnDestroy, SimpleChanges, ViewChild, ElementRef, inject, signal, computed, effect, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { ParsedAction, IntentParserService } from '../../services/intent-parser.service';
import { SolanaActionService, ValidatorInfo, StakeAccountInfo } from '../../services/solana-action.service';
import { ChatApiService, StoredActionResult } from '../../services/chat-api.service';
import { UploadService } from '@core/services/upload.service';
import { JupiterLendService, LEND_SUPPORTED_ASSETS, LendActionInfo, LendBorrowInfo } from '@core/services/market/jupiter-lend.service';
import { WalletService } from '@core/services/wallet.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { KaminoService, KaminoReserve, KaminoObligation, KaminoVaultMetrics, KaminoVaultPosition, KAMINO_MAIN_MARKET } from '@core/services/market/kamino.service';
import { TransactionPreviewService, TransactionPreview, BalanceChange } from '../../services/transaction-preview.service';
import { JargonTooltipComponent } from '@shared/components/jargon-tooltip/jargon-tooltip.component';
import { TokenPickerComponent } from '../token-picker/token-picker.component';
import { JupiterSwapService } from '@core/services/market/jupiter-swap.service';
import { RollbackService } from '@core/services/rollback.service';
import { SolanaRpcService } from '../../../../features/portfolio/services/solana-rpc.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { JitoService } from '@core/services/market/jito.service';
import { MarinadeService } from '@core/services/market/marinade.service';
import { JupSolService } from '@core/services/market/jupsol.service';
import { MeteoraService } from '@core/services/market/meteora.service';
import { ApiService } from '@core/services/api.service';
import { OrcaService } from '@core/services/market/orca.service';
import { MagicEdenService } from '@core/services/market/magic-eden.service';
import { environment } from '../../../../../environments/environment';
import { AppVersionService } from '@core/services/app-version.service';
import { computeDlmmRatio, rangeFromSpread, DlmmStrategy } from '@core/services/market/dlmm-math';
import { firstValueFrom, timeout } from 'rxjs';
import { createSolanaConnection } from '@core/utils/solana-connection';
import { PublicKey } from '@solana/web3.js';
import { sanitizeErrorMessage, ACTION_MIN_AMOUNT } from '@core/utils/error-messages';

const ACTION_RESULTS_KEY = 'oprai-action-results';

/**
 * Resize an image File to a square of `size` × `size` px (center-crop + scale).
 * Returns a new JPEG File. Videos and non-image files are returned unchanged.
 * pump.fun requires min. 1000×1000px square for token images.
 */
function resizeImageToSquare(file: File, size: number): Promise<File> {
  if (!file.type.startsWith('image/')) return Promise.resolve(file);
  // GIFs are left untouched — drawing to a canvas would flatten an animated GIF to
  // a single static frame. pump.fun accepts GIFs as-is, so preserve the animation.
  if (file.type === 'image/gif') return Promise.resolve(file);
  return new Promise((resolve, reject) => {
    const img = new Image();
    const blobUrl = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(blobUrl);
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext('2d');
      if (!ctx) { reject(new Error('Canvas not supported')); return; }
      // Preserve transparency: PNG/WebP sources (logos) have an alpha channel.
      // Encoding to JPEG flattens unpainted/transparent pixels to BLACK (JPEG has no
      // alpha), which is why transparent logos showed up as a solid black square.
      // Emit PNG for alpha-capable sources so transparency survives; else JPEG.
      const keepPng = file.type === 'image/png' || file.type === 'image/webp';
      // Center-crop: take the largest centered square from the source image.
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const sx = (img.naturalWidth - side) / 2;
      const sy = (img.naturalHeight - side) / 2;
      ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
      canvas.toBlob(blob => {
        if (!blob) { reject(new Error('Failed to encode resized image')); return; }
        const baseName = file.name.replace(/\.[^.]+$/, '') || 'image';
        const ext = keepPng ? '.png' : '.jpg';
        const type = keepPng ? 'image/png' : 'image/jpeg';
        resolve(new File([blob], baseName + ext, { type }));
      }, keepPng ? 'image/png' : 'image/jpeg', 0.92);
    };
    img.onerror = () => { URL.revokeObjectURL(blobUrl); reject(new Error('Failed to load image for resize')); };
    img.src = blobUrl;
  });
}


interface ProtocolConfig { name: string; icon: string; accent: string; accentBg: string; }

export interface FieldDef {
  key: string; label: string;
  type: 'text' | 'number' | 'address' | 'select' | 'toggle' | 'textarea' | 'token';
  placeholder?: string; suffix?: string; required?: boolean; half?: boolean;
  min?: number; max?: number; step?: string;
  options?: Array<{ label: string; value: string }>;
  hint?: string; transform?: 'upper' | 'lower';
  /** Display divisor: stored value is divided by this for display and multiplied back on save (e.g. 100 to show bps as %). */
  divisor?: number;
}

const PROTOCOL_CONFIGS: Record<string, ProtocolConfig> = {
  jupiter:   { name: 'Jupiter',    icon: 'assets/icons/protocols/jupiter.webp',   accent: '#5b5fc7', accentBg: 'rgba(91,95,199,0.12)' },
  jito:      { name: 'Jito',       icon: 'assets/icons/protocols/jito.webp',      accent: '#7df65c', accentBg: 'rgba(125,246,92,0.10)' },
  kamino:    { name: 'Kamino',     icon: 'assets/icons/protocols/kamino.svg',     accent: '#6C5CE7', accentBg: 'rgba(108,92,231,0.12)' },
  // orca.svg is a hand-drawn whale approximation, not Orca's mark — use the
  // real logo, and its own yellow rather than a borrowed cyan.
  orca:      { name: 'Orca',       icon: 'assets/icons/protocols/orca.webp',      accent: '#FFD15C', accentBg: 'rgba(255,209,92,0.12)' },
  raydium:   { name: 'Raydium',    icon: 'assets/icons/protocols/raydium.png',    accent: '#8B5CF6', accentBg: 'rgba(139,92,246,0.12)' },
  marginfi:  { name: 'MarginFi',   icon: 'assets/icons/protocols/marginfi.svg',   accent: '#F59E0B', accentBg: 'rgba(245,158,11,0.12)' },
  meteora:   { name: 'Meteora',    icon: 'assets/icons/protocols/meteora.webp',    accent: '#10B981', accentBg: 'rgba(16,185,129,0.12)' },
  marinade:  { name: 'Marinade',   icon: 'assets/icons/protocols/marinade.webp',  accent: '#22C55E', accentBg: 'rgba(34,197,94,0.12)' },
  solend:    { name: 'Solend',     icon: 'assets/icons/protocols/solend.svg',     accent: '#3B82F6', accentBg: 'rgba(59,130,246,0.12)' },
  tensor:    { name: 'Tensor',     icon: 'assets/icons/protocols/tensor.webp',    accent: '#00D4AA', accentBg: 'rgba(0,212,170,0.12)' },
  'magic-eden': { name: 'Magic Eden', icon: 'assets/icons/protocols/magiceden.webp', accent: '#E42575', accentBg: 'rgba(228,37,117,0.12)' },
  streamflow:{ name: 'Streamflow', icon: 'assets/icons/protocols/streamflow.svg', accent: '#00D4FF', accentBg: 'rgba(0,212,255,0.12)' },
  pumpfun:   { name: 'pump.fun',   icon: 'assets/icons/protocols/pumpfun.png',    accent: '#AD6DFF', accentBg: 'rgba(173,109,255,0.12)' },
  relay:     { name: 'Relay',      icon: 'assets/icons/protocols/relay.png',      accent: '#7C3AED', accentBg: 'rgba(124,58,237,0.12)' },
  default:   { name: 'Solana',     icon: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png', accent: '#9945FF', accentBg: 'rgba(153,69,255,0.10)' },
};

function getProtocolKey(action: ParsedAction): string {
  const p = (action.params['protocol'] ?? '').toLowerCase();
  if (p && PROTOCOL_CONFIGS[p]) return p;
  const t = action.type;
  // Jupiter surface: swaps, limit/DCA (incl. cancels), perpetuals, JLP — all Jupiter products.
  // A bridge is Relay's, and said Solana with Solana's mark until now.
  if (t === 'relay_bridge' || t === 'bridge' || t === 'cross_chain_swap') return 'relay';
  if (t.startsWith('relay_')) return 'relay';
  if (t === 'swap' || t === 'limit_order' || t === 'dca') return 'jupiter';
  if (t === 'cancel_dca' || t === 'cancel_limit_order' || t === 'cancel_all_limit_orders') return 'jupiter';
  if (t === 'perp_open' || t === 'perp_close' || t === 'jlp_add' || t === 'jlp_remove') return 'jupiter';
  if (t === 'stake') return p || 'jito';
  if (t === 'unstake') return p || 'jito';
  if (t.startsWith('native_stake')) return 'default';
  if (t === 'lend' || t === 'withdraw' || t === 'borrow' || t === 'repay') return p || 'jupiter';
  if (t === 'add_liquidity' || t === 'remove_liquidity') return p || 'orca';
  if (t === 'launch_token' || t === 'token_launch' || t === 'pumpfun_launch') return 'pumpfun';
  if (t.includes('pumpfun') || t.includes('pumpswap')) return 'pumpfun';
  if (t.includes('meteora')) return 'meteora';
  if (t.includes('kamino')) return 'kamino';
  if (t.includes('raydium')) return 'raydium';
  if (t.includes('marginfi')) return 'marginfi';
  if (t.includes('orca')) return 'orca';
  if (t.includes('solend')) return 'solend';
  if (t.startsWith('tensor_')) return 'tensor';
  if (t.startsWith('me_')) return 'magic-eden';
  if (t.startsWith('streamflow_')) return 'streamflow';
  if (t.startsWith('marinade_')) return 'marinade';
  if (t.startsWith('jito_') || t === 'jito_stake' || t === 'jito_unstake') return 'jito';
  // jupSOL is Jupiter's LST — surfaced under the Jupiter brand in the UI.
  if (t === 'jupsol_stake' || t === 'jupsol_unstake') return 'jupiter';
  return 'default';
}

function getActionLabel(action: ParsedAction): string {
  const labels: Record<string, string> = {
    swap: 'Swap', transfer: 'Transfer', stake: 'Stake', unstake: 'Unstake',
    lend: 'Lend / Earn', withdraw: 'Withdraw', borrow: 'Borrow', repay: 'Repay',
    add_liquidity: 'Add Liquidity', remove_liquidity: 'Remove Liquidity',
    token_launch: 'Create Token', launch_token: 'Create Token', pumpfun_launch: 'Create Token',
    pumpfun_buy: 'Buy on pump.fun', pumpfun_sell: 'Sell on pump.fun',
    pumpswap_buy: 'Buy on PumpSwap', pumpswap_sell: 'Sell on PumpSwap',
    pumpfun_token_info: 'Token Info', pumpfun_bonding_curve: 'Bonding Curve',
    pumpfun_trending: 'Trending Tokens', pumpfun_new: 'New Launches',
    pumpfun_graduating: 'Near Graduation', pumpfun_search: 'Search Tokens',
    pumpfun_koth: 'King of the Hill', pumpfun_comments: 'Token Comments',
    pumpfun_user: 'User Profile', pumpswap_pool_info: 'Pool Info',
    native_stake: 'Stake SOL (Native)', native_stake_deactivate: 'Deactivate Stake',
    native_stake_withdraw: 'Withdraw Stake', native_stake_split: 'Split Stake',
    native_stake_merge: 'Merge Stake Accounts',
    limit_order: 'Limit Order', cancel_limit_order: 'Cancel Order', cancel_all_limit_orders: 'Cancel All Orders',
    dca: 'DCA', cancel_dca: 'Cancel DCA',
    perp_open: 'Open Perp', perp_close: 'Close Perp',
    jlp_add: 'Add to JLP', jlp_remove: 'Remove from JLP',
    jupsol_stake: 'Stake for jupSOL', jupsol_unstake: 'Unstake jupSOL',
    burn: 'Burn Tokens', close_accounts: 'Close Empty Accounts',
    sns_register: 'Register .sol Domain', sns_transfer: 'Transfer Domain',
    sns_set_record: 'Set Domain Record', sns_delete: 'Delete Domain',
    sns_create_subdomain: 'Create Subdomain', sns_buy: 'Buy Domain',
    bridge: 'Bridge', squid_bridge: 'Squid Bridge', squid_status: 'Squid Status',
    cross_chain_swap: 'Cross-Chain Swap',
    // Kamino
    kamino_deposit: 'Deposit to Kamino', kamino_withdraw: 'Withdraw from Kamino',
    kamino_borrow: 'Borrow on Kamino', kamino_repay: 'Repay Kamino Loan',
    kamino_add_collateral: 'Add Collateral (Kamino)', kamino_withdraw_collateral: 'Withdraw Collateral (Kamino)',
    kamino_vault_deposit: 'Deposit to Kamino Vault', kamino_vault_withdraw: 'Withdraw from Kamino Vault',
    kamino_stake: 'Stake KMNO', kamino_unstake: 'Unstake KMNO',
    kamino_kswap: 'KSwap on Kamino',
    kamino_multiply_open: 'Open Multiply Position', kamino_multiply_add: 'Add to Multiply Position',
    kamino_multiply_withdraw: 'Withdraw from Multiply', kamino_multiply_close: 'Close Multiply Position',
    kamino_long_open: 'Open Long Position', kamino_short_open: 'Open Short Position',
    kamino_position_close: 'Close Position (Kamino)',
    kamino_claim_rewards: 'Claim Kamino Rewards',
    // MarginFi
    marginfi_deposit: 'Deposit to MarginFi', marginfi_withdraw: 'Withdraw from MarginFi',
    marginfi_borrow: 'Borrow on MarginFi', marginfi_repay: 'Repay MarginFi Loan',
    marginfi_create_account: 'Create MarginFi Account',
    marginfi_create_account_pda: 'Create MarginFi Account (PDA)',
    marginfi_close_account: 'Close MarginFi Account',
    marginfi_account_info: 'MarginFi Account Info', marginfi_banks: 'MarginFi Banks',
    marginfi_bank_detail: 'MarginFi Bank Detail', marginfi_health: 'Health Factor',
    marginfi_points: 'MarginFi Points', marginfi_user_accounts: 'My MarginFi Accounts',
    // Magic Eden
    me_buy: 'Buy NFT (Magic Eden)', me_sell: 'Sell NFT (Magic Eden)',
    me_list: 'List NFT', me_cancel_listing: 'Cancel Listing',
    me_make_offer: 'Make Offer', me_accept_offer: 'Accept Offer', me_cancel_offer: 'Cancel Offer',
    me_withdraw: 'Withdraw Magic Eden balance',
    me_buy_instruction: 'Buy NFT (Instruction)', me_buy_now: 'Buy Now', me_buy_now_transfer_nft: 'Buy & Transfer NFT',
    me_buy_cancel: 'Cancel Buy', me_buy_change_price: 'Change Buy Price',
    me_sell_now: 'Sell Now', me_sell_cancel: 'Cancel Sell', me_sell_change_price: 'Change Sell Price',
    me_mmm_create_pool: 'Create MMM Pool', me_mmm_update_pool: 'Update MMM Pool',
    me_mmm_sol_deposit_buy: 'Deposit SOL to Pool', me_mmm_sol_withdraw_buy: 'Withdraw SOL from Pool',
    me_mmm_sol_close_pool: 'Close MMM Pool', me_mmm_sol_fulfill_buy: 'Fulfill Buy',
    me_mmm_sol_fulfill_sell: 'Fulfill Sell',
    // Jito
    jito_stake: 'Stake for jitoSOL', jito_unstake: 'Unstake jitoSOL',
    jito_tip: 'Send Jito Tip', jito_bundle: 'Submit Jito Bundle',
    // Marinade
    marinade_stake: 'Stake for mSOL (Marinade)', marinade_unstake: 'Instant Unstake mSOL',
    marinade_delayed_unstake: 'Delayed Unstake mSOL', marinade_claim_ticket: 'Claim Marinade Ticket',
    // Streamflow
    streamflow_create: 'Create Stream', streamflow_cancel: 'Cancel Stream',
    streamflow_pause: 'Pause Stream', streamflow_resume: 'Resume Stream',
    streamflow_withdraw: 'Withdraw from Stream', streamflow_transfer: 'Transfer Stream',
    streamflow_topup: 'Top Up Stream', streamflow_update: 'Update Stream',
    streamflow_get_one: 'View Stream Details', streamflow_list: 'List My Streams',
    // Raydium
    raydium_swap: 'Swap on Raydium', raydium_add_liquidity: 'Add Liquidity (Raydium)',
    raydium_remove_liquidity: 'Remove Liquidity (Raydium)', raydium_create_pool: 'Create Raydium Pool',
    raydium_open_position: 'Open Position (Raydium)', raydium_close_position: 'Close Position (Raydium)',
    raydium_increase_position: 'Increase Position (Raydium)', raydium_decrease_position: 'Decrease Position (Raydium)',
    // Orca
    orca_swap: 'Swap on Orca', orca_add_liquidity: 'Add Liquidity (Orca)',
    orca_remove_liquidity: 'Remove Liquidity (Orca)', orca_open_position: 'Open Position (Orca)',
    orca_close_position: 'Close Position (Orca)', orca_increase_position: 'Increase Position (Orca)',
    orca_decrease_position: 'Decrease Position (Orca)',
    orca_collect_fees: 'Collect Fees (Orca)', orca_collect_rewards: 'Collect Rewards (Orca)',
    // Meteora
    meteora_swap: 'Swap on Meteora', meteora_dammv2_swap: 'Swap on Meteora DAMM v2',
    meteora_dammv1_swap: 'Swap on Meteora DAMM v1',
    meteora_add_liquidity: 'Add Liquidity (DLMM)', meteora_dammv2_add_liquidity: 'Add Liquidity (DAMM v2)',
    meteora_dammv1_deposit: 'Deposit (DAMM v1)', meteora_dammv1_withdraw: 'Withdraw (DAMM v1)',
    meteora_remove_liquidity: 'Remove Liquidity (DLMM)', meteora_dammv2_remove_liquidity: 'Remove Liquidity (DAMM v2)',
    meteora_create_pool: 'Create Meteora Pool',
    meteora_open_position: 'Open Position (DLMM)', meteora_close_position: 'Close Position (DLMM)',
    meteora_add_to_position: 'Add to Position (DLMM)',
    meteora_claim_fees: 'Claim Fees (DLMM)', meteora_claim_rewards: 'Claim Rewards (DLMM)',
    meteora_harvest: 'Harvest (Meteora)',
    meteora_stake: 'Stake (Meteora)', meteora_unstake: 'Unstake (Meteora)',
    meteora_vault_deposit: 'Deposit (Meteora Vault)', meteora_vault_withdraw: 'Withdraw (Meteora Vault)',
    meteora_s2e_stake: 'Stake (Stake2Earn)', meteora_s2e_unstake: 'Unstake (Stake2Earn)',
    meteora_s2e_claim_fee: 'Claim Fee (Stake2Earn)',
    meteora_s2e_cancel_unstake: 'Cancel Unstake (Stake2Earn)', meteora_s2e_withdraw: 'Withdraw (Stake2Earn)',
  };
  return labels[action.type] ?? action.type.replace(/_/g, ' ');
}

/**
 * Format an auto-computed DLMM token amount for display in a number input.
 * Strips trailing zeros, caps at 9 sig-fig precision (Solana max), and
 * never returns scientific notation — input[type=number] silently rejects
 * "1e-7" on some browsers.
 */
function formatDlmmAmount(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0';
  // Pick decimal precision based on magnitude: small amounts need more,
  // large amounts need fewer. 9 dp matches SOL/jupSOL native precision.
  const dp = n < 1e-3 ? 9 : n < 1 ? 6 : n < 100 ? 4 : 2;
  return n.toFixed(dp).replace(/\.?0+$/, '');
}


/**
 * Apply min + hint from the catalog to an amount field, but never overwrite
 * a hint a specific action config has already set (action-specific advice
 * wins over the generic catalog entry).
 */
function applyMinAmountGuard(field: FieldDef, actionType: string): FieldDef {
  const cfg = ACTION_MIN_AMOUNT[actionType];
  if (!cfg) return field;
  return {
    ...field,
    min: field.min ?? cfg.amount,
    hint: field.hint ?? cfg.hint,
    step: field.step ?? (cfg.amount < 0.001 ? '0.000001' : cfg.amount < 1 ? '0.001' : '0.01'),
  };
}

/**
 * Build the field schema for an action. `liveParams` is the *current*
 * editable params (post-flip, post-normalization) — for fields whose schema
 * depends on token / mode state (notably swap: ExactIn ↔ ExactOut affects
 * label, suffix, ordering), we must read from the live edit-state, not the
 * frozen LLM emission. Defaults to `action.params` for the cold path.
 */
/**
 * The chains Relay bridges between, by name.
 *
 * Solana's id is 792703809 — the service had 900 hardcoded in two places and
 * the card suggested it as a placeholder, so anyone who followed the hint was
 * naming a chain that does not exist.
 */
/** Relay's id for Solana. Bridging here needs no second wallet. */
export const RELAY_SOLANA_CHAIN_ID = 792703809;

/** What we know about one bridgeable token. `decimals` is optional because the
 *  quote names a token without scaling it; only the currency list carries it. */
interface RelayTokenMeta {
  symbol: string;
  name: string;
  logoURI: string | null;
  decimals?: number;
}

/** The slice of EIP-1193 this card uses. */
interface EvmProvider {
  request(args: { method: string; params?: unknown[] }): Promise<any>;
}

export const RELAY_CHAINS: Array<{ label: string; value: string }> = [
  { label: 'Solana',    value: '792703809' },
  { label: 'Ethereum',  value: '1' },
  { label: 'Base',      value: '8453' },
  { label: 'Arbitrum',  value: '42161' },
  { label: 'Optimism',  value: '10' },
  { label: 'Polygon',   value: '137' },
  { label: 'BNB Chain', value: '56' },
  { label: 'Avalanche', value: '43114' },
  { label: 'Linea',     value: '59144' },
  { label: 'Scroll',    value: '534352' },
  { label: 'zkSync Era', value: '324' },
];

function getActionFields(
  action: ParsedAction,
  liveParams?: Record<string, string | undefined>,
): FieldDef[] {
  const params = liveParams ?? action.params;
  const fields: FieldDef[] = [];
  const t = action.type;
  if (t === 'launch_token' || t === 'token_launch' || t === 'pumpfun_launch') return [];
  if (t === 'pumpfun_buy' || t === 'pumpswap_buy') {
    fields.push(
      { key: 'mint', label: 'Token Mint', type: 'address', placeholder: 'Token mint address...', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0.1', suffix: 'SOL', required: true, min: 0, step: '0.001' },
      { key: 'slippage', label: 'Slippage', type: 'number', placeholder: '10', suffix: '%', half: true, min: 0, max: 100, step: '0.1',
        hint: t === 'pumpswap_buy' ? 'PumpSwap AMM' : 'Bonding curve' },
      { key: 'priorityFee', label: 'Priority Fee', type: 'number', placeholder: '0.0005', suffix: 'SOL', half: true, min: 0, step: '0.0001' },
    );
    return fields;
  }
  if (t === 'pumpfun_sell' || t === 'pumpswap_sell') {
    fields.push(
      { key: 'mint', label: 'Token Mint', type: 'address', placeholder: 'Token mint address...', required: true },
      { key: 'amount', label: 'Token Amount', type: 'number', placeholder: '1000000', required: true, min: 0,
        hint: 'Token units (not SOL) — "all" sells entire balance' },
      { key: 'slippage', label: 'Slippage', type: 'number', placeholder: '10', suffix: '%', half: true, min: 0, max: 100, step: '0.1' },
      { key: 'priorityFee', label: 'Priority Fee', type: 'number', placeholder: '0.0005', suffix: 'SOL', half: true, min: 0, step: '0.0001' },
    );
    return fields;
  }
  if (t === 'swap') {
    // Raydium-style dual-amount layout: each token row carries its OWN amount
    // input inline. The user types into either side; whichever was typed last
    // is the EXACT side (drives `swapMode`), and the other side renders as a
    // live counterparty estimate from `swapEstimate`. The separate "Amount"
    // field that used to live below the tokens is gone — duplicated info.
    fields.push(
      { key: 'inputMint',  label: 'From', type: 'token', required: true },
      { key: 'outputMint', label: 'To',   type: 'token', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
      {
        key: 'onlyDirectRoutes',
        label: 'Direct routes only',
        type: 'toggle',
        half: true,
        hint: 'Skip multi-hop routing — single AMM pool only. Off gives a better price most of the time.',
      },
    );
  } else if (t === 'transfer') {
    // Fields are the swap card's panels below (see the TRANSFER block in the
    // template). Left as a generic three-row form, this card looked nothing
    // like the swap it sits beside — a thin bordered amount, a disabled token
    // chip and an address box.
    return [];
  } else if (t === '__transfer_legacy__') {
    fields.push(
      { key: 'to', label: 'Recipient', type: 'address', placeholder: 'Enter wallet address...', required: true },
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'stake' || t === 'unstake') {
    fields.push({ key: 'amount', label: 'Amount (SOL)', type: 'number', placeholder: '0', suffix: 'SOL', required: true });
  } else if (t === 'native_stake') {
    // Amount only. The validator is chosen in the picker below, which used to
    // sit UNDER a generic "Validator Vote Account" address row asking for the
    // same thing — so the card put the question twice and answered it once.
    // No fields at all. The amount is a swap-style "You stake" panel below —
    // a generic bordered form row under a swap-shaped card is what made this
    // read as a different product from the swap it sits next to.
  } else if (t === 'native_stake_deactivate') {
    // No fields: the account is chosen from the user's own stake accounts in
    // the picker. A stake account address is derived, never chosen, and shown
    // nowhere else — asking someone to paste one is asking for something they
    // do not have.
  } else if (t === 'native_stake_withdraw') {
    fields.push(
      // SOL, not lamports. The backend alone among the stake actions read this
      // as lamports, so "0.5" meant half a billionth of a SOL — and its
      // `parse().unwrap()` panicked on the decimal point before it got there.
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', hint: 'SOL, or "all" to close the account' },
    );
  } else if (t === 'native_stake_split') {
    fields.push(
      { key: 'amount', label: 'Amount to Split', type: 'number', placeholder: '1', suffix: 'SOL', required: true, min: 1, hint: 'Minimum 1 SOL' },
    );
  } else if (t === 'native_stake_merge') {
    // Both accounts come from the picker: destination first, then source.
  } else if (t === 'borrow') {
    // Borrowing needs collateral — you don't spend the debt token, you post an
    // asset to back the loan. Without these fields the loan had no collateral
    // to build against (and the card looked like a plain "spend USDC" form).
    fields.push(
      { key: 'token', label: 'Borrow Token', type: 'token', required: true },
      { key: 'amount', label: 'Borrow Amount', type: 'number', placeholder: '0', required: true },
      { key: 'collateral', label: 'Collateral Token', type: 'token', required: true, hint: 'Asset you deposit to back the loan (e.g. SOL, jitoSOL, JLP)' },
      { key: 'collateralAmount', label: 'Collateral Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (['lend', 'withdraw', 'repay'].includes(t)) {
    fields.push(
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'add_liquidity' || t === 'remove_liquidity') {
    fields.push(
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  // ── Jupiter Advanced Orders ────────────────────────────────────────────────
  } else if (t === 'limit_order') {
    fields.push(
      { key: 'inputMint', label: 'Sell Token', type: 'token', required: true },
      { key: 'outputMint', label: 'Buy Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'targetPrice', label: 'Target Price', type: 'number', placeholder: '0', required: true, hint: 'Output tokens received per 1 input token sold' },
      { key: 'expirySeconds', label: 'Expiry (sec)', type: 'number', placeholder: '86400', half: true, hint: 'Leave blank for GTC' },
    );
  } else if (t === 'cancel_limit_order') {
    fields.push(
      { key: 'order', label: 'Order Address', type: 'address', placeholder: 'Order pubkey...', required: true },
    );
  } else if (t === 'cancel_all_limit_orders') {
    // no editable fields — operates on all open orders
  } else if (t === 'dca') {
    fields.push(
      { key: 'inputMint', label: 'Spend Token', type: 'token', required: true },
      { key: 'outputMint', label: 'Buy Token', type: 'token', required: true },
      { key: 'totalAmount', label: 'Total Amount', type: 'number', placeholder: '0', required: true },
      { key: 'numberOfOrders', label: 'Orders', type: 'number', placeholder: '10', required: true, min: 1, half: true },
      { key: 'intervalSeconds', label: 'Interval (sec)', type: 'number', placeholder: '86400', required: true, half: true, hint: '3600=h 86400=d 604800=w' },
      { key: 'minPrice', label: 'Min Price', type: 'number', placeholder: '0', half: true },
      { key: 'maxPrice', label: 'Max Price', type: 'number', placeholder: '0', half: true },
    );
  } else if (t === 'cancel_dca') {
    // No editable fields — the DCA order address (`order`) is resolved
    // server-side (the LLM can't see it, and the user shouldn't have to paste a
    // pubkey). It arrives as order="self" from chat, or as an explicit address
    // from the DCA card's Cancel button; both pass straight through to build.
  // ── Jupiter Perpetuals ────────────────────────────────────────────────────
  } else if (t === 'perp_open') {
    const perpMarkets = [{ label: 'SOL', value: 'SOL' }, { label: 'wETH', value: 'wETH' }, { label: 'wBTC', value: 'wBTC' }];
    fields.push(
      { key: 'market', label: 'Market', type: 'select', options: perpMarkets, required: true },
      { key: 'side', label: 'Side', type: 'select', options: [{ label: 'Long', value: 'long' }, { label: 'Short', value: 'short' }], required: true },
      { key: 'collateralAmount', label: 'Collateral', type: 'number', placeholder: '1', required: true, min: 0 },
      { key: 'leverage', label: 'Leverage', type: 'number', placeholder: '2', required: true, min: 1, max: 10, step: '0.1', half: true },
      { key: 'sizeUsd', label: 'Size USD', type: 'number', placeholder: '0', half: true, hint: 'Overrides collateral×leverage' },
    );
  } else if (t === 'perp_close') {
    const perpMarkets = [{ label: 'SOL', value: 'SOL' }, { label: 'wETH', value: 'wETH' }, { label: 'wBTC', value: 'wBTC' }];
    fields.push(
      { key: 'market', label: 'Market', type: 'select', options: perpMarkets, required: true },
      { key: 'side', label: 'Side', type: 'select', options: [{ label: 'Long', value: 'long' }, { label: 'Short', value: 'short' }], required: true },
    );
  // ── Jupiter JLP Liquidity ──────────────────────────────────────────���─────
  } else if (t === 'jlp_add') {
    const jlpTokens = [{ label: 'SOL', value: 'SOL' }, { label: 'USDC', value: 'USDC' }, { label: 'USDT', value: 'USDT' }, { label: 'wETH', value: 'wETH' }, { label: 'wBTC', value: 'wBTC' }];
    fields.push(
      { key: 'token', label: 'Deposit Token', type: 'select', options: jlpTokens, required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'jlp_remove') {
    const jlpTokens = [{ label: 'SOL', value: 'SOL' }, { label: 'USDC', value: 'USDC' }, { label: 'USDT', value: 'USDT' }, { label: 'wETH', value: 'wETH' }, { label: 'wBTC', value: 'wBTC' }];
    fields.push(
      { key: 'amount', label: 'JLP Amount', type: 'number', placeholder: '0', required: true },
      { key: 'token', label: 'Receive Token', type: 'select', options: jlpTokens, required: true },
    );
  // ── Jupiter LST ──────────────────────────────────────────────────────────
  } else if (t === 'jupsol_stake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'SOL', required: true, min: 0 });
  } else if (t === 'jupsol_unstake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'jupSOL', required: true, min: 0 });
  // ── SNS transactional ────────────────────────────────────────────────────
  } else if (t === 'sns_register') {
    fields.push(
      { key: 'domain', label: 'Domain (without .sol)', type: 'text', placeholder: 'myname', required: true },
      { key: 'space', label: 'Record space (bytes)', type: 'number', placeholder: '1000', half: true, min: 0 },
    );
  } else if (t === 'sns_transfer') {
    fields.push(
      { key: 'domain', label: 'Domain (without .sol)', type: 'text', placeholder: 'myname', required: true },
      { key: 'newOwner', label: 'New Owner', type: 'address', placeholder: 'Recipient wallet...', required: true },
    );
  } else if (t === 'sns_set_record') {
    fields.push(
      { key: 'domain', label: 'Domain', type: 'text', placeholder: 'myname', required: true },
      { key: 'record', label: 'Record Type', type: 'text', placeholder: 'url', required: true, hint: 'e.g. url, email, twitter' },
      { key: 'value', label: 'Value', type: 'text', placeholder: 'https://...', required: true },
    );
  } else if (t === 'sns_delete') {
    fields.push(
      { key: 'domain', label: 'Domain', type: 'text', placeholder: 'myname', required: true },
    );
  } else if (t === 'sns_create_subdomain') {
    fields.push(
      { key: 'domain', label: 'Parent Domain', type: 'text', placeholder: 'myname', required: true },
      { key: 'subdomain', label: 'Subdomain', type: 'text', placeholder: 'sub', required: true },
    );
  // ── burn / close_accounts ────────────────────────────────────────────────
  } else if (t === 'burn') {
    fields.push(
      // A token chip, not an address box. Burning is irreversible and the card
      // showed the one thing that cannot be checked by eye — a base58 mint —
      // instead of the token's name and icon. The chip is pickable, so the
      // token can be changed without retyping a mint.
      { key: 'mint', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', hint: '"all" burns the entire balance' },
    );
  } else if (t === 'close_accounts') {
    // no required params — closes all empty token accounts
  // ── Raydium ──────────────────────────────────────────────────────────────
  } else if (t === 'raydium_swap') {
    fields.push(
      { key: 'inputMint', label: 'From Token', type: 'token', required: true },
      { key: 'outputMint', label: 'To Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'raydium_add_liquidity') {
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'amountA', label: 'Amount A', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Amount B', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'raydium_remove_liquidity') {
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'lpAmount', label: 'LP Amount', type: 'text', placeholder: 'all', required: true, hint: '"all" removes entire position' },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'raydium_create_pool') {
    fields.push(
      { key: 'mintA', label: 'Token A Mint', type: 'token', required: true },
      { key: 'mintB', label: 'Token B Mint', type: 'token', required: true },
      { key: 'initialPrice', label: 'Initial Price (A per B)', type: 'number', placeholder: '1', required: true, min: 0 },
      { key: 'feeRate', label: 'Fee Rate', type: 'number', placeholder: '2500', suffix: 'bps', hint: '2500=0.25%' },
      { key: 'tickSpacing', label: 'Tick Spacing', type: 'number', placeholder: '60', half: true },
    );
  } else if (t === 'raydium_open_position') {
    // Raydium CLMM concentrated liquidity.
    //
    // The dedicated `<div class="clmm-form">` template (rendered by
    // action-card.html when `isRaydiumOpenPosition()` is true) replaces this
    // generic field list — it shows: current price + 24h range, dual-amount
    // inputs (auto-balanced via `clmmRatio`), `±0.5% / ±1% / ±5% / ±10% /
    // Full Range` preset chips, and slippage in an "Advanced" expander.
    //
    // We still register the underlying fields here so saveDraft/restore +
    // payload normalization picks them up — the template just supplies a
    // bespoke layout for them.
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'amountA', label: 'Token A Amount', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Token B Amount', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'minPrice', label: 'Min Price', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'maxPrice', label: 'Max Price', type: 'number', placeholder: '0', required: true, half: true },
      {
        key: 'slippageBps',
        label: 'Slippage',
        type: 'number',
        placeholder: '0.5',
        suffix: '%',
        half: true,
        min: 0,
        max: 100,
        step: '0.1',
        divisor: 100,
      },
    );
  } else if (t === 'raydium_close_position') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
    );
  } else if (t === 'raydium_increase_position') {
    // `inputMint` decides WHICH side the deposit amount denominates (the SDK
    // derives the paired amount from it). It was missing from this list, so the
    // builder received no side at all and silently assumed token B.
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'inputMint', label: 'Deposit Token', type: 'token', required: true },
      { key: 'inputAmount', label: 'Deposit Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'raydium_decrease_position') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'liquidity', label: 'Liquidity', type: 'text', placeholder: 'all', required: true, hint: '"all" removes full position' },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  // ── Orca ─────────────────────────────────────────────────────────────────
  } else if (t === 'orca_swap') {
    fields.push(
      { key: 'inputMint', label: 'From Token', type: 'token', required: true },
      { key: 'outputMint', label: 'To Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_add_liquidity') {
    fields.push(
      { key: 'whirlpool', label: 'Whirlpool', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'amountA', label: 'Amount A', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Amount B', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_remove_liquidity') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'liquidity', label: 'Liquidity', type: 'text', placeholder: 'all', required: true, hint: '"all" removes full position' },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_open_position') {
    fields.push(
      { key: 'whirlpool', label: 'Whirlpool', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'inputAmount', label: 'Deposit Amount', type: 'number', placeholder: '0', required: true },
      { key: 'minPrice', label: 'Min Price', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'maxPrice', label: 'Max Price', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_close_position') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
    );
  } else if (t === 'orca_increase_position') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'inputAmount', label: 'Deposit Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_decrease_position') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'liquidity', label: 'Liquidity (raw units)', type: 'text', placeholder: 'e.g. 500000000', hint: 'OR use token amount below' },
      { key: 'inputMint', label: 'Token', type: 'token' },
      { key: 'inputAmount', label: 'Token Amount', type: 'number', placeholder: '0' },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'orca_collect_fees' || t === 'orca_collect_rewards') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
    );
  // ── Meteora ───────────────────────────────────────────────────────────────
  } else if (t === 'meteora_swap' || t === 'meteora_dammv2_swap' || t === 'meteora_dammv1_swap') {
    fields.push(
      { key: 'inputMint', label: 'From Token', type: 'token', required: true },
      { key: 'outputMint', label: 'To Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
      { key: 'pool', label: 'Pool', type: 'address', placeholder: 'Optional — leave blank to auto-route' },
    );
  } else if (t === 'meteora_add_liquidity' || t === 'meteora_dammv2_add_liquidity') {
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
    );
    // DLMM-specific extras: when the action carries a `binStep` (set by
    // QueryCard's "Use this pool" CTA from a DLMM row) we expose a Spread
    // (± bins) + Strategy (Spot/Curve/Bid-Ask) picker. The amount-ratio
    // engine in the component reads these fields and auto-fills the
    // complementary amount on edit. DAMM v1/v2 keep the bare form.
    const isDlmm = !!action.params['binStep'];
    if (isDlmm) {
      fields.push(
        { key: 'binSpread', label: 'Range (± bins)', type: 'number',
          placeholder: '15', half: true, min: 1, max: 200, step: '1',
          hint: 'Symmetric range around the active bin' },
        { key: 'strategy', label: 'Strategy', type: 'select', half: true,
          options: [
            { label: 'Spot — equal weight per bin',  value: 'spot' },
            { label: 'Curve — bell shape at price',  value: 'curve' },
            { label: 'Bid-Ask — heavy at edges',     value: 'bidask' },
          ],
        },
      );
    }
    fields.push(
      { key: 'amountA', label: 'Amount A', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Amount B', type: 'number', placeholder: '0', required: true, half: true },
    );
  } else if (t === 'meteora_remove_liquidity') {
    fields.push(
      { key: 'position', label: 'Position', type: 'address', placeholder: 'Position address...', required: true },
      { key: 'bpsToRemove', label: 'Remove %', type: 'number', placeholder: '10000', suffix: 'bps', hint: '10000 = 100%', half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '1', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'meteora_dammv2_remove_liquidity') {
    fields.push(
      { key: 'pool', label: 'Pool', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'lpAmount', label: 'LP Amount', type: 'number', placeholder: '0', required: true },
      { key: 'positionNft', label: 'Position NFT', type: 'address', placeholder: 'Position NFT mint...' },
    );
  } else if (t === 'meteora_open_position' || t === 'meteora_add_to_position') {
    const dlmmStrategies = [{ label: 'Spot', value: 'spot' }, { label: 'Curve', value: 'curve' }, { label: 'Uniform', value: 'uniform' }];
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'amountA', label: 'Amount A', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Amount B', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'strategy', label: 'Distribution', type: 'select', options: dlmmStrategies, hint: 'Bin distribution shape' },
    );
  } else if (t === 'meteora_close_position') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
    );
  } else if (t === 'meteora_claim_fees' || t === 'meteora_claim_rewards' || t === 'meteora_harvest') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
    );
  } else if (t === 'meteora_create_pool') {
    const dlmmStrategies = [{ label: 'Spot', value: 'spot' }, { label: 'Curve', value: 'curve' }, { label: 'Uniform', value: 'uniform' }];
    fields.push(
      { key: 'tokenA', label: 'Token A Mint', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B Mint', type: 'token', required: true },
      { key: 'binStep', label: 'Bin Step', type: 'number', placeholder: '10', required: true, min: 1, hint: 'e.g. 10, 20, 80' },
      { key: 'initialPrice', label: 'Initial Price', type: 'number', placeholder: '0', required: true, min: 0 },
      { key: 'baseFee', label: 'Base Fee', type: 'number', placeholder: '10', suffix: 'bps', half: true },
      { key: 'strategy', label: 'Distribution', type: 'select', options: dlmmStrategies },
    );
  } else if (t === 'meteora_stake') {
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'meteora_unstake') {
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', required: true, hint: '"all" unstakes full position' },
    );
  // ── Meteora DAMM v1 (legacy AMM) — deposit / withdraw ────────────────────
  } else if (t === 'meteora_dammv1_deposit') {
    fields.push(
      { key: 'poolId', label: 'Pool', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token' },
      { key: 'tokenB', label: 'Token B', type: 'token' },
      { key: 'amountA', label: 'Amount A', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'amountB', label: 'Amount B', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'meteora_dammv1_withdraw') {
    fields.push(
      { key: 'poolId', label: 'Pool', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'lpAmount', label: 'LP Amount', type: 'number', placeholder: '0', required: true,
        hint: 'LP tokens to redeem (use "all" for full position)' },
    );
  // ── Meteora Dynamic Vault — single-asset yield deposit/withdraw ─────────
  } else if (t === 'meteora_vault_deposit') {
    fields.push(
      { key: 'tokenMint', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true,
        hint: 'Deposited as the underlying asset; vault auto-allocates across yield sources.' },
    );
  } else if (t === 'meteora_vault_withdraw') {
    fields.push(
      { key: 'tokenMint', label: 'Token', type: 'token', required: true },
      { key: 'unmintAmount', label: 'LP Amount', type: 'number', placeholder: '0', required: true,
        hint: 'Vault LP tokens to redeem' },
    );
  // ── Meteora Stake-to-Earn (m3m3) — single-token stake / unstake / claim ─
  } else if (t === 'meteora_s2e_stake') {
    fields.push(
      { key: 'vault', label: 'Vault', type: 'address', placeholder: 'Stake2Earn vault...', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'meteora_s2e_unstake') {
    fields.push(
      { key: 'vault', label: 'Vault', type: 'address', placeholder: 'Stake2Earn vault...', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', required: true,
        hint: '"all" unstakes the full escrow' },
    );
  } else if (t === 'meteora_s2e_claim_fee') {
    fields.push(
      { key: 'vault', label: 'Vault', type: 'address', placeholder: 'Stake2Earn vault...', required: true },
      { key: 'maxAmount', label: 'Max Amount', type: 'number', placeholder: '0',
        hint: 'Optional cap on claim size; leave blank for full' },
    );
  } else if (t === 'meteora_s2e_cancel_unstake' || t === 'meteora_s2e_withdraw') {
    fields.push(
      { key: 'vault', label: 'Vault', type: 'address', placeholder: 'Stake2Earn vault...', required: true },
      { key: 'escrow', label: 'Escrow', type: 'address', placeholder: 'Escrow account...', required: true },
    );
  // ── Jito ─────────────────────────────────────────────────────────────────
  } else if (t === 'jito_stake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'SOL', required: true, min: 0 });
  } else if (t === 'jito_unstake') {
    fields.push(
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'jitoSOL', required: true, min: 0 },
    );
  } else if (t === 'jito_tip') {
    fields.push({ key: 'amount', label: 'Tip Amount', type: 'number', placeholder: '0.001', suffix: 'SOL', required: true, min: 0, hint: 'Tip sent to Jito validators' });
  } else if (t === 'jito_bundle') {
    fields.push(
      { key: 'transactions', label: 'Transactions', type: 'textarea', placeholder: 'Comma-separated base64 transactions...', required: true },
      { key: 'tipAmount', label: 'Tip Amount', type: 'number', placeholder: '0.001', suffix: 'SOL', half: true, min: 0 },
    );
  // ── Marinade ──────────────────────────────────────────────────────────────
  } else if (t === 'marinade_stake') {
    // No slippage field: Marinade's deposit instruction is a direct mint at the
    // pool's atomic rate (mSOL = SOL / msol_price), no swap, no AMM, nothing
    // to slip against. Slippage only applies to instant liquid-unstake where
    // the fee varies with reserve depth.
    fields.push(
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'SOL', required: true, min: 0 },
    );
  } else if (t === 'marinade_unstake') {
    fields.push(
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'mSOL', required: true, min: 0 },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'marinade_delayed_unstake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'mSOL', required: true, min: 0, hint: '~2 epochs to claim (~4-6 days)' });
  } else if (t === 'marinade_claim_ticket') {
    fields.push({ key: 'ticketAccount', label: 'Ticket Account', type: 'address', placeholder: 'Ticket pubkey...', required: true, hint: 'Ticket ready after ~2 epochs' });
  // ── Streamflow ────────────────────────────────────────────────────────────
  } else if (t === 'streamflow_create') {
    fields.push(
      { key: 'recipient', label: 'Recipient', type: 'address', placeholder: 'Recipient wallet...', required: true },
      { key: 'mint', label: 'Token Mint', type: 'token', required: true },
      { key: 'amount', label: 'Total Amount', type: 'number', placeholder: '0', required: true },
      { key: 'period', label: 'Unlock Period (sec)', type: 'number', placeholder: '86400', required: true, hint: '86400=daily 3600=hourly' },
      { key: 'amountPerPeriod', label: 'Amount / Period', type: 'number', placeholder: '0', required: true },
      { key: 'start', label: 'Start Time (unix)', type: 'number', placeholder: '0', hint: '0 = start immediately' },
      { key: 'cliff', label: 'Cliff Time (unix)', type: 'number', placeholder: '0', hint: '0 = no cliff' },
      { key: 'cliffAmount', label: 'Cliff Amount', type: 'number', placeholder: '0', hint: 'One-time unlock at cliff' },
    );
  } else if (t === 'streamflow_cancel') {
    fields.push({ key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true });
  } else if (t === 'streamflow_pause') {
    fields.push({ key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true });
  } else if (t === 'streamflow_resume') {
    fields.push({ key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true });
  } else if (t === 'streamflow_withdraw') {
    fields.push(
      { key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', hint: '"all" withdraws full available balance' },
    );
  } else if (t === 'streamflow_transfer') {
    fields.push(
      { key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true },
      { key: 'newRecipient', label: 'New Recipient', type: 'address', placeholder: 'New recipient wallet...', required: true },
    );
  } else if (t === 'streamflow_topup') {
    fields.push(
      { key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true },
      { key: 'amount', label: 'Amount to Add', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'streamflow_update') {
    fields.push(
      { key: 'streamId', label: 'Stream ID', type: 'address', placeholder: 'Stream account address...', required: true },
      { key: 'amountPerPeriod', label: 'New Amount / Period', type: 'number', placeholder: '0', hint: 'Leave blank to keep current' },
    );
  // ── Kamino ───────────────────────────────────────────────────────────────
  } else if (t === 'kamino_deposit' || t === 'kamino_borrow') {
    fields.push(
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'kamino_withdraw' || t === 'kamino_repay') {
    fields.push(
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', required: true, hint: '"all" = full balance' },
    );
  } else if (t === 'kamino_add_collateral') {
    fields.push(
      { key: 'token', label: 'Collateral Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'kamino_withdraw_collateral') {
    fields.push(
      { key: 'token', label: 'Collateral Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', required: true },
    );
  } else if (t === 'kamino_kswap') {
    fields.push(
      { key: 'tokenIn', label: 'From Token', type: 'token', required: true },
      { key: 'tokenOut', label: 'To Token', type: 'token', required: true },
      { key: 'amountIn', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'maxSlippageBps', label: 'Max Slippage', type: 'number', placeholder: '50', suffix: 'bps', half: true, min: 0, max: 10000 },
    );
  } else if (t === 'kamino_vault_deposit') {
    fields.push(
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'kamino_vault_withdraw') {
    fields.push(
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'ktokenAmount', label: 'kToken Amount', type: 'text', placeholder: 'all', required: true, hint: '"all" = full position' },
    );
  } else if (t === 'kamino_stake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'KMNO', required: true });
  } else if (t === 'kamino_unstake') {
    fields.push({ key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', required: true, hint: '"all" unstakes everything' });
  } else if (t === 'kamino_long_open' || t === 'kamino_short_open') {
    fields.push(
      { key: 'collateralToken', label: 'Collateral Token', type: 'token', required: true },
      { key: 'collateralAmount', label: 'Collateral Amount', type: 'number', placeholder: '0', required: true },
      { key: 'leverage', label: 'Leverage', type: 'number', placeholder: '2', required: true, min: 1, max: 10, step: '0.1', half: true },
    );
  } else if (t === 'kamino_multiply_open') {
    // A Multiply position is keyed by its collateral/debt token pair. The
    // backend loads/creates the obligation from (token, debtToken); debt
    // defaults to USDC but is usually SOL for LST strategies.
    // Collateral + debt read as the pool PAIR, so pair them on one row (equal
    // chips); amount and leverage are the editable inputs below, full width.
    fields.push(
      { key: 'token', label: 'Collateral Token', type: 'token', required: true, half: true },
      { key: 'debtToken', label: 'Debt Token', type: 'token', required: false, half: true, hint: 'Borrowed & looped; defaults to USDC' },
      { key: 'amount', label: 'Collateral Amount', type: 'number', placeholder: '0', required: true },
      { key: 'leverage', label: 'Leverage', type: 'number', placeholder: '2', required: true, min: 1, max: 10, step: '0.1' },
    );
  } else if (t === 'kamino_multiply_add') {
    fields.push(
      { key: 'token', label: 'Collateral Token', type: 'token', required: true },
      { key: 'amount', label: 'Additional Amount', type: 'number', placeholder: '0', required: true },
      { key: 'debtToken', label: 'Debt Token', type: 'token', required: false, hint: 'The position\'s debt token; defaults to USDC' },
    );
  } else if (t === 'kamino_multiply_withdraw') {
    fields.push(
      { key: 'token', label: 'Collateral Token', type: 'token', required: true },
      { key: 'amount', label: 'Withdraw Amount', type: 'number', placeholder: '0', required: true, hint: 'Collateral to withdraw (partial deleverage)' },
      { key: 'debtToken', label: 'Debt Token', type: 'token', required: false, hint: 'The position\'s debt token; defaults to USDC' },
    );
  } else if (t === 'kamino_multiply_close' || t === 'kamino_position_close') {
    // Close is keyed by the coll/debt pair; token is required, debt optional.
    if (t === 'kamino_multiply_close') {
      fields.push(
        { key: 'token', label: 'Collateral Token', type: 'token', required: true },
        { key: 'debtToken', label: 'Debt Token', type: 'token', required: false, hint: 'The position\'s debt token; defaults to USDC' },
      );
    }
  } else if (t === 'kamino_claim_rewards') {
    // no required params — claims all pending rewards
  } else if (t === 'kamino_liquidity_deposit') {
    fields.push(
      { key: 'strategy', label: 'Strategy', type: 'text', placeholder: 'strategy address', required: true, hint: 'Kamino CLMM strategy address' },
      { key: 'amountA', label: 'Amount (token A)', type: 'number', placeholder: '0', required: true, hint: 'The other side is auto-computed from the pool ratio' },
    );
  } else if (t === 'kamino_liquidity_withdraw') {
    fields.push(
      { key: 'strategy', label: 'Strategy', type: 'text', placeholder: 'strategy address', required: true },
      { key: 'shares', label: 'Shares', type: 'text', placeholder: 'all', required: true, hint: '"all" withdraws the full position' },
    );
  // ── MarginFi ──────────────────────────────────────────────────────────────
  } else if (t === 'marginfi_deposit') {
    fields.push(
      { key: 'bank', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'depositUpToLimit', label: 'Deposit up to limit', type: 'toggle', half: true },
    );
  } else if (t === 'marginfi_withdraw') {
    fields.push(
      { key: 'bank', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'withdrawAll', label: 'Withdraw all', type: 'toggle', half: true },
    );
  } else if (t === 'marginfi_borrow') {
    fields.push(
      { key: 'bank', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'marginfi_repay') {
    fields.push(
      { key: 'bank', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'repayAll', label: 'Repay all', type: 'toggle', half: true },
    );
  } else if (t === 'marginfi_create_account_pda') {
    fields.push({ key: 'accountIndex', label: 'Account Index', type: 'number', placeholder: '0', half: true, hint: '0 = first account' });
  } else if (t === 'marginfi_create_account' || t === 'marginfi_close_account') {
    // no user-editable fields
  } else if (t === 'marginfi_account_info' || t === 'marginfi_health') {
    fields.push({ key: 'wallet', label: 'Wallet (optional)', type: 'address', placeholder: 'Wallet address...' });
  } else if (t === 'marginfi_bank_detail') {
    fields.push({ key: 'bank', label: 'Token', type: 'token', required: true });
  } else if (t === 'marginfi_banks') {
    fields.push({ key: 'limit', label: 'Limit', type: 'number', placeholder: '20', half: true });
  } else if (t === 'marginfi_points' || t === 'marginfi_user_accounts') {
    fields.push({ key: 'wallet', label: 'Wallet (optional)', type: 'address', placeholder: 'Wallet address...' });
  // ── Magic Eden TX ─────────────────────────────────────────────────────────
  // ── Magic Eden ────────────────────────────────────────────────────────────
  //
  // These used to ask the user to type a mint address, a seller's wallet and a
  // token ATA. Nobody has those to hand, and the backend now reads all of them
  // off the live listing or offer — asking for them was asking the user to
  // re-derive what we already know, and getting one wrong meant a card that
  // failed on submit.
  //
  // What is left is what only the user can decide: the price, and how long the
  // offer stands. The mint travels in the params from the row that spawned the
  // action; when it did not (someone typed the request), it is the one field
  // still worth showing.
  } else if (t === 'me_buy' || t === 'me_buy_now' || t === 'me_buy_instruction'
             || t === 'me_buy_now_transfer_nft') {
    // Nothing to fill in — the listing sets the price, and paying anything
    // else is not a thing Magic Eden will accept.
    if (!params['mintAddress'] && !params['tokenMint']) {
      fields.push({ key: 'mintAddress', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true });
    }
  } else if (t === 'me_list' || t === 'me_sell') {
    // Price and expiry are rendered by the amount panel, not as form rows.
    if (!params['mintAddress'] && !params['tokenMint']) {
      fields.push({ key: 'mintAddress', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true });
    }
  } else if (t === 'me_cancel_listing' || t === 'me_sell_cancel'
             || t === 'me_cancel_offer' || t === 'me_buy_cancel'
             || t === 'me_accept_offer' || t === 'me_sell_now') {
    // Cancelling or accepting has no parameters at all: which listing, which
    // offer, at what price — all of it is looked up.
    if (!params['mintAddress'] && !params['tokenMint']) {
      fields.push({ key: 'mintAddress', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true });
    }
  } else if (t === 'me_make_offer') {
    if (!params['mintAddress'] && !params['tokenMint']) {
      fields.push({ key: 'mintAddress', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true });
    }
  } else if (t === 'me_buy_change_price' || t === 'me_sell_change_price') {
    // Only the NEW price. The current one is read from the live listing —
    // asking the user to restate it is how the two disagree.
    if (!params['mintAddress'] && !params['tokenMint']) {
      fields.push({ key: 'mintAddress', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true });
    }
    // The new price is the amount panel's.
  } else if (t === 'me_mmm_create_pool') {
    fields.push(
      { key: 'collectionSymbol', label: 'Collection', type: 'text', placeholder: 'e.g. mad_lads', required: true },
      { key: 'spotPrice', label: 'Starting price', type: 'number', placeholder: '0', suffix: 'SOL', required: true, half: true },
      { key: 'curveType', label: 'Curve', type: 'text', placeholder: 'linear or exp', required: true, half: true },
      { key: 'curveDelta', label: 'Step', type: 'number', placeholder: '0', required: true, half: true,
        hint: 'SOL per fill for linear, basis points for exp' },
      { key: 'lpFeeBp', label: 'Pool fee', type: 'number', placeholder: '0', suffix: 'bps', half: true },
    );
  } else if (t === 'me_mmm_update_pool') {
    fields.push(
      { key: 'pool', label: 'Pool', type: 'address', placeholder: 'Pool address…', required: true },
      { key: 'spotPrice', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true, half: true },
      { key: 'curveType', label: 'Curve', type: 'text', placeholder: 'linear or exp', required: true, half: true },
      { key: 'curveDelta', label: 'Step', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'me_mmm_sol_close_pool') {
    fields.push({ key: 'pool', label: 'Pool', type: 'address', placeholder: 'Pool address…', required: true });
  } else if (t === 'me_mmm_sol_deposit_buy' || t === 'me_mmm_sol_withdraw_buy') {
    fields.push(
      { key: 'pool', label: 'Pool', type: 'address', placeholder: 'Pool address…', required: true },
      { key: 'paymentAmount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_mmm_sol_fulfill_buy' || t === 'me_mmm_sol_fulfill_sell') {
    fields.push(
      { key: 'pool', label: 'Pool', type: 'address', placeholder: 'Pool address…', required: true },
      { key: 'assetMint', label: 'NFT', type: 'address', placeholder: 'NFT mint address…', required: true },
    );
  // ── Bridge / Cross-Chain ──────────────────────────────────────────────────
  } else if (t === 'relay_bridge' || t === 'bridge' || t === 'cross_chain_swap') {
    // Chains are chosen by name, not by number. The old form asked for a
    // "Source Chain ID" and suggested 900 for Solana — a value Relay has never
    // used, so following the placeholder produced a bridge from nowhere.
    // Nothing below the panel. Chain and token are chosen on the side they
    // belong to, in the picker, the way every bridge does it — a second copy
    // underneath is one more place for the two to disagree.
    fields.push(
      // No `amount` row: the send panel above owns it, the way the swap card
      // owns its own. Two boxes for one number is two places to disagree.
      // Slippage is Auto unless someone says otherwise — Relay picks a
      // tolerance per route, and a number typed into an empty box is a guess
      // competing with a calculation. The panel offers Auto or Custom.

      // Where it lands. On an EVM destination this is an EVM address, which
      // the user's Solana wallet cannot supply — hence the connect button the
      // template puts beside it.
      // No `recipient` row: the receive panel asks for the wallet, and two
      // Connect buttons for one address is two places to disagree.
    );
  } else if (t === 'squid_bridge') {
    fields.push(
      { key: 'originChainId', label: 'Source Chain ID', type: 'number', placeholder: '7565164 (Solana)', required: true, half: true },
      { key: 'destinationChainId', label: 'Dest Chain ID', type: 'number', placeholder: '1 (Ethereum)', required: true, half: true },
      { key: 'originToken', label: 'From Token', type: 'token', required: true },
      { key: 'destinationToken', label: 'To Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippage', label: 'Slippage %', type: 'number', placeholder: '1.0', suffix: '%', half: true, min: 0, max: 100, step: '0.1' },
      { key: 'recipient', label: 'Recipient (optional)', type: 'address', placeholder: 'Dest wallet...', half: true },
    );
  } else if (t === 'squid_status') {
    fields.push(
      { key: 'transactionId', label: 'Transaction ID', type: 'text', placeholder: 'TX hash...', required: true },
    );
  } else if (
    t === 'pumpfun_token_info' || t === 'pumpfun_bonding_curve' ||
    t === 'pumpfun_comments' || t === 'pumpfun_user' ||
    t === 'pumpswap_pool_info'
  ) {
    // Pump.fun single-mint info lookups
    if (action.params['mint']) {
      fields.push({ key: 'mint', label: 'Token Mint', type: 'address', placeholder: 'Token mint address...', required: true });
    }
    if (action.params['username']) {
      fields.push({ key: 'username', label: 'Username', type: 'text', placeholder: 'pump.fun username...', required: true });
    }
  } else if (t === 'pumpfun_search') {
    fields.push({ key: 'query', label: 'Search Query', type: 'text', placeholder: 'Token name or keyword...', required: true });
  } else {
    // Generic fallback — show all scalar params as editable fields
    for (const [key, value] of Object.entries(action.params)) {
      if (key === 'protocol') continue;
      const primitive = typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
      if (primitive) {
        const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase());
        const placeholder = String(value);
        const isAddr = typeof value === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value);
        fields.push({ key, label, type: isAddr ? 'address' : 'text', placeholder });
      }
    }
  }
  return fields;
}

type ActionStatus = 'pending' | 'quoting' | 'signing' | 'submitted' | 'confirmed' | 'error';

/** A selectable collateral in the borrow card's picker. */
interface CollateralOption {
  mint: string;
  symbol: string;
  logo: string;
  balance: number;
  debtSymbol: string;
}

@Component({
  selector: 'app-action-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, JargonTooltipComponent, TokenPickerComponent],
  templateUrl: './action-card.component.html',
  styleUrls: ['./action-card.component.scss'],
})
export class ActionCardComponent implements OnInit, OnChanges, OnDestroy {
  @Input() action!: ParsedAction;
  @Input() snapshotId: string | null = null;
  @Input() allActions: ParsedAction[] = [];
  @Input() sessionId: string | null = null;
  @Input() messageId: string | null = null;
  @Input() cachedResult: StoredActionResult | null = null;
  @Input() cachedQsResult: any = null;
  @Output() actionComplete = new EventEmitter<StoredActionResult>();
  @Output() actionDismissed = new EventEmitter<void>();
  @Output() requestChat = new EventEmitter<string>();
  @Output() executionComplete = new EventEmitter<{ success: boolean; error?: string }>();
  @Input() autoApprove = false;

  private readonly actionService = inject(SolanaActionService);
  private readonly chatApi = inject(ChatApiService);
  private readonly uploadService = inject(UploadService);
  private readonly jupiterLend = inject(JupiterLendService);
  private readonly kamino = inject(KaminoService);
  private readonly walletService = inject(WalletService);
  private readonly magicEden = inject(MagicEdenService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly previewService = inject(TransactionPreviewService);
  private readonly swapService = inject(JupiterSwapService);
  private readonly rollbackService = inject(RollbackService);
  private readonly intentParser = inject(IntentParserService);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly priceFeed = inject(PriceFeedService);
  private readonly jitoService = inject(JitoService);
  private readonly marinadeService = inject(MarinadeService);
  private readonly jupSolService = inject(JupSolService);
  private readonly meteoraService = inject(MeteoraService);
  private readonly apiService = inject(ApiService);
  private readonly appVersion = inject(AppVersionService);
  private readonly orcaService = inject(OrcaService);

  /** Cache so a Meteora pool address is fetched once per card lifecycle even
   *  if `editParams.poolId` thrashes (e.g. on draft restore). */
  private _resolvedMeteoraPool: string | null = null;
  /** Same idea for Raydium CLMM pool enrichment — fetch tokenA/B + currentPrice
   *  exactly once per card when the LLM emitted poolId without symbols. */
  private _enrichedRaydiumPool: string | null = null;

  @ViewChild('imageFileInput') imageFileInput!: ElementRef<HTMLInputElement>;
  @ViewChild('bannerFileInput') bannerFileInput!: ElementRef<HTMLInputElement>;

  // Status
  readonly status = signal<ActionStatus>('pending');
  readonly shake = signal(false);
  readonly txSignature = signal<string | null>(null);
  readonly dataResult = signal<string | null>(null);
  readonly isDataOnly = computed(() => SolanaActionService.DATA_ONLY_TYPES.has(this.action.type));
  readonly errorMessage = signal('');
  // Submitted-state recovery: tracks how long we've been waiting on chain so
  // the UI can surface a re-check CTA after ~25s and auto-fail at ~90s. The
  // tick updates the signal every second; we cancel it when status leaves
  // 'submitted'.
  readonly submittedElapsedSec = signal(0);
  readonly recheckInProgress = signal(false);
  private submittedAt = 0;
  private elapsedTicker: ReturnType<typeof setInterval> | null = null;

  // Token launch fields
  readonly editName = signal('');
  readonly editSymbol = signal('');
  readonly editDescription = signal('');
  readonly editInitialBuy = signal('');
  /** Set when the initial buy was specified in USD ("$10"): drives the "≈ $N"
   *  hint next to the SOL-denominated field after live conversion. */
  readonly initialBuyUsd = signal<number | null>(null);
  /** SOL logo for the Initial buy (SOL) field icon. */
  get solLogoURI(): string | null { return this.resolveTokenDisplay(this.SOL_MINT).logoURI ?? null; }
  readonly editTwitter = signal('');
  readonly editTelegram = signal('');
  readonly editWebsite = signal('');
  readonly editCashback = signal(false);
  readonly editTokenizedAgent = signal(false);
  /** Mint (contract) of the token created by this launch card, once known. */
  readonly createdMint = signal<string | null>(null);

  // Image
  readonly uploadingImage = signal(false);
  readonly imageUploadError = signal<string | null>(null);
  private uploadedImageUrl = signal<string | null>(null);
  readonly effectiveImageUrl = computed(() => this.uploadedImageUrl() || this.action?.params['image'] || this.action?.params['imageUrl'] || null);
  /**
   * The image URL that may be SENT, as opposed to shown.
   *
   * When the server upload fails the card falls back to a `blob:` URL so the
   * user still sees their picture. That is fine on screen and catastrophic on
   * chain: one launch went out with `blob:https://app.oprai.xyz/…` as its
   * permanent metadata URI, so the token exists, works, and is invisible to
   * pump.fun forever. Preview and payload are different things and no longer
   * share a signal.
   */
  readonly publicImageUrl = computed(() => {
    const url = this.effectiveImageUrl();
    return url && /^https?:\/\//i.test(url) ? url : null;
  });
  readonly isDragOver = signal(false);
  // Raw resized file kept for pump.fun IPFS upload at launch time
  private resizedImageFile: File | null = null;

  // Banner (optional pump.fun coin-page banner, 3:1 / 1500×500, set at creation only)
  readonly uploadingBanner = signal(false);
  readonly bannerUploadError = signal<string | null>(null);
  readonly uploadedBannerUrl = signal<string | null>(null);
  readonly bannerUrl = signal<string | null>(null);
  readonly effectiveBannerUrl = computed(() => this.uploadedBannerUrl() || this.bannerUrl());

  // Advanced options
  readonly showAdvanced = signal(false);
  readonly editSlippage = signal('10');
  readonly editPriorityFee = signal('0.0005');

  // Edit params
  readonly editParams = signal<Record<string, string | undefined>>({});

  // cancel_dca: the active DCA order this card will cancel, fetched on init so
  // the card shows *what* is being cancelled (the order address is resolved
  // server-side, so the LLM/action carries no details).
  readonly cancelDcaTarget = signal<{
    input: string; output: string; perCycle: number; frequency: string;
    remaining: number; total: number;
  } | null>(null);
  readonly cancelDcaLoading = signal(false);

  // Auto-save edits to localStorage so they survive page refresh for pending actions.
  private readonly _draftEffect = effect(() => {
    const status = this.status();
    if (status !== 'pending' || !this.sessionId || !this.messageId) return;
    const draft = {
      editInitialBuy: this.editInitialBuy(),
      editName: this.editName(),
      editSymbol: this.editSymbol(),
      editDescription: this.editDescription(),
      editTwitter: this.editTwitter(),
      editTelegram: this.editTelegram(),
      editWebsite: this.editWebsite(),
      editCashback: this.editCashback(),
      editTokenizedAgent: this.editTokenizedAgent(),
      editSlippage: this.editSlippage(),
      editPriorityFee: this.editPriorityFee(),
      editParams: this.editParams(),
      uploadedImageUrl: this.uploadedImageUrl(),
      uploadedBannerUrl: this.uploadedBannerUrl(),
    };
    try { localStorage.setItem(`draft:${this.sessionId}:${this.messageId}`, JSON.stringify(draft)); } catch {}
  });

  // Re-fetch balances whenever the connected wallet changes (disconnect,
  // reconnect, or switch-and-back). Without this, an action card mounted
  // before the wallet finished connecting shows "0" forever — the balance
  // loaders fire once in initFromAction() and never observe the wallet
  // becoming available. Tracks publicKey so a fresh fetch + cache write
  // happens for each new wallet.
  private _lastBalanceWallet: string | null = null;
  private readonly _walletBalanceEffect = effect(() => {
    const wallet = this.walletService.publicKey();
    if (wallet === this._lastBalanceWallet) return;
    this._lastBalanceWallet = wallet;
    // Drop stale balance display tied to the previous wallet.
    this.inputBalance.set(null);
    this.secondaryBalance.set(null);
    if (!wallet) return;
    untracked(() => {
      if (this.inputBalanceMint()) void this.loadInputBalance();
      if (this.secondaryBalanceMint()) void this.loadSecondaryBalance();
    });
  });

  /**
   * Reload the balance lines whenever the token they describe changes.
   *
   * The balance was only fetched on wallet change and at a handful of
   * hand-placed call sites, so picking a different token left the previous
   * token's number on screen under the new symbol — a swap card showed
   * "Balance: 0.0428" for USDC when 0.0428 was the wallet's SOL, and the
   * insufficient-balance warning repeated the same wrong figure.
   *
   * An effect rather than another call in `onTokenPicked`, because every new
   * way of changing a token would have to remember that call, and this one
   * already had four places that did and one that didn't.
   */
  private _lastInputMint = '';
  private _lastSecondaryMint = '';
  private readonly _balanceMintEffect = effect(() => {
    const input = this.inputBalanceMint();
    const secondary = this.secondaryBalanceMint();
    untracked(() => {
      if (!this.walletService.publicKey()) return;
      if (input !== this._lastInputMint) {
        this._lastInputMint = input;
        this.inputBalance.set(null);
        if (input) void this.loadInputBalance();
      }
      if (secondary !== this._lastSecondaryMint) {
        this._lastSecondaryMint = secondary;
        this.secondaryBalance.set(null);
        if (secondary) void this.loadSecondaryBalance();
      }
    });
  });

  // Kamino borrow: (re)load the reserve rates + obligation whenever the wallet
  // or borrow token changes. On a page reload the card can init BEFORE the
  // wallet reconnects — the initial fetch then runs with no wallet and the
  // obligation comes back empty (card wrongly shows "no collateral"). Tracking
  // publicKey() here re-fetches the moment the wallet becomes available, so an
  // existing on-chain position (even a tiny one) always shows, like Kamino's UI.
  private _lastKaminoBorrowKey: string | null = null;
  private readonly _kaminoBorrowEffect = effect(() => {
    const wallet = this.walletService.publicKey();
    const token = this.editParams()['token'] ?? this.editParams()['reserve'] ?? '';
    if (this.action?.type !== 'kamino_borrow' && this.action?.type !== 'kamino_repay'
        && this.action?.type !== 'kamino_withdraw' && this.action?.type !== 'kamino_withdraw_collateral') return;
    const key = `${wallet}:${token}`;
    if (key === this._lastKaminoBorrowKey) return;
    this._lastKaminoBorrowKey = key;
    untracked(() => void this.loadKaminoBorrowInfo());
  });

  // K-Vault withdraw: the position lookup needs the wallet, which often isn't
  // connected yet on first render. Reload once it (or the target vault) lands.
  private _lastKaminoVaultWithdrawKey: string | null = null;
  private readonly _kaminoVaultWithdrawEffect = effect(() => {
    if (this.action?.type !== 'kamino_vault_withdraw' && this.action?.type !== 'kamino_unstake') return;
    const wallet = this.walletService.publicKey();
    const vault = this.editParams()['vault'] ?? '';
    if (!wallet) return;
    const key = `${wallet}:${vault}`;
    if (key === this._lastKaminoVaultWithdrawKey) return;
    this._lastKaminoVaultWithdrawKey = key;
    untracked(() => void this.loadKaminoVaultWithdrawInfo());
  });

  // Input-token USD price, used to flag Jupiter's minimum order sizes UP FRONT
  // (limit order ≥ $5 total; DCA ≥ $50 per suborder) instead of surfacing the
  // backend rejection after submit. Refetches when the input token changes.
  readonly inputUsdPrice = signal<number | null>(null);
  private readonly _inputPriceEffect = effect(() => {
    const mint = this.inputBalanceMint();
    const t = this.action?.type;
    if ((t !== 'limit_order' && t !== 'dca' && t !== 'perp_open') || !mint) return;
    untracked(() => {
      this.inputUsdPrice.set(null);
      void this.priceFeed.getPrice(mint).then(p => this.inputUsdPrice.set(p));
    });
  });

  // Live perp quote (entry, liquidation, fees) fetched from the perps API via
  // /actions/build. Debounced so it re-quotes as the user edits market / side /
  // collateral / leverage.
  readonly perpQuote = signal<{
    entryPrice: number; liqPrice: number; sizeUsd: number;
    openFeeUsd: number; priceImpactFeeUsd: number; borrowFeeUsd: number; totalFeeUsd: number;
  } | null>(null);
  readonly perpQuoteLoading = signal(false);
  private _perpQuoteSeq = 0;
  private _perpQuoteTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly _perpQuoteEffect = effect(() => {
    if (this.action?.type !== 'perp_open') { this.perpQuote.set(null); return; }
    // Track the inputs that change the quote.
    const market = this.perpMarket();
    const side = this.perpSide();
    const coll = this.getEditParam('collateralAmount');
    const lev = this.getEditParam('leverage');
    const collToken = this.getEditParam('collateralToken');
    const belowMin = this.perpBelowMinCollateral();
    const collNum = parseFloat(coll);
    if (!Number.isFinite(collNum) || collNum <= 0 || belowMin) { this.perpQuote.set(null); return; }

    const seq = ++this._perpQuoteSeq;
    if (this._perpQuoteTimer) clearTimeout(this._perpQuoteTimer);
    this._perpQuoteTimer = setTimeout(() => {
      untracked(() => this.fetchPerpQuote(seq, { market, side, collateralAmount: coll, leverage: lev, collateralToken: collToken }));
    }, 600);
  });

  /** Fetch the perps-api quote for the current open params (discarding the tx). */
  private async fetchPerpQuote(
    seq: number,
    params: { market: string; side: string; collateralAmount: string; leverage: string; collateralToken: string },
  ): Promise<void> {
    this.perpQuoteLoading.set(true);
    try {
      const body: Record<string, unknown> = {
        market: params.market,
        side: params.side,
        collateralAmount: params.collateralAmount,
        leverage: params.leverage || '2',
        slippageBps: 200,
      };
      if (params.collateralToken) body['collateralToken'] = params.collateralToken;
      const res = await firstValueFrom(
        this.apiService.post<{ quote?: any }>('/actions/build', { type: 'perp_open', params: body }),
      );
      if (seq !== this._perpQuoteSeq) return; // superseded by a newer edit
      // Rust returns the full perps-api response under `quote`; the numeric
      // quote is nested one level in (`quote.quote`). All USD fields are micro-USD.
      const q = res?.quote?.quote ?? res?.quote ?? null;
      if (!q) { this.perpQuote.set(null); return; }
      const usd = (v: unknown) => parseFloat(String(v ?? '0')) / 1e6;
      const openFee = usd(q.openFeeUsd);
      const priceImpact = usd(q.priceImpactFeeUsd);
      const borrow = usd(q.outstandingBorrowFeeUsd);
      this.perpQuote.set({
        entryPrice: usd(q.averagePriceUsd),
        liqPrice: usd(q.liquidationPriceUsd),
        sizeUsd: usd(q.sizeUsdDelta),
        openFeeUsd: openFee,
        priceImpactFeeUsd: priceImpact,
        borrowFeeUsd: borrow,
        totalFeeUsd: openFee + priceImpact + borrow,
      });
    } catch {
      if (seq === this._perpQuoteSeq) this.perpQuote.set(null);
    } finally {
      if (seq === this._perpQuoteSeq) this.perpQuoteLoading.set(false);
    }
  }

  // JLP add/remove live estimate — how much JLP (add) or token (remove) the
  // user receives. Backend routes JLP through the Jupiter swap, so /actions/build
  // returns a swap quote (camelCase outAmount, in output-token base units).
  readonly jlpQuote = signal<{ out: number; symbol: string } | null>(null);
  readonly jlpQuoteLoading = signal(false);
  private _jlpQuoteSeq = 0;
  private _jlpQuoteTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly _jlpQuoteEffect = effect(() => {
    const t = this.action?.type;
    if (t !== 'jlp_add' && t !== 'jlp_remove') { this.jlpQuote.set(null); return; }
    const amount = this.getEditParam('amount');
    const token = this.getEditParam('token');
    const amtNum = parseFloat(amount);
    if (!Number.isFinite(amtNum) || amtNum <= 0) { this.jlpQuote.set(null); return; }
    const seq = ++this._jlpQuoteSeq;
    if (this._jlpQuoteTimer) clearTimeout(this._jlpQuoteTimer);
    this._jlpQuoteTimer = setTimeout(() => {
      untracked(() => this.fetchJlpQuote(seq, t, { operation: t === 'jlp_add' ? 'add' : 'remove', amount, token }));
    }, 600);
  });

  private async fetchJlpQuote(
    seq: number,
    type: 'jlp_add' | 'jlp_remove',
    params: { operation: string; amount: string; token: string },
  ): Promise<void> {
    this.jlpQuoteLoading.set(true);
    try {
      const res = await firstValueFrom(
        this.apiService.post<{ quote?: any }>('/actions/build', { type, params }),
      );
      if (seq !== this._jlpQuoteSeq) return;
      const q = res?.quote ?? null;
      const outRaw = q ? parseFloat(String(q.outAmount ?? '0')) : NaN;
      if (!Number.isFinite(outRaw) || outRaw <= 0) { this.jlpQuote.set(null); return; }
      // Output token: JLP (6 dp) when adding; the receive token when removing.
      const JLP_MINT = '27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4';
      if (type === 'jlp_add') {
        this.jlpQuote.set({ out: outRaw / 1e6, symbol: 'JLP' });
      } else {
        const outMint = String(q.outputMint ?? this.inputBalanceMint());
        const tok = this.tokenRegistry.getToken(outMint);
        const dec = tok?.decimals ?? 6;
        this.jlpQuote.set({ out: outRaw / Math.pow(10, dec), symbol: tok?.symbol ?? (params.token || '') });
      }
    } catch {
      if (seq === this._jlpQuoteSeq) this.jlpQuote.set(null);
    } finally {
      if (seq === this._jlpQuoteSeq) this.jlpQuoteLoading.set(false);
    }
  }

  // Validator picker (native_stake only)
  readonly validators = signal<ValidatorInfo[]>([]);
  readonly validatorsLoading = signal(false);
  readonly validatorCustom = signal(false); // toggle custom address input
  readonly isNativeStake = computed(() => this.action?.type === 'native_stake');

  /** Two hundred validators is a list; two hundred validators you can search
   *  is a choice. Matches on name or vote account. */
  readonly validatorQuery = signal('');

  /**
   * How the list is ordered, and it is the user's choice.
   *
   * The endpoint returns them by stake, so asking for "the highest APY" got a
   * list headed by the biggest validator — the request was simply not
   * represented anywhere in the card. Sorting locally because all two hundred
   * are already here: re-ordering them is instant and needs no round trip.
   *
   * APY is the default. It is what someone opening a staking card is choosing
   * between; size is a tiebreak, not a goal.
   */
  readonly validatorSort = signal<'apy' | 'stake' | 'fee'>('apy');
  readonly validatorSortDir = signal<'asc' | 'desc'>('desc');
  readonly validatorPage = signal(0);
  private static readonly VALIDATORS_PER_PAGE = 6;

  /** Clicking the column you are already sorted by reverses it, which is what
   *  a sortable column header is expected to do. */
  setValidatorSort(by: 'apy' | 'stake' | 'fee'): void {
    if (this.validatorSort() === by) {
      this.validatorSortDir.set(this.validatorSortDir() === 'desc' ? 'asc' : 'desc');
    } else {
      this.validatorSort.set(by);
      // Best-first for a rate or a size; cheapest-first for a fee.
      this.validatorSortDir.set(by === 'fee' ? 'asc' : 'desc');
    }
    this.validatorPage.set(0);
  }

  /** The page numbers worth drawing: a window around the current page, with
   *  the first and last always reachable. */
  readonly validatorPageNumbers = computed(() => {
    const total = this.validatorPageCount();
    const cur = this.validatorPage() + 1;
    const out: number[] = [];
    const push = (n: number) => { if (n >= 1 && n <= total && !out.includes(n)) out.push(n); };
    push(1);
    for (let n = cur - 1; n <= cur + 1; n++) push(n);
    push(total);
    return out.sort((a, b) => a - b);
  });

  goToValidatorPage(n: number): void {
    this.validatorPage.set(Math.min(Math.max(n - 1, 0), this.validatorPageCount() - 1));
  }

  /** "1–6 of 629" — the count is the point: it says the list is the whole set,
   *  not a top-20 someone chose for you. */
  validatorRange(): { from: number; to: number; total: number } {
    const per = ActionCardComponent.VALIDATORS_PER_PAGE;
    const total = this.sortedValidators().length;
    const from = total ? this.validatorPage() * per + 1 : 0;
    return { from, to: Math.min(from + per - 1, total), total };
  }

  setValidatorQuery(q: string): void {
    this.validatorQuery.set(q);
    this.validatorPage.set(0);
  }

  /** Search + sort, before paging. */
  readonly sortedValidators = computed(() => {
    const q = this.validatorQuery().trim().toLowerCase();
    const rows = this.validators().filter(v =>
      !q || (v.name ?? '').toLowerCase().includes(q) || v.voteAccount.toLowerCase().includes(q));
    const by = this.validatorSort();
    const flip = this.validatorSortDir() === 'asc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      if (by === 'fee') return flip * (b.commission - a.commission) || (b.activatedStakeSol - a.activatedStakeSol);
      if (by === 'stake') return flip * (b.activatedStakeSol - a.activatedStakeSol);
      // Dozens share the top APY, so stake breaks the tie — at the same yield
      // the difference that matters is how established the operator is. An
      // unmeasured APY sorts last: a missing number is not a good one.
      const aa = a.apyEstimatePct ?? -1;
      const bb = b.apyEstimatePct ?? -1;
      return flip * (bb - aa) || (b.activatedStakeSol - a.activatedStakeSol);
    });
  });

  readonly validatorPageCount = computed(() =>
    Math.max(1, Math.ceil(this.sortedValidators().length / ActionCardComponent.VALIDATORS_PER_PAGE)));

  /** One page. A clipped scroll box cut rows in half at both ends; a page
   *  shows five whole ones and says which five they are. */
  readonly pagedValidators = computed(() => {
    const per = ActionCardComponent.VALIDATORS_PER_PAGE;
    const start = Math.min(this.validatorPage(), this.validatorPageCount() - 1) * per;
    return this.sortedValidators().slice(start, start + per);
  });

  /**
   * A mark for a validator that has no logo.
   *
   * A fifth of them publish none, and a grey circle with a letter made those
   * rows look broken next to the ones that do — the same grey, over and over,
   * reading as a failed image rather than a deliberate absence. Derived from
   * the vote account, so it is stable per validator and distinct between them.
   */
  validatorHue(voteAccount: string): number {
    let h = 0;
    for (let i = 0; i < voteAccount.length; i++) h = (h * 31 + voteAccount.charCodeAt(i)) % 360;
    return h;
  }

  stepValidatorPage(delta: number): void {
    const next = this.validatorPage() + delta;
    if (next < 0 || next >= this.validatorPageCount()) return;
    this.validatorPage.set(next);
  }

  /** The one currently chosen, so the card can name it instead of echoing a
   *  base58 string back at the person who just picked it from a list. */
  readonly selectedValidator = computed(() => {
    const v = this.getEditParam('validatorVoteAccount');
    return v ? this.validators().find(x => x.voteAccount === v) ?? null : null;
  });

  // ── The user's own stake accounts ────────────────────────────────────────
  //
  // Deactivate, withdraw, split and merge all operate on an existing stake
  // account, and all four used to ask for its address as free text.

  readonly stakeAccounts = signal<StakeAccountInfo[]>([]);
  readonly stakeAccountsLoading = signal(false);
  readonly isStakeAccountAction = computed(() =>
    /^native_stake_(deactivate|withdraw|split|merge)$/.test(this.action?.type ?? ''));
  readonly isStakeMerge = computed(() => this.action?.type === 'native_stake_merge');

  /** Which parameter the list writes into, and what to call it on screen. */
  stakeAccountField(): 'stakeAccount' | 'destinationStakeAccount' {
    return this.isStakeMerge() ? 'destinationStakeAccount' : 'stakeAccount';
  }

  async loadStakeAccounts(): Promise<void> {
    const owner = this.walletService.publicKey();
    if (!owner || !this.isStakeAccountAction()) return;
    this.stakeAccountsLoading.set(true);
    try {
      this.stakeAccounts.set(await this.actionService.getStakeAccounts(owner));
    } finally {
      this.stakeAccountsLoading.set(false);
    }
  }

  selectStakeAccount(address: string, field: string): void {
    this.setEditParam(field, address);
  }

  /** Merging needs two different accounts, and merging one into itself is the
   *  mistake a plain pair of text inputs invites. */
  stakeMergeConflict(): boolean {
    const d = this.getEditParam('destinationStakeAccount').trim();
    const s = this.getEditParam('sourceStakeAccount').trim();
    return !!d && d === s;
  }

  stakeAccountLabel(a: StakeAccountInfo): string {
    const v = a.voteAccount
      ? this.validators().find(x => x.voteAccount === a.voteAccount)?.name
      : null;
    return v ?? (a.voteAccount ? `${a.voteAccount.slice(0, 4)}…${a.voteAccount.slice(-4)}` : 'Not delegated');
  }

  // Quick swap
  readonly showQuickSwap = signal(false);
  readonly qsStatus = signal<'idle' | 'swapping' | 'done' | 'error'>('idle');
  readonly qsError = signal<string | null>(null);
  readonly qsTxSig = signal<string | null>(null);
  readonly qsFromMint = signal('');
  readonly qsBalancesLoading = signal(false);
  readonly qsTokenList = signal<Array<{ mint: string; symbol: string; balance: number; logoURI?: string }>>([]);

  // Preview
  readonly showPreview = signal(false);
  readonly loadingPreview = signal(false);
  readonly preview = signal<TransactionPreview | null>(null);

  // Balance
  // ─ Primary: serves the `amount` and `amountA` fields. Falls through
  //   inputMint → inputToken → token → tokenA so we don't have to enumerate
  //   every action type. Two-token forms (Meteora add-liquidity etc.) use
  //   tokenA via this same signal.
  readonly inputBalance = signal<number | null>(null);
  readonly inputBalanceLoading = signal(false);
  readonly inputBalanceMint = computed(() => {
    const p = this.editParams();
    // Staking spends SOL, so the amount row gets the same balance line and Max
    // as every other card that spends SOL. It had neither, so the only way to
    // learn the wallet could not cover the stake was to sign and find out.
    if (/^native_stake(_split)?$/.test(this.action?.type ?? '')) return this.SOL_MINT;

    // Burn spends the token it names, and had no balance line at all — so the
    // card could not say how much was about to be destroyed, only that it
    // would be.
    if (this.action?.type === 'burn') return (p['mint'] ?? '').trim();

    // Bridging out of Solana spends a Solana token like any other card, so it
    // gets the same balance line. Relay's native SOL is its own address; the
    // wallet reads it under the wrapped mint.
    if (this.isRelayBridge()) {
      if (this.relayOriginIsEvm()) return '';
      const cur = (p['originCurrency'] ?? '').trim();
      return !cur || cur === ActionCardComponent.NATIVE_SOL ? this.SOL_MINT : cur;
    }
    // For dual-token forms (CLMM / DLMM / DAMM open-position, add-liquidity), the
    // LLM often emits BOTH inputMint=<one side> AND tokenA/tokenB=<pair>. Picking
    // `inputMint` first then makes the primary row resolve to the same mint as
    // `secondaryBalanceMint` (which reads tokenB), so both balance lines show the
    // same number. When a tokenA/tokenB pair is present, prefer tokenA and ignore
    // `inputMint` to keep the two rows distinct.
    // pump.fun / PumpSwap: the "spent" token is SOL on a buy and the token
    // itself on a sell (unless the amount is explicitly SOL-denominated). This
    // drives both the balance line and the amount-input icon (base coin).
    const pumpCfg = this.pumpActionConfig();
    if (pumpCfg) {
      return this.pumpAmountInSol(pumpCfg.side, p)
        ? this.SOL_MINT
        : (p['mint'] ?? p['token'] ?? '');
    }
    // LST stake/unstake: the "You pay" token is SOL when staking, the LST
    // itself when unstaking. Drives both the balance line and the amount-input
    // token pill (so the pay side shows a real icon, matching the receive side).
    const lstCfg = this.lstActionConfig();
    if (lstCfg) {
      return lstCfg.direction === 'stake'
        ? this.SOL_MINT
        : (this.tokenRegistry.getBySymbol(lstCfg.lstSymbol)?.address ?? '');
    }
    // Perp: collateral token drives the balance line + price. Long uses the
    // market's base token (SOL for SOL-PERP), short uses USDC — matching the
    // backend's collateral default.
    if (this.action?.type === 'perp_open') {
      return this.perpCollateralMint();
    }
    // JLP remove: the token being SPENT (and whose balance drives Max / the
    // "all" sentinel) is JLP — NOT `token`, which here is the RECEIVE token.
    // Without this, "sell all JLP" resolved to the receive-token balance.
    if (this.action?.type === 'jlp_remove') {
      return '27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4';
    }
    // Borrow: the `amount` is the DEBT you receive, not something you spend, so
    // no wallet-balance line belongs on it (the collateral is a separate field).
    if (this.action?.type === 'borrow' || this.action?.type === 'kamino_borrow') {
      return '';
    }
    const tokenA = p['tokenA'] ?? p['tokenXMint'];
    const tokenB = p['tokenB'] ?? p['tokenYMint'];
    if (tokenA && tokenB) return tokenA;
    return p['inputMint']
      ?? p['inputToken']
      ?? p['token']
      ?? tokenA
      ?? '';
  });
  readonly inputBalanceSymbol = computed(() => this.resolveTokenDisplay(this.inputBalanceMint()).symbol || '');
  // ─ Secondary: serves the `amountB` field on dual-token actions
  //   (meteora_add_liquidity, *_dammv2_add_liquidity, raydium_clmm,
  //   orca_open_position…). Empty/null when the action only has one
  //   amount field, in which case the template skips the second hint.
  readonly secondaryBalance = signal<number | null>(null);
  readonly secondaryBalanceLoading = signal(false);
  readonly secondaryBalanceMint = computed(() =>
    this.editParams()['tokenB']
    ?? this.editParams()['tokenYMint']
    ?? '');
  readonly secondaryBalanceSymbol = computed(() => this.resolveTokenDisplay(this.secondaryBalanceMint()).symbol || '');

  // ── DLMM ratio engine ────────────────────────────────────────────
  // Active when the action came from a DLMM "Use this pool" CTA — the
  // QueryCard embeds binStep, currentPrice, activeBinId and decimals so
  // we can compute the bin distribution + ratio entirely client-side.
  // Returns null for non-DLMM actions so the form stays bare for DAMM.
  readonly dlmmRatio = computed(() => {
    const p = this.editParams();
    const binStep = parseFloat(p['binStep'] ?? '0');
    const activeBinId = parseFloat(p['activeBinId'] ?? '0');
    if (!(binStep > 0)) return null;
    const strategy = (p['strategy'] ?? 'spot') as DlmmStrategy;
    // Prefer the explicit bin ids — they are what the deposit submits and they
    // can be ASYMMETRIC (Meteora's own default is). Deriving a symmetric range
    // from binSpread instead meant moving one bound alone changed nothing: the
    // ratio stayed put, so the amounts never responded to the range. Fall back
    // to the spread only when no explicit range exists yet.
    const explicitMin = parseInt(p['minBinId'] ?? '', 10);
    const explicitMax = parseInt(p['maxBinId'] ?? '', 10);
    const range = Number.isFinite(explicitMin) && Number.isFinite(explicitMax)
      ? { minBinId: Math.min(explicitMin, explicitMax), maxBinId: Math.max(explicitMin, explicitMax) }
      : rangeFromSpread(activeBinId, Math.max(1, Math.floor(parseFloat(p['binSpread'] ?? '15'))));
    return computeDlmmRatio({
      activeBinId,
      minBinId: range.minBinId,
      maxBinId: range.maxBinId,
      binStep,
      strategy,
    });
  });
  /**
   * Raw -> human scale for a DLMM price (Y per X).
   *
   *   human = (y_raw / 10^decY) / (x_raw / 10^decX) = raw * 10^(decX - decY)
   *
   * This was inverted, which is a factor of 10^6 on SOL/USDC (9 vs 6
   * decimals): the form offered 4,469 SOL against 0.33 USDC. It feeds both the
   * displayed active price and the amount ratio, so one sign error moved every
   * number on the card.
   */
  private readonly dlmmDecimalScale = computed(() => {
    const p = this.editParams();
    const decX = parseInt(p['tokenADecimals'] ?? '9', 10);
    const decY = parseInt(p['tokenBDecimals'] ?? '9', 10);
    return Math.pow(10, decX - decY);
  });
  /** True when the chosen range only collects token Y (range below active). */
  readonly dlmmSingleSidedY = computed(() => {
    const r = this.dlmmRatio();
    return !!(r && r.needsY && !r.needsX);
  });
  /** True when the chosen range only collects token X (range above active). */
  readonly dlmmSingleSidedX = computed(() => {
    const r = this.dlmmRatio();
    return !!(r && r.needsX && !r.needsY);
  });
  /** Active price displayed under the form: "1 jupSOL ≈ 1.10 SOL". */
  readonly dlmmActivePriceDisplay = computed(() => {
    const r = this.dlmmRatio();
    if (!r || !(r.activePrice > 0)) return null;
    const symA = this.inputBalanceSymbol() || 'A';
    const symB = this.secondaryBalanceSymbol() || 'B';
    // The bin formula yields price in raw units; scale to human units.
    const humanPrice = r.activePrice * this.dlmmDecimalScale();
    return { symA, symB, price: humanPrice };
  });

  // ── DLMM auto-balance effect ─────────────────────────────────────
  // Keeps amountA / amountB in sync given the chosen range + strategy.
  // We track which side the user last typed in (`dlmmLastEdited`) so that
  // a fresh keystroke on B fills A, and vice versa, without an infinite
  // ping-pong loop. The effect ignores changes it itself wrote (compares
  // computed value to current input within an epsilon).
  private readonly dlmmLastEdited = signal<'A' | 'B' | null>(null);
  /** Suppression flag — true while the effect is writing back to params. */
  private dlmmInternalWrite = false;

  /**
   * AMM (DAMM v1/v2) ratio: when the action carries `reserveA` and
   * `reserveB` in raw units (or a precomputed `amountRatio` = B/A in
   * human units), we compute the deposit ratio so the user only has to
   * enter one side. Returns null when no ratio data is available — the
   * effect below then leaves both amount fields independent.
   */
  readonly ammRatio = computed<{ yPerX: number; xPerY: number } | null>(() => {
    const p = this.editParams();
    // Direct human-units ratio takes precedence.
    const direct = parseFloat(p['amountRatio'] ?? '');
    if (Number.isFinite(direct) && direct > 0) {
      return { yPerX: direct, xPerY: 1 / direct };
    }
    // Otherwise reserves: r = reserveB / reserveA (in raw units).
    const ra = parseFloat(p['reserveA'] ?? '');
    const rb = parseFloat(p['reserveB'] ?? '');
    if (Number.isFinite(ra) && ra > 0 && Number.isFinite(rb) && rb > 0) {
      // Convert raw → human: r_human = (rb / 10^decB) / (ra / 10^decA)
      //                            = rb/ra × 10^(decA-decB)
      const decA = parseInt(p['tokenADecimals'] ?? '9', 10);
      const decB = parseInt(p['tokenBDecimals'] ?? '9', 10);
      const ratio = (rb / ra) * Math.pow(10, decA - decB);
      return { yPerX: ratio, xPerY: 1 / ratio };
    }
    return null;
  });

  /**
   * Raydium CLMM (concentrated liquidity) ratio. Active when the action
   * carries `currentPrice`, `minPrice`, `maxPrice` — same Uniswap v3 math
   * Raydium uses on its own UI:
   *
   *   sa = sqrt(minPrice), sb = sqrt(maxPrice), s = sqrt(currentPrice)
   *   in-range:   amountB / amountA = (s - sa) × s × sb / (sb - s)
   *   above max:  position is 100% Token A   → singleSided: 'A'
   *   below min:  position is 100% Token B   → singleSided: 'B'
   *
   * Returns null when any input is missing/invalid so the form falls back
   * to independent amount fields.
   */
  readonly clmmRatio = computed<
    { yPerX: number; xPerY: number; singleSided?: 'A' | 'B' } | null
  >(() => {
    const p = this.editParams();
    const cur = parseFloat(p['currentPrice'] ?? '');
    const lo  = parseFloat(p['minPrice'] ?? '');
    const hi  = parseFloat(p['maxPrice'] ?? '');
    if (!(cur > 0) || !(lo > 0) || !(hi > 0) || lo >= hi) return null;

    if (cur >= hi) {
      // Price above the upper bound → position holds only Token A
      // (waiting for price to come back down into range).
      return { yPerX: 0, xPerY: Infinity, singleSided: 'A' };
    }
    if (cur <= lo) {
      // Price below the lower bound → position holds only Token B
      // (waiting for price to climb back into range).
      return { yPerX: Infinity, xPerY: 0, singleSided: 'B' };
    }

    const s  = Math.sqrt(cur);
    const sa = Math.sqrt(lo);
    const sb = Math.sqrt(hi);
    const yPerX = (s - sa) * s * sb / (sb - s);
    return yPerX > 0
      ? { yPerX, xPerY: 1 / yPerX }
      : null;
  });

  /** True when the current action is Raydium CLMM open_position — the
   *  template flips the field-list-based form into the dedicated CLMM layout
   *  (current price + range presets + dual amounts + advanced expander). */
  // ── Meteora panels ────────────────────────────────────────────────────────
  // Meteora spans 25 write actions across DLMM, DAMM v1/v2, Dynamic Vaults and
  // Stake2Earn. Rather than 25 bespoke layouts, they collapse into five
  // archetypes that reuse the same panel language as the swap / CLMM cards.
  // These archetypes are shared, not Meteora-specific: a two-amount deposit, a
  // percentage withdrawal and a confirm-only action look the same whoever the
  // protocol is. Orca joins them rather than keeping the raw field-list form
  // with its "Liquidity (raw units)" box.
  private static readonly METEORA_DUAL = new Set([
    'meteora_add_liquidity', 'meteora_dammv2_add_liquidity',
    'meteora_open_position', 'meteora_add_to_position', 'meteora_dammv1_deposit',
    'orca_open_position', 'orca_increase_position',
    // Orca is concentrated-liquidity only, so "add liquidity" IS opening or
    // growing a position — the backend marks these two deprecated in favour
    // of the position actions. They keep the designed panel rather than
    // dropping to a raw form on the rare path that still emits them.
    'orca_add_liquidity',
  ]);
  /** DAMM v2 is constant-product: no bins, no range, so it takes the plain
   *  two-amount panel rather than the DLMM range controls. */
  readonly isDammV2 = computed(() => (this.action?.type ?? '').includes('dammv2'));
  private static readonly METEORA_REDUCE = new Set([
    'meteora_remove_liquidity', 'meteora_dammv2_remove_liquidity', 'meteora_dammv1_withdraw',
    'orca_decrease_position', 'orca_remove_liquidity',
  ]);
  private static readonly METEORA_SINGLE = new Set([
    'meteora_stake', 'meteora_unstake', 'meteora_vault_deposit', 'meteora_vault_withdraw',
    'meteora_s2e_stake', 'meteora_s2e_unstake',
  ]);
  private static readonly METEORA_CONFIRM = new Set([
    'orca_close_position', 'orca_collect_fees', 'orca_collect_rewards',
    'meteora_dammv2_claim_fee', 'meteora_dammv2_close_position',
    'meteora_close_position', 'meteora_claim_fees', 'meteora_claim_rewards',
    'meteora_harvest', 'meteora_s2e_claim_fee', 'meteora_s2e_cancel_unstake',
    'meteora_s2e_withdraw',
  ]);

  readonly isMeteoraDual = computed(() => ActionCardComponent.METEORA_DUAL.has(this.action.type));
  readonly isMeteoraReduce = computed(() => ActionCardComponent.METEORA_REDUCE.has(this.action.type));
  readonly isMeteoraSingle = computed(() => ActionCardComponent.METEORA_SINGLE.has(this.action.type));
  readonly isMeteoraConfirm = computed(() => ActionCardComponent.METEORA_CONFIRM.has(this.action.type));

  /** True for the DLMM variants, which add a bin range + distribution shape
   *  on top of the plain two-token deposit. */
  readonly isMeteoraDlmm = computed(() => {
    const t = this.action.type;
    if (t === 'meteora_open_position' || t === 'meteora_add_to_position') return true;
    return t === 'meteora_add_liquidity' && !!this.editParams()['binStep'];
  });

  readonly METEORA_STRATEGIES: ReadonlyArray<{ value: string; label: string; hint: string }> = [
    { value: 'spot',   label: 'Spot',    hint: 'Equal weight per bin' },
    { value: 'curve',  label: 'Curve',   hint: 'Concentrated at the current price' },
    { value: 'bidask', label: 'Bid-Ask', hint: 'Weighted toward the range edges' },
  ];

  meteoraStrategy(): string {
    return (this.editParams()['strategy'] || 'spot').toLowerCase();
  }
  setMeteoraStrategy(v: string): void { this.setEditParam('strategy', v); }

  // ── DLMM range: PRICE is the input, bins are the result ───────────────────
  // Meteora's own UI works this way — you move the price bounds and the bin
  // count follows. A bin is a fixed geometric step, so relative to the active
  // bin: price(bin) = currentPrice x (1 + binStep/10000)^(bin - activeBin).
  // Working relative to the active bin keeps token decimals out of the math.
  //
  // `minBinId`/`maxBinId` are the single source of truth (the submit path
  // already prefers them), which also allows the asymmetric ranges Meteora
  // permits — its own default sits at roughly -1.23% / +1.17%.

  /**
   * Bin ceiling for ONE add-liquidity transaction.
   *
   * Our builder uses add_liquidity_by_weight, which serialises the shape as a
   * per-bin vector — 6 bytes each (bin_id i32 + weight u16). Measured against
   * a real pool: 69 bins produced a 1235-byte transaction, 3 over Solana's
   * 1232 limit, which puts the base transaction near 821 bytes and the hard
   * ceiling at 68 bins. 60 keeps roughly 50 bytes of headroom for pools that
   * need an extra bin-array or ATA account.
   *
   * Meteora's own UI reaches 69 because it sends add_liquidity_by_strategy,
   * where the shape is a parameter rather than a list. Switching to that
   * instruction is what would buy back the range.
   */
  readonly METEORA_MAX_BINS = 60;

  private meteoraStep(): number | null {
    const bs = parseFloat(this.editParams()['binStep'] ?? '');
    return bs > 0 ? bs / 10_000 : null;
  }
  private meteoraActiveBin(): number | null {
    const v = parseInt(this.editParams()['activeBinId'] ?? '', 10);
    return Number.isFinite(v) ? v : null;
  }
  private meteoraCurrentPrice(): number | null {
    const v = parseFloat(this.editParams()['currentPrice'] ?? '');
    return v > 0 ? v : null;
  }

  /** Price at a bin id, relative to the active bin. */
  private meteoraPriceAtBin(bin: number): number | null {
    const step = this.meteoraStep();
    const active = this.meteoraActiveBin();
    const price = this.meteoraCurrentPrice();
    if (step === null || active === null || price === null) return null;
    return price * Math.pow(1 + step, bin - active);
  }

  /** Nearest bin id for a price, relative to the active bin. */
  private meteoraBinAtPrice(target: number): number | null {
    const step = this.meteoraStep();
    const active = this.meteoraActiveBin();
    const price = this.meteoraCurrentPrice();
    if (step === null || active === null || price === null || !(target > 0)) return null;
    return active + Math.round(Math.log(target / price) / Math.log(1 + step));
  }

  /** Bin spread (± bins around the active bin) with the backend's default. */
  meteoraSpread(): string { return this.editParams()['binSpread'] ?? '15'; }

  /** The resolved range: explicit bin ids when set, else the ± spread. */
  readonly meteoraRange = computed<{
    minBin: number; maxBin: number; bins: number;
    minPrice: number | null; maxPrice: number | null;
    minPct: number | null; maxPct: number | null;
  } | null>(() => {
    const p = this.editParams();
    const active = this.meteoraActiveBin();
    if (active === null) return null;
    let minBin = parseInt(p['minBinId'] ?? '', 10);
    let maxBin = parseInt(p['maxBinId'] ?? '', 10);
    if (!Number.isFinite(minBin) || !Number.isFinite(maxBin)) {
      const spread = parseInt(this.meteoraSpread(), 10);
      if (!Number.isFinite(spread) || spread <= 0) return null;
      minBin = active - spread;
      maxBin = active + spread;
    }
    if (maxBin < minBin) [minBin, maxBin] = [maxBin, minBin];
    const minPrice = this.meteoraPriceAtBin(minBin);
    const maxPrice = this.meteoraPriceAtBin(maxBin);
    const cur = this.meteoraCurrentPrice();
    return {
      minBin, maxBin,
      bins: maxBin - minBin + 1,
      minPrice, maxPrice,
      minPct: minPrice !== null && cur ? (minPrice / cur - 1) * 100 : null,
      maxPct: maxPrice !== null && cur ? (maxPrice / cur - 1) * 100 : null,
    };
  });

  readonly meteoraTotalBins = computed<number>(() => this.meteoraRange()?.bins ?? 0);

  /** True when the range exceeds what one position can hold. */
  readonly meteoraBinsOverflow = computed(() => this.meteoraTotalBins() > this.METEORA_MAX_BINS);

  private writeMeteoraBins(minBin: number, maxBin: number): void {
    const active = this.meteoraActiveBin();
    this.editParams.update(ep => ({
      ...ep,
      minBinId: String(minBin),
      maxBinId: String(maxBin),
      // Keep the ± control meaningful for symmetric ranges.
      ...(active !== null ? { binSpread: String(Math.max(active - minBin, maxBin - active)) } : {}),
    }));
    // The range decides how the deposit splits between the two tokens, so the
    // amounts have to follow it. The auto-balance effect only recomputes the
    // side opposite the last edit, so point it at whichever side has a value
    // when the user has moved the range without typing an amount first.
    if (this.dlmmLastEdited() === null) {
      const p = this.editParams();
      if (parseFloat(p['amountA'] ?? '') > 0) this.dlmmLastEdited.set('A');
      else if (parseFloat(p['amountB'] ?? '') > 0) this.dlmmLastEdited.set('B');
    }
  }

  setMeteoraSpread(v: string): void {
    const spread = parseInt(this.normalizeDecimal(v), 10);
    const active = this.meteoraActiveBin();
    if (!Number.isFinite(spread) || spread <= 0 || active === null) {
      this.setEditParam('binSpread', this.normalizeDecimal(v));
      return;
    }
    this.writeMeteoraBins(active - spread, active + spread);
  }

  /** User typed a price bound — convert it to a bin id, Meteora-style. */
  setMeteoraMinPrice(v: string): void {
    const bin = this.meteoraBinAtPrice(parseFloat(this.normalizeDecimal(v)));
    const r = this.meteoraRange();
    if (bin === null || !r) return;
    this.writeMeteoraBins(Math.min(bin, r.maxBin), r.maxBin);
  }
  setMeteoraMaxPrice(v: string): void {
    const bin = this.meteoraBinAtPrice(parseFloat(this.normalizeDecimal(v)));
    const r = this.meteoraRange();
    if (bin === null || !r) return;
    this.writeMeteoraBins(r.minBin, Math.max(bin, r.minBin));
  }
  /** +/- steppers on each bound, one bin at a time (Meteora has these too). */
  nudgeMeteoraBound(which: 'min' | 'max', delta: number): void {
    const r = this.meteoraRange();
    if (!r) return;
    if (which === 'min') this.writeMeteoraBins(Math.min(r.minBin + delta, r.maxBin), r.maxBin);
    else this.writeMeteoraBins(r.minBin, Math.max(r.maxBin + delta, r.minBin));
  }

  /**
   * Seed a range that actually suits the pool. The old default was a flat
   * "15 bins" regardless of bin step — which is +/-0.6% on a 4 bp pool but
   * +/-16% on a 100 bp one, so the same number meant wildly different
   * positions. Target a price band instead and clamp to the per-position bin
   * ceiling.
   */
  private seedMeteoraRange(): void {
    const p = this.editParams();
    if (p['minBinId'] || p['maxBinId']) return;   // explicit range already given
    const step = this.meteoraStep();
    const active = this.meteoraActiveBin();
    if (step === null || active === null) return;
    const TARGET_BAND = 0.05; // +/-5% around the active price
    const perSide = Math.max(
      1,
      Math.min(
        Math.floor((this.METEORA_MAX_BINS - 1) / 2),
        Math.round(Math.log(1 + TARGET_BAND) / Math.log(1 + step)),
      ),
    );
    this.writeMeteoraBins(active - perSide, active + perSide);
  }

  /**
   * Pool / position header for any Meteora panel: the pair, its logos and the
   * on-chain reference (pool, position or vault) the action targets.
   */
  readonly meteoraView = computed<{
    pair: string; symA: string; symB: string;
    logoA: string | null; logoB: string | null;
    refLabel: string; ref: string; kind: string;
  } | null>(() => {
    const t = this.action.type;
    if (!t.startsWith('meteora_') && !t.startsWith('orca_')) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] || this.resolveTokenDisplay(p['tokenA'] ?? '').symbol || '';
    const symB = p['tokenBSymbol'] || this.resolveTokenDisplay(p['tokenB'] ?? '').symbol || '';
    const single = p['tokenMint'] ?? '';
    const singleSym = single ? this.resolveTokenDisplay(single).symbol : '';

    const ref = p['position'] || p['positionId'] || p['positionNft'] || p['poolId'] || p['pool'] || p['vault'] || '';
    const refLabel = (p['position'] || p['positionId'] || p['positionNft']) ? 'Position'
      : p['vault'] ? 'Vault' : 'Pool';
    const kind = t.startsWith('orca_') ? 'WHIRLPOOL'
      : this.isMeteoraDlmm() ? 'DLMM'
      : t.includes('dammv2') ? 'DAMM V2'
      : t.includes('dammv1') ? 'DAMM V1'
      : t.includes('s2e') ? 'STAKE2EARN'
      : t.includes('vault') ? 'VAULT'
      : 'DLMM';

    const pair = symA && symB ? `${symA}/${symB}` : (singleSym || '');
    if (!pair && !ref) return null;
    return {
      pair, symA, symB,
      logoA: p['tokenALogo'] || (symA ? this.resolveTokenDisplay(p['tokenA'] ?? symA).logoURI ?? null : null),
      logoB: p['tokenBLogo'] || (symB ? this.resolveTokenDisplay(p['tokenB'] ?? symB).logoURI ?? null : null),
      refLabel, ref, kind,
    };
  });

  /**
   * The acting position's own numbers, when the card was spawned from a
   * positions row that had them (`dlmmActionParams`). A confirm-only action
   * has no inputs, so this is the entire content of the decision: what is in
   * the position, what is claimable, and whether its range still covers the
   * price. Null when the action arrived from chat text instead, where the
   * model only ever supplies addresses.
   */
  readonly meteoraPositionDetail = computed<{
    index: string;
    minPrice: number; maxPrice: number; currentPrice: number;
    binCount: number;
    amountA: number; amountB: number;
    feeA: number; feeB: number;
    hasFees: boolean;
    hasRange: boolean;
    outOfRange: boolean;
  } | null>(() => {
    const p = this.editParams();
    // Gate on the AMOUNTS, not the range: a DAMM v2 position is
    // constant-product and has no range at all, so requiring one meant the
    // whole detail panel — claimable amounts, what you get back, the
    // nothing-to-claim guard — silently vanished for every DAMM v2 action.
    const hasDetail = p['positionAmountA'] !== undefined || p['positionAmountB'] !== undefined
      || p['positionMinPrice'] !== undefined;
    if (!hasDetail) return null;
    const num = (k: string) => {
      const n = parseFloat(p[k] ?? '');
      return Number.isFinite(n) ? n : 0;
    };
    const feeA = num('positionFeeA');
    const feeB = num('positionFeeB');
    return {
      index: p['positionIndex'] ?? '',
      minPrice: num('positionMinPrice'),
      maxPrice: num('positionMaxPrice'),
      currentPrice: num('currentPrice'),
      binCount: num('positionBinCount'),
      amountA: num('positionAmountA'),
      amountB: num('positionAmountB'),
      feeA,
      feeB,
      hasFees: feeA > 0 || feeB > 0,
      hasRange: p['positionMinPrice'] !== undefined && p['positionMaxPrice'] !== undefined,
      outOfRange: p['positionOutOfRange'] === 'true',
    };
  });

  /** True when this is a fee/reward claim — the panel then leads with the
   *  claimable amounts rather than the position's balance. */
  readonly isClaimAction = computed(() => /claim|harvest|collect_fees|collect_rewards/.test(this.action?.type ?? ''));

  /**
   * Adding to an EXISTING position, as opposed to opening one. The difference
   * is not cosmetic: the range is fixed by the position and cannot be edited,
   * and no new position account is created — so no rent is locked. Offering
   * range inputs here would be a control that silently does nothing.
   */
  readonly isMeteoraAddToPosition = computed(() =>
    this.action?.type === 'meteora_add_to_position'
    // Increasing an Orca position deposits into its existing tick range, which
    // is likewise fixed — same panel, same "you can't change this" story.
    || this.action?.type === 'orca_increase_position');

  /** Orca open-position: the user picks a price band, like Raydium CLMM. */
  readonly isOrcaOpen = computed(() => this.action?.type === 'orca_open_position');

  /**
   * Every swap gets the two-panel "You pay / You receive" layout, not just
   * Jupiter's and Raydium's. A venue-specific swap is still a swap; leaving
   * the protocol ones on the raw field list meant the same operation looked
   * like a different product depending on which pool it ran through.
   *
   * The live aggregator quote stays restricted to `swap` / `raydium_swap` —
   * quoting a pool-specific trade against the aggregator would show a price
   * from a route this action will not take.
   */
  /**
   * A Magic Eden action on a specific NFT.
   *
   * These cards used to be a column of address inputs. What the user actually
   * needs to see is the picture — you recognise an NFT by looking at it, not
   * by reading its mint — plus the price and what it costs. The mint, seller,
   * token account and auction house are resolved server-side and never shown.
   */
  /**
   * Magic Eden actions that ask for an amount.
   *
   * They were rendering as bare form rows — a small uppercase label and an
   * input — while every other amount in the app is a panel with the token,
   * the balance it comes from and a Max. The amount IS the decision on these
   * cards; it should not look like a settings field.
   */
  // ── Token safety ──────────────────────────────────────────────────────────
  //
  // Runs on the action, not on request: someone who knows to ask "is this a
  // scam" is not the person at risk. The one at risk types a ticker they saw
  // and presses Confirm.
  //
  // It renders NOTHING when nothing was found. A panel that says "no problems"
  // on every clean token is noise, and noise is what teaches people to stop
  // reading the panel that matters.
  readonly tokenSafety = signal<{
    severity: 'note' | 'warn' | 'block';
    findings: Array<{ severity: string; title: string; detail: string }>;
    clean: boolean;
    limits: string[];
  } | null>(null);
  readonly safetyAcknowledged = signal(false);
  private safetyCheckedMint = '';

  /** The mint this action would put INTO the wallet. That is the one whose
   *  danger the user has not chosen yet. */
  private acquiredMint(): string | null {
    const t = this.action.type;
    const p = this.action.params;
    if (/swap|convert|exchange/.test(t)) {
      return (this.getEditParam('outputMint') || p['outputMint'] || p['toToken'] || null) as string | null;
    }
    if (/pumpfun_buy|pumpswap_buy|_buy$/.test(t) && !t.startsWith('me_')) {
      return (this.getEditParam('mint') || p['mint'] || p['tokenMint'] || null) as string | null;
    }
    return null;
  }

  /** Only findings worth interrupting for. Notes are carried for the answer,
   *  not for the card. */
  readonly safetyAlerts = computed(() =>
    (this.tokenSafety()?.findings ?? []).filter(f => f.severity !== 'note'));

  readonly safetyBlocks = computed(() =>
    this.safetyAlerts().some(f => f.severity === 'block'));

  /** True while a block is unacknowledged — Confirm stays shut. */
  readonly safetyGated = computed(() => this.safetyBlocks() && !this.safetyAcknowledged());

  private async runTokenSafety(): Promise<void> {
    const mint = this.acquiredMint();
    if (!mint || mint.length < 32) return;
    if (this.safetyCheckedMint === mint) return;
    this.safetyCheckedMint = mint;
    this.safetyAcknowledged.set(false);
    const data = await this.magicEden.read<any>('token_safety', { mintAddress: mint });
    // A check that could not run must not read as a clean bill of health.
    this.tokenSafety.set(data ?? null);
  }

  readonly isMeAmountPanel = computed(() => {
    const t = this.action.type;
    return /^me_(make_offer|list|sell|sell_change_price|buy_change_price|withdraw)$/.test(t);
  });

  /** The amount field this panel owns, so the generic list can skip it. */
  meAmountKey(): string {
    const t = this.action.type;
    if (t === 'me_withdraw') return 'amount';
    if (/change_price/.test(t)) return 'newPrice';
    return 'price';
  }

  /**
   * What is actually sitting in the Magic Eden escrow.
   *
   * Asked of the builder rather than of a separate read: `/actions/build` is
   * the path this card already uses to confirm, so it is the one path known to
   * work from here — and the withdraw builder answers with the balance it
   * would empty. Building is read-only; nothing is signed by asking.
   */
  readonly meEscrowSol = signal<number | null>(null);
  private meEscrowRead = false;

  private ensureMeEscrow(): void {
    if (this.meEscrowRead || this.action?.type !== 'me_withdraw') return;
    this.meEscrowRead = true;
    void (async () => {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (attempt > 0) await new Promise(r => setTimeout(r, 400 << attempt));
        try {
          const resp = await firstValueFrom(this.apiService.post<any>(
            '/actions/build', { type: 'me_withdraw', params: {} },
          ));
          const bal = Number(resp?.preview?.params?.balance);
          if (!Number.isFinite(bal)) continue;
          this.meEscrowSol.set(bal);
          if (bal > 0 && !this.getEditParam('amount')) {
            this.setEditParam('amount', String(bal));
          }
          return;
        } catch {
          // An empty balance is a 400 from the builder, and a real answer:
          // there is nothing to withdraw.
          this.meEscrowSol.set(0);
          return;
        }
      }
      this.meEscrowRead = false;
    })();
  }

  meBalanceNote(): string | null {
    if (this.action?.type !== 'me_withdraw') return null;
    this.ensureMeEscrow();
    const bal = this.meEscrowSol();
    if (bal === null) return null;
    return bal > 0 ? `Balance ${bal} SOL` : 'Nothing to withdraw';
  }

  /**
   * True when this field is a bridge recipient on an EVM chain.
   *
   * A Solana wallet cannot produce an EVM address, so a bridge to Base or
   * Arbitrum ends at an address the user has to go and find. Only the
   * destination matters: bridging INTO Solana lands in the wallet already
   * connected, and needs no second one.
   */
  needsEvmRecipient(key: string): boolean {
    return key === 'recipient' && this.isRelayBridge() && this.relayDestinationIsEvm();
  }

  // ── Transfer: who is actually receiving this ─────────────────────────────
  //
  // The recipient was a raw base58 box. The backend resolves `.sol` names, but
  // only at build time — so a user typed a name and found out whether it meant
  // anything after committing to the transaction. And nothing distinguished an
  // address that has never been seen on chain from one that has, which is the
  // difference between a typo and a wallet.

  readonly isTransfer = computed(() => this.action?.type === 'transfer');
  readonly isCloseAccounts = computed(() => this.action?.type === 'close_accounts');

  // ── What "close empty accounts" is actually about to close ───────────────
  //
  // The card had no parameters, so it rendered as a header above a button and
  // said neither how many accounts nor how much SOL. "About 0.002 each" is a
  // rate, not an answer; the wallet knows the number.

  readonly emptyAccounts = signal<Array<{ mint: string; account: string }>>([]);
  readonly emptyAccountsLoading = signal(false);
  /** Rent-exempt minimum for an SPL token account — what each one returns. */
  private static readonly ATA_RENT_SOL = 0.00203928;

  readonly emptyAccountsRent = computed(() =>
    this.emptyAccounts().length * ActionCardComponent.ATA_RENT_SOL);

  async loadEmptyAccounts(): Promise<void> {
    const owner = this.walletService.publicKey();
    if (!owner || !this.isCloseAccounts()) return;
    this.emptyAccountsLoading.set(true);
    try {
      const conn = createSolanaConnection('confirmed');
      const programs = [
        new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'),
        new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'), // Token-2022
      ];
      const found: Array<{ mint: string; account: string }> = [];
      for (const programId of programs) {
        const res = await conn.getParsedTokenAccountsByOwner(new PublicKey(owner), { programId })
          .catch(() => null);
        for (const a of res?.value ?? []) {
          const info = (a.account.data as any)?.parsed?.info;
          if (Number(info?.tokenAmount?.amount ?? '1') === 0) {
            found.push({ mint: info?.mint ?? '', account: a.pubkey.toBase58() });
          }
        }
      }
      this.emptyAccounts.set(found);
    } finally {
      this.emptyAccountsLoading.set(false);
    }
  }

  readonly recipientState = signal<
    | { kind: 'idle' }
    | { kind: 'checking' }
    | { kind: 'domain'; owner: string }
    | { kind: 'wallet'; funded: boolean }
    | { kind: 'self' }
    | { kind: 'invalid' }
  >({ kind: 'idle' });

  private recipientTimer?: ReturnType<typeof setTimeout>;
  private recipientKey = '';

  /** Debounced, because it runs on every keystroke of an address. */
  recipientMaybeCheck(): void {
    if (!this.isTransfer()) return;
    const raw = this.getEditParam('to').trim();
    if (raw === this.recipientKey) return;
    this.recipientKey = raw;
    if (this.recipientTimer) clearTimeout(this.recipientTimer);
    if (!raw) { this.recipientState.set({ kind: 'idle' }); return; }
    this.recipientTimer = setTimeout(() => void this.recipientCheckNow(raw), 400);
  }

  private async recipientCheckNow(raw: string): Promise<void> {
    this.recipientState.set({ kind: 'checking' });

    if (/\.sol$/i.test(raw)) {
      try {
        const resp = await firstValueFrom(this.apiService.post<any>('/actions/build', {
          type: 'sns_resolve', params: { domain: raw.replace(/\.sol$/i, '') },
        }));
        const owner = resp?.data?.owner ?? resp?.data ?? resp?.preview?.params?.owner;
        if (typeof owner === 'string' && owner.length >= 32) {
          this.recipientState.set({ kind: 'domain', owner });
          return;
        }
      } catch { /* fall through to "invalid" — an unregistered name is a typo */ }
      this.recipientState.set({ kind: 'invalid' });
      return;
    }

    if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(raw)) {
      this.recipientState.set({ kind: 'invalid' });
      return;
    }
    if (raw === this.walletService.publicKey()) {
      this.recipientState.set({ kind: 'self' });
      return;
    }

    // Funded or not decides whether the transfer also pays account rent, which
    // is the one cost a sender does not expect.
    try {
      const conn = createSolanaConnection('confirmed');
      const info = await conn.getAccountInfo(new PublicKey(raw));
      this.recipientState.set({ kind: 'wallet', funded: !!info && info.lamports > 0 });
    } catch {
      this.recipientState.set({ kind: 'wallet', funded: true });
    }
  }

  /** True on the bridge, which gets its own panel rather than a field list. */
  readonly isRelayBridge = computed(() =>
    /^(relay_bridge|bridge|cross_chain_swap)$/.test(this.action?.type ?? ''));

  /** What the bridge would actually deliver, refreshed as the inputs change. */
  readonly relayQuote = signal<{
    out: string; outSymbol: string; inSymbol: string;
    inUsd: string | null; outUsd: string | null;
    feeUsd: string | null; seconds: number | null; impact: string | null;
    // The quote already carries both tokens in full — symbol, name and logo.
    // Resolving an EVM token any other way means a second source that only
    // knows Solana, which is why the destination read "0xA0…".
    inToken: { symbol: string; name: string; logo: string | null } | null;
    outToken: { symbol: string; name: string; logo: string | null } | null;
  } | null>(null);
  readonly relayQuoting = signal(false);
  readonly relayQuoteError = signal<string | null>(null);
  private relayQuoteKey = '';
  private relayQuoteTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * Quote the bridge from what is on screen.
   *
   * A bridge card that shows only what you are sending is asking for a
   * signature on an unknown: the fee, the time and the amount that arrives are
   * the whole decision. Debounced, because it re-runs on every keystroke.
   */
  relayMaybeQuote(): void {
    if (!this.isRelayBridge()) return;
    // Keyed internally, so calling it on every edit costs nothing until the
    // wallet, chain or token actually changes.
    void this.loadRelayEvmBalance();
    const p = this.editParams();
    const key = [p['originChainId'], p['destinationChainId'], p['originCurrency'],
                 p['destinationCurrency'], p['amount'], p['recipient'], p['sender']].join('|');
    if (key === this.relayQuoteKey) return;
    this.relayQuoteKey = key;

    // A quote without a recipient the destination can receive on is a question
    // we already know the answer to. Asking it anyway produced a red API error
    // for a wallet the user simply had not connected yet — a prompt dressed as
    // a fault. The panel asks for the wallet instead.
    // The recipient is no longer part of readiness: a price does not depend on
    // who receives it, and hiding the rate until a wallet is connected asks
    // the user to commit before they can see what they are committing to.
    const ready = p['originChainId'] && p['destinationChainId'] && p['originCurrency']
      && p['destinationCurrency'] && Number(p['amount']) > 0;
    if (!ready) { this.relayQuote.set(null); this.relayQuoteError.set(null); return; }

    if (this.relayQuoteTimer) clearTimeout(this.relayQuoteTimer);
    this.relayQuoteTimer = setTimeout(() => void this.relayQuoteNow(), 450);
  }

  private async relayQuoteNow(): Promise<void> {
    const p = this.editParams();
    this.relayQuoting.set(true);
    this.relayQuoteError.set(null);
    try {
      const resp = await firstValueFrom(this.apiService.post<any>('/actions/build', {
        type: 'relay_get_quote',
        params: {
          originChainId: this.relayCanonicalChain(p['originChainId'] ?? ''),
          destinationChainId: this.relayCanonicalChain(p['destinationChainId'] ?? ''),
          originCurrency: p['originCurrency'],
          destinationCurrency: p['destinationCurrency'],
          amount: p['amount'],
          tradeType: 'EXACT_INPUT',
          ...(p['recipient'] ? { recipient: p['recipient'] } : {}),
          ...(p['sender'] ? { sender: p['sender'] } : {}),
        },
      }));
      const d = resp?.quote?.details ?? resp?.details ?? {};
      const outAmt = d?.currencyOut?.amountFormatted;
      if (!outAmt) { this.relayQuote.set(null); return; }
      const tok = (c: any) => c ? {
        symbol: c.symbol ?? '',
        name: c.name ?? '',
        logo: c.metadata?.logoURI ?? c.metadata?.logoUri ?? c.logoURI ?? null,
      } : null;
      const usd = (v: unknown): string | null => {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? (n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`) : null;
      };
      this.relayQuote.set({
        out: this.relayAmountText(outAmt),
        inUsd: usd(d?.currencyIn?.amountUsd),
        outUsd: usd(d?.currencyOut?.amountUsd),
        outSymbol: d?.currencyOut?.currency?.symbol ?? '',
        inSymbol: d?.currencyIn?.currency?.symbol ?? '',
        feeUsd: resp?.preview?.estimatedFee ?? null,
        // Relay calls it timeEstimate; our model renames it on the way out.
        // Reading only one of the two names is how the estimate went missing.
        seconds: typeof d?.timeEstimate === 'number' ? d.timeEstimate
          : (typeof d?.estimatedTime === 'number' ? d.estimatedTime : null),
        impact: d?.totalImpact?.percent ?? null,
        inToken: tok(d?.currencyIn?.currency),
        outToken: tok(d?.currencyOut?.currency),
      });
    } catch (err: unknown) {
      // The builder's message is the useful one — it names a blocked address,
      // an unsupported pair, an amount below the minimum.
      //
      // It goes through the same sanitizer as every other error on this card.
      // Reading it raw is how "Relay API error:" reached a user: the quote path
      // was the one error path that skipped the cleanup every other path gets.
      const msg = (err as { error?: { error?: string } })?.error?.error;
      this.relayQuote.set(null);
      this.relayQuoteError.set(
        (msg ? sanitizeErrorMessage(msg, this.action?.type) : '') || 'No route for this pair right now.',
      );
    } finally {
      this.relayQuoting.set(false);
    }
  }

  /**
   * Token display for a bridge side, from the quote rather than the registry.
   *
   * Our registry holds Solana mints. An Ethereum or Base address is not in it
   * and never will be, so the destination rendered as a coloured circle and a
   * truncated 0x — while the quote sitting beside it carried the symbol, the
   * name and the logo for both sides.
   */
  relayTokenDisplay(key: string): RelayTokenMeta | null {
    if (!this.isRelayBridge()) return null;
    const q = this.relayQuote();
    const fromQuote = key === 'originCurrency' ? q?.inToken
      : key === 'destinationCurrency' ? q?.outToken : null;
    // The quote names the token but does not say how many decimals it has, so
    // a cached entry that does is preferred for that one field — reading a
    // balance with the wrong scale is a factor-of-a-million error, not a
    // cosmetic one.
    const cached = this.relayTokenCache().get(
      `${this.relayCanonicalChain(this.getEditParam(key === 'originCurrency' ? 'originChainId' : 'destinationChainId'))}|${this.getEditParam(key).toLowerCase()}`,
    );
    if (fromQuote) {
      return {
        symbol: fromQuote.symbol, name: fromQuote.name, logoURI: fromQuote.logo,
        decimals: cached?.decimals,
      };
    }

    // Before a quote there is no quote to read, and the destination token sat
    // as a coloured circle over a truncated 0x for the whole time the user was
    // deciding whether to connect a wallet. Relay's currency list names it
    // without needing one.
    const chainKey = key === 'originCurrency' ? 'originChainId' : 'destinationChainId';
    const chain = this.relayCanonicalChain(this.getEditParam(chainKey));
    const addr = this.getEditParam(key);
    if (!chain || !addr) return null;
    const hit = this.relayTokenCache().get(`${chain}|${addr.toLowerCase()}`);
    // Deferred out of the render pass. This function is called FROM the
    // template and the lookup writes a signal the same template reads, so
    // starting it here re-entered change detection on every pass — the tab
    // stopped responding, which is what "the page won't load" looked like.
    if (hit === undefined) queueMicrotask(() => void this.relayLookupToken(chain, addr));
    return hit ?? null;
  }

  private readonly relayTokenCache = signal<Map<string, RelayTokenMeta | null>>(new Map());

  /** Name one token from Relay's currency list for its chain. */
  private async relayLookupToken(chain: string, address: string): Promise<void> {
    const key = `${chain}|${address.toLowerCase()}`;
    if (this.relayTokenCache().has(key)) return;
    // Claim the slot before awaiting, or every change-detection pass fires
    // another request for the same token. Safe now that the caller defers:
    // this write happens after the render, not inside it.
    this.relayTokenCache.update(m => new Map(m).set(key, null));
    try {
      const resp = await firstValueFrom(this.apiService.post<any>('/actions/build', {
        type: 'relay_get_currencies',
        params: { chainIds: [Number(chain)], address },
      }));
      const rows: any[] = resp?.data ?? resp?.currencies ?? [];
      const t = rows.find(r => String(r?.address ?? '').toLowerCase() === address.toLowerCase()) ?? rows[0];
      if (!t?.symbol) return;
      this.relayTokenCache.update(m => new Map(m).set(key, {
        symbol: t.symbol, name: t.name ?? '',
        logoURI: t.metadata?.logoURI ?? t.metadata?.logoUri ?? t.logoURI ?? null,
        decimals: typeof t.decimals === 'number' ? t.decimals : undefined,
      }));
    } catch { /* leave it unnamed rather than guess */ }
  }

  /**
   * Make the chain selects mean what they show.
   *
   * A `<select>` with no matching value renders its first option, and the
   * template marks that option selected — so the card read "From chain:
   * Solana" while the parameter was empty, and the quote sat waiting for a
   * chain the user could see. Writing the shown value fixes the gap between
   * what is displayed and what is held.
   */
  /** Relay's mint for native SOL. Our registry's "SOL" is the wrapped one. */
  private static readonly WRAPPED_SOL = 'So11111111111111111111111111111111111111112';
  private static readonly NATIVE_SOL = '11111111111111111111111111111111';

  private relaySeedChains(): void {
    if (!this.isRelayBridge()) return;
    // "0.01 SOL" filled the card with WSOL, because that is what SOL means
    // everywhere else here — Jupiter swaps the wrapped mint. Relay is the one
    // place the two are different assets, and the service quietly bridged the
    // native one because that is what the wallet holds. The card said WSOL
    // while the transaction said SOL; whichever is true, they must not
    // disagree. WSOL stays in the picker for anyone who means it.
    if (this.getEditParam('originCurrency') === ActionCardComponent.WRAPPED_SOL
        && this.relayCanonicalChain(this.getEditParam('originChainId')) === String(RELAY_SOLANA_CHAIN_ID)) {
      this.setEditParam('originCurrency', ActionCardComponent.NATIVE_SOL);
    }
    const origin = this.getEditParam('originChainId');
    const dest = this.getEditParam('destinationChainId');
    const canonicalOrigin = this.relayCanonicalChain(origin);
    if (!origin) this.setEditParam('originChainId', String(RELAY_SOLANA_CHAIN_ID));
    else if (canonicalOrigin !== origin) this.setEditParam('originChainId', canonicalOrigin);
    const canonicalDest = this.relayCanonicalChain(dest);
    if (dest && canonicalDest !== dest) this.setEditParam('destinationChainId', canonicalDest);
  }

  // ── Bridge token picker ────────────────────────────────────────────────
  //
  // The card could only ever show the token the model named. Someone who
  // wanted anything else — a different stablecoin, the chain's native asset,
  // any of the thousands Relay lists — had no way to say so. Relay's currency
  // list covers every chain it bridges, which is the one source that can
  // answer "what can I receive on Base".

  @ViewChild('tokenDialog') tokenDialog?: ElementRef<HTMLDialogElement>;
  readonly relayChainQuery = signal('');
  /** The chain being browsed inside the picker, which is not committed until
   *  a token on it is chosen — picking a chain and closing should change
   *  nothing. */
  readonly relayPickerChain = signal('');

  /**
   * Every chain Relay bridges, from Relay.
   *
   * The hardcoded eleven were the ones I happened to know, which is not a list
   * anyone can maintain — Relay has sixty-eight and adds more. Loaded once per
   * card and cached for the tab, with the built-in list as the fallback so the
   * picker is never empty.
   */
  static relayChainsCache: Array<{ label: string; value: string; icon: string | null }> | null = null;
  readonly relayChains = signal<Array<{ label: string; value: string; icon: string | null }>>(
    ActionCardComponent.relayChainsCache ?? RELAY_CHAINS.map(c => ({ ...c, icon: null })),
  );

  private async loadRelayChains(): Promise<void> {
    if (ActionCardComponent.relayChainsCache) {
      this.relayChains.set(ActionCardComponent.relayChainsCache);
      return;
    }
    try {
      const resp = await firstValueFrom(this.apiService.post<any>('/actions/build', {
        type: 'relay_get_chains', params: {},
      }));
      const rows: any[] = resp?.data?.chains ?? resp?.data ?? [];
      const chains = rows
        .filter(c => c?.id && !c?.disabled)
        .map(c => ({
          label: String(c.displayName ?? c.name ?? c.id),
          value: String(c.id),
          icon: c.iconUrl ?? null,
        }))
        // Solana first — it is where every bridge from here starts — then the
        // rest alphabetically, which is how someone looks for one.
        .sort((a, b) => {
          const sol = String(RELAY_SOLANA_CHAIN_ID);
          if (a.value === sol) return -1;
          if (b.value === sol) return 1;
          return a.label.localeCompare(b.label);
        });
      if (chains.length) {
        ActionCardComponent.relayChainsCache = chains;
        this.relayChains.set(chains);
      }
    } catch { /* the built-in list stands */ }
  }

  relayFilteredChains(): Array<{ label: string; value: string; icon: string | null }> {
    const q = this.relayChainQuery().trim().toLowerCase();
    const all = this.relayChains();
    return q ? all.filter(c => c.label.toLowerCase().includes(q)) : all;
  }

  selectRelayPickerChain(value: string): void {
    this.relayPickerChain.set(value);
    this.relayPickerRows.set([]);
    void this.relaySearchTokens(this.relayPickerQuery());
  }

  readonly relayPickerFor = signal<string | null>(null);
  readonly relayPickerQuery = signal('');
  readonly relayPickerRows = signal<Array<{ address: string; symbol: string; name: string; logo: string | null }>>([]);
  readonly relayPickerLoading = signal(false);
  private relayPickerTimer: ReturnType<typeof setTimeout> | null = null;

  openRelayPicker(key: string): void {
    if (!this.isRelayBridge() || !this.isEditable()) return;
    this.relayPickerFor.set(key);
    this.relayPickerQuery.set('');
    this.relayChainQuery.set('');
    this.relayPickerRows.set([]);
    this.relayPickerChain.set(this.relayCanonicalChain(
      this.getEditParam(key === 'originCurrency' ? 'originChainId' : 'destinationChainId'),
    ) || String(RELAY_SOLANA_CHAIN_ID));
    void this.relaySearchTokens('');
    void this.loadRelayChains();
    queueMicrotask(() => this.tokenDialog?.nativeElement?.showModal?.());
  }

  closeRelayPicker(): void {
    this.relayPickerFor.set(null);
    this.tokenDialog?.nativeElement?.close?.();
  }

  onRelayPickerQuery(term: string): void {
    this.relayPickerQuery.set(term);
    if (this.relayPickerTimer) clearTimeout(this.relayPickerTimer);
    this.relayPickerTimer = setTimeout(() => void this.relaySearchTokens(term), 250);
  }

  private async relaySearchTokens(term: string): Promise<void> {
    const key = this.relayPickerFor();
    if (!key) return;
    const chain = this.relayPickerChain();
    if (!chain) return;
    this.relayPickerLoading.set(true);
    try {
      const resp = await firstValueFrom(this.apiService.post<any>('/actions/build', {
        type: 'relay_get_currencies',
        params: {
          chainIds: [Number(chain)],
          ...(term.trim() ? { term: term.trim() } : {}),
          // Verified only, unprompted: a bridge search that returns three
          // tokens with the same ticker is a way to send money to the wrong
          // one. A pasted address still reaches anything.
          verified: true,
          limit: 30,
        },
      }));
      const rows: any[] = Array.isArray(resp?.data) ? resp.data : [];
      this.relayPickerRows.set(rows
        .filter(r => r?.address && r?.symbol)
        .map(r => ({
          address: String(r.address),
          symbol: String(r.symbol),
          name: String(r.name ?? ''),
          logo: r.metadata?.logoURI ?? r.metadata?.logoUri ?? r.logoURI ?? null,
        })));
    } catch {
      this.relayPickerRows.set([]);
    } finally {
      this.relayPickerLoading.set(false);
    }
  }

  chooseRelayToken(key: string, row: { address: string; symbol: string; name: string; logo: string | null }): void {
    // The chain comes with the token: they were chosen together, and a token
    // address only means anything on the chain it was listed for.
    const chain = this.relayPickerChain();
    const chainKey = key === 'originCurrency' ? 'originChainId' : 'destinationChainId';
    if (chain && this.getEditParam(chainKey) !== chain) this.setEditParam(chainKey, chain);
    this.setEditParam(key, row.address);
    this.relayTokenCache.update(m => new Map(m).set(`${chain}|${row.address.toLowerCase()}`, {
      symbol: row.symbol, name: row.name, logoURI: row.logo,
    }));
    this.closeRelayPicker();
    this.relayMaybeQuote();
  }

  /** Changing a chain invalidates the token chosen on the old one. */
  onRelayChainChange(key: string): void {
    const tokenKey = key === 'originChainId' ? 'originCurrency' : 'destinationCurrency';
    this.setEditParam(tokenKey, '');
    this.relayPickerFor.set(null);
    this.relayMaybeQuote();
  }

  /** Slippage is Relay's to choose unless the user takes it. */
  relaySlippageAuto(): boolean {
    return !this.getEditParam('slippageTolerance');
  }

  relaySetSlippageAuto(auto: boolean): void {
    this.setEditParam('slippageTolerance', auto ? '' : '50');
    this.relayMaybeQuote();
  }

  /**
   * Slippage as a percentage, because "50 bps" is a unit traders use and
   * everyone else has to convert. Stored in bps, since that is what Relay
   * takes — the conversion belongs here, not in the user's head.
   */
  relaySlippagePercent(): string {
    const bps = Number(this.getEditParam('slippageTolerance'));
    return Number.isFinite(bps) && bps > 0 ? String(Number((bps / 100).toFixed(2))) : '';
  }

  relaySetSlippagePercent(percent: string): void {
    const n = Number(percent.replace(',', '.'));
    this.setEditParam('slippageTolerance', Number.isFinite(n) && n > 0 ? String(Math.round(n * 100)) : '');
    this.relayMaybeQuote();
  }

  /**
   * An amount at the size it actually is.
   *
   * Six decimal places is fine for a stablecoin and useless for BTC: bridging
   * 0.001 SOL into cbBTC printed "0", which reads as a broken quote rather
   * than a small number. Significant digits keep the leading zeros and stop
   * where the number does.
   */
  relayAmountText(raw: unknown): string {
    const n = Number(raw);
    if (!Number.isFinite(n) || n === 0) return '0';
    if (n >= 1) return n.toFixed(4).replace(/\.?0+$/, '');
    // toPrecision keeps four meaningful figures however small the number is;
    // toFixed would have thrown them all away.
    return Number(n.toPrecision(4)).toFixed(12).replace(/0+$/, '').replace(/\.$/, '');
  }

  /** A chain's name, for a card that should not print ids at people. */
  relayChainName(id: string): string {
    const want = this.relayCanonicalChain(id);
    return this.relayChains().find(c => c.value === want)?.label
      ?? RELAY_CHAINS.find(c => c.value === want)?.label
      ?? (id ? `Chain ${id}` : '');
  }

  relayChainIcon(id: string): string | null {
    const want = this.relayCanonicalChain(id);
    return this.relayChains().find(c => c.value === want)?.icon ?? null;
  }

  /** 900 was our own id for Solana, and the model still writes it. */
  relayCanonicalChain(id: string): string {
    return id === '900' ? String(RELAY_SOLANA_CHAIN_ID) : id;
  }

  /** True when the bridge lands somewhere a Solana wallet cannot receive. */
  relayDestinationIsEvm(): boolean {
    const dest = this.relayCanonicalChain(this.getEditParam('destinationChainId'));
    return !!dest && dest !== String(RELAY_SOLANA_CHAIN_ID);
  }

  /**
   * True when the funds leave a chain only an EVM wallet can sign for.
   *
   * The mirror of `relayDestinationIsEvm`, and the reason a BNB→Sei quote came
   * back "Invalid address GB5m…rBjt for chain 56": Relay checks the sender
   * against the ORIGIN chain, and the only wallet this app authenticates is a
   * Solana one.
   */
  relayOriginIsEvm(): boolean {
    const origin = this.relayCanonicalChain(this.getEditParam('originChainId'));
    return !!origin && origin !== String(RELAY_SOLANA_CHAIN_ID);
  }

  /** True once a sender the origin chain can actually spend from is set. */
  relaySenderReady(): boolean {
    if (!this.relayOriginIsEvm()) return true;
    return /^0x[0-9a-fA-F]{40}$/.test(this.getEditParam('sender').trim());
  }

  // ── What the sending wallet actually holds ────────────────────────────────
  //
  // Without it the card asks for an amount with no idea whether the wallet can
  // cover it, so the first news of a shortfall is a rejection at signing. The
  // Solana side reuses the balance line every other card has; the EVM side has
  // to be read through the connected wallet, because we hold no RPC for
  // sixty-odd chains.

  readonly relayEvmBalance = signal<number | null>(null);
  readonly relayEvmSymbol = signal<string>('');
  /** The wallet is connected but pointed at a different chain than the route
   *  leaves from — so any balance it reports belongs to the wrong network. */
  readonly relayEvmChainMismatch = signal(false);
  readonly relayEvmSwitching = signal(false);
  private relayEvmBalanceKey = '';
  private evmProviderId: string | null = null;

  private currentEvmProvider(): EvmProvider | null {
    if (this.evmProviderId) return this.evmProviders.get(this.evmProviderId) ?? null;
    // Only one wallet ever announced itself: it is the one that connected.
    return this.evmProviders.size === 1 ? [...this.evmProviders.values()][0] : null;
  }

  /**
   * Read the origin balance from the connected wallet.
   *
   * Guarded on the chain the wallet is actually on. A provider answers
   * `eth_getBalance` for whatever network it is pointed at without saying so,
   * so a wallet sitting on Ethereum would have reported its ETH as though it
   * were the BNB about to be bridged — a wrong number is worse here than none,
   * because Max would then fill in an amount that cannot be paid.
   */
  async loadRelayEvmBalance(): Promise<void> {
    if (!this.isRelayBridge() || !this.relayOriginIsEvm()) {
      this.relayEvmBalance.set(null);
      this.relayEvmChainMismatch.set(false);
      return;
    }
    const addr = this.getEditParam('sender').trim();
    const chain = Number(this.relayCanonicalChain(this.getEditParam('originChainId')));
    const token = this.getEditParam('originCurrency').trim();
    if (!/^0x[0-9a-fA-F]{40}$/.test(addr) || !chain || !token) return;

    const key = `${addr}|${chain}|${token}`;
    if (key === this.relayEvmBalanceKey) return;
    this.relayEvmBalanceKey = key;

    const provider = this.currentEvmProvider();
    if (!provider) return;
    try {
      const onChain = Number(await provider.request({ method: 'eth_chainId' }));
      if (onChain !== chain) {
        this.relayEvmChainMismatch.set(true);
        this.relayEvmBalance.set(null);
        return;
      }
      this.relayEvmChainMismatch.set(false);

      const meta = this.relayTokenDisplay('originCurrency');
      const decimals = meta?.decimals ?? 18;
      const raw = /^0x0{40}$/.test(token)
        ? await provider.request({ method: 'eth_getBalance', params: [addr, 'latest'] })
        // balanceOf(address) — selector plus the address in a 32-byte word.
        : await provider.request({
            method: 'eth_call',
            params: [{ to: token, data: `0x70a08231${addr.slice(2).toLowerCase().padStart(64, '0')}` }, 'latest'],
          });
      const units = BigInt(raw || '0x0');
      this.relayEvmBalance.set(Number(units) / 10 ** decimals);
      this.relayEvmSymbol.set(meta?.symbol ?? '');
    } catch {
      // A wallet that will not answer is not an error worth a red line: the
      // balance is a convenience, and the quote does not depend on it.
      this.relayEvmBalance.set(null);
    }
  }

  /** Point the wallet at the chain the route leaves from. It has to happen
   *  before signing anyway; doing it here means the balance is true. */
  async switchEvmChain(): Promise<void> {
    const provider = this.currentEvmProvider();
    const chain = Number(this.relayCanonicalChain(this.getEditParam('originChainId')));
    if (!provider || !chain) return;
    this.relayEvmSwitching.set(true);
    this.evmError.set(null);
    try {
      await provider.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: `0x${chain.toString(16)}` }],
      });
      this.relayEvmBalanceKey = '';
      await this.loadRelayEvmBalance();
    } catch (err: unknown) {
      const code = (err as { code?: number })?.code;
      this.evmError.set(code === 4902
        ? `Your wallet does not have ${this.relayChainName(this.getEditParam('originChainId'))} configured. Add it in the wallet, then try again.`
        : 'The network switch was declined in your wallet.');
    } finally {
      this.relayEvmSwitching.set(false);
    }
  }

  /** Spend it all. The fee still has to come from somewhere, so a native
   *  balance keeps a little back rather than producing an amount that is
   *  arithmetically correct and unpayable. */
  setMaxRelay(): void {
    if (this.relayOriginIsEvm()) {
      const b = this.relayEvmBalance();
      if (b === null || b <= 0) return;
      const native = /^0x0{40}$/.test(this.getEditParam('originCurrency').trim());
      const usable = native ? Math.max(0, b - 0.002) : b;
      if (usable <= 0) return;
      this.setEditParam('amount', String(Number(usable.toPrecision(8))));
    } else {
      const b = this.inputBalance();
      if (b === null || b <= 0) return;
      const native = !this.getEditParam('originCurrency').trim()
        || /^1{32}$/.test(this.getEditParam('originCurrency').trim())
        || this.getEditParam('originCurrency').trim() === this.SOL_MINT;
      const usable = native ? Math.max(0, b - 0.01) : b;
      if (usable <= 0) return;
      this.setEditParam('amount', String(Number(usable.toPrecision(8))));
    }
    this.relayMaybeQuote();
  }

  /** The balance line for whichever side of the bridge we are sending from. */
  relayOriginBalance(): number | null {
    return this.relayOriginIsEvm() ? this.relayEvmBalance() : this.inputBalance();
  }

  /** True once a recipient the destination can actually accept is set. */
  relayRecipientReady(): boolean {
    const r = this.getEditParam('recipient').trim();
    if (!this.relayDestinationIsEvm()) return true;
    return /^0x[0-9a-fA-F]{40}$/.test(r);
  }

  /** Paste is available, but not the first thing offered. */
  readonly relayPasteRecipient = signal(false);

  readonly evmConnecting = signal(false);
  readonly evmError = signal<string | null>(null);
  readonly evmWallets = signal<Array<{ id: string; name: string; icon: string | null }>>([]);

  /** A local mark for wallets whose announced icon does not render. Phantom
   *  announces one that fails, and a blank square beside "Phantom" is worse
   *  than no icon at all. */
  private static readonly WALLET_ICONS: Record<string, string> = {
    phantom: 'assets/icons/wallets/phantom.svg',
  };

  walletIcon(w: { name: string; icon: string | null }): string | null {
    return ActionCardComponent.WALLET_ICONS[w.name.trim().toLowerCase()] ?? w.icon;
  }
  readonly evmPicking = signal(false);
  private evmProviders = new Map<string, EvmProvider>();

  /**
   * Every EVM wallet in this browser, not whichever one claimed
   * `window.ethereum` first.
   *
   * With several installed they fight over that property, so asking it
   * connected Rabby to someone reaching for MetaMask. EIP-6963 exists for
   * exactly this: wallets announce themselves and the page chooses.
   */
  private evmAnnounceHandler?: (e: Event) => void;

  /**
   * Listen for wallet announcements for as long as the card lives.
   *
   * Wallets answer `eip6963:requestProvider` whenever they get round to it —
   * some in the same tick, some later. Asking and unsubscribing in one breath
   * caught whoever was fastest, so the list filled in visibly after it was
   * already on screen. Subscribing on init means the answer is ready before
   * anyone clicks.
   */
  private startEvmDiscovery(): void {
    if (this.evmAnnounceHandler) return;
    this.evmAnnounceHandler = (e: Event) => {
      const d = (e as CustomEvent).detail as { info?: { uuid: string; name: string; icon: string }; provider?: any };
      if (!d?.info?.uuid || !d.provider) return;
      if (this.evmProviders.has(d.info.uuid)) return;
      this.evmProviders.set(d.info.uuid, d.provider);
      this.evmWallets.update(list => [...list, {
        id: d.info!.uuid, name: d.info!.name, icon: d.info!.icon ?? null,
      }]);
    };
    window.addEventListener('eip6963:announceProvider', this.evmAnnounceHandler);
    window.dispatchEvent(new Event('eip6963:requestProvider'));

    // Older wallets announce nothing and only set window.ethereum. Offered
    // once nobody has announced, since the announcing ones are the same object.
    setTimeout(() => {
      const legacy = (window as any).ethereum;
      if (!this.evmWallets().length && legacy) {
        this.evmProviders.set('legacy', legacy);
        this.evmWallets.set([{ id: 'legacy', name: legacy.isMetaMask ? 'MetaMask' : 'Browser wallet', icon: null }]);
      }
    }, 300);
  }

  private stopEvmDiscovery(): void {
    if (!this.evmAnnounceHandler) return;
    window.removeEventListener('eip6963:announceProvider', this.evmAnnounceHandler);
    this.evmAnnounceHandler = undefined;
  }

  @ViewChild('evmDialog') evmDialog?: ElementRef<HTMLDialogElement>;
  private evmDialogFor = 'recipient';

  /** Offer the choice; connect straight away when there is only one. */
  connectEvmWallet(key: string): void {
    this.evmError.set(null);
    this.startEvmDiscovery();
    const wallets = this.evmWallets();
    if (!wallets.length) {
      this.evmError.set('No EVM wallet found in this browser. Paste the destination address instead.');
      return;
    }
    if (wallets.length === 1) { void this.useEvmWallet(wallets[0].id, key); return; }
    this.evmDialogFor = key;
    this.evmPicking.set(true);
    // showModal, not a positioned div: a dialog renders in the browser's top
    // layer, which no transformed ancestor can clip — the trap that put the
    // token picker behind the chat once already.
    queueMicrotask(() => this.evmDialog?.nativeElement?.showModal?.());
  }

  closeEvmDialog(): void {
    this.evmPicking.set(false);
    this.evmDialog?.nativeElement?.close?.();
  }

  async useEvmWallet(id: string, key: string): Promise<void> {
    const provider = this.evmProviders.get(id);
    if (!provider) return;
    this.closeEvmDialog();
    this.evmProviderId = id;
    this.evmConnecting.set(true);
    this.evmError.set(null);
    try {
      const accounts = await provider.request({ method: 'eth_requestAccounts' });
      const addr = accounts?.[0];
      if (!addr) { this.evmError.set('That wallet returned no account.'); return; }
      this.setEditParam(key, addr);
      // One wallet, both jobs. On an EVM→EVM route the same account sends and
      // receives, and asking for it twice would be asking the same question
      // twice. Only the side that is actually EVM gets filled: on BNB→Solana
      // this account sends, and the Solana wallet already signed in receives.
      if (key === 'sender' && this.relayDestinationIsEvm() && !this.getEditParam('recipient')) {
        this.setEditParam('recipient', addr);
      } else if (key === 'recipient' && this.relayOriginIsEvm() && !this.getEditParam('sender')) {
        this.setEditParam('sender', addr);
      }
      if (this.isRelayBridge()) void this.loadRelayEvmBalance();
      this.relayMaybeQuote();
    } catch (err: unknown) {
      this.evmError.set((err as { code?: number })?.code === 4001
        ? 'Connection refused in your wallet.'
        : 'Could not reach that wallet. Paste the destination address instead.');
    } finally {
      this.evmConnecting.set(false);
    }
  }

  /** Forget the EVM wallet. It stays connected to the browser; this card
   *  simply stops naming it — on both sides, since it was one account doing
   *  both jobs and leaving half of it behind would quote against an address
   *  the card no longer shows. */
  disconnectEvmWallet(): void {
    this.setEditParam('recipient', '');
    this.setEditParam('sender', '');
    this.evmPicking.set(false);
    this.evmError.set(null);
    this.relayQuote.set(null);
    this.relayEvmBalance.set(null);
    this.relayEvmChainMismatch.set(false);
  }

  meAmountLabel(): string {
    const t = this.action.type;
    if (t === 'me_withdraw') return 'Withdraw';
    if (/change_price/.test(t)) return 'New price';
    if (/list|_sell$/.test(t)) return 'Your ask';
    return 'Your offer';
  }

  /**
   * A bid below the escrow's rent-exemption cannot exist.
   *
   * Magic Eden holds the bid in an escrow account and that account has to
   * cover its own rent, so the chain rejects anything smaller with
   * InsufficientFundsForRent — which surfaced as "not enough balance" on a
   * wallet holding 0.245 SOL. Say the floor before they sign, not after.
   */
  readonly ME_MIN_OFFER_SOL = 0.00089088;

  /**
   * Stated up front, not only once it has been broken.
   *
   * A floor a user meets by accident is a floor they never learn; the first
   * they hear of it is a rejected number they have already typed. Rounded up
   * from the exact 890,880 lamports so the figure shown is always one that
   * works.
   */
  meOfferMinNote(): string | null {
    if (!/make_offer|buy_change_price/.test(this.action?.type ?? '')) return null;
    return 'Minimum 0.0009 SOL — Magic Eden escrows the bid, and the escrow pays its own rent';
  }

  meOfferBelowMin(): boolean {
    if (!/make_offer|buy_change_price/.test(this.action?.type ?? '')) return false;
    const v = Number(this.getEditParam(this.meAmountKey()));
    return Number.isFinite(v) && v > 0 && v < this.ME_MIN_OFFER_SOL;
  }

  /** Only offers and listings expire. */
  meHasExpiry(): boolean {
    return /^me_(make_offer|list|sell)$/.test(this.action.type);
  }

  readonly ME_EXPIRY_CHOICES: ReadonlyArray<{ label: string; days: number }> = [
    { label: 'No expiry', days: 0 },
    { label: '1 day', days: 1 },
    { label: '3 days', days: 3 },
    { label: '7 days', days: 7 },
    { label: '30 days', days: 30 },
  ];

  /**
   * Expiry was a raw unix timestamp box. Nobody knows what 1785521483 is, and
   * a user who wants "a week" should not have to compute one.
   */
  meExpiryDays(): number {
    const raw = Number(this.getEditParam('expiry'));
    if (!Number.isFinite(raw) || raw <= 0) return 0;
    const secs = raw - Math.floor(Date.now() / 1000);
    if (secs <= 0) return 0;
    return this.ME_EXPIRY_CHOICES
      .filter(c => c.days > 0)
      .reduce((best, c) => (Math.abs(c.days * 86400 - secs) < Math.abs(best.days * 86400 - secs) ? c : best),
              { label: '', days: 1 }).days;
  }

  /** The date the chosen expiry lands on, or null for an open-ended listing. */
  meExpiryOn(): string | null {
    const raw = Number(this.getEditParam('expiry'));
    if (!Number.isFinite(raw) || raw <= 0) return null;
    return new Date(raw * 1000).toLocaleString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  setMeExpiry(days: number): void {
    this.setEditParam('expiry', days === 0 ? '' : String(Math.floor(Date.now() / 1000) + days * 86400));
  }

  /**
   * Quick-fills, each labelled with the number it puts in. A chip the user
   * clicks is their choice; a value the card pre-fills is the card's, which
   * is why the box still starts empty.
   */
  meQuickFills(): Array<{ label: string; value: number }> {
    const out: Array<{ label: string; value: number }> = [];
    // Withdrawing has one number worth offering, and it is all of it.
    if (this.action.type === 'me_withdraw') {
      const bal = this.meEscrowSol();
      return bal && bal > 0 ? [{ label: 'Max', value: bal }] : [];
    }
    const ask = this.meAskPrice();
    const floor = this.meFloorPrice();
    if (floor) out.push({ label: 'Floor', value: floor });
    if (ask && (!floor || Math.abs(ask - floor) > 1e-9)) out.push({ label: 'Ask', value: ask });
    const ref = ask ?? floor;
    if (ref && /make_offer/.test(this.action.type)) {
      out.push({ label: '−5%', value: Math.round(ref * 0.95 * 1e4) / 1e4 });
      out.push({ label: '−10%', value: Math.round(ref * 0.9 * 1e4) / 1e4 });
    }
    return out;
  }

  applyMeQuickFill(v: number): void {
    this.setEditParam(this.meAmountKey(), String(v));
  }

  private meEscrowAsked = false;


  /** Shown beside the amount: where the money comes from, or goes to. */

    /** Set once the protocol logo actually renders. The initial shows only
   *  while it hasn't — a plate behind a transparent icon is a black square. */
  readonly protoIconLoaded = signal(false);

  readonly isMeNftPanel = computed(() => {
    const t = this.action.type;
    if (!t.startsWith('me_') || t.startsWith('me_mmm_')) return false;
    this.ensureMeNftDisplay();
    return !!this.meNftName() || !!this.meNftImage()
      || !!this.getEditParam('mintAddress') || !!this.getEditParam('tokenMint');
  });

  /**
   * The NFT's name and picture.
   *
   * Normally they ride in from the row that spawned the action. When someone
   * types the request instead ("buy this mint"), there is nothing but an
   * address — so the card looks it up rather than showing the user a base58
   * string and asking them to recognise it.
   */
  private readonly meFetchedNft = signal<{
    name?: string;
    image?: string;
    collectionName?: string;
    attributes?: Array<{ trait_type?: string; traitType?: string; value?: unknown }>;
    sellerFeeBasisPoints?: number;
    price?: number;
  } | null>(null);

  private meNftLookupDone = '';

  /**
   * Read an NFT, retrying the failures that clear on their own.
   *
   * Magic Eden throttles, and one refused request left a card reading "This
   * NFT" above a "media not available" square for a piece with perfectly good
   * art. When the action came from a tile the name rode in with the click and
   * hid this; when the model emits the action directly there is only a mint,
   * and this lookup is the only thing standing between the user and a base58
   * string.
   */
  private async lookupMeNft(mint: string): Promise<Record<string, unknown> | null> {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (attempt > 0) await new Promise(r => setTimeout(r, 400 << attempt));
      const d = await this.magicEden.read<Record<string, unknown>>('me_token', { mintAddress: mint });
      if (d) return d;
    }
    return null;
  }

  /**
   * Fill in which offer or listing a cancel is about, when there is only one.
   *
   * The service already does this when it builds the transaction, but the card
   * is drawn long before that: with no mint in the params it renders a required
   * "NFT mint address" box and asks the user to paste an address for the single
   * bid they have out. Resolve it here too, so the card shows the piece instead
   * of a form.
   */
  private async ensureMeCancelTarget(): Promise<void> {
    const t = this.action?.type ?? '';
    if (this.getEditParam('mintAddress') || this.getEditParam('tokenMint')) return;
    const wallet = this.walletService.publicKey()?.toString();
    if (!wallet) return;

    let source: string;
    let mintKey: string;
    if (/cancel_offer|buy_cancel/.test(t)) {
      source = 'me_wallet_offers_made';
      mintKey = 'tokenMint';
    } else if (/accept_offer|sell_now/.test(t)) {
      source = 'me_wallet_offers_received';
      mintKey = 'tokenMint';
    } else if (/cancel_listing|sell_cancel/.test(t)) {
      source = 'me_wallet_tokens';
      mintKey = 'mintAddress';
    } else {
      return;
    }

    const rows = await this.magicEden.read<Array<Record<string, unknown>>>(source, { wallet });
    if (!Array.isArray(rows)) return;
    const candidates = source === 'me_wallet_tokens'
      ? rows.filter(r => r['listStatus'] === 'listed')
      : rows;
    // More than one and the choice is real — the empty box is then the honest
    // state, and the offers list is where it gets answered.
    if (candidates.length !== 1) return;

    const only = candidates[0];
    const mint = only[mintKey] as string | undefined;
    if (!mint) return;
    this.setEditParam('mintAddress', mint);
    const price = only['price'];
    if (typeof price === 'number' && price > 0 && !this.getEditParam('price')) {
      this.setEditParam('price', String(price));
    }
    this.ensureMeNftDisplay();
  }

  private ensureMeNftDisplay(): void {
    const mint = this.getEditParam('mintAddress') || this.getEditParam('tokenMint')
      || this.getEditParam('assetMint');
    if (!mint || this.meNftLookupDone === mint) return;
    // Don't skip just because a name and picture came in with the click. The
    // tile passes those two and nothing else, so bailing here meant the panel
    // never learned the traits on the one path people actually take — clicking
    // List or Buy on a tile they chose for its traits.
    this.meNftLookupDone = mint;
    void this.lookupMeNft(mint)
      .then(d => {
        // Nothing resolved after retrying — release the guard so a later
        // trigger can try again rather than leaving the card on "This NFT"
        // over an empty square for the rest of its life.
        if (!d) { this.meNftLookupDone = ''; return; }
        // `me_token` answers either flat or wrapped in `token` depending on
        // which upstream served it; read through both rather than betting.
        const src = (d['token'] as Record<string, unknown> | undefined) ?? d;
        this.meFetchedNft.set({
          name: src['name'] as string | undefined,
          image: src['image'] as string | undefined,
          collectionName: src['collectionName'] as string | undefined,
          attributes: src['attributes'] as Array<{ trait_type?: string; value?: unknown }> | undefined,
          sellerFeeBasisPoints: src['sellerFeeBasisPoints'] as number | undefined,
          price: src['price'] as number | undefined,
        });
      });
  }

  /**
   * What the NFT actually is.
   *
   * The browse tile shows traits and the action card did not, so confirming a
   * purchase meant deciding on a name and a picture alone — after choosing the
   * item precisely because of its traits. Same rows, same order, same card.
   */
  meNftTraits(): Array<{ label: string; value: string }> {
    const raw = this.meFetchedNft()?.attributes ?? [];
    return raw
      .map(a => ({
        label: String(a.trait_type ?? a.traitType ?? ''),
        value: String(a.value ?? ''),
      }))
      .filter(a => a.label && a.value)
      .slice(0, 6);
  }

  meNftName(): string {
    return this.getEditParam('nftName')
      || this.action.params?.['nftName']
      || this.meFetchedNft()?.name
      || '';
  }

  meNftImage(): string {
    return this.getEditParam('nftImage')
      || this.action.params?.['nftImage']
      || this.meFetchedNft()?.image
      || '';
  }

  /** The NFT's art, through the gateway's image cache at panel resolution.
   *  Issuer-hosted art is routinely hundreds of KB for a picture drawn at
   *  84px. */
  meArtSrc(): string | null {
    const url = this.meNftImage();
    if (!url) return null;
    if (!/^https:\/\//i.test(url)) return url;
    return `${environment.apiBase}/token-image?url=${encodeURIComponent(url)}&size=256`;
  }

  meCollectionName(): string {
    return this.getEditParam('collectionName')
      || this.action.params?.['collectionName']
      || this.meFetchedNft()?.collectionName
      || '';
  }

  /** What the action does, in the user's terms — the verb on the button and
   *  the sentence above the price both come from here. */
  meVerb(): string {
    const t = this.action.type;
    // Order matters, and "me_cancel_listing" contains "list": the withdrawal
    // has to be recognised BEFORE anything that matches on the word.
    if (/cancel_listing/.test(t)) return 'Listed at';
    if (/cancel_offer|buy_cancel/.test(t)) return 'Your offer';
    if (/cancel/.test(t)) return '';
    if (/change_price/.test(t)) return 'New price';
    // The bid, not the proceeds. What the seller keeps is the row below, after
    // the royalty and the fee come out of it.
    if (/accept_offer|sell_now/.test(t)) return 'Offer';
    if (/make_offer/.test(t)) return 'Your offer';
    if (/list|_sell$/.test(t)) return 'Ask';
    return 'You pay';
  }

  /** The asking price, when this action was spawned from a listed NFT. */
  meAskPrice(): number | null {
    const n = Number(this.getEditParam('askPrice') || this.action.params?.['askPrice']);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  /** The collection floor, as the other reference an offer is judged against. */
  meFloorPrice(): number | null {
    const n = Number(this.getEditParam('floorPrice') || this.action.params?.['floorPrice']);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  /** True while making an offer — the one action with real reference prices
   *  worth showing next to an empty box. */
  meShowsReference(): boolean {
    return /make_offer/.test(this.action.type) && (!!this.meAskPrice() || !!this.meFloorPrice());
  }

  /** What a re-price is moving away from. A card that shows only the number
   *  being typed cannot tell you whether you are raising or lowering. */
  meCurrentPrice(): number | null {
    if (!/change_price/.test(this.action.type)) return null;
    const n = Number(this.action.params?.['listPrice']);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  /** The number the card leads with. */
  meHeadlinePrice(): number | null {
    // `listPrice` is what a Remove-listing card is spawned with. It is named
    // apart from `price` on purpose: nothing downstream should be able to read
    // it as an amount to spend.
    const raw = this.getEditParam('newPrice') || this.getEditParam('price')
      || this.getEditParam('listPrice');
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return n;
    const live = this.meFetchedNft()?.price;
    return typeof live === 'number' && live > 0 ? live : null;
  }

  /** What Magic Eden shows for this listing, on the card that withdraws it. */
  meListedTotalSol(): number | null {
    const p = this.meHeadlinePrice();
    if (!p || !this.meRoyaltyKnown()) return null;
    const total = p * (1 + (this.meRoyaltyPct() ?? 0) + 0.02);
    return total > p ? total : null;
  }

  /** Magic Eden's cut, shown on the actions where the user is the one paying
   *  it — a seller who sees only the ask is reading the wrong number. */
  /** The collection's creator royalty, as a fraction. */
  meRoyaltyPct(): number | null {
    // Prefer what came with the click: the tile already knows the collection's
    // royalty, so the total can be stated immediately instead of after a round
    // trip. The lookup is the fallback for cards the model spawned.
    const carried = Number(this.getEditParam('royaltyBps') || this.action.params?.['royaltyBps']);
    if (Number.isFinite(carried) && carried > 0) return carried / 10_000;
    const bps = this.meFetchedNft()?.sellerFeeBasisPoints;
    return typeof bps === 'number' && bps > 0 ? bps / 10_000 : null;
  }

  meRoyaltySol(): number | null {
    const p = this.meHeadlinePrice();
    const r = this.meRoyaltyPct();
    return p && r ? p * r : null;
  }

  /** Withdrawing a listing: the card's job is to show which listing. */
  meIsCancelListing(): boolean {
    return /cancel_listing/.test(this.action.type);
  }

  meFeeSol(): number | null {
    const t = this.action.type;
    // Withdrawing a listing costs neither fee nor royalty — nothing is sold.
    // It reached this branch only because its name contains "list".
    if (this.meIsCancelListing()) return null;
    // Re-pricing a BID adds nothing on top: the bidder's number is the whole
    // number, and Magic Eden takes its cut from the seller when it is taken.
    // Only the sell side has a fee and a royalty stacked on the ask.
    if (/buy_change_price/.test(t)) return null;
    if (!/list|_sell$|accept_offer|sell_now|change_price|buy/.test(t)) return null;
    const p = this.meHeadlinePrice();
    // A total assembled from half its parts is a wrong number stated
    // confidently: without the collection's royalty this said 510 where
    // Magic Eden says 555. Wait for the lookup rather than publish a subtotal.
    if (!this.meRoyaltyKnown()) return null;
    return p ? p * 0.02 : null;
  }

  /** True once the NFT lookup has answered, so the royalty is a fact rather
   *  than an absence. */
  meRoyaltyKnown(): boolean {
    const carried = Number(this.getEditParam('royaltyBps') || this.action.params?.['royaltyBps']);
    return (Number.isFinite(carried) && carried > 0) || this.meFetchedNft() !== null;
  }

  /** True on the actions where the user is the one paying the total, not
   *  receiving the ask — a buyer sending 500 has 555 leave their wallet. */
  meIsBuySide(): boolean {
    return /buy/.test(this.action.type) && !/buy_change_price|buy_cancel/.test(this.action.type);
  }

  /**
   * Taking a bid runs the fees the other way.
   *
   * On a listing the royalty and the 2% are ADDED to the ask — the buyer pays
   * more and the seller keeps what they asked. On a bid the buyer has already
   * escrowed exactly their number, so the same two come OUT of it and the
   * seller keeps less. Same fees, opposite direction, and the card was showing
   * a seller a "buyer pays" figure for money nobody was going to send.
   */
  meIsProceedsSide(): boolean {
    return /accept_offer|sell_now/.test(this.action.type);
  }

  /** What actually lands in the seller's wallet when a bid is taken. */
  meProceedsSol(): number | null {
    const p = this.meHeadlinePrice();
    if (!p || !this.meRoyaltyKnown()) return null;
    const net = p - (this.meRoyaltySol() ?? 0) - (this.meFeeSol() ?? 0);
    return net > 0 ? net : null;
  }

  /** When a standing offer runs out, on the cards that act on one. */
  meOfferExpiry(): string | null {
    if (!/offer/.test(this.action?.type ?? '')) return null;
    const raw = Number(this.action.params?.['expiry']);
    if (!Number.isFinite(raw) || raw <= 0) return null;
    return new Date(raw * 1000).toLocaleString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  /**
   * What a buyer will be shown.
   *
   * Listing at 500 SOL put "555 SOL" on Magic Eden's grid, and the card had
   * said nothing about why: their marketplace fee and the collection's 9%
   * royalty are added on top of the ask. The seller's number and the buyer's
   * number are both real and they are not the same, so the card names both
   * rather than letting the marketplace deliver the surprise.
   */
  meBuyerPaysSol(): number | null {
    const p = this.meHeadlinePrice();
    if (!p) return null;
    const total = p + (this.meRoyaltySol() ?? 0) + (this.meFeeSol() ?? 0);
    return total > p ? total : null;
  }

  meNetSol(): number | null {
    const p = this.meHeadlinePrice();
    return p ? p : null;
  }

    readonly isSwapPanel = computed(() => {
    const t = this.action?.type ?? '';
    return t === 'swap' || t === 'raydium_swap'
      || t === 'meteora_swap' || t === 'meteora_dammv1_swap'
      || t === 'meteora_dammv2_swap' || t === 'orca_swap';
  });

  /**
   * Pool-scoped swaps name only the input side — the pool decides the output.
   * The panel needs both to render, so resolve the counter token from the
   * pool's pair when the action didn't carry it.
   */
  private async maybeResolveSwapCounterToken(): Promise<void> {
    const t = this.action?.type ?? '';
    if (t !== 'meteora_dammv2_swap' && t !== 'orca_swap') return;
    const p = this.editParams();
    if (p['outputMint']) return;
    const pool = p['pool'] || p['poolId'] || p['whirlpool'];
    const input = p['inputMint'];
    if (!pool || !input) return;

    // The pair may already be on the action if it came from a pool row.
    const known = [p['tokenA'], p['tokenB']].filter(Boolean);
    if (known.length === 2) {
      this.setEditParam('outputMint', known[0] === input ? known[1]! : known[0]!);
      return;
    }
    try {
      const isOrca = t === 'orca_swap';
      const resp = await firstValueFrom(
        this.apiService.post<any>('/actions/build', isOrca
          ? { type: 'orca_get_pools', params: { addresses: pool, size: 1 } }
          : { type: 'meteora_dammv2_get_pool', params: { address: pool } },
        ).pipe(timeout(15_000)),
      );
      const row = isOrca ? resp?.data?.data?.[0] : resp?.data;
      const a = isOrca ? row?.tokenA?.address : row?.token_x?.address;
      const b = isOrca ? row?.tokenB?.address : row?.token_y?.address;
      if (!a || !b) return;
      this.editParams.update(ep => ({
        ...ep,
        tokenA: a,
        tokenB: b,
        outputMint: a === input ? b : a,
      }));
    } catch {
      // Panel still renders; the receive side simply stays unresolved.
    }
  }

  /** What a partial withdrawal actually returns, at the chosen percentage. */
  readonly meteoraWithdrawReturns = computed<{ a: number; b: number } | null>(() => {
    const pd = this.meteoraPositionDetail();
    if (!pd) return null;
    const share = this.meteoraReducePct() / 100;
    if (!(share > 0)) return { a: 0, b: 0 };
    return { a: pd.amountA * share, b: pd.amountB * share };
  });

  /**
   * Share-to-remove for a Meteora withdrawal. The three reduce actions submit
   * it differently — DLMM in basis points (10000 = 100%), the DAMM variants as
   * an LP amount — so the panel drives a percentage and converts on write.
   */
  meteoraReduceKey(): 'bpsToRemove' | 'lpAmount' {
    // Orca takes a raw liquidity figure, but the panel must not ask for one —
    // the percentage is converted at submit from the position's liquidity.
    if (this.action.type === 'orca_decrease_position') return 'bpsToRemove';
    // DLMM and DAMM v2 both submit a share in basis points; only the DAMM v1
    // withdrawal is denominated in LP tokens.
    return this.action.type === 'meteora_remove_liquidity'
        || this.action.type === 'meteora_dammv2_remove_liquidity'
      ? 'bpsToRemove'
      : 'lpAmount';
  }

  readonly meteoraReducePct = computed<number>(() => {
    const p = this.editParams();
    if (this.meteoraReduceKey() === 'bpsToRemove') {
      const bps = parseFloat(p['bpsToRemove'] ?? '');
      return Number.isFinite(bps) && bps > 0 ? Math.min(100, bps / 100) : 100;
    }
    const raw = (p['lpAmount'] ?? '').trim().toLowerCase();
    if (!raw || raw === 'all' || raw === 'max') return 100;
    const total = parseFloat(p['positionLpAmount'] ?? '');
    const asked = parseFloat(raw);
    if (Number.isFinite(total) && total > 0 && Number.isFinite(asked)) {
      return Math.min(100, (asked / total) * 100);
    }
    return 100;
  });

  setMeteoraReducePct(pct: number): void {
    const key = this.meteoraReduceKey();
    if (key === 'bpsToRemove') { this.setEditParam(key, String(Math.round(pct * 100))); return; }
    if (pct >= 100) { this.setEditParam(key, 'all'); return; }
    const total = parseFloat(this.editParams()['positionLpAmount'] ?? '');
    this.setEditParam(key, Number.isFinite(total) && total > 0
      ? formatDlmmAmount(total * pct / 100)
      : String(pct));
  }

  /**
   * SOL held back for opening a DLMM position: the position account plus any
   * bin arrays the range touches that aren't initialised yet. Meteora quotes
   * ~0.057 SOL for a 69-bin position and refunds it on close; 0.06 covers
   * that with a little room for fees. Spending it leaves nothing for rent and
   * the deposit fails at simulation with an unhelpful "insufficient" error.
   */
  readonly METEORA_POSITION_RENT = 0.06;

  /**
   * What closing actually returns: the position account is a fixed 8120 bytes,
   * so its rent is deterministic — 0.057406 SOL, confirmed on-chain. Worth
   * stating exactly, because it is often larger than the position itself.
   */
  readonly METEORA_POSITION_RENT_REFUND = 0.057406;

  /**
   * What closing this position actually returns in rent. Prefer the figure the
   * backend read off the accounts — DLMM's 8120-byte position is 0.0574 SOL
   * while a DAMM v2 position plus its NFT account is around 0.0058, so a
   * shared constant overstated one of them by a factor of ten. The constant
   * remains only as a fallback for a card with no detail.
   */
  /** Net SOL the wallet will show for a close, measured by simulating it.
   *  Null until the preview lands, so the panel falls back to amounts + rent
   *  rather than rendering a blank. */
  /** True once a lookup finds the position is no longer open. */
  readonly meteoraPositionGone = signal(false);

  readonly meteoraCloseNetSol = computed<number | null>(() => {
    const v = parseFloat(this.editParams()['closeNetSol'] ?? '');
    return Number.isFinite(v) ? v : null;
  });

  readonly meteoraRentRefund = computed(() => {
    const v = parseFloat(this.editParams()['positionRentSol'] ?? '');
    return Number.isFinite(v) && v > 0 ? v : this.METEORA_POSITION_RENT_REFUND;
  });

  /** Close = withdraw + claim + close the account. Distinct from a plain
   *  withdrawal, which leaves the account (and its rent) in place. */
  readonly isMeteoraClose = computed(() =>
    this.action?.type === 'meteora_close_position'
    || this.action?.type === 'meteora_dammv2_close_position'
    || this.action?.type === 'orca_close_position');

  /**
   * A claim with nothing to claim. The card already says so; leaving the CTA
   * live invites the user to pay a fee for a transaction that moves nothing.
   * Only blocks when the position's fees were actually read — an unknown
   * amount is not the same as zero, and guessing would block a legitimate
   * claim issued from chat.
   */
  readonly nothingToClaim = computed(() => {
    if (!this.isClaimAction()) return false;
    const pd = this.meteoraPositionDetail();
    return pd !== null && !pd.hasFees;
  });

  /** True when this action opens a NEW DLMM position (rent applies). */
  private meteoraOpensPosition(): boolean {
    const t = this.action.type;
    return this.isMeteoraDlmm() && (t === 'meteora_open_position' || t === 'meteora_add_liquidity');
  }

  /**
   * Pre-Confirm guard for a Meteora deposit: each side against its balance,
   * and native SOL against the position rent on top of whatever is deposited.
   */
  readonly meteoraInsufficient = computed<string | null>(() => {
    if (!this.isMeteoraDual()) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? 'A';
    const symB = p['tokenBSymbol'] ?? 'B';
    const amtA = parseFloat(p['amountA'] ?? '');
    const amtB = parseFloat(p['amountB'] ?? '');
    const balA = this.inputBalance();
    const balB = this.secondaryBalance();
    const isSol = (x: string) => { const u = (x ?? '').toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    const rent = this.meteoraOpensPosition() ? this.METEORA_POSITION_RENT : 0.01;
    const EPS = 1e-9;

    if (Number.isFinite(amtA) && amtA > 0 && balA !== null &&
        amtA > balA - (isSol(symA) ? rent : 0) + EPS) {
      return isSol(symA) ? `Keep ~${rent} SOL for position rent` : `Not enough ${symA}`;
    }
    if (Number.isFinite(amtB) && amtB > 0 && balB !== null &&
        amtB > balB - (isSol(symB) ? rent : 0) + EPS) {
      return isSol(symB) ? `Keep ~${rent} SOL for position rent` : `Not enough ${symB}`;
    }
    return null;
  });

  /** The single amount field each one-sided Meteora action actually submits. */
  meteoraSingleKey(): string {
    switch (this.action.type) {
      case 'meteora_vault_withdraw': return 'unmintAmount';
      default: return 'amount';
    }
  }

  /** Token whose balance the one-sided panel shows. */
  readonly meteoraSingleToken = computed(() => {
    const p = this.editParams();
    const raw = p['tokenMint'] || p['tokenA'] || p['tokenASymbol'] || '';
    return this.resolveTokenDisplay(raw);
  });

  readonly isRaydiumOpenPosition = computed(
    // Orca Whirlpools are concentrated liquidity too: the same price band
    // decides the deposit ratio, so they take the same range panel rather
    // than a bare pair of amount boxes with no band at all.
    () => this.action.type === 'raydium_open_position'
       || this.action.type === 'orca_open_position',
  );

  /**
   * Raydium position withdrawal (CLMM close / standard LP remove). The generic
   * field list renders these as a bare "POSITION ID: <base58>" text box, which
   * tells the user nothing about what they're about to close. When the card was
   * spawned from the positions list it carries display context (pair, symbols,
   * logos, amount) — the template uses it to render a real position summary.
   */
  /** Full CLMM close — nothing to choose, so it keeps the summary-only panel.
   *  (Standard-LP withdrawals are partial-capable and use the reduce panel.) */
  readonly isRaydiumWithdraw = computed(() => this.action.type === 'raydium_close_position');

  /** Adding liquidity to an EXISTING CLMM position. */
  readonly isRaydiumIncrease = computed(() => this.action.type === 'raydium_increase_position');

  /** Partial withdrawal — CLMM range OR standard-LP position. Both let the
   *  user take out a share, so they share one panel. */
  readonly isRaydiumDecrease = computed(
    () => this.action.type === 'raydium_decrease_position' || this.action.type === 'raydium_remove_liquidity',
  );

  /** LP withdrawals scale `lpAmount` (UI units); CLMM scales `liquidity`. */
  private decreaseAmountKey(): 'liquidity' | 'lpAmount' {
    return this.action.type === 'raydium_remove_liquidity' ? 'lpAmount' : 'liquidity';
  }
  private decreaseTotalKey(): 'positionLiquidity' | 'positionLpAmount' {
    return this.action.type === 'raydium_remove_liquidity' ? 'positionLpAmount' : 'positionLiquidity';
  }

  readonly CLMM_DECREASE_PRESETS: ReadonlyArray<number> = [25, 50, 75, 100];

  /**
   * How much of the position the user is withdrawing, as a percentage. The
   * builder accepts "50%" / "all" / a raw liquidity value; we keep the param
   * in percent form because a raw liquidity constant ("22160774") tells the
   * user nothing about how much they're taking out.
   */
  readonly clmmDecreasePct = computed<number>(() => {
    const p = this.editParams();
    const raw = (p[this.decreaseAmountKey()] ?? '').trim().toLowerCase();
    const total = parseFloat(p[this.decreaseTotalKey()] ?? '');
    if (!raw) return 100; // no amount given → withdraw everything
    if (raw === 'all' || raw === 'max') return 100;
    const pct = raw.match(/^(\d+(?:\.\d+)?)\s*%$/);
    if (pct) return Math.min(100, Math.max(0, parseFloat(pct[1])));
    // A concrete amount → express it against the position's total.
    const asked = parseFloat(raw);
    if (Number.isFinite(total) && total > 0 && Number.isFinite(asked) && asked > 0) {
      return Math.min(100, (asked / total) * 100);
    }
    return 100;
  });

  setClmmDecreasePct(pct: number): void {
    const key = this.decreaseAmountKey();
    if (pct >= 100) { this.setEditParam(key, 'all'); return; }
    // LP removal takes a UI token amount, not a percentage string.
    if (key === 'lpAmount') {
      const total = parseFloat(this.editParams()['positionLpAmount'] ?? '');
      if (Number.isFinite(total) && total > 0) {
        this.setEditParam(key, formatDlmmAmount(total * pct / 100));
        return;
      }
    }
    this.setEditParam(key, `${pct}%`);
  }

  /** Withdrawal amount for one side, derived from the chosen share. */
  clmmDecreaseRowValue(side: 'A' | 'B'): string {
    const p = this.editParams();
    const pos = parseFloat((side === 'A' ? p['amountA'] : p['amountB']) ?? '');
    if (!Number.isFinite(pos)) return '';
    const v = pos * this.clmmDecreasePct() / 100;
    return v > 0 ? formatDlmmAmount(v) : '';
  }

  /**
   * User typed an exact token amount to withdraw → convert it to a share of
   * the position. Sent as RAW liquidity units when we know the position's
   * total, because the builder rounds percentages to whole numbers (a 1%
   * step is far coarser than the amount the user just typed).
   */
  onClmmDecreaseInput(side: 'A' | 'B', value: string): void {
    const p = this.editParams();
    const key = this.decreaseAmountKey();
    const pos = parseFloat((side === 'A' ? p['amountA'] : p['amountB']) ?? '');
    const asked = parseFloat(this.normalizeDecimal(value));
    if (!Number.isFinite(pos) || pos <= 0) return;
    if (!Number.isFinite(asked) || asked <= 0) {
      this.setEditParam(key, key === 'lpAmount' ? '0' : '0%');
      return;
    }
    const fraction = Math.min(1, asked / pos);
    const total = parseFloat(p[this.decreaseTotalKey()] ?? '');
    if (fraction >= 0.9999) { this.setEditParam(key, 'all'); return; }
    if (Number.isFinite(total) && total > 0) {
      // LP: a UI token amount. CLMM: raw integer liquidity units — the builder
      // rounds percentages to whole numbers, which is coarser than a typed amount.
      this.setEditParam(key, key === 'lpAmount'
        ? formatDlmmAmount(total * fraction)
        : String(Math.max(1, Math.floor(total * fraction))));
      return;
    }
    this.setEditParam(key, `${(fraction * 100).toFixed(2)}%`);
  }

  /** Position summary + what this withdrawal returns. */
  readonly raydiumDecreaseView = computed<{
    pair: string; symA: string; symB: string;
    logoA: string | null; logoB: string | null;
    outA: string; outB: string; currentA: string; currentB: string;
    positionId: string; closes: boolean; kind: string;
  } | null>(() => {
    if (!this.isRaydiumDecrease()) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? '';
    const symB = p['tokenBSymbol'] ?? '';
    const pair = p['pair'] || (symA && symB ? `${symA}/${symB}` : '');
    if (!pair) return null;
    const pct = this.clmmDecreasePct();
    const posA = parseFloat(p['amountA'] ?? '');
    const posB = parseFloat(p['amountB'] ?? '');
    const fmt = (v: number): string =>
      Number.isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : '—';
    return {
      pair, symA, symB,
      logoA: p['tokenALogo'] || this.resolveTokenDisplay(symA).logoURI || null,
      logoB: p['tokenBLogo'] || this.resolveTokenDisplay(symB).logoURI || null,
      outA: fmt(posA * pct / 100),
      outB: fmt(posB * pct / 100),
      currentA: fmt(posA),
      currentB: fmt(posB),
      positionId: p['positionId'] ?? '',
      closes: pct >= 100,
      kind: this.action.type === 'raydium_remove_liquidity' ? 'LP' : 'CLMM',
    };
  });

  /** Standard AMM (v4 / CPMM) add-liquidity — full range, no price band. */
  readonly isRaydiumAddLiquidity = computed(() => this.action.type === 'raydium_add_liquidity');

  /** Header context for the AMM deposit panel; null until the pool resolves. */
  readonly raydiumAmmView = computed<{
    pair: string; symA: string; symB: string;
    logoA: string | null; logoB: string | null; poolId: string; kind: string;
  } | null>(() => {
    if (!this.isRaydiumAddLiquidity()) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] || this.resolveTokenDisplay(p['tokenA'] ?? '').symbol || '';
    const symB = p['tokenBSymbol'] || this.resolveTokenDisplay(p['tokenB'] ?? '').symbol || '';
    if (!symA || !symB) return null;
    // "Standard" spans two different programs (newer CPMM vs legacy AMM v4);
    // name the one the deposit actually lands in.
    const PROGRAMS: Record<string, string> = {
      CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C: 'CPMM',
      '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'AMM V4',
    };
    return {
      pair: p['pair'] || `${symA}/${symB}`,
      symA, symB,
      logoA: p['tokenALogo'] || this.resolveTokenDisplay(p['tokenA'] ?? symA).logoURI || null,
      logoB: p['tokenBLogo'] || this.resolveTokenDisplay(p['tokenB'] ?? symB).logoURI || null,
      poolId: p['poolId'] ?? '',
      kind: PROGRAMS[p['programId'] ?? ''] ?? 'STANDARD',
    };
  });

  /** Max on one AMM deposit side, capped so BOTH sides stay within balance
   *  at the pool's constant-product ratio. */
  setMaxAmm(side: 'A' | 'B'): void {
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? '';
    const symB = p['tokenBSymbol'] ?? '';
    const isSol = (s: string) => { const u = (s ?? '').toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    // Hold back whatever the guard demands, or Max would always trip it.
    const RENT = this.meteoraOpensPosition() ? this.METEORA_POSITION_RENT : 0.02;
    const SAFETY = 0.99;
    const availA = Math.max(0, (this.inputBalance() ?? 0) - (isSol(symA) ? RENT : 0)) * SAFETY;
    const availB = Math.max(0, (this.secondaryBalance() ?? 0) - (isSol(symB) ? RENT : 0)) * SAFETY;
    const amm = this.ammRatio(); // yPerX = B per A
    const yPerX = amm?.yPerX;

    if (!yPerX || !Number.isFinite(yPerX) || yPerX <= 0) {
      this.setMaxAmount(side === 'A' ? 'amountA' : 'amountB');
      return;
    }
    if (side === 'B') {
      const maxB = Math.min(availB, availA * yPerX);
      if (!(maxB > 0)) return;
      this.setEditParam('amountB', formatDlmmAmount(maxB));
      this.dlmmLastEdited.set('B');
      return;
    }
    const maxA = Math.min(availA, availB / yPerX);
    if (!(maxA > 0)) return;
    this.setEditParam('amountA', formatDlmmAmount(maxA));
    this.dlmmLastEdited.set('A');
  }

  /** Pre-Confirm balance guard for the AMM deposit (both sides). */
  readonly ammInsufficient = computed<string | null>(() => {
    if (!this.isRaydiumAddLiquidity()) return null;
    const p = this.editParams();
    const amtA = parseFloat(p['amountA'] ?? '');
    const amtB = parseFloat(p['amountB'] ?? '');
    const symA = p['tokenASymbol'] ?? 'A';
    const symB = p['tokenBSymbol'] ?? 'B';
    const isSol = (s: string) => { const u = (s ?? '').toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    const RENT = 0.02;
    const EPS = 1e-9;
    const balA = this.inputBalance();
    const balB = this.secondaryBalance();
    if (Number.isFinite(amtA) && amtA > 0 && balA !== null &&
        amtA > balA - (isSol(symA) ? RENT : 0) + EPS) return `Not enough ${symA}`;
    if (Number.isFinite(amtB) && amtB > 0 && balB !== null &&
        amtB > balB - (isSol(symB) ? RENT : 0) + EPS) return `Not enough ${symB}`;
    return null;
  });

  /** Which side of the pair the user typed into ('A' | 'B'). The transaction
   *  carries ONE side (inputMint + inputAmount); the other is derived. */
  readonly clmmIncreaseSide = computed<'A' | 'B'>(() => {
    const p = this.editParams();
    const mint = this.resolveToMint(p['inputMint'] ?? '');
    const mintB = this.resolveToMint(p['tokenB'] ?? p['tokenBSymbol'] ?? '');
    return mint && mintB && mint === mintB ? 'B' : 'A';
  });

  /** paired-per-typed ratio from the position's own composition, or null. */
  private clmmIncreaseRatio(from: 'A' | 'B'): number | null {
    const p = this.editParams();
    const posA = parseFloat(p['amountA'] ?? '');
    const posB = parseFloat(p['amountB'] ?? '');
    if (!Number.isFinite(posA) || !Number.isFinite(posB)) return null;
    if (from === 'A') return posA > 0 ? posB / posA : 0;
    return posB > 0 ? posA / posB : 0;
  }

  /**
   * Display value for one of the two increase inputs. The side the user typed
   * shows their raw text; the other shows the amount derived at the position's
   * ratio — so both tokens are visible and either box can be edited, exactly
   * like the open-position form.
   */
  clmmIncreaseRowValue(side: 'A' | 'B'): string {
    const p = this.editParams();
    const typed = this.clmmIncreaseSide();
    const raw = p['inputAmount'] ?? '';
    if (side === typed) return raw;
    const entered = parseFloat(raw);
    if (!Number.isFinite(entered) || entered <= 0) return '';
    const ratio = this.clmmIncreaseRatio(typed);
    if (ratio === null) return '';
    return formatDlmmAmount(entered * ratio);
  }

  /** User typed into one of the two boxes → that side becomes the canonical
   *  input and the other becomes derived. */
  onClmmIncreaseInput(side: 'A' | 'B', value: string): void {
    const p = this.editParams();
    const mint = side === 'A'
      ? this.resolveToMint(p['tokenA'] ?? p['tokenASymbol'] ?? '')
      : this.resolveToMint(p['tokenB'] ?? p['tokenBSymbol'] ?? '');
    this.editParams.update(ep => ({
      ...ep,
      ...(mint ? { inputMint: mint } : {}),
      inputAmount: this.normalizeDecimal(value),
    }));
  }


  /**
   * Pre-Confirm balance guard for an increase: checks the typed side AND the
   * derived paired side against their balances, so the shortfall surfaces
   * before the user signs rather than as a failed simulation.
   */
  readonly clmmIncreaseInsufficient = computed<string | null>(() => {
    if (!this.isRaydiumIncrease()) return null;
    const p = this.editParams();
    const entered = parseFloat(p['inputAmount'] ?? '');
    if (!Number.isFinite(entered) || entered <= 0) return null;
    const symA = p['tokenASymbol'] ?? 'A';
    const symB = p['tokenBSymbol'] ?? 'B';
    // Row A always reads the tokenA balance, row B the tokenB balance.
    const amtA = parseFloat(this.clmmIncreaseRowValue('A'));
    const amtB = parseFloat(this.clmmIncreaseRowValue('B'));
    const isSol = (s: string) => { const u = (s ?? '').toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    const RENT = 0.02; // position update + ATA rent headroom
    const EPS = 1e-9;
    const balA = this.inputBalance();
    const balB = this.secondaryBalance();
    if (Number.isFinite(amtA) && amtA > 0 && balA !== null &&
        amtA > balA - (isSol(symA) ? RENT : 0) + EPS) return `Not enough ${symA}`;
    if (Number.isFinite(amtB) && amtB > 0 && balB !== null &&
        amtB > balB - (isSol(symB) ? RENT : 0) + EPS) return `Not enough ${symB}`;
    return null;
  });

  /** Max on one increase row, capped so BOTH sides stay within balance. */
  setMaxClmmIncrease(side: 'A' | 'B'): void {
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? '';
    const symB = p['tokenBSymbol'] ?? '';
    const isSol = (s: string) => { const u = (s ?? '').toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    const RENT = 0.02;
    const SAFETY = 0.99; // headroom so rounding doesn't trip the on-chain check
    const availA = Math.max(0, (this.inputBalance() ?? 0) - (isSol(symA) ? RENT : 0)) * SAFETY;
    const availB = Math.max(0, (this.secondaryBalance() ?? 0) - (isSol(symB) ? RENT : 0)) * SAFETY;

    const ratio = this.clmmIncreaseRatio(side); // paired-per-typed
    const ownAvail = side === 'A' ? availA : availB;
    const otherAvail = side === 'A' ? availB : availA;
    const otherKnown = (side === 'A' ? this.secondaryBalance() : this.inputBalance()) !== null;

    let max = ownAvail;
    if (ratio !== null && ratio > 0 && otherKnown) max = Math.min(max, otherAvail / ratio);
    if (!(max > 0)) return;
    this.onClmmIncreaseInput(side, formatDlmmAmount(max));
  }

  /** Summary for the increase panel: the position being topped up. */
  readonly raydiumIncreaseView = computed<{
    pair: string; symA: string; symB: string;
    logoA: string | null; logoB: string | null;
    current: string | null; positionId: string;
  } | null>(() => {
    if (!this.isRaydiumIncrease()) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? '';
    const symB = p['tokenBSymbol'] ?? '';
    const pair = p['pair'] || (symA && symB ? `${symA}/${symB}` : '');
    if (!pair) return null;
    const fmt = (v: string | undefined): string | null => {
      const n = parseFloat(v ?? '');
      return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 6 }) : null;
    };
    const a = fmt(p['amountA']);
    const b = fmt(p['amountB']);
    return {
      pair, symA, symB,
      logoA: p['tokenALogo'] || this.resolveTokenDisplay(symA).logoURI || null,
      logoB: p['tokenBLogo'] || this.resolveTokenDisplay(symB).logoURI || null,
      current: a !== null || b !== null ? `${a ?? '0'} ${symA} + ${b ?? '0'} ${symB}` : null,
      positionId: p['positionId'] ?? '',
    };
  });

  /** Position summary for the withdraw panel; null when the card wasn't
   *  spawned from the positions list (no pair context) — then the generic
   *  field list still renders so the action stays usable. */
  readonly raydiumWithdrawView = computed<{
    pair: string; kind: string; amount: string; amountLabel: string;
    logoA: string | null; logoB: string | null; symA: string; symB: string;
    ref: string;
  } | null>(() => {
    if (!this.isRaydiumWithdraw()) return null;
    const p = this.editParams();
    const symA = p['tokenASymbol'] ?? '';
    const symB = p['tokenBSymbol'] ?? '';
    const pair = p['pair'] || (symA && symB ? `${symA}/${symB}` : '');
    if (!pair) return null;
    const isLp = this.action.type === 'raydium_remove_liquidity';

    // Prefer the real token amounts ("0.0123 SOL + 4.56 USDC"). Raw CLMM
    // liquidity is an internal constant and means nothing to the user — it's
    // only the last-resort fallback when the amounts couldn't be derived.
    const fmt = (v: string | undefined): string | null => {
      const n = parseFloat(v ?? '');
      if (!Number.isFinite(n)) return null;
      return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
    };
    const amtA = fmt(p['amountA']);
    const amtB = fmt(p['amountB']);
    let amount: string;
    let amountLabel: string;
    if (!isLp && (amtA !== null || amtB !== null)) {
      amount = `${amtA ?? '0'} ${symA} + ${amtB ?? '0'} ${symB}`;
      amountLabel = 'You receive (plus earned fees)';
    } else if (isLp) {
      amount = fmt(p['lpAmount']) ?? '—';
      amountLabel = 'LP tokens to burn';
    } else {
      amount = p['positionLiquidity'] || p['liquidity'] || '—';
      amountLabel = 'Liquidity to withdraw';
    }

    return {
      pair,
      kind: isLp ? 'LP' : 'CLMM',
      amount,
      amountLabel,
      logoA: p['tokenALogo'] || this.resolveTokenDisplay(symA).logoURI || null,
      logoB: p['tokenBLogo'] || this.resolveTokenDisplay(symB).logoURI || null,
      symA, symB,
      ref: isLp ? (p['poolId'] ?? '') : (p['positionId'] ?? ''),
    };
  });

  /** "1 OSRUB ≈ 0.010002 USDT" line shown above the inputs. Derived purely
   *  from `currentPrice` carried in editParams, so it survives draft restore. */
  readonly clmmCurrentPriceDisplay = computed(() => {
    const p = this.editParams();
    const cur = parseFloat(p['currentPrice'] ?? '');
    if (!(cur > 0)) return null;
    return {
      symA: p['tokenASymbol'] || 'A',
      symB: p['tokenBSymbol'] || 'B',
      price: cur,
    };
  });

  /** True when the user's chosen range straddles the current pool price.
   *  Drives the highlight on the matching range-preset chip. */
  readonly clmmActiveRangePct = computed<number | null>(() => {
    const p = this.editParams();
    const cur = parseFloat(p['currentPrice'] ?? '');
    const lo  = parseFloat(p['minPrice'] ?? '');
    const hi  = parseFloat(p['maxPrice'] ?? '');
    if (!(cur > 0) || !(lo > 0) || !(hi > 0)) return null;
    const loPct = (cur - lo) / cur;
    const hiPct = (hi - cur) / cur;
    if (Math.abs(loPct - hiPct) > 0.001) return null; // asymmetric → no preset match
    const pct = Math.round(loPct * 1000) / 10; // %, one decimal
    return pct;
  });

  /** Preset buttons rendered under the min/max inputs. `Full` opens the
   *  widest practical range — Raydium itself caps at ±100x, but for a UI
   *  hint a 100x window is "effectively full" without breaking the math. */
  readonly CLMM_RANGE_PRESETS: ReadonlyArray<{ label: string; pct: number }> = [
    { label: '±0.5%', pct: 0.5 },
    { label: '±1%',   pct: 1   },
    { label: '±5%',   pct: 5   },
    { label: '±10%',  pct: 10  },
    { label: '±20%',  pct: 20  },
    { label: 'Full',  pct: 9900 }, // ~ ±100x, treated as "full range" preset
  ];

  /** Apply a ±N% range preset around the current price. */
  /**
   * An Orca position opened from a pool row arrives with the pool's price but
   * no band. Without one the ratio engine returns nothing, so typing into one
   * amount left the other empty under a line promising it would fill.
   *
   * Seed +/-10%: wide enough to hold through ordinary movement, narrow enough
   * to be worth concentrating. The presets are right there to change it.
   */
  private maybeSeedOrcaRange(): void {
    if (this.action?.type !== 'orca_open_position') return;
    const p = this.editParams();
    if (p['minPrice'] || p['maxPrice']) return;   // caller supplied a band
    if (!(parseFloat(p['currentPrice'] ?? '') > 0)) return;
    this.applyClmmRangePreset(10);
  }

  applyClmmRangePreset(pct: number): void {
    const p = this.editParams();
    const cur = parseFloat(p['currentPrice'] ?? '');
    if (!(cur > 0)) return;
    const factor = pct / 100;
    const minP = cur * (1 - factor);
    const maxP = cur * (1 + factor);
    // toPrecision keeps small-price tokens (OSRUB ≈ 0.01) readable, large-price
    // tokens (BTC-style) compact. 6 sig figs covers both extremes.
    this.editParams.update(ep => ({
      ...ep,
      minPrice: Math.max(minP, 1e-12).toPrecision(6),
      maxPrice: maxP.toPrecision(6),
    }));
    // Trigger a recompute on whichever side the user last edited.
    // If they haven't edited yet, default to A.
    if (!this.dlmmLastEdited()) this.dlmmLastEdited.set('A');
  }

  /**
   * Called from approve() when the card is in the error state. For
   * raydium_open_position on a stable-stable pair with a wide range, the
   * on-chain failure mode is "tick range outside observation window".
   * Auto-narrowing to ±1% gives Retry a real chance of succeeding without
   * forcing the user to click the preset themselves.
   */
  private maybeAutoTightenStableRange(): void {
    if (this.action?.type !== 'raydium_open_position') return;
    const p = this.editParams();
    // Stable check is driven by TokenRegistry (Jupiter tags + symbol/name
    // heuristics) so this catches any future USD-stable without a code change.
    const symA = p['tokenASymbol'] ?? p['tokenA'] ?? '';
    const symB = p['tokenBSymbol'] ?? p['tokenB'] ?? '';
    if (!this.tokenRegistry.isStable(symA) || !this.tokenRegistry.isStable(symB)) return;
    const cur = parseFloat(p['currentPrice'] ?? '');
    const lo  = parseFloat(p['minPrice'] ?? '');
    const hi  = parseFloat(p['maxPrice'] ?? '');
    if (!(cur > 0 && lo > 0 && hi > 0)) return;
    const spreadPct = Math.max(((cur - lo) / cur), ((hi - cur) / cur)) * 100;
    if (spreadPct <= 5) return; // already tight enough
    this.applyClmmRangePreset(1);
  }

  /**
   * DAMM v2's deposit ratio, straight from the SDK's own quote for this pool.
   * A constant-product pool with bounds relates the two sides linearly, so a
   * single number fills the second field as the user types — no build
   * round-trip per keystroke, and correct for concentrated pools where the
   * reserve ratio alone would not be.
   */
  private readonly dammV2Ratio = computed<{ yPerX: number; xPerY: number } | null>(() => {
    if (!this.isDammV2()) return null;
    const r = parseFloat(this.editParams()['depositRatio'] ?? '');
    if (!Number.isFinite(r) || r <= 0) return null;
    return { yPerX: r, xPerY: 1 / r };
  });

  private dlmmRatioEffect = effect(() => {
    // Three ratio sources: DLMM (range × strategy × active bin),
    // CLMM (Uniswap-v3 sqrt-price math), or AMM (constant-product reserves).
    // Priority: DLMM > CLMM > AMM (a single action only ever surfaces one).
    const dlmm = this.dlmmRatio();
    const clmm = this.clmmRatio();
    const amm = this.ammRatio();
    const params = this.editParams();
    const last = this.dlmmLastEdited();

    // DLMM-only: handle the single-sided range cases first — those exist
    // only for DLMM, not AMMs.
    if (dlmm) {
      if (this.dlmmSingleSidedY()) {
        if (params['amountA'] && params['amountA'] !== '0') {
          this.dlmmInternalWrite = true;
          this.editParams.update(ep => ({ ...ep, amountA: '0' }));
          this.dlmmInternalWrite = false;
        }
        return;
      }
      if (this.dlmmSingleSidedX()) {
        if (params['amountB'] && params['amountB'] !== '0') {
          this.dlmmInternalWrite = true;
          this.editParams.update(ep => ({ ...ep, amountB: '0' }));
          this.dlmmInternalWrite = false;
        }
        return;
      }
    }

    // CLMM single-sided (range entirely above or below current price): zero
    // out the unused side so the user can't accidentally deposit a token
    // the protocol won't accept at this range.
    if (clmm?.singleSided === 'A') {
      if (params['amountB'] && params['amountB'] !== '0') {
        this.dlmmInternalWrite = true;
        this.editParams.update(ep => ({ ...ep, amountB: '0' }));
        this.dlmmInternalWrite = false;
      }
      return;
    }
    if (clmm?.singleSided === 'B') {
      if (params['amountA'] && params['amountA'] !== '0') {
        this.dlmmInternalWrite = true;
        this.editParams.update(ep => ({ ...ep, amountA: '0' }));
        this.dlmmInternalWrite = false;
      }
      return;
    }

    // Pick effective ratio. DLMM yPerX is in raw units (needs decimal
    // scale baked in); CLMM and AMM yPerX are already human-unit.
    let humanYPerX: number | null = null;
    let humanXPerY: number | null = null;
    // DAMM v2 first: it has no bins and no tick math, so none of the sources
    // below can speak for it — without this the card promised "the other
    // follows" and then never filled it.
    const damm = this.dammV2Ratio();
    if (damm) {
      humanYPerX = damm.yPerX;
      humanXPerY = damm.xPerY;
    } else if (dlmm && dlmm.yPerX !== null && dlmm.xPerY !== null) {
      const scale = this.dlmmDecimalScale();
      humanYPerX = dlmm.yPerX * scale;
      humanXPerY = dlmm.xPerY / scale;
    } else if (clmm && Number.isFinite(clmm.yPerX) && Number.isFinite(clmm.xPerY)) {
      humanYPerX = clmm.yPerX;
      humanXPerY = clmm.xPerY;
    } else if (amm) {
      humanYPerX = amm.yPerX;
      humanXPerY = amm.xPerY;
    }
    if (humanYPerX === null || humanXPerY === null) return;

    // Dual-sided fill: derive the unedited side from the most recent edit.
    if (last === 'A') {
      const amtA = parseFloat(params['amountA'] ?? '');
      if (Number.isFinite(amtA) && amtA > 0) {
        const amtB = amtA * humanYPerX;
        const formatted = formatDlmmAmount(amtB);
        if (formatted !== (params['amountB'] ?? '')) {
          this.dlmmInternalWrite = true;
          this.editParams.update(ep => ({ ...ep, amountB: formatted }));
          this.dlmmInternalWrite = false;
        }
      }
    } else if (last === 'B') {
      const amtB = parseFloat(params['amountB'] ?? '');
      if (Number.isFinite(amtB) && amtB > 0) {
        const amtA = amtB * humanXPerY;
        const formatted = formatDlmmAmount(amtA);
        if (formatted !== (params['amountA'] ?? '')) {
          this.dlmmInternalWrite = true;
          this.editParams.update(ep => ({ ...ep, amountA: formatted }));
          this.dlmmInternalWrite = false;
        }
      }
    }
  }, { allowSignalWrites: true });

  /**
   * When a Meteora LP action arrives with `poolId` filled but `tokenA / tokenB`
   * empty (which is the common case — the LLM emits `pool` per the prompt
   * grammar but doesn't and shouldn't enumerate the underlying mints), fetch
   * the pool record and back-fill the token rows. Without this the form
   * shows two "?" placeholders and the user can't sanity-check what they're
   * about to deposit. Cached per pool so a draft restore doesn't re-fire.
   */
  private meteoraPoolTokensEffect = effect(() => {
    const t = this.action.type;
    const isMeteoraLp =
      t === 'meteora_open_position' ||
      t === 'meteora_add_liquidity' ||
      t === 'meteora_add_to_position';
    if (!isMeteoraLp) return;

    const params = this.editParams();
    const poolId = params['poolId'];
    if (!poolId || poolId.length < 32) return;
    if (params['tokenA'] && params['tokenB']) return;
    if (this._resolvedMeteoraPool === poolId) return;
    this._resolvedMeteoraPool = poolId;

    void this.meteoraService.getPool(poolId).then(pool => {
      if (!pool) return;
      // String '0' is truthy, so a stale `activeBinId: '0'` would survive
      // `ep[k] || fallback`. Treat '0' / '' / missing as unset and prefer
      // the freshly-fetched pool value when it's a real (non-zero) number.
      const pick = (cur: string | undefined, next: number | undefined): string => {
        const n = Number(cur);
        if (cur && Number.isFinite(n) && n !== 0) return cur;
        return next != null && next !== 0 ? String(next) : (cur ?? '');
      };
      this.editParams.update(ep => ({
        ...ep,
        tokenA: ep['tokenA'] || pool.tokenXMint,
        tokenB: ep['tokenB'] || pool.tokenYMint,
        // Carry the active bin id along so the DLMM ratio engine has a price
        // anchor when the LLM emitted minPrice/maxPrice instead of binIds.
        binStep: pick(ep['binStep'], pool.binStep),
        activeBinId: pick(ep['activeBinId'], pool.activeBinId),
        tokenADecimals: pick(ep['tokenADecimals'], pool.tokenXDecimals),
        tokenBDecimals: pick(ep['tokenBDecimals'], pool.tokenYDecimals),
      }));
    });
  }, { allowSignalWrites: true });

  /**
   * Mark which amount field the user just touched. Wired from the template
   * via `(input)` on amountA/amountB so the auto-balance effect knows
   * which side is the source of truth and which to compute.
   */
  noteAmountEdit(side: 'A' | 'B'): void {
    if (this.dlmmInternalWrite) return;
    this.dlmmLastEdited.set(side);
  }

  /**
   * True when the action's `amount` field represents an OUTPUT quantity
   * (Jupiter ExactOut "buy N TOKEN" semantics) instead of an input. Drives
   * both the suffix-side rendering ("5 USDC" instead of "5 SOL") and the
   * insufficientFunds skip — for ExactOut we can't pre-check the input
   * balance because the input cost is whatever the route quotes.
   */
  readonly swapAmountIsOutput = computed<boolean>(() => {
    if (!this.action || this.action.type !== 'swap') return false;
    const rawMode = String(this.editParams()['swapMode'] ?? '').toLowerCase();
    return rawMode === 'exactout' || rawMode === 'out';
  });

  /**
   * Actions whose `amount` is RECEIVED (borrow) or drawn from a protocol
   * position (withdraw from a lending market) rather than spent from the user's
   * wallet balance of the named token. A wallet-balance check is meaningless
   * here — you don't need 1000 USDC in your wallet to *borrow* 1000 USDC; you
   * post collateral. Gating the CTA on it wrongly blocks the action.
   */
  private readonly NON_WALLET_SPEND_ACTIONS = new Set<string>([
    'borrow', 'kamino_borrow', 'marginfi_borrow', 'solend_borrow',
    'withdraw_lend', 'kamino_withdraw', 'marginfi_withdraw', 'solend_withdraw',
  ]);

  readonly insufficientFunds = computed(() => {
    if (this.swapAmountIsOutput()) return false;
    if (this.action && this.NON_WALLET_SPEND_ACTIONS.has(this.action.type)) return false;
    const bal = this.inputBalance();
    const amt = parseFloat(this.editParams()['amount'] ?? '0');
    return bal !== null && amt > 0 && amt > bal;
  });

  /**
   * Per-side display value for the inline swap amount inputs. The side whose
   * mode is "exact" reads from the editable `amount` field; the other side
   * reads from the live counterparty estimate so the user sees both legs.
   */
  swapInputValueFor(fieldKey: 'inputMint' | 'outputMint'): string {
    const p = this.editParams();
    const mode = String(p['swapMode'] ?? '').toLowerCase();
    const isExactOut = mode === 'exactout' || mode === 'out';
    const isExactInputSide = (fieldKey === 'inputMint' && !isExactOut) || (fieldKey === 'outputMint' && isExactOut);
    if (isExactInputSide) return p['amount'] ?? '';
    const est = this.swapEstimate();
    return est?.counterUi ?? '';
  }

  /**
   * Called when the user types into one of the inline amount inputs. Pins the
   * `amount` to the edited side and flips `swapMode` so the OTHER side
   * becomes the floating counterparty estimate.
   */
  onSwapAmountInput(fieldKey: 'inputMint' | 'outputMint', raw: string): void {
    const value = this.normalizeDecimal(raw);
    this.editParams.update(prev => {
      const next = { ...prev };
      next['amount'] = value;
      next['swapMode'] = fieldKey === 'inputMint' ? 'ExactIn' : 'ExactOut';
      return next;
    });
  }
  readonly belowMinAmount = signal<number | null>(null);

  // Liquid staking conversion preview. exchange_rate is always SOL per LST
  // token, so: stake → out = SOL_in / rate; unstake → out = LST_in * rate.
  // The rate has three states: not yet fetched (loading), fetched OK (number),
  // or fetch failed (error). We never substitute a static constant — a wrong
  // preview is more dangerous than no preview.
  readonly lstExchangeRate = signal<number | null>(null);
  readonly lstRateLoading = signal(false);
  readonly lstRateError = signal<string | null>(null);
  readonly lstEstimate = computed<{ amount: number; symbol: string } | null>(() => {
    const cfg = this.lstActionConfig();
    if (!cfg) return null;
    const rate = this.lstExchangeRate();
    const amt = parseFloat(this.editParams()['amount'] ?? '');
    if (!rate || rate <= 0 || !Number.isFinite(amt) || amt <= 0) return null;
    return cfg.direction === 'stake'
      ? { amount: amt / rate, symbol: cfg.lstSymbol }
      : { amount: amt * rate, symbol: 'SOL' };
  });
  /** True when the action needs an LST rate but we don't have one yet. */
  readonly lstRateMissing = computed(() => {
    const cfg = this.lstActionConfig();
    return cfg !== null && this.lstExchangeRate() === null;
  });

  /** Map a known LST action type → which rate fetcher to use and its tokens. */
  lstActionConfig(): { direction: 'stake' | 'unstake'; lstSymbol: string; protocol: 'jito' | 'marinade' | 'jupsol' } | null {
    switch (this.action?.type) {
      case 'jito_stake':       return { direction: 'stake',   lstSymbol: 'jitoSOL', protocol: 'jito' };
      case 'jito_unstake':     return { direction: 'unstake', lstSymbol: 'jitoSOL', protocol: 'jito' };
      case 'marinade_stake':   return { direction: 'stake',   lstSymbol: 'mSOL',    protocol: 'marinade' };
      case 'marinade_unstake': return { direction: 'unstake', lstSymbol: 'mSOL',    protocol: 'marinade' };
      case 'jupsol_stake':     return { direction: 'stake',   lstSymbol: 'jupSOL',  protocol: 'jupsol' };
      case 'jupsol_unstake':   return { direction: 'unstake', lstSymbol: 'jupSOL',  protocol: 'jupsol' };
      default: return null;
    }
  }

  // ── pump.fun / PumpSwap live counterparty estimate ──────────────────────
  // Same idea as the LST estimate + the swap poller: while a pump.fun /
  // PumpSwap buy/sell card is pending, show the *other* leg of the trade
  // ("1 SOL ≈ N TOKEN") refreshed every POLL_INTERVAL_S so the user sees the
  // live conversion, with icons for both the spent and received token.
  readonly SOL_MINT = 'So11111111111111111111111111111111111111112';

  /** buy = spend SOL → receive token; sell = spend token → receive SOL. */
  pumpActionConfig(): { side: 'buy' | 'sell' } | null {
    switch (this.action?.type) {
      case 'pumpfun_buy':
      case 'pumpswap_buy':  return { side: 'buy' };
      case 'pumpfun_sell':
      case 'pumpswap_sell': return { side: 'sell' };
      default: return null;
    }
  }

  /** Is the typed `amount` denominated in SOL for this pump action? */
  private pumpAmountInSol(side: 'buy' | 'sell', params: Record<string, string | undefined>): boolean {
    // buy defaults to SOL-denominated (spend N SOL); sell defaults to token units.
    return side === 'buy'
      ? (params['denominatedInSol'] ?? 'true') !== 'false'
      : (params['denominatedInSol'] ?? 'false') === 'true';
  }

  // Raw quote result in BASE units + the counter mint. Display is derived in
  // `pumpEstimate` so it self-corrects once the registry resolves the token's
  // real decimals / logo (first quote may run before metadata arrives).
  private readonly _pumpQuoteRaw = signal<{ counterBase: number; counterMint: string } | null>(null);

  readonly pumpEstimate = computed<{ amount: number; symbol: string; logoURI: string | null } | null>(() => {
    const raw = this._pumpQuoteRaw();
    if (!raw) return null;
    this.tokenRegistry.version(); // reactive: re-derive when decimals/logo resolve
    const td = this.resolveTokenDisplay(raw.counterMint);
    const dec = td.decimals ?? (raw.counterMint === this.SOL_MINT ? 9 : 6);
    return { amount: raw.counterBase / Math.pow(10, dec), symbol: td.symbol, logoURI: td.logoURI ?? null };
  });

  /**
   * Logo for the RECEIVED (counterparty) token in the paired "≈ N SYMBOL"
   * output, for whichever estimate is active. Kept as a computed so the
   * template stays strictly typed (the LST and pump estimate shapes differ).
   */
  readonly pairedOutputLogo = computed<string | null>(() => {
    const lst = this.lstEstimate();
    if (lst) return this.resolveTokenDisplay(lst.symbol).logoURI ?? null;
    const pump = this.pumpEstimate();
    if (pump) return pump.logoURI;
    return null;
  });

  /** True for pump.fun / PumpSwap buy+sell — renders the mini-Uniswap layout. */
  readonly isPumpSwapForm = computed(() => this.pumpActionConfig() !== null);

  /** The RECEIVED token mint: buy → the pump token, sell → SOL. */
  readonly pumpReceiveMint = computed(() => {
    const cfg = this.pumpActionConfig();
    if (!cfg) return '';
    const p = this.editParams();
    return cfg.side === 'buy' ? (p['mint'] ?? p['token'] ?? '') : this.SOL_MINT;
  });

  private _pumpEstimateTimer: ReturnType<typeof setTimeout> | null = null;
  private _pumpEstimateSeq = 0;

  private readonly _pumpEstimateEffect = effect(() => {
    const params = this.editParams();
    const cfg = this.pumpActionConfig();
    if (!cfg) { this._pumpQuoteRaw.set(null); return; }
    const mint = (params['mint'] ?? params['token'] ?? '').trim();
    const amt = parseFloat((params['amount'] ?? '').trim());
    if (!mint || !Number.isFinite(amt) || amt <= 0) { this._pumpQuoteRaw.set(null); return; }
    const inSol = this.pumpAmountInSol(cfg.side, params);
    const seq = ++this._pumpEstimateSeq;
    if (this._pumpEstimateTimer) clearTimeout(this._pumpEstimateTimer);
    this._pumpEstimateTimer = setTimeout(() => {
      this.fetchPumpEstimate(seq, cfg.side, mint, amt, inSol);
    }, 400);
    // Edit = interaction → full poll-lifetime reset (mirrors the swap effect).
    if (this._pollIntervalId !== null) {
      this._pollVisibleElapsedMs = 0;
      this._pollSecondsSinceQuote = 0;
      this.quoteCountdown.set(this.POLL_INTERVAL_S);
    }
  });

  private async fetchPumpEstimate(
    seq: number, side: 'buy' | 'sell', mint: string, amt: number, amountInSol: boolean,
  ): Promise<void> {
    // The backend /actions/quote refuses unverified tokens, so pump.fun mints
    // (not in the strict registry) can't be priced there. Hit Jupiter's public
    // quote API directly instead — it routes both graduated (PumpSwap AMM) and
    // bonding-curve pump tokens. Amount must be in BASE units, so we need the
    // pump mint's real decimals (usually 6) — resolve them up front.
    const mintMeta = await this.tokenRegistry.resolveTokenMeta(mint).catch(() => null);
    if (seq !== this._pumpEstimateSeq) return;
    const mintDecimals = mintMeta?.decimals ?? 6;
    const SOL = this.SOL_MINT;
    // Quote direction + which leg the user typed vs. receives.
    //   buy  + SOL amount   → ExactIn  SOL→mint,  counter = tokens out
    //   buy  + token amount → ExactOut SOL→mint,  counter = SOL cost (in)
    //   sell + token amount → ExactIn  mint→SOL,  counter = SOL out
    //   sell + SOL amount   → ExactOut mint→SOL,  counter = tokens needed (in)
    const inAddr = side === 'buy' ? SOL : mint;
    const outAddr = side === 'buy' ? mint : SOL;
    const amountIsInput = side === 'buy' ? amountInSol : !amountInSol;
    const mode: 'ExactIn' | 'ExactOut' = amountIsInput ? 'ExactIn' : 'ExactOut';
    // Jupiter's `amount` denominates the leg the mode fixes: ExactIn → input,
    // ExactOut → output. Convert the typed UI amount to that leg's base units.
    const amountLeg = amountIsInput ? inAddr : outAddr;
    const amountDecimals = amountLeg === SOL ? 9 : mintDecimals;
    const baseAmount = Math.round(amt * Math.pow(10, amountDecimals));
    if (!(baseAmount > 0)) { this._pumpQuoteRaw.set(null); return; }
    try {
      const url = `https://api.jup.ag/swap/v1/quote?inputMint=${inAddr}&outputMint=${outAddr}`
        + `&amount=${baseAmount}&slippageBps=50&swapMode=${mode}`;
      const q = await fetch(url).then(r => (r.ok ? r.json() : null));
      if (seq !== this._pumpEstimateSeq) return;
      if (!q || !q.outAmount || !q.inAmount) { this._pumpQuoteRaw.set(null); return; }
      // Counter (what we DISPLAY) = the leg the user did NOT type.
      const counterBase = parseInt(amountIsInput ? q.outAmount : q.inAmount, 10);
      const counterMint = amountIsInput ? outAddr : inAddr;
      if (!Number.isFinite(counterBase) || counterBase <= 0) { this._pumpQuoteRaw.set(null); return; }
      this._pumpQuoteRaw.set({ counterBase, counterMint });
    } catch {
      // No route (e.g. brand-new token Jupiter hasn't indexed yet) — clear the
      // estimate; the card still works, just without the live preview.
      if (seq === this._pumpEstimateSeq) this._pumpQuoteRaw.set(null);
    }
  }

  private async refreshPumpQuoteSilently(): Promise<void> {
    const cfg = this.pumpActionConfig();
    if (!cfg) return;
    const p = this.editParams();
    const mint = (p['mint'] ?? p['token'] ?? '').trim();
    const amt = parseFloat(p['amount'] ?? '');
    if (!mint || !Number.isFinite(amt) || amt <= 0) return;
    const inSol = this.pumpAmountInSol(cfg.side, p);
    const seq = ++this._pumpEstimateSeq;
    await this.fetchPumpEstimate(seq, cfg.side, mint, amt, inSol);
  }

  // Lend
  readonly lendInfo = signal<LendActionInfo | null>(null);
  readonly lendInfoLoading = signal(false);
  readonly borrowLiquidityMode = signal(false);

  // Kamino K-Lend borrow: live reserve rates + the user's obligation, used to
  // project LTV / health factor and cap the borrow at what the collateral allows.
  readonly kaminoReserve = signal<KaminoReserve | null>(null);
  readonly kaminoObligation = signal<KaminoObligation | null>(null);
  readonly kaminoBorrowLoading = signal(false);
  // K-Vault (automated Earn vault) live metrics for the deposit card.
  readonly kaminoVaultMetrics = signal<KaminoVaultMetrics | null>(null);
  readonly kaminoVaultLoading = signal(false);
  // The user's live position in the vault they're withdrawing from (shares +
  // USD value + underlying tokenMint for the real icon).
  readonly kaminoVaultPosition = signal<KaminoVaultPosition | null>(null);
  // The vault's underlying token mint — the positions API doesn't return it, so
  // we resolve it from the vault list. Drives the card's real token icon/symbol.
  readonly kaminoVaultTokenMint = signal<string>('');

  // Collateral
  readonly collateralOptions = signal<CollateralOption[]>([]);
  readonly selectedCollateral = signal<CollateralOption | null>(null);
  readonly collateralInput = signal('');
  readonly borrowCapacity = signal<{ loading: boolean; maxBorrow: number } | null>(null);
  // Live Jupiter Lend borrow vaults for the chosen debt token (one per collateral).
  readonly borrowVaults = signal<LendBorrowInfo[]>([]);

  // Undo
  undoPrompt = false;
  readonly canUndo = computed(() => this.snapshotId !== null);
  readonly undoInProgress = signal(false);

  // Misc
  readonly copiedField = signal<string | null>(null);

  // Computed (template direct access)
  get protocolConfig(): ProtocolConfig { return PROTOCOL_CONFIGS[getProtocolKey(this.action)] ?? PROTOCOL_CONFIGS['default']; }
  get actionLabel(): string { return getActionLabel(this.action); }
  /** Adopt the swap widget's visual language (rounded 16px surfaces, taller
   *  inputs, larger figures) for token+amount forms that render via the generic
   *  proto-fields layout — Kamino Multiply and long/short. */
  get useSwaplikeLayout(): boolean {
    const t = this.action?.type ?? '';
    return t.startsWith('kamino_multiply_')
      || t === 'kamino_long_open' || t === 'kamino_short_open'
      // Raydium token-amount forms (deposit/withdraw/positions) use the same
      // bigger, rounded swap-card input styling. (raydium_swap renders its own
      // bespoke two-panel widget, so it's intentionally not here.)
      || t === 'raydium_add_liquidity' || t === 'raydium_remove_liquidity'
      || t === 'raydium_open_position' || t === 'raydium_increase_position'
      || t === 'raydium_decrease_position';
  }
  get actionFields(): FieldDef[] {
    // Post-process: stamp protocol-aware MIN/hint on every amount-shaped input
    // so the user sees the floor before submitting (prevents the on-chain
    // "amount below protocol's minimum" rejection). We do this here so each
    // action handler in getActionFields stays minimal — the catalog
    // (ACTION_MIN_AMOUNT) is the single source of truth.
    const AMOUNT_KEYS = new Set([
      'amount', 'amountA', 'amountB', 'amountX', 'amountY',
      'inputAmount', 'totalAmount',
    ]);
    return getActionFields(this.action, this.editParams()).map(f =>
      AMOUNT_KEYS.has(f.key) ? applyMinAmountGuard(f, this.action.type) : f
    );
  }
  /**
   * The CTA names the action, not the gesture. The user clicked "Withdraw" on
   * a position row, so a button reading "Confirm" makes them re-derive what
   * they are signing. Protocol actions are hundreds of types, so match on the
   * verb the type name already carries rather than enumerating every one —
   * a new `<protocol>_claim_fees` then labels itself correctly on arrival.
   */
  get confirmButtonLabel(): string {
    const labels: Record<string, string> = { swap:'Swap', transfer:'Send', stake:'Stake', unstake:'Unstake', lend:'Deposit', withdraw:'Withdraw', borrow:'Borrow', repay:'Repay', add_liquidity:'Add Liquidity', remove_liquidity:'Remove Liquidity' };
    const t = this.action?.type ?? '';
    if (labels[t]) return labels[t];
    // Ordered longest-verb-first so `add_to_position` isn't read as `stake`
    // by a shorter pattern, and `close_position` beats bare `position`.
    const verbs: ReadonlyArray<[RegExp, string]> = [
      [/(claim_fees|collect_fees|harvest)$/, 'Claim Fees'],
      [/(claim_rewards|collect_rewards)$/,   'Claim Rewards'],
      [/claim/,                              'Claim'],
      [/close_position/,                     'Close Position'],
      [/open_position/,                      'Open Position'],
      [/(add_to_position|increase_position|increase_liquidity)/, 'Add Liquidity'],
      [/(remove_liquidity|decrease_liquidity|decrease_position)/, 'Withdraw'],
      [/add_liquidity/,                      'Add Liquidity'],
      [/deposit/,                            'Deposit'],
      [/withdraw/,                           'Withdraw'],
      [/cancel_unstake/,                     'Cancel Unstake'],
      [/unstake/,                            'Unstake'],
      [/stake/,                              'Stake'],
      [/swap/,                               'Swap'],
      [/borrow/,                             'Borrow'],
      [/repay/,                              'Repay'],
    ];
    for (const [re, label] of verbs) {
      if (re.test(t)) return label;
    }
    return 'Confirm';
  }
  get explorerUrl(): string { const s = this.txSignature(); return s ? `https://solscan.io/tx/${s}` : ''; }
  get protocolNote(): { type: 'info' | 'warning'; lines: string[] } | null { return null; }

  readonly isLaunchAction = computed(() =>
    this.action?.type === 'launch_token' ||
    this.action?.type === 'token_launch' ||
    this.action?.type === 'pumpfun_launch'
  );
  readonly isTokenLaunch = computed(() => this.isLaunchAction());
  readonly isEditable = computed(() => this.status() === 'pending' || this.status() === 'error');
  readonly isNameInvalid = computed(() => this.isLaunchAction() && this.editName().trim().length === 0);
  readonly isSymbolInvalid = computed(() => this.isLaunchAction() && this.editSymbol().trim().length === 0);
  readonly isImageInvalid = false;
  /**
   * Why Confirm is disabled, in the words the button should say.
   *
   * This used to be a boolean, and the button carried a hardcoded "Insufficient
   * collateral" for whatever turned it false — correct for borrow, the only
   * gate at the time. Every gate added since inherited that label: a blocked
   * bridge claimed insufficient collateral, and so did a transfer with no
   * recipient. A reason travels with its cause; a boolean leaves the button
   * guessing.
   */
  readonly approvalBlock = computed<string | null>(() => {
    if (this.borrowLiquidityMode()) {
      const c = this.borrowCapacity();
      return c !== null && !c.loading && c.maxBorrow > 0 ? null : 'Insufficient collateral';
    }
    // A bridge to an EVM chain cannot be signed without an address that chain
    // can receive on, nor without the wallet that signs where it leaves from.
    if (this.isRelayBridge()) {
      if (!this.relayRecipientReady()) return 'Connect a wallet to receive';
      if (!this.relaySenderReady()) return 'Connect the sending wallet';
    }
    if (this.isTransfer()) {
      if (!(Number(this.getEditParam('amount')) > 0)) return 'Enter an amount';
      if (!this.getEditParam('to').trim()) return 'Enter a recipient';
      if (this.recipientState().kind === 'invalid') return 'That recipient is not valid';
    }
    if (this.isCloseAccounts() && !this.emptyAccountsLoading() && !this.emptyAccounts().length) {
      return 'Nothing to close';
    }
    return null;
  });

  readonly canApprove = computed(() => this.approvalBlock() === null);
  readonly unverifiedDestination = computed(() => this.action?.warnUnverifiedDestination ?? false);

  /**
   * Block the Borrow CTA while the inputs are incomplete or unsafe: no debt /
   * collateral amount, or the live stats flagged an error (over max borrowable,
   * insufficient collateral, below-minimum, or thin liquidity).
   */
  readonly borrowInputInvalid = computed(() => {
    if (this.action?.type !== 'borrow') return false;
    const debt = parseFloat(this.getEditParam('amount') || '0') || 0;
    const col = parseFloat(this.getEditParam('collateralAmount') || '0') || 0;
    if (debt <= 0 || col <= 0) return true;
    return !!this.borrowLiveStats?.errorMsg;
  });

  // Quick swap getters (template direct access)
  get qsFromToken(): { mint: string; symbol: string; balance: number; logoURI?: string } | null { return this.qsTokenList().find(t => t.mint === this.qsFromMint()) ?? null; }
  get qsFromBalance(): number { return this.qsFromToken?.balance ?? 0; }
  get qsNeededAmount(): number { return Math.max(0, +(parseFloat(this.editParams()['amount'] ?? '0') - (this.inputBalance() ?? 0)).toFixed(6)); }
  get qsToTokenData(): { symbol: string; name: string; logoURI?: string } { return this.resolveTokenDisplay(this.inputBalanceMint()); }

  /**
   * Live borrow-position projection for the selected collateral + typed amounts.
   * Standard money-market math off Jupiter's live vault prices/LTV:
   *   collateralUsd  = collateralAmount × collateralPrice
   *   maxBorrowable  = collateralUsd × maxLtv ÷ debtPrice           (debt units)
   *   healthFactor   = (collateralUsd × liquidationThreshold) ÷ borrowUsd
   *   liquidation px = borrowUsd ÷ (collateralAmount × liquidationThreshold)
   * Returns null (stats hidden) until a collateral is picked.
   */
  get borrowLiveStats(): { collateralUsd: number; maxBorrowable: number; healthFactor: number; healthFactorLabel: string; hfClass: string; liquidationPriceLabel: string; errorMsg?: string } | null {
    const sel = this.selectedCollateral();
    if (!sel) return null;
    const vault = this.borrowVaults().find(v => v.collateralMint === sel.mint);
    if (!vault) return null;

    const colAmt = parseFloat(this.getEditParam('collateralAmount') || '0') || 0;
    const debtAmt = parseFloat(this.getEditParam('amount') || '0') || 0;

    const collateralUsd = colAmt * vault.collateralPrice;
    const maxBorrowable = vault.debtPrice > 0 ? (collateralUsd * vault.maxLtv) / vault.debtPrice : 0;
    const borrowUsd = debtAmt * vault.debtPrice;
    const healthFactor = borrowUsd > 0 ? (collateralUsd * vault.liquidationThreshold) / borrowUsd : Infinity;
    const liquidationPrice = (colAmt > 0 && vault.liquidationThreshold > 0)
      ? borrowUsd / (colAmt * vault.liquidationThreshold)
      : 0;

    // Health factor only applies once BOTH sides have a value. With debt but no
    // collateral the ratio is 0 (instant-liquidation) — but no position exists
    // yet, so it's clearer to show "—" and prompt for collateral than a red 0.00.
    const hfApplies = borrowUsd > 0 && collateralUsd > 0;
    const hfClass = !hfApplies || !isFinite(healthFactor) || healthFactor >= 1.6
      ? 'safe'
      : healthFactor >= 1.2 ? 'caution' : 'danger';
    const healthFactorLabel = borrowUsd <= 0
      ? '—'
      : collateralUsd <= 0 ? '—'
      : !isFinite(healthFactor) ? '∞' : healthFactor.toFixed(2);

    let errorMsg: string | undefined;
    if (debtAmt > 0 && colAmt <= 0) {
      errorMsg = `Enter a collateral amount to secure this borrow.`;
    } else if (debtAmt > 0 && debtAmt > vault.availableLiquidity) {
      errorMsg = `Only ${vault.availableLiquidity.toFixed(2)} ${vault.debtSymbol} available to borrow right now.`;
    } else if (debtAmt > 0 && maxBorrowable > 0 && debtAmt > maxBorrowable) {
      errorMsg = `Exceeds max borrowable — add collateral or borrow ≤ ${maxBorrowable.toFixed(2)} ${vault.debtSymbol}.`;
    } else if (colAmt > 0 && sel.balance > 0 && colAmt > sel.balance) {
      errorMsg = `You only have ${sel.balance.toFixed(4)} ${sel.symbol} — reduce the collateral amount.`;
    } else if (debtAmt > 0 && vault.minimumBorrow > 0 && debtAmt < vault.minimumBorrow) {
      errorMsg = `Minimum borrow is ${vault.minimumBorrow} ${vault.debtSymbol}.`;
    }

    const liquidationPriceLabel = borrowUsd > 0 && liquidationPrice > 0
      ? `$${liquidationPrice < 1 ? liquidationPrice.toFixed(4) : liquidationPrice.toFixed(2)}`
      : '—';

    return {
      collateralUsd,
      maxBorrowable,
      healthFactor: isFinite(healthFactor) ? healthFactor : 0,
      healthFactorLabel,
      hfClass,
      liquidationPriceLabel,
      errorMsg,
    };
  }

  /** The live vault matching the selected collateral (falls back to the first). */
  get borrowSelectedVault(): LendBorrowInfo | undefined {
    const sel = this.selectedCollateral();
    const vaults = this.borrowVaults();
    return vaults.find(v => v.collateralMint === sel?.mint) ?? vaults[0];
  }

  /**
   * Bulk-load the wallet's balances into a mint -> uiAmount map with a single
   * getTokenAccounts sweep (2 RPC calls) plus native SOL. Native SOL is keyed
   * under both the WSOL mint and the literal 'SOL' sentinel so collateral
   * vaults that reference either resolve correctly.
   */
  private async loadWalletBalanceMap(): Promise<Map<string, number>> {
    const map = new Map<string, number>();
    const wallet = this.walletService.publicKey();
    if (!wallet) return map;
    const SOL_MINT = 'So11111111111111111111111111111111111111112';
    try {
      const [sol, tokens] = await Promise.all([
        this.solanaRpc.getBalance(wallet).catch(() => 0),
        this.solanaRpc.getTokenAccounts(wallet).catch(() => []),
      ]);
      for (const t of tokens) map.set(t.mint, t.balance);
      const nativeSol = sol / 1e9;
      // Surface native SOL as WSOL collateral balance (borrow vaults list SOL
      // under the WSOL mint); never let a stray WSOL ATA hide the native lamports.
      map.set(SOL_MINT, Math.max(map.get(SOL_MINT) ?? 0, nativeSol));
      map.set('SOL', nativeSol);
    } catch {
      /* leave map empty on total failure */
    }
    return map;
  }

  /**
   * Load the live Jupiter Lend borrow vaults for the current debt token, build
   * the collateral picker (one option per vault, annotated with the user's
   * wallet balance), and pick a sensible default collateral. Drives the
   * professional borrow card (picker + live health/liquidation stats).
   */
  async loadBorrowInfo(): Promise<void> {
    if (this.action?.type !== 'borrow') return;
    const debt = this.getEditParam('token') || 'USDC';
    this.lendInfoLoading.set(true);
    this.borrowCapacity.set({ loading: true, maxBorrow: 0 });
    try {
      const vaults = await this.jupiterLend.getBorrowInfo(debt);
      this.borrowVaults.set(vaults);
      if (!vaults.length) {
        this.collateralOptions.set([]);
        this.selectedCollateral.set(null);
        this.borrowCapacity.set({ loading: false, maxBorrow: 0 });
        return;
      }
      // One bulk balance fetch (getTokenAccounts = 2 RPC calls) + native SOL,
      // rather than one getTokenBalance per collateral vault (~19 calls). Build
      // a mint -> uiAmount map and annotate each collateral option from it.
      const balanceByMint = await this.loadWalletBalanceMap();
      const opts: CollateralOption[] = vaults.map(v => ({
        mint: v.collateralMint,
        symbol: v.collateralSymbol,
        logo: v.collateralLogo,
        debtSymbol: v.debtSymbol,
        balance: balanceByMint.get(v.collateralMint) ?? 0,
      }));
      this.collateralOptions.set(opts);

      // Default: the collateral the user named (if any), else the one they hold
      // the most of, else the first vault.
      const wanted = (this.getEditParam('collateral') || '').toUpperCase();
      const sel = opts.find(o => o.symbol.toUpperCase() === wanted)
        ?? [...opts].sort((a, b) => b.balance - a.balance)[0];
      if (sel) {
        this.selectedCollateral.set(sel);
        this.setEditParam('collateral', sel.symbol);
        const vault = vaults.find(v => v.collateralMint === sel.mint);
        if (vault) this.setEditParam('vaultId', String(vault.vaultId));
      }
      this.borrowCapacity.set({ loading: false, maxBorrow: 0 });
    } catch {
      this.borrowVaults.set([]);
      this.collateralOptions.set([]);
      this.borrowCapacity.set({ loading: false, maxBorrow: 0 });
    } finally {
      this.lendInfoLoading.set(false);
    }
  }

  ngOnInit(): void {
    if (this.action) this.initFromAction();
    this.maybeLoadLstRate();
    if (this.isMeteoraDlmm()) this.seedMeteoraRange();
    this.maybeEnrichRaydiumPool();
    void this.maybeEnrichRaydiumWithdraw();
    void this.maybeEnrichMeteoraPosition();
    void this.maybeResolveSwapCounterToken();
    this.maybeSeedOrcaRange();
    this.maybeNormalizeExactOutToExactIn();
    this.maybeLoadCancelDcaTarget();
    this.maybeDefaultBorrowCollateral();
    void this.ensureMeCancelTarget();
    this.relaySeedChains();
    if (this.isRelayBridge()) this.startEvmDiscovery();
    // The card arrives with its fields already filled by the model, and the
    // quote only ran on user input — so a bridge someone never typed into sat
    // on "pick both chains" with every chain already picked.
    this.relayMaybeQuote();
    // Also here, not only from the panel's computed: a computed evaluates once
    // and memoises, so a first evaluation that ran before the params landed
    // left the lookup permanently unfired — which is why no Magic Eden action
    // card ever showed traits.
    this.ensureMeNftDisplay();
  }

  /** Borrow needs a collateral asset; when the user didn't name one ("borrow
   *  1000 USDC"), pre-fill SOL so the card is usable instead of blocking on an
   *  empty required field. The user can still switch the collateral token. */
  private maybeDefaultBorrowCollateral(): void {
    if (this.action?.type !== 'borrow') return;
    if (!this.editParams()['collateral']) {
      this.editParams.update(prev => ({ ...prev, collateral: 'SOL' }));
    }
    // Load live vaults + collateral options + balances for the pro borrow card.
    void this.loadBorrowInfo();
  }
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['action'] && this.action) {
      this.initFromAction();
      this.lstExchangeRate.set(null);
      this.lstRateError.set(null);
      this.lstRateLoading.set(false);
      this.maybeLoadLstRate();
      this._enrichedRaydiumPool = null;
      this.maybeEnrichRaydiumPool();
      this.maybeNormalizeExactOutToExactIn();
      this.cancelDcaTarget.set(null);
      this.maybeLoadCancelDcaTarget();
      this.maybeDefaultBorrowCollateral();
      return;
    }
    // Re-apply cached result if it arrives after the component was already initialized
    if (changes['cachedResult'] && this.cachedResult && this.status() === 'pending') {
      this.initFromAction();
    }
  }

  /**
   * For a `cancel_dca` card, fetch the user's active DCA order(s) so the card
   * can show *what* is being cancelled. The order address itself is resolved
   * server-side at build time; this is purely for display. If an explicit order
   * address was supplied (frontend card Cancel button), we match it; otherwise
   * we show the single active order (the same one the backend will resolve).
   */
  async maybeLoadCancelDcaTarget(): Promise<void> {
    if (this.action?.type !== 'cancel_dca') return;
    this.cancelDcaLoading.set(true);
    try {
      const resp = await firstValueFrom(
        this.apiService.get<any>('/actions/dca-orders', { status: 'open' }),
      );
      // Jupiter Recurring API v1: time-based orders under resp.time.
      const orders: any[] = resp?.time ?? resp?.orders ?? resp?.all ?? [];
      const explicit = String(this.editParams()['order'] ?? '').trim().toLowerCase();
      const isAuto = !explicit || ['self', 'all', 'auto', 'mine', 'active'].includes(explicit);
      const picked = isAuto
        ? orders[0]
        : orders.find(o => (o.orderKey ?? o.publicKey ?? '').toLowerCase() === explicit) ?? orders[0];
      if (!picked) { this.cancelDcaTarget.set(null); return; }

      const inputMint: string = picked.inputMint ?? '';
      const outputMint: string = picked.outputMint ?? '';
      // Jupiter Recurring returns human-readable amounts already (inAmountPerCycle,
      // inDeposited, inUsed) — the `raw*` variants are the base-unit versions.
      // Cycle counts aren't returned directly; derive them from deposited/used.
      const perCycle = parseFloat(picked.inAmountPerCycle ?? '0');
      const deposited = parseFloat(picked.inDeposited ?? '0');
      const used = parseFloat(picked.inUsed ?? '0');
      const total = perCycle > 0 ? Math.round(deposited / perCycle) : 0;
      const executed = perCycle > 0 ? Math.round(used / perCycle) : 0;
      this.cancelDcaTarget.set({
        input: this.tokenRegistry.getToken(inputMint)?.symbol ?? (inputMint ? inputMint.slice(0, 4) : '—'),
        output: this.tokenRegistry.getToken(outputMint)?.symbol ?? (outputMint ? outputMint.slice(0, 4) : '—'),
        perCycle: Number(perCycle.toFixed(6)),
        frequency: this.formatDcaFrequency(Number(picked.cycleFrequency ?? 86400)),
        remaining: Math.max(0, total - executed),
        total,
      });
    } catch {
      this.cancelDcaTarget.set(null);
    } finally {
      this.cancelDcaLoading.set(false);
    }
  }

  private formatDcaFrequency(secs: number): string {
    switch (secs) {
      case 60: return 'every minute';
      case 3600: return 'hourly';
      case 86400: return 'daily';
      case 604800: return 'weekly';
      case 2592000: return 'monthly';
      default: {
        if (secs % 86400 === 0) return `every ${secs / 86400} days`;
        if (secs % 3600 === 0) return `every ${secs / 3600} hours`;
        return `every ${secs}s`;
      }
    }
  }

  /**
   * Fill in a Raydium withdraw card's display context (token amounts, pair,
   * symbols, logos) by reading the live positions when the spawning card
   * didn't supply them — e.g. a positions card restored from an older chat
   * snapshot, or an LLM-emitted close/remove that carries only an id.
   *
   * Positions are LIVE state, so reading them fresh here also keeps a
   * long-lived chat card honest about what it's about to withdraw.
   */
  /**
   * A Meteora action spawned from chat (or from a clarify prompt) carries only
   * the position address — the model has nothing else to give. The card then
   * rendered "??/??" with no range, no balance and no fees, which is the exact
   * information needed to decide whether to sign.
   *
   * The positions card passes all of it when the action starts from a row, so
   * this only fills the gap: look the position up in the wallet's DLMM
   * portfolio and merge the same fields in.
   */
  async maybeEnrichMeteoraPosition(): Promise<void> {
    const type = this.action?.type ?? '';
    if (!type.startsWith('meteora_')) return;
    const p = this.editParams();
    const position = p['position'] || p['positionId'];
    if (!position) return;

    // Always re-read, even when the symbols are already known. The row's
    // figures were captured when the positions card fetched; by the time the
    // user opens an action the position may have grown or shrunk, and a Close
    // panel quoting pre-deposit amounts is worse than one quoting none.
    const isDamm = (this.action?.type ?? '').includes('dammv2');
    try {
      const resp = await firstValueFrom(
        this.apiService.post<{ data?: { pools?: any[] } }>('/actions/build', {
          type: isDamm ? 'meteora_dammv2_get_user_positions' : 'meteora_dlmm_get_user_positions',
          params: {},
        }).pipe(timeout(20_000)),
      );
      const pools: any[] = resp?.data?.pools ?? [];
      const pool = pools.find(pl => (pl.listPositions ?? []).includes(position));
      if (!pool) {
        // The row that spawned this action is a record of an earlier moment.
        // The position it names may already be closed — say so and block the
        // CTA, rather than letting the user sign something the chain will
        // reject. This is why the listing itself doesn't need refetching on
        // reload: the check happens where someone is about to act.
        this.meteoraPositionGone.set(true);
        return;
      }
      this.meteoraPositionGone.set(false);
      const detail = (pool.positions ?? []).find((d: any) => d.address === position);

      this.editParams.update(ep => ({
        ...ep,
        pair: `${pool.tokenX}/${pool.tokenY}`,
        tokenASymbol: pool.tokenX,
        tokenBSymbol: pool.tokenY,
        ...(pool.tokenXMint ? { tokenA: pool.tokenXMint } : {}),
        ...(pool.tokenYMint ? { tokenB: pool.tokenYMint } : {}),
        ...(pool.tokenXIcon ? { tokenALogo: pool.tokenXIcon } : {}),
        ...(pool.tokenYIcon ? { tokenBLogo: pool.tokenYIcon } : {}),
        pool: pool.poolAddress,
        poolId: pool.poolAddress,
        binStep: String(pool.binStep ?? ''),
        currentPrice: String(pool.poolPrice ?? ''),
        // The ratio engine needs the bin ids, the active bin and the token
        // decimals — without them it can't tell which side of the price the
        // range sits on, so BOTH amount inputs stay enabled even when the
        // pool would only accept one of them at this range.
        ...(pool.activeBinId !== undefined ? { activeBinId: String(pool.activeBinId) } : {}),
        ...(pool.tokenXDecimals !== undefined ? { tokenADecimals: String(pool.tokenXDecimals) } : {}),
        ...(pool.tokenYDecimals !== undefined ? { tokenBDecimals: String(pool.tokenYDecimals) } : {}),
        ...(detail
          ? {
              minBinId: String(detail.lowerBinId),
              maxBinId: String(detail.upperBinId),
              ...(detail.lowerPrice !== undefined && detail.upperPrice !== undefined
                ? {
                    positionMinPrice: String(detail.lowerPrice),
                    positionMaxPrice: String(detail.upperPrice),
                    positionBinCount: String(detail.binCount ?? ''),
                  }
                : {}),
              positionAmountA: String(detail.amountX),
              positionAmountB: String(detail.amountY),
              positionFeeA: String(detail.unclaimedFeeX),
              positionFeeB: String(detail.unclaimedFeeY),
              positionOutOfRange: detail.inRange ? 'false' : 'true',
              ...(detail.rentSol !== undefined ? { positionRentSol: String(detail.rentSol) } : {}),
            }
          : {}),
      }));
      // The balance loaders ran in ngOnInit, before this lookup returned the
      // mints — so they had nothing to fetch and the amount row rendered with
      // no balance and no Max. Re-fire them now that the pair is known.
      this.inputBalance.set(null);
      this.secondaryBalance.set(null);
      if (this.inputBalanceMint()) void this.loadInputBalance();
      if (this.secondaryBalanceMint()) void this.loadSecondaryBalance();
    } catch {
      // Enrichment is cosmetic — the action still builds from the address.
    }

    // A close returns liquidity, fees and several rent refunds at once, and
    // which accounts get closed is the program's business, not ours. Adding up
    // the ones we know about left the figure 15% under what the wallet then
    // offered. Ask the builder, which simulates and reports the wallet's own
    // net change.
    if (this.isMeteoraClose()) {
      try {
        const built = await firstValueFrom(
          this.apiService.post<{ data?: { netSolChange?: number; receiveB?: number } }>(
            '/actions/build',
            { type: this.action!.type, params: { position } },
          ).pipe(timeout(20_000)),
        );
        const net = built?.data?.netSolChange;
        if (typeof net === 'number' && Number.isFinite(net)) {
          this.editParams.update(ep => ({ ...ep, closeNetSol: String(net) }));
        }
      } catch {
        // Falls back to amounts + rent, which is close enough to be useful.
      }
    }
  }

  async maybeEnrichRaydiumWithdraw(): Promise<void> {
    if (!this.isRaydiumWithdraw() && !this.isRaydiumIncrease() && !this.isRaydiumDecrease()) return;
    const p = this.editParams();
    const positionId = (p['positionId'] ?? '').trim();
    const poolId = (p['poolId'] ?? '').trim();
    if (!positionId && !poolId) return;
    // Already has what the panel needs.
    // The reduce panel needs the token amounts + the position total, so a card
    // carrying only ids/symbols still has to fetch.
    if (p['pair'] && p['amountA'] !== undefined) return;
    try {
      const resp = await firstValueFrom(
        this.apiService
          .post<any>('/actions/build', { type: 'raydium_get_user_positions', params: {} })
          .pipe(timeout(20_000)),
      );
      const data = resp?.data ?? resp?.preview?.params?.data;
      const positions: any[] = Array.isArray(data?.positions) ? data.positions : [];
      const match = positions.find(x =>
        positionId ? x?.positionId === positionId : (x?.kind === 'lp' && x?.poolId === poolId),
      );
      if (!match) return;
      this.editParams.update(ep => ({
        ...ep,
        pair: ep['pair'] || match.pair || '',
        tokenA: ep['tokenA'] || match.mintA?.address || '',
        tokenB: ep['tokenB'] || match.mintB?.address || '',
        // Increase needs a deposit side; default to token A once we know it.
        ...(this.isRaydiumIncrease() && !ep['inputMint'] && match.mintA?.address
          ? { inputMint: match.mintA.address } : {}),
        tokenASymbol: ep['tokenASymbol'] || match.mintA?.symbol || '',
        tokenBSymbol: ep['tokenBSymbol'] || match.mintB?.symbol || '',
        ...(match.mintA?.logoURI && !ep['tokenALogo'] ? { tokenALogo: match.mintA.logoURI } : {}),
        ...(match.mintB?.logoURI && !ep['tokenBLogo'] ? { tokenBLogo: match.mintB.logoURI } : {}),
        ...(match.amountA !== undefined ? { amountA: String(match.amountA) } : {}),
        ...(match.amountB !== undefined ? { amountB: String(match.amountB) } : {}),
        // The position's TOTAL liquidity goes in its own key: on a decrease,
        // `liquidity` is the user's requested withdrawal amount and must not be
        // overwritten with the position's full size.
        ...(match.liquidity ? { positionLiquidity: String(match.liquidity) } : {}),
        ...(match.lpAmount !== undefined ? { positionLpAmount: String(match.lpAmount) } : {}),
      }));
      // The mints only became known just now, so the balance lines (and the
      // increase card's paired-side check) need a load.
      if (this.inputBalanceMint() && this.inputBalance() === null) void this.loadInputBalance();
      if (this.secondaryBalanceMint() && this.secondaryBalance() === null) void this.loadSecondaryBalance();
    } catch {
      // Leave the card as-is — it still submits fine on the id alone.
    }
  }

  /**
   * Resolve a token PAIR (from the CLARIFY "pick a pair" path) to a concrete
   * Raydium pool when no poolId was supplied. Uses the same
   * `raydium_search_pools` endpoint the pool-list QueryCard uses, takes the
   * highest-liquidity match, and seeds `poolId` + the token mints into
   * editParams. Returns the resolved poolId (or '' if it can't resolve), after
   * which `maybeEnrichRaydiumPool` fetches the pool's price/symbols by id.
   */
  private async resolveRaydiumPoolFromPair(): Promise<string> {
    const p = this.editParams();
    const mintA = this.toMintAddress(p['tokenA'] ?? p['tokenASymbol'] ?? '');
    const mintB = this.toMintAddress(p['tokenB'] ?? p['tokenBSymbol'] ?? '');
    if (!mintA || !mintB) return '';
    const poolType = this.action.type === 'raydium_add_liquidity' ? 'standard' : 'concentrated';
    try {
      const resp = await firstValueFrom(
        this.apiService
          .post<any>('/actions/build', {
            type: 'raydium_search_pools',
            params: { tokenA: mintA, tokenB: mintB, poolType, sortField: 'liquidity', page: 1, pageSize: 1 },
          })
          .pipe(timeout(12_000)),
      );
      const rows = resp?.preview?.params?.data?.data;
      const pool = Array.isArray(rows) ? rows[0] : null;
      if (!pool?.id) return '';
      this.editParams.update(ep => ({
        ...ep,
        poolId: pool.id,
        tokenA: pool.mintA?.address ?? mintA,
        tokenB: pool.mintB?.address ?? mintB,
      }));
      return String(pool.id);
    } catch {
      return '';
    }
  }

  /** Resolve a raw token identifier (mint address OR symbol) to a mint
   *  address via the token registry. Returns '' when it can't be resolved. */
  private toMintAddress(raw: string): string {
    const v = (raw ?? '').trim();
    if (!v) return '';
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(v)) return v; // already a mint
    const tok = this.tokenRegistry.getBySymbol(v);
    return tok?.address ?? '';
  }

  /**
   * Fetch Raydium CLMM pool details when the LLM emitted only `poolId` +
   * `inputMint` + `inputAmount` (no `tokenASymbol` / `tokenBSymbol` /
   * `currentPrice`). Without these, the form shows generic "Token A" / "Token B"
   * labels and the ratio engine has no anchor price.
   *
   * The QueryCard path (`useRaydiumPool` in query-card.component.ts) already
   * fills these in directly from the row data, so the fetch is a no-op there.
   */
  async maybeEnrichRaydiumPool(): Promise<void> {
    if (this.action?.type !== 'raydium_open_position' && this.action?.type !== 'raydium_add_liquidity') return;
    let poolId = (this.editParams()['poolId'] ?? '').trim();
    // The CLARIFY "pick a pair" path (SOL/USDC …) spawns this card with only
    // tokenA/tokenB and NO poolId — so historically enrichment early-returned
    // here, leaving the form with generic "Token A/B" labels, no current price,
    // dead range presets, and a Max that dumped the full SOL balance. Resolve
    // the pair to its highest-liquidity CLMM pool first so the rest of the
    // enrichment (current price, symbols, ratio-aware Max) can run.
    if (!poolId) {
      poolId = await this.resolveRaydiumPoolFromPair();
      if (!poolId) {
        // No pool for this pair — surface it so the pair chooser shows a clean
        // message instead of an infinite "resolving" state.
        if (this.clmmBothTokensPicked()) {
          this.clmmPairError.set('No Raydium CLMM pool exists for this pair yet. Pick a different pair.');
        }
        return;
      }
    }
    const p = this.editParams();
    // ALWAYS strip the LLM's raw `inputMint`/`inputAmount` before anything
    // else — they have no business in the CLMM form's editParams. Leaving
    // `inputMint='USDC'` (a literal symbol) in place makes the form's
    // `inputBalanceMint()` chain resolve BOTH rows to the same token
    // (USDC's mint), so both rows show USDC's icon and balance. The
    // submission path (`solana-action.service.ts → raydium_open_position`)
    // re-derives inputMint from whichever amountA/amountB is non-zero, so
    // dropping them here doesn't break the build payload.
    if (p['inputMint'] !== undefined || p['inputAmount'] !== undefined) {
      const inputMintRaw = (p['inputMint'] ?? '').trim();
      const inputAmountRaw = (p['inputAmount'] ?? '').trim();
      const cleaned = { ...p };
      delete cleaned['inputMint'];
      delete cleaned['inputAmount'];
      // Route the LLM's "4 USDC" into amountA/amountB if symbols are
      // already known (draft restore path). The async enrichment below
      // handles the cold-start path separately.
      if (inputMintRaw && inputAmountRaw && cleaned['tokenASymbol']) {
        const upper = inputMintRaw.toUpperCase();
        const isA =
          inputMintRaw === cleaned['tokenA'] ||
          upper === (cleaned['tokenASymbol'] || '').toUpperCase();
        if (isA && !cleaned['amountA']) { cleaned['amountA'] = inputAmountRaw; this.dlmmLastEdited.set('A'); }
        else if (!isA && !cleaned['amountB']) { cleaned['amountB'] = inputAmountRaw; this.dlmmLastEdited.set('B'); }
      }
      this.editParams.set(cleaned);
      // Re-fire balance loaders since inputBalanceMint() now resolves
      // to tokenA (not the deleted inputMint) — different mint → fresh fetch.
      this.inputBalance.set(null);
      this.secondaryBalance.set(null);
      if (this.inputBalanceMint()) this.loadInputBalance();
      if (this.secondaryBalanceMint()) this.loadSecondaryBalance();
    }
    // "Already enriched" needs the RATIO ANCHOR too, not just the symbols. When
    // the emitter supplied tokenASymbol/tokenBSymbol but no price, returning
    // here left `amountRatio`/`currentPrice` unset — so the ratio engine was
    // dead and Max on one side filled the full balance while the other stayed 0.
    const _ep = this.editParams();
    if (_ep['tokenASymbol'] && _ep['tokenBSymbol'] && (_ep['currentPrice'] || _ep['amountRatio'])) return;
    if (this._enrichedRaydiumPool === poolId) return; // tried once
    this._enrichedRaydiumPool = poolId;
    try {
      // Use the gateway's `/actions/build` raydium_get_pool_info — same
      // response shape the QueryCard's pool rows use (mintA/mintB carry
      // `{address, symbol, decimals, logoURI}` objects, not just strings).
      const resp = await firstValueFrom(
        this.apiService.post<any>('/actions/build', {
          type: 'raydium_get_pool_info',
          params: { ids: poolId },
        }),
      );
      const rows = resp?.preview?.params?.data;
      const pool = Array.isArray(rows) ? rows[0] : null;
      if (!pool) return;
      const patched = { ...this.editParams() };
      const mintA = pool.mintA ?? {};
      const mintB = pool.mintB ?? {};
      if (!patched['tokenA']) patched['tokenA'] = mintA.address ?? '';
      if (!patched['tokenB']) patched['tokenB'] = mintB.address ?? '';
      if (!patched['tokenASymbol']) patched['tokenASymbol'] = mintA.symbol ?? 'A';
      if (!patched['tokenBSymbol']) patched['tokenBSymbol'] = mintB.symbol ?? 'B';
      if (!patched['tokenADecimals']) patched['tokenADecimals'] = String(mintA.decimals ?? 9);
      if (!patched['tokenBDecimals']) patched['tokenBDecimals'] = String(mintB.decimals ?? 9);
      const price = typeof pool.price === 'number' ? pool.price : parseFloat(pool.price ?? '');
      if (!patched['currentPrice'] && Number.isFinite(price) && price > 0) {
        patched['currentPrice'] = String(price);
        if (this.action.type === 'raydium_add_liquidity') {
          // Standard AMM: no range — the deposit ratio is simply the pool price
          // (mintB per mintA). Feed it as amountRatio so the auto-balance fills
          // the paired side on Max / single-side entry.
          if (!patched['amountRatio']) patched['amountRatio'] = String(price);
        } else {
          // CLMM: stable-stable pairs trade in a ~1% band; ±20% creates a
          // position whose ticks fall outside the pool's observation arrays and
          // the open call reverts. Default to ±1% for stables. Pair detection is
          // driven by TokenRegistry so any future USD-stable works unchanged.
          const symA = patched['tokenASymbol'] ?? mintA.address ?? '';
          const symB = patched['tokenBSymbol'] ?? mintB.address ?? '';
          const isStablePair = this.tokenRegistry.isStable(symA) && this.tokenRegistry.isStable(symB);
          const factor = isStablePair ? 0.01 : 0.2;
          if (!patched['minPrice']) patched['minPrice'] = (price * (1 - factor)).toPrecision(6);
          if (!patched['maxPrice']) patched['maxPrice'] = (price * (1 + factor)).toPrecision(6);
        }
      }
      // Single-sided input from the LLM ("4 USDC") → drop the user-supplied
      // amount into whichever side `inputMint` resolves to, then DELETE the
      // raw inputMint/inputAmount keys. The form's balance/icon resolvers
      // (`inputBalanceMint`, `secondaryBalanceMint`) read `inputMint` before
      // falling through to `tokenA`/`tokenB` — if we leave `inputMint='USDC'`
      // in place, BOTH rows end up resolving to USDC's mint and the user
      // sees identical icons and only one balance line.
      // The submission path (`solana-action.service.ts → raydium_open_position`)
      // already re-derives inputMint/inputAmount from whichever amount side
      // is non-zero, so dropping these here doesn't break the build payload.
      const inputMint = (this.action.params['inputMint'] ?? '').trim();
      const inputAmount = (this.action.params['inputAmount'] ?? '').trim();
      let prefilledSide: 'A' | 'B' | null = null;
      if (inputMint && inputAmount) {
        const upper = inputMint.toUpperCase();
        const isA =
          inputMint === patched['tokenA'] ||
          upper === (patched['tokenASymbol'] || '').toUpperCase();
        if (isA && !patched['amountA']) { patched['amountA'] = inputAmount; prefilledSide = 'A'; }
        else if (!isA && !patched['amountB']) { patched['amountB'] = inputAmount; prefilledSide = 'B'; }
        delete patched['inputMint'];
        delete patched['inputAmount'];
      }
      this.editParams.set(patched);
      // The balance loaders fired in ngOnInit before this async enrichment
      // landed. At that point inputMint was still the literal "USDC" so
      // loadInputBalance fetched USDC's balance into the primary signal,
      // and tokenB was empty so loadSecondaryBalance never ran. Re-fire
      // both now that the mints are correct.
      this.inputBalance.set(null);
      this.secondaryBalance.set(null);
      if (this.inputBalanceMint()) this.loadInputBalance();
      if (this.secondaryBalanceMint()) this.loadSecondaryBalance();
      // Kick the auto-balance ratio engine so the OTHER side fills in
      // immediately. Without this the user sees "4 USDC" but USDS stays
      // empty until they touch a field manually.
      if (prefilledSide) this.dlmmLastEdited.set(prefilledSide);
    } catch (err) {
      console.warn('[raydium-clmm] pool enrichment failed', err);
    }
  }

  /**
   * Fetch the live LST/SOL rate for the conversion preview. Uses the protocol
   * provider's own service (Jito Kobe API + on-chain fallback for Jito,
   * Marinade indexer's `/msol/price_sol` for Marinade, Jupiter Quote v6 probe
   * for jupSOL) — never a static constant. On failure, sets `lstRateError` so
   * the UI shows an explicit "live rate unavailable" message and the user
   * can retry.
   */
  /**
   * Normalize an ExactOut swap action to its ExactIn equivalent at card mount,
   * by pre-quoting Jupiter for the required input amount.
   *
   * Why: the LLM emits ExactOut for "buy 5 USDC for SOL" — semantically that's
   * "receive 5 USDC, input floats". But every DEX UI (Jupiter/Raydium/Orca)
   * shows the trade as "spend X SOL → receive Y USDC" with the input pinned.
   * Users see `BUY AMOUNT: 5 USDC` next to `PAYING WITH: SOL` and read it as
   * "this trade costs 5 USDC", which is wrong and undermines trust.
   *
   * Fix: at card open we ask Jupiter "how much SOL does 5 USDC out cost
   * right now?" and rewrite editParams in-place to ExactIn with the quoted
   * SOL amount. The card then renders as a normal ExactIn — amount in the
   * input token (SOL), receive estimate in the output (USDC).
   *
   * Market drift is normal DEX behavior — final amounts are determined by
   * the route quote computed at sign time, not at card open.
   */
  readonly exactOutNormalizing = signal(false);

  /**
   * Live counterparty estimate for a swap: the *other* side of the trade
   * computed from a Jupiter quote, so the user sees BOTH legs (Raydium-style
   * dual-amount feel) without needing to type into a second input.
   *
   * Updated by `_swapEstimateEffect` whenever editParams.amount /
   * inputMint / outputMint / swapMode changes, debounced ~400 ms so we
   * don't fan out quote calls on every keystroke.
   */
  readonly swapEstimate = signal<{
    counterUi: string;        // formatted counterparty amount (e.g. "5.0234")
    counterSymbol: string;    // counter token symbol
    counterIsOutput: boolean; // true if counter is the output side (ExactIn)
    pricePerInput: number;    // output per 1 input
    priceImpactPct: number;   // Jupiter quote priceImpactPct × 100 (e.g. 3.2 = 3.2%)
    platformFeeBps: number;   // what this trade actually pays OPRAI
  } | null>(null);

  /**
   * What each side of a swap is worth in dollars, and how far apart they are.
   *
   * A token amount is not a quantity anyone can judge — "877.276.509282 CATE"
   * says nothing about whether the trade is good. The dollar figure on each
   * side does, and the gap between them is the real cost of the swap: route
   * quality, price impact and fees all land in that one number, which is why
   * Jupiter puts it there instead of the raw price-impact percentage.
   */
  readonly swapPayUsdPrice = signal<number | null>(null);
  readonly swapRecvUsdPrice = signal<number | null>(null);
  private readonly _swapUsdPriceEffect = effect(() => {
    const pay = this.resolveToMint(this.getEditParam('inputMint'));
    const recv = this.resolveToMint(this.getEditParam('outputMint'));
    const t = this.action?.type;
    if (t !== 'swap' && t !== 'raydium_swap') return;
    // A settled card reads its dollars from the receipt, so there is nothing
    // to price.
    if (!this.isEditable()) return;
    untracked(() => {
      this.swapPayUsdPrice.set(null);
      this.swapRecvUsdPrice.set(null);
      if (pay) void this.priceFeed.getPrice(pay).then(p => this.swapPayUsdPrice.set(p));
      if (recv) void this.priceFeed.getPrice(recv).then(p => this.swapRecvUsdPrice.set(p));
    });
  });

  /**
   * The amount on one side of the swap, as a number.
   *
   * Read from the quote's own figures rather than the text in the box. The
   * displayed value is formatted for a human — "877.276,509282" under a
   * Turkish locale — and parsing that back gave 877.276, which turned a
   * fair trade into a "-100%" loss on the card.
   */
  private sideAmount(side: 'inputMint' | 'outputMint'): number | null {
    if (!this.isEditable() && this.executedSwapView()) {
      const done = side === 'inputMint'
        ? this.executedSwapView()!.pay
        : this.executedSwapView()!.receive;
      const n = parseFloat(String(done ?? '').replace(/[^0-9.eE-]/g, ''));
      return Number.isFinite(n) && n > 0 ? n : null;
    }
    const p = this.editParams();
    const mode = String(p['swapMode'] ?? '').toLowerCase();
    const exactOut = mode === 'exactout' || mode === 'out';
    const typed = parseFloat(p['amount'] ?? '');
    if (!Number.isFinite(typed) || typed <= 0) return null;

    const isTypedSide = (side === 'inputMint' && !exactOut) || (side === 'outputMint' && exactOut);
    if (isTypedSide) return typed;

    // The other side comes from the quote's ratio, which is a number.
    const rate = this.swapEstimate()?.pricePerInput;
    if (!rate || !Number.isFinite(rate) || rate <= 0) return null;
    return exactOut ? typed / rate : typed * rate;
  }

  private sideUsd(side: 'inputMint' | 'outputMint'): number | null {
    // A finished trade is a receipt. Show what it was worth when it happened,
    // not what it would be worth now.
    const frozen = this.executedSwapView();
    if (!this.isEditable() && frozen) {
      const v = side === 'inputMint' ? frozen.payUsd : frozen.recvUsd;
      return v ?? null;
    }
    const price = side === 'inputMint' ? this.swapPayUsdPrice() : this.swapRecvUsdPrice();
    if (!price || price <= 0) return null;
    const amt = this.sideAmount(side);
    if (amt === null) return null;
    return amt * price;
  }

  readonly swapPayUsd = computed(() => this.sideUsd('inputMint'));
  readonly swapRecvUsd = computed(() => this.sideUsd('outputMint'));

  /** How much value the swap costs, as a percentage of what goes in. */
  readonly swapUsdDeltaPct = computed(() => {
    const pay = this.swapPayUsd();
    const recv = this.swapRecvUsd();
    if (pay === null || recv === null || pay <= 0) return null;
    const delta = ((recv - pay) / pay) * 100;
    // A figure this extreme is a pricing failure, not a trade. Illiquid and
    // brand-new tokens routinely have no usable price, and reporting that as
    // "you will lose 100%" is worse than saying nothing: it is confidently
    // wrong about the user's money.
    if (!Number.isFinite(delta) || delta < -90 || delta > 90) return null;
    return delta;
  });

  /** Beyond this the swap is losing real money, and it should look like it. */
  readonly swapUsdDeltaSevere = computed(() => {
    const d = this.swapUsdDeltaPct();
    return d !== null && d <= -5;
  });

  /** "1 USDC ≈ 53,051.89 CATE" — the exchange rate this quote implies. */
  readonly swapRateLine = computed(() => {
    const est = this.swapEstimate();
    if (!est || !(est.pricePerInput > 0)) return null;
    const pay = this.resolveTokenDisplay(this.getEditParam('inputMint')).symbol;
    const recv = this.resolveTokenDisplay(this.getEditParam('outputMint')).symbol;
    if (!pay || !recv) return null;
    const rate = est.pricePerInput;
    const shown = rate >= 1
      ? rate.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : rate.toLocaleString(undefined, { maximumFractionDigits: 8 });
    return `1 ${pay} ≈ ${shown} ${recv}`;
  });

  /** What this particular trade pays OPRAI, as the quote priced it. */
  readonly swapPlatformFeePct = computed(() => {
    const bps = this.swapEstimate()?.platformFeeBps ?? 0;
    return bps > 0 ? bps / 100 : 0;
  });

  formatUsdCompact(v: number | null): string {
    if (v === null) return '';
    if (v >= 1000) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    if (v >= 1) return `$${v.toFixed(2)}`;
    return `$${v.toFixed(4)}`;
  }

  // Separate from `swapEstimate=null` (which also covers "not quoted yet" /
  // "inputs missing"). True only after a quote attempt with valid inputs
  // came back as "no route" — Jupiter literally has no path between these
  // mints. Signals a dead pair, drives the danger banner.
  readonly swapNoRoute = signal(false);

  /**
   * Frozen pay/receive amounts captured at the moment of submit. Once a swap is
   * executed the card must keep showing *what was actually swapped* — not the
   * live `swapInputValueFor()` value, which re-reads the (now post-swap) balance
   * and last quote and would otherwise drift to the remaining balance. Rendered
   * only when the card is no longer editable (submitted / confirmed).
   */
  readonly executedSwapView = signal<{ pay: string; receive: string; payUsd?: number; recvUsd?: number } | null>(null);

  /** The exact (user-edited) params handed to the last submit. Used by the
   *  async confirmation callbacks so they persist what was submitted rather
   *  than the pre-edit `this.action.params`. */
  private lastSubmittedParams: Record<string, string> | null = null;

  /** Frozen swap pay/receive captured at submit, persisted with the result. */
  private lastSwapView: { pay: string; receive: string; payUsd?: number; recvUsd?: number } | null = null;

  /** Re-arms the execute() stall timeout; set per-run, called on each progress event. */
  private resetStallTimeout: () => void = () => {};
  /** Set once a bridge's deposit is away: from then on there is nothing to
   *  make progress, only something to wait for. */
  private stallWatchOff = false;

  /**
   * Severity tier for the live quote's price impact. Drives both the warning
   * banner colour in the card and whether the Swap button is hard-gated.
   *
   *   safe     → no badge, no banner. Default "good route" path.
   *   notice   → yellow banner, button enabled. Inform but don't block.
   *   warning  → orange banner, button enabled with extra confirmation copy.
   *   danger   → red banner, button DISABLED. Pair has no/awful liquidity
   *              and signing would be self-harm; user must change tokens.
   *
   * Thresholds match what most DEX UIs (Jupiter, Raydium, Orca) use as
   * warning/danger cutoffs.
   */
  readonly swapImpactTier = computed<'safe' | 'notice' | 'warning' | 'danger'>(() => {
    if (this.swapNoRoute()) return 'danger';
    const est = this.swapEstimate();
    if (!est) return 'safe';
    const p = est.priceImpactPct;
    if (!Number.isFinite(p)) return 'safe';
    if (p >= 10) return 'danger';
    if (p >= 5) return 'warning';
    if (p >= 1) return 'notice';
    return 'safe';
  });

  /** True when the swap should be blocked — extreme price impact or no route. */
  readonly swapBlockedByImpact = computed(() => this.swapImpactTier() === 'danger');

  private _swapEstimateTimer: ReturnType<typeof setTimeout> | null = null;
  private _swapEstimateSeq = 0;

  /**
   * Auto-refresh polling for swap quotes while the card is pending. Spec:
   *  - 10 s interval (Jupiter/Raydium/Phantom standard)
   *  - Only when action.type === 'swap' AND status === 'pending'
   *  - Only while the tab is visible — `document.hidden` pauses
   *  - Edit (amount/mint/mode change) resets the countdown
   *  - Hard lifetime cap: 90 s from card open; if the user hasn't acted
   *    by then, polling stops to avoid background spam
   *  - On manual refresh / visibility-resume, fires immediately and resets
   */
  readonly POLL_INTERVAL_S = 3;
  private readonly POLL_LIFETIME_MS = 60_000;
  readonly quoteCountdown = signal<number | null>(null); // null = polling not active
  private _pollIntervalId: ReturnType<typeof setInterval> | null = null;
  // Visible-elapsed timer — hidden tab seconds don't count toward the cap.
  // Wall-clock would expire the polling silently in the background, then
  // the user returns to find a dead card with no fresh quote.
  private _pollVisibleElapsedMs = 0;
  private _pollSecondsSinceQuote = 0;
  private _visibilityHandler: (() => void) | null = null;

  private readonly _swapEstimateEffect = effect(() => {
    // Touch the signals we want to track. Effect re-runs on any change.
    const params = this.editParams();
    const actionType = this.action?.type;
    // orca_swap executes through Jupiter restricted to Whirlpool venues, so
    // the aggregator IS its quote source — it just has to be asked with the
    // same restriction. The DLMM / DAMM swaps build their own transactions
    // and are not quotable this way.
    if (actionType !== 'swap' && actionType !== 'raydium_swap' && actionType !== 'orca_swap') {
      this.swapEstimate.set(null);
      return;
    }
    const inMintRaw = (params['inputMint'] ?? '').trim();
    const outMintRaw = (params['outputMint'] ?? '').trim();
    const amtRaw = (params['amount'] ?? '').trim();
    const mode = String(params['swapMode'] ?? '').toLowerCase();
    if (!inMintRaw || !outMintRaw || !amtRaw) { this.swapEstimate.set(null); return; }
    const amt = parseFloat(amtRaw);
    if (!Number.isFinite(amt) || amt <= 0) { this.swapEstimate.set(null); return; }

    // Debounce — bump the seq so any in-flight quote resolves into a no-op.
    const seq = ++this._swapEstimateSeq;
    if (this._swapEstimateTimer) clearTimeout(this._swapEstimateTimer);
    this._swapEstimateTimer = setTimeout(() => {
      this.fetchSwapEstimate(seq, inMintRaw, outMintRaw, amt, mode);
    }, 400);

    // Edit IS interaction — full lifetime reset, not just the per-tick
    // countdown. Otherwise editing at t=55s only buys you 5 more seconds of
    // live quotes, which is hostile UX.
    if (this._pollIntervalId !== null) {
      this._pollVisibleElapsedMs = 0;
      this._pollSecondsSinceQuote = 0;
      this.quoteCountdown.set(this.POLL_INTERVAL_S);
    }
  });

  // Start / stop polling based on action type + card status. Effect re-runs
  // whenever `status()` changes, so submit / cancel / error all stop it.
  private readonly _quotePollLifecycleEffect = effect(() => {
    const isPollable = this.action?.type === 'swap' || this.action?.type === 'raydium_swap'
      || this.action?.type === 'orca_swap' || this.pumpActionConfig() !== null;
    const shouldPoll = isPollable && this.status() === 'pending';
    if (shouldPoll) this.startQuotePolling();
    else this.stopQuotePolling();
  });

  /**
   * Bound to a click on the card root. Re-arms the polling lifetime so an
   * idle-but-pending card the user re-engages with starts fetching fresh
   * quotes again. No-op when the card isn't in a polling-eligible state
   * (not a swap, or status != pending).
   */
  onCardInteract(): void {
    const eligible = (this.action?.type === 'swap' || this.action?.type === 'raydium_swap' || this.pumpActionConfig() !== null)
                     && this.status() === 'pending';
    if (!eligible) return;
    this._pollVisibleElapsedMs = 0;
    if (this._pollIntervalId === null) {
      // Polling had expired — start fresh AND fire an immediate quote so the
      // user doesn't have to wait 3s after re-engaging the card.
      this.startQuotePolling();
      this.refreshQuoteSilently();
    } else {
      // Already running — just reset the per-tick counter so the user sees
      // a full 3s before the next quote, not a stale partial countdown.
      this._pollSecondsSinceQuote = 0;
      this.quoteCountdown.set(this.POLL_INTERVAL_S);
    }
  }

  private startQuotePolling(): void {
    if (this._pollIntervalId !== null) return;
    this._pollVisibleElapsedMs = 0;
    this._pollSecondsSinceQuote = 0;
    this.quoteCountdown.set(this.POLL_INTERVAL_S);

    this._pollIntervalId = setInterval(() => this._pollTick(), 1000);

    // Visibility: pause countdown while the tab is hidden, fire an immediate
    // refresh when it comes back so the user never sees a stale number on
    // tab-switch return.
    if (typeof document !== 'undefined' && !this._visibilityHandler) {
      this._visibilityHandler = () => {
        if (!document.hidden && this._pollIntervalId !== null) {
          this._pollSecondsSinceQuote = 0;
          this.quoteCountdown.set(this.POLL_INTERVAL_S);
          this.refreshQuoteSilently();
        }
      };
      document.addEventListener('visibilitychange', this._visibilityHandler);
    }
  }

  private stopQuotePolling(): void {
    if (this._pollIntervalId !== null) {
      clearInterval(this._pollIntervalId);
      this._pollIntervalId = null;
    }
    if (this._visibilityHandler && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this._visibilityHandler);
      this._visibilityHandler = null;
    }
    this.quoteCountdown.set(null);
  }

  private _pollTick(): void {
    // Hidden tab = paused. Lifetime check must come AFTER this so background
    // seconds don't burn down the user's quota of fresh quotes.
    if (typeof document !== 'undefined' && document.hidden) return;

    this._pollVisibleElapsedMs += 1000;
    if (this._pollVisibleElapsedMs > this.POLL_LIFETIME_MS) {
      this.stopQuotePolling();
      return;
    }

    this._pollSecondsSinceQuote++;
    const remaining = this.POLL_INTERVAL_S - this._pollSecondsSinceQuote;
    this.quoteCountdown.set(Math.max(0, remaining));
    if (this._pollSecondsSinceQuote >= this.POLL_INTERVAL_S) {
      this._pollSecondsSinceQuote = 0;
      this.quoteCountdown.set(this.POLL_INTERVAL_S);
      this.refreshQuoteSilently();
      // The balance rides the same tick rather than getting a timer of its
      // own: it then inherits the 60-second lifetime and the hidden-tab pause
      // for free, and can never outlive the quote it sits next to.
      this.refreshBalancesSilently();
    }
  }

  /**
   * Re-read the wallet balance without the loading state.
   *
   * `loadInputBalance` flips a spinner, which is right when the user changed
   * the token and wrong three seconds into staring at the card — it would
   * blink "···" over a number that is almost always identical. The value is
   * simply replaced when it arrives.
   */
  private refreshBalancesSilently(): void {
    if (!this.walletService.publicKey()) return;
    const noop = () => {};
    if (this.inputBalanceMint()) {
      void this.fetchBalanceFor(this.inputBalanceMint(), v => this.inputBalance.set(v), noop);
    }
    if (this.secondaryBalanceMint()) {
      void this.fetchBalanceFor(this.secondaryBalanceMint(), v => this.secondaryBalance.set(v), noop);
    }
  }

  private async refreshQuoteSilently(): Promise<void> {
    // Route pump.fun / PumpSwap cards through their own estimator.
    if (this.pumpActionConfig()) { await this.refreshPumpQuoteSilently(); return; }
    const p = this.editParams();
    const inMintRaw = (p['inputMint'] ?? '').trim();
    const outMintRaw = (p['outputMint'] ?? '').trim();
    const amtRaw = (p['amount'] ?? '').trim();
    const mode = String(p['swapMode'] ?? '').toLowerCase();
    const amt = parseFloat(amtRaw);
    if (!inMintRaw || !outMintRaw || !Number.isFinite(amt) || amt <= 0) return;
    const seq = ++this._swapEstimateSeq;
    await this.fetchSwapEstimate(seq, inMintRaw, outMintRaw, amt, mode);
  }

  private async fetchSwapEstimate(
    seq: number,
    inMintRaw: string,
    outMintRaw: string,
    amt: number,
    mode: string,
  ): Promise<void> {
    await this.tokenRegistry.ensureLoaded();
    if (seq !== this._swapEstimateSeq) return;
    const inT = this.tokenRegistry.getBySymbol(inMintRaw) ?? this.tokenRegistry.getToken(inMintRaw);
    const outT = this.tokenRegistry.getBySymbol(outMintRaw) ?? this.tokenRegistry.getToken(outMintRaw);
    if (!inT || !outT) return;
    const isExactOut = mode === 'exactout' || mode === 'out';
    const swapMode: 'ExactIn' | 'ExactOut' = isExactOut ? 'ExactOut' : 'ExactIn';
    try {
      // Raydium swaps quote from Raydium's own venue (same DEX that executes);
      // everything else uses the Jupiter aggregated quote.
      // Each venue-scoped swap is quoted by the venue that will execute it —
      // Raydium from Raydium, Orca from the Whirlpool program via its SDK.
      // Only the aggregated `swap` goes to Jupiter, which is the venue there.
      const at = this.action?.type;
      const quote = at === 'raydium_swap'
        ? await this.swapService.getRaydiumQuote(
            inT.address, outT.address, String(amt), 50, swapMode,
          )
        : at === 'orca_swap'
        ? await this.orcaService.quoteSwap(
            inT.address, outT.address, String(amt), swapMode,
          )
        : await this.swapService.getQuote(
            inT.address, outT.address, String(amt), 50, swapMode,
          );
      if (seq !== this._swapEstimateSeq) return;
      if (!quote) {
        // Jupiter responded but found no route — distinct from "network
        // error" so we surface a deterministic "no liquidity" banner.
        this.swapNoRoute.set(true);
        this.swapEstimate.set(null);
        return;
      }
      this.swapNoRoute.set(false);
      const inAt = parseInt(quote.inAmount, 10);
      const outAt = parseInt(quote.outAmount, 10);
      if (!Number.isFinite(inAt) || !Number.isFinite(outAt) || inAt <= 0 || outAt <= 0) return;
      const inUi = inAt / Math.pow(10, inT.decimals ?? 9);
      const outUi = outAt / Math.pow(10, outT.decimals ?? 9);
      // Counter side = the OTHER side from the editable amount. ExactIn ⇒
      // amount is input, counter is output. ExactOut ⇒ amount is output,
      // counter is input.
      const counterUi = isExactOut ? inUi : outUi;
      const counterSymbol = isExactOut ? (inT.symbol ?? '') : (outT.symbol ?? '');
      const pricePerInput = inUi > 0 ? outUi / inUi : 0;
      // Jupiter returns priceImpactPct as a fraction string ("0.0032" = 0.32%).
      // Multiply to a percentage so downstream thresholds (1% / 5% / 10%) read
      // intuitively. Defensive parse so a missing field falls to 0 (safe tier)
      // rather than NaN (which the computed treats as safe anyway, but explicit).
      const rawImpact = parseFloat(String(quote.priceImpactPct ?? '0'));
      const priceImpactPct = Number.isFinite(rawImpact) ? rawImpact * 100 : 0;
      this.swapEstimate.set({
        counterUi: counterUi.toLocaleString(undefined, { maximumFractionDigits: 6 }),
        counterSymbol,
        counterIsOutput: !isExactOut,
        pricePerInput,
        priceImpactPct,
        // Read from the quote rather than re-deriving the rule here: the fee
        // is decided by the backend and priced in by Jupiter, and a second
        // copy of that logic on the client would drift the day either changes.
        // `opraiFeeBps` is attached by our backend and means the same thing on
        // both fee routes; `platformFee.feeBps` only exists on Jupiter's own,
        // and reading it alone made the card say "Free" on a trade that was
        // charging 0.5%.
        platformFeeBps: Number(
          (quote as { opraiFeeBps?: number; platformFee?: { feeBps?: number } }).opraiFeeBps
            ?? (quote as { platformFee?: { feeBps?: number } }).platformFee?.feeBps
            ?? 0,
        ),
      });
    } catch {
      // Quote failure (no route, network error, etc.) is itself a danger
      // signal — surface it through swapEstimate=null + we'll render a
      // "no liquidity for this pair" banner from that state in the template.
      if (seq === this._swapEstimateSeq) this.swapEstimate.set(null);
    }
  }

  async maybeNormalizeExactOutToExactIn(): Promise<void> {
    if (!this.action || this.action.type !== 'swap') return;
    const p = this.editParams();
    const mode = String(p['swapMode'] ?? '').toLowerCase();
    if (mode !== 'exactout' && mode !== 'out') return;

    const inputMintRaw = (p['inputMint'] ?? '').trim();
    const outputMintRaw = (p['outputMint'] ?? '').trim();
    const outAmountUi = (p['amount'] ?? '').trim();
    if (!inputMintRaw || !outputMintRaw || !outAmountUi) return;
    const outAmount = parseFloat(outAmountUi);
    if (!Number.isFinite(outAmount) || outAmount <= 0) return;

    await this.tokenRegistry.ensureLoaded();
    const inToken = this.tokenRegistry.getBySymbol(inputMintRaw) ?? this.tokenRegistry.getToken(inputMintRaw);
    const outToken = this.tokenRegistry.getBySymbol(outputMintRaw) ?? this.tokenRegistry.getToken(outputMintRaw);
    if (!inToken || !outToken) return;
    const inDecimals = inToken.decimals ?? 9;
    const outDecimals = outToken.decimals ?? 9;

    // Capture the params snapshot we're quoting against. If the user flips
    // direction (or otherwise mutates editParams) before the quote resolves,
    // we must NOT clobber their new state with stale results.
    //
    // The `amount` field is HUMAN-READABLE (e.g. "5") — the backend's
    // `parse_amount_to_base_units` multiplies by 10^decimals itself. Passing
    // pre-multiplied atomic ("5000000") would get re-multiplied to 5e12.
    const stake = {
      inMint: inToken.address,
      outMint: outToken.address,
      outAmount: String(outAmount),
    };

    this.exactOutNormalizing.set(true);
    try {
      const quote = await this.swapService.getQuote(
        stake.inMint, stake.outMint, stake.outAmount, 50, 'ExactOut',
      );
      if (!quote) return;
      const inAtomic = parseInt(quote.inAmount, 10);
      if (!Number.isFinite(inAtomic) || inAtomic <= 0) return;
      const inUi = inAtomic / Math.pow(10, inDecimals);
      const inUiStr = inUi.toFixed(Math.min(inDecimals, 9)).replace(/\.?0+$/, '');

      const live = this.editParams();
      const liveInRaw = (live['inputMint'] ?? '').trim();
      const liveOutRaw = (live['outputMint'] ?? '').trim();
      const liveIn = this.tokenRegistry.getBySymbol(liveInRaw) ?? this.tokenRegistry.getToken(liveInRaw);
      const liveOut = this.tokenRegistry.getBySymbol(liveOutRaw) ?? this.tokenRegistry.getToken(liveOutRaw);
      if (liveIn?.address !== stake.inMint || liveOut?.address !== stake.outMint) return;
      const liveMode = String(live['swapMode'] ?? '').toLowerCase();
      if (liveMode !== 'exactout' && liveMode !== 'out') return;

      this.editParams.update(prev => {
        const next = { ...prev };
        next['swapMode'] = 'ExactIn';
        next['amount'] = inUiStr;
        return next;
      });
    } catch {
      // Quote failed — leave the action as ExactOut. The card will still
      // submit correctly; we just couldn't pre-render the SOL-side preview.
    } finally {
      this.exactOutNormalizing.set(false);
    }
  }

  async maybeLoadLstRate(): Promise<void> {
    const cfg = this.lstActionConfig();
    if (!cfg) return;
    if (this.lstExchangeRate() !== null || this.lstRateLoading()) return;
    this.lstRateLoading.set(true);
    this.lstRateError.set(null);
    const sourceLabel = cfg.protocol === 'jito' ? 'Jito'
                      : cfg.protocol === 'marinade' ? 'Marinade'
                      : 'Jupiter';
    try {
      const rate = cfg.protocol === 'jito'     ? await this.jitoService.getExchangeRate()
                 : cfg.protocol === 'marinade' ? await this.marinadeService.getExchangeRate()
                 :                               await this.jupSolService.getExchangeRate();
      if (rate !== null && Number.isFinite(rate) && rate > 0) {
        this.lstExchangeRate.set(rate);
      } else {
        this.lstRateError.set(
          `Live ${cfg.lstSymbol}/SOL rate is currently unavailable from ${sourceLabel}.`
        );
      }
    } catch (e) {
      this.lstRateError.set(
        `Couldn't reach ${sourceLabel}'s rate API. Check your connection and retry.`
      );
    } finally {
      this.lstRateLoading.set(false);
    }
  }

  /** Stable key for persisting action results in message metadata. */
  private actionResultKey(): string {
    const sorted = Object.entries(this.action.params ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
    return `${this.action.type}:${JSON.stringify(sorted)}`;
  }

  private initFromAction(): void {
    // Restore confirmed/submitted/error state from DB-persisted result (survives page refresh).
    console.log('[initFromAction]', { type: this.action?.type, cachedResult: this.cachedResult, messageId: this.messageId });
    if (this.cachedResult) {
      this.status.set(this.cachedResult.status);
      if (this.cachedResult.txSignature) this.txSignature.set(this.cachedResult.txSignature);
      if (this.cachedResult.errorMessage) this.errorMessage.set(this.cachedResult.errorMessage);
      // Restore the created token's mint (contract) so it survives a page reload.
      const restoredMint = this.cachedResult.executedParams?.['mintPubkey'];
      if (restoredMint) this.createdMint.set(restoredMint);
      // Restore the frozen swap pay/receive so a re-hydrated card shows exactly
      // what was swapped (the edited amount), not a live/blank re-quote.
      if (this.cachedResult.swapView) this.executedSwapView.set(this.cachedResult.swapView);
      // Restore the frozen Jupiter Lend detail panel so a completed lend/withdraw
      // card still shows what was withdrawn + the rate/deposit — statically, with
      // no live re-fetch (the loaders are skipped below when cachedResult exists).
      if (this.cachedResult.lendSnapshot) {
        this.lendInfo.set(this.cachedResult.lendSnapshot as unknown as LendActionInfo);
      }
      // Restored "submitted" — start the elapsed ticker + auto re-check once,
      // so a page refresh after a network blip surfaces a recovery path.
      if (this.cachedResult.status === 'submitted' && this.cachedResult.txSignature) {
        this.startSubmittedTick();
        // Best-effort: poll once on restore; if the tx already confirmed
        // the recheck handler will flip status without user interaction.
        void this.recheckTxStatus();
      }
      // Fall through to populate form fields from action params (don't return early).
    }

    // Use the params that were actually submitted (executedParams) when restoring a completed action
    // so the history card shows what the user actually did, not the original LLM suggestion.
    const raw = this.cachedResult?.executedParams
      ? { ...this.action.params, ...this.cachedResult.executedParams }
      : this.action.params;
    // Normalize snake_case keys the LLM occasionally emits to the camelCase the frontend expects.
    // Also stringify number/boolean values that the backend preserves as native types.
    const p: Record<string, string> = Object.fromEntries(
      Object.entries(raw)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => [k, typeof v === 'string' ? v : String(v)])
    );
    if (p['input_mint'] && !p['inputMint'])   p['inputMint']   = p['input_mint'];
    if (p['output_mint'] && !p['outputMint']) p['outputMint']  = p['output_mint'];
    // Meteora aliases — the prompt teaches the LLM to emit `pool / amountX /
    // amountY` (per the [ACTION:…] grammar in solana_action_dex.txt), but the
    // form fields are keyed `poolId / amountA / amountB`. Without these
    // aliases an open_position / add_liquidity / add_to_position card opens
    // empty even though the LLM filled every required field.
    if (p['pool']    && !p['poolId'])  p['poolId']  = p['pool'];
    if (p['amountX'] && !p['amountA']) p['amountA'] = p['amountX'];
    if (p['amountY'] && !p['amountB']) p['amountB'] = p['amountY'];
    // Apply known-field sensible defaults *only when the LLM (or restored
    // executedParams) didn't already set the value*. Without this, swap /
    // LP / lend cards across Raydium / Jupiter / Orca / Meteora opened
    // with `slippage = 0`, which made every quote fail with
    // "slippage_exceeded" the second prices nudged. Defaults match the
    // placeholders shown in the field configs (0.5% slippage, 0.0005 SOL
    // priority fee) so the input cell is never blank-and-functional-as-zero.
    //
    // For `slippageBps` we store the bps integer (= percent × 100). The
    // input shows the divided value via `field.divisor`, so the same
    // 50-bps store appears as "0.5" in the UI.
    const FIELD_DEFAULTS: Record<string, string> = {
      slippageBps: '50',         // → 0.5 %
      slippage: '0.5',           // pump.fun / non-bps fields
      priorityFee: '0.0005',     // SOL
    };
    for (const [key, def] of Object.entries(FIELD_DEFAULTS)) {
      if (p[key] === undefined || p[key] === '' || p[key] === null) {
        p[key] = def;
      }
    }
    // A transfer's recipient under whichever name it arrived. The prompt says
    // `to`, but a model that has just been told a name could not be resolved
    // writes `recipient` or `destination` instead — and the card then opened
    // with an empty box under a message that had clearly named someone.
    if (this.action?.type === 'transfer' && !(p['to'] ?? '').trim()) {
      const alias = ['recipient', 'destination', 'address', 'toAddress']
        .map(k => (p[k] ?? '').trim())
        .find(v => !!v);
      if (alias) p['to'] = alias;
    }
    this.editParams.set({ ...p });
    // Limit order: seed the BUY-panel amount. The backend defines
    // targetPrice = OUTPUT tokens per INPUT token (takingAmount = amount ×
    // targetPrice), so buyAmount = sellAmount × targetPrice.
    if (this.action?.type === 'limit_order') {
      const amt = parseFloat(p['amount'] ?? '');
      const price = parseFloat(p['targetPrice'] ?? '');
      this.limitBuyAmount.set(
        Number.isFinite(amt) && amt > 0 && Number.isFinite(price) && price > 0
          ? String(+(amt * price).toFixed(9))
          : '');
    }
    // DCA: seed the frequency value + unit from the raw interval seconds.
    if (this.action?.type === 'dca') {
      this.seedDcaFrequency(parseInt(p['intervalSeconds'] ?? '', 10));
    }
    // Perp: the leverage slider is authoritative — clear any LLM sizeUsd override
    // and ensure a sane default leverage so the slider isn't empty.
    if (this.action?.type === 'perp_open') {
      if (!p['leverage']) this.setEditParam('leverage', '2');
      this.setEditParam('sizeUsd', '');
    }
    this.editName.set(p['name'] ?? p['tokenName'] ?? '');
    this.editSymbol.set(p['symbol'] ?? p['ticker'] ?? '');
    this.editDescription.set(p['description'] ?? '');
    // initialBuyAmount comes from LLM as 'initialBuyAmount'; legacy UI uses 'initialBuy'.
    // pump.fun buys are denominated in SOL. If the user gave a DOLLAR amount
    // ("$10 / 10 dolar initial buy") the LLM emits initialBuyAmountUsd — convert
    // it to the live SOL equivalent so the field shows the SOL actually spent,
    // not the raw dollar figure treated as SOL (10 SOL ≈ $770, not $10).
    const initBuySol = p['initialBuyAmount'] ?? p['initial_buy_amount'] ?? p['initialBuy'] ?? '';
    const initBuyUsdRaw = p['initialBuyAmountUsd'] ?? p['initial_buy_amount_usd'];
    const initBuyUsd = parseFloat(initBuyUsdRaw ?? '');
    if (!initBuySol && Number.isFinite(initBuyUsd) && initBuyUsd > 0) {
      this.initialBuyUsd.set(initBuyUsd);
      this.editInitialBuy.set(''); // hold until the live SOL price resolves
      void this.priceFeed.getPrice(this.SOL_MINT).then(solPrice => {
        if (solPrice && solPrice > 0) {
          // 4 dp keeps small USD buys readable ($10 ≈ 0.13 SOL) without noise.
          this.editInitialBuy.set((initBuyUsd / solPrice).toFixed(4));
        }
      });
    } else {
      this.initialBuyUsd.set(null);
      this.editInitialBuy.set(initBuySol);
    }
    this.editTwitter.set(p['twitter'] ?? '');
    this.editTelegram.set(p['telegram'] ?? '');
    this.editWebsite.set(p['website'] ?? '');
    this.editCashback.set(p['cashback'] === 'true' || p['cashback'] === true as any);
    // Tokenized Agent has no on-chain ix in our backend yet — force off regardless of LLM/draft.
    this.editTokenizedAgent.set(false);
    this.bannerUrl.set(p['bannerUrl'] ?? p['banner_url'] ?? p['banner'] ?? null);
    this.editSlippage.set(p['slippage'] ?? '10');
    this.editPriorityFee.set(p['priorityFee'] ?? '0.0005');

    // Restore image URL for display in submitted/confirmed state.
    // Priority: executedParams (survives page reload) → draft (same-session fallback).
    // uploadedImageUrl is a transient signal, not in action.params, so we must restore it explicitly.
    if (this.cachedResult) {
      const imgUrl = p['imageUrl'] ?? p['image'] ?? null;
      // Skip blob: URLs — they're session-specific and won't be valid after a page reload.
      if (imgUrl && !imgUrl.startsWith('blob:')) this.uploadedImageUrl.set(imgUrl);
    }
    if (!this.uploadedImageUrl() && this.sessionId && this.messageId) {
      try {
        const savedDraft = JSON.parse(localStorage.getItem(`draft:${this.sessionId}:${this.messageId}`) ?? 'null');
        if (savedDraft?.uploadedImageUrl != null) this.uploadedImageUrl.set(savedDraft.uploadedImageUrl);
        if (savedDraft?.uploadedBannerUrl != null) this.uploadedBannerUrl.set(savedDraft.uploadedBannerUrl);
      } catch {}
    }

    // Restore in-progress edits from localStorage for pending (not-yet-submitted) actions.
    // Skipped when cachedResult exists — the executed params already applied above are authoritative.
    if (!this.cachedResult && this.sessionId && this.messageId) {
      try {
        const draft = JSON.parse(localStorage.getItem(`draft:${this.sessionId}:${this.messageId}`) ?? 'null');
        if (draft) {
          if (draft.editInitialBuy != null) this.editInitialBuy.set(draft.editInitialBuy);
          if (draft.editName != null) this.editName.set(draft.editName);
          if (draft.editSymbol != null) this.editSymbol.set(draft.editSymbol);
          if (draft.editDescription != null) this.editDescription.set(draft.editDescription);
          if (draft.editTwitter != null) this.editTwitter.set(draft.editTwitter);
          if (draft.editTelegram != null) this.editTelegram.set(draft.editTelegram);
          if (draft.editWebsite != null) this.editWebsite.set(draft.editWebsite);
          if (draft.editCashback != null) this.editCashback.set(draft.editCashback);
          // Tokenized Agent intentionally not restored — backend rejects it.
          if (draft.editSlippage != null) this.editSlippage.set(draft.editSlippage);
          if (draft.editPriorityFee != null) this.editPriorityFee.set(draft.editPriorityFee);
          if (draft.editParams != null) {
            // Restore in-progress AMOUNT / slippage / settings edits — but NEVER
            // resurrect the swap's token IDENTITY from a stale draft. The token
            // the LLM proposed (in `p`, the authoritative action params) always
            // wins. Without this, a re-hydrated pending swap card could quote a
            // DIFFERENT token than the one the user asked for: the token picker
            // writes a raw mint into editParams, the draft effect persists it,
            // and on reload that stale mint (potentially an unverified junk
            // token) shadowed "SOL"/"USDC" — the card then priced a wrong asset.
            const draftParams = { ...(draft.editParams as Record<string, string>) };
            for (const k of ['inputMint', 'outputMint']) {
              if (p[k] != null) draftParams[k] = p[k];
              else delete draftParams[k];
            }
            this.editParams.set({ ...p, ...draftParams });
          }
        }
      } catch {}
    }

    this.tokenRegistry.ensureLoaded();

    // A completed card — restored from chat history with a cached result — is a
    // RECEIPT of an action that already succeeded. Its final state (executed
    // amount, tx signature, success) is already applied above. Never re-run the
    // live loaders for it: they'd flash "Loading protocol data...", re-fetch
    // balances/rates, and re-resolve "all", making a done transaction look like
    // it's about to run again. Only a still-pending card needs live data. This
    // applies to every mini-app (lend, kamino, staking, …).
    if (this.cachedResult) return;

    if (this.inputBalanceMint()) this.loadInputBalance();
    if (this.secondaryBalanceMint()) this.loadSecondaryBalance();
    if (['lend','withdraw_lend'].includes(this.action.type)) this.loadLendInfo();
    if (this.action.type === 'kamino_borrow' || this.action.type === 'kamino_repay'
        || this.action.type === 'kamino_withdraw' || this.action.type === 'kamino_withdraw_collateral') this.loadKaminoBorrowInfo();
    if (this.action.type === 'kamino_vault_deposit') this.loadKaminoVaultInfo();
    if (this.action.type === 'kamino_vault_withdraw' || this.action.type === 'kamino_unstake') {
      // kamino_unstake ("unstake my position") carries `amount`; the vault
      // withdraw card drives off `ktokenAmount`. Seed it so the input shows the
      // share figure (loadKaminoVaultWithdrawInfo then resolves "all" → number).
      if (this.action.type === 'kamino_unstake' && !this.getEditParam('ktokenAmount') && p['amount']) {
        this.setEditParam('ktokenAmount', p['amount']);
      }
      this.loadKaminoVaultWithdrawInfo();
    }
    if (this.isCloseAccounts()) void this.loadEmptyAccounts();
    if (this.action.type === 'native_stake') {
      // "the highest APY one" is a sort, and the model can say so. Without
      // this the card's default is the only order the user ever gets.
      const asked = (this.action.params?.['sortBy'] ?? '').toString().toLowerCase();
      if (asked === 'apy' || asked === 'stake' || asked === 'fee') this.validatorSort.set(asked);
      else if (asked === 'commission') this.validatorSort.set('fee');
      this.loadValidators();
    }
    // The stake-account actions need both: the accounts to choose from, and
    // the validator list to turn each account's vote address into a name.
    if (this.isStakeAccountAction()) {
      void this.loadStakeAccounts();
      this.loadValidators();
    }
    // Eagerly resolve "all" → actual balance for pumpfun/pumpswap sells so the
    // number input shows a real value instead of appearing blank.
    if ((this.action.type === 'pumpfun_sell' || this.action.type === 'pumpswap_sell')
        && p['amount'] === 'all' && !this.cachedResult) {
      const mint = p['mint'] ?? '';
      if (mint) {
        this.actionService.getTokenBalance(mint).then(bal => {
          if (bal > 0) this.editParams.update(ep => ({ ...ep, amount: bal.toString() }));
        }).catch(() => {});
      }
    }
  }

  async approve(): Promise<void> {
    // A background reload must not land between build and signature.
    this.appVersion.hold();
    try {
      await this.approveInner();
    } finally {
      this.appVersion.release();
    }
  }

  private async approveInner(): Promise<void> {
    // Stable-pair Raydium CLMM with a wide range almost always reverts on-chain
    // (tick range outside the pool's observation arrays). Auto-narrow to ±1%
    // BEFORE every submit — not only on retry — so the first attempt succeeds.
    // The helper is a no-op when the pair isn't stable or range is already tight.
    this.maybeAutoTightenStableRange();

    // Freeze the swap's pay/receive as currently displayed so the confirmed
    // card keeps showing what was actually swapped (not the post-swap balance).
    // Persisted in the stored result too, so a re-hydrated card restores it.
    if (this.action?.type === 'swap' || this.action?.type === 'raydium_swap') {
      this.lastSwapView = {
        pay: this.swapInputValueFor('inputMint'),
        receive: this.swapInputValueFor('outputMint'),
        // Freeze the dollar figures alongside the amounts. Recomputing them
        // from a live price after the trade meant a completed card drifted
        // with the market — a memecoin ticking down a minute later made it
        // look as though the swap had lost money the instant it landed.
        payUsd: this.swapPayUsd() ?? undefined,
        recvUsd: this.swapRecvUsd() ?? undefined,
      };
      this.executedSwapView.set(this.lastSwapView);
    }

    this.status.set('quoting');
    const mergedParams: Record<string, string> = Object.fromEntries(
      Object.entries(this.editParams()).filter(([, v]) => v !== undefined),
    ) as Record<string, string>;
    if (this.isLaunchAction()) {
      mergedParams['name'] = this.editName();
      mergedParams['symbol'] = this.editSymbol().replace(/[^A-Za-z0-9]/g, '').toUpperCase();
      mergedParams['description'] = this.editDescription();
      // Normalise to the key expected by solana-action.service.ts extract.
      // editInitialBuy already holds the SOL amount (USD inputs were converted
      // to the live SOL equivalent), so drop the USD-only hint keys — the
      // backend deals purely in SOL.
      mergedParams['initialBuyAmount'] = this.editInitialBuy();
      delete mergedParams['initialBuyAmountUsd'];
      delete mergedParams['initial_buy_amount_usd'];
      mergedParams['twitter'] = this.editTwitter();
      mergedParams['telegram'] = this.editTelegram();
      mergedParams['website'] = this.editWebsite();
      mergedParams['cashback'] = String(this.editCashback());
      // Tokenized Agent: backend has no ix wired up — always send false.
      mergedParams['tokenizedAgent'] = 'false';
      const publicBanner = this.effectiveBannerUrl();
      if (publicBanner && /^https?:\/\//i.test(publicBanner)) mergedParams['bannerUrl'] = publicBanner;
      if (this.publicImageUrl()) {
        mergedParams['imageUrl'] = this.publicImageUrl()!;
        // Keep legacy 'image' key for backward compat
        mergedParams['image'] = this.publicImageUrl()!;
      } else {
        delete mergedParams['imageUrl'];
        delete mergedParams['image'];
      }

      // Upload image + metadata to pump.fun IPFS so their indexer can always reach it.
      // Falls back to our own server metadata upload if IPFS upload fails.
      if (!mergedParams['metadataUri']) {
        const metaPayload = {
          name:        this.editName(),
          symbol:      this.editSymbol(),
          description: this.editDescription(),
          ...(this.editTwitter()       ? { twitter:  this.editTwitter()       } : {}),
          ...(this.editTelegram()      ? { telegram: this.editTelegram()      } : {}),
          ...(this.editWebsite()       ? { website:  this.editWebsite()       } : {}),
          ...(this.effectiveBannerUrl() ? { banner: this.effectiveBannerUrl()! } : {}),
        };

        // Prefer pump.fun IPFS when we have the raw file (guarantees external reachability).
        if (this.resizedImageFile) {
          try {
            const ipfsUri = await this.uploadService.uploadToPumpFunIpfs(
              this.resizedImageFile, metaPayload,
            );
            mergedParams['metadataUri'] = ipfsUri;
          } catch (ipfsErr: any) {
            console.warn('[launch_token] pump.fun IPFS upload failed, trying local metadata:', ipfsErr?.message);
          }
        }

        // Fallback: our own metadata endpoint (works in prod; localhost won't show on pump.fun).
        if (!mergedParams['metadataUri'] && this.publicImageUrl()) {
          try {
            const metaRes = await this.uploadService.uploadMetadata({
              ...metaPayload,
              image: this.publicImageUrl()!,
              ...(mergedParams['bannerUrl'] ? { banner: mergedParams['bannerUrl'] } : {}),
              showName: true,
            }).toPromise();
            if (metaRes?.url) mergedParams['metadataUri'] = metaRes.url;
          } catch (metaErr: any) {
            console.warn('[launch_token] metadata upload failed:', metaErr?.message);
          }
        }

        // Every route to a public address failed. Stop here.
        //
        // This used to fall through and launch anyway, using whatever was in
        // `imageUrl` — including a browser blob: URL. The transaction then
        // succeeded and produced a token that no indexer can describe, which
        // is worse than not launching, because a mint cannot be taken back.
        if (!mergedParams['metadataUri']) {
          throw new Error(
            'guard:Your token image could not be saved, so the launch was stopped before ' +
            'anything was signed. Re-upload the image and try again — launching now would ' +
            'create a token with no picture or name on pump.fun, and that cannot be fixed later.',
          );
        }
      }
    }
    // Kamino repay: when the user is clearing the whole debt, flag it so the
    // backend sends a repay-all sentinel. A fixed decimal can never match the
    // continuously-accruing debt exactly, so a "full" repay of a static amount
    // leaves sub-unit dust and trips NetValueRemainingTooSmall (6092). The flag
    // lets Kamino cap at ceil(debt) and close the line cleanly.
    if (this.action.type === 'kamino_repay') {
      const s = this.kaminoRepayStats;
      if (s && s.fullRepay) mergedParams['repayAll'] = 'true';
    }
    // "Unstake my position" means exiting an Earn vault when a live vault
    // position backs this card — execute it as a vault withdraw against the
    // pinned vault address (the backend maps amount/ktokenAmount to shares via
    // serde alias). With NO vault position it stays a genuine KMNO-governance
    // unstake and reaches the SDK farm-unstake backend.
    let dispatchType = this.action.type;
    if (this.action.type === 'kamino_unstake' && this.kaminoVaultPosition()) {
      dispatchType = 'kamino_vault_withdraw';
      if (!mergedParams['ktokenAmount'] && mergedParams['amount']) {
        mergedParams['ktokenAmount'] = mergedParams['amount'];
      }
    }
    // Safety net: the vault-withdraw backend rejects a non-numeric share amount
    // ("all"). If the position lagged behind the click, resolve any word amount
    // to the real full share figure so a full withdraw never submits "all" — and
    // the persisted (executed) params show the real number, not the word.
    if (dispatchType === 'kamino_vault_withdraw') {
      const amt = (mergedParams['ktokenAmount'] ?? mergedParams['amount'] ?? '').trim().toLowerCase();
      const shares = parseFloat(this.kaminoVaultPosition()?.shares ?? '0') || 0;
      if (shares > 0 && (amt === '' || amt === 'all' || amt === 'max' || amt === 'full')) {
        mergedParams['ktokenAmount'] = String(shares);
      }
    }
    // Merge user-edited slippage and priorityFee for ALL action types that use them
    // (launch, pumpfun_buy/sell, pumpswap_buy/sell, etc.)
    if (this.editSlippage()) mergedParams['slippage'] = this.editSlippage();
    if (this.editPriorityFee()) mergedParams['priorityFee'] = this.editPriorityFee();
    const mergedAction: ParsedAction = { ...this.action, type: dispatchType, params: mergedParams };
    // Remember the EDITED params so the late async confirmations (RPC poll /
    // manual re-check, which run after this method returns) persist what was
    // actually submitted — not the original `this.action.params`. Without this
    // the card reverts to the pre-edit amount once it re-hydrates from the
    // stored result (e.g. after the message list re-renders the card).
    this.lastSubmittedParams = mergedParams;

    const callbacks = {
      onQuote: () => { this.resetStallTimeout(); this.status.set('quoting'); },
      onSign: () => { this.resetStallTimeout(); this.status.set('signing'); },
      // Keep-alive from long multi-step flows (e.g. borrow's setup approval +
      // confirmation). Re-arms the stall timer without changing visible status.
      onProgress: () => this.resetStallTimeout(),
      // For launches: record the generated mint (contract) into the params that get
      // persisted, so the created token's address lands in chat history and the LLM
      // can resolve it for a later "sell this / sell <TICKER>".
      onMintGenerated: (mint: string) => { mergedParams['mintPubkey'] = mint; this.createdMint.set(mint); },
      onSubmit: (sig: string) => {
        if (this.isRelayBridge()) this.stallWatchOff = true;
        this.resetStallTimeout();
        this.txSignature.set(sig);
        this.status.set('submitted');
        this.startSubmittedTick();
        this.persistResult({ status: 'submitted', txSignature: sig, errorMessage: null, executedParams: mergedParams, swapView: this.lastSwapView ?? undefined });
      },
      onConfirm: (result?: string) => {
        this.stopSubmittedTick();
        this.status.set('confirmed');
        if (this.isDataOnly() && result) {
          this.dataResult.set(result);
        }
        const sig = this.txSignature() ?? result ?? '';
        const stored: StoredActionResult = { status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: mergedParams, swapView: this.lastSwapView ?? undefined };
        this.storeResult(mergedAction, sig);
        this.persistResult(stored);
        this.clearDraft();
        this.actionComplete.emit(stored);
      },
      // The transaction landed and reverted. It arrives here rather than
      // through the thrown-error path because submission already succeeded —
      // the card is past `signing` and holding a real signature, which stays
      // on the card so the user can open it in an explorer.
      onFail: (message: string, sig: string) => {
        this.stopSubmittedTick();
        this.txSignature.set(sig);
        this.errorMessage.set(
          sanitizeErrorMessage(message, this.action?.type) || 'The transaction failed on-chain.',
        );
        this.status.set('error');
        // Persist it. A failed transaction is as much a receipt as a
        // successful one, and re-hydrating it as "submitted" would leave the
        // card spinning forever on every reload.
        this.persistResult({
          status: 'error',
          txSignature: sig,
          errorMessage: this.errorMessage(),
          executedParams: mergedParams,
          swapView: this.lastSwapView ?? undefined,
        });
      },
    };

    // Race the chain against a stall timeout so the card never sits on
    // "Building transaction..." indefinitely when the backend stalls (slow
    // RPC, hanging Raydium CLMM enrichment, etc.). User gets a clear failure
    // they can Retry, instead of a silent spinner. This is a STALL timeout,
    // not a total-time budget: each progress callback (quote/sign/submit/
    // keep-alive) re-arms it, so a legitimately long flow — e.g. a borrow that
    // needs a separate collateral-setup approval + on-chain confirmation before
    // the main tx — isn't killed as long as it keeps making progress. Only a
    // genuine stall (no progress for TIMEOUT_MS) trips it.
    const TIMEOUT_MS = 45_000;
    let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
    let rejectTimeout: (e: Error) => void = () => {};
    const armTimeout = () => {
      if (timeoutHandle) clearTimeout(timeoutHandle);
      // Once a bridge's deposit is away there is nothing left for this card to
      // do but wait, and waiting is not stalling: the far side can take
      // minutes. Re-arming here would fail a healthy bridge at 45 seconds.
      if (this.stallWatchOff) return;
      timeoutHandle = setTimeout(
        () => rejectTimeout(new Error('Request timed out. The protocol may be slow or unreachable — try again.')),
        TIMEOUT_MS,
      );
    };
    this.resetStallTimeout = armTimeout;
    const timeoutPromise = new Promise<never>((_, reject) => { rejectTimeout = reject; });
    armTimeout();

    try {
      await Promise.race([
        this.actionService.executeChain([mergedAction], callbacks),
        timeoutPromise,
      ]);
    } catch (e: any) {
      const msg: string = e?.message ?? String(e ?? '');
      const isUserRejection = /reject|denied|cancel|declined|user refused/i.test(msg);
      if (isUserRejection) {
        this.status.set('pending');
        return;
      }
      // Blockhash expired between build and submit (user lingered on the
      // form). Rebuild once with a fresh blockhash and re-prompt for sign —
      // saves the user a manual Retry click. Limit to one retry to avoid an
      // infinite loop if the RPC itself is broken.
      if (/^BLOCKHASH_EXPIRED$|blockhash not found|block height exceeded/i.test(msg)) {
        try {
          this.status.set('quoting');
          await this.actionService.executeChain([mergedAction], callbacks);
          return;
        } catch (e2: any) {
          const m2: string = e2?.message ?? String(e2 ?? '');
          if (/reject|denied|cancel|declined|user refused/i.test(m2)) {
            this.status.set('pending');
            return;
          }
          this.errorMessage.set(sanitizeErrorMessage(m2, this.action?.type) || 'Failed to execute action');
          this.status.set('error');
          return;
        }
      }
      this.errorMessage.set(sanitizeErrorMessage(msg, this.action?.type) || 'Failed to execute action');
      this.status.set('error');
    } finally {
      if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
    }
  }

  reject(): void { this.clearDraft(); this.actionDismissed.emit(); }

  private clearDraft(): void {
    if (!this.sessionId || !this.messageId) return;
    try { localStorage.removeItem(`draft:${this.sessionId}:${this.messageId}`); } catch {}
  }

  private storeResult(action: ParsedAction, sig: string): void {
    try { const s = JSON.parse(localStorage.getItem(ACTION_RESULTS_KEY) ?? '[]'); s.push({ action, signature: sig, timestamp: Date.now() }); localStorage.setItem(ACTION_RESULTS_KEY, JSON.stringify(s)); } catch {}
  }

  private persistResult(result: StoredActionResult): void {
    if (!this.sessionId || !this.messageId) {
      console.warn('[persistResult] skipped — missing sessionId or messageId', { sessionId: this.sessionId, messageId: this.messageId });
      return;
    }
    // Freeze the Jupiter Lend detail panel into the result so a reloaded
    // completed card renders WHAT was withdrawn + the rate/deposit statically,
    // instead of an empty card (the live loader is skipped for completed cards).
    if (!result.lendSnapshot && ['lend', 'withdraw_lend'].includes(this.action?.type ?? '')) {
      const li = this.lendInfo();
      if (li) result = { ...result, lendSnapshot: li as unknown as StoredActionResult['lendSnapshot'] };
    }
    const key = this.actionResultKey();
    // Client-generated cards (query-card Deposit/Withdraw/Multiply, clarify picks)
    // aren't server messages, so updateMessageMeta can't persist their result —
    // the tx/confirmed state would vanish on reload. Mirror it to localStorage;
    // chat-shell's restoreClientActions folds it back into the card's metadata.
    if (/^(use-action-|clarify-action-|cancel-)/.test(this.messageId)) {
      try {
        const lsKey = `client-action-results:${this.sessionId}`;
        const map = JSON.parse(localStorage.getItem(lsKey) ?? '{}');
        map[`${this.messageId}::${key}`] = result;
        localStorage.setItem(lsKey, JSON.stringify(map));
      } catch { /* storage disabled — non-fatal */ }
      return; // no server message to PATCH
    }
    console.log('[persistResult] saving', { sessionId: this.sessionId, messageId: this.messageId, key, result });
    this.chatApi.updateMessageMeta(this.sessionId, this.messageId, {
      action_results: { [key]: result },
    }).then(ok => {
      if (ok) {
        console.log('[persistResult] saved OK');
      } else {
        console.warn('[persistResult] server returned ok:false — PATCH may have failed');
      }
    }).catch(err => {
      console.error('[persistResult] error', err);
    });
  }

  triggerImageUpload(): void { this.imageFileInput?.nativeElement?.click(); }


  onTickerInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    const sanitized = input.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 10);
    input.value = sanitized;
    this.editSymbol.set(sanitized);
  }

  async onImageFileSelected(event: Event): Promise<void> { const f = (event.target as HTMLInputElement).files?.[0]; if (f) await this.uploadImage(f); }
  onDragOver(event: DragEvent): void { event.preventDefault(); this.isDragOver.set(true); }
  onDragLeave(): void { this.isDragOver.set(false); }
  async onDrop(event: DragEvent): Promise<void> { event.preventDefault(); this.isDragOver.set(false); const f = event.dataTransfer?.files?.[0]; if (f) await this.uploadImage(f); }

  private async uploadImage(file: File): Promise<void> {
    this.uploadingImage.set(true); this.imageUploadError.set(null);
    try {
      // Resize to 1000×1000 (center-crop) — pump.fun requires min. 1000×1000, 1:1 square.
      const toUpload = await resizeImageToSquare(file, 1000);
      // Store resized file so launch flow can upload directly to pump.fun IPFS.
      this.resizedImageFile = toUpload;
      // Upload to our server for preview URL; failures fall back to a blob URL.
      try {
        const result = await this.uploadService.uploadImage(toUpload).toPromise();
        if (result) this.uploadedImageUrl.set(this.uploadService.getGatewayUrl(result.url));
      } catch {
        // Server upload failed. Show the picture so the form still makes
        // sense, but say so — this URL is local to this tab and cannot be
        // used for the launch, and staying quiet about it is how a token
        // ended up on chain pointing at it.
        this.uploadedImageUrl.set(URL.createObjectURL(toUpload));
        this.imageUploadError.set(
          'Image preview only — it could not be saved to the server yet. Try uploading it again before launching.',
        );
      }
    } catch (e: any) { this.imageUploadError.set(e?.message || 'Upload failed'); }
    finally { this.uploadingImage.set(false); }
  }

  triggerBannerUpload(): void { this.bannerFileInput?.nativeElement?.click(); }
  async onBannerFileSelected(event: Event): Promise<void> { const f = (event.target as HTMLInputElement).files?.[0]; if (f) await this.uploadBannerFile(f); }

  private async uploadBannerFile(file: File): Promise<void> {
    this.uploadingBanner.set(true); this.bannerUploadError.set(null);
    try {
      const result = await this.uploadService.uploadImage(file).toPromise();
      if (result) this.uploadedBannerUrl.set(this.uploadService.getGatewayUrl(result.url));
      else this.uploadedBannerUrl.set(URL.createObjectURL(file));
    } catch { this.uploadedBannerUrl.set(URL.createObjectURL(file)); }
    finally { this.uploadingBanner.set(false); }
  }

  getEditParam(key: string): string { return this.editParams()[key] ?? ''; }
  setEditParam(key: string, value: string): void { this.editParams.update(p => ({ ...p, [key]: value })); }

  // ── Limit order: sell/buy amounts drive the (derived) target price ─────────
  // A limit order is "sell N of X to receive M of Y". The user enters both
  // amounts directly in the SELL / BUY panels; `targetPrice` (input-per-output,
  // per the backend field) is derived = sellAmount ÷ buyAmount. `limitBuyAmount`
  // is the source of truth for the buy input (seeded in initFromAction), so the
  // field never fights the user's keystrokes.
  readonly limitBuyAmount = signal<string>('');

  onLimitSellInput(v: string): void {
    this.setEditParam('amount', v);
    this.syncLimitTargetPrice();
  }
  onLimitBuyInput(v: string): void {
    this.limitBuyAmount.set(v);
    this.syncLimitTargetPrice();
  }
  /** Pin sell amount to the whole input balance, then rederive target price. */
  limitSetMaxSell(): void {
    this.setMaxAmount('amount');
    this.syncLimitTargetPrice();
  }
  /** targetPrice = buyAmount ÷ sellAmount (OUTPUT tokens per 1 INPUT token —
   *  the backend's definition: takingAmount = amount × targetPrice). */
  private syncLimitTargetPrice(): void {
    const sell = parseFloat(this.getEditParam('amount'));
    const buy = parseFloat(this.limitBuyAmount());
    this.setEditParam('targetPrice',
      Number.isFinite(sell) && sell > 0 && Number.isFinite(buy) && buy > 0
        ? String(buy / sell)
        : '');
  }
  /** Implied rate for the helper line: output tokens per 1 input token. */
  limitRate(): number | null {
    const sell = parseFloat(this.getEditParam('amount'));
    const buy = parseFloat(this.limitBuyAmount());
    if (!Number.isFinite(sell) || sell <= 0 || !Number.isFinite(buy) || buy <= 0) return null;
    return buy / sell;
  }

  /** Jupiter's minimum limit-order size, in USD. */
  readonly MIN_LIMIT_USD = 5;
  /** Estimated USD value of the order (sell side), or null if price unknown. */
  limitOrderUsd(): number | null {
    const price = this.inputUsdPrice();
    const sell = parseFloat(this.getEditParam('amount'));
    if (price === null || !Number.isFinite(sell) || sell <= 0) return null;
    return sell * price;
  }
  /** True only when we KNOW the order is under the $5 minimum. */
  limitBelowMin(): boolean {
    const usd = this.limitOrderUsd();
    return usd !== null && usd < this.MIN_LIMIT_USD;
  }

  // ── DCA (recurring): total amount lives in the Spend panel ─────────────────
  /** Pin the total spend to the whole / half input balance. */
  dcaSetMax(): void {
    const b = this.inputBalance();
    if (b !== null && b > 0) this.setEditParam('totalAmount', String(b));
  }
  dcaSetHalf(): void {
    const b = this.inputBalance();
    if (b !== null && b > 0) this.setEditParam('totalAmount', String(+(b / 2).toFixed(9)));
  }
  /** Per-cycle spend = total ÷ orders, for the summary line. */
  dcaPerCycle(): number | null {
    const total = parseFloat(this.getEditParam('totalAmount'));
    const orders = parseInt(this.getEditParam('numberOfOrders'), 10);
    if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(orders) || orders <= 0) return null;
    return total / orders;
  }
  /** Jupiter's minimum value PER DCA suborder, in USD. */
  readonly MIN_DCA_ORDER_USD = 50;
  /** Estimated USD value of one suborder (perCycle × input price), or null. */
  dcaPerOrderUsd(): number | null {
    const price = this.inputUsdPrice();
    const per = this.dcaPerCycle();
    if (price === null || per === null) return null;
    return per * price;
  }
  /** True only when we KNOW a suborder is under the $50 minimum. */
  dcaBelowMin(): boolean {
    const usd = this.dcaPerOrderUsd();
    return usd !== null && usd < this.MIN_DCA_ORDER_USD;
  }

  // Frequency control (value + unit) → intervalSeconds, Jupiter-style, instead
  // of a raw seconds field. Value/unit are the source of truth; intervalSeconds
  // in editParams is derived from them.
  private readonly DCA_UNIT_SECONDS: Record<string, number> = {
    minute: 60, hour: 3600, day: 86400, week: 604800,
  };
  readonly dcaFreqValue = signal<string>('1');
  readonly dcaFreqUnit = signal<'minute' | 'hour' | 'day' | 'week'>('day');

  onDcaFreqValue(v: string): void { this.dcaFreqValue.set(v); this.syncDcaInterval(); }
  onDcaFreqUnit(u: string): void {
    if (u === 'minute' || u === 'hour' || u === 'day' || u === 'week') this.dcaFreqUnit.set(u);
    this.syncDcaInterval();
  }
  private syncDcaInterval(): void {
    const val = parseFloat(this.dcaFreqValue());
    const unitSec = this.DCA_UNIT_SECONDS[this.dcaFreqUnit()] ?? 86400;
    this.setEditParam('intervalSeconds',
      Number.isFinite(val) && val > 0 ? String(Math.round(val * unitSec)) : '');
  }
  /** Seed the frequency value+unit from a raw intervalSeconds (largest even unit). */
  private seedDcaFrequency(intervalSeconds: number): void {
    if (!Number.isFinite(intervalSeconds) || intervalSeconds <= 0) {
      this.dcaFreqUnit.set('day');
      this.dcaFreqValue.set('1');
      return;
    }
    let unit: 'minute' | 'hour' | 'day' | 'week' = 'minute';
    for (const u of ['week', 'day', 'hour', 'minute'] as const) {
      if (intervalSeconds % this.DCA_UNIT_SECONDS[u] === 0) { unit = u; break; }
    }
    this.dcaFreqUnit.set(unit);
    this.dcaFreqValue.set(String(intervalSeconds / this.DCA_UNIT_SECONDS[unit]));
  }

  /** Human label for the interval (seconds → "day"/"hour"/"week"/"N min"…). */
  dcaIntervalLabel(): string {
    const s = parseInt(this.getEditParam('intervalSeconds'), 10);
    if (!Number.isFinite(s) || s <= 0) return '';
    if (s % 604800 === 0) { const n = s / 604800; return n === 1 ? 'week' : `${n} weeks`; }
    if (s % 86400 === 0) { const n = s / 86400; return n === 1 ? 'day' : `${n} days`; }
    if (s % 3600 === 0) { const n = s / 3600; return n === 1 ? 'hour' : `${n} hours`; }
    if (s % 60 === 0) { const n = s / 60; return n === 1 ? 'minute' : `${n} min`; }
    return `${s}s`;
  }

  // ── Perp (Jupiter perpetuals): market + side + collateral + leverage slider ─
  private readonly PERP_MARKET_MINTS: Record<string, string> = {
    SOL: 'So11111111111111111111111111111111111111112',
    wETH: '7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs',
    wBTC: '3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh',
  };
  readonly PERP_MARKETS = ['SOL', 'wETH', 'wBTC'];
  readonly PERP_MIN_LEV = 1.1;
  readonly PERP_MAX_LEV = 250;
  /** Slider tick labels shown under the leverage rail. */
  readonly PERP_LEV_TICKS = [1.1, 50, 100, 150, 200, 250];

  perpMarket(): string {
    const m = this.getEditParam('market');
    return this.PERP_MARKETS.includes(m) ? m : 'SOL';
  }
  perpSide(): 'long' | 'short' {
    return this.getEditParam('side').toLowerCase() === 'short' ? 'short' : 'long';
  }
  perpMarketMint(m: string = this.perpMarket()): string {
    return this.PERP_MARKET_MINTS[m] ?? this.PERP_MARKET_MINTS['SOL'];
  }
  /**
   * Collateral token mint. Jupiter Perps lets the user pay collateral in any
   * supported token (USDC / SOL / wETH / wBTC) and swaps it into the position,
   * so an explicit `collateralToken` (e.g. "10 USDC" on a SOL long) wins over
   * the side default. Falls back to the protocol default: short → USDC,
   * long → the market's base token.
   */
  perpCollateralMint(): string {
    const ct = (this.editParams()['collateralToken'] ?? '').trim();
    if (ct) {
      const t = this.tokenRegistry.getBySymbol(ct) ?? this.tokenRegistry.getToken(ct);
      if (t) return t.address;
    }
    return this.perpSide() === 'short'
      ? 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
      : this.perpMarketMint();
  }
  perpMarketTd() { return this.resolveTokenDisplay(this.perpMarketMint()); }
  perpCollateralTd() { return this.resolveTokenDisplay(this.perpCollateralMint()); }

  // JLP: the deposit (add) or receive (remove) token pill display. Reuses the
  // balance-mint resolver so the pill icon matches the balance line.
  jlpTokenTd() { return this.resolveTokenDisplay(this.inputBalanceMint()); }
  // The JLP token itself (real SPL mint) — resolves the official Jupiter icon
  // from the token registry instead of a "J" placeholder.
  jlpMintTd() { return this.resolveTokenDisplay('27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4'); }
  // jlp_remove receive token — comes from the `token` param directly (NOT
  // inputBalanceMint, which for a remove now points at JLP).
  jlpReceiveTd() { return this.resolveTokenDisplay(this.editParams()['token'] ?? 'USDC'); }

  // Jupiter Lend (earn) deposit-token pill display.
  /** Withdraw-all: set the amount to the real deposit (floored 6dp, never above chain). */
  setMaxLendWithdraw(): void {
    const info = this.lendInfo();
    const dep = info?.kind === 'earn' ? (info.data.userDepositedAssets ?? 0) : 0;
    if (dep > 0) this.setEditParam('amount', String(Math.floor(dep * 1e6) / 1e6));
  }

  lendTokenTd() {
    // Jupiter Lend lists native SOL as WSOL; the token param arrives as "wSOL"
    // (any case), which the registry can't resolve → a wrong/placeholder icon.
    // Normalise WSOL→SOL so the native SOL logo + symbol render correctly.
    const raw = this.editParams()['token'] ?? 'USDC';
    return this.resolveTokenDisplay(raw.toUpperCase() === 'WSOL' ? 'SOL' : raw);
  }

  setPerpMarket(m: string): void { if (this.isEditable()) this.setEditParam('market', m); }
  setPerpSide(s: 'long' | 'short'): void { if (this.isEditable()) this.setEditParam('side', s); }

  /** Current leverage, clamped to [1.1, 250]. Default 2×. */
  perpLeverage(): number {
    const v = parseFloat(this.getEditParam('leverage'));
    if (!Number.isFinite(v)) return 2;
    return Math.min(this.PERP_MAX_LEV, Math.max(this.PERP_MIN_LEV, v));
  }
  setPerpLeverage(v: string | number): void {
    const n = typeof v === 'number' ? v : parseFloat(v);
    if (!Number.isFinite(n)) return;
    const clamped = Math.min(this.PERP_MAX_LEV, Math.max(this.PERP_MIN_LEV, n));
    // Leverage is authoritative — drop any LLM-supplied sizeUsd override so the
    // backend derives size from collateral × leverage.
    this.setEditParam('leverage', String(+clamped.toFixed(1)));
    this.setEditParam('sizeUsd', '');
  }
  nudgePerpLeverage(delta: number): void { this.setPerpLeverage(this.perpLeverage() + delta); }
  /** Slider fill / handle position, 0–100%. */
  perpLevPercent(): number {
    return ((this.perpLeverage() - this.PERP_MIN_LEV) / (this.PERP_MAX_LEV - this.PERP_MIN_LEV)) * 100;
  }

  /** Position size in USD = collateral × collateral-token price × leverage. */
  perpSizeUsd(): number | null {
    const coll = parseFloat(this.getEditParam('collateralAmount'));
    const price = this.inputUsdPrice();
    if (!Number.isFinite(coll) || coll <= 0 || price === null) return null;
    return coll * price * this.perpLeverage();
  }
  /** Collateral value in USD (before leverage). */
  perpCollateralUsd(): number | null {
    const coll = parseFloat(this.getEditParam('collateralAmount'));
    const price = this.inputUsdPrice();
    if (!Number.isFinite(coll) || coll <= 0 || price === null) return null;
    return coll * price;
  }
  /** Jupiter Perps requires ≥ $10 collateral to open a new position. */
  readonly PERP_MIN_COLLATERAL_USD = 10;
  /** True when the entered collateral is below Jupiter's $10 minimum. */
  perpBelowMinCollateral(): boolean {
    const usd = this.perpCollateralUsd();
    return usd !== null && usd < this.PERP_MIN_COLLATERAL_USD;
  }
  /** Pin collateral to the whole / half balance. */
  perpSetMaxCollateral(): void {
    const b = this.inputBalance();
    if (b !== null && b > 0) this.setEditParam('collateralAmount', String(b));
  }
  perpSetHalfCollateral(): void {
    const b = this.inputBalance();
    if (b !== null && b > 0) this.setEditParam('collateralAmount', String(+(b / 2).toFixed(9)));
  }

  /**
   * Catalog-driven minimum amount hint for amount-style inputs that live
   * outside the generic FieldDef grid (e.g. the bespoke Raydium CLMM /
   * DLMM forms). Returns empty when no catalog entry exists for the
   * current action type — caller `@if (minAmountHint())` hides the row.
   */
  minAmountHint(): string {
    const cfg = ACTION_MIN_AMOUNT[this.action?.type];
    return cfg?.hint ?? '';
  }

  /**
   * Locale-safe decimal normalizer for free-text amount inputs.
   *
   * European locales (Turkish, German, French, …) type "5,00052" with a
   * comma as the decimal separator. Native `<input type="number">` only
   * accepts a dot — typed commas show as "valid values: 0 and 1" browser
   * validation noise. We use `type="text"` + this normalizer so the input
   * accepts either separator and stores the canonical dot form for the
   * downstream Solana action builder.
   *
   * Also strips any non-numeric chars except for one decimal point — this
   * way paste-ing "$5.00" or "1 234.56" still produces a clean number.
   */
  normalizeDecimal(value: string): string {
    if (!value) return '';
    // Convert comma → dot, drop everything else except digits and the first dot.
    let out = value.replace(',', '.').replace(/[^0-9.]/g, '');
    const firstDot = out.indexOf('.');
    if (firstDot !== -1) {
      out = out.slice(0, firstDot + 1) + out.slice(firstDot + 1).replace(/\./g, '');
    }
    return out;
  }
  toggleEditParam(key: string): void { this.setEditParam(key, this.getEditParam(key) === 'true' ? 'false' : 'true'); }

  // ── Token picker modal ──────────────────────────────────────────────────
  // Opened when the user clicks a swap-row token chip (FROM / TO). Holds the
  // field key being edited so the picked mint goes to the right side of the
  // trade. `null` means modal is closed.
  readonly tokenPickerField = signal<string | null>(null);

  openTokenPicker(fieldKey: string, ev: Event): void {
    // Swap uses inputMint/outputMint; the Raydium CLMM "custom pair" path uses
    // tokenA/tokenB so the user can pick any two tokens before we resolve a
    // pool. Both are editable token pickers.
    const allowed = this.action?.type === 'swap' || this.action?.type === 'raydium_open_position';
    if (!this.isEditable() || !allowed) return;
    // Stop the click from bubbling to the card root and triggering the
    // poll-lifetime reset twice / the card's own click handlers.
    ev.stopPropagation();
    this.tokenPickerField.set(fieldKey);
  }

  closeTokenPicker(): void {
    this.tokenPickerField.set(null);
  }

  /** Short "AbCd…WxYz" form of a mint, used as a symbol fallback for tokens
   *  the registry doesn't name. */
  shortMint(m: string): string {
    const v = (m ?? '').trim();
    if (v.length <= 10) return v;
    return `${v.slice(0, 4)}…${v.slice(-4)}`;
  }

  pickerTitle(fieldKey: string): string {
    switch (fieldKey) {
      case 'inputMint': return 'From token';
      case 'outputMint': return 'To token';
      case 'tokenA': return 'First token';
      case 'tokenB': return 'Second token';
      default: return 'Select token';
    }
  }

  /**
   * User selected a token from the picker. Write the mint into the right side
   * of the swap, close the modal — the existing edit-effect will auto-fire a
   * fresh quote, so the counterparty estimate updates without extra wiring.
   */
  private static readonly TOKEN_PICKER_SIBLING: Record<string, string> = {
    inputMint: 'outputMint',
    outputMint: 'inputMint',
    tokenA: 'tokenB',
    tokenB: 'tokenA',
  };

  onTokenPicked(mint: string): void {
    const fieldKey = this.tokenPickerField();
    if (!fieldKey) return;
    const otherKey = ActionCardComponent.TOKEN_PICKER_SIBLING[fieldKey];
    this.editParams.update(prev => {
      const next = { ...prev };
      // If the user picks the SAME token on the other side, swap them so we
      // never end up with input == output (a self-pair the pool/quote rejects).
      if (otherKey && this.resolveToMint(next[otherKey] ?? '') === mint) {
        next[otherKey] = next[fieldKey] ?? '';
      }
      next[fieldKey] = mint;
      return next;
    });
    this.closeTokenPicker();

    // CLMM custom-pair path: once BOTH tokens are chosen, resolve the pool
    // (highest-liquidity) and enrich the form (price / symbols / range).
    if (this.action?.type === 'raydium_open_position') {
      const p = this.editParams();
      if (this.resolveToMint(p['tokenA'] ?? '') && this.resolveToMint(p['tokenB'] ?? '')) {
        void this.resolveClmmPair();
      }
    }
  }

  readonly clmmResolving = signal(false);
  readonly clmmPairError = signal<string | null>(null);

  /** True once both custom-pair tokens are chosen (mint-resolvable). */
  readonly clmmBothTokensPicked = computed(() => {
    const p = this.editParams();
    return !!this.resolveToMint(p['tokenA'] ?? '') && !!this.resolveToMint(p['tokenB'] ?? '');
  });

  /** CLMM open-position with no pool resolved yet (no current price) — the
   *  deposit form isn't ready, so Confirm must stay disabled. */
  readonly clmmUnresolved = computed(
    () => this.isRaydiumOpenPosition() && !this.editParams()['currentPrice'],
  );

  /**
   * A Meteora deposit that names no pool. The panel then renders a pair of
   * "??" logos, an empty price range and "Total bins: 0" — a form that cannot
   * be completed, since every field below depends on the pool. Blocking the
   * CTA and saying so beats letting the user fill in amounts that have nowhere
   * to go.
   *
   * Usually this means the model reached for the pool-level deposit when the
   * user was pointing at an existing position; the prompt covers that, and
   * this is the backstop.
   */
  readonly meteoraPoolUnresolved = computed(() => {
    if (!this.isMeteoraDual()) return false;
    const p = this.editParams();
    if (p['position'] || p['positionId']) return false;   // targets a position
    return !p['pool'] && !p['poolId'];
  });

  /**
   * Pre-Confirm balance guard for the CLMM deposit — returns a short "Not
   * enough X" message when either side's amount exceeds its spendable balance
   * (SOL keeps a rent buffer). Catches the shortfall BEFORE the user hits
   * Confirm and eats a failed simulation ("guide me, don't dump a failing
   * card"). The 1% Max headroom keeps a full-balance Max from tripping this.
   */
  readonly clmmInsufficient = computed<string | null>(() => {
    if (!this.isRaydiumOpenPosition() || !this.editParams()['currentPrice']) return null;
    const p = this.editParams();
    const amtA = parseFloat(p['amountA'] ?? '0');
    const amtB = parseFloat(p['amountB'] ?? '0');
    const balA = this.inputBalance();
    const balB = this.secondaryBalance();
    const symA = (p['tokenASymbol'] ?? 'token A');
    const symB = (p['tokenBSymbol'] ?? 'token B');
    const isSol = (s: string) => { const u = s.toUpperCase(); return u === 'SOL' || u === 'WSOL'; };
    const RENT_BUFFER = 0.03;
    const EPS = 1e-9;
    if (amtA > 0 && balA !== null && amtA > balA - (isSol(symA) ? RENT_BUFFER : 0) + EPS) {
      return `Not enough ${symA}`;
    }
    if (amtB > 0 && balB !== null && amtB > balB - (isSol(symB) ? RENT_BUFFER : 0) + EPS) {
      return `Not enough ${symB}`;
    }
    return null;
  });

  /**
   * Resolve the currently-chosen tokenA/tokenB pair to a Raydium CLMM pool and
   * enrich the form. Drives the "Select Token Pair" state on the custom-pair
   * path: sets a resolving flag, clears any prior pool so a re-pick re-resolves,
   * and surfaces a clean message when no pool exists for the pair.
   */
  async resolveClmmPair(): Promise<void> {
    this.clmmPairError.set(null);
    this.clmmResolving.set(true);
    try {
      // Clear the previous pool so enrichment re-runs for the newly-picked pair.
      this._enrichedRaydiumPool = null;
      this.editParams.update(ep => {
        const n = { ...ep };
        for (const k of ['poolId', 'currentPrice', 'tokenASymbol', 'tokenBSymbol', 'minPrice', 'maxPrice', 'amountA', 'amountB']) {
          delete n[k];
        }
        return n;
      });
      const poolId = await this.resolveRaydiumPoolFromPair();
      if (!poolId) {
        this.clmmPairError.set('No Raydium CLMM pool exists for this pair yet. Pick a different pair.');
        return;
      }
      await this.maybeEnrichRaydiumPool();
      // The mints changed — reload both balance lines for the new pair.
      this.inputBalance.set(null);
      this.secondaryBalance.set(null);
      if (this.inputBalanceMint()) this.loadInputBalance();
      if (this.secondaryBalanceMint()) this.loadSecondaryBalance();
    } catch {
      this.clmmPairError.set('Could not load a pool for this pair. Try again.');
    } finally {
      this.clmmResolving.set(false);
    }
  }

  /**
   * Resolve `editParams[fieldKey]` (which may be a symbol like "SOL" *or* a
   * raw mint address) to a canonical mint address. Required because the
   * picker filters / highlights by address only — a literal "SOL" entry
   * would never match a TokenMeta whose `address` is the wrapped-SOL mint.
   */
  private resolveToMint(raw: string): string {
    if (!raw) return '';
    const trimmed = raw.trim();
    // Base58-looking → already a mint
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(trimmed)) return trimmed;
    return this.tokenRegistry.getBySymbol(trimmed)?.address ?? '';
  }

  /** Resolve the mint to show as "currently selected" inside the picker. */
  currentPickerMint(): string {
    const k = this.tokenPickerField();
    if (!k) return '';
    return this.resolveToMint(this.editParams()[k] ?? '');
  }

  /** Mint to HIDE in the picker (the other side of the pair — no self-pair). */
  excludedPickerMint(): string {
    const k = this.tokenPickerField();
    if (!k) return '';
    const otherKey = ActionCardComponent.TOKEN_PICKER_SIBLING[k];
    if (!otherKey) return '';
    return this.resolveToMint(this.editParams()[otherKey] ?? '');
  }

  /**
   * Reverse a swap's direction without re-prompting the LLM. Swaps inputMint
   * with outputMint AND toggles swapMode so the amount's *denomination token*
   * is preserved across the flip:
   *
   *   Pre:  in=SOL, out=USDC, amount=5, ExactOut  → "receive 5 USDC, pay SOL"
   *   Post: in=USDC, out=SOL, amount=5, ExactIn   → "spend 5 USDC, get SOL"
   *
   * The "5" stays attached to USDC (which moves from outputMint to inputMint),
   * exactly what users mean when they hit ↕ after typing an amount. If we
   * only swapped tokens without flipping mode, "5 USDC ExactOut" would
   * become "5 SOL ExactOut" — silently inflating the trade by ~25× because
   * the unit changed under them.
   */
  flipSwapDirection(): void {
    if (!this.action || !this.isEditable()) return;
    const isRaydium = this.action.type === 'raydium_swap';
    if (this.action.type !== 'swap' && !isRaydium) return;
    this.editParams.update(p => {
      const next = { ...p };
      const inMint = next['inputMint'] ?? '';
      const outMint = next['outputMint'] ?? '';
      next['inputMint'] = outMint;
      next['outputMint'] = inMint;
      // Raydium's build path is ExactIn-only, so a flip always lands on
      // ExactIn (the pay side stays the exact amount). Jupiter toggles.
      if (isRaydium) {
        next['swapMode'] = 'ExactIn';
      } else {
        const mode = String(next['swapMode'] ?? '').toLowerCase();
        const wasExactOut = mode === 'exactout' || mode === 'out';
        next['swapMode'] = wasExactOut ? 'ExactIn' : 'ExactOut';
      }
      return next;
    });
  }
  /**
   * Fill a numeric amount field with the user's full balance for the
   * matching token. Default field is `amount` (single-token actions);
   * dual-token forms pass `amountA` / `amountB` to target the right input.
   *
   * Also signals which side was just edited so the DLMM/AMM auto-balance
   * effect can fill the OTHER side. Without this, clicking Max on amount A
   * would leave amount B at 0 (the effect only knows to recompute when a
   * fresh edit is recorded).
   */
  setMaxAmount(fieldKey: string = 'amount'): void {
    const b = (fieldKey === 'amountB') ? this.secondaryBalance() : this.inputBalance();
    if (b === null || b <= 0) return;
    // Swap's inline amount input lives on `inputMint`/`outputMint` rows now —
    // Max means "spend my whole input balance", so it always pins the trade
    // to ExactIn regardless of which mode the LLM emitted.
    if (this.action?.type === 'swap' && (fieldKey === 'inputMint' || fieldKey === 'outputMint')) {
      this.editParams.update(prev => ({ ...prev, amount: b.toString(), swapMode: 'ExactIn' }));
      return;
    }
    this.setEditParam(fieldKey, b.toString());
    if (fieldKey === 'amountA') this.dlmmLastEdited.set('A');
    else if (fieldKey === 'amountB') this.dlmmLastEdited.set('B');
  }

  /**
   * Smart Max for the CLMM dual-amount card. A plain per-side Max fills the
   * WHOLE balance of one token, but the two sides are locked to the pool ratio
   * for the chosen range — so maxing SOL could demand far more USDC than the
   * user holds (the "insufficient balance" the user hit at ±20%). Instead,
   * deposit the LARGEST position that fits BOTH balances at the current ratio,
   * leaving a small SOL rent/fee buffer for the position NFT + tick arrays.
   * This is the "just do it for me" behaviour: it can never produce a position
   * the wallet can't fund.
   */
  /**
   * MAX for a CLMM deposit side. `side` is which token's MAX button was
   * clicked. Previously BOTH buttons ran the A-side logic, so clicking Token B
   * (USDC) MAX filled Token A (SOL) — the bug Berra hit. Now each side maxes
   * itself and the auto-balance effect fills the other at the pool ratio,
   * clamped so neither side exceeds its balance.
   */
  setMaxClmm(side: 'A' | 'B' = 'A'): void {
    const clmm = this.clmmRatio();
    const p = this.editParams();
    const balA = this.inputBalance() ?? 0;      // token A (e.g. WSOL)
    const balB = this.secondaryBalance() ?? 0;  // token B (e.g. USDC)
    const symA = (p['tokenASymbol'] ?? p['tokenA'] ?? '').toUpperCase();
    const symB = (p['tokenBSymbol'] ?? p['tokenB'] ?? '').toUpperCase();
    const isSol = (s: string) => s === 'SOL' || s === 'WSOL';
    // Opening a CLMM position costs ~0.02–0.05 SOL in rent (position NFT + tick
    // arrays + ATAs) + fees; keep a buffer so the native-SOL side never uses it all.
    const RENT_BUFFER = 0.03;
    // Leave 1% headroom on BOTH sides: a Max that fills the EXACT balance fails
    // simulation because the on-chain deposit needs a hair more than quoted
    // (tick rounding + slippage) — "Max then Not enough balance". The headroom
    // absorbs that so Max produces a value that actually clears.
    const SAFETY = 0.99;
    const availA = Math.max(0, balA - (isSol(symA) ? RENT_BUFFER : 0)) * SAFETY;
    const availB = Math.max(0, balB - (isSol(symB) ? RENT_BUFFER : 0)) * SAFETY;

    // Single-sided ranges (price outside the range): only one token is used,
    // regardless of which MAX was clicked.
    if (clmm?.singleSided === 'A') {
      this.editParams.update(ep => ({ ...ep, amountA: formatDlmmAmount(availA), amountB: '0' }));
      this.dlmmLastEdited.set('A');
      return;
    }
    if (clmm?.singleSided === 'B') {
      this.editParams.update(ep => ({ ...ep, amountB: formatDlmmAmount(availB), amountA: '0' }));
      this.dlmmLastEdited.set('B');
      return;
    }

    const yPerX = clmm?.yPerX; // amount B per 1 amount A
    if (!yPerX || !Number.isFinite(yPerX) || yPerX <= 0) {
      // No usable ratio yet (range not set) — max ONLY the clicked side; the
      // auto-balance effect fills the other once a range exists.
      this.setMaxAmount(side === 'A' ? 'amountA' : 'amountB');
      return;
    }

    if (side === 'B') {
      // Largest B that satisfies BOTH: B ≤ availB AND B/yPerX ≤ availA.
      const maxB = Math.min(availB, availA * yPerX);
      if (!(maxB > 0)) return;
      this.setEditParam('amountB', formatDlmmAmount(maxB));
      this.dlmmLastEdited.set('B'); // auto-balance fills amountA = maxB / yPerX (≤ availA)
      return;
    }
    // Largest A that satisfies BOTH: A ≤ availA AND A×yPerX ≤ availB.
    const maxA = Math.min(availA, availB / yPerX);
    if (!(maxA > 0)) return;
    this.setEditParam('amountA', formatDlmmAmount(maxA));
    this.dlmmLastEdited.set('A'); // auto-balance effect fills amountB = maxA×yPerX (≤ availB)
  }

  async copyValue(value: string): Promise<void> { try { await navigator.clipboard.writeText(value); this.copiedField.set(value); setTimeout(() => this.copiedField.set(null), 2000); } catch {} }

  private balanceCacheKey(wallet: string, mint: string): string {
    return `oprai_bal_${wallet}_${mint}`;
  }

  private readBalanceCache(wallet: string, mint: string): number | null {
    try {
      const raw = localStorage.getItem(this.balanceCacheKey(wallet, mint));
      if (!raw) return null;
      const { value, ts } = JSON.parse(raw) as { value: number; ts: number };
      // 90-second TTL — fresh enough for the warning to appear immediately
      if (Date.now() - ts > 90_000) return null;
      return value;
    } catch { return null; }
  }

  private writeBalanceCache(wallet: string, mint: string, value: number): void {
    try {
      localStorage.setItem(this.balanceCacheKey(wallet, mint), JSON.stringify({ value, ts: Date.now() }));
    } catch {}
  }

  /**
   * Fetch the connected wallet's balance for `raw` (mint address or symbol)
   * and report it via the supplied setters. Shared between primary and
   * secondary balance loaders so the SOL fallback, symbol-resolution, and
   * cache-on-fail logic don't drift.
   */
  private async fetchBalanceFor(
    raw: string,
    setBalance: (b: number | null) => void,
    setLoading: (b: boolean) => void,
    onResolved?: (balance: number) => void,
  ): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet || !raw) return;

    // The LLM frequently passes a symbol ("JupSOL", "USDC") instead of a mint
    // address. Resolve here so the balance lookup actually matches.
    const SOL_MINT = 'So11111111111111111111111111111111111111112';
    const isMintAddress = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(raw);
    const resolved = isMintAddress
      ? raw
      : (this.tokenRegistry.getBySymbol(raw)?.address ?? raw);

    // Show cached value immediately so the hint isn't blank during the RPC roundtrip.
    const cached = this.readBalanceCache(wallet, resolved);
    if (cached !== null) setBalance(cached);

    setLoading(true);
    try {
      const isSol = raw === 'SOL' || raw === 'sol' || resolved === SOL_MINT;
      let balance: number;
      if (isSol) {
        const lamports = await this.solanaRpc.getBalance(wallet);
        balance = lamports / 1e9;
      } else {
        const acct = await this.solanaRpc.getTokenBalance(wallet, resolved);
        balance = acct?.balance ?? 0;
      }
      // Diagnostic — surfaces in DevTools so we can tell "0 because no ATA",
      // "0 because RPC returned empty", or "0 because the wrong mint was
      // resolved" apart. Don't ship verbose logs to users; console only.
      try {
        // eslint-disable-next-line no-console
        console.debug('[oprai] balance_fetch', {
          wallet,
          raw,
          resolved,
          balance,
        });
      } catch { /* console may be sandboxed */ }
      setBalance(balance);
      this.writeBalanceCache(wallet, resolved, balance);
      onResolved?.(balance);
    } catch (err) {
      // Diagnostic — distinguish "RPC error" from "0 balance" in console.
      try {
        // eslint-disable-next-line no-console
        console.warn('[oprai] balance_fetch_failed', {
          wallet,
          raw,
          resolved,
          err: (err as Error)?.message ?? String(err),
        });
      } catch { /* ignore */ }
      // Keep cached value when RPC fails; only reset to null on a cold cache.
      if (cached === null) setBalance(null);
    } finally {
      setLoading(false);
    }
  }

  private loadInputBalance(): Promise<void> {
    return this.fetchBalanceFor(
      this.inputBalanceMint(),
      v => this.inputBalance.set(v),
      v => this.inputBalanceLoading.set(v),
      bal => this.maybeResolveAllSentinel(bal),
    );
  }

  private loadSecondaryBalance(): Promise<void> {
    return this.fetchBalanceFor(
      this.secondaryBalanceMint(),
      v => this.secondaryBalance.set(v),
      v => this.secondaryBalanceLoading.set(v),
    );
  }

  /**
   * The prompt instructs the LLM to pass `amount: "all"` (or "max" / "100%") as a
   * sentinel for "use the full balance". The form input is `type="number"`, which
   * silently drops non-numeric strings, so the user sees an empty field. As soon
   * as the real balance loads, swap the sentinel in for the actual amount so the
   * card displays what's about to happen.
   */
  private maybeResolveAllSentinel(balance: number): void {
    if (!(balance > 0)) return;
    // Withdrawals resolve "all"/"max" against the DEPOSITED position, not the
    // wallet balance — handled by loadLendInfo (Jupiter) / the protocol loader.
    // Filling the wallet balance here withdrew the wrong amount (e.g. 0.187 SOL
    // of loose wallet balance instead of the ~1 SOL supplied to Jupiter Lend),
    // and it clobbered the "all" sentinel before the deposit-based resolver ran.
    const WITHDRAW_TYPES = ['withdraw_lend', 'kamino_withdraw', 'marginfi_withdraw', 'solend_withdraw'];
    if (this.action && WITHDRAW_TYPES.includes(this.action.type)) return;
    const params = this.editParams();
    const cur = (params['amount'] ?? '').trim().toLowerCase();
    if (cur === 'all' || cur === 'max' || cur === 'full' || cur === '100%') {
      this.setEditParam('amount', balance.toString());
      return;
    }
    // Percentage sentinel ("32%", "50 %") → that fraction of the balance.
    const pct = cur.match(/^(\d+(?:\.\d+)?)\s*%$/);
    if (pct) {
      const frac = parseFloat(pct[1]) / 100;
      if (frac > 0 && frac < 1) this.setEditParam('amount', (balance * frac).toString());
    }
  }

  private async loadValidators(): Promise<void> {
    if (this.validators().length) return; // already loaded
    this.validatorsLoading.set(true);
    try {
      const list = await this.actionService.getTopValidators();
      this.validators.set(list);
    } catch { /* silent — user can type custom address */ }
    finally { this.validatorsLoading.set(false); }
  }

  selectValidator(voteAccount: string): void {
    this.editParams.update(p => ({ ...p, validatorVoteAccount: voteAccount }));
    this.validatorCustom.set(false);
  }

  private async loadLendInfo(): Promise<void> {
    this.lendInfoLoading.set(true);
    try {
      // Pass the wallet so the earn info carries the user's deposited amount —
      // the withdraw card needs it for the "Deposited" balance AND to turn an
      // "all"/"max" sentinel into the real number.
      const wallet = this.walletService.publicKey() ?? undefined;
      const info = await this.jupiterLend.getEarnInfo(this.editParams()['token'] ?? 'USDC', wallet);
      if (info) {
        if (this.action.type === 'withdraw_lend') {
          // The user's position may be a borrow-market SUPPLY ("Lending") rather
          // than an Earn deposit — the Earn balance is then just dust. Surface
          // whichever is the real position so "Deposited", Max, and the
          // conversion all reflect the money that will actually be withdrawn.
          const token = this.editParams()['token'] ?? 'USDC';
          const target = wallet
            ? await this.jupiterLend.getSupplyWithdrawTarget(wallet, token)
            : null;
          const earnDep = info.userDepositedAssets ?? 0;
          if (target && target.supplyAmount > earnDep) {
            info.userDepositedAssets = target.supplyAmount;
            info.userJlBalance = target.supplyAmount;
            // Borrow-market supply is 1:1 with the underlying (no jlToken share
            // ratio), so present a 1:1 conversion instead of the Earn vault's.
            info.assetsPerJlToken = 1;
            info.jlTokensPerAsset = 1;
          }
        }
        this.lendInfo.set({ kind: 'earn', data: info } as LendActionInfo);
        if (this.action.type === 'withdraw_lend') {
          const amt = this.editParams()['amount'];
          const dep = info.userDepositedAssets ?? 0;
          if ((amt === 'all' || amt === 'max') && dep > 0) {
            // Show the real amount. Floor to 6 dp so the value never rounds ABOVE
            // the on-chain deposit (which would fail with insufficient funds);
            // interest accrual only grows the deposit, so a snapshot is safe.
            const safe = Math.floor(dep * 1e6) / 1e6;
            this.setEditParam('amount', String(safe));
          }
        }
      }
    }
    catch { this.lendInfo.set(null); } finally { this.lendInfoLoading.set(false); }
  }

  /** Load the borrow token's Kamino reserve rates + the wallet's obligation. */
  private async loadKaminoBorrowInfo(): Promise<void> {
    this.kaminoBorrowLoading.set(true);
    try {
      const wallet = this.walletService.publicKey() ?? undefined;
      const token = (this.editParams()['token'] ?? this.editParams()['reserve'] ?? 'USDC');
      const [reserves, obligations] = await Promise.all([
        this.kamino.getMainMarketReserves(),
        wallet ? this.kamino.getObligations(wallet, KAMINO_MAIN_MARKET) : Promise.resolve([] as KaminoObligation[]),
      ]);
      const want = token.trim().replace(/^\$/, '');
      const byMint = want.length >= 32;
      const norm = (s: string) => (s.toUpperCase() === 'WSOL' ? 'SOL' : s.toUpperCase());
      // Same "deepest reserve wins" rule the backend uses when a token has several.
      const matches = reserves.filter(r =>
        byMint ? r.liquidityTokenMint === want : norm(r.symbol) === norm(want));
      const reserve = matches.sort((a, b) => b.totalSupplyUsd - a.totalSupplyUsd)[0] ?? null;
      this.kaminoReserve.set(reserve);
      this.kaminoObligation.set(obligations[0] ?? null);
    }
    catch { this.kaminoReserve.set(null); this.kaminoObligation.set(null); }
    finally { this.kaminoBorrowLoading.set(false); }
  }

  /** Force a fresh obligation+reserve fetch — used when the user focuses the
   *  amount field, so a deposit made after this card opened is picked up. */
  reloadKaminoBorrowInfo(): void {
    this._lastKaminoBorrowKey = null;
    void this.loadKaminoBorrowInfo();
  }

  /** USD price of one unit of the reserve token (supplyUsd ÷ supply). */
  private kaminoReservePrice(r: KaminoReserve): number {
    const supply = parseFloat(r.totalSupply) || 0;
    return supply > 0 ? r.totalSupplyUsd / supply : 0;
  }

  /**
   * Live Kamino borrow projection. Reserve rates always render; the LTV / health
   * / max-borrowable block appears once the wallet has an obligation. Borrowing
   * `amount` of the token adds `amount × price` to the obligation's debt.
   *   ltvAfter   = (borrowedUsd + amount×price) ÷ collateralUsd
   *   maxBorrow  = (borrowLimit − borrowedUsd) ÷ price           (token units)
   *   HF (after) = borrowLiquidationLimit ÷ (borrowedUsd + amount×price)
   */
  get kaminoBorrowStats(): {
    symbol: string; borrowApyPct: number; maxLtvPct: number; liquidationLtvPct: number;
    availableToken: number; availableUsd: number; price: number;
    hasPosition: boolean; collateralUsd: number; borrowedUsd: number;
    maxBorrowable: number; borrowUsd: number; requiredCollateralUsd: number;
    ltvCurrentPct: number; ltvAfterPct: number;
    hf: number; hfLabel: string; hfClass: string; errorMsg?: string;
  } | null {
    const r = this.kaminoReserve();
    if (!r) return null;
    const price = this.kaminoReservePrice(r);
    const borrowApyPct = r.borrowApyNum * 100;
    // Max (borrow) LTV is the only ratio the public REST metrics expose per
    // reserve; the per-reserve liquidation threshold isn't in the API (it lives
    // on-chain / in the SDK), so we never fabricate it. Real liquidation RISK is
    // shown as the health factor below, derived from the obligation's on-chain
    // borrowLiquidationLimit once the wallet has collateral.
    const maxLtvPct = r.ltvNum * 100;
    const availableUsd = Math.max(0, r.totalSupplyUsd - (parseFloat(r.totalBorrowUsd) || 0));
    const availableToken = price > 0 ? availableUsd / price : 0;

    const ob = this.kaminoObligation();
    const collateralUsd = ob ? parseFloat(ob.depositedValue) || 0 : 0;
    const borrowedUsd = ob ? parseFloat(ob.borrowedValue) || 0 : 0;
    const borrowLimit = ob ? parseFloat(ob.borrowLimit) || 0 : 0;
    const liqLimitUsd = ob ? parseFloat(ob.borrowLiquidationLimit) || 0 : 0;
    // Effective liquidation LTV for THIS obligation (weighted across its
    // collateral) = liquidation-limit ÷ collateral. Real, position-specific.
    const liquidationLtvPct = collateralUsd > 0 ? (liqLimitUsd / collateralUsd) * 100 : 0;
    const hasPosition = collateralUsd > 0;

    const amount = parseFloat(this.getEditParam('amount') || '0') || 0;
    const addUsd = amount * price;
    const newBorrowUsd = borrowedUsd + addUsd;
    const maxBorrowable = price > 0 ? Math.max(0, (borrowLimit - borrowedUsd) / price) : 0;
    const ltvCurrentPct = collateralUsd > 0 ? (borrowedUsd / collateralUsd) * 100 : 0;
    const ltvAfterPct = collateralUsd > 0 ? (newBorrowUsd / collateralUsd) * 100 : 0;
    const hf = newBorrowUsd > 0 ? liqLimitUsd / newBorrowUsd : Infinity;

    const hfApplies = newBorrowUsd > 0 && collateralUsd > 0;
    const hfClass = !hfApplies || !isFinite(hf) || hf >= 1.6
      ? 'safe' : hf >= 1.2 ? 'caution' : 'danger';
    const hfLabel = !hfApplies ? '—' : !isFinite(hf) ? '∞' : hf.toFixed(2);

    let errorMsg: string | undefined;
    if (amount > 0 && !hasPosition) {
      errorMsg = 'No collateral deposited on Kamino yet — deposit a token first, then borrow against it.';
    } else if (amount > 0 && amount > availableToken) {
      errorMsg = `Only ${availableToken.toFixed(2)} ${r.symbol} available to borrow right now.`;
    } else if (amount > 0 && maxBorrowable > 0 && amount > maxBorrowable) {
      errorMsg = `Exceeds max borrowable — deposit more collateral or borrow ≤ ${maxBorrowable.toFixed(maxBorrowable < 1 ? 6 : 2)} ${r.symbol}.`;
    }

    // With no position yet, there is no denominator for LTV/HF — but we can
    // still give live feedback: how much collateral this borrow would require
    // at the reserve's max LTV. Updates as the amount is typed.
    const borrowUsd = amount * price;
    const requiredCollateralUsd = maxLtvPct > 0 ? borrowUsd / (maxLtvPct / 100) : 0;

    return {
      symbol: r.symbol, borrowApyPct, maxLtvPct, liquidationLtvPct, availableToken, availableUsd, price,
      hasPosition, collateralUsd, borrowedUsd, maxBorrowable, borrowUsd, requiredCollateralUsd,
      ltvCurrentPct, ltvAfterPct, hf: isFinite(hf) ? hf : 0, hfLabel, hfClass, errorMsg,
    };
  }

  /** Fill the borrow amount to the collateral-allowed maximum (× fraction). */
  setKaminoBorrowMax(fraction = 1): void {
    const s = this.kaminoBorrowStats;
    if (!s || s.maxBorrowable <= 0) return;
    const capped = Math.min(s.maxBorrowable, s.availableToken) * fraction;
    // Floor to 6 dp so the projected amount never rounds above the on-chain limit.
    this.setEditParam('amount', String(Math.floor(capped * 1e6) / 1e6));
  }

  /**
   * Live Kamino repay projection. Reuses the same reserve + obligation fetched
   * by loadKaminoBorrowInfo. Repaying `amount` of the token subtracts
   * `amount × price` from the obligation's debt (capped at the actual debt):
   *   ltvAfter    = (borrowedUsd − repayUsd) ÷ collateralUsd            (drops)
   *   HF (after)  = borrowLiquidationLimit ÷ (borrowedUsd − repayUsd)   (rises)
   * The debt for THIS token comes from the matching borrow line item, or (when
   * the API returns no line items) from the obligation's total debt — never
   * fabricated; borrowedValue is the on-chain refreshed figure.
   */
  get kaminoRepayStats(): {
    symbol: string; borrowApyPct: number; price: number;
    hasDebt: boolean; collateralUsd: number; borrowedUsd: number;
    debtToken: number; debtUsd: number;
    repayToken: number; repayUsd: number; remainingUsd: number; remainingToken: number;
    ltvCurrentPct: number; ltvAfterPct: number; liquidationLtvPct: number;
    hfCurrent: number; hfAfter: number; hfLabel: string; hfClass: string;
    fullRepay: boolean; errorMsg?: string;
  } | null {
    const r = this.kaminoReserve();
    if (!r) return null;
    const price = this.kaminoReservePrice(r);
    const borrowApyPct = r.borrowApyNum * 100;

    const ob = this.kaminoObligation();
    const collateralUsd = ob ? parseFloat(ob.depositedValue) || 0 : 0;
    const borrowedUsd = ob ? parseFloat(ob.borrowedValue) || 0 : 0;
    const liqLimitUsd = ob ? parseFloat(ob.borrowLiquidationLimit) || 0 : 0;
    const liquidationLtvPct = collateralUsd > 0 ? (liqLimitUsd / collateralUsd) * 100 : 0;

    // Debt in THIS token: prefer the matching borrow line item; fall back to the
    // obligation total (accurate for the common single-debt obligation).
    const norm = (s: string) => (s.toUpperCase() === 'WSOL' ? 'SOL' : s.toUpperCase());
    const debtLine = (ob?.borrows ?? []).find(
      b => b.reserveAddress === r.reserve || norm(b.symbol) === norm(r.symbol));
    const debtUsd = debtLine ? (parseFloat(debtLine.valueUsd) || 0) : borrowedUsd;
    const debtToken = debtLine ? (parseFloat(debtLine.amount) || 0) : (price > 0 ? borrowedUsd / price : 0);
    const hasDebt = debtUsd > 0;

    const amount = parseFloat(this.getEditParam('amount') || '0') || 0;
    const repayToken = Math.min(amount, debtToken);
    const repayUsd = Math.min(amount * price, debtUsd);
    const remainingUsd = Math.max(0, borrowedUsd - repayUsd);
    const remainingToken = Math.max(0, debtToken - repayToken);

    const ltvCurrentPct = collateralUsd > 0 ? (borrowedUsd / collateralUsd) * 100 : 0;
    const ltvAfterPct = collateralUsd > 0 ? (remainingUsd / collateralUsd) * 100 : 0;
    const hfCurrent = borrowedUsd > 0 ? liqLimitUsd / borrowedUsd : Infinity;
    const hfAfter = remainingUsd > 0 ? liqLimitUsd / remainingUsd : Infinity;
    // Health after repay only improves — class off the AFTER value.
    const hfApplies = remainingUsd > 0 && collateralUsd > 0;
    const hfClass = !hfApplies || !isFinite(hfAfter) || hfAfter >= 1.6
      ? 'safe' : hfAfter >= 1.2 ? 'caution' : 'danger';
    const hfLabel = !isFinite(hfAfter) ? '∞' : hfAfter.toFixed(2);
    const fullRepay = hasDebt && amount >= debtToken - 1e-9;

    let errorMsg: string | undefined;
    if (amount > 0 && !hasDebt) {
      errorMsg = 'No Kamino debt in this token to repay.';
    } else if (amount > 0 && amount > debtToken + 1e-9) {
      errorMsg = `You only owe ${debtToken.toFixed(debtToken < 1 ? 6 : 2)} ${r.symbol} — repaying the full debt clears it.`;
    }

    return {
      symbol: r.symbol, borrowApyPct, price,
      hasDebt, collateralUsd, borrowedUsd, debtToken, debtUsd,
      repayToken, repayUsd, remainingUsd, remainingToken,
      ltvCurrentPct, ltvAfterPct, liquidationLtvPct,
      hfCurrent: isFinite(hfCurrent) ? hfCurrent : 0, hfAfter: isFinite(hfAfter) ? hfAfter : 0,
      hfLabel, hfClass, fullRepay, errorMsg,
    };
  }

  /** Fill the repay amount to the full debt. Rounds UP (ceil), never down — a
   *  floored amount lands *below* the accruing debt and repays a partial, which
   *  leaves dust → 6092. Over-shooting is safe: Kamino caps the transfer at
   *  ceil(debt) and this also trips `fullRepay` so the build sends repay-all. */
  setKaminoRepayMax(): void {
    const s = this.kaminoRepayStats;
    if (!s || s.debtToken <= 0) return;
    // Ceil to 6 dp so the displayed amount is ≥ the true debt (never a partial).
    this.setEditParam('amount', String(Math.ceil(s.debtToken * 1e6) / 1e6));
  }

  /**
   * Live Kamino withdraw projection. Reuses the reserve + obligation fetched by
   * loadKaminoBorrowInfo. Withdrawing `amount` removes that much SUPPLIED
   * collateral, so — when the wallet also has debt — the collateral shrinks and
   * the position gets RISKIER (LTV up, health down):
   *   ltvAfter   = borrowedUsd ÷ (suppliedUsd − withdrawUsd)
   *   HF (after) = (liqLimit × remaining⁄supplied) ÷ borrowedUsd
   * Max withdraw keeps the remaining collateral able to back the debt
   *   maxWithdrawUsd = suppliedUsd × (1 − borrowedUsd⁄borrowLimit)
   * With no debt the whole supply is withdrawable. Supplied balance/price come
   * from the obligation's refreshed stats — never fabricated.
   */
  get kaminoWithdrawStats(): {
    symbol: string; supplyApyPct: number; price: number;
    hasSupply: boolean; hasDebt: boolean;
    suppliedToken: number; suppliedUsd: number; borrowedUsd: number;
    withdrawToken: number; withdrawUsd: number; remainingToken: number; remainingUsd: number;
    maxWithdrawable: number; fullWithdraw: boolean;
    ltvCurrentPct: number; ltvAfterPct: number; liquidationLtvPct: number;
    hfCurrent: number; hfAfter: number; hfLabel: string; hfClass: string; errorMsg?: string;
  } | null {
    const r = this.kaminoReserve();
    if (!r) return null;
    const price = this.kaminoReservePrice(r);
    const supplyApyPct = r.supplyApyNum * 100;

    const ob = this.kaminoObligation();
    const suppliedUsd = ob ? parseFloat(ob.depositedValue) || 0 : 0;
    const borrowedUsd = ob ? parseFloat(ob.borrowedValue) || 0 : 0;
    const borrowLimit = ob ? parseFloat(ob.borrowLimit) || 0 : 0;
    const liqLimitUsd = ob ? parseFloat(ob.borrowLiquidationLimit) || 0 : 0;
    const liquidationLtvPct = suppliedUsd > 0 ? (liqLimitUsd / suppliedUsd) * 100 : 0;

    // Supplied amount of THIS token: prefer the matching deposit line item; fall
    // back to the obligation total (accurate for the common single-supply case).
    const norm = (s: string) => (s.toUpperCase() === 'WSOL' ? 'SOL' : s.toUpperCase());
    const depLine = (ob?.deposits ?? []).find(
      d => d.reserveAddress === r.reserve || norm(d.symbol) === norm(r.symbol));
    const suppliedToken = depLine ? (parseFloat(depLine.amount) || 0) : (price > 0 ? suppliedUsd / price : 0);
    const hasSupply = suppliedUsd > 0;
    const hasDebt = borrowedUsd > 0;

    const amount = parseFloat(this.getEditParam('amount') || '0') || 0;
    const withdrawToken = Math.min(amount, suppliedToken);
    const withdrawUsd = Math.min(amount * price, suppliedUsd);
    const remainingUsd = Math.max(0, suppliedUsd - withdrawUsd);
    const remainingToken = Math.max(0, suppliedToken - withdrawToken);

    // Max withdrawable: with no debt the whole supply; with debt, leave enough
    // collateral to still back it (keep borrowed ≤ remaining borrow-limit).
    const maxWithdrawUsd = !hasDebt
      ? suppliedUsd
      : borrowLimit > 0 ? Math.max(0, suppliedUsd * (1 - borrowedUsd / borrowLimit)) : 0;
    const maxWithdrawable = price > 0 ? maxWithdrawUsd / price : 0;
    const fullWithdraw = hasSupply && amount >= suppliedToken - 1e-9;

    const ltvCurrentPct = suppliedUsd > 0 ? (borrowedUsd / suppliedUsd) * 100 : 0;
    const ltvAfterPct = remainingUsd > 0 ? (borrowedUsd / remainingUsd) * 100 : (borrowedUsd > 0 ? Infinity : 0);
    // Liq-limit scales with the collateral left behind (single-collateral case).
    const frac = suppliedUsd > 0 ? remainingUsd / suppliedUsd : 0;
    const liqLimitAfter = liqLimitUsd * frac;
    const hfCurrent = borrowedUsd > 0 ? liqLimitUsd / borrowedUsd : Infinity;
    const hfAfter = borrowedUsd > 0 ? liqLimitAfter / borrowedUsd : Infinity;
    const hfApplies = borrowedUsd > 0;
    const hfClass = !hfApplies || (isFinite(hfAfter) && hfAfter >= 1.6) || !isFinite(hfAfter)
      ? 'safe' : hfAfter >= 1.2 ? 'caution' : 'danger';
    const hfLabel = !hfApplies ? '—' : !isFinite(hfAfter) ? '∞' : hfAfter.toFixed(2);

    let errorMsg: string | undefined;
    if (amount > 0 && !hasSupply) {
      errorMsg = 'You have no supplied balance on Kamino for this token.';
    } else if (amount > 0 && amount > suppliedToken + 1e-9) {
      errorMsg = `You only have ${suppliedToken.toFixed(suppliedToken < 1 ? 6 : 2)} ${r.symbol} supplied — withdraw that or less.`;
    } else if (amount > 0 && hasDebt && maxWithdrawable > 0 && amount > maxWithdrawable + 1e-9) {
      errorMsg = `Withdrawing this much would leave too little collateral for your debt. Withdraw ≤ ${maxWithdrawable.toFixed(maxWithdrawable < 1 ? 6 : 2)} ${r.symbol}, or repay first.`;
    }

    return {
      symbol: r.symbol, supplyApyPct, price,
      hasSupply, hasDebt, suppliedToken, suppliedUsd, borrowedUsd,
      withdrawToken, withdrawUsd, remainingToken, remainingUsd,
      maxWithdrawable, fullWithdraw,
      ltvCurrentPct, ltvAfterPct: isFinite(ltvAfterPct) ? ltvAfterPct : 0, liquidationLtvPct,
      hfCurrent: isFinite(hfCurrent) ? hfCurrent : 0, hfAfter: isFinite(hfAfter) ? hfAfter : 0,
      hfLabel, hfClass, errorMsg,
    };
  }

  /** Fill the withdraw amount to the max safely-withdrawable supplied balance. */
  setKaminoWithdrawMax(): void {
    const s = this.kaminoWithdrawStats;
    if (!s) return;
    // No debt → whole supply; with debt → the collateral-safe max.
    const target = s.hasDebt ? Math.min(s.suppliedToken, s.maxWithdrawable) : s.suppliedToken;
    if (target <= 0) return;
    this.setEditParam('amount', String(Math.floor(target * 1e6) / 1e6));
  }

  /** Fetch the chosen K-Vault's live metrics (APY, TVL, share price, holders). */
  private async loadKaminoVaultInfo(): Promise<void> {
    const vault = (this.editParams()['vault'] ?? '').trim();
    if (vault.length < 32) { this.kaminoVaultMetrics.set(null); return; }
    this.kaminoVaultLoading.set(true);
    try {
      this.kaminoVaultMetrics.set(await this.kamino.getVaultMetrics(vault));
    } catch {
      this.kaminoVaultMetrics.set(null);
    } finally {
      this.kaminoVaultLoading.set(false);
    }
  }

  /**
   * Live K-Vault deposit projection. Depositing `amount` tokens mints
   * ≈ amount ÷ tokensPerShare vault shares and earns the vault's APY. TVL =
   * invested + available (USD). All figures come from the vault's live /metrics —
   * never fabricated.
   */
  get kaminoVaultStats(): {
    apyPct: number; apy30dPct: number; tvlUsd: number; holders: number;
    tokenPrice: number; depositUsd: number; sharesReceived: number;
  } | null {
    const m = this.kaminoVaultMetrics();
    if (!m) return null;
    const apyPct = (parseFloat(m.apy) || 0) * 100;
    const apy30dPct = (parseFloat(m.apy30d) || 0) * 100;
    const tvlUsd = (parseFloat(m.tokensInvestedUsd) || 0) + (parseFloat(m.tokensAvailableUsd) || 0);
    const tokenPrice = parseFloat(m.tokenPrice) || 0;
    const tokensPerShare = parseFloat(m.tokensPerShare) || 0;
    const amount = parseFloat(this.getEditParam('amount') || '0') || 0;
    return {
      apyPct, apy30dPct, tvlUsd, holders: m.numberOfHolders || 0, tokenPrice,
      depositUsd: amount * tokenPrice,
      sharesReceived: tokensPerShare > 0 ? amount / tokensPerShare : 0,
    };
  }

  /**
   * Load everything the K-Vault WITHDRAW card needs: the user's live position
   * in the vault (shares held + USD value + underlying tokenMint for the real
   * icon) plus the vault's live metrics (APY, TVL, share price).
   *
   * The LLM may pass the vault by NAME ("SOL") or leave it implicit. Positions
   * come back keyed by vault ADDRESS, so we match by address when we have one,
   * otherwise fall back to the user's sole vault position (the common "withdraw
   * my vault position" case). Once matched, we pin the real vault address into
   * the params so the built withdraw tx targets the exact vault we're pricing.
   */
  private async loadKaminoVaultWithdrawInfo(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet) { this.kaminoVaultPosition.set(null); return; }
    this.kaminoVaultLoading.set(true);
    try {
      const positions = await this.kamino.getVaultPositions(wallet);
      const want = (this.editParams()['vault'] ?? '').trim();
      let pos = want.length >= 32 ? positions.find(p => p.vaultAddress === want) : undefined;
      // Named/implicit vault with a single position → that's unambiguously it.
      if (!pos && positions.length === 1) pos = positions[0];
      this.kaminoVaultPosition.set(pos ?? null);
      const vaultAddr = pos?.vaultAddress || (want.length >= 32 ? want : '');
      if (pos?.vaultAddress) this.setEditParam('vault', pos.vaultAddress);
      // The positions payload has no tokenMint — resolve the vault's underlying
      // token from the vault list so the card shows the real icon/symbol
      // (e.g. PYUSD for the "Ethena PYUSD Prime" vault) instead of the address.
      if (vaultAddr) {
        const [metrics, vaults] = await Promise.all([
          this.kamino.getVaultMetrics(vaultAddr),
          this.kamino.getVaults(),
        ]);
        this.kaminoVaultMetrics.set(metrics);
        const mint = vaults.find(v => v.address === vaultAddr)?.tokenMint ?? pos?.tokenMint ?? '';
        this.kaminoVaultTokenMint.set(mint);
        if (mint) this.tokenRegistry.resolveAsync(mint);
        // Resolve "all"/empty to the actual share figure up front: the backend
        // requires a positive number (it rejects "all"), and the user wants to
        // see the real amount, not the word "all". Uses the full share position.
        const rawAmt = (this.getEditParam('ktokenAmount') || '').trim().toLowerCase();
        const shares = parseFloat(pos?.shares ?? '0') || 0;
        if (shares > 0 && (rawAmt === '' || rawAmt === 'all' || rawAmt === 'max' || rawAmt === 'full')) {
          this.setEditParam('ktokenAmount', String(shares));
        }
      } else {
        this.kaminoVaultMetrics.set(null);
      }
    } catch {
      this.kaminoVaultPosition.set(null);
      this.kaminoVaultMetrics.set(null);
    } finally {
      this.kaminoVaultLoading.set(false);
    }
  }

  /**
   * Live K-Vault withdraw projection. Redeeming `ktokenAmount` shares (or "all"
   * = the full position) returns ≈ shares × tokensPerShare underlying tokens.
   * USD is prorated from the position's real `sharesUsd` — more accurate than
   * price × tokens — never fabricated. Null until the position/metrics land.
   */
  get kaminoVaultWithdrawStats(): {
    apyPct: number; apy30dPct: number; tvlUsd: number; holders: number;
    sharesHeld: number; positionUsd: number; withdrawShares: number;
    receiveTokens: number; receiveUsd: number; fullWithdraw: boolean;
    tokenSymbol: string; tokenLogo: string | null;
  } | null {
    const m = this.kaminoVaultMetrics();
    const pos = this.kaminoVaultPosition();
    if (!m && !pos) return null;
    const apyPct = (parseFloat(m?.apy ?? '0') || 0) * 100;
    const apy30dPct = (parseFloat(m?.apy30d ?? '0') || 0) * 100;
    const tvlUsd = m ? (parseFloat(m.tokensInvestedUsd) || 0) + (parseFloat(m.tokensAvailableUsd) || 0) : 0;
    const tokenPrice = parseFloat(m?.tokenPrice ?? '0') || 0;
    const tokensPerShare = parseFloat(m?.tokensPerShare ?? '0') || 0;
    const sharesHeld = parseFloat(pos?.shares ?? '0') || 0;
    const raw = (this.getEditParam('ktokenAmount') || '').trim().toLowerCase();
    const isWord = raw === '' || raw === 'all' || raw === 'max' || raw === 'full';
    const withdrawShares = isWord ? sharesHeld : (parseFloat(raw) || 0);
    // "Full position" wording also applies once "all" is resolved to the exact
    // share figure (which equals the held balance).
    const fullWithdraw = isWord || (sharesHeld > 0 && Math.abs(withdrawShares - sharesHeld) < 1e-9);
    // 1 share ≈ tokensPerShare underlying tokens; USD via the vault's tokenPrice.
    // The positions API carries no USD, so derive it from live metrics.
    const receiveTokens = withdrawShares * tokensPerShare;
    const receiveUsd = receiveTokens * tokenPrice;
    const positionUsd = sharesHeld * tokensPerShare * tokenPrice;
    const mintForIcon = this.kaminoVaultTokenMint() || pos?.tokenMint || this.getEditParam('token') || '';
    const td = mintForIcon
      ? this.resolveTokenDisplay(mintForIcon)
      : { symbol: '', name: '', logoURI: undefined };
    return {
      apyPct, apy30dPct, tvlUsd, holders: m?.numberOfHolders ?? 0,
      sharesHeld, positionUsd, withdrawShares, receiveTokens, receiveUsd, fullWithdraw,
      tokenSymbol: td.symbol, tokenLogo: td.logoURI ?? null,
    };
  }

  /** Set the withdraw amount to the user's full vault-share position. */
  setKaminoVaultWithdrawMax(): void {
    const pos = this.kaminoVaultPosition();
    const shares = parseFloat(pos?.shares ?? '0') || 0;
    if (shares > 0) this.setEditParam('ktokenAmount', String(shares));
  }

  selectCollateral(opt: CollateralOption): void {
    this.selectedCollateral.set(opt);
    // Keep editParams in sync so the built tx uses the chosen collateral AND the
    // exact vault whose rate/LTV the card is showing — each collateral maps to a
    // specific vault, and the borrow tx must operate on that one (not the
    // cheapest-rate vault for the debt token, which may pair a different asset).
    this.setEditParam('collateral', opt.symbol);
    const vault = this.borrowVaults().find(v => v.collateralMint === opt.mint);
    if (vault) this.setEditParam('vaultId', String(vault.vaultId));
  }

  /** Set the collateral amount to the user's full balance of the picked asset. */
  setMaxCollateral(): void {
    const sel = this.selectedCollateral();
    if (sel && sel.balance > 0) this.setEditParam('collateralAmount', sel.balance.toString());
  }

  openQuickSwap(): void { this.showQuickSwap.set(true); this.loadQsBalances(); }
  private async loadQsBalances(): Promise<void> { this.qsBalancesLoading.set(true); try { this.qsTokenList.set([]); } finally { this.qsBalancesLoading.set(false); } }
  async executeQuickSwap(): Promise<void> { this.qsStatus.set('swapping'); try { this.qsStatus.set('done'); } catch (e: any) { this.qsError.set(e?.message || 'Swap failed'); this.qsStatus.set('error'); } }

  async showTransactionPreview(): Promise<void> {
    this.showPreview.set(true); this.loadingPreview.set(true);
    // The safety check runs alongside the preview rather than after it — this
    // is the last screen before a signature, and it is where someone who has
    // never heard of a freeze authority needs to be told about one.
    void this.runTokenSafety();
    try {
      const previewParams: Record<string, string> = Object.fromEntries(
        Object.entries(this.editParams()).filter(([, v]) => v !== undefined),
      ) as Record<string, string>;
      this.preview.set(await this.previewService.preview({ ...this.action, params: previewParams }));
    }
    catch { this.preview.set(null); } finally { this.loadingPreview.set(false); }
  }
  formatPreviewChange(change: BalanceChange): string { return this.previewService.formatChange(change); }
  formatPreviewUsd(value: number): string { return this.previewService.formatUsd(value); }
  cancelPreview(): void { this.showPreview.set(false); }
  confirmPreview(): void { this.showPreview.set(false); this.approve(); }

  ngOnDestroy(): void {
    this.stopEvmDiscovery();
    this.stopSubmittedTick();
    this.stopQuotePolling();
    if (this._swapEstimateTimer) clearTimeout(this._swapEstimateTimer);
    if (this._pumpEstimateTimer) clearTimeout(this._pumpEstimateTimer);
  }

  /** Start counting how long the tx has been waiting for confirmation, and
   *  run a parallel `getSignatureStatus` poll every 3s. The poll exists
   *  because confirmTransaction relies on a websocket subscription that
   *  silently dies in common scenarios (laptop sleep, RPC flap, mobile
   *  background) — when that happens the websocket never fires and the user
   *  would otherwise sit on a spinner forever. The poll catches the same
   *  state via plain HTTP, so the spinner clears the moment the tx lands. */
  private startSubmittedTick(): void {
    this.submittedAt = Date.now();
    this.submittedElapsedSec.set(0);
    this.elapsedTicker = setInterval(() => {
      const sec = Math.floor((Date.now() - this.submittedAt) / 1000);
      this.submittedElapsedSec.set(sec);
      // Poll RPC every 3s for the actual on-chain status. Whichever signal
      // (websocket vs poll) wins first promotes the UI to 'confirmed'.
      if (sec > 0 && sec % 3 === 0 && this.status() === 'submitted') {
        void this.pollSignatureStatus(/*finalCheck*/ false);
      }
      // 60s upper bound. If neither the websocket nor 20+ HTTP polls have
      // surfaced the tx, run one last definitive check before declaring failure.
      // Not for a bridge: sixty seconds is an ordinary wait on the far chain,
      // and stopping the ticker there would freeze the elapsed counter while
      // the transfer was still perfectly healthy.
      if (sec >= 60 && this.status() === 'submitted' && !this.isRelayBridge()) {
        this.stopSubmittedTick();
        void this.pollSignatureStatus(/*finalCheck*/ true);
      }
    }, 1000);
  }

  /** Direct RPC check for the submitted signature. `finalCheck=true` means
   *  this is the last attempt — if not confirmed, surface the timeout UI. */
  private async pollSignatureStatus(finalCheck: boolean): Promise<void> {
    // A bridge is not this card's to settle. Its origin transaction confirms
    // within seconds and means only that the funds left; the arrival is
    // watched separately, against Relay. Left in, this poll would promote the
    // card to "confirmed" on the deposit — and on an EVM origin it would be
    // asking Solana about a hash Solana has never heard of, then calling the
    // silence a failure.
    if (this.isRelayBridge()) return;
    const sig = this.txSignature();
    if (!sig) {
      if (finalCheck) {
        this.status.set('error');
        this.errorMessage.set('Confirmation timed out. The transaction may still land — open Solscan or click Re-check.');
      }
      return;
    }
    try {
      const conn = createSolanaConnection('confirmed');
      const status = await conn.getSignatureStatus(sig, { searchTransactionHistory: true });
      const value = status.value;
      if (value?.err) {
        this.stopSubmittedTick();
        this.status.set('error');
        this.errorMessage.set('Transaction failed on-chain. See explorer for details.');
        return;
      }
      if (value?.confirmationStatus === 'confirmed' || value?.confirmationStatus === 'finalized') {
        this.stopSubmittedTick();
        this.status.set('confirmed');
        this.persistResult({ status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: this.lastSubmittedParams ?? this.action.params, swapView: this.lastSwapView ?? undefined });
        return;
      }
    } catch { /* RPC blip — try again on the next 3s tick */ }
    if (finalCheck) {
      this.status.set('error');
      this.errorMessage.set('Confirmation timed out. The transaction may still land — open Solscan or click Re-check.');
    }
  }

  private stopSubmittedTick(): void {
    if (this.elapsedTicker) {
      clearInterval(this.elapsedTicker);
      this.elapsedTicker = null;
    }
  }

  /** Manual confirmation re-check via Solana RPC. Picks up txs whose
   *  websocket-based confirmation tracking fell over (laptop slept, Helius
   *  flapped, etc.) without forcing the user to retry the whole flow. */
  async recheckTxStatus(): Promise<void> {
    const sig = this.txSignature();
    if (!sig || this.recheckInProgress()) return;
    this.recheckInProgress.set(true);
    try {
      const conn = createSolanaConnection('confirmed');
      const status = await conn.getSignatureStatus(sig, { searchTransactionHistory: true });
      const value = status.value;
      if (value?.err) {
        this.stopSubmittedTick();
        this.status.set('error');
        this.errorMessage.set('Transaction failed on-chain. See explorer for details.');
      } else if (value?.confirmationStatus === 'confirmed' || value?.confirmationStatus === 'finalized') {
        this.stopSubmittedTick();
        this.status.set('confirmed');
        this.persistResult({ status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: this.lastSubmittedParams ?? this.action.params, swapView: this.lastSwapView ?? undefined });
      }
      // else: still pending — leave UI as-is.
    } catch {
      // Network error — leave the UI as-is so the user can try again.
    } finally {
      this.recheckInProgress.set(false);
    }
  }

  requestUndo(): void {
    if (!this.snapshotId) return;
    this.undoInProgress.set(true);
    this.rollbackService.undo(this.snapshotId, {
      onQuote: () => {}, onSign: () => {}, onSubmit: () => {},
      onConfirm: () => { this.undoInProgress.set(false); },
    }).catch(() => this.undoInProgress.set(false));
  }

  onTokenImgError(event: Event, mintOrSymbol: string): void {
    const img = event.target as HTMLImageElement;
    if (img.dataset['fallback']) { img.style.display = 'none'; return; }
    const token = this.tokenRegistry.getToken(mintOrSymbol) ?? this.tokenRegistry.getBySymbol(mintOrSymbol);
    const address = token?.address ?? (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mintOrSymbol) ? mintOrSymbol : null);
    if (address) {
      img.dataset['fallback'] = '1';
      // img.jup.ag is dead (host no longer resolves), so a failed primary logo
      // used to fall through to a guaranteed-broken URL → letter placeholder.
      // Raydium's icon CDN is live and covers the major tokens users actually
      // trade here; an obscure mint that 404s here just re-fires (error) and
      // hides, landing on the same letter fallback as before.
      img.src = `https://img-v1.raydium.io/icon/${address}.png`;
    } else {
      img.style.display = 'none';
    }
  }

  resolveTokenDisplay(mintOrSymbol: string): { symbol: string; name: string; logoURI?: string; decimals?: number } {
    this.tokenRegistry.version(); // reactive dependency — re-evaluates whenever a token is fetched
    if (!mintOrSymbol) return { symbol: '??', name: 'Unknown' };
    // 1. Lookup by mint address
    const byMint = this.tokenRegistry.getToken(mintOrSymbol);
    if (byMint) {
      this.tokenRegistry.resolveAsync(byMint.address); // no-op if logo already set
      return { symbol: byMint.symbol, name: byMint.name, logoURI: byMint.logoURI ?? undefined, decimals: byMint.decimals };
    }
    // 2. Lookup by symbol ("SOL", "USDC" etc.) — always re-read from tokenMap to get latest logo
    const bySymbol = this.tokenRegistry.getBySymbol(mintOrSymbol);
    if (bySymbol) {
      const latest = this.tokenRegistry.getToken(bySymbol.address) ?? bySymbol;
      this.tokenRegistry.resolveAsync(bySymbol.address); // no-op if logo already set
      return { symbol: latest.symbol, name: latest.name, logoURI: latest.logoURI ?? undefined, decimals: latest.decimals };
    }
    // 3. Unknown mint — trigger async fetch (will update version when done)
    this.tokenRegistry.resolveAsync(mintOrSymbol);
    return { symbol: mintOrSymbol.length > 8 ? mintOrSymbol.slice(0, 4) + '...' : mintOrSymbol, name: mintOrSymbol, decimals: 9 };
  }
}
