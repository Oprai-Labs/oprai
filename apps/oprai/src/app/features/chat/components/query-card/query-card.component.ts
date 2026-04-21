import { Component, Input, Output, EventEmitter, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { ParsedQuery, ParsedAction, IntentParserService } from '../../services/intent-parser.service';
import { ApiService } from '@core/services/api.service';
import { ChatApiService, QuerySnapshot } from '../../services/chat-api.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { WalletService } from '@core/services/wallet.service';
import { PortfolioService } from '@features/portfolio/services/portfolio.service';
import { SolanaRpcService } from '@features/portfolio/services/solana-rpc.service';
import { JupiterLendService, LendPosition, BorrowPosition } from '@core/services/market/jupiter-lend.service';
import { JupiterPerpService, PerpPosition } from '@core/services/market/jupiter-perp.service';
import { environment } from '../../../../../environments/environment';
import { firstValueFrom } from 'rxjs';

/** Mock query result types */
interface BalanceResult {
  token: string;
  symbol: string;
  balance: number;
  value: number;
  change24h: number;
}

interface PriceResult {
  symbol: string;
  price: number;
  change24h: number;
  volume24h: number;
  marketCap: number;
}

interface PositionResult {
  protocol: string;
  type: string;
  token: string;
  amount: number;
  value: number;
  apy: number;
}

interface TransactionResult {
  type: string;
  description: string;
  amount: string;
  time: string;
  status: string;
}

interface TrendingResult {
  rank: number;
  symbol: string;
  name: string;
  price: number;
  change24h: number;
}

interface NetworkResult {
  tps: number;
  slot: number;
  epoch: number;
  epochProgress: number;
  validators: number;
}

interface YieldResult {
  protocol: string;
  token: string;
  apy: number;
  tvl: number;
  risk: string;
}

interface AnalyticsResult {
  totalPnl: number;
  pnlPercent: number;
  winRate: number;
  totalTrades: number;
  bestTrade: string;
  worstTrade: string;
  avgHoldTime: string;
  topTokens: { symbol: string; pnl: number; trades: number }[];
}

interface NftItem {
  name: string;
  collection: string;
  image: string;
  floorPrice: number;
  rarity: string;
  lastSale: number;
}

interface AirdropResult {
  protocol: string;
  status: string;
  amount: string;
  token: string;
  deadline: string;
  eligible: boolean;
}

interface GasResult {
  baseFee: number;
  priorityLow: number;
  priorityMedium: number;
  priorityHigh: number;
  swapCost: string;
  transferCost: string;
}

interface WalletInfoResult {
  address: string;
  solBalance: number;
  tokenCount: number;
  nftCount: number;
  firstTx: string;
  totalTxs: number;
  label: string;
}

interface TaxResult {
  year: number;
  totalGains: number;
  totalLosses: number;
  netGains: number;
  shortTermGains: number;
  longTermGains: number;
  totalTxs: number;
  categories: { type: string; amount: number; count: number }[];
}

interface DcaOrder {
  id: string;
  inputToken: string;
  outputToken: string;
  amount: number;
  frequency: string;
  remaining: number;
  total: number;
  status: 'active' | 'paused' | 'completed';
  nextExecution: string;
}

interface LimitOrder {
  id: string;
  inputToken: string;
  outputToken: string;
  amount: number;
  targetPrice: number;
  currentPrice: number;
  status: 'open' | 'filled' | 'cancelled';
  createdAt: string;
}

interface AlertItem {
  id: string;
  type: string;
  token: string;
  condition: string;
  targetValue: string;
  currentValue: string;
  status: 'active' | 'triggered' | 'expired';
  createdAt: string;
}

// Static lookup for the most common Solana tokens (avoids async registry for display)
const KNOWN_TOKENS: Record<string, { symbol: string; decimals: number }> = {
  'So11111111111111111111111111111111111111112': { symbol: 'SOL', decimals: 9 },
  'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': { symbol: 'USDC', decimals: 6 },
  'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': { symbol: 'USDT', decimals: 6 },
  'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN': { symbol: 'JUP', decimals: 6 },
  'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263': { symbol: 'BONK', decimals: 5 },
  'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v': { symbol: 'JupSOL', decimals: 9 },
  'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kongC': { symbol: 'jitoSOL', decimals: 9 },
  'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So': { symbol: 'mSOL', decimals: 9 },
  'bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1': { symbol: 'bSOL', decimals: 9 },
  '7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs': { symbol: 'ETH', decimals: 8 },
  'WENWENvqqNya429ubCdR81ZmD69brwQaaBYY6p3LCpk': { symbol: 'WEN', decimals: 5 },
  'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm': { symbol: 'WIF', decimals: 6 },
};

@Component({
  selector: 'app-query-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './query-card.component.html',
  styleUrl: './query-card.component.scss',
})
export class QueryCardComponent implements OnInit {
  @Input({ required: true }) query!: ParsedQuery;
  @Input() sessionId: string | null = null;
  @Input() messageId: string | null = null;
  /** DB-persisted snapshot from a previous fetch; if present, skip re-fetching. */
  @Input() snapshot: QuerySnapshot | null = null;
  @Output() cancelAction = new EventEmitter<ParsedAction>();

  private readonly intentParser = inject(IntentParserService);
  private readonly api = inject(ApiService);
  private readonly chatApi = inject(ChatApiService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly priceFeed = inject(PriceFeedService);
  private readonly walletService = inject(WalletService);
  private readonly portfolioService = inject(PortfolioService);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly jupiterLend = inject(JupiterLendService);
  private readonly jupiterPerp = inject(JupiterPerpService);

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  // Mock result data
  balanceResults: BalanceResult[] = [];
  priceResult: PriceResult | null = null;
  positionResults: PositionResult[] = [];
  transactionResults: TransactionResult[] = [];
  trendingResults: TrendingResult[] = [];
  networkResult: NetworkResult | null = null;
  yieldResults: YieldResult[] = [];
  analyticsResult: AnalyticsResult | null = null;
  nftResults: NftItem[] = [];
  airdropResults: AirdropResult[] = [];
  gasResult: GasResult | null = null;
  walletInfoResult: WalletInfoResult | null = null;
  taxResult: TaxResult | null = null;
  dcaResults: DcaOrder[] = [];
  limitOrderResults: LimitOrder[] = [];
  limitOrderStatusFilter: 'open' | 'history' = 'open';
  alertResults: AlertItem[] = [];
  lendEarnPositions: LendPosition[] = [];
  lendBorrowPositions: BorrowPosition[] = [];
  perpPositions: PerpPosition[] = [];

  get queryIcon(): string {
    return this.intentParser.getQueryIcon(this.query.type);
  }

  get queryLabel(): string {
    return this.intentParser.getQueryLabel(this.query);
  }

  /** Returns a protocol image path for query types tied to a specific protocol, null otherwise. */
  get protocolIcon(): string | null {
    switch (this.query.type) {
      case 'limit_orders':
      case 'dca':
      case 'lend_positions':
      case 'perp_positions':
        return 'assets/icons/protocols/jupiter.webp';
      default:
        return null;
    }
  }

  ngOnInit(): void {
    if (this.snapshot) {
      this.restoreSnapshot(this.snapshot);
    } else {
      this.simulateQuery();
    }
  }

  private restoreSnapshot(snap: QuerySnapshot): void {
    const d = snap.data as Record<string, unknown>;
    if (d['limitOrderResults']) this.limitOrderResults = d['limitOrderResults'] as LimitOrder[];
    if (d['dcaResults'])        this.dcaResults        = d['dcaResults']        as DcaOrder[];
    if (d['balanceResults'])    this.balanceResults    = d['balanceResults']    as BalanceResult[];
    if (d['priceResult'])       this.priceResult       = d['priceResult']       as PriceResult;
    if (d['positionResults'])   this.positionResults   = d['positionResults']   as PositionResult[];
    if (d['transactionResults'])this.transactionResults= d['transactionResults']as TransactionResult[];
    if (d['trendingResults'])   this.trendingResults   = d['trendingResults']   as TrendingResult[];
    if (d['networkResult'])     this.networkResult     = d['networkResult']     as NetworkResult;
    if (d['yieldResults'])      this.yieldResults      = d['yieldResults']      as YieldResult[];
    if (d['analyticsResult'])   this.analyticsResult   = d['analyticsResult']   as AnalyticsResult;
    if (d['nftResults'])        this.nftResults        = d['nftResults']        as NftItem[];
    if (d['airdropResults'])    this.airdropResults    = d['airdropResults']    as AirdropResult[];
    if (d['gasResult'])         this.gasResult         = d['gasResult']         as GasResult;
    if (d['walletInfoResult'])  this.walletInfoResult  = d['walletInfoResult']  as WalletInfoResult;
    if (d['taxResult'])         this.taxResult         = d['taxResult']         as TaxResult;
    if (d['alertResults'])      this.alertResults      = d['alertResults']      as AlertItem[];
    this.loading.set(false);
  }

  private currentSnapshotData(): Record<string, unknown> {
    const d: Record<string, unknown> = {};
    if (this.limitOrderResults.length)  d['limitOrderResults']  = this.limitOrderResults;
    if (this.dcaResults.length)         d['dcaResults']         = this.dcaResults;
    if (this.balanceResults.length)     d['balanceResults']     = this.balanceResults;
    if (this.priceResult)               d['priceResult']        = this.priceResult;
    if (this.positionResults.length)    d['positionResults']    = this.positionResults;
    if (this.transactionResults.length) d['transactionResults'] = this.transactionResults;
    if (this.trendingResults.length)    d['trendingResults']    = this.trendingResults;
    if (this.networkResult)             d['networkResult']      = this.networkResult;
    if (this.yieldResults.length)       d['yieldResults']       = this.yieldResults;
    if (this.analyticsResult)           d['analyticsResult']    = this.analyticsResult;
    if (this.nftResults.length)         d['nftResults']         = this.nftResults;
    if (this.airdropResults.length)     d['airdropResults']     = this.airdropResults;
    if (this.gasResult)                 d['gasResult']          = this.gasResult;
    if (this.walletInfoResult)          d['walletInfoResult']   = this.walletInfoResult;
    if (this.taxResult)                 d['taxResult']          = this.taxResult;
    if (this.alertResults.length)       d['alertResults']       = this.alertResults;
    return d;
  }

  private persistSnapshot(): void {
    if (!this.sessionId || !this.messageId) return;
    const snap: QuerySnapshot = {
      type: this.query.type,
      data: this.currentSnapshotData(),
      fetchedAt: new Date().toISOString(),
    };
    this.chatApi.updateMessageMeta(this.sessionId, this.messageId, {
      query_snapshots: { [this.query.raw]: snap },
    });
  }

  private resolveTokenSymbol(mint: string): string {
    if (!mint) return '?';
    const known = KNOWN_TOKENS[mint];
    if (known) return known.symbol;
    const fromRegistry = this.tokenRegistry.getToken(mint);
    if (fromRegistry) return fromRegistry.symbol;
    return mint.slice(0, 4) + '…';
  }

  private resolveTokenDecimals(mint: string): number {
    if (!mint) return 9;
    const known = KNOWN_TOKENS[mint];
    if (known) return known.decimals;
    const fromRegistry = this.tokenRegistry.getToken(mint);
    if (fromRegistry) return fromRegistry.decimals;
    return 9;
  }

  private formatRelativeTime(raw: string | number): string {
    if (!raw) return '—';
    let ms: number;
    if (typeof raw === 'number') {
      ms = raw > 1e12 ? raw : raw * 1000; // seconds vs ms
    } else {
      // Could be ISO string or unix timestamp string
      const parsed = Number(raw);
      ms = !isNaN(parsed) ? (parsed > 1e12 ? parsed : parsed * 1000) : new Date(raw).getTime();
    }
    if (isNaN(ms)) return '—';
    const diff = Math.floor((Date.now() - ms) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  private formatNextCycle(timestampSecs: number): string {
    if (!timestampSecs) return '—';
    const now = Math.floor(Date.now() / 1000);
    const diff = timestampSecs - now;
    if (diff <= 0) return 'Soon';
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return `${Math.floor(diff / 86400)}d`;
  }

  private formatCycleFrequency(secs: number): string {
    if (secs <= 60) return 'Per min';
    if (secs <= 3600) return 'Hourly';
    if (secs <= 86400) return 'Daily';
    if (secs <= 604800) return 'Weekly';
    if (secs <= 2592000) return 'Monthly';
    return `Every ${Math.floor(secs / 86400)}d`;
  }

  private fetchLimitOrders(status: 'open' | 'history' = 'open'): void {
    const apiStatus = status === 'history' ? 'completed' : 'open';
    firstValueFrom(this.api.get<any>('/actions/limit-orders', { status: apiStatus }))
      .then(async resp => {
        const rawOrders: any[] = resp?.orders ?? [];
        const orders: LimitOrder[] = rawOrders.map(order => {
          // Jupiter Trigger v1 API: flat structure, amounts in both raw and human-readable
          const inputMint: string = order.inputMint ?? '';
          const outputMint: string = order.outputMint ?? '';
          const inputSymbol = this.resolveTokenSymbol(inputMint);
          const outputSymbol = this.resolveTokenSymbol(outputMint);
          const inputDecimals = this.resolveTokenDecimals(inputMint);
          const outputDecimals = this.resolveTokenDecimals(outputMint);

          // Prefer raw amounts (most accurate), fall back to human-readable
          const makingRaw = parseFloat(order.rawMakingAmount ?? order.rawRemainingMakingAmount ?? '0');
          const takingRaw = parseFloat(order.rawTakingAmount ?? order.rawRemainingTakingAmount ?? '0');
          const makingAmount = makingRaw > 0
            ? makingRaw / Math.pow(10, inputDecimals)
            : parseFloat(order.makingAmount ?? '0');
          const takingAmount = takingRaw > 0
            ? takingRaw / Math.pow(10, outputDecimals)
            : parseFloat(order.takingAmount ?? '0');
          const targetPrice = makingAmount > 0 ? takingAmount / makingAmount : 0;

          // Status: Jupiter returns "Open", "Completed", "Cancelled" (capitalized)
          const statusRaw = (order.status ?? '').toLowerCase();
          const orderStatus: 'open' | 'filled' | 'cancelled' =
            statusRaw === 'open' ? 'open' : statusRaw === 'cancelled' ? 'cancelled' : 'filled';

          return {
            id: order.orderKey ?? order.publicKey ?? '',
            inputToken: inputSymbol,
            outputToken: outputSymbol,
            inputMint,
            amount: makingAmount,
            targetPrice,
            currentPrice: 0,
            status: orderStatus,
            createdAt: this.formatRelativeTime(order.createdAt),
          } as LimitOrder & { inputMint: string };
        });

        this.limitOrderResults = orders;
        this.loading.set(false);
        this.persistSnapshot();

        // Fetch current prices for open orders to show distance from target
        if (status === 'open' && orders.length > 0) {
          const uniqueMints = [...new Set(orders.map((o: any) => o.inputMint).filter(Boolean))];
          try {
            const priceMap = await this.priceFeed.getPrices(uniqueMints);
            this.limitOrderResults = this.limitOrderResults.map((o, i) => {
              const mint = (orders[i] as any).inputMint;
              const priceData = mint ? priceMap.get(mint) : null;
              return { ...o, currentPrice: priceData?.price ?? 0 };
            });
          } catch {
            // Non-fatal: currentPrice stays 0
          }
        }
      })
      .catch(() => {
        this.error.set('Failed to load limit orders');
        this.loading.set(false);
      });
  }

  onLimitOrderFilterChange(filter: 'open' | 'history'): void {
    this.limitOrderStatusFilter = filter;
    this.loading.set(true);
    this.error.set(null);
    this.fetchLimitOrders(filter);
  }

  private fetchDcaOrders(): void {
    firstValueFrom(this.api.get<any>('/actions/dca-orders', { status: 'open' }))
      .then(resp => {
        // Jupiter Recurring API v1: time-based DCA orders are under resp.time (not resp.orders)
        const rawOrders: any[] = resp?.time ?? resp?.orders ?? [];
        this.dcaResults = rawOrders.map(order => {
          const inputMint: string = order.inputMint ?? '';
          const outputMint: string = order.outputMint ?? '';
          const inputSymbol = this.resolveTokenSymbol(inputMint);
          const outputSymbol = this.resolveTokenSymbol(outputMint);
          const inputDecimals = this.resolveTokenDecimals(inputMint);

          const perCycleRaw = parseFloat(order.inAmountPerCycle ?? '0');
          const perCycleAmount = perCycleRaw / Math.pow(10, inputDecimals);

          const cycleFrequency: number = order.cycleFrequency ?? 86400;
          const frequency = this.formatCycleFrequency(cycleFrequency);

          const totalCycles: number = order.numberOfOrders ?? 0;
          const cyclesExecuted: number = order.cyclesExecuted ?? 0;
          const remaining = Math.max(0, totalCycles - cyclesExecuted);

          const nextCycleAt: number = order.nextCycleAt ?? 0;
          const nextExecution = this.formatNextCycle(nextCycleAt);

          const statusRaw = (order.orderStatus ?? order.status ?? '').toLowerCase();
          const status: 'active' | 'paused' | 'completed' =
            statusRaw === 'completed' || statusRaw === 'closed' ? 'completed' :
            statusRaw === 'paused' ? 'paused' : 'active';

          return {
            id: order.orderKey ?? order.publicKey ?? '',
            inputToken: inputSymbol,
            outputToken: outputSymbol,
            amount: perCycleAmount,
            frequency,
            remaining,
            total: totalCycles,
            status,
            nextExecution,
          } as DcaOrder;
        });
        this.loading.set(false);
        this.persistSnapshot();
      })
      .catch(() => {
        this.error.set('Failed to load DCA orders');
        this.loading.set(false);
      });
  }

  private async fetchLendPositions(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see lending positions');
      this.loading.set(false);
      return;
    }
    try {
      const [earn, borrow] = await Promise.all([
        this.jupiterLend.getAllEarnPositions(wallet),
        this.jupiterLend.getBorrowPositions(wallet),
      ]);
      this.lendEarnPositions = earn;
      this.lendBorrowPositions = borrow;
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load lending positions');
      this.loading.set(false);
    }
  }

  private async fetchPerpPositions(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see perp positions');
      this.loading.set(false);
      return;
    }
    try {
      this.perpPositions = await this.jupiterPerp.getPositions(wallet);
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load perp positions');
      this.loading.set(false);
    }
  }

  onClosePerpPosition(market: string, side: string): void {
    this.cancelAction.emit({
      type: 'perp_close',
      params: { market, side },
      raw: `[ACTION:perp_close] market=${market} side=${side}`,
    });
  }

  onCancelLimitOrder(orderId: string): void {
    this.cancelAction.emit({
      type: 'cancel_limit_order',
      params: { order: orderId },
      raw: `[ACTION:cancel_limit_order] order=${orderId}`,
    });
  }

  onCancelDcaOrder(orderId: string): void {
    this.cancelAction.emit({
      type: 'cancel_dca',
      params: { order: orderId },
      raw: `[ACTION:cancel_dca] order=${orderId}`,
    });
  }

  private simulateQuery(): void {
    void this._fetchQuery();
  }

  private async _fetchQuery(): Promise<void> {
    switch (this.query.type) {
      case 'limit_orders': {
        const statusParam = (this.query.params?.['status'] ?? 'open') as 'open' | 'history';
        this.limitOrderStatusFilter = statusParam;
        this.fetchLimitOrders(statusParam);
        return;
      }
      case 'dca':
        this.fetchDcaOrders();
        return;
      case 'lend_positions':
        await this.fetchLendPositions();
        return;
      case 'perp_positions':
        await this.fetchPerpPositions();
        return;
      case 'balance':
      case 'portfolio':
      case 'risk':
        await this.fetchBalance();
        return;
      case 'price':
      case 'token_info':
        await this.fetchPrice();
        return;
      case 'positions':
        await this.fetchPositions();
        return;
      case 'transactions':
        await this.fetchTransactions();
        return;
      case 'trending':
        await this.fetchTrending();
        return;
      case 'network':
        await this.fetchNetwork();
        return;
      case 'gas':
        await this.fetchGas();
        return;
      case 'wallet_info':
        await this.fetchWalletInfo();
        return;
      case 'solend_user_info':
      case 'solend_reserves':
      case 'solend_market':
      case 'me_collection_info':
      case 'me_nft_info':
      case 'me_wallet_nfts':
      case 'me_collection_activity':
      case 'me_listings':
      case 'me_offers':
      case 'me_collection_nfts':
      case 'cross_chain_quote':
      case 'cross_chain_chains':
      case 'cross_chain_tokens':
        // These types are handled as ACTION blocks by the backend.
        this.error.set('Use the action card to execute this request.');
        this.loading.set(false);
        return;
      default:
        // Mock data for queries without a real backend yet (yield, analytics, nft_collection, airdrops, tax_report, alerts)
        setTimeout(() => {
          switch (this.query.type) {
            case 'yield':
              this.yieldResults = this.getMockYields();
              break;
            case 'analytics':
              this.analyticsResult = this.getMockAnalytics();
              break;
            case 'nft_collection':
              this.nftResults = this.getMockNfts();
              break;
            case 'airdrops':
              this.airdropResults = this.getMockAirdrops();
              break;
            case 'tax_report':
              this.loading.set(true);
              this.fetchTaxReport().catch(() => {
                this.taxResult = this.getMockTax();
                this.loading.set(false);
                this.persistSnapshot();
              });
              return;
            case 'alerts':
              this.alertResults = this.getMockAlerts();
              break;
            default:
              this.error.set('Unknown query type');
          }
          this.loading.set(false);
          this.persistSnapshot();
        }, 600);
    }
  }

  /** Resolve token symbol or address to a mint address. */
  private resolveToMint(tokenParam: string): string {
    const SOL_MINT = 'So11111111111111111111111111111111111111112';
    if (!tokenParam) return SOL_MINT;
    // Already looks like an address (>20 chars base58)
    if (tokenParam.length > 20 && !tokenParam.includes(' ')) return tokenParam;
    // Reverse lookup from KNOWN_TOKENS
    for (const [mint, info] of Object.entries(KNOWN_TOKENS)) {
      if (info.symbol.toUpperCase() === tokenParam.toUpperCase()) return mint;
    }
    return SOL_MINT;
  }

  /** Ensure portfolio is loaded for the connected wallet. Returns summary or null. */
  private async ensurePortfolio() {
    const wallet = this.walletService.publicKey();
    if (!wallet) return null;
    if (!this.portfolioService.isLoaded() || this.portfolioService.summary()?.walletAddress !== wallet) {
      await this.portfolioService.loadPortfolio(wallet);
    }
    return this.portfolioService.summary();
  }

  private async fetchBalance(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see balances');
      this.loading.set(false);
      return;
    }
    try {
      const summary = await this.ensurePortfolio();
      if (!summary) {
        this.error.set('Failed to load portfolio data');
        this.loading.set(false);
        return;
      }
      const token = this.query.params['token']?.toUpperCase();
      const solEntry: BalanceResult = {
        token: 'Solana',
        symbol: 'SOL',
        balance: summary.solBalance.sol,
        value: summary.solBalance.usdValue ?? 0,
        change24h: summary.solBalance.priceChange24h ?? 0,
      };
      const tokenEntries: BalanceResult[] = summary.tokens
        .filter(t => (t.usdValue ?? 0) > 0.01)
        .map(t => ({
          token: t.name,
          symbol: t.symbol,
          balance: t.balance,
          value: t.usdValue ?? 0,
          change24h: t.priceChange24h ?? 0,
        }));
      const all = [solEntry, ...tokenEntries];
      this.balanceResults = (token && token !== 'ALL')
        ? all.filter(b => b.symbol.toUpperCase() === token)
        : all;
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load balances');
      this.loading.set(false);
    }
  }

  private async fetchPrice(): Promise<void> {
    const tokenParam = this.query.params['token'] ?? this.query.params['mint'] ?? 'SOL';
    const mint = this.resolveToMint(tokenParam);
    try {
      const [prices, tokenInfo] = await Promise.all([
        this.priceFeed.getPrices([mint]),
        firstValueFrom(this.api.get<any>(`/market/tokens/${mint}`)).catch(() => null),
      ]);
      const price = prices.get(mint)?.price ?? 0;
      const symbol: string = tokenInfo?.symbol ?? KNOWN_TOKENS[mint]?.symbol ?? tokenParam.toUpperCase();
      this.priceResult = {
        symbol,
        price,
        change24h: tokenInfo?.priceChange24h ?? 0,
        volume24h: tokenInfo?.daily_volume ?? 0,
        marketCap: tokenInfo?.market_cap ?? 0,
      };
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load price data');
      this.loading.set(false);
    }
  }

  private async fetchPositions(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see positions');
      this.loading.set(false);
      return;
    }
    try {
      const summary = await this.ensurePortfolio();
      if (!summary) {
        this.positionResults = this.getMockPositions();
        this.loading.set(false);
        this.persistSnapshot();
        return;
      }
      const positions = this.portfolioService.protocolPositions();
      this.positionResults = positions.flatMap(p =>
        p.positions.map(pos => ({
          protocol: p.protocolName,
          type: p.category.replace(/-/g, ' '),
          token: pos.tokens.map(t => t.symbol).join('+'),
          amount: pos.tokens[0]?.amount ?? 0,
          value: pos.totalUsdValue ?? 0,
          apy: 0,
        }))
      );
      if (this.positionResults.length === 0) {
        this.positionResults = this.getMockPositions();
      }
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.positionResults = this.getMockPositions();
      this.loading.set(false);
      this.persistSnapshot();
    }
  }

  private async fetchTransactions(): Promise<void> {
    const wallet = this.query.params['wallet'] ?? this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see transactions');
      this.loading.set(false);
      return;
    }
    try {
      const resp = await firstValueFrom(
        this.api.get<any>(`/market/account/${wallet}/transactions`, { limit: '10', hideSpam: 'true' })
      );
      const txs: any[] = resp?.transactions ?? [];
      this.transactionResults = txs.map(tx => ({
        type: tx.type || 'unknown',
        description: tx.description || `${tx.type || 'Transaction'}`,
        amount: tx.valueSol > 0 ? `${(tx.valueSol as number).toFixed(4)} SOL` : '—',
        time: this.formatRelativeTime(tx.blockTime),
        status: tx.success ? 'confirmed' : 'failed',
      }));
      if (this.transactionResults.length === 0) {
        this.transactionResults = this.getMockTransactions();
      }
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load transactions');
      this.loading.set(false);
    }
  }

  private async fetchTrending(): Promise<void> {
    try {
      // Fetch from chat-service trending API
      const resp = await firstValueFrom(
        this.api.get<any>('/tokens/trending?category=all')
      );

      if (resp?.hot && resp.hot.length > 0) {
        this.trendingResults = resp.hot.map((t: any, i: number) => ({
          rank: i + 1,
          symbol: t.symbol,
          name: t.name,
          price: t.price,
          change24h: t.price_change_h24 || 0,
        }));
      } else {
        // Fall back to mock data
        this.trendingResults = this.getMockTrending();
      }

      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.trendingResults = this.getMockTrending();
      this.loading.set(false);
      this.persistSnapshot();
    }
  }

  private async fetchNetwork(): Promise<void> {
    try {
      const rpcUrl = environment.solanaRpc;
      const [epochResp, perfResp] = await Promise.all([
        fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getEpochInfo' }),
        }),
        fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'getRecentPerformanceSamples', params: [1] }),
        }),
      ]);
      const [epochData, perfData] = await Promise.all([epochResp.json(), perfResp.json()]);
      const epoch = epochData.result;
      const perf = perfData.result?.[0];
      const tps = perf ? Math.round(perf.numTransactions / perf.samplePeriodSecs) : 0;
      this.networkResult = {
        tps,
        slot: epoch.absoluteSlot,
        epoch: epoch.epoch,
        epochProgress: parseFloat((epoch.slotIndex / epoch.slotsInEpoch * 100).toFixed(1)),
        validators: 0,
      };
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.networkResult = this.getMockNetwork();
      this.loading.set(false);
      this.persistSnapshot();
    }
  }

  private async fetchGas(): Promise<void> {
    try {
      const medianMicroLamports = await this.solanaRpc.getRecentPriorityFeeMicroLamports();
      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      const solPrice = await this.priceFeed.getPrice(SOL_MINT);
      const priceUsd = solPrice ?? 0;
      const microToSol = (µL: number) => µL / 1_000_000 / 1_000_000_000;
      const medSol = microToSol(medianMicroLamports);
      const baseFee = 0.000005;
      const formatCost = (sol: number) => {
        const usd = sol * priceUsd;
        return usd < 0.01 ? '~$0.01' : `~$${usd.toFixed(2)}`;
      };
      this.gasResult = {
        baseFee,
        priorityLow: medSol * 0.25,
        priorityMedium: medSol,
        priorityHigh: medSol * 4,
        swapCost: formatCost(medSol * 1.2 + baseFee),
        transferCost: formatCost(medSol * 0.2 + baseFee),
      };
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.gasResult = this.getMockGas();
      this.loading.set(false);
      this.persistSnapshot();
    }
  }

  private async fetchWalletInfo(): Promise<void> {
    const wallet = this.query.params['wallet'] ?? this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see wallet info');
      this.loading.set(false);
      return;
    }
    try {
      const summary = await this.ensurePortfolio();
      if (!summary) {
        this.walletInfoResult = this.getMockWalletInfo();
        this.loading.set(false);
        this.persistSnapshot();
        return;
      }
      this.walletInfoResult = {
        address: wallet,
        solBalance: summary.solBalance.sol,
        tokenCount: summary.tokens.length,
        nftCount: 0,
        firstTx: '—',
        totalTxs: 0,
        label: `${wallet.slice(0, 4)}...${wallet.slice(-4)}`,
      };
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load wallet info');
      this.loading.set(false);
    }
  }


  private getMockPositions(): PositionResult[] {
    return [
      { protocol: 'Marinade', type: 'Staking', token: 'mSOL', amount: 12.5, value: 2225.63, apy: 7.2 },
      { protocol: 'Raydium', type: 'LP', token: 'SOL-USDC', amount: 1, value: 890.45, apy: 24.5 },
      { protocol: 'Marginfi', type: 'Lending', token: 'USDC', amount: 500, value: 500.00, apy: 8.3 },
      { protocol: 'Jito', type: 'Staking', token: 'jitoSOL', amount: 5.0, value: 895.25, apy: 7.8 },
    ];
  }

  private getMockTransactions(): TransactionResult[] {
    return [
      { type: 'swap', description: 'Swap 2 SOL → 354 USDC', amount: '$354.10', time: '2 min ago', status: 'confirmed' },
      { type: 'transfer', description: 'Sent 1.5 SOL', amount: '-$267.08', time: '15 min ago', status: 'confirmed' },
      { type: 'stake', description: 'Stake 10 SOL on Marinade', amount: '$1,780.50', time: '1 hr ago', status: 'confirmed' },
      { type: 'swap', description: 'Swap 100 USDC → 0.56 SOL', amount: '$100.00', time: '3 hrs ago', status: 'confirmed' },
      { type: 'receive', description: 'Received 5 SOL', amount: '+$890.25', time: '1 day ago', status: 'confirmed' },
    ];
  }

  private getMockTrending(): TrendingResult[] {
    return [
      { rank: 1, symbol: 'WIF', name: 'dogwifhat', price: 2.45, change24h: 28.34 },
      { rank: 2, symbol: 'BONK', name: 'Bonk', price: 0.000025, change24h: 15.67 },
      { rank: 3, symbol: 'JTO', name: 'Jito', price: 3.12, change24h: 12.45 },
      { rank: 4, symbol: 'PYTH', name: 'Pyth Network', price: 0.45, change24h: 8.92 },
      { rank: 5, symbol: 'JUP', name: 'Jupiter', price: 0.2503, change24h: -3.21 },
    ];
  }

  private getMockNetwork(): NetworkResult {
    return {
      tps: 3847,
      slot: 284_567_123,
      epoch: 612,
      epochProgress: 67.3,
      validators: 1847,
    };
  }

  private getMockYields(): YieldResult[] {
    return [
      { protocol: 'Marinade', token: 'mSOL', apy: 7.2, tvl: 1_200_000_000, risk: 'Low' },
      { protocol: 'Jito', token: 'jitoSOL', apy: 7.8, tvl: 800_000_000, risk: 'Low' },
      { protocol: 'Raydium', token: 'SOL-USDC LP', apy: 24.5, tvl: 450_000_000, risk: 'Medium' },
      { protocol: 'Kamino', token: 'USDC', apy: 12.1, tvl: 320_000_000, risk: 'Low' },
      { protocol: 'Marginfi', token: 'USDC', apy: 8.3, tvl: 280_000_000, risk: 'Low' },
    ];
  }

  private getMockAnalytics(): AnalyticsResult {
    return {
      totalPnl: 2847.32,
      pnlPercent: 18.4,
      winRate: 64.2,
      totalTrades: 142,
      bestTrade: '+$890 (SOL/USDC)',
      worstTrade: '-$234 (BONK/SOL)',
      avgHoldTime: '4.2 days',
      topTokens: [
        { symbol: 'SOL', pnl: 1420.50, trades: 45 },
        { symbol: 'JUP', pnl: 680.20, trades: 28 },
        { symbol: 'BONK', pnl: 340.12, trades: 35 },
        { symbol: 'WIF', pnl: -120.40, trades: 12 },
      ],
    };
  }

  private getMockNfts(): NftItem[] {
    return [
      { name: 'Mad Lad #4521', collection: 'Mad Lads', image: 'ML', floorPrice: 142.5, rarity: 'Top 10%', lastSale: 155.0 },
      { name: 'Clayno #892', collection: 'Claynosaurz', image: 'CL', floorPrice: 28.4, rarity: 'Top 25%', lastSale: 32.0 },
      { name: 'SMB #3341', collection: 'SMB Gen2', image: 'SM', floorPrice: 18.2, rarity: 'Top 40%', lastSale: 20.5 },
      { name: 'Tensorian #127', collection: 'Tensorians', image: 'TN', floorPrice: 8.5, rarity: 'Top 15%', lastSale: 9.8 },
    ];
  }

  private getMockAirdrops(): AirdropResult[] {
    return [
      { protocol: 'Jupiter', status: 'Claimable', amount: '1,247', token: 'JUP', deadline: '2025-03-15', eligible: true },
      { protocol: 'Tensor', status: 'Claimable', amount: '340', token: 'TNSR', deadline: '2025-04-01', eligible: true },
      { protocol: 'Kamino', status: 'Expired', amount: '—', token: 'KMNO', deadline: 'Expired', eligible: false },
      { protocol: 'Parcl', status: 'Upcoming', amount: 'TBD', token: 'PRCL', deadline: 'Q2 2025', eligible: false },
    ];
  }

  private getMockGas(): GasResult {
    return {
      baseFee: 0.000005,
      priorityLow: 0.00001,
      priorityMedium: 0.0001,
      priorityHigh: 0.001,
      swapCost: '~$0.02',
      transferCost: '~$0.001',
    };
  }

  private getMockWalletInfo(): WalletInfoResult {
    return {
      address: 'HwMdhvXYPPDircKQ7ub45XeMAeohJL4AT3V5CtshXtK6',
      solBalance: 24.56,
      tokenCount: 12,
      nftCount: 4,
      firstTx: '2024-01-15',
      totalTxs: 1847,
      label: 'Main Wallet',
    };
  }

  private async fetchTaxReport(): Promise<void> {
    const currentYear = new Date().getFullYear();
    const year = Number(this.query.params['year'] ?? currentYear - 1);
    const resp = await firstValueFrom(
      this.api.post<any>('/tax/report', {
        year,
        cost_basis_method: 'FIFO',
        include_staking: true,
        include_farming: true,
        include_nfts: false,
        include_airdrops: true,
      })
    );
    if (!resp?.success || !resp.report) {
      this.taxResult = this.getMockTax();
      this.loading.set(false);
      this.persistSnapshot();
      return;
    }
    const r = resp.report;
    const cg = r.capital_gains ?? {};
    const inc = r.income ?? {};
    const tot = r.totals ?? {};
    const stGains = cg.short_term_gains ?? 0;
    const ltGains = cg.long_term_gains ?? 0;
    const stLoss = cg.short_term_losses ?? 0;
    const ltLoss = cg.long_term_losses ?? 0;
    const totalIncome = tot.total_income ?? 0;
    this.taxResult = {
      year: r.year ?? year,
      totalGains: stGains + ltGains + totalIncome,
      totalLosses: stLoss + ltLoss,
      netGains: (tot.net_gain_loss ?? 0) + totalIncome,
      shortTermGains: stGains,
      longTermGains: ltGains,
      totalTxs: r.transaction_count ?? 0,
      categories: [
        { type: 'Short-term Gains', amount: stGains, count: 0 },
        { type: 'Long-term Gains', amount: ltGains, count: 0 },
        { type: 'Staking Rewards', amount: inc.staking_rewards ?? 0, count: 0 },
        { type: 'Airdrops', amount: inc.airdrops ?? 0, count: 0 },
        { type: 'Farming Rewards', amount: inc.farming_rewards ?? 0, count: 0 },
      ].filter(c => c.amount > 0),
    };
    this.loading.set(false);
    this.persistSnapshot();
  }

  private getMockTax(): TaxResult {
    return {
      year: 2025,
      totalGains: 4280.50,
      totalLosses: 1433.18,
      netGains: 2847.32,
      shortTermGains: 2100.00,
      longTermGains: 747.32,
      totalTxs: 142,
      categories: [
        { type: 'Swaps', amount: 1890.40, count: 85 },
        { type: 'Staking Rewards', amount: 420.50, count: 24 },
        { type: 'LP Fees', amount: 340.12, count: 18 },
        { type: 'Airdrops', amount: 196.30, count: 15 },
      ],
    };
  }

  private getMockAlerts(): AlertItem[] {
    return [
      { id: 'al-1', type: 'Price Above', token: 'SOL', condition: '>', targetValue: '$200.00', currentValue: '$178.05', status: 'active', createdAt: '1d ago' },
      { id: 'al-2', type: 'Price Below', token: 'JUP', condition: '<', targetValue: '$0.20', currentValue: '$0.2503', status: 'active', createdAt: '3d ago' },
      { id: 'al-3', type: 'Whale Alert', token: 'BONK', condition: 'Whale >$1M', targetValue: '$1,000,000', currentValue: '—', status: 'active', createdAt: '5d ago' },
      { id: 'al-4', type: 'Price Above', token: 'WIF', condition: '>', targetValue: '$2.00', currentValue: '$2.45', status: 'triggered', createdAt: '7d ago' },
    ];
  }

  formatNumber(n: number): string {
    if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return n.toFixed(2);
  }

  formatPrice(p: number): string {
    if (p === 0) return '—';
    if (p < 0.01) return `$${p.toFixed(6)}`;
    if (p < 1) return `$${p.toFixed(4)}`;
    return `$${p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  formatUsd(n: number): string {
    return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  get totalPortfolioValue(): string {
    const total = this.balanceResults.reduce((s, r) => s + r.value, 0);
    return this.formatUsd(total);
  }

  formatBalance(n: number): string {
    if (n >= 1000) return this.formatNumber(n);
    return n.toFixed(2);
  }

  riskBadgeClass(risk: string): string {
    return 'risk-badge--' + risk.toLowerCase();
  }
}
