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
import { AccountService } from '@core/services/account.service';
import { EvmPortfolioService } from '@features/portfolio/services/evm-portfolio.service';
import { PortfolioService } from '@features/portfolio/services/portfolio.service';
import { SolanaRpcService } from '@features/portfolio/services/solana-rpc.service';
import { BirdeyeService } from '@features/portfolio/services/birdeye.service';
import { JupiterLendService, LendPosition, BorrowPosition } from '@core/services/market/jupiter-lend.service';
import { JupiterPerpService, PerpPosition } from '@core/services/market/jupiter-perp.service';
import { MeteoraService, DlmmPair, DammV2Pool, DammV1Pool } from '@core/services/market/meteora.service';
import { OrcaService, OrcaPoolRow, OrcaUserPosition } from '@core/services/market/orca.service';
import {
  MagicEdenService, MeCollectionRow, MeTokenRow, MeActivityRow, MeOfferRow,
} from '@core/services/market/magic-eden.service';

/** Pool categories offered by the Orca card's filter chips. */
type OrcaCategory = 'all' | 'stable' | 'lst' | 'rwa' | 'governance' | 'utility' | 'meme';
import { environment } from '../../../../../environments/environment';
import { firstValueFrom, debounceTime, distinctUntilChanged, timeout } from 'rxjs';
import { TPipe } from '@core/i18n';

/** Mock query result types */
interface BalanceResult {
  token: string;
  symbol: string;
  balance: number;
  value: number;
  change24h: number;
  logoUri?: string | null;
  mint?: string;
  /** EVM rows: which chain this balance is on (e.g. "Base"). Absent for Solana. */
  chain?: string;
}

/** One Uniswap liquidity pool row (from the DexScreener-backed listing). */
interface UniswapPool {
  pairAddress: string;
  version: string;          // "v2" | "v3" | "v4"
  baseSymbol: string;
  quoteSymbol: string;
  baseAddress: string;
  quoteAddress: string;
  tvlUsd: number;
  volume24hUsd: number;
  priceUsd: string;
  url: string;
  chain: string;
  baseLogo?: string | null;
  quoteLogo?: string | null;
}

interface UniswapLaunch {
  tokenAddress: string;
  symbol: string;
  name: string;
  launchpad: string;        // "pools.trade" | "Pons" | …
  launchpadId: string;
  priceUsd: string;
  fdvUsd: number;
  liquidityUsd: number;
  volume24hUsd: number;
  priceChange24h?: number | null;
  holders?: number | null;
  graduationProgress?: number | null;  // 0..100
  status?: string;          // "curveLive" | …
  isSpam?: boolean;
  isVerified?: boolean;
  imageEmoji?: string | null;
  imageHue?: number | null;
  quoteSymbol: string;
  quoteAddress: string;
  logo?: string | null;
  url: string;
  createdAt?: string | null;
  chain: string;
}
interface LaunchpadOpt { id: string; label: string; }

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
  /** Null when nothing measured one — which is every row here. */
  apy: number | null;
  /** The protocol's mark and the position's tokens, so the row can be read
   *  the way every other position row in this card is: by its logos. The
   *  aggregate had only text, which is why it rendered as a bare table. */
  protocolLogoUri?: string | null;
  tokens?: Array<{ symbol: string; amount: number; logoUri?: string | null }>;
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
  /** "Concentrated" (CLMM) | "Standard" — drives Deposit routing. NOTE:
   *  "Standard" covers BOTH the newer CPMM and the legacy AMM v4; only
   *  `programId` tells them apart. */
  type: string;
  /** Owning program — the only way to distinguish CPMM from legacy AMM v4. */
  programId?: string;
  mintA: { address: string; symbol: string; decimals: number; logoURI?: string };
  mintB: { address: string; symbol: string; decimals: number; logoURI?: string };
  tvl: number;
  /** Spot price of mintA in mintB units (used to pre-fill CLMM range). */
  price?: number;
  /** Time-windowed metrics; only `day` is rendered in the table. */
  day?: { volume?: number; fee?: number; apr?: number };
  week?: { volume?: number; fee?: number; apr?: number };
}

/** A user's own Raydium position — either a CLMM range NFT or a Standard/CPMM
 *  LP-token holding. Emitted by getRaydiumUserPositionsSdk. */
interface RaydiumUserPosition {
  kind: 'clmm' | 'lp';
  poolId: string;
  pair: string;
  mintA: { address: string; symbol: string; logoURI?: string | null } | null;
  mintB: { address: string; symbol: string; logoURI?: string | null } | null;
  // CLMM
  positionId?: string;
  tickLower?: number;
  tickUpper?: number;
  liquidity?: string;
  /** Token amounts the position holds, derived server-side from liquidity +
   *  tick range. Raw `liquidity` is meaningless to a user — show these. */
  amountA?: number;
  amountB?: number;
  empty?: boolean;
  // LP
  poolType?: string;
  lpMint?: string;
  lpAmount?: number;
}

/** One DLMM pool the wallet has open positions in. The API groups by pool and
 *  lists the position addresses inside it. */
interface DlmmUserPool {
  poolAddress: string;
  tokenX: string; tokenY: string;
  tokenXIcon?: string; tokenYIcon?: string;
  tokenXMint?: string; tokenYMint?: string;
  binStep: number; baseFee: number;
  poolPrice: number;
  balances: number; balancesSol: number;
  totalDeposit: number;
  unclaimedFees: number;
  pnl: number; pnlPctChange: number;
  openPositionCount: number;
  outOfRange: boolean;
  listPositions: string[];
  positionsOutOfRange?: string[];
  /** Per-position detail read on-chain by the backend (SDK), when available. */
  positions?: DlmmPositionDetail[];
  activeBinId?: number;
  tokenXDecimals?: number;
  tokenYDecimals?: number;
  /** DAMM v2: a pool can be created with concentrated bounds rather than
   *  spanning the whole curve, and the price can then sit outside them. */
  minPrice?: number;
  maxPrice?: number;
  concentrated?: boolean;
  priceOutOfRange?: boolean;
  /** DAMM v2: token Y per 1 token X at the pool's current state. */
  depositRatio?: number;
}

/** One DLMM position inside a pool — its own range, balance and fees. */
interface DlmmPositionDetail {
  address: string;
  // Range fields are DLMM-only. A DAMM v2 position is constant-product: no
  // bins, no bounds — the panel simply omits the range strip for those.
  lowerBinId?: number;
  upperBinId?: number;
  lowerPrice?: number;
  upperPrice?: number;
  binCount?: number;
  /** SOL locked in the accounts a close returns, read from the chain. */
  rentSol?: number;
  /** DAMM v2: vested or permanently-locked liquidity can't be withdrawn yet. */
  locked?: boolean;
  permanentlyLocked?: boolean;
  amountX: number;
  amountY: number;
  unclaimedFeeX: number;
  unclaimedFeeY: number;
  inRange: boolean;
}

/** A flattened pool×position pair — one rendered panel. */
interface DlmmPositionRow {
  pool: DlmmUserPool;
  address: string;
  index: number;
  detail: DlmmPositionDetail | null;
  outOfRange: boolean;
}

const SOL_MINT = 'So11111111111111111111111111111111111111112';
const SOL_LOGO = '/assets/coins/sol.svg';

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
  imports: [CommonModule, LucideAngularModule, TPipe],
  templateUrl: './query-card.component.html',
  styleUrl: './query-card.component.scss',
})
export class QueryCardComponent implements OnInit, OnDestroy {
  @Input({ required: true }) query!: ParsedQuery;
  @Input() sessionId: string | null = null;

  /**
   * Set once the assistant message exists in the database.
   *
   * A card starts fetching the moment it renders, which is while the message
   * is still streaming and has no id yet. Snapshots written in that window
   * were dropped on the floor — persistSnapshot returns early without one —
   * and the card then had nothing to restore on a shared page, which is where
   * "This result isn't part of the shared snapshot" came from. Fast queries
   * lost the race and slow ones happened to win it, so the same chat could
   * share one card's result and not another's.
   *
   * Holding the id in a setter lets a snapshot taken too early be written as
   * soon as there is somewhere to write it.
   */
  @Input()
  set messageId(v: string | null) {
    this._messageId = v;
    if (v && this._snapshotPending) {
      this._snapshotPending = false;
      this.persistSnapshot();
    }
  }
  get messageId(): string | null {
    return this._messageId;
  }
  private _messageId: string | null = null;
  private _snapshotPending = false;
  /** DB-persisted snapshot from a previous fetch; if present, skip re-fetching. */
  @Input() snapshot: QuerySnapshot | null = null;
  /**
   * Render from the stored snapshot ONLY — never call the API.
   *
   * Set on the public shared-chat page, where the reader has no wallet and
   * no session: every live fetch here would either 401 or answer about the
   * *visitor's* holdings while sitting under someone else's question. The
   * snapshot is what was actually shown when the answer was given, which is
   * the only honest thing a shared conversation can display.
   */
  @Input() offline = false;

  /**
   * Offline card with nothing stored to render. The question is still part of
   * the conversation, so the card stays and says what it was — it does not
   * pretend to have an answer it cannot fetch.
   */
  readonly unavailableOffline = signal(false);
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
   * Whether this card ended up with anything to show. A message can carry
   * several listings — "my positions" fans out across a protocol's products —
   * and an empty one alongside a populated one is noise: the user asked what
   * they hold, not which products they don't use.
   *
   * The card can't see its siblings, so it reports and the message decides.
   */
  @Output() emptyStateChanged = new EventEmitter<boolean>();

  /** Emit once the fetch has settled, so the parent never hides a card that is
   *  merely still loading. */
  private reportEmptyState(isEmpty: boolean): void {
    this.emptyStateChanged.emit(isEmpty);
  }
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
  private readonly accountService = inject(AccountService);
  private readonly evmPortfolio = inject(EvmPortfolioService);
  private readonly portfolioService = inject(PortfolioService);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly jupiterLend = inject(JupiterLendService);
  private readonly jupiterPerp = inject(JupiterPerpService);
  private readonly meteora = inject(MeteoraService);
  private readonly orca = inject(OrcaService);
  private readonly magicEden = inject(MagicEdenService);

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
  // Token search: filter the pool list to pools containing a given token
  // (server-side search-by-mint). Empty → full list. Lets the "pick another
  // pair" flow find a pool by typing a symbol instead of browsing every pool.
  readonly raydiumSearchInput  = signal('');
  readonly raydiumSearchTokenA = signal<string | null>(null);

  // The user's own Raydium positions (CLMM + Standard/CPMM LP), read straight
  // from chain via the SDK — rendered in the same km-table design as the pool
  // list, with a per-row Withdraw button.
  readonly dlmmUserPools = signal<DlmmUserPool[]>([]);
  readonly dlmmPositionsFetching = signal(false);

  readonly raydiumPositions = signal<RaydiumUserPosition[]>([]);
  readonly raydiumPositionsFetching = signal(false);

  // Which position kind the list shows. A "list my CLMM positions" request must
  // NOT dump the standard/CPMM LP holdings too — seeded from the query type /
  // params on a fresh query, and flippable via the chips.
  readonly raydiumPositionKind = signal<'all' | 'clmm' | 'lp'>('all');

  readonly RAYDIUM_POSITION_KINDS: { value: 'all' | 'clmm' | 'lp'; label: string }[] = [
    { value: 'all',  label: 'All' },
    { value: 'clmm', label: 'CLMM' },
    { value: 'lp',   label: 'LP' },
  ];

  readonly visibleRaydiumPositions = computed(() => {
    const kind = this.raydiumPositionKind();
    const all = this.raydiumPositions();
    return kind === 'all' ? all : all.filter(p => p.kind === kind);
  });

  /** Count per kind — drives the chip badges so a filter that would show an
   *  empty list is visible before it's clicked. */
  raydiumPositionCount(kind: 'all' | 'clmm' | 'lp'): number {
    const all = this.raydiumPositions();
    return kind === 'all' ? all.length : all.filter(p => p.kind === kind).length;
  }

  setRaydiumPositionKind(kind: 'all' | 'clmm' | 'lp'): void {
    this.raydiumPositionKind.set(kind);
  }

  // Classic numbered pagination (1 2 3 4 5). Each page REPLACES the list with
  // its own 10 rows — no infinite-scroll / auto-append. Raydium V3 doesn't
  // expose a total count, so we drive a windowed page bar off the current page
  // + `hasNextPage`: offer a few pages ahead while more data exists, cap once
  // the last page is reached.
  readonly raydiumPageNumbers = computed<number[]>(() => {
    const cur = this.raydiumPage();
    const hasNext = this.raydiumHasNextPage();
    const WINDOW = 5;
    // `hasNext` only proves ONE more page exists — NOT four. Offering cur+4
    // whenever hasNext was true is what surfaced dead page buttons (2 3 4 5 6
    // for a pair with only 2 pages), each fetching an empty page. Offer exactly
    // one page ahead; more numbers appear as the user actually advances.
    const maxOffer = cur + (hasNext ? 1 : 0);
    let start = Math.max(1, cur - Math.floor(WINDOW / 2));
    let end = Math.min(maxOffer, start + WINDOW - 1);
    start = Math.max(1, end - WINDOW + 1);
    const pages: number[] = [];
    for (let p = start; p <= end; p++) pages.push(p);
    return pages;
  });

  raydiumGoToPage(n: number): void {
    if (n < 1 || n === this.raydiumPage() || this.raydiumFetching()) return;
    this.raydiumPage.set(n);
    void this.fetchRaydiumPools();
  }

