/**
 * ActionCardComponent
 *
 * Renders parsed actions (swaps, transfers, stakes, token launches, etc.)
 * with protocol-specific branding, editable fields, and execution flow.
 */
import { Component, Input, Output, EventEmitter, OnInit, OnChanges, OnDestroy, SimpleChanges, ViewChild, ElementRef, inject, signal, computed, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { ParsedAction, IntentParserService } from '../../services/intent-parser.service';
import { SolanaActionService, ValidatorInfo } from '../../services/solana-action.service';
import { ChatApiService, StoredActionResult } from '../../services/chat-api.service';
import { UploadService } from '@core/services/upload.service';
import { JupiterLendService, LEND_SUPPORTED_ASSETS, LendActionInfo } from '@core/services/market/jupiter-lend.service';
import { WalletService } from '@core/services/wallet.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { TransactionPreviewService, TransactionPreview, BalanceChange } from '../../services/transaction-preview.service';
import { JargonTooltipComponent } from '@shared/components/jargon-tooltip/jargon-tooltip.component';
import { JupiterSwapService } from '@core/services/market/jupiter-swap.service';
import { RollbackService } from '@core/services/rollback.service';
import { SolanaRpcService } from '../../../../features/portfolio/services/solana-rpc.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { JitoService } from '@core/services/market/jito.service';
import { MarinadeService } from '@core/services/market/marinade.service';
import { JupSolService } from '@core/services/market/jupsol.service';
import { MeteoraService } from '@core/services/market/meteora.service';
import { computeDlmmRatio, rangeFromSpread, DlmmStrategy } from '@core/services/market/dlmm-math';
import { environment } from '../../../../../environments/environment';

const ACTION_RESULTS_KEY = 'oprai-action-results';

/**
 * Resize an image File to a square of `size` × `size` px (center-crop + scale).
 * Returns a new JPEG File. Videos and non-image files are returned unchanged.
 * pump.fun requires min. 1000×1000px square for token images.
 */
function resizeImageToSquare(file: File, size: number): Promise<File> {
  if (!file.type.startsWith('image/')) return Promise.resolve(file);
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
      // Center-crop: take the largest centered square from the source image.
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const sx = (img.naturalWidth - side) / 2;
      const sy = (img.naturalHeight - side) / 2;
      ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
      canvas.toBlob(blob => {
        if (!blob) { reject(new Error('Failed to encode resized image')); return; }
        const baseName = file.name.replace(/\.[^.]+$/, '') || 'image';
        resolve(new File([blob], baseName + '.jpg', { type: 'image/jpeg' }));
      }, 'image/jpeg', 0.92);
    };
    img.onerror = () => { URL.revokeObjectURL(blobUrl); reject(new Error('Failed to load image for resize')); };
    img.src = blobUrl;
  });
}

/**
 * User-facing strings for the simulation error codes we already know how
 * to classify in `SolanaActionService.parseSimulationError`. Anything not
 * in this map falls through to the generic-fallback branch below.
 */
const SIM_ERROR_MESSAGES: Record<string, string> = {
  insufficient_tokens:
    "Not enough balance for this action. Check the token amount, fees, and the rent buffer left for SOL.",
  slippage_exceeded:
    "Price moved too much during quote. Try increasing slippage tolerance, or split into smaller trades.",
};

/**
 * Best-effort English translation of a Marinade/Jito/Marinade-style custom
 * error code. These are the codes we've seen in practice; everything else
 * gets a "code N" fallback.
 */
const SIM_GENERIC_HINTS: Record<string, string> = {
  // Marinade — most-common minimum-stake/account-state codes seen in the
  // wild. The protocol does not publish a stable enum index, so labels are
  // intentionally non-committal.
  '101':
    "The stake amount is below the protocol minimum or your wallet does not meet the protocol's prerequisites. Try a larger amount (Marinade requires ≥ 0.01 SOL above rent + fees).",
  '102':
    "Account state precondition failed. Make sure your wallet is funded and not currently liquidating a stake.",
};

