import { Injectable, NgZone, inject, signal, computed } from '@angular/core';
import { SolanaRpcService } from './solana-rpc.service';
import { PriceService } from './price.service';
import { BirdeyeService, type BirdeyeTokenPrice } from './birdeye.service';
import { HeliusService } from './helius.service';
import { ProtocolDetectionService } from './protocol-detection.service';
import { DefiPositionsService } from './defi-positions.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { LiveYieldsService } from './live-yields.service';
import { PortfolioAnalyticsService } from './portfolio-analytics.service';
import type {
  PortfolioSummary,
  DefiPositions,
  RecentTransaction,
  EnhancedTransaction,
  EnhancedTokenAccount,
  NftAsset,
  NftCollection,
  ProtocolPosition,
  PortfolioValueChange,
  PortfolioTab,
  LoadingState,
  TransactionType,
} from '../models/portfolio.models';
import type { HeliusParsedTransaction } from '../models/helius.models';

const SOL_MINT = 'So11111111111111111111111111111111111111112';
const LAMPORTS_PER_SOL = 1_000_000_000;

@Injectable({ providedIn: 'root' })
export class PortfolioService {
  private readonly ngZone = inject(NgZone);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly priceService = inject(PriceService);
  private readonly birdeyeService = inject(BirdeyeService);
  private readonly heliusService = inject(HeliusService);
  private readonly protocolDetection = inject(ProtocolDetectionService);
  private readonly defiPositionsService = inject(DefiPositionsService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly liveYields = inject(LiveYieldsService);
  private readonly analytics = inject(PortfolioAnalyticsService);

  // ──── Core State ────
  private readonly _summary = signal<PortfolioSummary | null>(null);
  private readonly _defiPositions = signal<DefiPositions | null>(null);
  private readonly _recentTransactions = signal<RecentTransaction[]>([]);
  private readonly _loadingState = signal<LoadingState>('idle');
  private readonly _error = signal<string | null>(null);

  // ──── New State ────
  private readonly _activeTab = signal<PortfolioTab>('portfolio');
  private readonly _protocolPositions = signal<ProtocolPosition[]>([]);
  // Independent loading state for the multi-protocol DeFi fan-out. Stays
  // 'loading' until *every* per-protocol fetch settles so the DeFi tab can
  // show a skeleton instead of jumping to "No positions found" the moment
  // the cheap LST scan returns but the slower lend/LP/perp calls are still
  // in flight.
  private readonly _protocolPositionsLoading = signal<boolean>(false);
  private readonly _portfolioChange = signal<PortfolioValueChange | null>(null);
  private readonly _nfts = signal<NftAsset[]>([]);
  private readonly _nftCollections = signal<NftCollection[]>([]);
  private readonly _nftLoadingState = signal<LoadingState>('idle');
  private readonly _enhancedTransactions = signal<EnhancedTransaction[]>([]);
  private readonly _historyLoadingState = signal<LoadingState>('idle');
  private readonly _historyHasMore = signal<boolean>(false);
  private readonly _historyLoadingMore = signal<boolean>(false);
  private readonly _historyCache = new Map<string, { transactions: EnhancedTransaction[]; hasMore: boolean }>();

  // Track which tabs have been loaded
  private nftsLoaded = false;
  private historyLoaded = false;
  private historyLoadedWallet: string | null = null;
  private historyLoadingPromise: Promise<void> | null = null;
  private static readonly HISTORY_PAGE_SIZE = 15;
  // Cache token symbols for swap descriptions
  private readonly _tokenSymbolCache = new Map<string, string>();
  // Mints encountered while parsing history rows that weren't in the
  // wallet's token list — flushed in batches so subsequent renders carry
  // symbol + logo + price for those mints.
  private readonly pendingMetadataMints = new Set<string>();
  private metadataFlushTimer: ReturnType<typeof setTimeout> | null = null;

  // ──── Public Signals ────
  readonly summary = this._summary.asReadonly();
  readonly defiPositions = this._defiPositions.asReadonly();
  readonly recentTransactions = this._recentTransactions.asReadonly();
  readonly loadingState = this._loadingState.asReadonly();
  readonly error = this._error.asReadonly();
  readonly activeTab = this._activeTab.asReadonly();
  readonly protocolPositions = this._protocolPositions.asReadonly();
  readonly protocolPositionsLoading = this._protocolPositionsLoading.asReadonly();
  readonly portfolioChange = this._portfolioChange.asReadonly();
  readonly nfts = this._nfts.asReadonly();
  readonly nftCollections = this._nftCollections.asReadonly();
  readonly nftLoadingState = this._nftLoadingState.asReadonly();
  readonly enhancedTransactions = this._enhancedTransactions.asReadonly();
  readonly historyLoadingState = this._historyLoadingState.asReadonly();
  readonly historyHasMore = this._historyHasMore.asReadonly();
  readonly historyLoadingMore = this._historyLoadingMore.asReadonly();

  readonly isLoading = computed(() => this._loadingState() === 'loading');
  readonly isLoaded = computed(() => this._loadingState() === 'loaded');

  setActiveTab(tab: PortfolioTab, walletAddress: string | null): void {
    this._activeTab.set(tab);

    if (!walletAddress) return;

    if (tab === 'nfts' && !this.nftsLoaded) {
      this.loadNfts(walletAddress);
    }
    if (tab === 'history' && (!this.historyLoaded || this.historyLoadedWallet !== walletAddress)) {
      this.loadEnhancedHistory(walletAddress);
    }
  }

  async loadPortfolio(walletAddress: string): Promise<void> {
    this._loadingState.set('loading');
    this._error.set(null);

    try {
      // Fetch RPC data in parallel, each with its own fallback
      const [balanceLamports, rawTokens, stakeAccounts, signatures] =
        await Promise.all([
          this.solanaRpc.getBalance(walletAddress).catch(() => 0),
          this.solanaRpc.getTokenAccounts(walletAddress).catch(() => []),
          this.solanaRpc.getStakeAccounts(walletAddress).catch(() => []),
          this.solanaRpc.getRecentSignatures(walletAddress).catch(() => []),
        ]);

      // Fetch metadata + prices (Birdeye: prices AND 24h change in one call)
      const allMints = [SOL_MINT, ...rawTokens.map((t) => t.mint)];
      const [tokenList, birdeyeData, , costBasis] = await Promise.all([
        this.priceService.getTokenList(),
        this.birdeyeService.getTokenPrices(allMints).catch(() => new Map<string, BirdeyeTokenPrice>()),
        // Warm DefiLlama LST APYs so the inline "5.66%" badge next to JitoSOL/
        // JupSOL/mSOL etc. shows the *live* DefiLlama rate on first paint,
        // rather than the static fallback in LST_REGISTRY.
        this.liveYields.ensureLoaded().catch(() => null),
        // Persisted cost-basis snapshot from chat-service. First load on a
        // wallet returns [] (no rows yet) and we fire `refreshCostBasis`
        // below to kick the backfill — subsequent visits paint the PnL
        // column on first frame.
        this.analytics.getCostBasis(walletAddress),
      ]);

      // Fire-and-forget incremental sync. Server-side debounced 5min so
      // hammering the page won't fan out duplicate Helius walks.
      this.analytics.refreshCostBasis(walletAddress);

      // Build a mint → cost-basis lookup once so the per-token enrichment
      // loop below is O(1) instead of O(n²).
      const costBasisByMint = new Map(costBasis.map((c) => [c.mint, c]));

      // Populate symbol cache for swap descriptions (Jupiter + DexScreener)
      this._tokenSymbolCache.clear();
      for (const [mint, meta] of tokenList) {
        this._tokenSymbolCache.set(mint, meta.symbol);
      }
      for (const [mint, meta] of this.birdeyeService.getAllTokenMeta()) {
        if (!this._tokenSymbolCache.has(mint)) {
          this._tokenSymbolCache.set(mint, meta.symbol);
        }
      }

      // Extract prices and 24h changes from unified Birdeye response
      const prices = new Map<string, number>();
      const priceChanges = new Map<string, number>();
      for (const [mint, data] of birdeyeData) {
        prices.set(mint, data.price);
        if (data.change24h !== null) {
          priceChanges.set(mint, data.change24h);
        }
      }

      const solPrice = prices.get(SOL_MINT) ?? null;
      const solBalance = balanceLamports / LAMPORTS_PER_SOL;
      const solUsdValue = solPrice !== null ? solBalance * solPrice : null;

      // Strip invisible / placeholder symbols that some indexers ship for
      // memecoins (Hangul Filler `ㅤ`, ZWJ `‍`, ZWSP `​`,
      // RTL/LTR marks, etc.). They render as a blank second line under the
      // token name and look like the row simply has no ticker. Treat
      // anything that has no visible characters as "no symbol available"
      // so the fallback chain (truncated mint) kicks in.
      const cleanSymbol = (s: string | null | undefined): string | null => {
        if (!s) return null;
        // Strip whitespace + zero-width + Hangul filler + bidi marks.
        const trimmed = s.replace(/[\s​-‏‪-‮⁠ㅤ﻿]/g, '');
        return trimmed.length > 0 ? trimmed : null;
      };

      // Spam-token signatures — these patterns reliably identify airdropped
      // scam tokens. We classify a token as spam when ANY signal fires;
      // keep the regex liberal because the cost of false-negatives (user
      // sees a fake $X balance) is much higher than false-positives (user
      // toggles "show all" and finds their real meme).
      // Examples flagged by these rules: tokens with invisible-only symbol
      // (the Hangul-filler "autistic genius intelligence" the user
      // reported), URLs in name (`claim-reward.com`), promo wording
      // (`FREE 100 SOL`, `Claim Airdrop Now`), or symbols that resolve to
      // a truncated mint after every metadata fallback (no aggregator knew
      // what the token was).
      const SPAM_KEYWORDS = /(claim|airdrop|visit|reward|bonus|free|giveaway|winner|gift)/i;
      const URL_PATTERN = /(https?:\/\/|www\.|\.com\b|\.io\b|\.ru\b|\.xyz\b|\.app\b|\.gg\b|t\.me\/|telegram)/i;
      const detectSpam = (
        rawDexSymbol: string | null | undefined,
        rawJupSymbol: string | null | undefined,
        finalSymbol: string,
        finalName: string,
      ): { spam: boolean; reason: string } => {
        // Strongest signal: DexScreener / Jupiter returned a symbol but
        // every visible char got stripped — only invisible glyphs.
        if (rawDexSymbol && !cleanSymbol(rawDexSymbol)) {
          return { spam: true, reason: 'invisible-symbol' };
        }
        if (rawJupSymbol && !cleanSymbol(rawJupSymbol)) {
          return { spam: true, reason: 'invisible-symbol' };
        }
        // URL in name or symbol — promo dust airdrop pattern.
        if (URL_PATTERN.test(finalName) || URL_PATTERN.test(finalSymbol)) {
          return { spam: true, reason: 'url-in-metadata' };
        }
        // Promo keywords in name — "Claim", "Free", "Airdrop" wording.
        if (SPAM_KEYWORDS.test(finalName) || SPAM_KEYWORDS.test(finalSymbol)) {
          return { spam: true, reason: 'promo-keyword' };
        }
        return { spam: false, reason: '' };
      };

      // Build enhanced tokens with 24h change + protocol classification
      // Metadata resolution: Jupiter strict → DexScreener (already fetched) → Jupiter individual
      const rawEnhanced: EnhancedTokenAccount[] = rawTokens.map((raw) => {
        const jupMeta = tokenList.get(raw.mint);
        const dexMeta = this.birdeyeService.getTokenMeta(raw.mint);
        const usdPrice = prices.get(raw.mint) ?? null;
        const usdValue = usdPrice !== null ? raw.balance * usdPrice : null;
        const classification = this.protocolDetection.classifyToken(raw.mint);

        // 4-layer fallback: Jupiter strict → DexScreener → cleaned-symbol → truncated mint
        const symbol = cleanSymbol(jupMeta?.symbol)
          ?? cleanSymbol(dexMeta?.symbol)
          ?? (raw.mint.slice(0, 4) + '...');
        const name = jupMeta?.name ?? dexMeta?.name ?? 'Unknown Token';
        const logoUri = jupMeta?.logoURI ?? dexMeta?.imageUrl ?? null;

        // Spam detection runs against the *raw* indexer payloads (so we
        // can detect invisible-only symbols before they're sanitized
        // away) plus the final resolved name/symbol (for URL + keyword
        // matches). Jupiter strict-listed tokens skip the check entirely
        // — if Jupiter verified it, it's not spam.
        const { spam, reason } = jupMeta
          ? { spam: false, reason: '' }
          : detectSpam(dexMeta?.symbol, undefined, symbol, name);

        // LST inline-badge APY: prefer the live DefiLlama rate (warmed in
        // the parallel fetch above) and fall back to the static default in
        // LST_REGISTRY if DefiLlama is missing the project. The CTA path
        // ("Get X% APY → JitoSOL") has been removed from the UI per design
        // direction — LSTs surface their real yield via the inline badge
        // and that's it.
        const lstApy = classification.isLst
          ? (this.liveYields.getApyByMint(raw.mint) ?? this.protocolDetection.getLstDefaultApy(raw.mint))
          : null;

        // All-time PnL from the persisted cost-basis snapshot. Null when
        // the wallet has never been synced (first visit) or when the mint
        // has no recorded purchases — the column renders "—" in those
        // cases rather than a fake "+$0.00".
        const basis = costBasisByMint.get(raw.mint);
        const pnl = basis
          ? this.analytics.computePnl(basis, raw.balance, usdPrice)
          : null;

        return {
          mint: raw.mint,
          symbol,
          name,
          logoUri,
          balance: raw.balance,
          decimals: raw.decimals,
          usdPrice,
          usdValue,
          priceChange24h: priceChanges.get(raw.mint) ?? null,
          allocationPercent: 0, // computed below
          isLiquidStaking: classification.isLst,
          protocol: classification.protocol,
          isSuspectedSpam: spam,
          spamReason: reason,
          apy: lstApy,
          pnlAllTimeUsd: pnl?.totalUsd ?? null,
          pnlAllTimePct: pnl?.totalPct ?? null,
        };
      });

      // Duplicate-symbol pass — when two or more *different* mints share
      // the same visible symbol AND none are Jupiter-verified, both are
      // almost certainly drop variants of the same scam (the "zort1234_"
      // duplicate the user reported). Mark both copies as spam so the
      // filter catches the whole cluster, not just whichever one ranks
      // lower in the table.
      const symbolBuckets = new Map<string, EnhancedTokenAccount[]>();
      for (const t of rawEnhanced) {
        if (tokenList.get(t.mint)) continue; // strict-listed → skip
        if (t.symbol.endsWith('...')) continue; // truncated-mint sentinel — not a real symbol clash
        const key = t.symbol.toLowerCase();
        const bucket = symbolBuckets.get(key) ?? [];
        bucket.push(t);
        symbolBuckets.set(key, bucket);
      }
      for (const bucket of symbolBuckets.values()) {
        if (bucket.length < 2) continue;
        for (const t of bucket) {
          t.isSuspectedSpam = true;
          t.spamReason = t.spamReason || 'duplicate-symbol';
        }
      }

      // For tokens still missing metadata, try Jupiter individual token API
      const stillMissing = rawEnhanced
        .filter((t) => t.name === 'Unknown Token')
        .map((t) => t.mint);

      if (stillMissing.length > 0) {
        const extraMeta = await this.priceService.getTokensMetadata(stillMissing);
        for (const token of rawEnhanced) {
          const meta = extraMeta.get(token.mint);
          if (meta) {
            token.symbol = meta.symbol;
            token.name = meta.name;
            token.logoUri = meta.logoURI ?? token.logoUri;
            this._tokenSymbolCache.set(token.mint, meta.symbol);
          }
        }
      }

      // Final pass: fill any still-missing logos / names from the chat-side
      // TokenRegistry (Jupiter token API, deeper coverage). Triggers async
      // resolves so the next render warms up. This catches the JitoSOL,
      // JupSOL, $WIF cases where Birdeye returns price but no logo, plus the
      // long tail of mints where the symbol stays as "6pwS..." truncated.
      for (const token of rawEnhanced) {
        if (token.logoUri && token.name !== 'Unknown Token' && !token.symbol.endsWith('...')) {
          continue;
        }
        const meta = this.tokenRegistry.getToken(token.mint);
        if (meta) {
          if (!token.logoUri && meta.logoURI) token.logoUri = meta.logoURI;
          if ((token.name === 'Unknown Token' || !token.name) && meta.name) token.name = meta.name;
          if (token.symbol.endsWith('...') && meta.symbol) {
            token.symbol = meta.symbol;
            this._tokenSymbolCache.set(token.mint, meta.symbol);
          }
        } else {
          this.tokenRegistry.resolveAsync(token.mint);
        }
      }

      // Last-resort metadata pass: anything still showing as truncated /
      // 'Unknown Token' (Pump.fun launches, deep-cap memecoins) gets a
      // single Helius getAssetBatch call against the on-chain Metaplex
      // metadata. This is the canonical source for SPL token identity, so
      // it works even when no aggregator has indexed the mint yet.
      const heliusMissing = rawEnhanced
        .filter(t => !t.logoUri || t.name === 'Unknown Token' || t.symbol.endsWith('...'))
        .map(t => t.mint);

      if (heliusMissing.length > 0) {
        const assetMeta = await this.heliusService.getAssetBatch(heliusMissing);
        for (const token of rawEnhanced) {
          const meta = assetMeta.get(token.mint);
          if (!meta) continue;
          if (!token.logoUri && meta.logoUri) token.logoUri = meta.logoUri;
          if ((token.name === 'Unknown Token' || !token.name) && meta.name) token.name = meta.name;
          const cleanedSym = cleanSymbol(meta.symbol);
          if ((token.symbol.endsWith('...') || !token.symbol) && cleanedSym) {
            token.symbol = cleanedSym;
            this._tokenSymbolCache.set(token.mint, cleanedSym);
          }
        }
      }

      // Compute allocation percentages — spam tokens excluded from the
      // headline total + donut so a $149 fake "autistic genius intelligence"
      // doesn't inflate the portfolio reading. They stay in `tokens[]`
      // with the flag set so the user can still see them via the toggle.
      const tokensUsdTotal = rawEnhanced
        .filter((t) => !t.isSuspectedSpam)
        .reduce((sum, t) => sum + (t.usdValue ?? 0), 0);
      const totalPortfolioValue = (solUsdValue ?? 0) + tokensUsdTotal;

      const tokens = rawEnhanced
        .map((t) => ({
          ...t,
          allocationPercent:
            totalPortfolioValue > 0 ? ((t.usdValue ?? 0) / totalPortfolioValue) * 100 : 0,
        }))
        .sort((a, b) => (b.usdValue ?? 0) - (a.usdValue ?? 0));

      const solChange24h = priceChanges.get(SOL_MINT) ?? null;
      const solAllocationPercent = totalPortfolioValue > 0 ? ((solUsdValue ?? 0) / totalPortfolioValue) * 100 : 0;
      // Native SOL cost basis covers every buy/sell across the wallet's
      // history. Wrapped-SOL transfers are accounted for under the same
      // mint by `portfolio_analytics.py`, so a single lookup nets it all.
      const solBasis = costBasisByMint.get(SOL_MINT);
      const solPnl = solBasis ? this.analytics.computePnl(solBasis, solBalance, solPrice) : null;

      this._summary.set({
        walletAddress,
        solBalance: {
          lamports: balanceLamports,
          sol: solBalance,
          usdPrice: solPrice,
          usdValue: solUsdValue,
          priceChange24h: solChange24h,
          allocationPercent: solAllocationPercent,
          pnlAllTimeUsd: solPnl?.totalUsd ?? null,
          pnlAllTimePct: solPnl?.totalPct ?? null,
        },
        tokens,
        totalUsdValue: totalPortfolioValue,
      });

      // ──── Staking ────
      const stakePositions = stakeAccounts.map((sa) => ({
        ...sa,
        stakedSol: sa.stakedLamports / LAMPORTS_PER_SOL,
        usdValue:
          solPrice !== null
            ? (sa.stakedLamports / LAMPORTS_PER_SOL) * solPrice
            : null,
      }));

      const totalStakedSol = stakePositions.reduce(
        (sum, p) => sum + p.stakedSol,
        0
      );

      this._defiPositions.set({
        stakePositions,
        totalStakedSol,
        totalStakedUsdValue:
          solPrice !== null ? totalStakedSol * solPrice : null,
      });

      // ──── 24h Portfolio Change ────
      // Computed up-front (right after we know every priced holding +
      // staked SOL) so the "+$X.XX (X.XX%) 24h" indicator paints with the
      // hero card on first render instead of waiting on the protocol fan-
      // out. DeFi positions don't move the math meaningfully for liquid
      // pairs that are mostly already in the wallet token list, and the
      // protocols that *do* swing day-over-day (perp/leverage PnL) need
      // their own dedicated indicator anyway.
      this.applyDailyChange(solChange24h, solUsdValue, tokens, totalStakedSol, solPrice);

      // ──── Protocol Positions (streaming) ────
      // Each per-protocol fetch updates the signal as it resolves so the
      // UI fills in progressively — the user sees Meteora the moment it
      // returns instead of blocking on the slowest call. Previously we
      // awaited `getLpPositions` (Raydium pairs is ~7MB JSON) *before* the
      // parallel fan-out, gating every protocol behind it. That alone
      // could stretch first paint past 20s on a slow network.
      this._protocolPositionsLoading.set(true);
      const accumulated: ProtocolPosition[] = [];

      const emit = () => this._protocolPositions.set([...accumulated]);

      // Native staking + LST resolve synchronously / from already-loaded
      // wallet data — push them up front so the DeFi section paints
      // immediately, even while the network fetches are still in flight.
      const solLogo = this.protocolDetection.getSolLogo();
      // Live SOL inflation rate → native staking APY. Pulled in parallel
      // with the protocol stream below; defaults to 7.0% (recent epoch
      // baseline) if the RPC misbehaves so we never render "—" for a
      // position the user actively earns on.
      const nativeStakeApy = await this.solanaRpc
        .getNativeStakingApr()
        .catch(() => 7.0);
      if (stakePositions.length > 0) {
        const stakingUsd = solPrice !== null ? totalStakedSol * solPrice : 0;
        accumulated.push({
          protocolId: 'solana-staking',
          protocolName: 'Solana Staking',
          protocolLogoUri: solLogo,
          category: 'native-staking',
          positions: stakePositions.map((sp) => ({
            label: `Validator ${sp.validatorVoteAccount.slice(0, 6)}...`,
            tokens: [{ symbol: 'SOL', amount: sp.stakedSol, logoUri: solLogo }],
            totalUsdValue: sp.usdValue,
            metadata: { status: sp.status },
            apy: nativeStakeApy,
          })),
          totalUsdValue: stakingUsd,
        });
      }
      // LiveYields was already warmed in the parallel fetch above (covers
      // both the token-list LST badges and the protocol-position APYs).
      const lstPositions = this.defiPositionsService.getLiquidStakingPositions(tokens, solPrice);
      accumulated.push(...lstPositions);
      emit();

      // Per-fetch timeout — 10s caps "indexer slow" without giving up too
      // early on legitimate first-call cold paths. A protocol that misses
      // this window simply doesn't appear; the rest of the DeFi tab still
      // resolves on time.
      const withTimeout = <T>(p: Promise<T>, fallback: T, ms = 10_000): Promise<T> =>
        Promise.race([
          p,
          new Promise<T>(resolve => setTimeout(() => resolve(fallback), ms)),
        ]);

      // Atomic render mode (Jupiter-portfolio style): kick off every
      // protocol fetcher in parallel, then commit the merged result in a
      // single signal write. Previously each fetcher emitted as it
      // resolved which made the DeFi tab visibly "pop in" piece by piece
      // — user feedback was that they want one consolidated render.
      const fetchOne = (p: Promise<ProtocolPosition[]>): Promise<ProtocolPosition[]> =>
        withTimeout(p.catch(() => []), []);

      const protocolBatches = await Promise.all([
        fetchOne(this.defiPositionsService.getLpPositions(walletAddress, tokens)),
        fetchOne(this.defiPositionsService.getLendingPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getKaminoPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getMarginFiPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getOrcaPositions()),
        fetchOne(this.defiPositionsService.getRaydiumClmmPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getMeteoraPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getDriftPositions(walletAddress)),
        fetchOne(this.defiPositionsService.getStreamflowPositions(walletAddress)),
        // Jupiter Portfolio aggregator — covers Jupiter products (DCA, limit,
        // perp, lend, JUP / JupSOL stake, LP) that none of the per-protocol
        // fetchers above pick up.
        fetchOne(this.defiPositionsService.getJupiterPortfolioPositions(walletAddress)),
        // Pump.fun creator rewards — stubbed in PR 1, real on-chain decode in PR 3.
        fetchOne(this.defiPositionsService.getPumpfunRewards(walletAddress)),
      ]);

      for (const batch of protocolBatches) {
        if (batch.length > 0) accumulated.push(...batch);
      }

      // Pricing pass before the single render so APR + claimable columns
      // all paint on first frame instead of arriving as a delta.
      await Promise.race([
        this.defiPositionsService.priceAllPositions(accumulated),
        new Promise<void>(resolve => setTimeout(resolve, 6_000)),
      ]);

      emit();
      this._protocolPositionsLoading.set(false);

      this._recentTransactions.set(signatures);
      this._loadingState.set('loaded');

      // Pre-load enhanced history using already-fetched signatures (avoid duplicate RPC call)
      this.preloadEnhancedHistory(walletAddress, signatures).catch(() => {});
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to load portfolio';
      this._error.set(message);
      this._loadingState.set('error');
      this._protocolPositionsLoading.set(false);
    }
  }

  // ──── Lazy NFT Loading ────
  async loadNfts(walletAddress: string): Promise<void> {
    this._nftLoadingState.set('loading');
    try {
      const assets = await this.heliusService.getAssetsByOwner(walletAddress);

      const nfts: NftAsset[] = assets.map((asset) => {
        const image =
          asset.content.links?.image ??
          asset.content.files?.[0]?.uri ??
          null;

        const collection = asset.grouping.find((g) => g.group_key === 'collection');

        return {
          id: asset.id,
          name: asset.content.metadata.name || 'Unknown NFT',
          imageUri: image,
          collectionName: collection?.collection_metadata?.name ?? null,
          collectionId: collection?.group_value ?? null,
          floorPrice: collection?.floor_price ?? null,
          compressed: asset.compression?.compressed ?? false,
        };
      });

      // Group into collections
      const collectionMap = new Map<string, NftAsset[]>();
      const uncollected: NftAsset[] = [];

      for (const nft of nfts) {
        if (nft.collectionId) {
          const group = collectionMap.get(nft.collectionId) ?? [];
          group.push(nft);
          collectionMap.set(nft.collectionId, group);
        } else {
          uncollected.push(nft);
        }
      }

      const collections: NftCollection[] = [];
      for (const [id, items] of collectionMap) {
        const first = items[0];
        collections.push({
          id,
          name: first.collectionName ?? 'Unknown Collection',
          imageUri: items[0]?.imageUri ?? null,
          floorPrice: first.floorPrice,
          items,
        });
      }

      if (uncollected.length > 0) {
        collections.push({
          id: '__uncollected__',
          name: 'Uncollected',
          imageUri: null,
          floorPrice: null,
          items: uncollected,
        });
      }

      this.ngZone.run(() => {
        this._nfts.set(nfts);
        this._nftCollections.set(collections);
        this._nftLoadingState.set('loaded');
      });
      this.nftsLoaded = true;
    } catch {
      this.ngZone.run(() => {
        this._nftLoadingState.set('error');
      });
    }
  }

  // ──── Enhanced History Loading ────

  /**
   * Called from loadPortfolio() with already-fetched signatures to avoid duplicate RPC call.
   * Shares the same historyLoadingPromise so setActiveTab can await or skip.
   */
  private preloadEnhancedHistory(
    walletAddress: string,
    rawSignatures: Array<{ signature: string; blockTime: number | null; success: boolean; memo: string | null }>
  ): Promise<void> {
    if (this.historyLoaded && this.historyLoadedWallet === walletAddress) {
      return Promise.resolve();
    }
    if (this.historyLoadingPromise) {
      return this.historyLoadingPromise;
    }

    this.ngZone.run(() => this._historyLoadingState.set('loading'));

    const loadPromise = (async () => {
      try {
        const pageSize = PortfolioService.HISTORY_PAGE_SIZE;
        const trimmed = rawSignatures.slice(0, pageSize);
        const enhanced = await this.parseSignatureBatch(trimmed);
        this.ngZone.run(() => {
          this._enhancedTransactions.set(enhanced);
          this._historyHasMore.set(rawSignatures.length >= pageSize);
          this._historyLoadingState.set('loaded');
        });
        this.historyLoaded = true;
        this.historyLoadedWallet = walletAddress;
        this._historyCache.set(walletAddress, {
          transactions: enhanced,
          hasMore: rawSignatures.length >= pageSize,
        });
      } catch {
        this.ngZone.run(() => {
          this._historyLoadingState.set('error');
        });
      } finally {
        this.historyLoadingPromise = null;
      }
    })();

    this.historyLoadingPromise = loadPromise;
    return loadPromise;
  }

  /**
   * Called from setActiveTab() when History tab is clicked.
   * Reuses existing promise if preload is in progress.
   */
  async loadEnhancedHistory(walletAddress: string): Promise<void> {
    // If already loading, reuse existing promise
    if (this.historyLoadingPromise) {
      return this.historyLoadingPromise;
    }

    // Already loaded for this wallet
    if (this.historyLoaded && this.historyLoadedWallet === walletAddress) {
      return;
    }

    const cached = this._historyCache.get(walletAddress);
    if (cached && cached.transactions.length > 0) {
      this.ngZone.run(() => {
        this._enhancedTransactions.set(cached.transactions);
        this._historyHasMore.set(cached.hasMore);
        this._historyLoadingState.set('loaded');
      });
      this.historyLoaded = true;
      this.historyLoadedWallet = walletAddress;
      return;
    }

    this.ngZone.run(() => {
      this._historyLoadingState.set('loading');
      this._historyHasMore.set(false);
    });

    const loadPromise = (async () => {
      try {
        const pageSize = PortfolioService.HISTORY_PAGE_SIZE;
        const signatures = await this.solanaRpc
          .getRecentSignatures(walletAddress, pageSize)
          .catch(() => []);

        const enhanced = await this.parseSignatureBatch(signatures);
        this.ngZone.run(() => {
          this._enhancedTransactions.set(enhanced);
          this._historyHasMore.set(signatures.length >= pageSize);
          this._historyLoadingState.set('loaded');
        });
        this.historyLoaded = true;
        this.historyLoadedWallet = walletAddress;
        this._historyCache.set(walletAddress, {
          transactions: enhanced,
          hasMore: signatures.length >= pageSize,
        });
      } catch {
        this.ngZone.run(() => {
          this._historyLoadingState.set('error');
        });
      } finally {
        this.historyLoadingPromise = null;
      }
    })();

    this.historyLoadingPromise = loadPromise;
    return loadPromise;
  }

  async loadMoreHistory(walletAddress: string): Promise<void> {
    if (this._historyLoadingMore() || !this._historyHasMore()) return;

    const current = this._enhancedTransactions();
    const lastSig = current[current.length - 1]?.signature;
    if (!lastSig) return;

    this._historyLoadingMore.set(true);

    try {
      const pageSize = PortfolioService.HISTORY_PAGE_SIZE;
      const moreSignatures = await this.solanaRpc
        .getRecentSignatures(walletAddress, pageSize, lastSig)
        .catch(() => []);

      const enhanced = await this.parseSignatureBatch(moreSignatures);
      this.ngZone.run(() => {
        if (enhanced.length > 0) {
          const seen = new Set(current.map((tx) => tx.signature));
          const merged = [...current];
          for (const tx of enhanced) {
            if (seen.has(tx.signature)) continue;
            merged.push(tx);
          }
          this._enhancedTransactions.set(merged);
          this._historyCache.set(walletAddress, {
            transactions: merged,
            hasMore: moreSignatures.length >= pageSize,
          });
        }
        this._historyHasMore.set(moreSignatures.length >= pageSize);
      });
    } catch {
      // Ignore — keep existing history
    } finally {
      this.ngZone.run(() => {
        this._historyLoadingMore.set(false);
      });
    }
  }

  private async parseSignatureBatch(
    signatures: Array<{ signature: string; blockTime: number | null; success: boolean }>
  ): Promise<EnhancedTransaction[]> {
    if (signatures.length === 0) return [];

    const sigs = signatures.map((s) => s.signature);
    const parsed = await this.heliusService.parseTransactions(sigs);
    const heliusByID = new Map(parsed.map(p => [p.signature, p]));

    // For any signature Helius didn't return (most common cause: tx is
    // newer than Helius's indexer cursor), fall back to a per-tx RPC
    // `getTransaction(jsonParsed)` call so the row at least carries the
    // protocol name + token amount + USD instead of all-blank "ACTION".
    // Capped to avoid hammering the RPC on a freshly-active wallet.
    const missingSigs = sigs.filter(s => !heliusByID.has(s));
    const RPC_FALLBACK_CAP = 10;
    const rpcParsed = new Map<string, EnhancedTransaction>();
    if (missingSigs.length > 0) {
      const slice = missingSigs.slice(0, RPC_FALLBACK_CAP);
      const meta = new Map(signatures.map(s => [s.signature, s]));
      const results = await Promise.all(
        slice.map(async sig => {
          const raw = await this.solanaRpc.getParsedTransaction(sig);
          if (!raw) return null;
          return this.mapRpcTx(sig, meta.get(sig) ?? null, raw);
        }),
      );
      for (const r of results) if (r) rpcParsed.set(r.signature, r);
    }

    const out = signatures.map(sig => {
      const helius = heliusByID.get(sig.signature);
      if (helius) return this.mapHeliusTx(helius);
      const rpc = rpcParsed.get(sig.signature);
      if (rpc) return rpc;
      return {
        signature: sig.signature,
        blockTime: sig.blockTime,
        success: sig.success,
        type: 'unknown' as TransactionType,
        description: sig.signature.slice(0, 8) + '...',
        details: null,
        platform: null,
      };
    });

    // Background flush of unknown mints encountered during RPC parse.
    // Debounced so consecutive parseSignatureBatch calls (initial load +
    // load-more) coalesce into one Helius getAssetBatch + Birdeye prices
    // request, then re-emit the enhanced transactions with the resolved
    // symbol/logo/USD baked in.
    if (this.pendingMetadataMints.size > 0) {
      this.scheduleMetadataFlush();
    }

    return out;
  }

  private scheduleMetadataFlush(): void {
    if (this.metadataFlushTimer) return;
    this.metadataFlushTimer = setTimeout(() => {
      this.metadataFlushTimer = null;
      void this.flushPendingMetadata();
    }, 600);
  }

  private async flushPendingMetadata(): Promise<void> {
    const mints = Array.from(this.pendingMetadataMints);
    if (!mints.length) return;
    this.pendingMetadataMints.clear();

    // Resolve metadata + price in parallel. Helius covers Pump.fun /
    // Metaplex names + icons; DexScreener (via BirdeyeService) and
    // Jupiter Lite handle USD prices for any liquid mint.
    const [assetMeta, priceMap] = await Promise.all([
      this.heliusService.getAssetBatch(mints),
      this.birdeyeService.getTokenPrices(mints).catch(() => new Map()),
    ]);

    for (const [mint, meta] of assetMeta) {
      if (meta.symbol) this._tokenSymbolCache.set(mint, meta.symbol);
    }

    // Re-emit the enhanced transactions with newly-resolved metadata
    // patched into each row so the table re-renders without a full reload.
    const txs = this._enhancedTransactions();
    if (!txs.length) return;
    let changed = false;
    const next = txs.map(tx => {
      const d = tx.details;
      if (!d?.tokenMint) return tx;
      const meta = assetMeta.get(d.tokenMint);
      const priceEntry = priceMap.get(d.tokenMint);
      if (!meta && !priceEntry) return tx;
      const symbol = d.tokenSymbol ?? meta?.symbol ?? null;
      const logoUri = d.tokenLogoUri ?? meta?.logoUri ?? null;
      const amount = d.fromAmount ?? d.toAmount ?? 0;
      const usdValue = d.usdValue ?? (priceEntry ? Math.abs(amount) * priceEntry.price : null);
      if (symbol === d.tokenSymbol && logoUri === d.tokenLogoUri && usdValue === d.usdValue) {
        return tx;
      }
      changed = true;
      return {
        ...tx,
        details: {
          ...d,
          tokenSymbol: symbol,
          tokenLogoUri: logoUri,
          usdValue,
          fromSymbol: d.fromSymbol ?? symbol,
          fromLogoUri: d.fromLogoUri ?? logoUri,
          fromUsdValue: d.fromUsdValue ?? usdValue,
        },
      };
    });
    if (changed) this.ngZone.run(() => this._enhancedTransactions.set(next));
  }

  /**
   * Best-effort RPC-based parser. Walks pre/post token balances + native SOL
   * balance deltas for the fee payer to surface the most relevant token
   * movement for that wallet. Recognises a handful of well-known program
   * IDs to attribute the platform when Helius's source field is absent.
   */
  private mapRpcTx(
    signature: string,
    sigMeta: { signature: string; blockTime: number | null; success: boolean } | null,
    raw: any,
  ): EnhancedTransaction {
    const message = raw?.transaction?.message ?? {};
    const accountKeys: string[] = (message.accountKeys ?? []).map((k: any) =>
      typeof k === 'string' ? k : k?.pubkey ?? '',
    );
    const feePayer = accountKeys[0] ?? null;

    // Program-id → friendly platform name. Order matters: more specific
    // routers (Jupiter/MagicEden) before generic AMMs (Raydium/Orca).
    const KNOWN_PROGRAMS: Record<string, { platform: string; type: TransactionType }> = {
      JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4: { platform: 'jupiter', type: 'swap' },
      JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB: { platform: 'jupiter', type: 'swap' },
      '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P': { platform: 'pump.fun', type: 'swap' },
      '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': { platform: 'raydium', type: 'swap' },
      whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc: { platform: 'orca', type: 'swap' },
      LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo: { platform: 'meteora', type: 'swap' },
      M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K: { platform: 'magic eden', type: 'nft-sale' },
      TSWAPaqyCSx2KABk68Shruf4rp7CxcNi8hAsbdwmHbN: { platform: 'tensor', type: 'nft-sale' },
      CRoSSzVxmtLn4VEkyVrQYcjC1JoYWaELxiD3wwQYzLNd: { platform: 'streamflow', type: 'transfer' },
    };

    let platform: string | null = null;
    let type: TransactionType = 'unknown';
    for (const key of accountKeys) {
      const known = KNOWN_PROGRAMS[key];
      if (known) {
        platform = known.platform;
        type = known.type;
        break;
      }
    }

    // Compute the *biggest* SPL movement that touches the fee payer (most
    // user-visible amount). Helius normally surfaces this as `tokenInputs[0]`
    // for swaps; we approximate by diffing pre/post token balances on the
    // fee payer's accounts.
    const preTokens: any[] = raw?.meta?.preTokenBalances ?? [];
    const postTokens: any[] = raw?.meta?.postTokenBalances ?? [];
    let movement: { mint: string; delta: number; decimals: number } | null = null;
    for (const post of postTokens) {
      if (post.owner !== feePayer) continue;
      const pre = preTokens.find(p =>
        p.accountIndex === post.accountIndex || (p.mint === post.mint && p.owner === post.owner),
      );
      const preAmt = parseFloat(pre?.uiTokenAmount?.uiAmountString ?? '0');
      const postAmt = parseFloat(post?.uiTokenAmount?.uiAmountString ?? '0');
      const delta = postAmt - preAmt;
      if (Math.abs(delta) < 1e-9) continue;
      if (!movement || Math.abs(delta) > Math.abs(movement.delta)) {
        movement = { mint: post.mint, delta, decimals: post.uiTokenAmount?.decimals ?? 0 };
      }
    }

    // Native SOL delta for fee payer (account 0)
    const preLamports = raw?.meta?.preBalances?.[0] ?? 0;
    const postLamports = raw?.meta?.postBalances?.[0] ?? 0;
    const fee = raw?.meta?.fee ?? 0;
    const nativeDelta = (postLamports - preLamports + fee) / LAMPORTS_PER_SOL;

    // Pick the more meaningful side for the headline: token movement if it
    // exists, otherwise native. Type heuristic: if we have a token movement
    // and a recognised swap program, leave the swap type; if we only see a
    // single transfer and platform is null, it's a transfer.
    if (type === 'unknown') {
      if (movement && Math.abs(nativeDelta) > 0.001) type = 'swap';
      else if (movement || Math.abs(nativeDelta) > 0.001) type = 'transfer';
    }

    const tokenMint = movement?.mint ?? (nativeDelta !== 0 ? SOL_MINT : null);
    const tokenSymbol = tokenMint ? this.resolveSymbol(tokenMint) ?? (tokenMint === SOL_MINT ? 'SOL' : null) : null;
    const tokenLogoUri = tokenMint ? this.resolveLogoUri(tokenMint) : null;
    // If the parser hit a mint that wasn't in the wallet's token list (the
    // user swapped *through* it but doesn't hold any), queue a metadata
    // resolve so subsequent renders can surface symbol/icon/USD instead
    // of leaving the row blank.
    if (tokenMint && tokenMint !== SOL_MINT && !tokenSymbol) {
      this.pendingMetadataMints.add(tokenMint);
    }
    const primaryAmount = movement?.delta ?? nativeDelta;
    const usdValue = this.estimateUsdValue(tokenMint, Math.abs(primaryAmount));

    return {
      signature,
      blockTime: sigMeta?.blockTime ?? raw?.blockTime ?? null,
      success: !raw?.meta?.err && (sigMeta?.success ?? true),
      type,
      description: type === 'swap' ? `Swap via ${platform ?? 'unknown'}`
                  : type === 'transfer' ? 'Transfer'
                  : 'Transaction',
      details: tokenMint ? {
        fromToken: primaryAmount < 0 ? tokenMint : null,
        toToken: primaryAmount > 0 ? tokenMint : null,
        fromAmount: primaryAmount < 0 ? Math.abs(primaryAmount) : null,
        toAmount: primaryAmount > 0 ? primaryAmount : null,
        counterparty: null,
        programName: platform,
        fromAddress: feePayer,
        toAddress: null,
        tokenMint,
        tokenSymbol,
        tokenLogoUri,
        usdValue,
        fromSymbol: tokenSymbol,
        fromLogoUri: tokenLogoUri,
        fromUsdValue: usdValue,
      } : null,
      platform,
    };
  }

  private mapHeliusTx(tx: HeliusParsedTransaction): EnhancedTransaction {
    let type = this.mapTxType(tx.type);
    const platform = this.inferPlatform(tx);
    let details;
    try {
      details = this.extractDetails(tx, platform);
    } catch {
      details = null;
    }
    // Helius returns type UNKNOWN for a lot of plain SPL/SOL movements
    // (token-account funding, simple sends, program interactions it doesn't
    // categorise). If extractDetails still decoded a concrete token/native
    // movement, classify the row as a transfer so it renders "TRANSFER" +
    // the amount instead of a meaningless "ACTION" with empty columns.
    if (type === 'unknown' && details &&
        (details.fromAmount != null || details.toAmount != null)) {
      // Two decoded legs with a recognised swap program → it's a swap that
      // Helius mislabelled; otherwise treat as a transfer.
      type = (details.toAmount != null && details.fromAmount != null && platform)
        ? 'swap'
        : 'transfer';
    }
    return {
      signature: tx.signature,
      blockTime: tx.timestamp ?? null,
      success: !tx.transactionError,
      type,
      description: tx.description || this.buildDescription(tx),
      details,
      platform,
    };
  }

  private inferPlatform(tx: HeliusParsedTransaction): string | null {
    const source = (tx.source || '').trim();
    if (source && source.toLowerCase() !== 'unknown') {
      // Normalize Helius source names (JUPITER → jupiter, SYSTEM_PROGRAM → system program)
      return source.toLowerCase().replace(/_/g, ' ');
    }

    const desc = (tx.description || '').toLowerCase();
    if (desc.includes('jupiter')) return 'jupiter';
    if (desc.includes('raydium')) return 'raydium';
    if (desc.includes('orca')) return 'orca';
    if (desc.includes('meteora')) return 'meteora';
    if (desc.includes('marinade')) return 'marinade';
    if (desc.includes('pump') || desc.includes('pump_fun')) return 'pump.fun';
    if (desc.includes('dflow')) return 'dflow';
    if (tx.events?.swap) return 'swap';
    return null;
  }

  private mapSignatureFallback(
    signatures: Array<{ signature: string; blockTime: number | null; success: boolean }>
  ): EnhancedTransaction[] {
    return signatures.map((s) => ({
      signature: s.signature,
      blockTime: s.blockTime,
      success: s.success,
      type: 'unknown' as TransactionType,
      description: s.signature.slice(0, 8) + '...',
      details: null,
      platform: null,
    }));
  }

  private mapTxType(heliusType: string): TransactionType {
    const map: Record<string, TransactionType> = {
      'TRANSFER': 'transfer',
      'SWAP': 'swap',
      'STAKE_SOL': 'stake',
      'UNSTAKE_SOL': 'unstake',
      'NFT_SALE': 'nft-sale',
      'NFT_BID': 'nft-purchase',
      'NFT_MINT': 'nft-mint',
      'TOKEN_MINT': 'token-mint',
      'BURN': 'burn',
      'VOTE': 'vote',
      'COMPRESSED_NFT_MINT': 'nft-mint',
    };
    return map[heliusType] ?? 'unknown';
  }

  private buildDescription(tx: HeliusParsedTransaction): string {
    const swap = tx.events?.swap;
    if (swap) {
      const fromAmt = this.formatSwapAmount(swap.nativeInput, swap.tokenInputs);
      const toAmt = this.formatSwapAmount(swap.nativeOutput, swap.tokenOutputs);
      const protocol = tx.source ? ` via ${tx.source}` : '';
      if (fromAmt && toAmt) {
        return `Swapped ${fromAmt} → ${toAmt}${protocol}`;
      }
      return `Token swap${protocol}`;
    }
    if (tx.nativeTransfers?.length) {
      const t = tx.nativeTransfers[0];
      const sol = t.amount / LAMPORTS_PER_SOL;
      return `Transferred ${sol.toFixed(4)} SOL`;
    }
    if (tx.tokenTransfers?.length) {
      const t = tx.tokenTransfers[0];
      const amt = t.tokenAmount ? t.tokenAmount.toFixed(4) : '';
      return `Transferred ${amt} tokens`;
    }
    return tx.type || 'Transaction';
  }

  private formatSwapAmount(
    native: { account: string; amount: string } | undefined,
    tokens: Array<{ mint: string; rawTokenAmount: { tokenAmount: string; decimals: number } }> | undefined
  ): string | null {
    if (native) {
      const sol = parseInt(native.amount, 10) / LAMPORTS_PER_SOL;
      return `${sol.toFixed(4)} SOL`;
    }
    if (tokens?.length) {
      const t = tokens[0];
      const amt = parseInt(t.rawTokenAmount.tokenAmount, 10) / Math.pow(10, t.rawTokenAmount.decimals);
      // Use cached token list for symbol lookup
      const meta = this._tokenSymbolCache.get(t.mint);
      const symbol = meta ?? t.mint.slice(0, 4) + '...';
      return `${amt.toFixed(4)} ${symbol}`;
    }
    return null;
  }

  private extractDetails(tx: HeliusParsedTransaction, platform: string | null) {
    const swap = tx.events?.swap;
    if (swap) {
      const fromNativeAmt = swap.nativeInput ? parseInt(swap.nativeInput.amount, 10) / LAMPORTS_PER_SOL : null;
      const toNativeAmt = swap.nativeOutput ? parseInt(swap.nativeOutput.amount, 10) / LAMPORTS_PER_SOL : null;
      const fromTokenAmt = swap.tokenInputs?.[0] ? parseInt(swap.tokenInputs[0].rawTokenAmount.tokenAmount, 10) / Math.pow(10, swap.tokenInputs[0].rawTokenAmount.decimals) : null;
      const toTokenAmt = swap.tokenOutputs?.[0] ? parseInt(swap.tokenOutputs[0].rawTokenAmount.tokenAmount, 10) / Math.pow(10, swap.tokenOutputs[0].rawTokenAmount.decimals) : null;

      const inputMint = swap.tokenInputs?.[0]?.mint ?? (swap.nativeInput ? SOL_MINT : null);
      const outputMint = swap.tokenOutputs?.[0]?.mint ?? (swap.nativeOutput ? SOL_MINT : null);

      // For swaps, show the input token (what was spent). Prefer output token for symbol/logo if input is unknown.
      const primaryMint = inputMint;
      const primaryAmount = fromTokenAmt ?? fromNativeAmt;

      // Resolve symbol: try input, then output, then tokenTransfers
      let tokenSymbol = this.resolveSymbol(primaryMint);
      let tokenLogoUri = this.resolveLogoUri(primaryMint);
      let tokenMint = primaryMint;

      // If input token symbol is unknown, try the output token
      if (!tokenSymbol && outputMint) {
        tokenSymbol = this.resolveSymbol(outputMint);
        tokenLogoUri = this.resolveLogoUri(outputMint);
        tokenMint = outputMint;
      }

      // Last resort: use tokenTransfers to find any recognized token
      if (!tokenSymbol && tx.tokenTransfers?.length) {
        for (const tt of tx.tokenTransfers) {
          const sym = this.resolveSymbol(tt.mint);
          if (sym) {
            tokenSymbol = sym;
            tokenLogoUri = this.resolveLogoUri(tt.mint);
            tokenMint = tt.mint;
            break;
          }
        }
      }

      // Estimate USD: try input mint, then output mint
      let usdValue = this.estimateUsdValue(primaryMint, primaryAmount);
      if (usdValue === null && outputMint) {
        const outAmount = toTokenAmt ?? toNativeAmt;
        usdValue = this.estimateUsdValue(outputMint, outAmount);
      }

      // Two-leg metadata for the table row. Both sides resolved against the
      // same symbol/logo/price helpers so the renderer can show
      //   "−1.5 SOL → +120.45 USDC" with logos on each side, plus per-leg
      // USD values when prices for either mint are known.
      const fromAmount = fromTokenAmt ?? fromNativeAmt;
      const toAmount = toTokenAmt ?? toNativeAmt;
      const fromMint = inputMint;
      const toMintFinal = outputMint;
      const fromSymbol = this.resolveSymbol(fromMint) ?? (fromMint === SOL_MINT ? 'SOL' : null);
      const toSymbol = this.resolveSymbol(toMintFinal) ?? (toMintFinal === SOL_MINT ? 'SOL' : null);
      const fromLogoUri = this.resolveLogoUri(fromMint);
      const toLogoUri = this.resolveLogoUri(toMintFinal);
      const fromUsdValue = this.estimateUsdValue(fromMint, fromAmount);
      const toUsdValue = this.estimateUsdValue(toMintFinal, toAmount);

      return {
        fromToken: swap.tokenInputs?.[0]?.mint ?? (swap.nativeInput ? 'SOL' : null),
        toToken: swap.tokenOutputs?.[0]?.mint ?? (swap.nativeOutput ? 'SOL' : null),
        fromAmount,
        toAmount,
        counterparty: null,
        programName: platform,
        fromAddress: swap.nativeInput?.account ?? swap.tokenInputs?.[0]?.userAccount ?? tx.feePayer,
        toAddress: swap.nativeOutput?.account ?? swap.tokenOutputs?.[0]?.userAccount ?? null,
        tokenMint,
        tokenSymbol,
        tokenLogoUri,
        usdValue,
        fromSymbol,
        fromLogoUri,
        fromUsdValue,
        toSymbol,
        toLogoUri,
        toUsdValue,
      };
    }

    if (tx.tokenTransfers?.length) {
      const t = tx.tokenTransfers[0];
      const symbol = this.resolveSymbol(t.mint) ?? (t.mint === SOL_MINT ? 'SOL' : null);
      const logoUri = this.resolveLogoUri(t.mint);
      const usd = this.estimateUsdValue(t.mint, t.tokenAmount);
      return {
        fromToken: t.mint,
        toToken: null,
        fromAmount: t.tokenAmount,
        toAmount: null,
        counterparty: t.toUserAccount || null,
        programName: platform,
        fromAddress: t.fromUserAccount || null,
        toAddress: t.toUserAccount || null,
        tokenMint: t.mint,
        tokenSymbol: symbol,
        tokenLogoUri: logoUri,
        usdValue: usd,
        fromSymbol: symbol,
        fromLogoUri: logoUri,
        fromUsdValue: usd,
      };
    }

    if (tx.nativeTransfers?.length) {
      const t = tx.nativeTransfers[0];
      const solAmount = t.amount / LAMPORTS_PER_SOL;
      const logoUri = this.resolveLogoUri(SOL_MINT);
      const usd = this.estimateUsdValue(SOL_MINT, solAmount);
      return {
        fromToken: 'SOL',
        toToken: null,
        fromAmount: solAmount,
        toAmount: null,
        counterparty: t.toUserAccount || null,
        programName: platform,
        fromAddress: t.fromUserAccount || null,
        toAddress: t.toUserAccount || null,
        tokenMint: SOL_MINT,
        tokenSymbol: 'SOL',
        tokenLogoUri: logoUri,
        usdValue: usd,
        fromSymbol: 'SOL',
        fromLogoUri: logoUri,
        fromUsdValue: usd,
      };
    }

    return null;
  }

  private resolveSymbol(mint: string | null): string | null {
    if (!mint) return null;
    if (mint === SOL_MINT) return 'SOL';
    return this._tokenSymbolCache.get(mint) ?? null;
  }

  private resolveLogoUri(mint: string | null): string | null {
    if (!mint) return null;
    if (mint === SOL_MINT) return 'https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png';
    const meta = this.birdeyeService.getTokenMeta(mint);
    return meta?.imageUrl ?? null;
  }

  private estimateUsdValue(mint: string | null, amount: number | null): number | null {
    if (!mint || amount === null || amount === undefined) return null;
    const summary = this._summary();
    if (!summary) return null;
    if (mint === SOL_MINT) {
      const solPrice = summary.solBalance.usdPrice;
      return solPrice !== null ? amount * solPrice : null;
    }
    const token = summary.tokens.find(t => t.mint === mint);
    if (token?.usdPrice !== null && token?.usdPrice !== undefined) {
      return amount * token.usdPrice;
    }
    return null;
  }

  async refresh(walletAddress: string): Promise<void> {
    this.nftsLoaded = false;
    this.historyLoaded = false;
    this.historyLoadedWallet = null;
    this.historyLoadingPromise = null;
    this._historyCache.delete(walletAddress);
    // Drop the in-memory price cache so a partial-fetch from the previous
    // load (where the DexScreener batch dropped some pump tokens) doesn't
    // get reused. Without this the manual Refresh button surfaced the same
    // missing-price rows because the 60s TTL kept the empty-resolution
    // state alive.
    this.birdeyeService.clearCache();
    await this.loadPortfolio(walletAddress);
  }

  reset(): void {
    this._summary.set(null);
    this._defiPositions.set(null);
    this._recentTransactions.set([]);
    this._protocolPositions.set([]);
    this._portfolioChange.set(null);
    this._nfts.set([]);
    this._nftCollections.set([]);
    this._enhancedTransactions.set([]);
    this._historyHasMore.set(false);
    this._historyLoadingMore.set(false);
    this._loadingState.set('idle');
    this._nftLoadingState.set('idle');
    this._historyLoadingState.set('idle');
    this._error.set(null);
    this._activeTab.set('portfolio');
    this.nftsLoaded = false;
    this.historyLoaded = false;
    this.historyLoadedWallet = null;
    this.historyLoadingPromise = null;
    this._historyCache.clear();
  }

  /**
   * 24h portfolio change in absolute USD + percentage. Uses the exact
   * derivation `past = now / (1 + change%/100)` per holding so big swings
   * (a memecoin up 50%) aren't mangled by the naive `now × change%`
   * approximation. Spam tokens are excluded so airdrop dust doesn't drag
   * the denominator; unpriced rows contribute nothing to either side.
   */
  private applyDailyChange(
    solChange24h: number | null,
    solUsdValue: number | null,
    tokens: EnhancedTokenAccount[],
    totalStakedSol: number,
    solPrice: number | null,
  ): void {
    let change24hUsd = 0;
    let pastValueBasis = 0;

    if (solChange24h !== null && solUsdValue !== null) {
      const pastSol = solUsdValue / (1 + solChange24h / 100);
      change24hUsd += solUsdValue - pastSol;
      pastValueBasis += pastSol;
    }
    for (const t of tokens) {
      if (t.isSuspectedSpam) continue;
      if (t.priceChange24h !== null && t.usdValue !== null) {
        const pastT = t.usdValue / (1 + t.priceChange24h / 100);
        change24hUsd += t.usdValue - pastT;
        pastValueBasis += pastT;
      }
    }
    if (solChange24h !== null && solPrice !== null && totalStakedSol > 0) {
      const stakedNow = totalStakedSol * solPrice;
      const stakedPast = stakedNow / (1 + solChange24h / 100);
      change24hUsd += stakedNow - stakedPast;
      pastValueBasis += stakedPast;
    }

    const change24hPercent = pastValueBasis > 0 ? (change24hUsd / pastValueBasis) * 100 : 0;
    this._portfolioChange.set({ change24hUsd, change24hPercent });
  }
}
