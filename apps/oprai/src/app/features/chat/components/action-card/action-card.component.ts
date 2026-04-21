/**
 * ActionCardComponent
 *
 * Renders parsed actions (swaps, transfers, stakes, token launches, etc.)
 * with protocol-specific branding, editable fields, and execution flow.
 */
import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ViewChild, ElementRef, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { ParsedAction, IntentParserService } from '../../services/intent-parser.service';
import { SolanaActionService } from '../../services/solana-action.service';
import { ChatApiService, StoredActionResult } from '../../services/chat-api.service';
import { UploadService } from '@core/services/upload.service';
import { JupiterLendService, LEND_SUPPORTED_ASSETS, LendActionInfo } from '@core/services/market/jupiter-lend.service';
import { WalletService } from '@core/services/wallet.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { TransactionPreviewService, TransactionPreview, BalanceChange } from '../../services/transaction-preview.service';
import { JupiterSwapService } from '@core/services/market/jupiter-swap.service';
import { RollbackService } from '@core/services/rollback.service';
import { environment } from '../../../../../environments/environment';

const ACTION_RESULTS_KEY = 'oprai-action-results';

interface ProtocolConfig { name: string; icon: string; accent: string; accentBg: string; }

export interface FieldDef {
  key: string; label: string;
  type: 'text' | 'number' | 'address' | 'select' | 'toggle' | 'textarea' | 'token';
  placeholder?: string; suffix?: string; required?: boolean; half?: boolean;
  min?: number; max?: number; step?: string;
  options?: Array<{ label: string; value: string }>;
  hint?: string; transform?: 'upper' | 'lower';
}

const PROTOCOL_CONFIGS: Record<string, ProtocolConfig> = {
  jupiter:   { name: 'Jupiter',    icon: 'assets/icons/protocols/jupiter.webp',   accent: '#5b5fc7', accentBg: 'rgba(91,95,199,0.12)' },
  jito:      { name: 'Jito',       icon: 'assets/icons/protocols/jito.svg',       accent: '#7df65c', accentBg: 'rgba(125,246,92,0.10)' },
  kamino:    { name: 'Kamino',     icon: 'assets/icons/protocols/kamino.svg',     accent: '#6C5CE7', accentBg: 'rgba(108,92,231,0.12)' },
  orca:      { name: 'Orca',       icon: 'assets/icons/protocols/orca.svg',       accent: '#06B6D4', accentBg: 'rgba(6,182,212,0.12)' },
  raydium:   { name: 'Raydium',    icon: 'assets/icons/protocols/raydium.svg',    accent: '#8B5CF6', accentBg: 'rgba(139,92,246,0.12)' },
  marginfi:  { name: 'MarginFi',   icon: 'assets/icons/protocols/marginfi.svg',   accent: '#F59E0B', accentBg: 'rgba(245,158,11,0.12)' },
  meteora:   { name: 'Meteora',    icon: 'assets/icons/protocols/meteora.svg',     accent: '#10B981', accentBg: 'rgba(16,185,129,0.12)' },
  marinade:  { name: 'Marinade',   icon: 'assets/icons/protocols/marinade.svg',   accent: '#22C55E', accentBg: 'rgba(34,197,94,0.12)' },
  blazestake:{ name: 'BlazeStake', icon: 'assets/icons/protocols/blazestake.svg', accent: '#EF4444', accentBg: 'rgba(239,68,68,0.12)' },
  drift:     { name: 'Drift',      icon: 'assets/icons/protocols/drift.svg',      accent: '#F97316', accentBg: 'rgba(249,115,22,0.12)' },
  solend:    { name: 'Solend',     icon: 'assets/icons/protocols/solend.svg',     accent: '#3B82F6', accentBg: 'rgba(59,130,246,0.12)' },
  pumpfun:   { name: 'pump.fun',   icon: 'assets/icons/protocols/pumpfun.png',    accent: '#AD6DFF', accentBg: 'rgba(173,109,255,0.12)' },
  bonkfun:   { name: 'LetsBONK',   icon: 'assets/icons/protocols/pumpfun.webp',   accent: '#FBBD23', accentBg: 'rgba(251,189,35,0.12)' },
  default:   { name: 'Solana',     icon: 'assets/favicon.svg',                     accent: '#9945FF', accentBg: 'rgba(153,69,255,0.10)' },
};

