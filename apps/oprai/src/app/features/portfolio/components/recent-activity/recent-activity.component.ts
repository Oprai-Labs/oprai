import { Component, inject, signal, computed, effect, Output, EventEmitter, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { interval } from 'rxjs';
import { LucideAngularModule } from 'lucide-angular';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { WalletService } from '@core/services/wallet.service';
import { PortfolioService } from '../../services/portfolio.service';
import type { EnhancedTransaction, TransactionType } from '../../models/portfolio.models';

type TxFilter = 'all' | 'swap' | 'transfer' | 'stake' | 'nft';
type TimeFilter = '24h' | '7d' | '30d' | 'all';
type PlatformFilter = string | 'all';

interface PlatformInfo {
  id: string;
  name: string;
  icon: string;
  color: string;
}

interface ProtocolRule {
  key: string;
  label: string;
  aliases: string[];
}

@Component({
  selector: 'app-recent-activity',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe, TimeAgoPipe],
  templateUrl: './recent-activity.component.html',
  styleUrl: './recent-activity.component.scss',
})
export class RecentActivityComponent {
  // ──── Direct service injection — no Input binding chain ────
  private readonly portfolio = inject(PortfolioService);
  private readonly walletService = inject(WalletService);
  private readonly cdr = inject(ChangeDetectorRef);

  @Output() loadMore = new EventEmitter<void>();

  // Read signals DIRECTLY from the service
  readonly loadingState = this.portfolio.historyLoadingState;
  readonly enhanced = this.portfolio.enhancedTransactions;
  readonly transactions = this.portfolio.recentTransactions;
  readonly hasMoreSignal = this.portfolio.historyHasMore;
  readonly isLoadingMoreSignal = this.portfolio.historyLoadingMore;

  private _pollingDone = false;

  constructor() {
    // Track enhanced changes for platform updates + page reset
    effect(() => {
      const txs = this.enhanced();
      this.updateAvailablePlatforms(txs);
    });

    // Self-load: if state is idle when component appears, trigger loading
    const wallet = this.walletService.publicKey();
    if (wallet && this.loadingState() === 'idle') {
      queueMicrotask(() => this.portfolio.loadEnhancedHistory(wallet));
    }

    // Force re-renders until data is settled.
    // Angular signal updates from async fetch outside zone don't reliably
    // trigger change detection. Poll every 200ms, stop once loaded + rendered.
    interval(200).pipe(
      takeUntilDestroyed(),
    ).subscribe(() => {
      if (this._pollingDone) return;
      const state = this.loadingState();
      this.cdr.detectChanges();
      if (state === 'loaded' || state === 'error') {
        // Give 2 extra ticks after loaded, then stop
        setTimeout(() => {
          this.cdr.detectChanges();
          this._pollingDone = true;
        }, 400);
      }
    });
  }

  protected readonly selectedTx = signal<EnhancedTransaction | null>(null);
  protected readonly txModalOpen = signal(false);
  readonly activeFilter = signal<TxFilter>('all');
  readonly activeTimeFilter = signal<TimeFilter>('all');
  readonly activePlatformFilter = signal<PlatformFilter>('all');
  readonly hideSpamTxs = signal(true);
  readonly hideFailed = signal(false);
  readonly oldestFirst = signal(false);
  private readonly _currentPage = signal(1);
  readonly pageSize = 10;
  private readonly _availablePlatforms = signal<PlatformInfo[]>([]);

  readonly skeletonRows = [0, 1, 2, 3, 4, 5, 6, 7];

  readonly filters: Array<{ id: TxFilter; label: string }> = [
    { id: 'all', label: 'All' },
    { id: 'swap', label: 'Swaps' },
    { id: 'transfer', label: 'Transfers' },
    { id: 'stake', label: 'Staking' },
    { id: 'nft', label: 'NFTs' },
  ];
  readonly timeFilters: Array<{ id: TimeFilter; label: string }> = [
    { id: '24h', label: '24H' },
    { id: '7d', label: '7D' },
    { id: '30d', label: '30D' },
    { id: 'all', label: 'All' },
  ];