function sanitizeErrorMessage(msg: string): string {
  // Strip internal API instructions (e.g. "Upload via POST /upload/...") that leak from backend errors.
  let out = msg.replace(/\.\s+(Upload|Call|Use|POST|GET|See|Retry)\s+.*/i, '.').trim();

  // Translate machine codes from `parseSimulationError`. The shape is one of:
  //   sim:insufficient_tokens
  //   sim:slippage_exceeded
  //   sim:generic:<code-or-text>
  // We look for the prefix anywhere in the string so it still works after a
  // wrapper has prepended its own "Failed to execute action: " label.
  const known = out.match(/sim:(insufficient_tokens|slippage_exceeded)/);
  if (known) {
    return SIM_ERROR_MESSAGES[known[1]] ?? out;
  }
  const generic = out.match(/sim:generic:([A-Za-z0-9_-]+)/);
  if (generic) {
    const code = generic[1];
    const hint = SIM_GENERIC_HINTS[code];
    if (hint) return hint;
    if (/^\d+$/.test(code)) {
      return `The transaction simulation failed (program error ${code}). The most common causes are insufficient balance for fees + rent, or an amount below the protocol's minimum. Try a slightly larger amount.`;
    }
    return "The transaction simulation failed before signing. Check that your wallet has enough SOL for fees and that the amount meets the protocol's minimum.";
  }

  return out;
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
  orca:      { name: 'Orca',       icon: 'assets/icons/protocols/orca.svg',       accent: '#06B6D4', accentBg: 'rgba(6,182,212,0.12)' },
  raydium:   { name: 'Raydium',    icon: 'assets/icons/protocols/raydium.svg',    accent: '#8B5CF6', accentBg: 'rgba(139,92,246,0.12)' },
  marginfi:  { name: 'MarginFi',   icon: 'assets/icons/protocols/marginfi.svg',   accent: '#F59E0B', accentBg: 'rgba(245,158,11,0.12)' },
  meteora:   { name: 'Meteora',    icon: 'assets/icons/protocols/meteora.webp',    accent: '#10B981', accentBg: 'rgba(16,185,129,0.12)' },
  marinade:  { name: 'Marinade',   icon: 'assets/icons/protocols/marinade.webp',  accent: '#22C55E', accentBg: 'rgba(34,197,94,0.12)' },
  solend:    { name: 'Solend',     icon: 'assets/icons/protocols/solend.svg',     accent: '#3B82F6', accentBg: 'rgba(59,130,246,0.12)' },
  tensor:    { name: 'Tensor',     icon: 'assets/icons/protocols/tensor.webp',    accent: '#00D4AA', accentBg: 'rgba(0,212,170,0.12)' },
  'magic-eden': { name: 'Magic Eden', icon: 'assets/icons/protocols/magic-eden.svg', accent: '#E42575', accentBg: 'rgba(228,37,117,0.12)' },
  streamflow:{ name: 'Streamflow', icon: 'assets/icons/protocols/streamflow.svg', accent: '#00D4FF', accentBg: 'rgba(0,212,255,0.12)' },
  pumpfun:   { name: 'pump.fun',   icon: 'assets/icons/protocols/pumpfun.png',    accent: '#AD6DFF', accentBg: 'rgba(173,109,255,0.12)' },
  default:   { name: 'Solana',     icon: 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png', accent: '#9945FF', accentBg: 'rgba(153,69,255,0.10)' },
};

function getProtocolKey(action: ParsedAction): string {
  const p = (action.params['protocol'] ?? '').toLowerCase();
  if (p && PROTOCOL_CONFIGS[p]) return p;
  const t = action.type;
  if (t === 'swap' || t === 'limit_order' || t === 'dca') return 'jupiter';
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
    me_deposit: 'Deposit to ME Escrow', me_withdraw: 'Withdraw from ME Escrow',
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

function getActionFields(action: ParsedAction): FieldDef[] {
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
    fields.push(
      { key: 'inputMint', label: 'From Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: action.params['inputMint'] ?? 'SOL', required: true },
      { key: 'outputMint', label: 'To Token', type: 'token', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
      { key: 'onlyDirectRoutes', label: 'Direct routes only', type: 'toggle', half: true },
    );
  } else if (t === 'transfer') {
    fields.push(
      { key: 'to', label: 'Recipient', type: 'address', placeholder: 'Enter wallet address...', required: true },
      { key: 'token', label: 'Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'stake' || t === 'unstake') {
    fields.push({ key: 'amount', label: 'Amount (SOL)', type: 'number', placeholder: '0', suffix: 'SOL', required: true });
  } else if (t === 'native_stake') {
    // Validator picker is rendered separately via isNativeStake() + validators signal
    fields.push(
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '1', suffix: 'SOL', required: true, min: 1, step: '0.01', hint: 'Minimum 1 SOL' },
      { key: 'validatorVoteAccount', label: 'Validator Vote Account', type: 'address', placeholder: 'Validator pubkey...', required: true },
    );
  } else if (t === 'native_stake_deactivate') {
    fields.push({ key: 'stakeAccount', label: 'Stake Account', type: 'address', placeholder: 'Stake account pubkey...', required: true, hint: 'Cooldown ~2-3 days before withdrawal' });
  } else if (t === 'native_stake_withdraw') {
    fields.push(
      { key: 'stakeAccount', label: 'Stake Account', type: 'address', placeholder: 'Stake account pubkey...', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', hint: 'Lamports, or "all" to close the account' },
    );
  } else if (t === 'native_stake_split') {
    fields.push(
      { key: 'stakeAccount', label: 'Source Stake Account', type: 'address', placeholder: 'Stake account pubkey...', required: true },
      { key: 'amount', label: 'Amount to Split', type: 'number', placeholder: '1', suffix: 'SOL', required: true, min: 1, hint: 'Minimum 1 SOL' },
    );
  } else if (t === 'native_stake_merge') {
    fields.push(
      { key: 'destinationStakeAccount', label: 'Destination Account', type: 'address', placeholder: 'Destination stake account...', required: true, hint: 'Keeps the balance after merge' },
      { key: 'sourceStakeAccount', label: 'Source Account', type: 'address', placeholder: 'Source stake account...', required: true, hint: 'Will be closed after merge' },
    );
  } else if (['lend', 'withdraw', 'borrow', 'repay'].includes(t)) {
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
      { key: 'targetPrice', label: 'Target Price', type: 'number', placeholder: '0', required: true, hint: 'Price per output token in input token' },
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
      { key: 'minOutPerCycle', label: 'Min Out/Cycle', type: 'number', placeholder: '0', half: true },
      { key: 'maxOutPerCycle', label: 'Max Out/Cycle', type: 'number', placeholder: '0', half: true },
    );
  } else if (t === 'cancel_dca') {
    fields.push(
      { key: 'dcaAccount', label: 'DCA Account', type: 'address', placeholder: 'DCA pubkey...', required: true },
    );
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
      { key: 'mint', label: 'Token Mint', type: 'address', placeholder: 'Mint address, "dust", or "empty_accounts"', required: true },
      { key: 'amount', label: 'Amount', type: 'text', placeholder: 'all', hint: '"all" burns entire balance; "dust" burns all dust' },
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
    fields.push(
      { key: 'poolId', label: 'Pool ID', type: 'address', placeholder: 'Pool address...', required: true },
      { key: 'tokenA', label: 'Token A', type: 'token', required: true },
      { key: 'tokenB', label: 'Token B', type: 'token', required: true },
      { key: 'inputAmount', label: 'Deposit Amount', type: 'number', placeholder: '0', required: true },
      { key: 'minPrice', label: 'Min Price', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'maxPrice', label: 'Max Price', type: 'number', placeholder: '0', required: true, half: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '0.5', suffix: '%', half: true, min: 0, max: 100, step: '0.1', divisor: 100 },
    );
  } else if (t === 'raydium_close_position') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
    );
  } else if (t === 'raydium_increase_position') {
    fields.push(
      { key: 'positionId', label: 'Position ID', type: 'address', placeholder: 'Position address...', required: true },
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
  } else if (t === 'kamino_multiply_open' || t === 'kamino_long_open' || t === 'kamino_short_open') {
    fields.push(
      { key: 'collateralToken', label: 'Collateral Token', type: 'token', required: true },
      { key: 'collateralAmount', label: 'Collateral Amount', type: 'number', placeholder: '0', required: true },
      { key: 'leverage', label: 'Leverage', type: 'number', placeholder: '2', required: true, min: 1, max: 10, step: '0.1', half: true },
    );
  } else if (t === 'kamino_multiply_add') {
    fields.push(
      { key: 'collateralToken', label: 'Collateral Token', type: 'token', required: true },
      { key: 'collateralAmount', label: 'Additional Amount', type: 'number', placeholder: '0', required: true },
    );
  } else if (t === 'kamino_multiply_withdraw') {
    fields.push(
      { key: 'collateralToken', label: 'Collateral Token', type: 'token', required: true },
      { key: 'percent', label: 'Withdraw %', type: 'number', placeholder: '50', suffix: '%', required: true, min: 1, max: 100 },
    );
  } else if (t === 'kamino_multiply_close' || t === 'kamino_position_close') {
    // no required params — closes the active position
  } else if (t === 'kamino_claim_rewards') {
    // no required params — claims all pending rewards
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
  } else if (t === 'me_buy') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
      { key: 'seller', label: 'Seller', type: 'address', placeholder: "Seller's wallet (from listing)..." },
      { key: 'tokenAddress', label: 'Token Account', type: 'address', placeholder: 'Escrow token account...' },
    );
  } else if (t === 'me_buy_instruction') {
    fields.push(
      { key: 'buyer', label: 'Buyer', type: 'address', placeholder: 'Buyer wallet...', required: true },
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_buy_now') {
    fields.push(
      { key: 'buyer', label: 'Buyer', type: 'address', placeholder: 'Buyer wallet...', required: true },
      { key: 'seller', label: 'Seller', type: 'address', placeholder: 'Seller wallet...', required: true },
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'tokenATA', label: 'Token ATA', type: 'address', placeholder: "Seller's ATA...", required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_list') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
      { key: 'expiry', label: 'Expiry', type: 'number', placeholder: '0 = no expiry', hint: 'Unix timestamp' },
    );
  } else if (t === 'me_sell') {
    fields.push(
      { key: 'seller', label: 'Seller', type: 'address', placeholder: 'Seller wallet...', required: true },
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'tokenAccount', label: 'Token Account', type: 'address', placeholder: 'Token account...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_cancel_listing') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
      { key: 'tokenAddress', label: 'Token Account', type: 'address', placeholder: 'Escrow token account...' },
    );
  } else if (t === 'me_buy_cancel' || t === 'me_sell_cancel') {
    fields.push(
      { key: 'seller', label: 'Seller', type: 'address', placeholder: 'Seller wallet...', required: true },
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'tokenAccount', label: 'Token Account', type: 'address', placeholder: 'Token account...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_make_offer') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Offer Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
      { key: 'expiry', label: 'Expiry', type: 'number', placeholder: '0 = no expiry', hint: 'Unix timestamp' },
    );
  } else if (t === 'me_accept_offer') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
      { key: 'buyer', label: 'Buyer', type: 'address', placeholder: "Offer maker's wallet..." },
    );
  } else if (t === 'me_sell_now') {
    fields.push(
      { key: 'seller', label: 'Seller', type: 'address', placeholder: 'Seller wallet...', required: true },
      { key: 'buyer', label: 'Buyer', type: 'address', placeholder: 'Buyer wallet...', required: true },
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_cancel_offer') {
    fields.push(
      { key: 'mintAddress', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  } else if (t === 'me_buy_change_price' || t === 'me_sell_change_price') {
    fields.push(
      { key: 'tokenMint', label: 'NFT Mint', type: 'address', placeholder: 'NFT mint address...', required: true },
      { key: 'price', label: 'Current Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true, half: true },
      { key: 'newPrice', label: 'New Price', type: 'number', placeholder: '0', suffix: 'SOL', required: true, half: true },
    );
  } else if (t === 'me_deposit' || t === 'me_withdraw') {
    fields.push(
      { key: 'buyer', label: 'Wallet', type: 'address', placeholder: 'Wallet address...', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: 'SOL', required: true },
    );
  // ── Bridge / Cross-Chain ──────────────────────────────────────────────────
  } else if (t === 'relay_bridge' || t === 'bridge' || t === 'cross_chain_swap') {
    fields.push(
      { key: 'originChainId', label: 'Source Chain ID', type: 'number', placeholder: '900 (Solana)', required: true, half: true },
      { key: 'destinationChainId', label: 'Dest Chain ID', type: 'number', placeholder: '1 (Ethereum)', required: true, half: true },
      { key: 'originCurrency', label: 'From Token', type: 'token', required: true },
      { key: 'destinationCurrency', label: 'To Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', required: true },
      { key: 'slippageTolerance', label: 'Slippage', type: 'number', placeholder: '50', suffix: 'bps', half: true },
      { key: 'recipient', label: 'Recipient (optional)', type: 'address', placeholder: 'Dest wallet...', half: true },
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

@Component({
  selector: 'app-action-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, JargonTooltipComponent],
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
  private readonly walletService = inject(WalletService);
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

  /** Cache so a Meteora pool address is fetched once per card lifecycle even
   *  if `editParams.poolId` thrashes (e.g. on draft restore). */
  private _resolvedMeteoraPool: string | null = null;

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
  readonly editTwitter = signal('');
  readonly editTelegram = signal('');
  readonly editWebsite = signal('');
  readonly editMayhemMode = signal(false);
  readonly editCashback = signal(false);
  readonly editTokenizedAgent = signal(false);

  // Image
  readonly uploadingImage = signal(false);
  readonly imageUploadError = signal<string | null>(null);
  readonly bannerUrl = signal<string | null>(null);
  private uploadedImageUrl = signal<string | null>(null);
  readonly effectiveImageUrl = computed(() => this.uploadedImageUrl() || this.action?.params['image'] || this.action?.params['imageUrl'] || null);
  readonly isDragOver = signal(false);
  // Raw resized file kept for pump.fun IPFS upload at launch time
  private resizedImageFile: File | null = null;

  // Banner
  readonly uploadingBanner = signal(false);
  readonly bannerUploadError = signal<string | null>(null);
  readonly uploadedBannerUrl = signal<string | null>(null);
  readonly effectiveBannerUrl = computed(() => this.uploadedBannerUrl() || this.bannerUrl());

  // Advanced options
  readonly showAdvanced = signal(false);
  readonly editSlippage = signal('10');
  readonly editPriorityFee = signal('0.0005');

  // Edit params
  readonly editParams = signal<Record<string, string>>({});

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
      editMayhemMode: this.editMayhemMode(),
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

  // Validator picker (native_stake only)
  readonly validators = signal<ValidatorInfo[]>([]);
  readonly validatorsLoading = signal(false);
  readonly validatorCustom = signal(false); // toggle custom address input
  readonly isNativeStake = computed(() => this.action?.type === 'native_stake');

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
  readonly inputBalanceMint = computed(() =>
    this.editParams()['inputMint']
    ?? this.editParams()['inputToken']
    ?? this.editParams()['token']
    ?? this.editParams()['tokenA']
    ?? this.editParams()['tokenXMint']
    ?? '');
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
    const spread = Math.max(1, Math.floor(parseFloat(p['binSpread'] ?? '15')));
    const strategy = (p['strategy'] ?? 'spot') as DlmmStrategy;
    const range = rangeFromSpread(activeBinId, spread);
    return computeDlmmRatio({
      activeBinId,
      minBinId: range.minBinId,
      maxBinId: range.maxBinId,
      binStep,
      strategy,
    });
  });
  /** Decimals scale factor (Y per X has units `10^(decY - decX)` baked in). */
  private readonly dlmmDecimalScale = computed(() => {
    const p = this.editParams();
    const decX = parseInt(p['tokenADecimals'] ?? '9', 10);
    const decY = parseInt(p['tokenBDecimals'] ?? '9', 10);
    return Math.pow(10, decY - decX);
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

  private dlmmRatioEffect = effect(() => {
    // Two ratio sources: DLMM (range × strategy × active bin) or AMM
    // (constant-product reserves). DLMM wins when both are present.
    const dlmm = this.dlmmRatio();
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

    // Pick effective ratio. DLMM yPerX is in raw units (needs decimal
    // scale baked in); AMM yPerX is already human-unit. Distinguish so the
    // arithmetic is right either way.
    let humanYPerX: number | null = null;
    let humanXPerY: number | null = null;
    if (dlmm && dlmm.yPerX !== null && dlmm.xPerY !== null) {
      const scale = this.dlmmDecimalScale();
      humanYPerX = dlmm.yPerX * scale;
      humanXPerY = dlmm.xPerY / scale;
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

  readonly insufficientFunds = computed(() => {
    const bal = this.inputBalance();
    const amt = parseFloat(this.editParams()['amount'] ?? '0');
    return bal !== null && amt > 0 && amt > bal;
  });
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

  // Lend
  readonly lendInfo = signal<LendActionInfo | null>(null);
  readonly lendInfoLoading = signal(false);
  readonly borrowLiquidityMode = signal(false);

  // Collateral
  readonly collateralOptions = signal<Array<{ mint: string; symbol: string; balance: number; debtSymbol: string }>>([]);
  readonly selectedCollateral = signal<{ mint: string; symbol: string; balance: number; debtSymbol: string } | null>(null);
  readonly collateralInput = signal('');
  readonly borrowCapacity = signal<{ loading: boolean; maxBorrow: number } | null>(null);

  // Undo
  undoPrompt = false;
  readonly canUndo = computed(() => this.snapshotId !== null);
  readonly undoInProgress = signal(false);

  // Misc
  readonly copiedField = signal<string | null>(null);

  // Computed (template direct access)
  get protocolConfig(): ProtocolConfig { return PROTOCOL_CONFIGS[getProtocolKey(this.action)] ?? PROTOCOL_CONFIGS['default']; }
  get actionLabel(): string { return getActionLabel(this.action); }
  get actionFields(): FieldDef[] { return getActionFields(this.action); }
  get confirmButtonLabel(): string {
    const labels: Record<string, string> = { swap:'Swap', transfer:'Send', stake:'Stake', unstake:'Unstake', lend:'Deposit', withdraw:'Withdraw', borrow:'Borrow', repay:'Repay', add_liquidity:'Add Liquidity', remove_liquidity:'Remove Liquidity' };
    return labels[this.action?.type] ?? 'Confirm';
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
  readonly canApprove = computed(() => {
    if (this.borrowLiquidityMode()) { const c = this.borrowCapacity(); return c !== null && !c.loading && c.maxBorrow > 0; }
    return true;
  });
  readonly unverifiedDestination = computed(() => this.action?.warnUnverifiedDestination ?? false);

  // Quick swap getters (template direct access)
  get qsFromToken(): { mint: string; symbol: string; balance: number; logoURI?: string } | null { return this.qsTokenList().find(t => t.mint === this.qsFromMint()) ?? null; }
  get qsFromBalance(): number { return this.qsFromToken?.balance ?? 0; }
  get qsNeededAmount(): number { return Math.max(0, +(parseFloat(this.editParams()['amount'] ?? '0') - (this.inputBalance() ?? 0)).toFixed(6)); }
  get qsToTokenData(): { symbol: string; name: string; logoURI?: string } { return this.resolveTokenDisplay(this.inputBalanceMint()); }

  get borrowLiveStats(): { collateralUsd: number; maxBorrowable: number; healthFactor: number; hfClass: string; liquidationPriceLabel: string; errorMsg?: string } | null { return null; }

  ngOnInit(): void {
    if (this.action) this.initFromAction();
    this.maybeLoadLstRate();
  }
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['action'] && this.action) {
      this.initFromAction();
      this.lstExchangeRate.set(null);
      this.lstRateError.set(null);
      this.lstRateLoading.set(false);
      this.maybeLoadLstRate();
      return;
    }
    // Re-apply cached result if it arrives after the component was already initialized
    if (changes['cachedResult'] && this.cachedResult && this.status() === 'pending') {
      this.initFromAction();
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
    this.editParams.set({ ...p });
    this.editName.set(p['name'] ?? p['tokenName'] ?? '');
    this.editSymbol.set(p['symbol'] ?? p['ticker'] ?? '');
    this.editDescription.set(p['description'] ?? '');
    // initialBuyAmount comes from LLM as 'initialBuyAmount'; legacy UI uses 'initialBuy'
    this.editInitialBuy.set(p['initialBuyAmount'] ?? p['initial_buy_amount'] ?? p['initialBuy'] ?? '');
    this.editTwitter.set(p['twitter'] ?? '');
    this.editTelegram.set(p['telegram'] ?? '');
    this.editWebsite.set(p['website'] ?? '');
    this.editMayhemMode.set(p['mayhemMode'] === 'true' || p['mayhemMode'] === true as any);
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
          if (draft.editMayhemMode != null) this.editMayhemMode.set(draft.editMayhemMode);
          if (draft.editCashback != null) this.editCashback.set(draft.editCashback);
          // Tokenized Agent intentionally not restored — backend rejects it.
          if (draft.editSlippage != null) this.editSlippage.set(draft.editSlippage);
          if (draft.editPriorityFee != null) this.editPriorityFee.set(draft.editPriorityFee);
          if (draft.editParams != null) this.editParams.set({ ...p, ...draft.editParams });
        }
      } catch {}
    }

    this.tokenRegistry.ensureLoaded();
    if (this.inputBalanceMint()) this.loadInputBalance();
    if (this.secondaryBalanceMint()) this.loadSecondaryBalance();
    if (['lend','withdraw_lend'].includes(this.action.type)) this.loadLendInfo();
    if (this.action.type === 'native_stake') this.loadValidators();
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
    this.status.set('quoting');
    const mergedParams = { ...this.editParams() };
    if (this.isLaunchAction()) {
      mergedParams['name'] = this.editName();
      mergedParams['symbol'] = this.editSymbol().replace(/[^A-Za-z0-9]/g, '').toUpperCase();
      mergedParams['description'] = this.editDescription();
      // Normalise to the key expected by solana-action.service.ts extract
      mergedParams['initialBuyAmount'] = this.editInitialBuy();
      mergedParams['twitter'] = this.editTwitter();
      mergedParams['telegram'] = this.editTelegram();
      mergedParams['website'] = this.editWebsite();
      mergedParams['mayhemMode'] = String(this.editMayhemMode());
      mergedParams['cashback'] = String(this.editCashback());
      // Tokenized Agent: backend has no ix wired up — always send false.
      mergedParams['tokenizedAgent'] = 'false';
      if (this.effectiveBannerUrl()) mergedParams['bannerUrl'] = this.effectiveBannerUrl()!;
      if (this.effectiveImageUrl()) mergedParams['imageUrl'] = this.effectiveImageUrl()!;
      // Keep legacy 'image' key for backward compat
      if (this.effectiveImageUrl()) mergedParams['image'] = this.effectiveImageUrl()!;

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
        if (!mergedParams['metadataUri'] && this.effectiveImageUrl()) {
          try {
            const metaRes = await this.uploadService.uploadMetadata({
              ...metaPayload,
              image: this.effectiveImageUrl()!,
              ...(this.effectiveBannerUrl() ? { banner: this.effectiveBannerUrl()! } : {}),
              showName: true,
            }).toPromise();
            if (metaRes?.url) mergedParams['metadataUri'] = metaRes.url;
          } catch (metaErr: any) {
            console.warn('[launch_token] metadata upload failed, using imageUrl fallback:', metaErr?.message);
          }
        }
      }
    }
    // Merge user-edited slippage and priorityFee for ALL action types that use them
    // (launch, pumpfun_buy/sell, pumpswap_buy/sell, etc.)
    if (this.editSlippage()) mergedParams['slippage'] = this.editSlippage();
    if (this.editPriorityFee()) mergedParams['priorityFee'] = this.editPriorityFee();
    const mergedAction: ParsedAction = { ...this.action, params: mergedParams };
    try {
      await this.actionService.executeChain([mergedAction], {
        onQuote: () => this.status.set('quoting'),
        onSign: () => this.status.set('signing'),
        onSubmit: (sig: string) => {
          this.txSignature.set(sig);
          this.status.set('submitted');
          this.startSubmittedTick();
          // Persist immediately on submit so refresh shows tx hash even before on-chain confirm.
          this.persistResult({ status: 'submitted', txSignature: sig, errorMessage: null, executedParams: mergedParams });
        },
        onConfirm: (result?: string) => {
          this.stopSubmittedTick();
          this.status.set('confirmed');
          if (this.isDataOnly() && result) {
            this.dataResult.set(result);
          }
          const sig = this.txSignature() ?? result ?? '';
          const stored: StoredActionResult = { status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: mergedParams };
          this.storeResult(mergedAction, sig);
          this.persistResult(stored);
          this.clearDraft();
          this.actionComplete.emit(stored);
        },
      });
    } catch (e: any) {
      const msg: string = e?.message ?? String(e ?? '');
      const isUserRejection = /reject|denied|cancel|declined|user refused/i.test(msg);
      if (isUserRejection) {
        this.status.set('pending');
      } else {
        this.errorMessage.set(sanitizeErrorMessage(msg) || 'Failed to execute action');
        this.status.set('error');
      }
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
    const key = this.actionResultKey();
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
  triggerBannerUpload(): void { this.bannerFileInput?.nativeElement?.click(); }

  // pump.fun rule: Mayhem-mode launches require >= 0.05 SOL initial buy.
  // Auto-bump the user's input when they enable Mayhem so the backend doesn't reject the launch.
  toggleMayhemMode(): void {
    const next = !this.editMayhemMode();
    this.editMayhemMode.set(next);
    if (next) {
      const current = parseFloat(this.editInitialBuy() || '0');
      if (!Number.isFinite(current) || current < 0.05) {
        this.editInitialBuy.set('0.05');
      }
    }
  }

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
        // Server upload failed (e.g. dev env) — use a local blob URL just for preview.
        this.uploadedImageUrl.set(URL.createObjectURL(toUpload));
      }
    } catch (e: any) { this.imageUploadError.set(e?.message || 'Upload failed'); }
    finally { this.uploadingImage.set(false); }
  }

  async onBannerFileSelected(event: Event): Promise<void> { const f = (event.target as HTMLInputElement).files?.[0]; if (f) await this.uploadBannerFile(f); }

  private async uploadBannerFile(file: File): Promise<void> {
    this.uploadingBanner.set(true); this.bannerUploadError.set(null);
    try {
      const result = await this.uploadService.uploadImage(file).toPromise();
      if (result) this.uploadedBannerUrl.set(this.uploadService.getGatewayUrl(result.url));
    } catch (e: any) { this.bannerUploadError.set(e?.message || 'Upload failed'); }
    finally { this.uploadingBanner.set(false); }
  }

  getEditParam(key: string): string { return this.editParams()[key] ?? ''; }
  setEditParam(key: string, value: string): void { this.editParams.update(p => ({ ...p, [key]: value })); }
  toggleEditParam(key: string): void { this.setEditParam(key, this.getEditParam(key) === 'true' ? 'false' : 'true'); }
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
    if (b !== null && b > 0) {
      this.setEditParam(fieldKey, b.toString());
      if (fieldKey === 'amountA') this.dlmmLastEdited.set('A');
      else if (fieldKey === 'amountB') this.dlmmLastEdited.set('B');
    }
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
      setBalance(balance);
      this.writeBalanceCache(wallet, resolved, balance);
      onResolved?.(balance);
    } catch {
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
    const params = this.editParams();
    const cur = (params['amount'] ?? '').trim().toLowerCase();
    if (cur === 'all' || cur === 'max' || cur === 'full' || cur === '100%') {
      this.setEditParam('amount', balance.toString());
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
    try { const info = await this.jupiterLend.getEarnInfo(this.editParams()['token'] ?? 'USDC'); if (info) this.lendInfo.set({ kind: 'earn', data: info } as LendActionInfo); }
    catch { this.lendInfo.set(null); } finally { this.lendInfoLoading.set(false); }
  }

  selectCollateral(opt: any): void { this.selectedCollateral.set(opt); }

  openQuickSwap(): void { this.showQuickSwap.set(true); this.loadQsBalances(); }
  private async loadQsBalances(): Promise<void> { this.qsBalancesLoading.set(true); try { this.qsTokenList.set([]); } finally { this.qsBalancesLoading.set(false); } }
  async executeQuickSwap(): Promise<void> { this.qsStatus.set('swapping'); try { this.qsStatus.set('done'); } catch (e: any) { this.qsError.set(e?.message || 'Swap failed'); this.qsStatus.set('error'); } }

  async showTransactionPreview(): Promise<void> {
    this.showPreview.set(true); this.loadingPreview.set(true);
    try { this.preview.set(await this.previewService.preview({ ...this.action, params: { ...this.editParams() } })); }
    catch { this.preview.set(null); } finally { this.loadingPreview.set(false); }
  }
  formatPreviewChange(change: BalanceChange): string { return this.previewService.formatChange(change); }
  formatPreviewUsd(value: number): string { return this.previewService.formatUsd(value); }
  cancelPreview(): void { this.showPreview.set(false); }
  confirmPreview(): void { this.showPreview.set(false); this.approve(); }

  ngOnDestroy(): void {
    this.stopSubmittedTick();
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
      if (sec >= 60 && this.status() === 'submitted') {
        this.stopSubmittedTick();
        void this.pollSignatureStatus(/*finalCheck*/ true);
      }
    }, 1000);
  }

  /** Direct RPC check for the submitted signature. `finalCheck=true` means
   *  this is the last attempt — if not confirmed, surface the timeout UI. */
  private async pollSignatureStatus(finalCheck: boolean): Promise<void> {
    const sig = this.txSignature();
    if (!sig) {
      if (finalCheck) {
        this.status.set('error');
        this.errorMessage.set('Confirmation timed out. The transaction may still land — open Solscan or click Re-check.');
      }
      return;
    }
    try {
      const web3 = await import('@solana/web3.js');
      const conn = new web3.Connection(environment.solanaRpc, { commitment: 'confirmed', httpHeaders: { 'X-Requested-With': 'XMLHttpRequest' } });
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
        this.persistResult({ status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: this.action.params });
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
      const web3 = await import('@solana/web3.js');
      const conn = new web3.Connection(environment.solanaRpc, { commitment: 'confirmed', httpHeaders: { 'X-Requested-With': 'XMLHttpRequest' } });
      const status = await conn.getSignatureStatus(sig, { searchTransactionHistory: true });
      const value = status.value;
      if (value?.err) {
        this.stopSubmittedTick();
        this.status.set('error');
        this.errorMessage.set('Transaction failed on-chain. See explorer for details.');
      } else if (value?.confirmationStatus === 'confirmed' || value?.confirmationStatus === 'finalized') {
        this.stopSubmittedTick();
        this.status.set('confirmed');
        this.persistResult({ status: 'confirmed', txSignature: sig, errorMessage: null, executedParams: this.action.params });
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
      img.src = `https://img.jup.ag/tokens/${address}.png`;
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