function getProtocolKey(action: ParsedAction): string {
  const p = (action.params['protocol'] ?? '').toLowerCase();
  if (p && PROTOCOL_CONFIGS[p]) return p;
  const t = action.type;
  if (t === 'swap' || t === 'limit_order' || t === 'dca') return 'jupiter';
  if (t === 'stake') return p || 'jito';
  if (t === 'unstake') return p || 'jito';
  if (t === 'lend' || t === 'withdraw' || t === 'borrow' || t === 'repay') return p || 'jupiter';
  if (t === 'add_liquidity' || t === 'remove_liquidity') return p || 'orca';
  if (t === 'launch_token' || t === 'token_launch' || t === 'pumpfun_launch') return p === 'bonkfun' ? 'bonkfun' : 'pumpfun';
  if (t.includes('pumpfun') || t.includes('pumpswap')) return 'pumpfun';
  if (t.includes('meteora')) return 'meteora';
  if (t.includes('kamino')) return 'kamino';
  if (t.includes('raydium')) return 'raydium';
  if (t.includes('marginfi')) return 'marginfi';
  if (t.includes('drift')) return 'drift';
  if (t.includes('orca')) return 'orca';
  if (t.includes('solend')) return 'solend';
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
    limit_order: 'Limit Order', dca: 'DCA', bridge: 'Bridge',
    squid_bridge: 'Squid Bridge', squid_status: 'Squid Status',
    cross_chain_swap: 'Cross-Chain Swap',
  };
  return labels[action.type] ?? action.type.replace(/_/g, ' ');
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
        hint: 'Token units (not SOL)' },
      { key: 'slippage', label: 'Slippage', type: 'number', placeholder: '10', suffix: '%', half: true, min: 0, max: 100, step: '0.1' },
      { key: 'priorityFee', label: 'Priority Fee', type: 'number', placeholder: '0.0005', suffix: 'SOL', half: true, min: 0, step: '0.0001' },
    );
    return fields;
  }
  if (t === 'swap') {
    fields.push(
      { key: 'inputToken', label: 'From Token', type: 'token', required: true },
      { key: 'amount', label: 'Amount', type: 'number', placeholder: '0', suffix: action.params['inputToken'] ?? 'SOL', required: true },
      { key: 'outputToken', label: 'To Token', type: 'token', required: true },
      { key: 'slippageBps', label: 'Slippage', type: 'number', placeholder: '50', suffix: 'bps', half: true, min: 0, max: 10000 },
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
  } else {
    for (const [key, value] of Object.entries(action.params)) {
      if (typeof value === 'string' && key !== 'protocol') {
        fields.push({ key, label: key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase()), type: 'text', placeholder: value });
      }
    }
  }
  return fields;
}

type ActionStatus = 'pending' | 'quoting' | 'signing' | 'submitted' | 'confirmed' | 'error';

@Component({
  selector: 'app-action-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './action-card.component.html',
  styleUrls: ['./action-card.component.scss'],
})
export class ActionCardComponent implements OnInit, OnChanges {
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

  @ViewChild('imageFileInput') imageFileInput!: ElementRef<HTMLInputElement>;
  @ViewChild('bannerFileInput') bannerFileInput!: ElementRef<HTMLInputElement>;

  // Status
  readonly status = signal<ActionStatus>('pending');
  readonly shake = signal(false);
  readonly txSignature = signal<string | null>(null);
  readonly errorMessage = signal('');

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