  private static readonly ICON_BASE = '/assets/icons/protocols';

  readonly platformIcons: Record<string, string> = {
    'jupiter': `${RecentActivityComponent.ICON_BASE}/jupiter.png`,
    'jupiter v6': `${RecentActivityComponent.ICON_BASE}/jupiter.png`,
    'jupiter ag': `${RecentActivityComponent.ICON_BASE}/jupiter.png`,
    'jup ag': `${RecentActivityComponent.ICON_BASE}/jupiter.png`,
    'jupag': `${RecentActivityComponent.ICON_BASE}/jupiter.png`,
    'raydium': `${RecentActivityComponent.ICON_BASE}/raydium.png`,
    'raydium amm': `${RecentActivityComponent.ICON_BASE}/raydium.png`,
    'raydium clmm': `${RecentActivityComponent.ICON_BASE}/raydium.png`,
    'orca': `${RecentActivityComponent.ICON_BASE}/orca.png`,
    'orca whirlpool': `${RecentActivityComponent.ICON_BASE}/orca.png`,
    'meteora': `${RecentActivityComponent.ICON_BASE}/meteora.png`,
    'meteora dlmm': `${RecentActivityComponent.ICON_BASE}/meteora.png`,
    'lifinity': `${RecentActivityComponent.ICON_BASE}/lifinity.png`,
    'marinade': `${RecentActivityComponent.ICON_BASE}/marinade.png`,
    'marinade finance': `${RecentActivityComponent.ICON_BASE}/marinade.png`,
    'jito': `${RecentActivityComponent.ICON_BASE}/jito.png`,
    'kamino': `${RecentActivityComponent.ICON_BASE}/kamino.png`,
    'marginfi': `${RecentActivityComponent.ICON_BASE}/marginfi.png`,
    'phantom': `${RecentActivityComponent.ICON_BASE}/phantom.png`,
    'tensor': `${RecentActivityComponent.ICON_BASE}/tensor.png`,
    'magic eden': `${RecentActivityComponent.ICON_BASE}/magiceden.png`,
    'pump.fun': `${RecentActivityComponent.ICON_BASE}/pumpfun.png`,
    'pump fun': `${RecentActivityComponent.ICON_BASE}/pumpfun.png`,
    'dflow': `${RecentActivityComponent.ICON_BASE}/dflow.png`,
    'debridge': `${RecentActivityComponent.ICON_BASE}/debridge.png`,
    'serum': `${RecentActivityComponent.ICON_BASE}/serum.png`,
    'lido': `${RecentActivityComponent.ICON_BASE}/lido.png`,
  };
  private readonly protocolRules: ProtocolRule[] = [
    { key: 'jupiter', label: 'Jupiter', aliases: ['jupiter', 'jup ag', 'jupag'] },
    { key: 'raydium', label: 'Raydium', aliases: ['raydium'] },
    { key: 'orca', label: 'Orca', aliases: ['orca'] },
    { key: 'meteora', label: 'Meteora', aliases: ['meteora'] },
    { key: 'lifinity', label: 'Lifinity', aliases: ['lifinity'] },
    { key: 'pump fun', label: 'Pump.fun', aliases: ['pump fun', 'pumpfun', 'pump'] },
    { key: 'dflow', label: 'DFlow', aliases: ['dflow'] },
    { key: 'debridge', label: 'deBridge', aliases: ['debridge', 'de bridge'] },
    { key: 'serum', label: 'Serum', aliases: ['serum'] },
    { key: 'tensor', label: 'Tensor', aliases: ['tensor'] },
    { key: 'magic eden', label: 'Magic Eden', aliases: ['magic eden', 'magiceden'] },
    { key: 'kamino', label: 'Kamino', aliases: ['kamino'] },
    { key: 'marginfi', label: 'MarginFi', aliases: ['marginfi', 'margin fi'] },
    { key: 'jito', label: 'Jito', aliases: ['jito'] },
    { key: 'marinade', label: 'Marinade', aliases: ['marinade'] },
    { key: 'lido', label: 'Lido', aliases: ['lido'] },
    { key: 'phantom', label: 'Phantom', aliases: ['phantom'] },
  ];