  priceResult: PriceResult | null = null;
  positionResults: PositionResult[] = [];
  transactionResults: TransactionResult[] = [];
  trendingResults: TrendingResult[] = [];
  networkResult: NetworkResult | null = null;
  yieldResults: YieldResult[] = [];
  analyticsResult: AnalyticsResult | null = null;
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
    // The aggregate is generic, but a protocol-scoped one ("my Marinade
    // positions") is about exactly one protocol and its header should say so.
    // The mark comes from the rows themselves rather than a second lookup
    // table that would drift from the portfolio's.
    if (this.query.type === 'positions' && (this.query.params['protocol'] ?? '').trim()) {
      return this.positionResults.find(p => p.protocolLogoUri)?.protocolLogoUri ?? null;
    }
    switch (this.query.type) {
      case 'limit_orders':
      case 'dca':
      case 'lend_positions':
      case 'perp_positions':
        return 'assets/icons/protocols/jupiter.webp';
      case 'meteora_dlmm_get_pairs':
      case 'meteora_dlmm_get_user_positions':
      case 'meteora_dammv2_get_user_positions':
      case 'meteora_dammv2_get_pools':
      case 'meteora_dammv1_get_pools':
        return 'assets/icons/protocols/meteora.webp';
      case 'raydium_get_pools':
      case 'raydium_search_pools':
      case 'raydium_get_user_positions':
      case 'raydium_get_clmm_positions':
        return 'assets/icons/protocols/raydium.png';
      case 'orca_get_pools':
      case 'orca_search_pools':
      case 'orca_get_user_positions':
        return 'assets/icons/protocols/orca.webp';
      case 'uniswap_pools':
        return 'assets/protocols/uniswap.jpg';
      case 'uniswap_launches':
        return 'assets/protocols/poolstrade.svg';
      case 'kamino_multiply_markets':
        return 'assets/icons/protocols/kamino.svg';
      case 'marinade_exchange_rate':
      case 'marinade_list_tickets':
        return 'assets/icons/protocols/marinade.webp';
      // Every Magic Eden read, by prefix — there are twenty-six of them and
      // listing each one here is how one gets forgotten and renders headerless.
      default:
        if (this.query.type.startsWith('me_')) {
          return 'assets/icons/protocols/magiceden.webp';
        }
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
      this.query.type === 'raydium_search_pools' ||
      this.query.type === 'orca_get_pools' ||
      this.query.type === 'orca_search_pools'
    );
  }

  /** Refetch whichever live-pool listing this card represents. Used by both
   *  the manual refresh button and the 30s auto-poll. */
  refreshLiveData(): void {
    switch (this.query.type) {
      case 'meteora_dlmm_get_pairs':    void this.fetchDlmmPairs();   break;
      case 'meteora_dlmm_get_user_positions':
      case 'meteora_dammv2_get_user_positions': void this.fetchDlmmPositions(); break;
      case 'meteora_dammv2_get_pools':  void this.fetchDammV2Pools(); break;
      case 'meteora_dammv1_get_pools':  void this.fetchDammV1Pools(); break;
      case 'raydium_get_pools':         void this.fetchRaydiumPools(); break;
      case 'raydium_search_pools':      void this.fetchRaydiumPools(); break;
      case 'orca_get_pools':
      case 'orca_search_pools':         void this.fetchOrcaPools(); break;
      case 'kamino_multiply_markets':   void this.fetchKaminoMultiplyMarkets(); break;
      case 'marinade_exchange_rate':    void this.fetchMarinadeRate(); break;
      case 'marinade_list_tickets':     void this.fetchMarinadeTickets(); break;
      case 'my_stake_accounts':         void this.fetchStakePositions(); break;
    }
  }

  ngOnInit(): void {
    // Honor the LLM-requested launch sort ("top by FDV" vs "newest") on first
    // load, instead of always defaulting to newest and forcing a manual toggle.
    if (this.query.type === 'uniswap_launches') {
      const s = (this.query.params?.['sort'] ?? '').toString().toLowerCase();
      this.uniswapLaunchesSort = s === 'top' ? 'top' : s === 'trending' ? 'trending' : 'new';
      const lp = (this.query.params?.['launchpad'] ?? '').toString();
      if (lp) this.uniswapLaunchFilter = lp;
      // A name/symbol search requested by the user ("buy frong") arrives as
      // `query` — seed the search box so the card opens on the matches.
      const q = (this.query.params?.['query'] ?? this.query.params?.['search'] ?? '').toString();
      if (q) this.uniswapLaunchSearch = q;
    }

    // `nft_collection` used to render four invented NFTs — Mad Lads, Claynosaurz,
    // SMBs nobody owned, with invented floor prices — under the user's own
    // wallet heading. It is the same question `me_wallet_tokens` answers for
    // real, so it is now that question. A snapshot taken while it was still
    // mock data has nothing worth restoring, so those cards refetch.
    if (this.query.type === 'nft_collection') {
      const p = this.query.params ?? {};
      this.query = {
        ...this.query,
        type: 'me_wallet_tokens',
        params: { ...p, wallet: p['wallet'] ?? p['walletAddress'] ?? 'self' },
      };
      const restored = this.snapshot?.data as Record<string, unknown> | undefined;
      if (this.snapshot && !restored?.['meShape']) this.snapshot = null;
    }

    // Offline surface (public shared chat): restore what was shown and stop.
    // Every branch below this point either fetches or reconciles against live
    // state, and there is no wallet here to fetch or reconcile for.
    if (this.offline) {
      if (this.snapshot) this.restoreSnapshot(this.snapshot);
      else this.unavailableOffline.set(true);
      return;
    }

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
      // Raydium positions are LIVE state, not a receipt: a snapshot restored
      // from an older chat turn can show closed positions, stale amounts, or
      // (before the amounts existed) a raw liquidity constant. Refetch so the
      // list — and any Withdraw spawned from it — reflects the chain.
      if (this.query.type === 'raydium_get_user_positions' || this.query.type === 'raydium_get_clmm_positions') {
        void this.fetchRaydiumPositions();
      }
      // NOT refetched. A message is a record of what was true when it was
      // answered; re-running its query on every reload rewrites history —
      // close a position, reload, and the earlier answer that listed it now
      // says you never had one. The snapshot is the answer.
      //
      // Acting on a stale row is handled where it can be handled honestly:
      // the action card re-reads the position when it opens, so a Withdraw
      // aimed at something already closed says so instead of failing on
      // chain. That costs nothing until someone actually clicks.
    } else {
      // Seed the Raydium pool-type filter (and sort) from the incoming query
      // params BEFORE the first fetch, so an explicit "CLMM" / "concentrated"
      // request defaults the card to that filter instead of ALL — otherwise
      // "open a CLMM position" listed AMM pools too. Only on a fresh query; a
      // restored snapshot keeps the user's last chosen filter.
      if (this.query.type === 'raydium_search_pools' || this.query.type === 'raydium_get_pools') {
        const pt = (this.query.params?.['poolType'] as string | undefined)?.toLowerCase();
        if (pt === 'concentrated' || pt === 'clmm') this.raydiumPoolType.set('concentrated');
        else if (pt === 'standard' || pt === 'amm') this.raydiumPoolType.set('standard');
        else if (pt === 'all') this.raydiumPoolType.set('all');
        const sf = this.query.params?.['sortField'] as string | undefined;
        if (sf === 'liquidity' || sf === 'volume24h' || sf === 'fee24h' || sf === 'apr24h') {
          this.raydiumSortField.set(sf);
        }
      }
      // Same idea for the POSITIONS list: "list my CLMM positions" must not also
      // dump standard LP holdings. The dedicated query type implies CLMM; a
      // generic positions query can still carry a kind/poolType hint.
      if (this.query.type === 'raydium_get_clmm_positions') {
        this.raydiumPositionKind.set('clmm');
      } else if (this.query.type === 'raydium_get_user_positions') {
        const k = (
          (this.query.params?.['kind'] as string | undefined) ??
          (this.query.params?.['poolType'] as string | undefined) ??
          (this.query.params?.['positionType'] as string | undefined) ??
          ''
        ).toLowerCase();
        if (k === 'clmm' || k === 'concentrated') this.raydiumPositionKind.set('clmm');
        else if (k === 'lp' || k === 'standard' || k === 'amm') this.raydiumPositionKind.set('lp');
      }
      // Open the Orca list on the category the user asked for. Someone who
      // said "RWA pools" and got a card sitting on All Pools has to find the
      // chip themselves to see the answer to their own question.
      if (this.query.type === 'orca_get_pools' || this.query.type === 'orca_search_pools') {
        const c = this.orcaCategoryFromParams();
        if (c) this.orcaCategory.set(c);
      }
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
    if (d['raydiumPositions']) {
      this.raydiumPositions.set(d['raydiumPositions'] as RaydiumUserPosition[]);
    }
    if (d['dlmmUserPools']) {
      this.dlmmUserPools.set(d['dlmmUserPools'] as DlmmUserPool[]);
    }
    if (d['orcaRows']) {
      this.orcaRows.set(d['orcaRows'] as OrcaPoolRow[]);
      this.orcaPage.set((d['orcaPage'] as number | undefined) ?? 1);
      this.orcaSortField.set((d['orcaSortField'] as any) ?? 'tvl');
      this.orcaSortDir.set((d['orcaSortDir'] as any) ?? 'desc');
      this.orcaCategory.set((d['orcaCategory'] as OrcaCategory | undefined) ?? 'all');
      this.orcaCapped.set(!!d['orcaCapped']);
    }
    if (d['meShape']) {
      this.meShape.set(d['meShape'] as any);
      this.mePage.set((d['mePage'] as number | undefined) ?? 1);
      this.meCollections.set((d['meCollections'] as MeCollectionRow[] | undefined) ?? []);
      this.meTokens.set((d['meTokens'] as MeTokenRow[] | undefined) ?? []);
      this.meActivities.set((d['meActivities'] as MeActivityRow[] | undefined) ?? []);
      this.meOffers.set((d['meOffers'] as MeOfferRow[] | undefined) ?? []);
      this.meTraitRows.set((d['meTraitRows'] as any[] | undefined) ?? []);
      this.mePools.set((d['mePools'] as any[] | undefined) ?? []);
      this.meTraders.set((d['meTraders'] as any[] | undefined) ?? []);
      this.meNft.set((d['meNft'] as MeTokenRow | undefined) ?? null);
      this.meTraitStats.set((d['meTraitStats'] as any) ?? {});
      if (d['meTrending']) this.meTrending.set(d['meTrending'] as any);
      if (d['meHolders']) this.meHolders.set(d['meHolders'] as any);
      if (d['meSales'])   this.meSales.set(d['meSales'] as any);
      this.meTab.set((d['meTab'] as any) ?? 'traits');
      this.meChain.set((d['meChain'] as any) ?? {});
      this.meStats.set((d['meStats'] as MeCollectionRow | undefined) ?? null);
    }
    if (d['orcaPositions']) {
      this.orcaPositions.set(d['orcaPositions'] as OrcaUserPosition[]);
    }
    if (d['lendEarnPositions'])   this.lendEarnPositions   = d['lendEarnPositions']   as LendPosition[];
    if (d['lendBorrowPositions']) this.lendBorrowPositions = d['lendBorrowPositions'] as BorrowPosition[];
    if (d['uniswapPoolsResults']) this.uniswapPoolsResults = d['uniswapPoolsResults'] as UniswapPool[];
    if (d['uniswapLaunchesResults']) this.uniswapLaunchesResults = d['uniswapLaunchesResults'] as UniswapLaunch[];
    if (d['uniswapLaunchpads']) this.uniswapLaunchpads = d['uniswapLaunchpads'] as LaunchpadOpt[];
    if (d['uniswapLaunchFilter'] != null) this.uniswapLaunchFilter = d['uniswapLaunchFilter'] as string;
    if (d['uniswapLaunchSearch'] != null) this.uniswapLaunchSearch = d['uniswapLaunchSearch'] as string;
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
    if (this.raydiumPositions().length) {
      d['raydiumPositions'] = this.raydiumPositions();
    }
    if (this.dlmmUserPools().length) {
      d['dlmmUserPools'] = this.dlmmUserPools();
    }
    if (this.orcaRows().length) {
      d['orcaRows'] = this.orcaRows();
      d['orcaPage'] = this.orcaPage();
      d['orcaSortField'] = this.orcaSortField();
      d['orcaSortDir'] = this.orcaSortDir();
      d['orcaCategory'] = this.orcaCategory();
    }
    // Magic Eden. The shape has to be stored too — it is what the template
    // switches on, and a restored card with rows but no shape renders blank.
    if (this.meShape()) {
      d['meShape'] = this.meShape();
      d['mePage'] = this.mePage();
      if (this.meCollections().length) d['meCollections'] = this.meCollections();
      if (this.meTokens().length)      d['meTokens'] = this.meTokens();
      if (this.meActivities().length)  d['meActivities'] = this.meActivities();
      if (this.meOffers().length)      d['meOffers'] = this.meOffers();
      if (this.meTraitRows().length)   d['meTraitRows'] = this.meTraitRows();
      if (this.mePools().length)       d['mePools'] = this.mePools();
      if (this.meTraders().length)     d['meTraders'] = this.meTraders();
      if (this.meNft()) {
        d['meNft'] = this.meNft();
        d['meTraitStats'] = this.meTraitStats();
        d['meTab'] = this.meTab();
        d['meChain'] = this.meChain();
      }
      // Not inside the single-NFT branch. These shapes never load an NFT, so
      // nesting them there meant they were never written — and a reload turned
      // a full ranking into "Nothing here on Magic Eden".
      if (this.meTrending()) d['meTrending'] = this.meTrending();
      if (this.meHolders())  d['meHolders']  = this.meHolders();
      if (this.meSales())    d['meSales']    = this.meSales();
      if (this.meStats())              d['meStats'] = this.meStats();
      d['orcaCapped'] = this.orcaCapped();
    }
    if (this.orcaPositions().length) {
      d['orcaPositions'] = this.orcaPositions();
    }
    if (this.lendEarnPositions.length)   d['lendEarnPositions']   = this.lendEarnPositions;
    if (this.lendBorrowPositions.length) d['lendBorrowPositions'] = this.lendBorrowPositions;
    if (this.uniswapPoolsResults.length) d['uniswapPoolsResults'] = this.uniswapPoolsResults;
    if (this.uniswapLaunchesResults.length) d['uniswapLaunchesResults'] = this.uniswapLaunchesResults;
    if (this.uniswapLaunchpads.length) d['uniswapLaunchpads'] = this.uniswapLaunchpads;
    if (this.uniswapLaunchFilter) d['uniswapLaunchFilter'] = this.uniswapLaunchFilter;
    if (this.uniswapLaunchSearch) d['uniswapLaunchSearch'] = this.uniswapLaunchSearch;
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
    if (!this.sessionId || !this.messageId) {
      // Nothing to write to yet — remember, and the messageId setter will
      // call back the moment there is.
      this._snapshotPending = true;
      return;
    }
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

  /**
   * Token logo for a mint, for the pool-list pair icons. Reads the registry's
   * version signal so the icons appear once the token list finishes loading
   * (these rows can render before it does), and kicks off a lookup for mints
   * the registry hasn't seen yet.
   */
  tokenLogo(mint: string): string | null {
    void this.tokenRegistry.version();
    if (!mint) return null;
    const meta = this.tokenRegistry.getToken(mint);
    if (meta?.logoURI) return meta.logoURI;
    this.tokenRegistry.resolveAsync(mint);
    return null;
  }

  /**
   * Route a third-party token logo through the gateway's image cache.
   *
   * Token logos are hosted by whoever issued the token, and some of those
   * hosts are slow enough to look broken — the GILTS and CETES logos are
   * 621 KB PNGs that take a minute to arrive, so the circle just sits empty.
   * The gateway redirects the first request to the origin and caches the
   * bytes, so nothing is ever slower than going direct and everything after
   * the first view is served from our own server.
   *
   * Local assets and data URIs are left alone — they're already ours.
   */
  logoSrc(url: string | null | undefined, size?: number): string | null {
    if (!url) return null;
    if (!/^https:\/\//i.test(url)) return url;
    const sz = size ? `&size=${size}` : '';
    return `${environment.apiBase}/token-image?url=${encodeURIComponent(url)}${sz}`;
  }

  /**
   * NFT art, cached at tile resolution.
   *
   * The grid draws these at ~150px, which is ~300 on a retina screen. The
   * default cache size is built for 28px token circles, and art served at
   * that size looks like a thumbnail of a thumbnail.
   */
  nftArtSrc(url: string | null | undefined): string | null {
    return this.logoSrc(url, 320);
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
      pageSize: this.requestedPageSize(this.DLMM_PAGE_SIZE),
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
  /**
   * A count the user actually asked for ("the top 5 pools"), if the model
   * passed one through. Listing everything when a number was named ignores
   * half the request — and a 10-row default page is not "5".
   *
   * Clamped to what a card can sensibly show; the pager covers the rest.
   */
  requestedPageSize(fallback: number): number {
    const raw = this.query.params?.['pageSize']
      ?? this.query.params?.['page_size']
      ?? this.query.params?.['limit']
      ?? this.query.params?.['count'];
    const n = parseInt(String(raw ?? ''), 10);
    return Number.isFinite(n) && n > 0 ? Math.min(n, 100) : fallback;
  }

  // ── Orca Whirlpools ───────────────────────────────────────────────────────
  // ── Magic Eden ────────────────────────────────────────────────────────────
  //
  // Twenty-six query types, four shapes. A card picks its renderer from the
  // shape of what came back, not from the endpoint name — otherwise every new
  // Magic Eden endpoint needs its own branch and the ones that already exist
  // drift apart.
  readonly ME_PAGE_SIZE = 10;
  readonly meCollections = signal<MeCollectionRow[]>([]);
  readonly meTokens = signal<MeTokenRow[]>([]);
  readonly meActivities = signal<MeActivityRow[]>([]);
  readonly meOffers = signal<MeOfferRow[]>([]);
  readonly meStats = signal<MeCollectionRow | null>(null);
  readonly meFetching = signal(false);
  readonly mePage = signal(1);
  /** Which renderer this card is using: set once the payload lands. */
  readonly meShape = signal<'collections' | 'tokens' | 'activities' | 'offers' | 'stats' | 'traits' | 'pools' | 'traders' | 'nft' | 'holders' | 'sales' | 'trending' | null>(null);

  /** What is trading most, for a window. */
  readonly meTrending = signal<{
    window: string;
    collections: Array<{
      symbol: string | null; name: string | null; image: string | null; isVerified: boolean;
      volume: number | null; volumeChange: number | null; sales: number | null;
      floorPrice: number | null; floorChange: number | null;
      supply: number | null; listedCount: number | null; ownerCount: number | null;
    }>;
  } | null>(null);

  /** Who holds a collection, and how tightly. */
  readonly meHolders = signal<{
    symbol?: string; name?: string | null; image?: string | null; isVerified?: boolean;
    floorPrice?: number | null; supply?: number | null; reportedOwners?: number | null;
    uniqueHolders: number; held: number; scanned: number; complete: boolean;
    singleItemHolders: number; singleItemShare: number; averageHeld: number;
    top1Share: number; top5Share: number; top10Share: number; top20Share: number;
    topHolders: Array<{ wallet: string; count: number; share: number }>;
  } | null>(null);

  /** Sales per day, oldest first. */
  readonly meSales = signal<{
    symbol?: string; name?: string | null; image?: string | null; isVerified?: boolean;
    floorPrice?: number | null;
    days: number; sales: number; volume: number; average: number;
    series: Array<{ day: number; sales: number; volume: number; average: number; low: number | null; high: number | null }>;
  } | null>(null);
  /** Trait rarity with a floor per trait — the table a buyer actually uses. */
  readonly meTraitRows = signal<Array<{ trait: string; value: string; count: number; floor: number | null }>>([]);
  /** MMM pools: an AMM quoting both sides of a collection. */
  readonly mePools = signal<Array<Record<string, any>>>([]);
  /** A collection's biggest traders. */
  readonly meTraders = signal<Array<{ wallet: string; volume: number | null; lastTradeAt: number | null }>>([]);
  /** The one NFT a detail card is about. */
  readonly meNft = signal<MeTokenRow | null>(null);
  /** Rarity and floor for this NFT's traits, keyed "Trait|Value". */
  readonly meTraitStats = signal<Record<string, { count: number; share: number; floor: number | null }>>({});
  /** Which face of the detail card is showing. Offers came out: on almost
   *  every NFT the tab was an empty state with a button, which is a prompt
   *  dressed as data. Bidding is still one click away in the price bar. */
  readonly meTab = signal<'traits' | 'details'>('traits');
  setMeTab(t: 'traits' | 'details'): void {
    this.meTab.set(t);
    this.persistSnapshot();
  }

  readonly mePagedCollections = computed(() => this.mePageSlice(this.meCollections()));
  readonly mePagedTokens = computed(() => this.mePageSlice(this.meTokens()));
  readonly mePagedActivities = computed(() => this.mePageSlice(this.meActivities()));
  readonly mePagedOffers = computed(() => this.mePageSlice(this.meOffers()));
  readonly mePagedTrending = computed(() => this.mePageSlice(this.meTrending()?.collections ?? []));
  readonly mePagedHolders = computed(() => this.mePageSlice(this.meHolders()?.topHolders ?? []));
  readonly mePagedTraits = computed(() => this.mePageSlice(this.meTraitRows()));
  readonly mePagedPools = computed(() => this.mePageSlice(this.mePools()));
  readonly mePagedTraders = computed(() => this.mePageSlice(this.meTraders()));

  /**
   * How many rows a page holds, for the shape being shown.
   *
   * A holder list is read top-down and compared row against row, so ten is a
   * screen and a page break is a natural place to pause. The wider tables
   * carry more before they feel long.
   */
  meShapePageSize(): number {
    return this.requestedPageSize(this.ME_PAGE_SIZE);
  }

  private mePageSlice<T>(rows: T[]): T[] {
    const size = this.meShapePageSize();
    const start = (this.mePage() - 1) * size;
    return rows.slice(start, start + size);
  }

  readonly meRowCount = computed(() => {
    switch (this.meShape()) {
      case 'collections': return this.meCollections().length;
      case 'tokens':      return this.meTokens().length;
      case 'activities':  return this.meActivities().length;
      case 'offers':      return this.meOffers().length;
      case 'traits':      return this.meTraitRows().length;
      case 'pools':       return this.mePools().length;
      case 'traders':     return this.meTraders().length;
      case 'trending':    return this.meTrending()?.collections.length ?? 0;
      case 'holders':     return this.meHolders()?.topHolders.length ?? 0;
      case 'nft':         return this.meNft() ? 1 : 0;
      default:            return 0;
    }
  });

  readonly meTotalPages = computed(() =>
    Math.max(1, Math.ceil(this.meRowCount() / this.meShapePageSize())));

  readonly mePageNumbers = computed<Array<number | '…'>>(() => {
    const total = this.meTotalPages();
    const cur = this.mePage();
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const out: Array<number | '…'> = [1];
    const from = Math.max(2, cur - 1);
    const to = Math.min(total - 1, cur + 1);
    if (from > 2) out.push('…');
    for (let p = from; p <= to; p++) out.push(p);
    if (to < total - 1) out.push('…');
    out.push(total);
    return out;
  });

  meGoToPage(n: number | '…'): void {
    if (n === '…' || n < 1 || n > this.meTotalPages() || n === this.mePage()) return;
    this.mePage.set(n);
    this.persistSnapshot();
  }
  mePrevPage(): void { this.meGoToPage(this.mePage() - 1); }
  meNextPage(): void { this.meGoToPage(this.mePage() + 1); }

    readonly ORCA_PAGE_SIZE = 15;
  readonly orcaRows = signal<OrcaPoolRow[]>([]);
  readonly orcaFetching = signal(false);
  readonly orcaNextCursor = signal<string | null>(null);
  readonly orcaPrevCursor = signal<string | null>(null);
  readonly orcaSortField = signal<'tvl' | 'volume' | 'fees' | 'yieldovertvl'>('tvl');
  readonly orcaSortDir = signal<'asc' | 'desc'>('desc');
  readonly orcaSearchRaw = signal('');
  readonly orcaPositions = signal<OrcaUserPosition[]>([]);
  readonly orcaPositionsFetching = signal(false);

  /**
   * Category chips, mirroring Orca's own UI and answered by Orca's own API —
   * `categories=` on the pool list, which is how orca.so does it.
   *
   * It has to be the API and not a filter over the loaded rows. The working
   * set is the top 200 pools by TVL, and an RWA pool does a few thousand
   * dollars a day against SOL/USDC's tens of millions: none of them are in
   * the top 200, so filtering locally would answer "RWAs" with an empty
   * table while Orca shows a full one.
   *
   * The API's own word for RWAs is `security`; the chip keeps the word the
   * user uses and the backend translates.
   */
  readonly ORCA_CATEGORIES: ReadonlyArray<{ value: OrcaCategory; label: string }> = [
    { value: 'all',        label: 'All Pools' },
    { value: 'stable',     label: 'Stablecoins' },
    { value: 'rwa',        label: 'RWAs' },
    { value: 'lst',        label: 'LSTs' },
    { value: 'governance', label: 'Governance' },
    { value: 'utility',    label: 'Utility' },
    { value: 'meme',       label: 'Memes' },
  ];
  readonly orcaCategory = signal<OrcaCategory>('all');

  /** The chip's label, for prose ("No RWA pools…"). */
  orcaCategoryLabel(): string {
    return this.ORCA_CATEGORIES.find(c => c.value === this.orcaCategory())?.label ?? '';
  }

  /** The category carried by the incoming query, if it names one we show. */
  private orcaCategoryFromParams(): OrcaCategory | null {
    const raw = String(
      this.query.params?.['category'] ?? this.query.params?.['categories'] ?? '',
    ).toLowerCase().trim();
    if (!raw) return null;
    const k = raw.replace(/[\s-]/g, '_').replace(/s$/, '');
    if (k === 'rwa' || k === 'real_world_asset' || k === 'security') return 'rwa';
    if (k === 'stable' || k === 'stablecoin') return 'stable';
    if (k === 'lst' || k === 'liquid_staking_token' || k === 'liquid_staking') return 'lst';
    if (k === 'governance') return 'governance';
    if (k === 'utility') return 'utility';
    if (k === 'meme' || k === 'memecoin') return 'meme';
    return null;
  }

  setOrcaCategory(c: OrcaCategory): void {
    if (this.orcaCategory() === c) return;
    this.orcaCategory.set(c);
    this.orcaResetPaging();
    void this.fetchOrcaPools();
  }

  /** Rows the pager works over. The category is applied upstream, so this is
   *  just the loaded set — kept as a seam for any local narrowing. */
  readonly orcaFilteredRows = computed(() => this.orcaRows());

  readonly ORCA_SORTS: ReadonlyArray<{ value: 'tvl' | 'volume' | 'fees' | 'yieldovertvl'; label: string }> = [
    { value: 'tvl',           label: 'TVL' },
    { value: 'volume',        label: 'Volume' },
    { value: 'fees',          label: 'Fees' },
    { value: 'yieldovertvl',  label: 'Yield/TVL' },
  ];

  /**
   * Open a position in this pool. Carries the pair, its decimals and the
   * current price so the action card can seed a range and price both sides —
   * a poolId alone would leave the user with an empty form.
   */
  // ── Magic Eden row actions ────────────────────────────────────────────────
  //
  // Every one of these carries the NFT's name and picture into the action so
  // the card that opens shows what is being bought or sold. The backend
  // resolves the seller, token account, auction house and expiry off the live
  // listing, so none of those are passed here and none are asked of the user.

  /** Everything the action card needs to render an NFT rather than a form. */
  private meNftContext(t: MeTokenRow): Record<string, string> {
    return {
      mintAddress: t.mintAddress,
      ...(t.name ? { nftName: t.name } : {}),
      ...(t.image ? { nftImage: t.image } : {}),
      ...(t.collectionName ? { collectionName: t.collectionName } : {}),
      ...(t.collection ? { collectionSymbol: t.collection } : {}),
      ...(t.rarityRank ? { rarityRank: String(t.rarityRank) } : {}),
      // The collection's royalty, carried across rather than looked up again.
      // The action card was waiting on a second request for a number the row
      // already holds, and until it answered the card could not state a total
      // at all.
      ...(typeof t.sellerFeeBasisPoints === 'number'
        ? { royaltyBps: String(t.sellerFeeBasisPoints) } : {}),
    };
  }

  private meEmit(type: string, params: Record<string, string>): void {
    this.useAction.emit({ type, params, raw: `[ACTION:${type}] ${JSON.stringify(params)}` });
  }

  /** Buy a listed NFT. */
  buyMeToken(t: MeTokenRow): void {
    this.meEmit('me_buy', {
      ...this.meNftContext(t),
      ...(t.price ? { price: String(t.price) } : {}),
    });
  }

  /** List one the wallet owns. Seeded at the collection floor when we know it
   *  — an empty price box is a question the user has to go and answer
   *  somewhere else. */
  listMeToken(t: MeTokenRow): void {
    const floor = this.meSol(this.meStats()?.floorPrice);
    this.meEmit('me_list', {
      ...this.meNftContext(t),
      ...(t.price ? { price: String(t.price) } : floor ? { price: String(floor) } : {}),
    });
  }

  /**
   * Move a live listing to a different price.
   *
   * Magic Eden does this in one instruction. Without it, re-pricing meant
   * cancel-then-relist: two transactions, two fees, and a window where the
   * NFT is not for sale at all.
   */
  changeMeListingPrice(t: MeTokenRow): void {
    this.meEmit('me_sell_change_price', {
      ...this.meNftContext(t),
      // Seeded at the current price so the field opens on a real number the
      // user edits, rather than an empty box asking what it already knows.
      ...(t.price ? { newPrice: String(t.price), listPrice: String(t.price) } : {}),
    });
  }

  /** Same, for a bid the user has standing. */
  changeMeOfferPrice(o: MeOfferRow): void {
    if (!o.tokenMint) return;
    this.meEmit('me_buy_change_price', {
      mintAddress: o.tokenMint,
      ...(o.price ? { newPrice: String(o.price), listPrice: String(o.price) } : {}),
      ...(o.name ? { nftName: o.name } : {}),
      ...(o.image ? { nftImage: o.image } : {}),
    });
  }

  cancelMeListing(t: MeTokenRow): void {
    this.meEmit('me_cancel_listing', {
      ...this.meNftContext(t),
      // Which listing is being withdrawn. Named apart from `price` so no
      // builder can mistake it for an amount to spend.
      ...(t.price ? { listPrice: String(t.price) } : {}),
    });
  }

  /**
   * Bid on an NFT.
   *
   * The price is left EMPTY on purpose. It was seeded at 90% of the ask, and
   * 90% is a number I made up — presented in the box it reads as a market
   * figure rather than as the guess it is. What an offer should be is the
   * user's judgement, and the card's job is to put the real reference numbers
   * in front of them: the asking price and the collection floor.
   */
  offerOnMeToken(t: MeTokenRow): void {
    const floor = this.meSol(this.meStats()?.floorPrice);
    this.meEmit('me_make_offer', {
      ...this.meNftContext(t),
      ...(t.price ? { askPrice: String(t.price) } : {}),
      ...(floor ? { floorPrice: String(floor) } : {}),
    });
  }

  /** True when the connected wallet owns this NFT — decides List vs Buy. */
  meOwnsToken(t: MeTokenRow): boolean {
    const me = this.walletService.publicKey()?.toString();
    return !!me && !!t.owner && t.owner === me;
  }

  meIsListed(t: MeTokenRow): boolean {
    return t.listStatus === 'listed' || (t.price ?? 0) > 0;
  }

  /**
   * What a buyer is charged for this listing: the ask, plus the collection's
   * royalty, plus Magic Eden's 2% taker fee.
   *
   * Magic Eden's own grid shows this number, not the ask — a 500 SOL listing
   * reads there as 555. Showing only the ask left the two products
   * contradicting each other over the same NFT, so the card names both: what
   * the seller set, and what the marketplace will quote for it.
   */
  meBuyerPays(t: MeTokenRow): number | null {
    const ask = t.price ?? 0;
    const bps = t.sellerFeeBasisPoints;
    // No royalty figure means no total. Treating a missing one as zero prints
    // a confident number that is short by the largest part of the difference —
    // 510 where the marketplace says 555.
    if (ask <= 0 || typeof bps !== 'number') return null;
    const total = ask * (1 + Math.max(0, bps) / 10_000 + this.ME_TAKER_FEE);
    return total > ask ? total : null;
  }

  /**
   * The headline: the number Magic Eden prints next to this NFT, so the two
   * products don't quote different prices for the same thing. What the seller
   * set survives underneath as "You receive" — it is the second question, not
   * the first, for everyone except the seller.
   */
  meMarketPrice(t: MeTokenRow): number | null {
    return this.meBuyerPays(t) ?? (t.price && t.price > 0 ? t.price : null);
  }

  /** How the headline is built, for whoever is looking at it. */
  mePriceNote(t: MeTokenRow): string | null {
    const ask = t.price ?? 0;
    if (ask <= 0 || this.meBuyerPays(t) === null) return null;
    const fmt = (n: number) => `${Number(n.toFixed(3))} SOL`;
    if (this.meOwnsToken(t)) return `You receive ${fmt(ask)}`;
    const royaltyPct = Math.max(0, t.sellerFeeBasisPoints ?? 0) / 100;
    return `${fmt(ask)} ask + ${Number(royaltyPct.toFixed(2))}% royalty + 2% fee`;
  }

  /** Magic Eden's taker fee on Solana. */
  private readonly ME_TAKER_FEE = 0.02;

  /** Take a bid on an NFT you own, or withdraw one you made. */
  acceptMeOffer(o: MeOfferRow): void {
    if (!o.tokenMint) return;
    this.meEmit('me_accept_offer', {
      mintAddress: o.tokenMint,
      ...(o.buyer ? { buyer: o.buyer } : {}),
      ...(o.price ? { price: String(o.price) } : {}),
      ...(o.name ? { nftName: o.name } : {}),
      ...(o.image ? { nftImage: o.image } : {}),
    });
  }

  cancelMeOffer(o: MeOfferRow): void {
    if (!o.tokenMint) return;
    this.meEmit('me_cancel_offer', {
      mintAddress: o.tokenMint,
      ...(o.price ? { price: String(o.price) } : {}),
      ...(o.name ? { nftName: o.name } : {}),
      ...(o.image ? { nftImage: o.image } : {}),
    });
  }

  /** Open a collection's listings from a collection row. */
  browseMeCollection(c: MeCollectionRow): void {
    this.useAction.emit({
      type: 'me_collection_listings',
      params: { symbol: c.symbol, collectionName: c.name },
      raw: `[QUERY:me_collection_listings] symbol=${c.symbol}`,
    });
  }


  useOrcaPool(row: OrcaPoolRow): void {
    const params: Record<string, string> = {
      whirlpool: row.address,
      poolId: row.address,
      // Picked from a ranked list the user could see — the action card
      // re-checks only pools the model named on its own.
      poolChosenBy: 'user',
      pair: `${row.tokenA.symbol}/${row.tokenB.symbol}`,
      tokenA: row.tokenA.address,
      tokenB: row.tokenB.address,
      tokenASymbol: row.tokenA.symbol,
      tokenBSymbol: row.tokenB.symbol,
      tokenADecimals: String(row.tokenA.decimals),
      tokenBDecimals: String(row.tokenB.decimals),
      ...(row.tokenA.imageUrl ? { tokenALogo: row.tokenA.imageUrl } : {}),
      ...(row.tokenB.imageUrl ? { tokenBLogo: row.tokenB.imageUrl } : {}),
      currentPrice: String(row.price ?? ''),
      tickSpacing: String(row.tickSpacing ?? ''),
      feeRate: String(row.feeRate ?? ''),
    };
    this.useAction.emit({ type: 'orca_open_position', params, raw: `[ACTION:orca_open_position] ${JSON.stringify(params)}` });
  }

  /** Params every position-scoped Orca action needs, plus the display context
   *  that keeps the action card from rendering an anonymous form. */
  private orcaActionParams(pos: OrcaUserPosition): Record<string, string> {
    const num = (v: unknown) => (v === undefined || v === null ? undefined : String(v));
    return {
      positionMint: pos.positionMint,
      position: pos.positionAddress,
      positionAddress: pos.positionAddress,
      whirlpool: pos.whirlpool,
      poolId: pos.whirlpool,
      positionMinPrice: String(pos.priceLower),
      positionMaxPrice: String(pos.priceUpper),
      // The CLMM ratio engine reads minPrice/maxPrice — the band is what
      // decides how a deposit splits. Passing only the position-prefixed copy
      // left the engine with nothing, so typing one amount never filled the
      // other. The panel still shows the band as fixed; this only feeds the
      // maths.
      minPrice: String(pos.priceLower),
      maxPrice: String(pos.priceUpper),
      liquidity: pos.liquidity,
      // Everything the panel needs to render itself. The row already resolved
      // all of it; without passing it along the action opened as "??/??" with
      // zeros, which is the row's own data thrown away one click later.
      ...(pos.tokenASymbol ? { tokenASymbol: pos.tokenASymbol } : {}),
      ...(pos.tokenBSymbol ? { tokenBSymbol: pos.tokenBSymbol } : {}),
      ...(pos.tokenAMint ? { tokenA: pos.tokenAMint } : {}),
      ...(pos.tokenBMint ? { tokenB: pos.tokenBMint } : {}),
      ...(pos.tokenAMint && this.tokenLogo(pos.tokenAMint)
        ? { tokenALogo: this.tokenLogo(pos.tokenAMint)! } : {}),
      ...(pos.tokenBMint && this.tokenLogo(pos.tokenBMint)
        ? { tokenBLogo: this.tokenLogo(pos.tokenBMint)! } : {}),
      ...(num(pos.tokenADecimals) ? { tokenADecimals: num(pos.tokenADecimals)! } : {}),
      ...(num(pos.tokenBDecimals) ? { tokenBDecimals: num(pos.tokenBDecimals)! } : {}),
      ...(num(pos.amountA) ? { positionAmountA: num(pos.amountA)! } : {}),
      ...(num(pos.amountB) ? { positionAmountB: num(pos.amountB)! } : {}),
      ...(num(pos.feeOwedAUi) ? { positionFeeA: num(pos.feeOwedAUi)! } : {}),
      ...(num(pos.feeOwedBUi) ? { positionFeeB: num(pos.feeOwedBUi)! } : {}),
      ...(num(pos.currentPrice) ? { currentPrice: num(pos.currentPrice)! } : {}),
      ...(num((pos as { rentSol?: number }).rentSol)
        ? { positionRentSol: num((pos as { rentSol?: number }).rentSol)! } : {}),
      positionOutOfRange: pos.inRange === false ? 'true' : 'false',
      // Whirlpools have a tick range, not bins — say so, so the panel omits
      // the bin count rather than printing "0 bins".
      positionMinPriceIsRange: 'true',
    };
  }

  /** A collect with nothing owed costs a fee and moves nothing. */
  orcaHasFees(pos: OrcaUserPosition): boolean {
    const a = Number(pos.feeOwedAUi ?? pos.feeOwedA ?? 0);
    const b = Number(pos.feeOwedBUi ?? pos.feeOwedB ?? 0);
    return (Number.isFinite(a) && a > 0) || (Number.isFinite(b) && b > 0);
  }

  collectOrcaFees(pos: OrcaUserPosition): void {
    const params = this.orcaActionParams(pos);
    this.useAction.emit({ type: 'orca_collect_fees', params, raw: `[ACTION:orca_collect_fees] ${JSON.stringify(params)}` });
  }
  increaseOrcaPosition(pos: OrcaUserPosition): void {
    const params = this.orcaActionParams(pos);
    this.useAction.emit({ type: 'orca_increase_position', params, raw: `[ACTION:orca_increase_position] ${JSON.stringify(params)}` });
  }
  decreaseOrcaPosition(pos: OrcaUserPosition): void {
    const params = { ...this.orcaActionParams(pos), bpsToRemove: '10000' };
    this.useAction.emit({ type: 'orca_decrease_position', params, raw: `[ACTION:orca_decrease_position] ${JSON.stringify(params)}` });
  }
  closeOrcaPosition(pos: OrcaUserPosition): void {
    const params = this.orcaActionParams(pos);
    this.useAction.emit({ type: 'orca_close_position', params, raw: `[ACTION:orca_close_position] ${JSON.stringify(params)}` });
  }

  onOrcaSearch(e: Event): void {
    this.orcaSearchRaw.set((e.target as HTMLInputElement).value);
    this.orcaResetPaging();
    void this.fetchOrcaPools();
  }

  setOrcaSort(field: 'tvl' | 'volume' | 'fees' | 'yieldovertvl'): void {
    if (this.orcaSortField() === field) {
      this.orcaSortDir.set(this.orcaSortDir() === 'desc' ? 'asc' : 'desc');
    } else {
      this.orcaSortField.set(field);
      this.orcaSortDir.set('desc');
    }
    this.orcaResetPaging();
    void this.fetchOrcaPools();
  }

  /**
   * A real pager needs a total, and Orca's API never returns one — only a
   * cursor to the next page. Walking cursors is why the bar used to grow a
   * number at a time, which is not a pager at all.
   *
   * So fetch a bounded working set for the chosen sort and page it here. The
   * count is then exact: fixed page numbers, instant switching, and no
   * request per page. The set is the top ORCA_WORKING_SET pools by that sort,
   * which is what a "browse the pools" card is for — anything outside it is
   * reached by searching, not by paging to page 400.
   */
  readonly ORCA_WORKING_SET = 200;
  readonly orcaPage = signal(1);

  /**
   * Floor for the browse list.
   *
   * Orca has thousands of pools and most of them are dust — 540 pools hold an
   * RWA, 43 of them hold more than $1,000. Without a floor every category
   * filled the 200-row working set and the pager read "14 pages" for all of
   * them, which is what made the numbers look broken: they were all reporting
   * our own cap, not the data.
   *
   * With it the counts are real and different (RWAs 43, LSTs 68, memes 50),
   * and every row is a pool someone could actually add liquidity to. Nothing
   * is hidden silently — the count line names the floor, and SEARCH is not
   * floored, so a specific small pool is still findable by name.
   */
  readonly ORCA_MIN_TVL = 1_000;
  readonly ORCA_MIN_TVL_LABEL = '$1K';

  /** True when Orca reported more pools past the working set, i.e. the count
   *  shown is our cap rather than the real total. */
  readonly orcaCapped = signal(false);

  readonly orcaTotalPages = computed(() =>
    Math.max(1, Math.ceil(this.orcaFilteredRows().length / this.requestedPageSize(this.ORCA_PAGE_SIZE))));

  readonly orcaPagedRows = computed(() => {
    const size = this.requestedPageSize(this.ORCA_PAGE_SIZE);
    const start = (this.orcaPage() - 1) * size;
    return this.orcaFilteredRows().slice(start, start + size);
  });

  /** Windowed page numbers with ellipsis, against a KNOWN total. */
  readonly orcaPageNumbers = computed<Array<number | '…'>>(() => {
    const total = this.orcaTotalPages();
    const cur = this.orcaPage();
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const out: Array<number | '…'> = [1];
    const from = Math.max(2, cur - 1);
    const to = Math.min(total - 1, cur + 1);
    if (from > 2) out.push('…');
    for (let p = from; p <= to; p++) out.push(p);
    if (to < total - 1) out.push('…');
    out.push(total);
    return out;
  });

  orcaGoToPage(n: number | '…'): void {
    if (n === '…' || n < 1 || n > this.orcaTotalPages() || n === this.orcaPage()) return;
    this.orcaPage.set(n);
    this.persistSnapshot();
  }

  private orcaResetPaging(): void {
    this.orcaPage.set(1);
    this.orcaRows.set([]);
  }

  orcaPrevPage(): void { this.orcaGoToPage(this.orcaPage() - 1); }
  orcaNextPage(): void { this.orcaGoToPage(this.orcaPage() + 1); }

  /**
   * Fetch any Magic Eden read and pick a renderer from what came back.
   *
   * Shape-first rather than name-first: `me_collection_listings` and
   * `me_wallet_tokens` are different endpoints returning the same thing — a
   * list of NFTs with prices — and should look identical to the user.
   */
  private async fetchMagicEden(): Promise<void> {
    this.meFetching.set(true);
    this.error.set(null);

    const p = this.query.params ?? {};
    const wallet = this.walletService.publicKey()?.toString();
    const params: Record<string, unknown> = {
      ...(p['symbol'] || p['collection'] || p['collectionSymbol']
        ? { symbol: p['symbol'] ?? p['collection'] ?? p['collectionSymbol'] } : {}),
      ...(p['mintAddress'] || p['mint'] || p['tokenMint']
        ? { mintAddress: p['mintAddress'] ?? p['mint'] ?? p['tokenMint'] } : {}),
      // The NFT's number within its collection. Without this a question like
      // "Mad Lads 8051" arrived at the backend as a collection and nothing
      // else, and came back as an error the card reported as unreachable.
      ...(p['number'] || p['tokenId'] || p['id']
        ? { number: String(p['number'] ?? p['tokenId'] ?? p['id']).replace(/^#/, '') } : {}),
      // The time window the question carried. The card refetches for itself,
      // so anything not forwarded here is silently replaced by a default —
      // which is how "rank them by weekly volume" came back with a day's
      // numbers and disagreed with Magic Eden by a factor of seven.
      ...(p['window'] || p['days'] || p['period'] || p['timeWindow'] || p['range']
        ? { window: p['window'] ?? p['days'] ?? p['period'] ?? p['timeWindow'] ?? p['range'] } : {}),
      ...(p['sort'] || p['sortBy'] ? { sort: p['sort'] ?? p['sortBy'] } : {}),
      limit: this.requestedPageSize(this.ME_PAGE_SIZE) * 5,
    };
    // Anything wallet-scoped means the CONNECTED wallet unless the user named
    // another one — "my NFTs" must never resolve to whoever was asked about
    // last.
    if (/wallet|owner/.test(this.query.type)) {
      const named = p['wallet'] ?? p['address'] ?? p['owner'];
      const w = named && named !== 'self' ? named : wallet;
      if (!w) {
        this.error.set('Connect your wallet to see this');
        this.meFetching.set(false);
        this.loading.set(false);
        return;
      }
      params['wallet'] = w;
    }

    const data = await this.magicEden.read(this.query.type, params);
    if (data === null) {
      // The backend says WHY — "#8051 is not currently listed", "there is no
      // collection by that name". Reporting all of it as unreachable throws
      // away the only part the user can act on.
      this.error.set(this.magicEden.lastError() ?? 'Could not reach Magic Eden');
      this.meFetching.set(false);
      this.loading.set(false);
      return;
    }
    this.applyMagicEdenPayload(data);
    this.mePage.set(1);
    const shapeCarriesRows = !['stats', 'sales'].includes(this.meShape() ?? '');
    this.reportEmptyState(this.meRowCount() === 0 && shapeCarriesRows);
    this.persistSnapshot();
    this.meFetching.set(false);
    this.loading.set(false);
  }

  /**
   * How many rows the question asked for, if it named a number.
   *
   * "the five most active wallets" is a request for five rows, not for a
   * hundred paged five at a time. The model passes the number through as
   * `limit`; anything absurd is ignored rather than trusted.
   */
  meRequestedCount(): number | null {
    const raw = this.query.params?.['limit']
      ?? this.query.params?.['count']
      ?? this.query.params?.['top']
      ?? this.query.params?.['n'];
    const n = parseInt(String(raw ?? ''), 10);
    return Number.isFinite(n) && n > 0 && n <= 200 ? n : null;
  }

  /** Trim a row list to the count the question named. */
  private meCap<T>(rows: T[]): T[] {
    const n = this.meRequestedCount();
    return n === null ? rows : rows.slice(0, n);
  }

  /** Decide which of the four shapes this payload is, and file it. */
  private applyMagicEdenPayload(data: unknown): void {
    const t = this.query.type;

    if (t === 'me_trending_collections') {
      const tr = data as { window: string; collections: any[] };
      this.meTrending.set({ ...tr, collections: this.meCap(tr.collections ?? []) });
      this.meShape.set('trending');
      return;
    }
    if (t === 'me_collection_holder_stats') {
      const hs = data as any;
      this.meHolders.set({ ...hs, topHolders: this.meCap(hs.topHolders ?? []) });
      this.meShape.set('holders');
      return;
    }
    if (t === 'me_collection_sales_history') {
      this.meSales.set(data as any);
      this.meShape.set('sales');
      return;
    }

    // A single collection's stats is one record, not a list.
    if (t === 'me_collection_stats' || t === 'me_collection_info') {
      const row = (Array.isArray(data) ? data[0] : data) as MeCollectionRow | null;
      if (row && (row.symbol || row.name)) {
        this.meStats.set(row);
        this.meShape.set('stats');
        return;
      }
    }

    // One NFT, everything about it. The backend merges the token with its
    // offers and its activity, because "tell me about this NFT" is one
    // question and answering it with three cards is three headers.
    if (t === 'me_token' || t === 'me_nft_info') {
      const d = data as { token?: MeTokenRow; offers?: MeOfferRow[]; activities?: MeActivityRow[] } | null;
      const token = d?.token ?? ((Array.isArray(data) ? data[0] : data) as MeTokenRow | null);
      if (token?.mintAddress) {
        this.meNft.set(token);
        this.meOffers.set(d?.offers ?? []);
        this.meActivities.set(d?.activities ?? []);
        this.meTraitStats.set((data as any)?.traitStats ?? {});
        this.meChain.set((data as any)?.chain ?? {});
        this.meTab.set('traits');
        this.meShape.set('nft');
        return;
      }
    }

    if (/activit/.test(t)) {
      this.meActivities.set(this.meCap(MagicEdenService.rowsFrom<MeActivityRow>(data, 'activities')));
      this.meShape.set('activities');
      return;
    }
    if (/offer/.test(t)) {
      const offers = this.meCap(MagicEdenService.rowsFrom<MeOfferRow>(data, 'offers'));
      this.meOffers.set(offers);
      void this.nameUnresolvedOffers(offers);
      this.meShape.set('offers');
      return;
    }
    if (/listing/.test(t)) {
      // Listings ARE tokens with a price on them, and the user wants to buy
      // from this list — so they render as the NFT grid, not as a table of
      // addresses.
      const rows = MagicEdenService.rowsFrom<Record<string, unknown>>(data, 'listings');
      const tokens = rows.map(r => this.meListingToToken(r));
      this.meTokens.set(this.meCap(tokens));
      this.meShape.set('tokens');
      void this.loadTraitStatsForRows(tokens);
      return;
    }
    if (t === 'me_mmm_pools') {
      // A pool is not an NFT and must not render as one: what matters is the
      // price it quotes, which way it is facing, and what it has to trade
      // with.
      this.mePools.set(MagicEdenService.rowsFrom<Record<string, any>>(data, 'results', 'pools'));
      this.meShape.set('pools');
      return;
    }
    if (t === 'me_collection_leaderboard') {
      // Wallets, not collections. Volume is in lamports here.
      const rows = MagicEdenService.rowsFrom<Record<string, any>>(data, 'results');
      this.meTraders.set(this.meCap(rows.map(r => ({
        wallet: String(r['wallet'] ?? ''),
        volume: MagicEdenService.solFromMaybeLamports(r['totalVolume'] ?? null),
        lastTradeAt: (r['lastTradeAt'] as number | undefined) ?? null,
      })).filter(r => r.wallet)));
      this.meShape.set('traders');
      return;
    }
    if (t === 'me_collection_attributes') {
      // `{results:{availableAttributes:[{attribute:{trait_type,value},count,floor}]}}`
      // — the floor is per trait and in lamports, which is the whole reason
      // to show this rather than a bare trait list.
      // The read now wraps the original response so it can carry normalised
      // trait stats alongside it: `{attributes: <original>, traitStats}`.
      // Reading only the old shape emptied this card the moment that landed.
      const root = data as Record<string, any> | null;
      const res = (root?.['attributes']?.['results'] ?? root?.['results']) ?? {};
      const avail = (res['availableAttributes'] ?? []) as Array<Record<string, any>>;
      this.meTraitRows.set(this.meCap(avail.map(a => ({
        trait: String(a['attribute']?.['trait_type'] ?? ''),
        value: String(a['attribute']?.['value'] ?? ''),
        count: Number(a['count'] ?? 0),
        floor: MagicEdenService.solFromMaybeLamports(a['floor'] ?? null),
      })).filter(r => r.trait).sort((x, y) => (y.floor ?? 0) - (x.floor ?? 0))));
      this.meShape.set('traits');
      return;
    }
    if (/collection/.test(t) && !/nfts|token/.test(t)) {
      this.meCollections.set(this.meCap(MagicEdenService.collectionsFrom(data)));
      this.meShape.set('collections');
      return;
    }

    // Everything else is a list of NFTs: a wallet's tokens, a collection's
    // tokens, MMM pool inventory.
    const rows = MagicEdenService.rowsFrom<MeTokenRow>(data, 'tokens', 'nfts', 'pools');
    this.meTokens.set(this.meCap(rows));
    this.meShape.set('tokens');
    void this.loadTraitStatsForRows(rows);
  }

  /** A listing row carries the NFT under `token` on some endpoints and inline
   *  on others. Flatten so the grid only ever reads one shape. */
  private meListingToToken(r: Record<string, unknown>): MeTokenRow {
    const inner = (r['token'] ?? {}) as Record<string, unknown>;
    const pick = <T,>(k: string): T | undefined => (r[k] ?? inner[k]) as T | undefined;
    const extra = (r['extra'] ?? {}) as Record<string, unknown>;
    return {
      mintAddress: (pick<string>('mintAddress') ?? pick<string>('tokenMint') ?? '') as string,
      name: (pick<string>('name') ?? '') as string,
      image: (pick<string>('image') ?? (extra['img'] as string | undefined) ?? null),
      collection: pick<string>('collection') ?? null,
      collectionName: pick<string>('collectionName') ?? null,
      owner: pick<string>('owner') ?? (r['seller'] as string | undefined) ?? null,
      price: (r['price'] as number | undefined) ?? null,
      listStatus: 'listed',
      tokenAddress: (r['tokenAddress'] as string | undefined) ?? null,
      rarityRank: this.meRarityRank(r),
    };
  }

  /** Rarity arrives under three competing providers; take whichever is there. */
  private meRarityRank(r: Record<string, unknown>): number | null {
    const rarity = (r['rarity'] ?? {}) as Record<string, Record<string, unknown>>;
    for (const provider of ['moonrank', 'howrare', 'meInstant']) {
      const rank = rarity[provider]?.['rank'];
      if (typeof rank === 'number') return rank;
    }
    return null;
  }

  /** A pool's quote, in SOL. Magic Eden stores it in lamports. */
  mePoolSpot(pool: Record<string, any>): number | null {
    return MagicEdenService.solFromMaybeLamports(pool['spotPrice']);
  }

  /** What the pool has to trade with: NFTs on the sell side, SOL on the buy
   *  side. A pool with neither quotes nothing, which is worth seeing. */
  mePoolInventory(pool: Record<string, any>): string {
    const nfts = Number(pool['sellsideAssetAmount'] ?? 0);
    const sol = MagicEdenService.solFromMaybeLamports(pool['buysidePaymentAmount']) ?? 0;
    const parts: string[] = [];
    if (nfts) parts.push(`${nfts} NFT${nfts === 1 ? '' : 's'}`);
    if (sol) parts.push(`${sol.toFixed(2)} SOL`);
    return parts.length ? parts.join(' · ') : 'Empty';
  }

  /** "exp 15%" / "linear 0.1 SOL" — the curve only means something with its
   *  step, and the step's unit depends on the curve. */
  mePoolCurve(pool: Record<string, any>): string {
    const type = String(pool['curveType'] ?? '');
    const delta = Number(pool['curveDelta'] ?? 0);
    if (!delta) return type || '—';
    return type === 'exp' ? `exp ${(delta / 100).toFixed(2)}%` : `linear ${(delta / 1e9).toFixed(3)} SOL`;
  }

  /** A two-sided pool both buys and sells; a one-sided one only does one. */
  mePoolCanBuyFrom(pool: Record<string, any>): boolean {
    return Number(pool['sellsideAssetAmount'] ?? 0) > 0;
  }
  mePoolCanSellTo(pool: Record<string, any>): boolean {
    return (MagicEdenService.solFromMaybeLamports(pool['buysidePaymentAmount']) ?? 0) > 0;
  }

  /** Buy an NFT out of the pool. The pool decides which one, so the mint
   *  comes from its own inventory. */
  buyFromMePool(pool: Record<string, any>): void {
    const mint = (pool['mints'] as string[] | undefined)?.[0];
    if (!mint) return;
    const spot = this.mePoolSpot(pool);
    this.meEmit('me_mmm_sol_fulfill_sell', {
      pool: String(pool['poolKey'] ?? ''),
      assetMint: mint,
      ...(spot ? { maxPaymentAmount: String(spot) } : {}),
      ...(pool['collectionName'] ? { collectionName: String(pool['collectionName']) } : {}),
    });
  }

  /** Sell an NFT into the pool's bid. Which NFT is the user's to choose, so
   *  the action card asks for it — it is the one thing the pool cannot say. */
  sellToMePool(pool: Record<string, any>): void {
    const spot = this.mePoolSpot(pool);
    this.meEmit('me_mmm_sol_fulfill_buy', {
      pool: String(pool['poolKey'] ?? ''),
      ...(spot ? { minPaymentAmount: String(spot) } : {}),
      ...(pool['collectionSymbol'] ? { collectionSymbol: String(pool['collectionSymbol']) } : {}),
    });
  }

  /** A collection's official links, when it published any. */
  meCollectionLinks(c: MeCollectionRow): Array<{ label: string; href: string; icon: string }> {
    const out: Array<{ label: string; href: string; icon: string }> = [];
    const add = (label: string, url: string | null | undefined, icon: string) => {
      if (url && /^https?:\/\//i.test(url)) out.push({ label, href: url, icon });
    };
    add('Website', c.website, 'globe');
    add('X', c.twitter, 'twitter');
    add('Discord', c.discord, 'message-circle');
    if (c.symbol) {
      out.push({ label: 'Magic Eden', href: `https://magiceden.io/marketplace/${c.symbol}`, icon: 'external-link' });
    }
    return out;
  }

  /** True once a bid's expiry has passed. Magic Eden keeps returning them. */
  meOfferExpired(o: MeOfferRow): boolean {
    const e = o.expiry ?? -1;
    return e > 0 && e < Math.floor(Date.now() / 1000);
  }

  /** Bids that could still be accepted, best first. */
  meLiveOffers(): MeOfferRow[] {
    return this.meOffers()
      .filter(o => !this.meOfferExpired(o))
      .sort((a, b) => (b.price ?? 0) - (a.price ?? 0));
  }

  /**
   * The best bid anyone could actually take.
   *
   * This counted expired ones, so an NFT with a single dead 158 SOL bid
   * advertised a top offer of 158 SOL. Nobody can accept it and the seller
   * cannot get it; showing it is worse than showing nothing.
   */
  meTopOffer(): number | null {
    const prices = this.meLiveOffers().map(o => o.price ?? 0).filter(p => p > 0);
    return prices.length ? Math.max(...prices) : null;
  }

  /** On-chain facts from the detail payload. */
  readonly meChain = signal<{
    collectionMint?: string; standard?: string; frozen?: boolean;
    compressed?: boolean; creatorsVerified?: boolean; mutable?: boolean; burnt?: boolean;
  }>({});

  /**
   * What can be checked about an NFT before buying it.
   *
   * Magic Eden lists whatever anyone mints, and copying a famous collection's
   * art and name costs nothing. What cannot be copied is a signature: a
   * creator counts as verified only if that key signed the metadata, and a
   * collection membership is only reported once the collection's own
   * authority has verified it. Those two separate the real thing from a
   * picture of it.
   */
  meChecks(): Array<{ label: string; ok: boolean; note: string }> {
    const c = this.meChain();
    const out: Array<{ label: string; ok: boolean; note: string }> = [];
    if (c.collectionMint !== undefined) {
      out.push({
        label: 'Verified collection',
        ok: !!c.collectionMint,
        note: c.collectionMint ? 'Signed by the collection authority' : 'Not part of a verified collection',
      });
    }
    if (c.creatorsVerified !== undefined) {
      out.push({
        label: 'Verified creator',
        ok: !!c.creatorsVerified,
        note: c.creatorsVerified ? 'A listed creator signed the metadata' : 'No creator signature',
      });
    }
    if (c.mutable !== undefined) {
      out.push({
        label: 'Metadata',
        ok: !c.mutable,
        note: c.mutable ? 'Mutable — the update authority can still change it' : 'Immutable',
      });
    }
    // NOT a frozen check. Programmable NFTs report `frozen: true` by design —
    // they move through the Token Metadata program rather than a plain SPL
    // transfer — and Mad Lads that are listed and selling right now come back
    // frozen. Flagging that would fire on an entire token standard, which is
    // how a warning becomes something people click past. A plain NFT frozen
    // in its account genuinely cannot move, so only that is worth saying.
    if (c.frozen && c.standard !== 'ProgrammableNFT') {
      out.push({ label: 'Transfer', ok: false, note: 'Frozen — cannot be moved' });
    }
    return out;
  }

  meStandardLabel(): string | null {
    const i = this.meChain().standard;
    if (!i) return null;
    if (i === 'ProgrammableNFT') return 'Programmable NFT';
    if (i === 'V1_NFT') return this.meChain().compressed ? 'Compressed NFT' : 'NFT';
    return i;
  }

  solscan(addr: string | null | undefined): string {
    return `https://solscan.io/token/${addr ?? ''}`;
  }
  solscanAccount(addr: string | null | undefined): string {
    return `https://solscan.io/account/${addr ?? ''}`;
  }

  /**
   * Every trait, with what it is worth and how rare it is.
   *
   * A trait list on its own describes a picture. The share of the collection
   * carrying it, and the floor of the pieces that do, is what people open an
   * NFT page to read.
   */
  meNftTraits(): Array<{ label: string; value: string; share: number | null; count: number | null; floor: number | null }> {
    const n = this.meNft();
    if (!n) return [];
    return this.meRowTraits(n);
  }

  /**
   * Three bands: gold under 5%, magenta to 15%, muted above. A trait most of
   * the listed pieces share should not shout.
   *
   * The share is over the LISTED set, not the collection — Magic Eden's
   * attribute table only counts what is for sale (202 listed pieces plus 5
   * one-of-ones = its own listedCount of 207, against a collection of 2,421).
   * So it colours the chip but is never printed as a rarity percentage: it
   * would read as Magic Eden's 18% and say 21.7%.
   */
  meRarityClass(share: number | null): string {
    if (share === null) return '';
    if (share <= 0.05) return 'me-rar--gold';
    if (share <= 0.15) return 'me-rar--mid';
    return 'me-rar--common';
  }

  /** The royalty a sale pays the creator, which comes out of the seller's
   *  proceeds and is not the marketplace fee. */
  meRoyaltyPct(): number | null {
    const bps = this.meNft()?.sellerFeeBasisPoints;
    return typeof bps === 'number' && bps > 0 ? bps / 100 : null;
  }

  /**
   * `volume7d` reaches us from two sources with two units: the stats host
   * sends SOL, the older v2 record sends lamports. Reading one as the other is
   * a billion-fold error, so magnitude decides — no collection trades a
   * billion SOL in a week.
   */
  meSolOrRaw(v: number | null | undefined): number | null {
    if (v === null || v === undefined || !Number.isFinite(v)) return null;
    return v > 1e6 ? v / 1e9 : v;
  }

  /** Floor prices arrive in lamports from stats, SOL elsewhere. */
  meSol(v: number | null | undefined): number | null {
    return MagicEdenService.solFromMaybeLamports(v);
  }

  meTraits(t: MeTokenRow): Array<{ label: string; value: string }> {
    return (t.attributes ?? []).map(a => ({
      label: String(a.trait_type ?? a.traitType ?? ''),
      value: String(a.value ?? ''),
    })).filter(a => a.label);
  }

  /**
   * A tile's traits, scored the way the detail view scores them.
   *
   * A list of trait names is a description of the picture. What tells you
   * whether the piece is worth anything is how few others carry the trait and
   * what those trade for — which is what Magic Eden shows and we did not.
   */
  meRowTraits(t: MeTokenRow): Array<{ label: string; value: string; share: number | null; count: number | null; floor: number | null }> {
    const stats = this.meTraitStats();
    return this.meTraits(t).map(tr => {
      const s = stats[`${tr.label}|${tr.value}`];
      return {
        ...tr,
        share: s ? s.share : null,
        count: s ? s.count : null,
        floor: s ? MagicEdenService.solFromMaybeLamports(s.floor) : null,
      };
    });
  }

  /**
   * Rarity for a whole list, from one request.
   *
   * Magic Eden's attribute table is collection-wide, so scoring twenty tiles
   * costs the same as scoring one. Asking per NFT would be twenty identical
   * requests for the same answer.
   */
  private async loadTraitStatsForRows(rows: MeTokenRow[]): Promise<void> {
    if (Object.keys(this.meTraitStats()).length) return;
    const symbol = rows.map(r => r.collection).find((c): c is string => !!c);
    if (!symbol) return;
    const data = await this.magicEden.read<{ traitStats?: Record<string, { count: number; share: number; floor: number | null }> }>(
      'me_collection_attributes', { symbol },
    );
    const stats = data?.traitStats;
    if (stats && Object.keys(stats).length) {
      this.meTraitStats.set(stats);
      this.persistSnapshot();
    }
  }

  /**
   * Name any bid the server could not.
   *
   * The read resolves each offer's NFT, but that is one Magic Eden call per
   * mint and they rate-limit: a throttled row arrives as a bare address, and
   * clicking again "fixed" it, which is the signature of a transient failure
   * rather than a missing feature. This retries the few that came back unnamed
   * so the tile does not depend on the first attempt succeeding.
   */
  private async nameUnresolvedOffers(offers: MeOfferRow[]): Promise<void> {
    const missing = offers.filter(o => !o.name && o.tokenMint).slice(0, 6);
    if (!missing.length) return;
    const resolved = await Promise.all(missing.map(async o => {
      const d = await this.magicEden.read<Record<string, unknown>>('me_token', { mintAddress: o.tokenMint });
      const src = ((d?.['token'] as Record<string, unknown> | undefined) ?? d) ?? {};
      return { mint: o.tokenMint, ...src } as Record<string, unknown> & { mint?: string };
    }));
    const byMint = new Map(resolved.filter(r => r['name']).map(r => [r.mint, r]));
    if (!byMint.size) return;
    this.meOffers.update(rows => rows.map(r => {
      const hit = r.tokenMint ? byMint.get(r.tokenMint) : undefined;
      return hit
        ? {
            ...r,
            name: hit['name'] as string,
            image: (hit['image'] as string | undefined) ?? r.image,
            collectionName: (hit['collectionName'] as string | undefined) ?? r.collectionName,
          }
        : r;
    }));
    this.persistSnapshot();
  }

  /** The piece a bid is on. Falls back to the mint only when the lookup that
   *  names it could not answer. */
  meOfferTitle(o: MeOfferRow): string {
    if (o.name) return o.name;
    const m = o.tokenMint ?? '';
    return m ? `${m.slice(0, 4)}…${m.slice(-4)}` : '—';
  }

  /** How a window reads in a heading. */
  meWindowLabel(w: string | undefined): string {
    switch (w) {
      case '1h': return 'last hour';
      case '6h': return 'last 6 hours';
      case '7d': return 'last 7 days';
      case '30d': return 'last 30 days';
      default:   return 'last 24 hours';
    }
  }

  /** Open a trending row as a collection. */
  browseTrending(row: { symbol: string | null; name: string | null }): void {
    if (!row.symbol) return;
    this.browseMeCollection({ symbol: row.symbol, name: row.name ?? row.symbol } as MeCollectionRow);
  }

  /** The busiest day, so every other bar is drawn against it. */
  meSalesPeak(): number {
    return Math.max(1, ...(this.meSales()?.series ?? []).map(s => s.volume));
  }

  /** Which bar the pointer is on, so the readout can name its day. */
  readonly meBarHover = signal<number | null>(null);

  /** The day under the pointer, or nothing. */
  meHoveredDay(): { day: number; sales: number; volume: number; average: number; low: number | null; high: number | null } | null {
    const i = this.meBarHover();
    if (i === null) return null;
    return this.meSales()?.series[i] ?? null;
  }

  /**
   * Thirty labels under thirty bars collide into a grey smear — "9 Tem10
   * Tem11 Tem". Six is what fits, so every nth is drawn and the last is
   * always one of them: a chart whose right edge is unlabelled does not say
   * when it ends.
   */
  meShowBarLabel(index: number): boolean {
    const n = this.meSales()?.series.length ?? 0;
    if (n <= 8) return true;
    const step = Math.ceil(n / 6);
    return index % step === 0 || index === n - 1;
  }

  meSalesDay(unix: number): string {
    return new Date(unix * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  meWhen(blockTime: number | null | undefined): string {
    if (!blockTime) return '';
    const secs = Math.max(0, Math.floor(Date.now() / 1000) - blockTime);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  }

  meExpiry(expiry: number | null | undefined): string {
    if (!expiry || expiry < 0) return 'No expiry';
    const secs = expiry - Math.floor(Date.now() / 1000);
    if (secs <= 0) return 'Expired';
    if (secs < 3600) return `${Math.floor(secs / 60)}m left`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h left`;
    return `${Math.floor(secs / 86400)}d left`;
  }

  /** True when the offer is one the connected wallet made — Cancel, not Accept. */
  meIsOwnOffer(o: MeOfferRow): boolean {
    const me = this.walletService.publicKey()?.toString();
    return !!me && o.buyer === me;
  }

  private async fetchOrcaPools(): Promise<void> {
    this.orcaFetching.set(true);
    this.error.set(null);
    const search = (this.orcaSearchRaw() || (this.query.params['query'] as string | undefined) || '').trim();
    // One request for the whole working set; paging happens locally.
    const size = this.ORCA_WORKING_SET;
    const cat = this.orcaCategory();
    const category = cat === 'all' ? undefined : cat;
    // Search is NOT floored — someone looking for a specific small pool by
    // name should find it. The floor is for browsing.
    const page = search
      ? await this.orca.searchPoolsPage(search, { size, category })
      : await this.orca.fetchPoolsPage({
          sortBy: this.orcaSortField(),
          sortDirection: this.orcaSortDir(),
          size,
          category,
          minTvl: this.ORCA_MIN_TVL,
          token: this.query.params['token'] as string | undefined,
        });
    if (!page) {
      this.error.set('Failed to load Orca pools');
    } else {
      this.orcaRows.set(page.rows);
      this.orcaCapped.set(!search && !!page.nextCursor);
      this.orcaPage.set(1);
      this.reportEmptyState(page.rows.length === 0);
      this.persistSnapshot();
    }
    this.orcaFetching.set(false);
    this.loading.set(false);
  }


  private async fetchOrcaPositions(): Promise<void> {
    this.orcaPositionsFetching.set(true);
    this.error.set(null);
    if (!this.walletService.publicKey()) {
      this.error.set('Connect your wallet to see positions');
      this.orcaPositionsFetching.set(false);
      this.loading.set(false);
      return;
    }
    const rows = await this.orca.fetchUserPositions();
    if (rows === null) {
      this.error.set('Failed to load Orca positions');
      this.reportEmptyState(false);
    } else {
      this.orcaPositions.set(rows);
      this.reportEmptyState(rows.length === 0);
      this.persistSnapshot();
    }
    this.orcaPositionsFetching.set(false);
    this.loading.set(false);
  }

  /**
   * Abbreviated USD for table cells: $26.07M rather than $26,071,872.82.
   *
   * A pool list is scanned, not audited — the extra seven characters buy no
   * information and cost a column that then wraps and shifts the row. The
   * full figure stays in the cell's title for anyone who wants it.
   */
  formatUsdCompact(v: number | string | null | undefined): string {
    const n = typeof v === 'number' ? v : Number(v ?? 0);
    if (!Number.isFinite(n)) return '$0';
    const abs = Math.abs(n);
    const [div, suffix] = abs >= 1e9 ? [1e9, 'B']
      : abs >= 1e6 ? [1e6, 'M']
      : abs >= 1e3 ? [1e3, 'K']
      : [1, ''];
    const scaled = n / div;
    const digits = suffix && Math.abs(scaled) < 100 ? 2 : suffix ? 1 : 2;
    return `$${scaled.toFixed(digits)}${suffix}`;
  }

  /** Orca quotes its fee as hundredths of a basis point: 400 -> 0.04%. */
  orcaFeePct(row: OrcaPoolRow): number {
    return (Number(row.feeRate) || 0) / 10_000;
  }

  orcaVolume24h(row: OrcaPoolRow): number {
    return Number(row.stats?.['24h']?.volume ?? 0) || 0;
  }

  /** Fees over TVL for the window, annualised — Orca's own yield figure. */
  orcaApr(row: OrcaPoolRow): number {
    return (Number(row.yieldOverTvl) || 0) * 365 * 100;
  }

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
      pageSize: this.requestedPageSize(this.DAMMV2_PAGE_SIZE),
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
  // ── Marinade ──────────────────────────────────────────────────────────
  //
  // Two reads the chat could not previously answer without quoting a number
  // from memory: what staking pays right now, and which of this wallet's
  // delayed unstakes have matured.

  readonly marinadeRate = signal<{ msolPriceInSol: number; solPriceInMsol: number; apyPercent: number | null } | null>(null);
  readonly marinadeTicketRows = signal<Array<{
    address: string; solAmount: string; epochsRemaining: number; isClaimable: boolean; status: string;
  }>>([]);
  readonly marinadeFetching = signal(false);

  readonly stakePositions = signal<{
    stakeAccounts: Array<{ stakeAccount: string; stakedSol: number; status: string; voteAccount: string }>;
    totalStakedSol: number;
    liquidStaking: Array<{ symbol: string; mint: string; amount: number }>;
  } | null>(null);

  private async fetchStakePositions(): Promise<void> {
    this.marinadeFetching.set(true);
    this.error.set(null);
    try {
      const resp = await firstValueFrom(
        this.api.post<{ data?: any }>('/actions/build', {
          type: 'my_stake_accounts', params: {},
        }),
      );
      const d = resp?.data ?? {};
      this.stakePositions.set({
        stakeAccounts: d.stakeAccounts ?? [],
        totalStakedSol: Number(d.totalStakedSol ?? 0),
        liquidStaking: d.liquidStaking ?? [],
      });
    } catch {
      this.error.set('Could not read your staking positions right now.');
    } finally {
      this.marinadeFetching.set(false);
      this.loading.set(false);
    }
  }

  private async fetchMarinadeRate(): Promise<void> {
    this.marinadeFetching.set(true);
    this.error.set(null);
    try {
      const resp = await firstValueFrom(
        this.api.post<{ data?: any }>('/actions/build', { type: 'marinade_exchange_rate', params: {} }),
      );
      const d = resp?.data ?? {};
      this.marinadeRate.set({
        msolPriceInSol: Number(d.msolPriceInSol ?? 0),
        solPriceInMsol: Number(d.solPriceInMsol ?? 0),
        apyPercent: d.apyPercent === null || d.apyPercent === undefined ? null : Number(d.apyPercent),
      });
    } catch {
      this.error.set('Could not read the Marinade rate right now.');
    } finally {
      this.marinadeFetching.set(false);
      this.loading.set(false);
    }
  }

  private async fetchMarinadeTickets(): Promise<void> {
    this.marinadeFetching.set(true);
    this.error.set(null);
    try {
      const resp = await firstValueFrom(
        this.api.post<{ data?: any }>('/actions/build', { type: 'marinade_list_tickets', params: {} }),
      );
      this.marinadeTicketRows.set(resp?.data?.tickets ?? []);
    } catch {
      this.error.set('Could not read your Marinade tickets right now.');
    } finally {
      this.marinadeFetching.set(false);
      this.loading.set(false);
    }
  }

  /** Claim a matured ticket: hands the action card the address, which is the
   *  one thing the user cannot supply themselves. */
  claimMarinadeTicket(address: string): void {
    this.useAction.emit({
      type: 'marinade_claim_ticket',
      params: { ticketAccount: address },
    } as unknown as ParsedAction);
  }

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
    // The DEFAULT page size is not necessarily the page size in use — a
    // request that named a count overrides it, and the footer then claimed
    // "Showing 1–10" under five rows.
    const size = this.requestedPageSize(this.DAMMV2_PAGE_SIZE);
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
    // The typed search box takes precedence over any tokenA the query was
    // seeded with; tokenB only ever comes from the original query params.
    const tokenA = this.raydiumSearchTokenA()
      ?? (isSearch ? (this.query.params?.['tokenA'] as string | undefined) : undefined);
    const tokenB = isSearch ? (this.query.params?.['tokenB'] as string | undefined) : undefined;
    const body = tokenA
      ? {
          type: 'raydium_search_pools',
          params: {
            tokenA,
            ...(tokenB ? { tokenB } : {}),
            poolType: this.raydiumPoolType(),
            sortField: this.raydiumSortField(),
            page: this.raydiumPage(),
            pageSize: this.requestedPageSize(this.RAYDIUM_PAGE_SIZE),
          },
        }
      : {
          type: 'raydium_get_pools',
          params: {
            poolType: this.raydiumPoolType(),
            sortField: this.raydiumSortField(),
            sortType: this.raydiumSortDir(),
            page: this.raydiumPage(),
            pageSize: this.requestedPageSize(this.RAYDIUM_PAGE_SIZE),
          },
        };

    try {
      // Hard 15s ceiling so a stuck gateway/RPC connection surfaces the error
      // state (with a Refresh affordance) instead of spinning forever.
      const resp = await firstValueFrom(this.api.post<any>('/actions/build', body).pipe(timeout(15_000)));
      // preview.params holds the unmodified Raydium API envelope:
      //   { id, success, data: { count, hasNextPage, data: RaydiumPool[] } }
      // Raydium V3 quirk: `count` is the page size (10), NOT the total —
      // there's no field that exposes the total pool count. We drive
      // pagination off `hasNextPage` instead.
      const apiData = resp?.preview?.params?.data;
      const rows: RaydiumPool[] = Array.isArray(apiData?.data) ? apiData.data : [];
      const hasNext: boolean = !!apiData?.hasNextPage;

      // Numbered pagination: each page REPLACES the visible list with its own
      // 10 rows (never append). The table always shows exactly the current
      // page's slice.
      this.raydiumResults = rows;
      this.raydiumHasNextPage.set(hasNext);
      this.raydiumFetching.set(false);
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load Raydium pools');
      this.raydiumFetching.set(false);
      this.loading.set(false);
    }
  }

  // ── Uniswap pool listing (EVM, DexScreener-backed) ───────────────────────
  uniswapPoolsResults: UniswapPool[] = [];
  readonly uniswapPoolsFetching = signal(false);
  uniswapVersionFilter: 'all' | 'v2' | 'v3' | 'v4' = 'all';

  readonly UNISWAP_PAGE_SIZE = 10;
  uniswapPoolsPage = 0;

  get filteredUniswapPools(): UniswapPool[] {
    const v = this.uniswapVersionFilter;
    return v === 'all'
      ? this.uniswapPoolsResults
      : this.uniswapPoolsResults.filter(p => (p.version || '').toLowerCase() === v);
  }

  get uniswapTotalPages(): number {
    return Math.max(1, Math.ceil(this.filteredUniswapPools.length / this.UNISWAP_PAGE_SIZE));
  }

  get pagedUniswapPools(): UniswapPool[] {
    const page = Math.min(this.uniswapPoolsPage, this.uniswapTotalPages - 1);
    return this.filteredUniswapPools.slice(page * this.UNISWAP_PAGE_SIZE, (page + 1) * this.UNISWAP_PAGE_SIZE);
  }

  uniswapNextPage(): void { if (this.uniswapPoolsPage < this.uniswapTotalPages - 1) this.uniswapPoolsPage++; }
  uniswapPrevPage(): void { if (this.uniswapPoolsPage > 0) this.uniswapPoolsPage--; }

  /** Versions actually present in the current result set — drives the chips. */
  get uniswapVersionsAvailable(): string[] {
    const set = new Set(this.uniswapPoolsResults.map(p => (p.version || '').toLowerCase()).filter(Boolean));
    return ['v2', 'v3', 'v4'].filter(v => set.has(v));
  }

  setUniswapVersion(v: 'all' | 'v2' | 'v3' | 'v4'): void { this.uniswapVersionFilter = v; this.uniswapPoolsPage = 0; }

  private async fetchUniswapPools(): Promise<void> {
    this.uniswapPoolsFetching.set(true);
    this.error.set(null);
    const body = {
      type: 'uniswap_pools',
      params: {
        chain: this.query.params?.['chain'] ?? '',
        ...(this.query.params?.['query'] ? { query: this.query.params['query'] } : {}),
        ...(this.query.params?.['version'] ? { version: this.query.params['version'] } : {}),
      },
    };
    try {
      const resp = await firstValueFrom(this.api.post<any>('/actions/build', body).pipe(timeout(15_000)));
      const rows: UniswapPool[] = Array.isArray(resp?.data?.pools) ? resp.data.pools : [];
      this.uniswapPoolsResults = rows;
      this.uniswapPoolsFetching.set(false);
      this.loading.set(false);
      this.persistSnapshot();
    } catch (e: any) {
      const msg = (e?.error?.error ?? e?.message ?? '').toString();
      // Surface the backend's clean 400 (bad/unsupported chain) but never a raw stack.
      this.error.set(/[a-z].*(chain|available|required)/i.test(msg) ? msg : 'Failed to load Uniswap pools');
      this.uniswapPoolsFetching.set(false);
      this.loading.set(false);
    }
  }

  /** A pool row → open the add-liquidity action card, pre-filled with the pool
   *  identity. Fee tier / tickSpacing are resolved on-chain at build time
   *  (DexScreener doesn't expose them), so we pass only what we know here. */
  useUniswapPool(p: UniswapPool): void {
    const params: Record<string, string> = {
      chain: p.chain,
      version: p.version,
      poolAddress: p.pairAddress,
      token0: p.baseAddress,
      token1: p.quoteAddress,
      token0Symbol: p.baseSymbol,
      token1Symbol: p.quoteSymbol,
      pair: `${p.baseSymbol}/${p.quoteSymbol}`,
      ...(p.baseLogo ? { token0Logo: p.baseLogo } : {}),
      ...(p.quoteLogo ? { token1Logo: p.quoteLogo } : {}),
    };
    this.useAction.emit({
      type: 'uniswap_add_liquidity',
      params,
      raw: `[ACTION:uniswap_add_liquidity] ${JSON.stringify(params)}`,
    });
  }

  /** DexScreener token image for an EVM token (great long-tail coverage). */
  uniTokenLogo(chain: string, addr: string): string {
    if (!addr) return '';
    return `https://dd.dexscreener.com/ds-data/tokens/${chain}/${addr.toLowerCase()}.png`;
  }

  // ── pools.trade launchpad feed (Robinhood) ─────────────────────────────
  uniswapLaunchesResults: UniswapLaunch[] = [];
  uniswapLaunchpads: LaunchpadOpt[] = [];   // filter chips from the feed
  uniswapLaunchFilter = '';                 // '' = all launchpads
  readonly uniswapLaunchesFetching = signal(false);
  uniswapLaunchesSort: 'new' | 'top' | 'trending' = 'new';
  uniswapLaunchesPage = 0;
  uniswapLaunchSearch = '';                  // name/symbol search ('' = feed)
  private _uniswapSearchTimer: ReturnType<typeof setTimeout> | null = null;

  /** Debounced search-box input: re-query pools.trade by name/symbol. */
  onUniswapLaunchSearch(v: string): void {
    this.uniswapLaunchSearch = v;
    this.uniswapLaunchesPage = 0;
    if (this._uniswapSearchTimer) clearTimeout(this._uniswapSearchTimer);
    this._uniswapSearchTimer = setTimeout(() => void this.fetchUniswapLaunches(), 400);
  }
  clearUniswapLaunchSearch(): void {
    if (!this.uniswapLaunchSearch) return;
    this.uniswapLaunchSearch = '';
    this.uniswapLaunchesPage = 0;
    void this.fetchUniswapLaunches();
  }
  readonly UNISWAP_LAUNCH_PAGE_SIZE = 8;

  setUniswapLaunchFilter(id: string): void {
    if (this.uniswapLaunchFilter === id) return;
    this.uniswapLaunchFilter = id;
    this.uniswapLaunchesPage = 0;
    void this.fetchUniswapLaunches();
  }

  get pagedUniswapLaunches(): UniswapLaunch[] {
    const page = Math.min(this.uniswapLaunchesPage, this.uniswapLaunchesTotalPages - 1);
    return this.uniswapLaunchesResults.slice(page * this.UNISWAP_LAUNCH_PAGE_SIZE, (page + 1) * this.UNISWAP_LAUNCH_PAGE_SIZE);
  }
  get uniswapLaunchesTotalPages(): number {
    return Math.max(1, Math.ceil(this.uniswapLaunchesResults.length / this.UNISWAP_LAUNCH_PAGE_SIZE));
  }
  uniswapLaunchesNextPage(): void { if (this.uniswapLaunchesPage < this.uniswapLaunchesTotalPages - 1) this.uniswapLaunchesPage++; }
  uniswapLaunchesPrevPage(): void { if (this.uniswapLaunchesPage > 0) this.uniswapLaunchesPage--; }

  setUniswapLaunchesSort(s: 'new' | 'top' | 'trending'): void {
    if (this.uniswapLaunchesSort === s) return;
    this.uniswapLaunchesSort = s;
    this.uniswapLaunchesPage = 0;
    void this.fetchUniswapLaunches();
  }

  private async fetchUniswapLaunches(): Promise<void> {
    this.uniswapLaunchesFetching.set(true);
    this.error.set(null);
    const body = {
      type: 'uniswap_launches',
      params: {
        sort: this.uniswapLaunchesSort,
        ...(this.uniswapLaunchFilter ? { launchpad: this.uniswapLaunchFilter } : {}),
        ...(this.uniswapLaunchSearch.trim() ? { query: this.uniswapLaunchSearch.trim() } : {}),
        ...(this.query.params?.['limit'] ? { limit: Number(this.query.params['limit']) } : {}),
      },
    };
    try {
      const resp = await firstValueFrom(this.api.post<any>('/actions/build', body).pipe(timeout(30_000)));
      this.uniswapLaunchesResults = Array.isArray(resp?.data?.launches) ? resp.data.launches : [];
      // Keep the union of launchpads seen (a filtered response only carries the
      // one filtered launchpad, so never shrink the chip set to a single option).
      const opts: LaunchpadOpt[] = Array.isArray(resp?.data?.launchpads) ? resp.data.launchpads : [];
      const merged = new Map<string, LaunchpadOpt>(this.uniswapLaunchpads.map(o => [o.id, o]));
      for (const o of opts) if (o?.id) merged.set(o.id, o);
      this.uniswapLaunchpads = [...merged.values()];
      this.uniswapLaunchesFetching.set(false);
      this.loading.set(false);
      this.persistSnapshot();
    } catch (e: any) {
      this.error.set('Failed to load pools.trade launches');
      this.uniswapLaunchesFetching.set(false);
      this.loading.set(false);
    }
  }

  /** Buy a launched token natively on pools.trade (bonding curve / CCA), paying
   *  in USD-worth of ETH on Robinhood Chain. Opens the pools_buy action card
   *  where the user enters the USD amount. Uses trade.prepareBuy, NOT a raw v4
   *  swap — curve-live tokens aren't swappable until they graduate. */
  buyUniswapLaunch(l: UniswapLaunch): void {
    const params: Record<string, string> = {
      chain: 'robinhood',
      tokenAddress: l.tokenAddress,
      symbol: l.symbol,
      name: l.name,
      amountUsd: '5',
      slippagePct: '5',
      ...(l.logo ? { logo: l.logo } : {}),
      ...(l.launchpad ? { launchpad: l.launchpad } : {}),
    };
    this.useAction.emit({
      type: 'pools_buy',
      params,
      raw: `[ACTION:pools_buy] ${JSON.stringify(params)}`,
    });
  }

  /** Compact "3m/2h/1d ago" from an ISO timestamp. */
  launchAge(iso?: string | null): string {
    if (!iso) return '';
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return `${Math.floor(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    return `${Math.floor(s / 86400)}d`;
  }

  onRaydiumPoolTypeChange(t: 'all' | 'concentrated' | 'standard'): void {
    if (this.raydiumPoolType() === t) return;
    this.raydiumPoolType.set(t);
    this.raydiumPage.set(1);
    void this.fetchRaydiumPools();
  }

  /**
   * Precise pool-program label. Raydium's API lumps the newer CPMM and the
   * legacy AMM v4 together as "Standard", but they are different programs with
   * different costs and mechanics — showing both as "AMM" hides which one a
   * deposit actually lands in. Falls back to the API's coarse type.
   */
  private static readonly RAY_PROGRAMS: Record<string, string> = {
    CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C: 'CPMM',
    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'AMM v4',
    CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK: 'CLMM',
  };

  raydiumPoolKind(p: { type?: string; programId?: string }): string {
    const known = p.programId ? QueryCardComponent.RAY_PROGRAMS[p.programId] : undefined;
    if (known) return known;
    return (p.type ?? '').toLowerCase() === 'concentrated' ? 'CLMM' : 'AMM';
  }

  onRaydiumSearchInput(v: string): void {
    this.raydiumSearchInput.set(v);
  }

  /** Resolve the typed symbol/mint and refetch the pool list filtered to pools
   *  containing that token. Empty input clears the filter. */
  onRaydiumSearchSubmit(): void {
    const raw = this.raydiumSearchInput().trim();
    if (!raw) { this.clearRaydiumSearch(); return; }
    const mint = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(raw)
      ? raw
      : this.tokenRegistry.getBySymbol(raw)?.address ?? null;
    if (!mint) { this.error.set(`No token found for "${raw}"`); return; }
    this.error.set(null);
    this.raydiumSearchTokenA.set(mint);
    this.raydiumPage.set(1);
    void this.fetchRaydiumPools();
  }

  clearRaydiumSearch(): void {
    if (!this.raydiumSearchInput() && !this.raydiumSearchTokenA()) return;
    this.raydiumSearchInput.set('');
    this.raydiumSearchTokenA.set(null);
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

  /** Fetch the user's Raydium positions (CLMM + LP) straight from chain. */
  private async fetchRaydiumPositions(): Promise<void> {
    this.raydiumPositionsFetching.set(true);
    this.error.set(null);
    const wallet = this.walletService.publicKey();
    if (!wallet) {
      this.error.set('Connect your wallet to see positions');
      this.loading.set(false);
      this.raydiumPositionsFetching.set(false);
      return;
    }
    try {
      const resp = await firstValueFrom(
        this.api.post<any>('/actions/build', {
          type: 'raydium_get_user_positions',
          params: { wallet },
        }).pipe(timeout(20_000)),
      );
      const data = resp?.data ?? resp?.preview?.params?.data;
      const positions: RaydiumUserPosition[] = Array.isArray(data?.positions) ? data.positions : [];
      this.raydiumPositions.set(positions);
      this.loading.set(false);
      this.raydiumPositionsFetching.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load Raydium positions');
      this.loading.set(false);
      this.raydiumPositionsFetching.set(false);
    }
  }

  /**
   * The wallet's Meteora DLMM positions, grouped by pool as the API returns
   * them. Rendering these as a card (rather than letting the model narrate
   * them) is what lets the user click the position they mean — with two
   * positions in one pool, prose can only offer a pair of base58 addresses.
   */
  private async fetchDlmmPositions(): Promise<void> {
    this.dlmmPositionsFetching.set(true);
    this.error.set(null);
    if (!this.walletService.publicKey()) {
      this.error.set('Connect your wallet to see positions');
      this.loading.set(false);
      this.dlmmPositionsFetching.set(false);
      return;
    }
    try {
      const resp = await firstValueFrom(
        this.api.post<any>('/actions/build', {
          type: this.query.type === 'meteora_dammv2_get_user_positions'
            ? 'meteora_dammv2_get_user_positions'
            : 'meteora_dlmm_get_user_positions',
          params: {},
        }).pipe(timeout(20_000)),
      );
      const data = resp?.data ?? resp?.preview?.params?.data;
      this.dlmmUserPools.set(Array.isArray(data?.pools) ? data.pools : []);
      this.reportEmptyState(this.dlmmUserPools().length === 0);
      this.persistSnapshot();
    } catch {
      this.error.set('Failed to load Meteora DLMM positions');
      // Report non-empty so a multi-listing message still shows the error
      // rather than silently hiding a card that failed.
      this.reportEmptyState(false);
    } finally {
      this.dlmmPositionsFetching.set(false);
      this.loading.set(false);
    }
  }

  /** Shared display context so the action card can render a real summary. */
  /**
   * Params handed to a spawned Meteora action. Beyond the addresses the
   * builder needs, this carries the position's own economics — range, token
   * amounts, unclaimed fees — as display context. A confirm-only action like
   * Claim has nothing to fill in, so without these the card could only echo a
   * base58 address back at the user: "sign this, trust me". With them it can
   * state what is actually being claimed.
   */
  private dlmmActionParams(row: DlmmPositionRow): Record<string, string> {
    const pool = row.pool;
    const d = row.detail;
    return {
      pool: pool.poolAddress,
      poolId: pool.poolAddress,
      position: row.address,
      positionId: row.address,
      pair: `${pool.tokenX}/${pool.tokenY}`,
      tokenASymbol: pool.tokenX,
      tokenBSymbol: pool.tokenY,
      ...(pool.tokenXMint ? { tokenA: pool.tokenXMint } : {}),
      ...(pool.tokenYMint ? { tokenB: pool.tokenYMint } : {}),
      ...(pool.tokenXIcon ? { tokenALogo: pool.tokenXIcon } : {}),
      ...(pool.tokenYIcon ? { tokenBLogo: pool.tokenYIcon } : {}),
      binStep: String(pool.binStep ?? ''),
      currentPrice: String(pool.poolPrice ?? ''),
      ...(pool.concentrated ? { poolConcentrated: 'true' } : {}),
      ...(pool.depositRatio ? { depositRatio: String(pool.depositRatio) } : {}),
      ...(pool.priceOutOfRange ? { poolPriceOutOfRange: 'true' } : {}),
      positionIndex: String(row.index + 1),
      positionOutOfRange: row.outOfRange ? 'true' : 'false',
      ...(pool.activeBinId !== undefined ? { activeBinId: String(pool.activeBinId) } : {}),
      ...(pool.tokenXDecimals !== undefined ? { tokenADecimals: String(pool.tokenXDecimals) } : {}),
      ...(pool.tokenYDecimals !== undefined ? { tokenBDecimals: String(pool.tokenYDecimals) } : {}),
      ...(d
        ? {
            // Range fields only when the product HAS a range — a DAMM v2
            // position has none, and emitting empty strings would make the
            // panel draw a "0 – 0" band that does not exist.
            ...(d.lowerPrice !== undefined && d.upperPrice !== undefined
              ? {
                  positionMinPrice: String(d.lowerPrice),
                  positionMaxPrice: String(d.upperPrice),
                  positionBinCount: String(d.binCount ?? ''),
                }
              : {}),
            positionAmountA: String(d.amountX),
            positionAmountB: String(d.amountY),
            positionFeeA: String(d.unclaimedFeeX),
            positionFeeB: String(d.unclaimedFeeY),
            ...(d.rentSol !== undefined ? { positionRentSol: String(d.rentSol) } : {}),
            // The position's range IS the range an add-liquidity card must
            // deposit into — it cannot be re-ranged, only widened by opening
            // a new position. Passing the bin ids lets the card's existing
            // ratio engine work against the real range instead of seeding a
            // fresh one around the active bin.
            minBinId: String(d.lowerBinId),
            maxBinId: String(d.upperBinId),
          }
        : {}),
    };
  }

  addToDlmmPosition(row: DlmmPositionRow): void {
    const params = this.dlmmActionParams(row);
    const type = this.isDammV2Positions() ? 'meteora_dammv2_add_liquidity' : 'meteora_add_to_position';
    this.useAction.emit({ type, params, raw: `[ACTION:${type}] ${JSON.stringify(params)}` });
  }

  withdrawDlmmPosition(row: DlmmPositionRow): void {
    const params = { ...this.dlmmActionParams(row), bpsToRemove: '10000' };
    const type = this.isDammV2Positions() ? 'meteora_dammv2_remove_liquidity' : 'meteora_remove_liquidity';
    this.useAction.emit({ type, params, raw: `[ACTION:${type}] ${JSON.stringify(params)}` });
  }

  claimDlmmFees(row: DlmmPositionRow): void {
    const params = this.dlmmActionParams(row);
    const type = this.isDammV2Positions() ? 'meteora_dammv2_claim_fee' : 'meteora_claim_fees';
    this.useAction.emit({ type, params, raw: `[ACTION:${type}] ${JSON.stringify(params)}` });
  }

  /**
   * Open a NEW position in the same pool, at the current price.
   *
   * This is the way out when a position's own range can no longer take a
   * deposit: a range is fixed at creation, so "add to this one" has no
   * meaning, but "put liquidity in this pool" — what the user actually wants —
   * is still perfectly possible. Blocking Add without offering this leaves
   * them with a dead end.
   *
   * No range is passed: the action card seeds one around the pool's active bin.
   */
  openNewDlmmPosition(row: DlmmPositionRow): void {
    const { position, positionId, ...rest } = this.dlmmActionParams(row);
    void position; void positionId;
    const params: Record<string, string> = {
      ...rest,
      // The pool is the one they already hold a position in — a deliberate
      // choice, not a guess, so the action card must not re-pick it.
      poolChosenBy: 'user',
      // A fresh position must not inherit the old one's bounds.
      minBinId: '',
      maxBinId: '',
      positionMinPrice: '',
      positionMaxPrice: '',
    };
    for (const k of Object.keys(params)) if (params[k] === '') delete params[k];
    this.useAction.emit({ type: 'meteora_open_position', params, raw: `[ACTION:meteora_open_position] ${JSON.stringify(params)}` });
  }

  /**
   * Close: withdraw + claim + close the position account, which is the only
   * thing that returns the ~0.057 SOL of rent the position holds. Withdraw
   * alone leaves that locked in an empty position forever, so a row that can
   * withdraw but not close is a row that can only lose the user money.
   */
  closeDlmmPosition(row: DlmmPositionRow): void {
    const params = this.dlmmActionParams(row);
    const type = this.isDammV2Positions() ? 'meteora_dammv2_close_position' : 'meteora_close_position';
    this.useAction.emit({ type, params, raw: `[ACTION:${type}] ${JSON.stringify(params)}` });
  }

  /** DAMM v2 rather than DLMM. The two share this card because a position is a
   *  position; what differs is that DAMM v2 has no range and its actions live
   *  under different type names. */
  readonly isDammV2Positions = computed(() => this.query.type === 'meteora_dammv2_get_user_positions');

  /** Pair logo for a positions row. The DAMM v2 data API carries no logo
   *  fields at all, so fall through to the token registry by mint rather than
   *  dropping to a letter tile. */
  dlmmPoolLogo(pool: DlmmUserPool, side: 'x' | 'y'): string | null {
    const fromApi = side === 'x' ? pool.tokenXIcon : pool.tokenYIcon;
    if (fromApi) return fromApi;
    const mint = side === 'x' ? pool.tokenXMint : pool.tokenYMint;
    return mint ? this.tokenLogo(mint) : null;
  }

  dlmmPositionOutOfRange(pool: DlmmUserPool, position: string): boolean {
    return (pool.positionsOutOfRange ?? []).includes(position);
  }

  /**
   * One panel per POSITION, not per pool. Re-ranging a DLMM position means
   * opening a second one in the same pool, so "2 positions" in a single pool
   * row is the common case — and it hides exactly what differs between them:
   * each has its own price range, balance and unclaimed fees. Flattening here
   * mirrors the Raydium CLMM card, where every position is its own panel with
   * its own actions.
   *
   * `detail` is null when the on-chain enrichment failed; the panel then still
   * renders with the address and the pool-level context, which is enough to
   * act on.
   */
  readonly dlmmPositionRows = computed<DlmmPositionRow[]>(() => {
    const rows: DlmmPositionRow[] = [];
    for (const pool of this.dlmmUserPools()) {
      const byAddress = new Map<string, DlmmPositionDetail>(
        (pool.positions ?? []).map(p => [p.address, p]),
      );
      const addresses = pool.listPositions?.length
        ? pool.listPositions
        : (pool.positions ?? []).map(p => p.address);
      addresses.forEach((address, index) => {
        const detail = byAddress.get(address) ?? null;
        rows.push({
          pool,
          address,
          index,
          detail,
          // Prefer the chain's answer; fall back to the API's per-pool list.
          outOfRange: detail ? !detail.inRange : this.dlmmPositionOutOfRange(pool, address),
        });
      });
    }
    return rows;
  });

  /** Total open positions across every pool — the card's header count. */
  readonly dlmmPositionTotal = computed(() => this.dlmmPositionRows().length);

  /** Pool-level value / PnL / fees, rolled up. Splitting the card into
   *  per-position panels would otherwise drop these — they are only priced
   *  per pool upstream, so they belong in one summary strip, not repeated on
   *  every panel where they'd read as that position's own PnL. */
  readonly dlmmPositionsSummary = computed(() => {
    const pools = this.dlmmUserPools();
    if (pools.length === 0) return null;
    const num = (v: unknown) => {
      const n = Number(v ?? 0);
      return Number.isFinite(n) ? n : 0;
    };
    const value = pools.reduce((s, p) => s + num(p.balances), 0);
    const pnl = pools.reduce((s, p) => s + num(p.pnl), 0);
    const fees = pools.reduce((s, p) => s + num(p.unclaimedFees), 0);
    const deposited = pools.reduce((s, p) => s + num(p.totalDeposit), 0);
    return {
      value,
      pnl,
      fees,
      pnlPct: deposited > 0 ? (pnl / deposited) * 100 : 0,
      poolCount: pools.length,
      positionCount: this.dlmmPositionRows().length,
    };
  });

  /**
   * True when the position's range is so far from the pool's price that the
   * backend will refuse a deposit — the same 100x bound it enforces, computed
   * here so the card never offers an Add that cannot succeed.
   *
   * The bound is expressed in price rather than bins because a bin's width
   * depends on the pool's bin step.
   */
  dlmmRangeUnusable(row: DlmmPositionRow): boolean {
    const d = row.detail;
    const active = row.pool.activeBinId;
    const step = row.pool.binStep;
    // Bin ids are DLMM-only — a constant-product position has no range to be
    // far from, so this can never apply to DAMM v2.
    if (!d || d.lowerBinId === undefined || d.upperBinId === undefined) return false;
    if (active === undefined || !(step > 0)) return false;
    const lower = d.lowerBinId;
    const upper = d.upperBinId;
    const maxDistance = Math.ceil(Math.log(100) / Math.log(1 + step / 10_000));
    const nearest = upper < active ? active - upper
      : lower > active ? lower - active
      : 0;
    return nearest > maxDistance;
  }

  /**
   * True when this position has fees worth claiming. Only decidable once the
   * on-chain detail is in — without it, assume there might be (a claim issued
   * blind is better than a Claim button that refuses for the wrong reason).
   */
  dlmmHasClaimableFees(row: DlmmPositionRow): boolean {
    const d = row.detail;
    if (!d) return true;
    return d.unclaimedFeeX > 0 || d.unclaimedFeeY > 0;
  }

  /** USD value of one position, prorated from the pool total by token amounts
   *  when per-position detail is available. The portfolio API only prices the
   *  pool as a whole, so this is a split, not an independent valuation. */
  dlmmPositionValue(row: DlmmPositionRow): number | null {
    const detail = row.detail;
    if (!detail) return null;
    const positions = row.pool.positions ?? [];
    if (positions.length === 0) return null;
    const px = row.pool.poolPrice || 0;
    const weight = (p: DlmmPositionDetail) => p.amountX * px + p.amountY;
    const total = positions.reduce((sum, p) => sum + weight(p), 0);
    const poolValue = Number(row.pool.balances ?? 0);
    if (!Number.isFinite(poolValue)) return null;
    if (total <= 0) return poolValue / positions.length;
    return poolValue * (weight(detail) / total);
  }

  /** Add liquidity to an EXISTING position: CLMM → increase the range's
   *  liquidity; LP → deposit more into the same standard pool. Carries the same
   *  display context as withdraw so the action card renders a real summary. */
  addToRaydiumPosition(pos: RaydiumUserPosition): void {
    const display: Record<string, string> = {
      pair: pos.pair ?? '',
      poolId: pos.poolId,
      tokenASymbol: pos.mintA?.symbol ?? '',
      tokenBSymbol: pos.mintB?.symbol ?? '',
      ...(pos.mintA?.address ? { tokenA: pos.mintA.address } : {}),
      ...(pos.mintB?.address ? { tokenB: pos.mintB.address } : {}),
      ...(pos.mintA?.logoURI ? { tokenALogo: pos.mintA.logoURI } : {}),
      ...(pos.mintB?.logoURI ? { tokenBLogo: pos.mintB.logoURI } : {}),
    };
    if (pos.kind === 'lp') {
      const params: Record<string, string> = { ...display, poolId: pos.poolId };
      this.useAction.emit({ type: 'raydium_add_liquidity', params, raw: `[ACTION:raydium_add_liquidity] ${JSON.stringify(params)}` });
      return;
    }
    const params: Record<string, string> = {
      ...display,
      positionId: pos.positionId ?? '',
      positionKind: 'clmm',
      // Default the deposit side to token A; the card lets the user switch.
      ...(pos.mintA?.address ? { inputMint: pos.mintA.address } : {}),
      ...(pos.amountA !== undefined ? { amountA: String(pos.amountA) } : {}),
      ...(pos.amountB !== undefined ? { amountB: String(pos.amountB) } : {}),
    };
    this.useAction.emit({ type: 'raydium_increase_position', params, raw: `[ACTION:raydium_increase_position] ${JSON.stringify(params)}` });
  }

  /** Withdraw a position: LP → remove-liquidity; CLMM → close the range.
   *  Carries DISPLAY context (pair, token symbols/logos, amount) alongside the
   *  functional ids so the action card can render a real position summary
   *  instead of a bare "POSITION ID: <base58>" text field. */
  withdrawRaydiumPosition(pos: RaydiumUserPosition): void {
    const display: Record<string, string> = {
      pair: pos.pair ?? '',
      poolId: pos.poolId,
      tokenASymbol: pos.mintA?.symbol ?? '',
      tokenBSymbol: pos.mintB?.symbol ?? '',
      ...(pos.mintA?.logoURI ? { tokenALogo: pos.mintA.logoURI } : {}),
      ...(pos.mintB?.logoURI ? { tokenBLogo: pos.mintB.logoURI } : {}),
    };
    if (pos.kind === 'lp') {
      const params: Record<string, string> = {
        ...display,
        poolId: pos.poolId,
        lpAmount: String(pos.lpAmount ?? ''),
        // The full holding, so the card can scale a partial withdrawal, plus
        // the token amounts those LP tokens redeem for.
        positionLpAmount: String(pos.lpAmount ?? ''),
        ...(pos.amountA !== undefined ? { amountA: String(pos.amountA) } : {}),
        ...(pos.amountB !== undefined ? { amountB: String(pos.amountB) } : {}),
        positionKind: 'lp',
      };
      this.useAction.emit({ type: 'raydium_remove_liquidity', params, raw: `[ACTION:raydium_remove_liquidity] ${JSON.stringify(params)}` });
    } else {
      const params: Record<string, string> = {
        ...display,
        positionId: pos.positionId ?? '',
        positionKind: 'clmm',
        ...(pos.liquidity ? { liquidity: pos.liquidity } : {}),
        // Token amounts for display — the card shows "you get back X + Y"
        // instead of the raw liquidity constant.
        ...(pos.amountA !== undefined ? { amountA: String(pos.amountA) } : {}),
        ...(pos.amountB !== undefined ? { amountB: String(pos.amountB) } : {}),
      };
      this.useAction.emit({ type: 'raydium_close_position', params, raw: `[ACTION:raydium_close_position] ${JSON.stringify(params)}` });
    }
  }

  raydiumPrevPage(): void {
    if (this.raydiumPage() > 1) this.raydiumGoToPage(this.raydiumPage() - 1);
  }

  raydiumNextPage(): void {
    if (!this.raydiumHasNextPage()) return;
    this.raydiumGoToPage(this.raydiumPage() + 1);
  }

  /**
   * Page-based row range for the footer ("11–20 of this page"). Each page holds
   * up to RAYDIUM_PAGE_SIZE rows and replaces the list, so the range is derived
   * from the current page index, not an accumulated count.
   */
  get raydiumShowingRange(): { from: number; to: number } {
    const n = this.raydiumResults.length;
    if (n === 0) return { from: 0, to: 0 };
    const from = (this.raydiumPage() - 1) * this.RAYDIUM_PAGE_SIZE + 1;
    return { from, to: from + n - 1 };
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
      // Picked from a ranked list the user could see — the action card
      // re-checks a pool the model named on its own, and must leave this one
      // alone.
      poolChosenBy: 'user',
      tokenA: p.mintA.address,
      tokenB: p.mintB.address,
      tokenASymbol: p.mintA.symbol,
      tokenBSymbol: p.mintB.symbol,
      tokenADecimals: String(p.mintA.decimals ?? 9),
      tokenBDecimals: String(p.mintB.decimals ?? 9),
      // Lets the action card name the exact program (CPMM vs legacy AMM v4)
      // rather than the API's coarse "Standard".
      ...(p.programId ? { programId: p.programId } : {}),
    };
    // Raydium `price` is quote-per-base = mintB per mintA = amountB / amountA
    // in human units. Carry it so the action card can auto-balance the two
    // deposit amounts (type one side / hit Max → the other side fills to the
    // pool ratio).
    if (typeof p.price === 'number' && p.price > 0) {
      params['currentPrice'] = String(p.price);
      if (isCLMM) {
        // CLMM: pre-fill a balanced ±20% range so the card's Uniswap-v3 ratio
        // engine has a reference band (user can tighten/widen before confirm).
        params['minPrice'] = (p.price * 0.8).toPrecision(6);
        params['maxPrice'] = (p.price * 1.2).toPrecision(6);
      } else {
        // Standard AMM v4: full-range only (no min/max). The deposit ratio is
        // simply the pool price — feed it as `amountRatio` (B per A) so
        // `ammRatio()` drives the auto-balance on Max / single-side entry.
        params['amountRatio'] = String(p.price);
      }
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
      // current_price is human-units; the bin formula speaks in RAW units
      // (y_raw per x_raw), so convert with 10^(decY-decX) before solving for
      // the bin id — 1 SOL = 74.97 USDC is 74.97e6/1e9 = 0.07497 raw.
      // Checked against the chain: the SOL/USDC pool's active_id is -6479 and
      // only this direction reproduces it. The bin ids derived here are what
      // the deposit submits, so the wrong sign puts the whole range tens of
      // thousands of bins from the pool.
      activeBinId = Math.round(
        Math.log(currentPrice * Math.pow(10, decY - decX)) /
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
    // "Open a position with half of my mSOL" now answers with this listing and
    // no action card, because which pool to enter is the user's call. The size
    // they asked for must survive that step: without it the card opens empty
    // and the user has to restate a number they already gave. The listing
    // query carries the share, and the action card resolves it against the
    // live balance at the pool's ratio.
    const pct = (this.query?.params?.['amountPercent'] ?? '').trim();
    if (pct) {
      const n = parseFloat(pct.replace(/[%\s]/g, '').replace(',', '.'));
      if (Number.isFinite(n) && n > 0 && n <= 100) params['amountA'] = `${n}%`;
    }
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
      // The user picked this row out of a ranked list they could see. The
      // card re-checks a pool the model named on its own, and must not
      // second-guess this one — overriding a choice someone made deliberately
      // is a different kind of wrong from letting a bad default through.
      poolChosenBy: 'user',
      tokenA: p.token_x.address,
      tokenB: p.token_y.address,
      tokenXMint: p.token_x.address,
      tokenYMint: p.token_y.address,
      tokenASymbol: p.token_x.symbol,
      tokenBSymbol: p.token_y.symbol,
      tokenADecimals: String(p.token_x.decimals ?? 9),
      tokenBDecimals: String(p.token_y.decimals ?? 9),
      // Deliberately no ratio and no reserves. A DAMM v2 pool can concentrate
      // its liquidity, and then the deposit split follows where the price sits
      // inside the band — this pool trades at 76.93 in a 70–440 band and the
      // SDK quotes 6.10. The card asks the SDK instead
      // (meteora_dammv2_pool_quote): one call, and it is the same quote the
      // deposit will run. Passing an approximation here would only suppress
      // that call and pair the deposit wrong.
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
    const size = this.requestedPageSize(this.DLMM_PAGE_SIZE);
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
      case 'meteora_dlmm_get_user_positions':
      case 'meteora_dammv2_get_user_positions':
        await this.fetchDlmmPositions();
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
      case 'uniswap_pools':
        await this.fetchUniswapPools();
        return;
      case 'uniswap_launches':
        await this.fetchUniswapLaunches();
        return;
      // Every Magic Eden read goes through one fetcher; the renderer is
      // chosen from the shape of the reply, not from the type name.
      case 'me_collections':
      case 'me_collection_listings':
      case 'me_collection_nfts':
      case 'me_collection_activities':
      case 'me_collection_activity':
      case 'me_collection_stats':
      case 'me_collection_info':
      case 'me_collection_attributes':
      case 'me_trending_collections':
      case 'me_collection_holder_stats':
      case 'me_collection_sales_history':
      case 'me_collection_leaderboard':
      case 'me_token':
      case 'me_nft_info':
      case 'me_token_activities':
      case 'me_token_listings':
      case 'me_token_offers_received':
      case 'me_offers':
      case 'me_listings':
      case 'me_wallet_tokens':
      case 'me_wallet_nfts':
      case 'me_wallet_activities':
      case 'me_owner_activities':
      case 'me_wallet_offers_made':
      case 'me_wallet_offers_received':
      case 'me_mmm_pools':
        await this.fetchMagicEden();
        return;
      case 'orca_get_pools':
      case 'orca_search_pools':
        await this.fetchOrcaPools();
        return;
      case 'orca_get_user_positions':
        await this.fetchOrcaPositions();
        return;
      case 'raydium_get_user_positions':
      case 'raydium_get_clmm_positions':
        await this.fetchRaydiumPositions();
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
      case 'cross_chain_quote':
      case 'cross_chain_chains':
      case 'cross_chain_tokens':
        // These types are handled as ACTION blocks by the backend.
        this.error.set('Use the action card to execute this request.');
        this.loading.set(false);
        return;
      case 'tax_report':
        this.loading.set(true);
        await this.fetchTaxReport().catch(() => {
          this.error.set('Could not build your tax report right now.');
          this.loading.set(false);
          this.persistSnapshot();
        });
        return;
      // Nothing backs these yet. They used to render invented rows — claimable
      // airdrops nobody was eligible for, alerts nobody had set — which is
      // worse than an empty card, because the user acts on it.
      case 'airdrops':
        this.error.set('Airdrop eligibility isn\'t available yet.');
        this.loading.set(false);
        return;
      case 'alerts':
        this.error.set('Alerts aren\'t available yet.');
        this.loading.set(false);
        return;
      default:
        this.error.set('Unknown query type');
        this.loading.set(false);
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
    const solWallet = resolvedParam || this.walletService.publicKey();
    const tokenParam = (this.query.params['token'] as string | undefined)?.trim();
    const wantsAll = !tokenParam || tokenParam.toUpperCase() === 'ALL';

    // ── EVM path ────────────────────────────────────────────────────────────
    // "which chains do I have ETH on", or any balance question when the user's
    // connected wallet is EVM. The Solana balance path below can't answer these
    // (ETH isn't an SPL mint). Route to the multichain EVM portfolio, keyed on
    // the linked EVM address — never the Solana key — so a connected EVM user
    // never sees "Connect your wallet".
    const explicitEvmAddr = resolvedParam && /^0x[0-9a-fA-F]{40}$/.test(resolvedParam) ? resolvedParam : null;
    const evmAddrs = explicitEvmAddr ? [explicitEvmAddr] : await this.resolveEvmAddresses();
    const wantsEvm = this.isEvmNativeToken(tokenParam) || !!explicitEvmAddr || (!solWallet && evmAddrs.length > 0);
    if (wantsEvm && evmAddrs.length > 0) {
      try {
        this.balanceResults = await this.fetchEvmBalances(evmAddrs, wantsAll ? null : tokenParam!);
        this.loading.set(false);
        this.persistSnapshot();
      } catch {
        this.error.set('Failed to load balances');
        this.loading.set(false);
      }
      return;
    }

    // ── Solana path ─────────────────────────────────────────────────────────
    const wallet = solWallet;
    if (!wallet) {
      this.error.set('Connect your wallet to see balances');
      this.loading.set(false);
      return;
    }

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

  /** EVM addresses linked to the account (primary first), plus the live
   *  browser-connected address as a fallback for a connected-but-unlinked
   *  wallet. De-duped, lowercased for stable comparison. */
  private async resolveEvmAddresses(): Promise<string[]> {
    const addrs = new Set<string>();
    try {
      const me = await firstValueFrom(this.accountService.getMe().pipe(timeout(6000)));
      for (const id of me?.identities ?? []) {
        if (id.type === 'evm_wallet' && /^0x[0-9a-fA-F]{40}$/.test(id.identifier)) {
          addrs.add(id.identifier.toLowerCase());
        }
      }
    } catch { /* not signed in / offline — fall back to the live wallet below */ }
    const live = (globalThis as any)?.ethereum?.selectedAddress as string | undefined;
    if (live && /^0x[0-9a-fA-F]{40}$/.test(live)) addrs.add(live.toLowerCase());
    return [...addrs];
  }

  /** True when the requested token is a native EVM gas asset — the signal that
   *  a balance question is about EVM chains, not Solana. */
  private isEvmNativeToken(token?: string): boolean {
    if (!token) return false;
    return ['ETH', 'WETH', 'BNB', 'MATIC', 'POL', 'AVAX'].includes(token.toUpperCase());
  }

  private static readonly EVM_CHAIN_LABEL: Record<string, string> = {
    ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum', optimism: 'Optimism',
    polygon: 'Polygon', bsc: 'BNB Chain', robinhood: 'Robinhood',
  };

  // Trust Wallet asset folders per chain (bsc → smartchain); Robinhood has no
  // Trust Wallet folder, so it uses its Relay icon. Mirrors evm-holdings.
  private static readonly EVM_TW_FOLDER: Record<string, string> = {
    ethereum: 'ethereum', base: 'base', arbitrum: 'arbitrum', optimism: 'optimism',
    polygon: 'polygon', bsc: 'smartchain',
  };

  /** Small network badge shown on an EVM token icon (e.g. an Optimism mark
   *  under an ETH coin). Returns '' for Solana rows (no badge). */
  chainBadgeLogo(chain?: string): string {
    if (!chain) return '';
    if (chain === 'robinhood') return 'https://assets.relay.link/icons/4663/light.png';
    const folder = QueryCardComponent.EVM_TW_FOLDER[chain];
    if (!folder) return '';
    return `https://cdn.jsdelivr.net/gh/trustwallet/assets@master/blockchains/${folder}/info/logo.png`;
  }

  /** Multichain EVM balances via the gateway portfolio endpoint. When `token`
   *  is given, keep only that asset (ETH → native ETH on each chain); otherwise
   *  return every non-spam holding. One row per (chain, token). */
  private async fetchEvmBalances(addresses: string[], token: string | null): Promise<BalanceResult[]> {
    const want = token?.toUpperCase() ?? null;
    const rows: BalanceResult[] = [];
    const portfolios = await Promise.all(
      addresses.map((a) => firstValueFrom(this.evmPortfolio.getPortfolio(a).pipe(timeout(15000))).catch(() => null)),
    );
    for (const pf of portfolios) {
      for (const t of pf?.tokens ?? []) {
        if (t.spam) continue;
        if (want) {
          // "ETH" means the native gas asset, not every ERC-20 that shares the
          // ticker; match native by symbol, or an exact ERC-20 symbol hit.
          const sym = (t.symbol || '').toUpperCase();
          if (sym !== want) continue;
        }
        const chainLabel = QueryCardComponent.EVM_CHAIN_LABEL[t.chain] ?? t.chain;
        rows.push({
          token: chainLabel,
          symbol: t.symbol || '?',
          balance: t.uiAmount,
          value: t.valueUsd,
          change24h: 0,
          logoUri: t.logo ?? null,
          chain: t.chain,
        });
      }
    }
    // Biggest holdings first so the answer leads with where the money is.
    rows.sort((a, b) => b.value - a.value || b.balance - a.balance);
    return rows;
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
        this.error.set('Could not load your positions right now — try again.');
        this.loading.set(false);
        return;
      }
      // The protocol named in the question is a FILTER, not just a heading.
      // It was only ever used for the title, so "my Jito positions" rendered
      // every protocol's positions under a Jito header — with pump.fun rewards
      // listed as a Jito position.
      const want = (this.query.params['protocol'] ?? '').trim().toLowerCase();
      const positions = this.portfolioService.protocolPositions()
        .filter(p => !want || p.protocolName.toLowerCase().includes(want)
          || want.includes(p.protocolName.toLowerCase()));

      this.positionResults = positions.flatMap(p =>
        p.positions.map(pos => ({
          protocol: p.protocolName,
          type: p.category.replace(/-/g, ' '),
          token: pos.tokens.map(t => t.symbol).join('+'),
          amount: pos.tokens[0]?.amount ?? 0,
          value: pos.totalUsdValue ?? 0,
          // Null, not zero. Nothing here measures a yield, and "0.0%" in an
          // APY column is a claim — the one number on the row a user would act
          // on, invented.
          apy: null,
          protocolLogoUri: p.protocolLogoUri ?? null,
          // The holding itself. The row used to show only a USD value, so the
          // amount — the thing the user actually holds — reached them only if
          // the model happened to repeat it in prose.
          tokens: pos.tokens.map(t => ({
            symbol: t.symbol,
            amount: t.amount,
            logoUri: t.logoUri ?? null,
          })),
        }))
      );
      this.loading.set(false);
      this.persistSnapshot();
    } catch {
      this.error.set('Could not load your positions right now — try again.');
      this.loading.set(false);
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
      this.error.set('Could not read network fees right now.');
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
        this.error.set('Could not load wallet info right now — try again.');
        this.loading.set(false);
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
      this.error.set('Could not build your tax report right now.');
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

  /**
   * Several protocol APIs (Meteora's portfolio endpoint among them) return
   * money as JSON *strings*. `String.prototype.toLocaleString` silently
   * ignores the options object and hands the string straight back, which is
   * how "$0.6551870858262281" reached the card — so coerce first.
   *
   * Sub-cent amounts are real here: unclaimed DLMM fees start in the
   * thousandths, and rounding them to "$0.00" reads as "nothing to claim".
   */
  formatUsd(n: number | string | null | undefined): string {
    const v = typeof n === 'number' ? n : Number(n ?? 0);
    if (!Number.isFinite(v)) return '$0.00';
    const abs = Math.abs(v);
    const maxDigits = abs > 0 && abs < 0.01 ? 6 : 2;
    const body = abs.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDigits,
    });
    return `${v < 0 ? '-' : ''}$${body}`;
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
      [/solend/, 'solend.svg'],
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