  // Image
  readonly uploadingImage = signal(false);
  readonly imageUploadError = signal<string | null>(null);
  readonly bannerUrl = signal<string | null>(null);
  private uploadedImageUrl = signal<string | null>(null);
  readonly effectiveImageUrl = computed(() => this.uploadedImageUrl() || this.action?.params['image'] || this.action?.params['imageUrl'] || null);
  readonly isDragOver = signal(false);

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
  readonly inputBalance = signal<number | null>(null);
  readonly inputBalanceLoading = signal(false);
  readonly inputBalanceMint = computed(() => this.editParams()['inputToken'] ?? this.editParams()['token'] ?? '');
  readonly inputBalanceSymbol = computed(() => this.resolveTokenDisplay(this.inputBalanceMint()).symbol || '');
  readonly insufficientFunds = computed(() => {
    const bal = this.inputBalance();
    const amt = parseFloat(this.editParams()['amount'] ?? '0');
    return bal !== null && amt > 0 && amt > bal;
  });
  readonly belowMinAmount = signal<number | null>(null);

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
  readonly isBonkFunLaunch = computed(() => (this.action?.params['protocol'] ?? '') === 'bonkfun');
  readonly isTokenLaunch = computed(() => this.isLaunchAction());
  readonly isEditable = computed(() => this.status() === 'pending');
  readonly isNameInvalid = computed(() => this.isLaunchAction() && this.editName().trim().length === 0);
  readonly isSymbolInvalid = computed(() => this.isLaunchAction() && this.editSymbol().trim().length === 0);
  readonly isImageInvalid = false;
  readonly canApprove = computed(() => {
    if (this.borrowLiquidityMode()) { const c = this.borrowCapacity(); return c !== null && !c.loading && c.maxBorrow > 0; }
    return true;
  });

  // Quick swap getters (template direct access)
  get qsFromToken(): { mint: string; symbol: string; balance: number; logoURI?: string } | null { return this.qsTokenList().find(t => t.mint === this.qsFromMint()) ?? null; }
  get qsFromBalance(): number { return this.qsFromToken?.balance ?? 0; }
  get qsNeededAmount(): number { return Math.max(0, +(parseFloat(this.editParams()['amount'] ?? '0') - (this.inputBalance() ?? 0)).toFixed(6)); }
  get qsToTokenData(): { symbol: string; name: string; logoURI?: string } { return this.resolveTokenDisplay(this.inputBalanceMint()); }

  get borrowLiveStats(): { collateralUsd: number; maxBorrowable: number; healthFactor: number; hfClass: string; liquidationPriceLabel: string; errorMsg?: string } | null { return null; }

  ngOnInit(): void { if (this.action) this.initFromAction(); }
  ngOnChanges(changes: SimpleChanges): void { if (changes['action'] && this.action) this.initFromAction(); }

  private initFromAction(): void {
    const p = this.action.params;
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
    this.bannerUrl.set(p['bannerUrl'] ?? p['banner_url'] ?? p['banner'] ?? null);
    this.editSlippage.set(p['slippage'] ?? '10');
    this.editPriorityFee.set(p['priorityFee'] ?? '0.0005');
    if (this.inputBalanceMint()) this.loadInputBalance();
    if (['lend','withdraw','borrow','repay'].includes(this.action.type)) this.loadLendInfo();
  }

