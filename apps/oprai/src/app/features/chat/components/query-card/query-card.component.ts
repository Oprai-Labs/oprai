import { Component, Input, Output, EventEmitter, inject, signal, computed, effect, viewChild, ElementRef, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { toSignal, toObservable } from '@angular/core/rxjs-interop';
import { ParsedQuery, ParsedAction, IntentParserService } from '../../services/intent-parser.service';
import { ApiService } from '@core/services/api.service';
import { ChatApiService, QuerySnapshot } from '../../services/chat-api.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { WalletService } from '@core/services/wallet.service';
import { PortfolioService } from '@features/portfolio/services/portfolio.service';
import { SolanaRpcService } from '@features/portfolio/services/solana-rpc.service';
import { BirdeyeService } from '@features/portfolio/services/birdeye.service';
import { JupiterLendService, LendPosition, BorrowPosition } from '@core/services/market/jupiter-lend.service';
import { JupiterPerpService, PerpPosition } from '@core/services/market/jupiter-perp.service';
import { MeteoraService, DlmmPair, DammV2Pool, DammV1Pool } from '@core/services/market/meteora.service';
import { environment } from '../../../../../environments/environment';
import { firstValueFrom, debounceTime, distinctUntilChanged } from 'rxjs';

/** Mock query result types */
interface BalanceResult {
  token: string;
  symbol: string;
  balance: number;
  value: number;
  change24h: number;
  logoUri?: string | null;
  mint?: string;
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

/**
 * One Kamino Multiply pool from `kamino_multiply_markets`. `estMaxApyPct` is an
 * ESTIMATE (leverage·collateral-yield − borrow-cost from live on-chain rates),
 * NOT Kamino's exact displayed figure — the card labels it as such.
 */
interface KaminoMultiplyMarket {
  collToken: string; collMint: string;
  debtToken: string; debtMint: string;
  maxLeverage: number; maxLtvPct: number; estMaxApyPct: number; avgLeverage: number;
  tvlUsd: number; liquidityUsd: number; collSupplyApyPct: number; debtBorrowApyPct: number;
  // false when Kamino's borrow cap for this pair is full — pool can't be opened.
  // Optional for backwards-compat with older cached payloads (treated as true).
  borrowable?: boolean;
}

/**
 * Single row from the Raydium V3 `/pools/info/list` API response. Defined
 * inline (vs. lifting into a shared service file) because it's only used by
 * the QueryCard's Raydium pool list mini-app — no other consumer.
 */
interface RaydiumPool {
  id: string;
  /** "Concentrated" (CLMM) | "Standard" (AMM v4) — drives Deposit routing. */
  type: string;
  mintA: { address: string; symbol: string; decimals: number; logoURI?: string };
  mintB: { address: string; symbol: string; decimals: number; logoURI?: string };
  tvl: number;
  /** Spot price of mintA in mintB units (used to pre-fill CLMM range). */
  price?: number;
  /** Time-windowed metrics; only `day` is rendered in the table. */
  day?: { volume?: number; fee?: number; apr?: number };
  week?: { volume?: number; fee?: number; apr?: number };
}

const SOL_MINT = 'So11111111111111111111111111111111111111112';
const SOL_LOGO = 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png';

// Static lookup for the most common Solana tokens (avoids async registry for display)
const KNOWN_TOKENS: Record<string, { symbol: string; decimals: number }> = {
  // Wrapped SOL has the same mint string the SDK reports for native SOL
  // (`So11…`), but in a balance list it MUST render as "wSOL" so the user
  // can see their native SOL row separately from any wrapped balance left
  // over after a swap / LP unwind. The fetchSol path below assigns "SOL" to
  // the native lamport balance, leaving this entry to label the SPL token.
  'So11111111111111111111111111111111111111112': { symbol: 'wSOL', decimals: 9 },
  'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': { symbol: 'USDC', decimals: 6 },
  'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': { symbol: 'USDT', decimals: 6 },
  'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN': { symbol: 'JUP', decimals: 6 },
  'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263': { symbol: 'BONK', decimals: 5 },
  'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v': { symbol: 'JupSOL', decimals: 9 },
  'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn': { symbol: 'jitoSOL', decimals: 9 },
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
export class QueryCardComponent implements OnInit, OnDestroy {
  @Input({ required: true }) query!: ParsedQuery;
  @Input() sessionId: string | null = null;
  @Input() messageId: string | null = null;
  /** DB-persisted snapshot from a previous fetch; if present, skip re-fetching. */
  @Input() snapshot: QuerySnapshot | null = null;
  @Output() cancelAction = new EventEmitter<ParsedAction>();
  /**
   * Emitted when the user clicks a "use this pool / use this row" CTA on a
   * row of an interactive QueryCard (currently DLMM pool list). Carries a
   * fully-formed ParsedAction with the pool / mint identifiers pre-filled
   * so the chat-shell can append an action card to the same conversation,
   * letting the user enter only the amounts.
   */
  @Output() useAction = new EventEmitter<ParsedAction>();
  /**
   * UI-only feedback: which pool address row was just copied. Cleared
   * after a brief delay so the icon/label can flash "copied" state.
   */
  readonly copiedAddress = signal<string | null>(null);

  private readonly intentParser = inject(IntentParserService);
  private readonly api = inject(ApiService);
  private readonly chatApi = inject(ChatApiService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly priceFeed = inject(PriceFeedService);
  private readonly birdeye = inject(BirdeyeService);
  private readonly walletService = inject(WalletService);
  private readonly portfolioService = inject(PortfolioService);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly jupiterLend = inject(JupiterLendService);
  private readonly jupiterPerp = inject(JupiterPerpService);
  private readonly meteora = inject(MeteoraService);

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  private readonly BALANCE_PAGE_SIZE = 10;

  // Mock result data
  balanceResults: BalanceResult[] = [];

  // ── Balance search (debounced) ────────────────────────────────────────────
  readonly balanceSearchRaw = signal('');
  // 200 ms debounce + distinctUntilChanged — prevents thrashing on fast typing
  readonly balanceSearch = toSignal(
    toObservable(this.balanceSearchRaw).pipe(
      debounceTime(200),
      distinctUntilChanged(),
    ),
    { initialValue: '' }
  );

  // ── Sort state — default: value descending ───────────────────────────────
  readonly balanceSortField = signal<'value' | 'change24h'>('value');
  readonly balanceSortDir   = signal<'asc' | 'desc'>('desc');

  // ── Pagination ───────────────────────────────────────────────────────────
  readonly balancePage = signal(0);

  readonly filteredBalanceResults = computed(() => {
    const q = (this.balanceSearch() ?? '').toLowerCase().trim();

    // Filter: symbol, name, or mint prefix match
    let results: BalanceResult[] = q
      ? this.balanceResults.filter(r =>
          r.symbol.toLowerCase().includes(q) ||
          r.token.toLowerCase().includes(q) ||
          (r.mint?.toLowerCase().startsWith(q) ?? false)
        )
      : [...this.balanceResults];

    // Sort in place (copy already made above)
    const field = this.balanceSortField();
    const dir   = this.balanceSortDir();
    results.sort((a, b) => {
      const av = field === 'value' ? a.value : a.change24h;
      const bv = field === 'value' ? b.value : b.change24h;
      return dir === 'desc' ? bv - av : av - bv;
    });

    return results;
  });

  readonly balanceTotalPages = computed(() =>
    Math.max(1, Math.ceil(this.filteredBalanceResults().length / this.BALANCE_PAGE_SIZE))
  );

  readonly pagedBalanceResults = computed(() => {
    // Clamp page so stale page index never shows empty rows after a search
    const page = Math.min(this.balancePage(), this.balanceTotalPages() - 1);
    const all  = this.filteredBalanceResults();
    return all.slice(page * this.BALANCE_PAGE_SIZE, (page + 1) * this.BALANCE_PAGE_SIZE);
  });

  // ── Meteora DLMM pool list (server-side pagination) ─────────────────────
  // Each sort/page/search change triggers a refetch — the API can return
  // 100+ pools across many pages, so we never hold them all client-side.
  readonly DLMM_PAGE_SIZE = 10;
  readonly DLMM_SORT_OPTIONS: { field: 'tvl' | 'volume' | 'fee_tvl_ratio'; label: string }[] = [
    { field: 'tvl', label: 'TVL' },
    { field: 'volume', label: 'Volume' },
    { field: 'fee_tvl_ratio', label: 'Fee/TVL' },
  ];
  dlmmResults: DlmmPair[] = [];
  readonly dlmmPage = signal(1);            // 1-based, matches API
  readonly dlmmTotalPages = signal(1);
  readonly dlmmTotal = signal(0);
  readonly dlmmSortField = signal<'tvl' | 'volume' | 'fee_tvl_ratio'>('tvl');
  readonly dlmmSortDir   = signal<'asc' | 'desc'>('desc');
  readonly dlmmSearchRaw = signal('');
  readonly dlmmFetching = signal(false);
  private dlmmSearchDebounce: ReturnType<typeof setTimeout> | null = null;

  // ── Meteora DAMM v2 (constant-product / dynamic AMM) ──────────────────────
  readonly DAMMV2_PAGE_SIZE = 10;
  readonly DAMMV2_SORT_OPTIONS: { field: 'tvl' | 'volume' | 'fee_tvl_ratio'; label: string }[] = [
    { field: 'tvl', label: 'TVL' },
    { field: 'volume', label: 'Volume' },
    { field: 'fee_tvl_ratio', label: 'Fee/TVL' },
  ];
  dammV2Results: DammV2Pool[] = [];
  readonly dammV2Page = signal(1);
  readonly dammV2TotalPages = signal(1);
  readonly dammV2Total = signal(0);
  readonly dammV2SortField = signal<'tvl' | 'volume' | 'fee_tvl_ratio'>('tvl');
  readonly dammV2SortDir   = signal<'asc' | 'desc'>('desc');
  readonly dammV2SearchRaw = signal('');
  readonly dammV2Fetching = signal(false);
  private dammV2SearchDebounce: ReturnType<typeof setTimeout> | null = null;

  // ── Kamino Multiply pools (leveraged looping) ─────────────────────────────
  // The backend returns the full set (up to ~100); the card sorts, filters and
  // paginates entirely client-side over that snapshot.
  readonly KAMINO_MULT_PAGE_SIZE = 8;
  readonly KAMINO_MULT_SORT_OPTIONS: { field: 'apy' | 'leverage' | 'tvl' | 'liquidity'; label: string }[] = [
    { field: 'apy', label: 'Est. APY' },
    { field: 'leverage', label: 'Max Lev' },
    { field: 'tvl', label: 'TVL' },
    { field: 'liquidity', label: 'Liquidity' },
  ];
  // A SIGNAL (not a plain field): the filter/sort/paginate computeds below read
  // it, and computed() only tracks signal reads — a plain field wouldn't trigger
  // recompute when the fetch lands, leaving the card stuck empty.
  readonly kaminoMultiplyAll = signal<KaminoMultiplyMarket[]>([]);
  readonly kaminoMultiplyTotal = signal(0);
  readonly kaminoMultiplyPage = signal(1);
  readonly kaminoMultiplySortField = signal<'apy' | 'leverage' | 'tvl' | 'liquidity'>('apy');
  readonly kaminoMultiplySortDir = signal<'asc' | 'desc'>('desc');
  readonly kaminoMultiplySearchRaw = signal('');
  readonly kaminoMultiplyFetching = signal(false);
  private kaminoMultiplySearchDebounce: ReturnType<typeof setTimeout> | null = null;

  /** Filter (by coll/debt symbol) + sort the fetched set. */
  readonly kaminoMultiplyFiltered = computed<KaminoMultiplyMarket[]>(() => {
    const q = this.kaminoMultiplySearchRaw().trim().toUpperCase();
    const field = this.kaminoMultiplySortField();
    const dir = this.kaminoMultiplySortDir() === 'asc' ? 1 : -1;
    let rows = this.kaminoMultiplyAll();
    if (q) rows = rows.filter(r => r.collToken.toUpperCase().includes(q) || r.debtToken.toUpperCase().includes(q));
    const key: Record<string, (r: KaminoMultiplyMarket) => number> = {
      apy: r => r.estMaxApyPct, leverage: r => r.maxLeverage, tvl: r => r.tvlUsd, liquidity: r => r.liquidityUsd,
    };
    const k = key[field] ?? key['apy'];
    return [...rows].sort((a, b) => (k(a) - k(b)) * dir);
  });
  readonly kaminoMultiplyTotalPages = computed(() =>
    Math.max(1, Math.ceil(this.kaminoMultiplyFiltered().length / this.KAMINO_MULT_PAGE_SIZE)));
  readonly kaminoMultiplyPageRows = computed<KaminoMultiplyMarket[]>(() => {
    const start = (this.kaminoMultiplyPage() - 1) * this.KAMINO_MULT_PAGE_SIZE;
    return this.kaminoMultiplyFiltered().slice(start, start + this.KAMINO_MULT_PAGE_SIZE);
  });
  readonly kaminoMultiplyShowingRange = computed(() => {
    const total = this.kaminoMultiplyFiltered().length;
    const from = total === 0 ? 0 : (this.kaminoMultiplyPage() - 1) * this.KAMINO_MULT_PAGE_SIZE + 1;
    const to = Math.min(this.kaminoMultiplyPage() * this.KAMINO_MULT_PAGE_SIZE, total);
    return { from, to, total };
  });

  // ── Meteora DAMM v1 (legacy AMM, flat array — client-paginated) ───────────
  readonly DAMMV1_PAGE_SIZE = 10;
  readonly DAMMV1_SORT_OPTIONS: { field: 'pool_tvl' | 'weekly_base_apy' | 'weekly_trading_volume'; label: string }[] = [
    { field: 'pool_tvl', label: 'TVL' },
    { field: 'weekly_base_apy', label: 'APY' },
    { field: 'weekly_trading_volume', label: '7d Vol' },
  ];
  dammV1All: DammV1Pool[] = [];
  readonly dammV1Page = signal(1);
  readonly dammV1SortField = signal<'pool_tvl' | 'weekly_base_apy' | 'weekly_trading_volume'>('pool_tvl');
  readonly dammV1SortDir   = signal<'asc' | 'desc'>('desc');
  readonly dammV1SearchRaw = signal('');
  readonly dammV1Fetching = signal(false);
  private dammV1SearchDebounce: ReturnType<typeof setTimeout> | null = null;

  // ── Raydium pool list (server-side pagination, no search) ────────────────
  // Raydium V3 `/pools/info/list` accepts poolType / poolSortField / sortType
  // / page / pageSize but no `query` param — so we expose filter chips +
  // sort buttons only. Mirrors the DLMM mini-app otherwise.
  readonly RAYDIUM_PAGE_SIZE = 10;
  readonly RAYDIUM_POOL_TYPES: { value: 'all' | 'concentrated' | 'standard'; label: string }[] = [
    { value: 'all',          label: 'All' },
    { value: 'concentrated', label: 'CLMM' },
    { value: 'standard',     label: 'AMM' },
  ];
  readonly RAYDIUM_SORT_OPTIONS: { field: 'liquidity' | 'volume24h' | 'fee24h' | 'apr24h'; label: string }[] = [
    { field: 'liquidity',  label: 'Liquidity' },
    { field: 'volume24h',  label: 'Volume 24h' },
    { field: 'fee24h',     label: 'Fee 24h' },
    { field: 'apr24h',     label: 'APR 24h' },
  ];
  raydiumResults: RaydiumPool[] = [];
  readonly raydiumPage        = signal(1);
  // Raydium V3 doesn't expose a total-pool count — `data.count` is the
  // page size, not the total — so we drive Next/Prev via `hasNextPage`
  // instead of computing total pages.
  readonly raydiumHasNextPage = signal(false);
  readonly raydiumPoolType    = signal<'all' | 'concentrated' | 'standard'>('all');
  readonly raydiumSortField   = signal<'liquidity' | 'volume24h' | 'fee24h' | 'apr24h'>('liquidity');
  readonly raydiumSortDir     = signal<'asc' | 'desc'>('desc');
  readonly raydiumFetching    = signal(false);

  // Infinite-scroll plumbing. The template parks a `<div #raydiumSentinel>`
  // just below the last row; when it enters the viewport we auto-page. The
  // 200px rootMargin pre-fetches before the user hits the actual bottom so
  // scrolling feels continuous, not staccato. Prev/Next buttons stay in the
  // footer as a manual fallback (and for going backwards).
  readonly raydiumSentinel = viewChild<ElementRef<HTMLElement>>('raydiumSentinel');
  private _raydiumScrollObserver?: IntersectionObserver;
  private readonly _raydiumScrollEffect = effect(() => {
    const ref = this.raydiumSentinel();
    this._raydiumScrollObserver?.disconnect();
    this._raydiumScrollObserver = undefined;
    if (!ref || typeof IntersectionObserver === 'undefined') return;
    this._raydiumScrollObserver = new IntersectionObserver(
      (entries) => {
        if (!entries.some(e => e.isIntersecting)) return;
        if (!this.raydiumHasNextPage() || this.raydiumFetching()) return;
        this.raydiumNextPage();
      },
      { rootMargin: '200px 0px' },
    );
    this._raydiumScrollObserver.observe(ref.nativeElement);
  });

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
      case 'meteora_dlmm_get_pairs':
      case 'meteora_dammv2_get_pools':
      case 'meteora_dammv1_get_pools':
        return 'assets/icons/protocols/meteora.webp';
      case 'raydium_get_pools':
      case 'raydium_search_pools':
        return 'assets/icons/protocols/raydium.png';
      case 'kamino_multiply_markets':
        return 'assets/icons/protocols/kamino.svg';
      default:
        return null;
    }
  }

  /** Periodic refetch so live pool listings (DLMM/DAMM) stay close to the
   *  protocol's official site while the user is reading the card. Cleared
   *  in ngOnDestroy so it never runs for a chat the user has navigated
   *  away from. */
  private livePollTimer: ReturnType<typeof setInterval> | null = null;

  /** True iff this query type renders live market data that goes stale by
   *  the minute (TVL, 24h volume). */
  private get isLivePoolList(): boolean {
    return (
      this.query.type === 'meteora_dlmm_get_pairs' ||
      this.query.type === 'meteora_dammv2_get_pools' ||
      this.query.type === 'meteora_dammv1_get_pools' ||
      this.query.type === 'raydium_get_pools' ||
      this.query.type === 'raydium_search_pools'
    );
  }

  /** Refetch whichever live-pool listing this card represents. Used by both
   *  the manual refresh button and the 30s auto-poll. */
  refreshLiveData(): void {
    switch (this.query.type) {
      case 'meteora_dlmm_get_pairs':    void this.fetchDlmmPairs();   break;
      case 'meteora_dammv2_get_pools':  void this.fetchDammV2Pools(); break;
      case 'meteora_dammv1_get_pools':  void this.fetchDammV1Pools(); break;
      case 'raydium_get_pools':         void this.fetchRaydiumPools(); break;
      case 'raydium_search_pools':      void this.fetchRaydiumPools(); break;
      case 'kamino_multiply_markets':   void this.fetchKaminoMultiplyMarkets(); break;
    }
  }

  ngOnInit(): void {
    if (this.snapshot) {
      this.restoreSnapshot(this.snapshot);
      // Snapshot freshness check — restored snapshots older than 60s are
      // already drifting from the live API, so kick off a silent refetch.
      // The snapshot still renders first so the user sees data, not a spinner.
      const fetchedMs = this.snapshot.fetchedAt ? Date.parse(this.snapshot.fetchedAt) : 0;
      const ageMs = Date.now() - (Number.isFinite(fetchedMs) ? fetchedMs : 0);
      if (this.isLivePoolList && ageMs > 60_000) {
        this.refreshLiveData();
      }
      // Perp positions are a record: a position closed since the snapshot (by
      // the card's × or via chat) should stay in the card flagged "closed",
      // not silently vanish. Reconcile the restored list against live state.
      if (this.query.type === 'perp_positions') {
        void this.reconcilePerpPositions();
      }
    } else {
      this.simulateQuery();
    }

    // While the card is mounted (i.e. visible in the active chat), refresh
    // every 30s. Only for live pool listings — other query types either
    // don't change (token info) or have their own update flow. Naturally
    // scoped to the active chat: navigating away triggers ngOnDestroy.
    if (this.isLivePoolList) {
      this.livePollTimer = setInterval(() => {
        if (document.hidden) return; // skip when tab is in background
        // Don't stack concurrent fetches — whichever fetching signal is
        // relevant for this card type acts as the busy guard.
        const busy =
          (this.query.type === 'meteora_dlmm_get_pairs'   && this.dlmmFetching())    ||
          (this.query.type === 'meteora_dammv2_get_pools' && this.dammV2Fetching())  ||
          (this.query.type === 'meteora_dammv1_get_pools' && this.dammV1Fetching())  ||
          ((this.query.type === 'raydium_get_pools' || this.query.type === 'raydium_search_pools') && this.raydiumFetching());
        if (busy) return;
        this.refreshLiveData();
      }, 30_000);
    }
  }

  ngOnDestroy(): void {
    if (this.livePollTimer) {
      clearInterval(this.livePollTimer);
      this.livePollTimer = null;
    }
    this._raydiumScrollObserver?.disconnect();
    this._raydiumScrollObserver = undefined;
  }

  private restoreSnapshot(snap: QuerySnapshot): void {
    const d = snap.data as Record<string, unknown>;
    if (d['limitOrderResults']) this.limitOrderResults = d['limitOrderResults'] as LimitOrder[];
    if (d['dcaResults'])        this.dcaResults        = d['dcaResults']        as DcaOrder[];
    if (d['balanceResults']) {
      // Old snapshots labelled wSOL as "SOL" — same as native SOL — because
      // the registry path was authoritative back then. Re-label on restore so
      // historical chats render correctly without forcing a refetch.
      this.balanceResults = (d['balanceResults'] as BalanceResult[]).map(r =>
        r.mint === SOL_MINT
          ? { ...r, symbol: 'wSOL', token: 'Wrapped SOL' }
          : r,
      );
    }
    if (d['priceResult'])       this.priceResult       = d['priceResult']       as PriceResult;
    if (d['positionResults'])   this.positionResults   = d['positionResults']   as PositionResult[];
    if (d['transactionResults'])this.transactionResults= d['transactionResults']as TransactionResult[];
    if (d['trendingResults'])   this.trendingResults   = d['trendingResults']   as TrendingResult[];
    if (d['networkResult'])     this.networkResult     = d['networkResult']     as NetworkResult;
    if (d['yieldResults'])      this.yieldResults      = d['yieldResults']      as YieldResult[];
    if (d['perpPositions'])     this.perpPositions     = d['perpPositions']     as PerpPosition[];
    if (d['analyticsResult'])   this.analyticsResult   = d['analyticsResult']   as AnalyticsResult;
    if (d['nftResults'])        this.nftResults        = d['nftResults']        as NftItem[];
    if (d['airdropResults'])    this.airdropResults    = d['airdropResults']    as AirdropResult[];
    if (d['gasResult'])         this.gasResult         = d['gasResult']         as GasResult;
    if (d['walletInfoResult'])  this.walletInfoResult  = d['walletInfoResult']  as WalletInfoResult;
    if (d['taxResult'])         this.taxResult         = d['taxResult']         as TaxResult;
    if (d['alertResults'])      this.alertResults      = d['alertResults']      as AlertItem[];
    if (d['dlmmResults']) {
      this.dlmmResults = d['dlmmResults'] as DlmmPair[];
      this.dlmmTotal.set((d['dlmmTotal'] as number | undefined) ?? this.dlmmResults.length);
      this.dlmmTotalPages.set((d['dlmmTotalPages'] as number | undefined) ?? 1);
      this.dlmmPage.set((d['dlmmPage'] as number | undefined) ?? 1);
      this.dlmmSortField.set((d['dlmmSortField'] as 'tvl' | 'volume' | 'fee_tvl_ratio' | undefined) ?? 'tvl');
      this.dlmmSortDir.set((d['dlmmSortDir'] as 'asc' | 'desc' | undefined) ?? 'desc');
    }
    if (d['dammV2Results']) {
      this.dammV2Results = d['dammV2Results'] as DammV2Pool[];
      this.dammV2Total.set((d['dammV2Total'] as number | undefined) ?? this.dammV2Results.length);
      this.dammV2TotalPages.set((d['dammV2TotalPages'] as number | undefined) ?? 1);
      this.dammV2Page.set((d['dammV2Page'] as number | undefined) ?? 1);
      this.dammV2SortField.set((d['dammV2SortField'] as 'tvl' | 'volume' | 'fee_tvl_ratio' | undefined) ?? 'tvl');
      this.dammV2SortDir.set((d['dammV2SortDir'] as 'asc' | 'desc' | undefined) ?? 'desc');
    }
    if (d['dammV1All']) {
      this.dammV1All = d['dammV1All'] as DammV1Pool[];
      this.dammV1Page.set((d['dammV1Page'] as number | undefined) ?? 1);
      this.dammV1SortField.set(
        (d['dammV1SortField'] as 'pool_tvl' | 'weekly_base_apy' | 'weekly_trading_volume' | undefined)
        ?? 'pool_tvl');
      this.dammV1SortDir.set((d['dammV1SortDir'] as 'asc' | 'desc' | undefined) ?? 'desc');
    }
    if (d['raydiumResults']) {
      this.raydiumResults = d['raydiumResults'] as RaydiumPool[];
      this.raydiumHasNextPage.set((d['raydiumHasNextPage'] as boolean | undefined) ?? false);
      this.raydiumPage.set((d['raydiumPage'] as number | undefined) ?? 1);
      this.raydiumPoolType.set(
        (d['raydiumPoolType'] as 'all' | 'concentrated' | 'standard' | undefined) ?? 'all');
      this.raydiumSortField.set(
        (d['raydiumSortField'] as 'liquidity' | 'volume24h' | 'fee24h' | 'apr24h' | undefined) ?? 'liquidity');
      this.raydiumSortDir.set((d['raydiumSortDir'] as 'asc' | 'desc' | undefined) ?? 'desc');
    }
    if (d['kaminoMultiplyAll']) {
      this.kaminoMultiplyAll.set(d['kaminoMultiplyAll'] as KaminoMultiplyMarket[]);
      this.kaminoMultiplyTotal.set((d['kaminoMultiplyTotal'] as number | undefined) ?? this.kaminoMultiplyAll().length);
      this.kaminoMultiplySortField.set((d['kaminoMultiplySortField'] as 'apy' | 'leverage' | 'tvl' | 'liquidity' | undefined) ?? 'apy');
      this.kaminoMultiplySortDir.set((d['kaminoMultiplySortDir'] as 'asc' | 'desc' | undefined) ?? 'desc');
    }
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
    if (this.perpPositions.length)      d['perpPositions']      = this.perpPositions;
    if (this.analyticsResult)           d['analyticsResult']    = this.analyticsResult;
    if (this.nftResults.length)         d['nftResults']         = this.nftResults;
    if (this.airdropResults.length)     d['airdropResults']     = this.airdropResults;
    if (this.gasResult)                 d['gasResult']          = this.gasResult;
    if (this.walletInfoResult)          d['walletInfoResult']   = this.walletInfoResult;
    if (this.taxResult)                 d['taxResult']          = this.taxResult;
    if (this.alertResults.length)       d['alertResults']       = this.alertResults;
    if (this.dlmmResults.length) {
      d['dlmmResults']    = this.dlmmResults;
      d['dlmmTotal']      = this.dlmmTotal();
      d['dlmmTotalPages'] = this.dlmmTotalPages();
      d['dlmmPage']       = this.dlmmPage();
      d['dlmmSortField']  = this.dlmmSortField();
      d['dlmmSortDir']    = this.dlmmSortDir();
    }
    if (this.dammV2Results.length) {
      d['dammV2Results']    = this.dammV2Results;
      d['dammV2Total']      = this.dammV2Total();
      d['dammV2TotalPages'] = this.dammV2TotalPages();
      d['dammV2Page']       = this.dammV2Page();
      d['dammV2SortField']  = this.dammV2SortField();
      d['dammV2SortDir']    = this.dammV2SortDir();
    }
    if (this.dammV1All.length) {
      d['dammV1All']       = this.dammV1All;
      d['dammV1Page']      = this.dammV1Page();
      d['dammV1SortField'] = this.dammV1SortField();
      d['dammV1SortDir']   = this.dammV1SortDir();
    }
    if (this.raydiumResults.length) {
      d['raydiumResults']     = this.raydiumResults;
      d['raydiumHasNextPage'] = this.raydiumHasNextPage();
      d['raydiumPage']        = this.raydiumPage();
      d['raydiumPoolType']    = this.raydiumPoolType();
      d['raydiumSortField']   = this.raydiumSortField();
      d['raydiumSortDir']     = this.raydiumSortDir();
    }
    if (this.kaminoMultiplyAll().length) {
      d['kaminoMultiplyAll']       = this.kaminoMultiplyAll();
      d['kaminoMultiplyTotal']     = this.kaminoMultiplyTotal();
      d['kaminoMultiplySortField'] = this.kaminoMultiplySortField();
      d['kaminoMultiplySortDir']   = this.kaminoMultiplySortDir();
    }
    return d;
  }

  onBalanceSearch(e: Event): void {
    const val = (e.target as HTMLInputElement).value.slice(0, 100);
    this.balanceSearchRaw.set(val);
    this.balancePage.set(0); // reset page immediately on any keystroke
  }

  toggleSort(field: 'value' | 'change24h'): void {
    if (this.balanceSortField() === field) {
      this.balanceSortDir.update(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.balanceSortField.set(field);
      this.balanceSortDir.set('desc');
    }
    this.balancePage.set(0);
  }

  balancePrevPage(): void {
    if (this.balancePage() > 0) this.balancePage.update(p => p - 1);
  }

  balanceNextPage(): void {
    if (this.balancePage() < this.balanceTotalPages() - 1) this.balancePage.update(p => p + 1);
  }

  /** Stable key for snapshot lookup — type + sorted param pairs. */
  private snapshotKey(): string {
    const sorted = Object.entries(this.query.params ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
    return `${this.query.type}:${JSON.stringify(sorted)}`;
  }

  private persistSnapshot(): void {
    if (!this.sessionId || !this.messageId) return;
    const snap: QuerySnapshot = {
      type: this.query.type,
      data: this.currentSnapshotData(),
      fetchedAt: new Date().toISOString(),
    };
    this.chatApi.updateMessageMeta(this.sessionId, this.messageId, {
      query_snapshots: { [this.snapshotKey()]: snap },
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

          // Jupiter Recurring v1 returns human-readable amounts (inAmountPerCycle,
          // inDeposited, inUsed); `raw*` are the base-unit variants. Cycle counts
          // are not returned — derive them from deposited/used ÷ per-cycle.
          const perCycleAmount = parseFloat(order.inAmountPerCycle ?? '0');
          const deposited = parseFloat(order.inDeposited ?? '0');
          const used = parseFloat(order.inUsed ?? '0');
          const totalCycles = perCycleAmount > 0 ? Math.round(deposited / perCycleAmount) : 0;
          const cyclesExecuted = perCycleAmount > 0 ? Math.round(used / perCycleAmount) : 0;
          const remaining = Math.max(0, totalCycles - cyclesExecuted);

          const cycleFrequency: number = Number(order.cycleFrequency ?? 86400);
          const frequency = this.formatCycleFrequency(cycleFrequency);

          const nextCycleAt: number = Number(order.nextCycleAt ?? 0);
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

  /**
   * Merge live perp state into a restored snapshot: positions still open are
   * refreshed with live values; positions no longer open are marked `closed`
   * and KEPT (so a closed position reads as closed instead of disappearing).
   */
  private async reconcilePerpPositions(): Promise<void> {
    const wallet = this.walletService.publicKey();
    if (!wallet || this.perpPositions.length === 0) return;
    // Only reconcile on a CONFIRMED live result. A transient/failed fetch must
    // never flip an open position to "closed" — that's how a still-open Jupiter
    // position wrongly showed as closed.
    const { ok, positions: live } = await this.jupiterPerp.getPositionsResult(wallet);
    if (!ok) return;
    const liveByKey = new Map(live.map(p => [`${p.market}:${p.side}`, p]));
    this.perpPositions = this.perpPositions.map(p => {
      const l = liveByKey.get(`${p.market}:${p.side}`);
      // Present in live → still open (refresh values). Absent → genuinely closed.
      return l ? { ...l, closed: false } : { ...p, closed: true };
    });
    this.persistSnapshot();
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

  /**
   * Fetch one server-side page of DLMM pools. Called on initial render and on
   * every page/sort/search change. The API caps at ~10 rows per page and we
   * just hand them to the template — no client-side filtering or trimming.
   */
  private async fetchDlmmPairs(): Promise<void> {
    this.dlmmFetching.set(true);
    this.error.set(null);

    // The model can pre-seed `query` (e.g. "jupSOL"); the user's search box
    // overrides it once they start typing.
    const searchOverride = (this.dlmmSearchRaw() ?? '').trim();
    const seedQuery = (this.query.params['query'] as string | undefined)?.trim();
    const queryStr = searchOverride || seedQuery || undefined;

    const sortBy = `${this.dlmmSortField()}:${this.dlmmSortDir()}`;

    const page = await this.meteora.fetchDlmmPairs({
      query: queryStr,
      page: this.dlmmPage(),
      pageSize: this.DLMM_PAGE_SIZE,
      sortBy,
    });

    if (!page) {
      // Network or server error — keep whatever rows we already had so the
      // user isn't yanked back to a blank state on a transient failure.
      this.error.set('Failed to load DLMM pools');
      this.dlmmFetching.set(false);
      this.loading.set(false);
      return;
    }

    this.dlmmResults = page.data ?? [];
    this.dlmmTotal.set(page.total ?? this.dlmmResults.length);
    this.dlmmTotalPages.set(Math.max(1, page.pages ?? 1));
    // Server clamps the page; mirror its echo so prev/next stays accurate.
    if (page.current_page) this.dlmmPage.set(page.current_page);

    this.dlmmFetching.set(false);
    this.loading.set(false);
    this.persistSnapshot();
  }

  onDlmmSearch(e: Event): void {
    const val = (e.target as HTMLInputElement).value.slice(0, 100);
    this.dlmmSearchRaw.set(val);
    this.dlmmPage.set(1);
    if (this.dlmmSearchDebounce) clearTimeout(this.dlmmSearchDebounce);
    this.dlmmSearchDebounce = setTimeout(() => {
      this.dlmmSearchDebounce = null;
      void this.fetchDlmmPairs();
    }, 250);
  }

  onDlmmSortChange(field: 'tvl' | 'volume' | 'fee_tvl_ratio'): void {
    if (this.dlmmSortField() === field) {
      this.dlmmSortDir.update(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.dlmmSortField.set(field);
      this.dlmmSortDir.set('desc');
    }
    this.dlmmPage.set(1);
    void this.fetchDlmmPairs();
  }

  dlmmPrevPage(): void {
    if (this.dlmmPage() > 1) {
      this.dlmmPage.update(p => p - 1);
      void this.fetchDlmmPairs();
    }
  }

  dlmmNextPage(): void {
    if (this.dlmmPage() < this.dlmmTotalPages()) {
      this.dlmmPage.update(p => p + 1);
      void this.fetchDlmmPairs();
    }
  }

  // ── DAMM v2 ─────────────────────────────────────────────────────────────
  private async fetchDammV2Pools(): Promise<void> {
    this.dammV2Fetching.set(true);
    this.error.set(null);
    const searchOverride = (this.dammV2SearchRaw() ?? '').trim();
    const seedQuery = (this.query.params['query'] as string | undefined)?.trim();
    const queryStr = searchOverride || seedQuery || undefined;
    const sortBy = `${this.dammV2SortField()}:${this.dammV2SortDir()}`;
    const page = await this.meteora.fetchDammV2Pools({
      query: queryStr,
      page: this.dammV2Page(),
      pageSize: this.DAMMV2_PAGE_SIZE,
      sortBy,
    });
    if (!page) {
      this.error.set('Failed to load DAMM v2 pools');
      this.dammV2Fetching.set(false);
      this.loading.set(false);
      return;
    }
    this.dammV2Results = page.data ?? [];
    this.dammV2Total.set(page.total ?? this.dammV2Results.length);
    this.dammV2TotalPages.set(Math.max(1, page.pages ?? 1));
    if (page.current_page) this.dammV2Page.set(page.current_page);
    this.dammV2Fetching.set(false);
    this.loading.set(false);
    this.persistSnapshot();
  }

  // ── Kamino Multiply pools ───────────────────────────────────────────────
  /** Fetch the full Multiply pool set once (client sorts/filters/paginates). */
  private async fetchKaminoMultiplyMarkets(): Promise<void> {
    this.kaminoMultiplyFetching.set(true);
    this.error.set(null);
    try {
      const seedToken = (this.query.params['token'] as string | undefined)?.trim();
      const resp = await firstValueFrom(
        this.api.post<{ data?: { markets?: KaminoMultiplyMarket[]; total?: number } }>(
          '/actions/build',
          { type: 'kamino_multiply_markets', params: { limit: '100', ...(seedToken ? { token: seedToken } : {}) } },
        ),
      );
      const markets = resp?.data?.markets ?? [];
      this.kaminoMultiplyAll.set(markets);
      this.kaminoMultiplyTotal.set(resp?.data?.total ?? markets.length);
    } catch {
      this.error.set('Failed to load Kamino Multiply pools');
    } finally {
      this.kaminoMultiplyFetching.set(false);
      this.loading.set(false);
      this.persistSnapshot();
    }
  }

  onKaminoMultiplySearch(e: Event): void {
    const val = (e.target as HTMLInputElement).value.slice(0, 40);
    this.kaminoMultiplySearchRaw.set(val);
    this.kaminoMultiplyPage.set(1);
  }

  onKaminoMultiplySortChange(field: 'apy' | 'leverage' | 'tvl' | 'liquidity'): void {
    if (this.kaminoMultiplySortField() === field) {
      this.kaminoMultiplySortDir.update(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      this.kaminoMultiplySortField.set(field);
      this.kaminoMultiplySortDir.set('desc');
    }
    this.kaminoMultiplyPage.set(1);
  }

  kaminoMultiplyGoToPage(page: number): void {
    this.kaminoMultiplyPage.set(Math.max(1, Math.min(page, this.kaminoMultiplyTotalPages())));
  }
  kaminoMultiplyPrevPage(): void { this.kaminoMultiplyGoToPage(this.kaminoMultiplyPage() - 1); }
  kaminoMultiplyNextPage(): void { this.kaminoMultiplyGoToPage(this.kaminoMultiplyPage() + 1); }

  /** Resolve a token logo live from the registry by mint (dual-icon rows). */
  logoForMint(mint: string): string | null {
    void this.tokenRegistry.version(); // signal dependency for re-render
    const logo = mint ? this.tokenRegistry.getToken(mint)?.logoURI : null;
    // Kamino pairs include LSTs/stables (PYUSD, cbBTC, hubSOL, FDUSD…) that
    // aren't in the strict list — fire-and-forget fetch their logo, which bumps
    // version() and re-renders this cell with the real icon (no more fallback).
    if (!logo && mint) this.tokenRegistry.resolveAsync(mint);
    return logo ?? null;
  }

  /** Pre-fill and emit a kamino_multiply_open action for the chosen pool. */
  useMultiplyMarket(r: KaminoMultiplyMarket): void {
    // Capped pool — opening would revert on-chain (6089). Don't emit a doomed
    // action; the row is already visually marked and its button disabled.
    if (r.borrowable === false) return;
    // Pass MINT addresses, not symbols — the backend resolves the reserve by
    // mint, and its static symbol map doesn't know LSTs/newer tokens (cbBTC,
    // PYUSD…), which would otherwise fail as a non-base58 "address".
    const params: Record<string, string> = {
      token: r.collMint,
      debtToken: r.debtMint,
      leverage: String(Math.min(r.maxLeverage, 3)),
    };
    this.useAction.emit({
      type: 'kamino_multiply_open',
      params,
      raw: `[ACTION:kamino_multiply_open] ${JSON.stringify(params)}`,
    });
  }

  onDammV2Search(e: Event): void {
    const val = (e.target as HTMLInputElement).value.slice(0, 100);
    this.dammV2SearchRaw.set(val);
    this.dammV2Page.set(1);
    if (this.dammV2SearchDebounce) clearTimeout(this.dammV2SearchDebounce);
    this.dammV2SearchDebounce = setTimeout(() => {
      this.dammV2SearchDebounce = null;
      void this.fetchDammV2Pools();
    }, 250);
  }

  onDammV2SortChange(field: 'tvl' | 'volume' | 'fee_tvl_ratio'): void {
    if (this.dammV2SortField() === field) {
      this.dammV2SortDir.update(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.dammV2SortField.set(field);
      this.dammV2SortDir.set('desc');
    }
    this.dammV2Page.set(1);
    void this.fetchDammV2Pools();
  }

  dammV2PrevPage(): void {
    if (this.dammV2Page() > 1) {
      this.dammV2Page.update(p => p - 1);
      void this.fetchDammV2Pools();
    }
  }

  dammV2NextPage(): void {
    if (this.dammV2Page() < this.dammV2TotalPages()) {
      this.dammV2Page.update(p => p + 1);
      void this.fetchDammV2Pools();
    }
  }

  dammV2GoToPage(page: number): void {
    const target = Math.max(1, Math.min(page, this.dammV2TotalPages()));
    if (target === this.dammV2Page()) return;
    this.dammV2Page.set(target);
    void this.fetchDammV2Pools();
  }

  /** 24h volume for a DAMM v2 pool — handles either `volume.24h` or `volume["24h"]`. */
  dammV2Volume24h(p: DammV2Pool): number {
    return p.volume?.['24h'] ?? 0;
  }

  /** Computed APY for a DAMM v2 pool (display only). farm_apy + base/24h fee yield. */
  dammV2Apy(p: DammV2Pool): number {
    const farmApy = p.farm_apy ?? 0;
    const fee24h = p.fees?.['24h'] ?? 0;
    const tvl = p.tvl || 1;
    const baseApy = (fee24h / tvl) * 365 * 100;
    return baseApy + farmApy;
  }

  get dammV2ShowingRange(): { from: number; to: number; total: number } {
    const total = this.dammV2Total();
    const size = this.DAMMV2_PAGE_SIZE;
    const from = total === 0 ? 0 : (this.dammV2Page() - 1) * size + 1;
    const to = Math.min(this.dammV2Page() * size, total);
    return { from, to, total };
  }

  // ── DAMM v1 ─────────────────────────────────────────────────────────────
  // The legacy AMM API hands back a flat array (no server-side paging) so
  // we sort/filter/page client-side over the cached `dammV1All`.
  private async fetchDammV1Pools(): Promise<void> {
    this.dammV1Fetching.set(true);
    this.error.set(null);
    const seedQuery = (this.query.params['query'] as string | undefined)?.trim();
    const data = await this.meteora.fetchDammV1Pools({
      query: this.dammV1SearchRaw() || seedQuery || undefined,
      // The endpoint accepts `limit` but no real cursor — pull a generous
      // slice once and page client-side.
      limit: 500,
    });
    if (!data) {
      this.error.set('Failed to load DAMM v1 pools');
      this.dammV1Fetching.set(false);
      this.loading.set(false);
      return;
    }
    this.dammV1All = data;
    this.dammV1Fetching.set(false);
    this.loading.set(false);
    this.persistSnapshot();
  }

  /** Sorted, filtered, sliced view of `dammV1All` — used by the template. */
  get dammV1Visible(): DammV1Pool[] {
    const q = this.dammV1SearchRaw().trim().toLowerCase();
    let pool = this.dammV1All;
    if (q) {
      pool = pool.filter(p =>
        (p.pool_name ?? '').toLowerCase().includes(q) ||
        (p.pool_address ?? '').toLowerCase().includes(q),
      );
    }
    const field = this.dammV1SortField();
    const dir = this.dammV1SortDir() === 'desc' ? -1 : 1;
    pool = [...pool].sort((a, b) => {
      const va = parseFloat(a[field] as string) || 0;
      const vb = parseFloat(b[field] as string) || 0;
      return (va - vb) * dir;
    });
    const size = this.DAMMV1_PAGE_SIZE;
    const start = (this.dammV1Page() - 1) * size;
    return pool.slice(start, start + size);
  }

  get dammV1FilteredTotal(): number {
    const q = this.dammV1SearchRaw().trim().toLowerCase();
    if (!q) return this.dammV1All.length;
    return this.dammV1All.filter(p =>
      (p.pool_name ?? '').toLowerCase().includes(q) ||
      (p.pool_address ?? '').toLowerCase().includes(q),
    ).length;
  }

  get dammV1TotalPages(): number {
    return Math.max(1, Math.ceil(this.dammV1FilteredTotal / this.DAMMV1_PAGE_SIZE));
  }

  get dammV1ShowingRange(): { from: number; to: number; total: number } {
    const total = this.dammV1FilteredTotal;
    const size = this.DAMMV1_PAGE_SIZE;
    const from = total === 0 ? 0 : (this.dammV1Page() - 1) * size + 1;
    const to = Math.min(this.dammV1Page() * size, total);
    return { from, to, total };
  }

  onDammV1Search(e: Event): void {
    const val = (e.target as HTMLInputElement).value.slice(0, 100);
    this.dammV1SearchRaw.set(val);
    this.dammV1Page.set(1);
    if (this.dammV1SearchDebounce) clearTimeout(this.dammV1SearchDebounce);
    this.dammV1SearchDebounce = setTimeout(() => {
      this.dammV1SearchDebounce = null;
      // No refetch needed — search is client-side over the cached set.
    }, 100);
  }

  onDammV1SortChange(field: 'pool_tvl' | 'weekly_base_apy' | 'weekly_trading_volume'): void {
    if (this.dammV1SortField() === field) {
      this.dammV1SortDir.update(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.dammV1SortField.set(field);
      this.dammV1SortDir.set('desc');
    }
    this.dammV1Page.set(1);
  }

  dammV1PrevPage(): void {
    if (this.dammV1Page() > 1) this.dammV1Page.update(p => p - 1);
  }

  dammV1NextPage(): void {
    if (this.dammV1Page() < this.dammV1TotalPages) this.dammV1Page.update(p => p + 1);
  }

  dammV1GoToPage(page: number): void {
    const target = Math.max(1, Math.min(page, this.dammV1TotalPages));
    this.dammV1Page.set(target);
  }

  /** Pretty-print numeric string from DAMM v1 raw API. */
  dammV1Num(s: string | undefined): number {
    return parseFloat(s ?? '0') || 0;
  }

  // ── Raydium pools ───────────────────────────────────────────────────────
  /**
   * Fetch one server-side page of Raydium pools from `/actions/build` with
   * `type=raydium_get_pools`. The Rust solana-service forwards to the V3
   * `/pools/info/list` endpoint and embeds the raw response under
   * `preview.params` — Raydium V3 wraps the pool array two levels deep,
   * which is why we drill `preview.params.data.data` to reach rows.
   */
  private async fetchRaydiumPools(): Promise<void> {
    this.raydiumFetching.set(true);
    this.error.set(null);

    // When the query came in as a pair-filtered search (`raydium_search_pools`),
    // route to the search-by-mint endpoint with tokenA/tokenB from query.params.
    // Pool-type chips still apply so the user can flip CLMM ↔ Standard within
    // the same pair. Same response shape as `/pools/info/list`, so the rest of
    // the render path is unchanged.
    const isSearch = this.query.type === 'raydium_search_pools';
    const tokenA = isSearch ? (this.query.params?.['tokenA'] as string | undefined) : undefined;
    const tokenB = isSearch ? (this.query.params?.['tokenB'] as string | undefined) : undefined;
    const body = isSearch && tokenA
      ? {
          type: 'raydium_search_pools',
          params: {
            tokenA,
            ...(tokenB ? { tokenB } : {}),
            poolType: this.raydiumPoolType(),
            sortField: this.raydiumSortField(),
            pageSize: this.RAYDIUM_PAGE_SIZE,
          },
        }
      : {
          type: 'raydium_get_pools',
          params: {
            poolType: this.raydiumPoolType(),
            sortField: this.raydiumSortField(),
            sortType: this.raydiumSortDir(),
            page: this.raydiumPage(),
            pageSize: this.RAYDIUM_PAGE_SIZE,
          },
        };

    try {
      const resp = await firstValueFrom(this.api.post<any>('/actions/build', body));
      // preview.params holds the unmodified Raydium API envelope:
      //   { id, success, data: { count, hasNextPage, data: RaydiumPool[] } }
      // Raydium V3 quirk: `count` is the page size (10), NOT the total —
      // there's no field that exposes the total pool count. We drive
      // pagination off `hasNextPage` instead.
      const apiData = resp?.preview?.params?.data;
      const rows: RaydiumPool[] = Array.isArray(apiData?.data) ? apiData.data : [];
      const hasNext: boolean = !!apiData?.hasNextPage;

      // Append vs replace: page == 1 is a fresh load (initial mount, sort or
      // filter change, manual refresh) — replace. page > 1 is the user (or
      // the auto-scroll observer) asking for the next slice — append.
      //
      // Defense in depth: dedupe by pool id when appending. If the backend
      // (or upstream Raydium API) returns the same row across pages — a known
      // failure mode for badly-paginated APIs — silently dropping the dupes
      // keeps `@for ... track p.id` happy and prevents the table from
      // visually inflating with phantom rows. If EVERY appended row is a
      // dup, the page added nothing real → flip `hasNextPage` off so the
      // observer stops re-firing on an unchanged sentinel position.
      let effectiveHasNext = hasNext;
      if (this.raydiumPage() > 1) {
        const seen = new Set(this.raydiumResults.map(r => r.id));
        const fresh = rows.filter(r => !seen.has(r.id));
        if (fresh.length === 0) {
          // Backend claimed more pages but returned only dupes → stop looping.
          effectiveHasNext = false;
        } else {
          this.raydiumResults = [...this.raydiumResults, ...fresh];
        }
      } else {
        this.raydiumResults = rows;
      }
      this.raydiumHasNextPage.set(effectiveHasNext);
      this.raydiumFetching.set(false);
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load Raydium pools');
      this.raydiumFetching.set(false);
      this.loading.set(false);
    }
  }

  onRaydiumPoolTypeChange(t: 'all' | 'concentrated' | 'standard'): void {
    if (this.raydiumPoolType() === t) return;
    this.raydiumPoolType.set(t);
    this.raydiumPage.set(1);
    void this.fetchRaydiumPools();
  }

  onRaydiumSortChange(field: 'liquidity' | 'volume24h' | 'fee24h' | 'apr24h'): void {
    if (this.raydiumSortField() === field) {
      this.raydiumSortDir.update(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      this.raydiumSortField.set(field);
      this.raydiumSortDir.set('desc');
    }
    this.raydiumPage.set(1);
    void this.fetchRaydiumPools();
  }

  raydiumPrevPage(): void {
    if (this.raydiumPage() > 1) {
      this.raydiumPage.update(p => p - 1);
      void this.fetchRaydiumPools();
    }
  }

  raydiumNextPage(): void {
    if (!this.raydiumHasNextPage()) return;
    // Synchronous race guard — set fetching=true here, BEFORE awaiting the
    // async fetch, so a second observer firing or rapid scroll burst can't
    // sneak through the guard in the IntersectionObserver callback. Without
    // this, the page counter could advance multiple times before the first
    // fetch resolved, causing pages 2 / 3 / 4 to race and append in arbitrary
    // order (and on a backend that ignores the `page` param, the same rows
    // would land 3× as duplicates).
    if (this.raydiumFetching()) return;
    this.raydiumFetching.set(true);
    this.raydiumPage.update(p => p + 1);
    void this.fetchRaydiumPools();
  }

  /**
   * Footer counter for infinite scroll. Returns the accumulated row count —
   * the per-page `from/to` from the old paginated UI no longer applies once
   * pages append into a single growing list. V3 doesn't expose the total so
   * we just show what we have plus a "more available" hint when applicable.
   */
  get raydiumShowingRange(): { from: number; to: number } {
    return { from: this.raydiumResults.length > 0 ? 1 : 0, to: this.raydiumResults.length };
  }


  /** Format the day APR (already a percentage in the Raydium response). */
  raydiumApr24h(p: RaydiumPool): number {
    return p.day?.apr ?? 0;
  }

  raydiumVolume24h(p: RaydiumPool): number {
    return p.day?.volume ?? 0;
  }

  /**
   * Spawn an add-liquidity action card for the row. Concentrated pools route
   * to `raydium_open_position` (CLMM, single-sided range deposit); standard
   * AMM pools route to `raydium_add_liquidity`. Both action types live in
   * KNOWN_ACTION_TYPES already.
   */
  useRaydiumPool(p: RaydiumPool): void {
    const isCLMM = (p.type ?? '').toLowerCase() === 'concentrated';
    const params: Record<string, string> = {
      poolId: p.id,
      tokenA: p.mintA.address,
      tokenB: p.mintB.address,
      tokenASymbol: p.mintA.symbol,
      tokenBSymbol: p.mintB.symbol,
      tokenADecimals: String(p.mintA.decimals ?? 9),
      tokenBDecimals: String(p.mintB.decimals ?? 9),
    };
    // For CLMM pools, pre-fill a balanced ±20% range around the current
    // pool price so the user has a sane starting range — they can still
    // tighten or widen it before confirming. AMM v4 (Standard) ignores
    // these fields, so we only set them on CLMM.
    if (isCLMM && typeof p.price === 'number' && p.price > 0) {
      // Pre-fill ±20% range and the current price so the action card's CLMM
      // ratio engine has a reference price to compute amountB-per-amountA.
      params['currentPrice'] = String(p.price);
      params['minPrice'] = (p.price * 0.8).toPrecision(6);
      params['maxPrice'] = (p.price * 1.2).toPrecision(6);
    }
    const type = isCLMM ? 'raydium_open_position' : 'raydium_add_liquidity';
    this.useAction.emit({
      type,
      params,
      raw: `[ACTION:${type}] ${JSON.stringify(params)}`,
    });
  }

  /**
   * Plain string equality on `query.type`. Wraps the comparison in a
   * non-discriminating function call so Angular's strict template type
   * checker doesn't narrow `query.type` between sibling `@if` blocks.
   * Without this wrapper, sequential `@if (query.type === 'X')` branches
   * collapse to a single literal type and the subsequent comparison
   * becomes "no overlap" → compile error.
   */
  isQueryType(t: string): boolean {
    return this.query.type === t;
  }

  /** "JupSOL-INF" → ["JupSOL", "INF"]. Defensive against missing dash. */
  dlmmPairTokens(p: DlmmPair): [string, string] {
    const [a, b] = (p.name ?? '').split('-');
    return [a ?? p.token_x?.symbol ?? '?', b ?? p.token_y?.symbol ?? '?'];
  }

  /** 24h volume for a pool — handles either `volume.24h` or `volume["24h"]`. */
  dlmmVolume24h(p: DlmmPair): number {
    return p.volume?.['24h'] ?? 0;
  }

  /** 24h fees for a pool. */
  dlmmFees24h(p: DlmmPair): number {
    return p.fees?.['24h'] ?? 0;
  }

  /**
   * DLMM bin step → human percentage. The API returns bin step in basis
   * points (1 bp = 0.01%), so bin=100 means 1% per price step.
   */
  dlmmBinStepPct(p: DlmmPair): number {
    return (p.pool_config?.bin_step ?? 0) / 100;
  }

  /**
   * Copy a pool address (or any string) to the clipboard and flash a brief
   * "copied" indicator on the row. Falls back silently when the Clipboard
   * API is unavailable (older browsers / insecure contexts).
   */
  async copyPoolAddress(address: string): Promise<void> {
    if (!address) return;
    try {
      await navigator.clipboard.writeText(address);
    } catch {
      // Best-effort fallback for browsers without async clipboard.
      const ta = document.createElement('textarea');
      ta.value = address;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* noop */ }
      document.body.removeChild(ta);
    }
    this.copiedAddress.set(address);
    setTimeout(() => {
      if (this.copiedAddress() === address) this.copiedAddress.set(null);
    }, 1500);
  }

  /**
   * Build a meteora_add_liquidity ParsedAction for a single DLMM pool row
   * and emit it upward. The chat-shell appends it as an action card to the
   * conversation, with the pool / token mints pre-filled — the user only
   * has to enter amounts. Both `pool/poolId` and `tokenA/tokenB/tokenXMint/
   * tokenYMint` aliases are emitted so the form fields and the downstream
   * service-side normalizer both find what they expect.
   */
  useDlmmPool(p: DlmmPair): void {
    // Compute active bin id from the current price so the action card can
    // run ratio math without a second RPC roundtrip:
    //   bin_price(id) = (1 + bin_step_bps/10000) ^ id
    //   activeBinId  = ln(current_price) / ln(1 + bin_step_bps/10000)
    // bin_step is in basis points (1 bp = 0.01%), e.g. 8 → 0.08% per bin.
    const binStep = p.pool_config?.bin_step ?? 0;
    const currentPrice = p.current_price ?? 0;
    const decX = p.token_x?.decimals ?? 9;
    const decY = p.token_y?.decimals ?? 9;
    let activeBinId = 0;
    if (binStep > 0 && currentPrice > 0) {
      // current_price is human-units; the bin formula speaks in raw units,
      // so scale by 10^(decX-decY) before solving for the bin id.
      activeBinId = Math.round(
        Math.log(currentPrice * Math.pow(10, decX - decY)) /
          Math.log(1 + binStep / 10_000),
      );
    }
    const params: Record<string, string> = {
      pool: p.address,
      poolId: p.address,
      tokenA: p.token_x.address,
      tokenB: p.token_y.address,
      tokenXMint: p.token_x.address,
      tokenYMint: p.token_y.address,
      // Symbols help the action card's preview/title render nicely when
      // the registry doesn't know one of the mints yet.
      tokenASymbol: p.token_x.symbol,
      tokenBSymbol: p.token_y.symbol,
      // DLMM math inputs — embedded so the form can compute amountA↔amountB
      // ratio, decide single-sided cases, and show the active price.
      binStep: String(binStep),
      currentPrice: String(currentPrice),
      activeBinId: String(activeBinId),
      tokenADecimals: String(p.token_x.decimals ?? 9),
      tokenBDecimals: String(p.token_y.decimals ?? 9),
      // Reasonable defaults — user can change these in the action card.
      strategy: 'spot',
      binSpread: '15',
    };
    this.useAction.emit({
      type: 'meteora_add_liquidity',
      params,
      raw: `[ACTION:meteora_add_liquidity] ${JSON.stringify(params)}`,
    });
  }

  /**
   * Build a meteora_dammv2_add_liquidity ParsedAction from a DAMM v2 pool
   * row and emit it. Reserves are embedded so the AMM ratio engine in the
   * action card auto-fills the second amount on edit.
   */
  useDammV2Pool(p: DammV2Pool): void {
    const params: Record<string, string> = {
      pool: p.address,
      poolId: p.address,
      tokenA: p.token_x.address,
      tokenB: p.token_y.address,
      tokenXMint: p.token_x.address,
      tokenYMint: p.token_y.address,
      tokenASymbol: p.token_x.symbol,
      tokenBSymbol: p.token_y.symbol,
      tokenADecimals: String(p.token_x.decimals ?? 9),
      tokenBDecimals: String(p.token_y.decimals ?? 9),
      // Reserves drive the AMM ratio engine. Already in human units —
      // the engine prefers `amountRatio` when present, otherwise reserves.
      reserveA: String(p.token_x_amount ?? 0),
      reserveB: String(p.token_y_amount ?? 0),
      amountRatio: String(p.current_price ?? 0),
    };
    this.useAction.emit({
      type: 'meteora_dammv2_add_liquidity',
      params,
      raw: `[ACTION:meteora_dammv2_add_liquidity] ${JSON.stringify(params)}`,
    });
  }

  /**
   * Build a meteora_dammv1_deposit ParsedAction from a DAMM v1 pool row.
   * The legacy API hands reserves as parallel arrays of stringified raw
   * integers — we lift them into reserveA/B and tell the action card the
   * right decimals so the ratio engine can compute deposits.
   */
  useDammV1Pool(p: DammV1Pool): void {
    const [mintA, mintB] = p.pool_token_mints ?? [];
    const [rawA, rawB]   = p.pool_token_amounts ?? [];
    // Decimals aren't on the pool row; look them up via the token registry
    // so the AMM ratio comes out in human units. Fall through to 9 / 6 —
    // the most common pairing on Solana — when the registry doesn't know.
    const decA = this.tokenRegistry.getToken(mintA ?? '')?.decimals ?? 9;
    const decB = this.tokenRegistry.getToken(mintB ?? '')?.decimals ?? 6;
    const params: Record<string, string> = {
      pool: p.pool_address,
      poolId: p.pool_address,
      tokenA: mintA ?? '',
      tokenB: mintB ?? '',
      tokenXMint: mintA ?? '',
      tokenYMint: mintB ?? '',
      tokenADecimals: String(decA),
      tokenBDecimals: String(decB),
      reserveA: String(rawA ?? '0'),
      reserveB: String(rawB ?? '0'),
    };
    this.useAction.emit({
      type: 'meteora_dammv1_deposit',
      params,
      raw: `[ACTION:meteora_dammv1_deposit] ${JSON.stringify(params)}`,
    });
  }

  /**
   * "Showing 1–10 of 155" — current page slice descriptor. Falls back
   * gracefully when total/pages are still defaults.
   */
  get dlmmShowingRange(): { from: number; to: number; total: number } {
    const total = this.dlmmTotal();
    const size = this.DLMM_PAGE_SIZE;
    const from = total === 0 ? 0 : (this.dlmmPage() - 1) * size + 1;
    const to = Math.min(this.dlmmPage() * size, total);
    return { from, to, total };
  }

  /**
   * Smart-truncated page list for the pagination bar. Returns either page
   * numbers or a literal '...' marker for ellipsis gaps. Pattern:
   *   total ≤ 7 → all pages
   *   otherwise → first, neighbours of current (±1), last, with ellipses
   */
  get dlmmPaginationItems(): (number | '...')[] {
    const total = this.dlmmTotalPages();
    const cur = this.dlmmPage();
    if (total <= 7) {
      return Array.from({ length: total }, (_, i) => i + 1);
    }
    const items: (number | '...')[] = [1];
    const start = Math.max(2, cur - 1);
    const end = Math.min(total - 1, cur + 1);
    if (start > 2) items.push('...');
    for (let i = start; i <= end; i++) items.push(i);
    if (end < total - 1) items.push('...');
    items.push(total);
    return items;
  }

  dlmmGoToPage(page: number): void {
    if (page < 1 || page > this.dlmmTotalPages() || page === this.dlmmPage()) return;
    this.dlmmPage.set(page);
    void this.fetchDlmmPairs();
  }

  onClosePerpPosition(market: string, side: string): void {
    // Just start the close flow (opens a perp_close action card to sign). Do
    // NOT mark the position closed here — it is only truly closed once the user
    // signs and the tx lands. `reconcilePerpPositions()` flips it to "closed"
    // when a live fetch confirms it's gone, so the card never claims a position
    // is closed before the on-chain close actually happened.
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
      case 'meteora_dlmm_get_pairs':
        await this.fetchDlmmPairs();
        return;
      case 'meteora_dammv2_get_pools':
        await this.fetchDammV2Pools();
        return;
      case 'meteora_dammv1_get_pools':
        await this.fetchDammV1Pools();
        return;
      case 'raydium_get_pools':
      case 'raydium_search_pools':
        await this.fetchRaydiumPools();
        return;
      case 'kamino_multiply_markets':
        await this.fetchKaminoMultiplyMarkets();
        return;
      case 'analytics':
        await this.fetchAnalytics();
        return;
      case 'yield':
        await this.fetchYields();
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
        // Mock data for queries without a real backend yet (nft_collection, airdrops, tax_report, alerts).
        // `analytics` and `yield` are handled above with live fetches — no mock fallback.
        setTimeout(() => {
          switch (this.query.type) {
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
    if (!tokenParam) return SOL_MINT;
    // Already looks like an address (>20 chars base58)
    if (tokenParam.length > 20 && !tokenParam.includes(' ')) return tokenParam;
    // Reverse lookup from KNOWN_TOKENS
    for (const [mint, info] of Object.entries(KNOWN_TOKENS)) {
      if (info.symbol.toUpperCase() === tokenParam.toUpperCase()) return mint;
    }
    return SOL_MINT;
  }

  /** Ensure portfolio is loaded for the given wallet. Returns summary or null. */
  private async ensurePortfolio(wallet: string) {
    if (!this.portfolioService.isLoaded() || this.portfolioService.summary()?.walletAddress !== wallet) {
      await this.portfolioService.loadPortfolio(wallet);
    }
    return this.portfolioService.summary();
  }

  private async fetchBalance(): Promise<void> {
    const paramWallet = this.query.params['wallet'] as string | undefined;
    const resolvedParam = paramWallet && paramWallet !== 'self' ? paramWallet : null;
    const wallet = resolvedParam || this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see balances');
      this.loading.set(false);
      return;
    }

    const tokenParam = (this.query.params['token'] as string | undefined)?.trim();
    const wantsAll = !tokenParam || tokenParam.toUpperCase() === 'ALL';

    try {
      this.balanceResults = wantsAll
        ? await this.fetchAllBalancesLean(wallet)
        : await this.fetchSingleTokenBalance(wallet, tokenParam!);
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load balances');
      this.loading.set(false);
    }
  }

  /**
   * Fast path: user asked for ONE token's balance.
   * One mint-filtered RPC call + one price lookup. ~500ms cold, ~50ms warm.
   * Bypasses PortfolioService entirely — no DeFi scans, no TX history,
   * no metadata sweep for unrelated tokens.
   */
  private async fetchSingleTokenBalance(
    wallet: string,
    tokenParam: string,
  ): Promise<BalanceResult[]> {
    const mint = this.tryResolveToMint(tokenParam);
    if (!mint) {
      // Symbol not in our registry — return empty so the card shows
      // "You don't hold any <token>" via the empty-state branch.
      return [];
    }

    // SOL has its own RPC method (lamports, not SPL).
    if (mint === SOL_MINT) {
      const [lamports, prices] = await Promise.all([
        this.solanaRpc.getBalance(wallet).catch(() => 0),
        this.birdeye.getTokenPrices([SOL_MINT]).catch(() => new Map()),
      ]);
      const sol = lamports / 1_000_000_000;
      if (sol <= 0) return [];
      const p = prices.get(SOL_MINT);
      return [{
        token: 'Solana',
        symbol: 'SOL',
        mint: SOL_MINT,
        logoUri: SOL_LOGO,
        balance: sol,
        value: sol * (p?.price ?? 0),
        change24h: p?.change24h ?? 0,
      }];
    }

    const [acct, prices] = await Promise.all([
      this.solanaRpc.getTokenBalance(wallet, mint).catch(() => null),
      this.birdeye.getTokenPrices([mint]).catch(() => new Map()),
    ]);
    if (!acct || acct.balance <= 0) return [];

    // Resolve display metadata. For a freshly-launched pump.fun token the sync
    // registry lookup is empty (not in Jupiter yet) — await the on-chain
    // (Helius DAS) metadata fetch instead of falling back to the raw mint
    // address, which is what made the "TOKEN" column show the contract string.
    let meta = this.tokenRegistry.getToken(mint);
    if (!meta?.symbol || meta.name === mint || meta.symbol.endsWith('…')) {
      meta = (await this.tokenRegistry.resolveTokenMeta(mint)) ?? meta;
    }
    const symbol = meta?.symbol ?? KNOWN_TOKENS[mint]?.symbol ?? tokenParam.toUpperCase();
    const name   = meta?.name   ?? symbol;
    const p = prices.get(mint);
    return [{
      token: name,
      symbol,
      mint,
      logoUri: meta?.logoURI ?? null,
      balance: acct.balance,
      value: acct.balance * (p?.price ?? 0),
      change24h: p?.change24h ?? 0,
    }];
  }

  /**
   * Lean full-balance path: SOL + all SPL accounts + batch prices.
   * Skips the heavy stuff `loadPortfolio` does: stake accounts, TX history,
   * and 8 DeFi-protocol scans. Those belong to the Portfolio page, not to
   * a "show me my tokens" chat query.
   */
  private async fetchAllBalancesLean(wallet: string): Promise<BalanceResult[]> {
    const [lamports, rawTokens] = await Promise.all([
      this.solanaRpc.getBalance(wallet).catch(() => 0),
      this.solanaRpc.getTokenAccounts(wallet).catch(() => []),
    ]);

    const allMints = [SOL_MINT, ...rawTokens.map(t => t.mint)];
    const prices = await this.birdeye.getTokenPrices(allMints).catch(() => new Map());

    const sol = lamports / 1_000_000_000;
    const solP = prices.get(SOL_MINT);
    const out: BalanceResult[] = [];
    if (sol > 0) {
      out.push({
        token: 'Solana',
        symbol: 'SOL',
        mint: SOL_MINT,
        logoUri: SOL_LOGO,
        balance: sol,
        value: sol * (solP?.price ?? 0),
        change24h: solP?.change24h ?? 0,
      });
    }

    for (const t of rawTokens) {
      const p = prices.get(t.mint);
      const priceKnown = typeof p?.price === 'number' && p.price > 0;
      const value = priceKnown ? t.balance * p!.price : 0;
      // Drop dust ONLY when we have a price and it's genuinely <1¢. If the price
      // lookup failed or the token isn't on Birdeye, still surface the holding —
      // hiding a real balance because we can't price it has cost users their
      // ability to act on real positions (this is what made jitoSOL invisible
      // when Birdeye briefly omitted it).
      if (priceKnown && value < 0.01) continue;
      const meta = this.tokenRegistry.getToken(t.mint);
      // Kick off a background metadata fetch for any mint we don't yet have a
      // logo for. resolveAsync is idempotent and bumps the registry's version
      // signal when it lands, which `logoFor()` re-reads from the template to
      // swap in the icon without a full re-fetch (keeps WIF / new mints from
      // showing the "W" letter fallback forever).
      if (!meta?.logoURI) this.tokenRegistry.resolveAsync(t.mint);
      // Wrapped SOL is reported by Jupiter's registry under symbol "SOL"
      // (mint `So11…112`), but in a balance list we MUST distinguish it from
      // native SOL — otherwise the user sees two indistinguishable "SOL"
      // rows. Hard-override before the registry/known-tokens fallbacks.
      const isWsol = t.mint === SOL_MINT;
      const symbol = isWsol
        ? 'wSOL'
        : (meta?.symbol ?? KNOWN_TOKENS[t.mint]?.symbol ?? t.mint.slice(0, 4));
      const tokenName = isWsol
        ? 'Wrapped SOL'
        : (meta?.name ?? KNOWN_TOKENS[t.mint]?.symbol ?? t.mint.slice(0, 4) + '…');
      out.push({
        token: tokenName,
        symbol,
        mint: t.mint,
        logoUri: meta?.logoURI ?? null,
        balance: t.balance,
        value,
        change24h: p?.change24h ?? 0,
      });
    }
    out.sort((a, b) => b.value - a.value);
    return out;
  }

  /** Resolve a row's icon URL live from the token registry. The registry's
   *  `version()` signal is read inside this method, so when an async metadata
   *  fetch lands, every template binding that calls `logoFor` re-renders and
   *  the WIF / unknown-token "letter fallback" gets replaced with the real
   *  logo without rerunning the balance pipeline. */
  logoFor(row: BalanceResult): string | null {
    void this.tokenRegistry.version(); // signal dependency for re-render
    const fromRegistry = row.mint ? this.tokenRegistry.getToken(row.mint)?.logoURI : null;
    return fromRegistry ?? row.logoUri ?? null;
  }

  /** Like resolveToMint but returns null on miss instead of falling back to SOL. */
  private tryResolveToMint(tokenParam: string): string | null {
    if (!tokenParam) return null;
    if (tokenParam.length > 20 && !tokenParam.includes(' ')) return tokenParam;
    for (const [mint, info] of Object.entries(KNOWN_TOKENS)) {
      if (info.symbol.toUpperCase() === tokenParam.toUpperCase()) return mint;
    }
    const fromRegistry = this.tokenRegistry.getBySymbol?.(tokenParam);
    if (fromRegistry?.address) return fromRegistry.address;
    return null;
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
      const summary = await this.ensurePortfolio(wallet);
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
      const _rpcHeaders = { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
      const [epochResp, perfResp] = await Promise.all([
        fetch(rpcUrl, {
          method: 'POST',
          headers: _rpcHeaders,
          credentials: 'include',
          body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getEpochInfo' }),
        }),
        fetch(rpcUrl, {
          method: 'POST',
          headers: _rpcHeaders,
          credentials: 'include',
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

  /**
   * Live PnL fetch from Birdeye via gateway proxy. Used by `analytics` query.
   * Requires a connected wallet (or wallet param). On any upstream failure or
   * empty result we render an explicit "data unavailable" state — we never
   * fall back to the old mock numbers, since fake portfolio stats are worse
   * than no stats at all.
   */
  private async fetchAnalytics(): Promise<void> {
    const walletParam = (this.query.params['wallet'] ?? '').trim();
    const wallet = walletParam || this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see portfolio analytics.');
      this.loading.set(false);
      return;
    }
    const rawDuration = (this.query.params['period'] ?? this.query.params['duration'] ?? '30d').toString();
    const allowed = new Set(['all', '90d', '30d', '7d', '24h']);
    const duration = allowed.has(rawDuration) ? rawDuration : '30d';

    try {
      const [summary, details] = await Promise.all([
        firstValueFrom(this.api.get<any>('/market/wallet/pnl-summary', { wallet, duration })).catch(() => null),
        firstValueFrom(this.api.get<any>('/market/wallet/pnl-details', { wallet, duration, limit: '10' })).catch(() => null),
      ]);

      const s = summary?.data ?? summary ?? {};
      const items: any[] = details?.data?.items ?? details?.items ?? details?.data ?? [];

      const realized   = Number(s.realized_pnl   ?? s.realizedPnl   ?? s.realized   ?? 0);
      const unrealized = Number(s.unrealized_pnl ?? s.unrealizedPnl ?? s.unrealized ?? 0);
      const totalPnl   = Number(s.total_pnl ?? s.totalPnl ?? (realized + unrealized));
      const pnlPercent = Number(s.total_pnl_percent ?? s.pnl_percent ?? s.pnlPercent ?? 0);
      const winRate    = Number(s.win_rate ?? s.winRate ?? 0);
      const totalTrades= Number(s.trades_count ?? s.tradesCount ?? s.total_trades ?? s.totalTrades ?? 0);

      if (!summary && items.length === 0) {
        this.error.set('Portfolio analytics unavailable right now — try again in a moment.');
        this.loading.set(false);
        return;
      }

      const topTokens = items
        .map((it) => {
          const symbol = (it.token_symbol ?? it.symbol ?? '').toString();
          const realizedT = Number(it.realized_pnl ?? it.realizedPnl ?? 0);
          const unrealizedT = Number(it.unrealized_pnl ?? it.unrealizedPnl ?? 0);
          const pnl = Number(it.total_pnl ?? it.totalPnl ?? (realizedT + unrealizedT));
          const trades = Number(it.trades_count ?? it.tradesCount ?? it.trade_count ?? 0);
          return { symbol, pnl, trades };
        })
        .filter((t) => t.symbol)
        .sort((a, b) => b.pnl - a.pnl);

      const best  = [...items].sort((a, b) => Number(b.total_pnl ?? b.totalPnl ?? 0) - Number(a.total_pnl ?? a.totalPnl ?? 0))[0];
      const worst = [...items].sort((a, b) => Number(a.total_pnl ?? a.totalPnl ?? 0) - Number(b.total_pnl ?? b.totalPnl ?? 0))[0];
      const bestStr = best
        ? `${Number(best.total_pnl ?? best.totalPnl ?? 0) >= 0 ? '+' : ''}${this.formatUsd(Number(best.total_pnl ?? best.totalPnl ?? 0))} (${best.token_symbol ?? best.symbol ?? ''})`
        : '—';
      const worstStr = worst
        ? `${Number(worst.total_pnl ?? worst.totalPnl ?? 0) >= 0 ? '+' : ''}${this.formatUsd(Number(worst.total_pnl ?? worst.totalPnl ?? 0))} (${worst.token_symbol ?? worst.symbol ?? ''})`
        : '—';

      this.analyticsResult = {
        totalPnl,
        pnlPercent,
        winRate,
        totalTrades,
        bestTrade: bestStr,
        worstTrade: worstStr,
        avgHoldTime: '—',
        topTokens,
      };
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Portfolio analytics unavailable right now — try again in a moment.');
      this.loading.set(false);
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
      const summary = await this.ensurePortfolio(wallet);
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

  /**
   * Fetch LIVE yields from the backend `/yields` aggregator (Jito, Marinade,
   * Jupsol/Kamino LST feeds, or lending markets) — replaces the old hardcoded
   * mock that listed unrelated USDC pools with stale numbers. Category is
   * inferred from the query: SOL/LST tokens → liquid_staking, else lending.
   */
  private async fetchYields(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    const p = this.query.params ?? {};
    const token = String(p['token'] ?? '').toUpperCase();
    const rawCat = String(p['category'] ?? '').toLowerCase();
    const isLst = !token || /SOL$/.test(token) || ['SOL', 'MSOL', 'JITOSOL', 'JUPSOL', 'BSOL', 'INF'].includes(token);
    const category = rawCat === 'lending' || rawCat === 'liquid_staking'
      ? rawCat
      : (isLst ? 'liquid_staking' : 'lending');
    try {
      const resp = await firstValueFrom(this.api.get<any>('/yields', { category, limit: '10' }));
      const rows: any[] = Array.isArray(resp?.yields) ? resp.yields : [];
      this.yieldResults = rows
        .filter((r) => typeof r?.apy === 'number' && r.apy > 0)
        .map((r) => ({
          protocol: r.name ?? r.protocol ?? '—',
          token: (r.mint && this.tokenRegistry.getToken(r.mint)?.symbol) || r.name || r.protocol || '',
          // The aggregator is unit-inconsistent: LST feeds return a FRACTION
          // (0.0649 = 6.49%) while some lending feeds already return a percent
          // (8.3). Real DeFi yields sit in ~3–25%, so a value < 1 is a fraction
          // that needs ×100; a value ≥ 1 is already a percentage.
          apy: r.apy < 1 ? r.apy * 100 : r.apy,
          tvl: typeof r.tvl === 'number' ? r.tvl : 0,
          risk: category === 'liquid_staking' ? 'Low' : (r.risk ?? 'Low'),
        }) as YieldResult);
      if (!this.yieldResults.length) this.error.set('No live yield data available right now.');
    } catch {
      this.error.set('Could not load yields. Please try again.');
    } finally {
      this.loading.set(false);
      this.persistSnapshot();
    }
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

  /** True when at least one token row has a non-zero trade count. Used by
   *  the analytics PnL card to hide the "Trades" column when Birdeye's
   *  response omits per-token trade counts (some durations don't ship them).
   */
  hasTradeCounts(rows: { trades: number }[]): boolean {
    return rows.some((r) => (r?.trades ?? 0) > 0);
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

  /** Compact USD for dense table cells: $2.52M, $18.4K, $47.24. */
  formatCompactUsd(n: number): string {
    if (!Number.isFinite(n)) return '$0';
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (abs >= 10_000) return `$${(n / 1_000).toFixed(1)}K`;
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

  /** Maps a yield row's protocol/token to a colourful protocol logo asset so
   *  each row shows a real brand mark (Marinade/Jito/Jupiter…) like the swap
   *  card's protocol icon — far livelier than a monochrome glyph. Returns null
   *  when no logo matches, so the template falls back to a lettered avatar. */
  yieldLogo(y: YieldResult): string | null {
    const hay = `${y.protocol} ${y.token}`.toLowerCase();
    // Order matters: most-specific token hints first, then protocol names.
    const map: Array<[RegExp, string]> = [
      [/marinade|msol/, 'marinade.webp'],
      [/jupsol|jupiter/, 'jupiter.webp'],
      [/jito/, 'jito.webp'],
      [/blaze|bsol/, 'blazestake.webp'],
      [/sanctum|\binf\b/, 'sanctum.webp'],
      [/lido|steth/, 'lido.webp'],
      [/kamino/, 'kamino.webp'],
      [/marginfi/, 'marginfi.webp'],
      [/solend/, 'solend.svg'],
      [/drift/, 'drift.webp'],
      [/meteora/, 'meteora.webp'],
      [/raydium/, 'raydium.webp'],
      [/orca/, 'orca.webp'],
    ];
    for (const [re, file] of map) {
      if (re.test(hay)) return `assets/icons/protocols/${file}`;
    }
    return null;
  }

  /** First letter for the fallback lettered avatar when no logo matches. */
  yieldInitial(y: YieldResult): string {
    return (y.protocol || y.token || '?').trim().charAt(0).toUpperCase();
  }

  /** Token logo for a perp market (SOL / BTC / ETH), via the token registry. */
  perpMarketLogo(market: string): string | null {
    const sym = (market || '').toUpperCase();
    const candidates = sym === 'BTC' ? ['BTC', 'WBTC'] : sym === 'ETH' ? ['ETH', 'WETH'] : [sym];
    for (const c of candidates) {
      const t = this.tokenRegistry.getBySymbol(c);
      if (t?.logoURI) return t.logoURI;
    }
    return null;
  }
}
