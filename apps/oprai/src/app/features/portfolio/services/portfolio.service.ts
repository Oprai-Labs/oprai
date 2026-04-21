import { Injectable, NgZone, inject, signal, computed } from '@angular/core';
import { SolanaRpcService } from './solana-rpc.service';
import { PriceService } from './price.service';
import { BirdeyeService, type BirdeyeTokenPrice } from './birdeye.service';
import { HeliusService } from './helius.service';
import { ProtocolDetectionService } from './protocol-detection.service';
import { DefiPositionsService } from './defi-positions.service';
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

  // ──── Core State ────
  private readonly _summary = signal<PortfolioSummary | null>(null);
  private readonly _defiPositions = signal<DefiPositions | null>(null);
  private readonly _recentTransactions = signal<RecentTransaction[]>([]);
  private readonly _loadingState = signal<LoadingState>('idle');
  private readonly _error = signal<string | null>(null);

  // ──── New State ────
  private readonly _activeTab = signal<PortfolioTab>('tokens');
  private readonly _protocolPositions = signal<ProtocolPosition[]>([]);
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

  // ──── Public Signals ────
  readonly summary = this._summary.asReadonly();
  readonly defiPositions = this._defiPositions.asReadonly();
  readonly recentTransactions = this._recentTransactions.asReadonly();
  readonly loadingState = this._loadingState.asReadonly();
  readonly error = this._error.asReadonly();
  readonly activeTab = this._activeTab.asReadonly();
  readonly protocolPositions = this._protocolPositions.asReadonly();
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
      const [tokenList, birdeyeData] = await Promise.all([
        this.priceService.getTokenList(),
        this.birdeyeService.getTokenPrices(allMints).catch(() => new Map<string, BirdeyeTokenPrice>()),
      ]);

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

      // Build enhanced tokens with 24h change + protocol classification
      // Metadata resolution: Jupiter strict → DexScreener (already fetched) → Jupiter individual
      const rawEnhanced: EnhancedTokenAccount[] = rawTokens.map((raw) => {
        const jupMeta = tokenList.get(raw.mint);
        const dexMeta = this.birdeyeService.getTokenMeta(raw.mint);
        const usdPrice = prices.get(raw.mint) ?? null;
        const usdValue = usdPrice !== null ? raw.balance * usdPrice : null;
        const classification = this.protocolDetection.classifyToken(raw.mint);

        // 3-layer fallback: Jupiter strict → DexScreener → truncated mint
        const symbol = jupMeta?.symbol ?? dexMeta?.symbol ?? raw.mint.slice(0, 4) + '...';
        const name = jupMeta?.name ?? dexMeta?.name ?? 'Unknown Token';
        const logoUri = jupMeta?.logoURI ?? dexMeta?.imageUrl ?? null;

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
        };
      });

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

      // Compute allocation percentages
      const tokensUsdTotal = rawEnhanced.reduce((sum, t) => sum + (t.usdValue ?? 0), 0);
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

      this._summary.set({
        walletAddress,
        solBalance: {
          lamports: balanceLamports,
          sol: solBalance,
          usdPrice: solPrice,
          usdValue: solUsdValue,
          priceChange24h: solChange24h,
          allocationPercent: solAllocationPercent,
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

      // ──── Protocol Positions ────
      const allPositions: ProtocolPosition[] = [];

      // Native staking as a protocol position
      const solLogo = this.protocolDetection.getSolLogo();
      if (stakePositions.length > 0) {
        const stakingUsd = solPrice !== null ? totalStakedSol * solPrice : 0;
        allPositions.push({
          protocolId: 'solana-staking',
          protocolName: 'Solana Staking',
          protocolLogoUri: solLogo,
          category: 'native-staking',
          positions: stakePositions.map((sp) => ({
            label: `Validator ${sp.validatorVoteAccount.slice(0, 6)}...`,
            tokens: [{ symbol: 'SOL', amount: sp.stakedSol, logoUri: solLogo }],
            totalUsdValue: sp.usdValue,
            metadata: { status: sp.status },
          })),
          totalUsdValue: stakingUsd,
        });
      }

      // Liquid staking positions
      const lstPositions = this.defiPositionsService.getLiquidStakingPositions(tokens, solPrice);
      allPositions.push(...lstPositions);

      // LP positions (async)
      const lpPositions = await this.defiPositionsService
        .getLpPositions(walletAddress, tokens)
        .catch(() => []);
      allPositions.push(...lpPositions);

      // Lending + DeFi positions — all in parallel
      const [
        lendingPositions,
        kaminoPositions,
        marginfiPositions,
        orcaPositions,
        raydiumClmmPositions,
        meteoraPositions,
        driftPositions,
        streamflowPositions,
      ] = await Promise.all([
        this.defiPositionsService.getLendingPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getKaminoPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getMarginFiPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getOrcaPositions().catch(() => []),
        this.defiPositionsService.getRaydiumClmmPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getMeteoraPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getDriftPositions(walletAddress).catch(() => []),
        this.defiPositionsService.getStreamflowPositions(walletAddress).catch(() => []),
      ]);
      allPositions.push(
        ...lendingPositions,
        ...kaminoPositions,
        ...marginfiPositions,
        ...orcaPositions,
        ...raydiumClmmPositions,
        ...meteoraPositions,
        ...driftPositions,
        ...streamflowPositions,
      );

      this._protocolPositions.set(allPositions);

      // ──── 24h Portfolio Change ────
      let change24hUsd = 0;
      if (solChange24h !== null && solUsdValue !== null) {
        change24hUsd += (solUsdValue * solChange24h) / 100;
      }
      for (const t of tokens) {
        if (t.priceChange24h !== null && t.usdValue !== null) {
          change24hUsd += (t.usdValue * t.priceChange24h) / 100;
        }
      }
      const totalWithStaking = totalPortfolioValue + (solPrice !== null ? totalStakedSol * solPrice : 0);
      const change24hPercent = totalWithStaking > 0 ? (change24hUsd / totalWithStaking) * 100 : 0;

      this._portfolioChange.set({ change24hUsd, change24hPercent });

      this._recentTransactions.set(signatures);
      this._loadingState.set('loaded');

      // Pre-load enhanced history using already-fetched signatures (avoid duplicate RPC call)
      this.preloadEnhancedHistory(walletAddress, signatures).catch(() => {});
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to load portfolio';
      this._error.set(message);
      this._loadingState.set('error');
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

    if (parsed.length > 0) {
      return parsed.map((tx) => this.mapHeliusTx(tx));
    }

    // Fallback: map raw signatures as "unknown" type
    return this.mapSignatureFallback(signatures);
  }

  private mapHeliusTx(tx: HeliusParsedTransaction): EnhancedTransaction {
    const type = this.mapTxType(tx.type);
    const platform = this.inferPlatform(tx);
    let details;
    try {
      details = this.extractDetails(tx, platform);
    } catch {
      details = null;
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

      return {
        fromToken: swap.tokenInputs?.[0]?.mint ?? (swap.nativeInput ? 'SOL' : null),
        toToken: swap.tokenOutputs?.[0]?.mint ?? (swap.nativeOutput ? 'SOL' : null),
        fromAmount: fromTokenAmt ?? fromNativeAmt,
        toAmount: toTokenAmt ?? toNativeAmt,
        counterparty: null,
        programName: platform,
        fromAddress: swap.nativeInput?.account ?? swap.tokenInputs?.[0]?.userAccount ?? tx.feePayer,
        toAddress: swap.nativeOutput?.account ?? swap.tokenOutputs?.[0]?.userAccount ?? null,
        tokenMint,
        tokenSymbol,
        tokenLogoUri,
        usdValue,
      };
    }

    if (tx.tokenTransfers?.length) {
      const t = tx.tokenTransfers[0];
      const symbol = this.resolveSymbol(t.mint) ?? (t.mint === SOL_MINT ? 'SOL' : null);
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
        tokenLogoUri: this.resolveLogoUri(t.mint),
        usdValue: this.estimateUsdValue(t.mint, t.tokenAmount),
      };
    }

    if (tx.nativeTransfers?.length) {
      const t = tx.nativeTransfers[0];
      const solAmount = t.amount / LAMPORTS_PER_SOL;
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
        tokenLogoUri: this.resolveLogoUri(SOL_MINT),
        usdValue: this.estimateUsdValue(SOL_MINT, solAmount),
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
    this._activeTab.set('tokens');
    this.nftsLoaded = false;
    this.historyLoaded = false;
    this.historyLoadedWallet = null;
    this.historyLoadingPromise = null;
    this._historyCache.clear();
  }
}