  async approve(): Promise<void> {
    this.status.set('quoting');
    const mergedParams = { ...this.editParams() };
    if (this.isLaunchAction()) {
      mergedParams['name'] = this.editName();
      mergedParams['symbol'] = this.editSymbol();
      mergedParams['description'] = this.editDescription();
      // Normalise to the key expected by solana-action.service.ts extract
      mergedParams['initialBuyAmount'] = this.editInitialBuy();
      mergedParams['twitter'] = this.editTwitter();
      mergedParams['telegram'] = this.editTelegram();
      mergedParams['website'] = this.editWebsite();
      mergedParams['mayhemMode'] = String(this.editMayhemMode());
      mergedParams['cashback'] = String(this.editCashback());
      if (this.effectiveBannerUrl()) mergedParams['bannerUrl'] = this.effectiveBannerUrl()!;
      if (this.effectiveImageUrl()) mergedParams['imageUrl'] = this.effectiveImageUrl()!;
      // Keep legacy 'image' key for backward compat
      if (this.effectiveImageUrl()) mergedParams['image'] = this.effectiveImageUrl()!;
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
        onSubmit: (sig: string) => { this.txSignature.set(sig); this.status.set('submitted'); },
        onConfirm: (result?: string) => {
          this.status.set('confirmed');
          const sig = this.txSignature() ?? result ?? '';
          this.storeResult(mergedAction, sig);
          this.actionComplete.emit({ status: 'confirmed', txSignature: sig, errorMessage: null });
        },
      });
    } catch (e: any) {
      this.errorMessage.set(e?.message || 'Failed to execute action');
      this.status.set('error');
    }
  }

  reject(): void { this.actionDismissed.emit(); }

  private storeResult(action: ParsedAction, sig: string): void {
    try { const s = JSON.parse(localStorage.getItem(ACTION_RESULTS_KEY) ?? '[]'); s.push({ action, signature: sig, timestamp: Date.now() }); localStorage.setItem(ACTION_RESULTS_KEY, JSON.stringify(s)); } catch {}
  }

  triggerImageUpload(): void { this.imageFileInput?.nativeElement?.click(); }
  triggerBannerUpload(): void { this.bannerFileInput?.nativeElement?.click(); }

  async onImageFileSelected(event: Event): Promise<void> { const f = (event.target as HTMLInputElement).files?.[0]; if (f) await this.uploadImage(f); }
  onDragOver(event: DragEvent): void { event.preventDefault(); this.isDragOver.set(true); }
  onDragLeave(): void { this.isDragOver.set(false); }
  async onDrop(event: DragEvent): Promise<void> { event.preventDefault(); this.isDragOver.set(false); const f = event.dataTransfer?.files?.[0]; if (f) await this.uploadImage(f); }

  private async uploadImage(file: File): Promise<void> {
    this.uploadingImage.set(true); this.imageUploadError.set(null);
    try {
      const result = await this.uploadService.uploadImage(file).toPromise();
      if (result) this.uploadedImageUrl.set(this.uploadService.getGatewayUrl(result.url));
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
  setMaxAmount(): void { const b = this.inputBalance(); if (b !== null && b > 0) this.setEditParam('amount', b.toString()); }

  async copyValue(value: string): Promise<void> { try { await navigator.clipboard.writeText(value); this.copiedField.set(value); setTimeout(() => this.copiedField.set(null), 2000); } catch {} }

  private async loadInputBalance(): Promise<void> {
    this.inputBalanceLoading.set(true);
    try { await this.tokenRegistry.ensureLoaded(); this.inputBalance.set(null); }
    finally { this.inputBalanceLoading.set(false); }
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

  requestUndo(): void {
    if (!this.snapshotId) return;
    this.undoInProgress.set(true);
    this.rollbackService.undo(this.snapshotId, {
      onQuote: () => {}, onSign: () => {}, onSubmit: () => {},
      onConfirm: () => { this.undoInProgress.set(false); },
    }).catch(() => this.undoInProgress.set(false));
  }

  resolveTokenDisplay(mintOrSymbol: string): { symbol: string; name: string; logoURI?: string; decimals?: number } {
    if (!mintOrSymbol) return { symbol: '??', name: 'Unknown' };
    const known = this.tokenRegistry.getToken(mintOrSymbol);
    if (known) return { symbol: known.symbol, name: known.name, logoURI: known.logoURI ?? undefined, decimals: known.decimals };
    const commons: Record<string, { symbol: string; name: string; decimals: number }> = { SOL: { symbol: 'SOL', name: 'Solana', decimals: 9 }, USDC: { symbol: 'USDC', name: 'USD Coin', decimals: 6 }, USDT: { symbol: 'USDT', name: 'Tether', decimals: 6 } };
    if (commons[mintOrSymbol.toUpperCase()]) return commons[mintOrSymbol.toUpperCase()];
    return { symbol: mintOrSymbol.slice(0, 4) + '...', name: mintOrSymbol, decimals: 9 };
  }
}