  readonly hasEnhanced = computed(() => this.enhanced().length > 0);

  readonly availablePlatforms = computed(() => {
    const all = this._availablePlatforms();
    return [
      { id: 'all', name: 'All Platforms', icon: '', color: '#888' },
      ...all,
    ];
  });

  readonly filteredTransactions = computed(() => {
    const enhanced = this.enhanced();
    const typeFilter = this.activeFilter();
    const timeFilter = this.activeTimeFilter();
    const platformFilter = this.activePlatformFilter();
    const hideSpam = this.hideSpamTxs();
    const hideFailed = this.hideFailed();
    const oldestFirst = this.oldestFirst();

    let filtered = enhanced;

    // Type filter
    if (typeFilter !== 'all') {
      const typeMap: Record<TxFilter, TransactionType[]> = {
        all: [],
        swap: ['swap'],
        transfer: ['transfer'],
        stake: ['stake', 'unstake'],
        nft: ['nft-sale', 'nft-purchase', 'nft-mint'],
      };
      const types = typeMap[typeFilter];
      filtered = filtered.filter((tx) => types.includes(tx.type));
    }

    // Time filter
    if (timeFilter !== 'all') {
      const now = Date.now() / 1000;
      const timeLimits: Record<TimeFilter, number> = {
        '24h': 24 * 60 * 60,
        '7d': 7 * 24 * 60 * 60,
        '30d': 30 * 24 * 60 * 60,
        'all': 0,
      };
      const limit = timeLimits[timeFilter];
      filtered = filtered.filter((tx) => tx.blockTime && (now - tx.blockTime) <= limit);
    }

    // Platform filter
    if (platformFilter !== 'all') {
      const normalizedFilter = this.normalizePlatformKey(platformFilter);
      filtered = filtered.filter((tx) => this.normalizePlatformKey(tx.platform) === normalizedFilter);
    }

    if (hideFailed) {
      filtered = filtered.filter((tx) => tx.success);
    }

    if (hideSpam) {
      filtered = filtered.filter((tx) => {
        const desc = tx.description.toLowerCase();
        return !desc.includes('spam') && tx.type !== 'nft-mint';
      });
    }

    filtered = [...filtered].sort((a, b) => {
      const at = a.blockTime ?? 0;
      const bt = b.blockTime ?? 0;
      return oldestFirst ? at - bt : bt - at;
    });

    return filtered;
  });

  private updateAvailablePlatforms(transactions: EnhancedTransaction[]): void {
    const platformMap = new Map<string, PlatformInfo>();

    for (const tx of transactions) {
      if (tx.platform) {
        const key = this.normalizePlatformKey(tx.platform);
        if (!platformMap.has(key)) {
          platformMap.set(key, {
            id: key,
            name: this.getPlatformLabel(tx.platform),
            icon: this.getPlatformIcon(tx.platform),
            color: this.getPlatformColor(tx.platform),
          });
        }
      }
    }

    this._availablePlatforms.set(Array.from(platformMap.values()));
  }

  readonly currentPage = this._currentPage.asReadonly();

  readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.filteredTransactions().length / this.pageSize))
  );

  readonly paginatedTransactions = computed(() => {
    const all = this.filteredTransactions();
    const start = (this._currentPage() - 1) * this.pageSize;
    return all.slice(start, start + this.pageSize);
  });

  readonly pageNumbers = computed(() => {
    const total = this.totalPages();
    const current = this._currentPage();
    const pages: (number | 'ellipsis')[] = [];

    if (total <= 7) {
      for (let i = 1; i <= total; i++) pages.push(i);
      return pages;
    }

    pages.push(1);
    if (current > 3) pages.push('ellipsis');

    const start = Math.max(2, current - 1);
    const end = Math.min(total - 1, current + 1);
    for (let i = start; i <= end; i++) pages.push(i);

    if (current < total - 2) pages.push('ellipsis');
    pages.push(total);

    return pages;
  });

  readonly totalCount = computed(() => this.filteredTransactions().length);

  readonly showingFrom = computed(() =>
    this.filteredTransactions().length === 0 ? 0 : (this._currentPage() - 1) * this.pageSize + 1
  );

  readonly showingTo = computed(() =>
    Math.min(this._currentPage() * this.pageSize, this.filteredTransactions().length)
  );

  setFilter(filter: TxFilter): void {
    this.activeFilter.set(filter);
    this._currentPage.set(1);
  }

  setTimeFilter(filter: TimeFilter): void {
    this.activeTimeFilter.set(filter);
    this._currentPage.set(1);
  }

  setPlatformFilter(filter: PlatformFilter): void {
    this.activePlatformFilter.set(filter);
    this._currentPage.set(1);
  }

  goToPage(page: number): void {
    if (page < 1 || page > this.totalPages()) return;
    this._currentPage.set(page);
  }

  prevPage(): void {
    this.goToPage(this._currentPage() - 1);
  }

  nextPage(): void {
    const current = this._currentPage();
    const total = this.totalPages();

    if (current >= total) {
      if (this.hasMoreSignal() && !this.isLoadingMoreSignal()) {
        this.loadMore.emit();
      }
      return;
    }

    this.goToPage(current + 1);
  }

  getSolscanUrl(signature: string): string {
    return `https://solscan.io/tx/${signature}`;
  }

  toIsoString(blockTime: number): string {
    return new Date(blockTime * 1000).toISOString();
  }

  /** Absolute date for the feed row, e.g. "20 May 2026, 10:36". */
  getDateAbsolute(blockTime: number | null): string {
    if (blockTime === null) return '';
    return new Date(blockTime * 1000).toLocaleString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  /**
   * Primary platform icon for a tx, with a guaranteed non-empty result
   * (letter-avatar fallback). Used for the round avatar that leads each
   * feed row — answers "where did this action happen?".
   */
  getTxPlatformIcon(tx: EnhancedTransaction): string {
    const primary = this.getTxPrimaryProtocol(tx);
    if (primary) return this.getProtocolIconForKey(primary.key);
    return this.getPlatformIconSafe(tx.platform);
  }

  getTxPlatformLabel(tx: EnhancedTransaction): string {
    const primary = this.getTxPrimaryProtocol(tx);
    return primary ? primary.label : this.getPlatformLabel(tx.platform);
  }

  openTxDetail(tx: EnhancedTransaction): void {
    this.selectedTx.set(tx);
    this.txModalOpen.set(true);
  }

  closeTxDetail(): void {
    this.txModalOpen.set(false);
  }

  getPlatformLabel(platform: string | null): string {
    if (!platform) return 'Unknown';
    const key = this.normalizePlatformKey(platform);
    const platformMap: Record<string, string> = {
      'jupiter': 'Jupiter',
      'jupiter v6': 'Jupiter',
      'jupiter ag': 'Jupiter',
      'jup ag': 'Jupiter',
      'jupag': 'Jupiter',
      'raydium': 'Raydium',
      'raydium amm': 'Raydium',
      'raydium clmm': 'Raydium',
      'orca': 'Orca',
      'orca whirlpool': 'Orca',
      'meteora': 'Meteora',
      'meteora dlmm': 'Meteora',
      'lifinity': 'Lifinity',
      'saber': 'Saber',
      'marinade': 'Marinade',
      'marinade finance': 'Marinade',
      'jito': 'Jito',
      'kamino': 'Kamino',
      'marginfi': 'MarginFi',
      'lido': 'Lido',
      'spl token': 'SPL Token',
      'system': 'System',
      'system program': 'System',
      'phantom': 'Phantom',
      'tensor': 'Tensor',
      'magic eden': 'Magic Eden',
      'pump.fun': 'Pump.fun',
      'pump fun': 'Pump.fun',
      'dflow': 'DFlow',
      'debridge': 'deBridge',
      'serum': 'Serum',
    };
    return platformMap[key] || platform.charAt(0).toUpperCase() + platform.slice(1).toLowerCase();
  }

  getPlatformColor(platform: string | null): string {
    if (!platform) return '#888';
    const key = this.normalizePlatformKey(platform);
    const colorMap: Record<string, string> = {
      'jupiter': '#595BF6',
      'jupiter v6': '#595BF6',
      'jupiter ag': '#595BF6',
      'jup ag': '#595BF6',
      'jupag': '#595BF6',
      'raydium': '#682AED',
      'raydium amm': '#682AED',
      'raydium clmm': '#682AED',
      'orca': '#FFD600',
      'orca whirlpool': '#FFD600',
      'meteora': '#FF6B00',
      'meteora dlmm': '#FF6B00',
      'lifinity': '#0A6EFF',
      'marinade': '#2FCB6E',
      'marinade finance': '#2FCB6E',
      'jito': '#FF8A00',
      'kamino': '#43D4AA',
      'marginfi': '#4F46E5',
      'lido': '#00A3FF',
      'phantom': '#AB9FF2',
      'tensor': '#00D18C',
      'magic eden': '#E42575',
      'pump.fun': '#00C853',
      'pump fun': '#00C853',
      'dflow': '#3B82F6',
      'debridge': '#2563EB',
      'serum': '#6366F1',
    };
    return colorMap[key] || '#888';
  }

  getPlatformIcon(platform: string | null): string {
    if (!platform) return '';
    const key = this.normalizePlatformKey(platform);
    const direct = this.platformIcons[key];
    if (direct) return direct;
    const inferred = this.inferPlatformIconByKeyword(key);
    if (inferred) return inferred;
    return this.getRemoteProtocolIcon(key);
  }

  getPlatformIconSafe(platform: string | null): string {
    const icon = this.getPlatformIcon(platform);
    if (icon) return icon;
    return this.getPlatformLetterFallback(platform);
  }

  getTxProtocols(tx: EnhancedTransaction): Array<{ key: string; label: string }> {
    const blobs = [
      this.normalizePlatformKey(tx.platform),
      this.normalizePlatformKey(tx.details?.programName ?? null),
      this.normalizePlatformKey(tx.description),
    ].filter(Boolean);
    const merged = blobs.join(' ');
    const matched = new Map<string, string>();

    for (const rule of this.protocolRules) {
      if (rule.aliases.some((a) => merged.includes(a))) {
        matched.set(rule.key, rule.label);
      }
    }

    if (!matched.size && tx.platform) {
      const fallbackKey = this.normalizePlatformKey(tx.platform);
      matched.set(fallbackKey, this.getPlatformLabel(tx.platform));
    }
    return Array.from(matched, ([key, label]) => ({ key, label }));
  }

  getTxPrimaryProtocol(tx: EnhancedTransaction): { key: string; label: string } | null {
    const protocols = this.getTxProtocols(tx);
    return protocols.length ? protocols[0] : null;
  }

  getProtocolIconForKey(protocolKey: string): string {
    return this.getPlatformIconSafe(protocolKey);
  }

  onPlatformIconError(event: Event, platform: string | null): void {
    const target = event.target as HTMLImageElement | null;
    if (!target) return;
    target.src = this.getPlatformLetterFallback(platform);
    target.classList.add('platform-icon-fallback');
  }

  getPlatformTxCount(platformId: string): number {
    if (platformId === 'all') {
      return this.filteredTransactions().length;
    }
    return this.enhanced().filter(tx => this.normalizePlatformKey(tx.platform) === platformId).length;
  }

  getTypeTxCount(typeFilter: TxFilter): number {
    if (typeFilter === 'all') {
      return this.enhanced().length;
    }
    const typeMap: Record<TxFilter, TransactionType[]> = {
      all: [],
      swap: ['swap'],
      transfer: ['transfer'],
      stake: ['stake', 'unstake'],
      nft: ['nft-sale', 'nft-purchase', 'nft-mint'],
    };
    const types = typeMap[typeFilter];
    return this.enhanced().filter(tx => types.includes(tx.type)).length;
  }

  resetFilters(): void {
    this.activeFilter.set('all');
    this.activeTimeFilter.set('all');
    this.activePlatformFilter.set('all');
    this.hideSpamTxs.set(true);
    this.hideFailed.set(false);
    this.oldestFirst.set(false);
    this._currentPage.set(1);
  }

  toggleHideSpam(): void {
    this.hideSpamTxs.update((v) => !v);
    this._currentPage.set(1);
  }

  toggleHideFailed(): void {
    this.hideFailed.update((v) => !v);
    this._currentPage.set(1);
  }

  toggleOldestFirst(): void {
    this.oldestFirst.update((v) => !v);
    this._currentPage.set(1);
  }

  getActionText(tx: EnhancedTransaction): string {
    const map: Record<string, string> = {
      transfer: 'TRANSFER',
      swap: 'SWAP',
      stake: 'STAKE',
      unstake: 'UNSTAKE',
      'nft-sale': 'NFT SALE',
      'nft-purchase': 'NFT BUY',
      'nft-mint': 'NFT MINT',
      'token-mint': 'TOKEN MINT',
      burn: 'BURN',
      vote: 'VOTE',
      unknown: 'ACTION',
    };
    return map[tx.type] ?? 'ACTION';
  }

  getActionPillClass(tx: EnhancedTransaction): string {
    const map: Record<string, string> = {
      transfer: 'pill-transfer',
      swap: 'pill-swap',
      stake: 'pill-stake',
      unstake: 'pill-stake',
      'nft-sale': 'pill-nft',
      'nft-purchase': 'pill-nft',
      'nft-mint': 'pill-nft',
      'token-mint': 'pill-create',
      burn: 'pill-burn',
      vote: 'pill-vote',
      unknown: 'pill-default',
    };
    return map[tx.type] ?? 'pill-default';
  }

  getFromAddress(tx: EnhancedTransaction): string {
    const addr = tx.details?.fromAddress;
    if (!addr) return '--';
    return this.truncateAddr(addr);
  }

  getFromAddressFull(tx: EnhancedTransaction): string {
    return tx.details?.fromAddress ?? '';
  }

  getToAddress(tx: EnhancedTransaction): string {
    const addr = tx.details?.toAddress ?? tx.details?.counterparty;
    if (!addr) return '--';
    return this.truncateAddr(addr);
  }

  getToAddressFull(tx: EnhancedTransaction): string {
    return tx.details?.toAddress ?? tx.details?.counterparty ?? '';
  }

  getAmountDisplay(tx: EnhancedTransaction): string {
    const d = tx.details;
    if (!d) return '--';
    const amount = d.fromAmount ?? d.toAmount;
    if (amount === null || amount === undefined) return '--';
    const sign = this.isAmountPositive(tx) ? '+' : '-';
    return `${sign}${this.formatAmount(Math.abs(amount))}`;
  }

  /**
   * For swaps: out leg (red) + in leg (green) so the row shows "−1.5 SOL → +120 USDC".
   * Returns null when the tx isn't a 2-leg swap or the legs aren't both decoded —
   * caller falls back to the single-amount renderer.
   */
  getSwapLegs(tx: EnhancedTransaction): {
    out: { amount: string; symbol: string; logo: string | null; usd: string | null };
    in:  { amount: string; symbol: string; logo: string | null; usd: string | null };
  } | null {
    const d = tx.details;
    if (!d || tx.type !== 'swap') return null;
    const fromAmount = d.fromAmount;
    const toAmount = d.toAmount;
    if (fromAmount == null || toAmount == null) return null;
    const fromSym = d.fromSymbol ?? d.tokenSymbol ?? '';
    const toSym = d.toSymbol ?? '';
    if (!fromSym && !toSym) return null;
    const usd = (v: number | null | undefined): string | null =>
      v == null ? null : '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return {
      out: {
        amount: '−' + this.formatAmount(Math.abs(fromAmount)),
        symbol: fromSym,
        logo: d.fromLogoUri ?? null,
        usd: usd(d.fromUsdValue),
      },
      in: {
        amount: '+' + this.formatAmount(Math.abs(toAmount)),
        symbol: toSym,
        logo: d.toLogoUri ?? null,
        usd: usd(d.toUsdValue),
      },
    };
  }

  isAmountPositive(tx: EnhancedTransaction): boolean {
    if (tx.type === 'swap') return false;
    if (tx.type === 'transfer' && tx.details?.toAmount !== null && tx.details?.toAmount !== undefined) return true;
    return false;
  }

  getTokenIcon(tx: EnhancedTransaction): string | null {
    return tx.details?.tokenLogoUri ?? null;
  }

  getTokenSymbol(tx: EnhancedTransaction): string {
    if (tx.details?.tokenSymbol) return tx.details.tokenSymbol;
    if (tx.details?.fromToken === 'SOL' || tx.details?.toToken === 'SOL') return 'SOL';
    const desc = tx.description || '';
    const match = desc.match(/→\s*[\d,.]+\s+(\w+)/);
    if (match) return match[1];
    const match2 = desc.match(/Transferred\s+[\d,.]+\s+(\w+)/);
    if (match2) return match2[1];
    return '';
  }

  getUsdValue(tx: EnhancedTransaction): string {
    const usd = tx.details?.usdValue;
    if (usd === null || usd === undefined) return '';
    return '$' + usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  private truncateAddr(addr: string): string {
    if (addr.length <= 10) return addr;
    return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
  }

  private getPlatformLetterFallback(platform: string | null): string {
    const letter = (platform ?? '?').charAt(0).toUpperCase();
    const color = this.getPlatformColor(platform);
    return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="${color}"/><text x="16" y="21" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif" font-size="14" font-weight="700">${letter}</text></svg>`)}`;
  }

  private normalizePlatformKey(platform: string | null): string {
    if (!platform) return '';
    return platform
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  private inferPlatformIconByKeyword(normalizedKey: string): string {
    const rules: Array<{ contains: string[]; iconKey: string }> = [
      { contains: ['jupiter', 'jup ag', 'jupag'], iconKey: 'jupiter' },
      { contains: ['raydium'], iconKey: 'raydium' },
      { contains: ['orca'], iconKey: 'orca' },
      { contains: ['meteora'], iconKey: 'meteora' },
      { contains: ['lifinity'], iconKey: 'lifinity' },
      { contains: ['pump', 'pumpfun'], iconKey: 'pump fun' },
      { contains: ['dflow'], iconKey: 'dflow' },
      { contains: ['debridge'], iconKey: 'debridge' },
      { contains: ['serum'], iconKey: 'serum' },
      { contains: ['tensor'], iconKey: 'tensor' },
      { contains: ['magic eden'], iconKey: 'magic eden' },
      { contains: ['kamino'], iconKey: 'kamino' },
      { contains: ['marginfi'], iconKey: 'marginfi' },
    ];

    for (const rule of rules) {
      if (rule.contains.some((term) => normalizedKey.includes(term))) {
        return this.platformIcons[rule.iconKey] || '';
      }
    }
    return '';
  }

  private getRemoteProtocolIcon(normalizedKey: string): string {
    if (!normalizedKey) return '';
    const slug = normalizedKey.replace(/\s+/g, '-');
    return `https://icons.llamao.fi/icons/protocols/${encodeURIComponent(slug)}?w=64&h=64`;
  }

  private formatAmount(n: number): string {
    if (n >= 1000) return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (n >= 1) return n.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 });
    return n.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 6 });
  }
}
