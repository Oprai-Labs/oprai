import { ChangeDetectionStrategy, Component, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { forkJoin, of, from } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AccountService } from '../../../../core/services/account.service';
import { ApiService } from '../../../../core/services/api.service';
import { EvmPortfolioService, EvmToken, EvmPosition, EvmTx, EvmNft } from '../../services/evm-portfolio.service';
import { LighterPerpService, LighterPosition } from '../../../../core/services/market/lighter-perp.service';
import { AllocationChartComponent, ChartSegment } from '../allocation-chart/allocation-chart.component';
import { DefiPositionsComponent } from '../defi-positions/defi-positions.component';
import { ClaimableRewardsComponent } from '../claimable-rewards/claimable-rewards.component';
import type { ProtocolPosition, ProtocolCategory } from '../../models/portfolio.models';

const CHAIN_COLOR: Record<string, string> = {
  ethereum: '#627eea', base: '#0052ff', arbitrum: '#28a0f0', optimism: '#ff0420', polygon: '#8247e5', bsc: '#f0b90b', robinhood: '#00c805',
};
const CHAIN_LABEL: Record<string, string> = {
  ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum', optimism: 'Optimism', polygon: 'Polygon', bsc: 'BNB', robinhood: 'Robinhood',
};
const CHAIN_ORDER = ['ethereum', 'base', 'bsc', 'polygon', 'arbitrum', 'optimism', 'robinhood'];
const TW_FOLDER: Record<string, string> = {
  ethereum: 'ethereum', base: 'base', arbitrum: 'arbitrum', optimism: 'optimism', polygon: 'polygon', bsc: 'smartchain',
};
const chainLogo = (chain: string) =>
  chain === 'robinhood'
    ? 'https://assets.relay.link/icons/4663/light.png' // no Trust Wallet folder
    : `https://cdn.jsdelivr.net/gh/trustwallet/assets@master/blockchains/${TW_FOLDER[chain] || 'ethereum'}/info/logo.png`;
const ALLOC_COLORS = ['#5b5fc7', '#06b6d4', '#22c55e', '#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#ef4444'];

function categoryFor(label: string): ProtocolCategory {
  const l = (label || '').toLowerCase();
  if (l.includes('borrow')) return 'borrowing';
  if (l.includes('suppl') || l.includes('lend') || l.includes('deposit')) return 'lending';
  if (l.includes('stak')) return 'liquid-staking';
  if (l.includes('liquid') || l.includes('lp') || l.includes('pool')) return 'liquidity-pool';
  return 'rewards';
}

@Component({
  selector: 'app-evm-holdings',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, AllocationChartComponent, DefiPositionsComponent, ClaimableRewardsComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (walletCount() > 0) {
      @if (loading()) {
        <div class="evm-skel"><div class="sk sk-lg"></div><div class="sk"></div><div class="sk"></div></div>
      } @else {
        <!-- ── Hero: avatar · address · total · allocation donut (Solana layout) ── -->
        <div class="hero">
          <div class="hero-left">
            <div class="hero-row">
              <div class="hero-avatar" [style.--cc]="chainColor(heroChain())">
                <img [src]="heroIcon()" [alt]="heroChain()" (error)="hideImg($event)" />
              </div>
              <button class="hero-address" (click)="copyAddr()" title="Copy address">
                {{ shortAddr(address()) }} <span class="hero-copy">{{ copied() ? '✓' : '⧉' }}</span>
              </button>
            </div>
            <div class="hero-total">{{ totalUsd() | currency:'USD':'symbol':'1.2-2' }}</div>
            <div class="hero-sub">Ethereum &amp; L2s · {{ tokens().length }} tokens · {{ positions().length }} positions</div>
          </div>
          <div class="hero-right">
            <app-allocation-chart [segments]="allocationSegments()" [totalValue]="totalUsd()" />
          </div>
        </div>

        <!-- ── Category cards (Wallet + protocols) ── -->
        <div class="proto-cards">
          @for (c of cards(); track c.name) {
            <div class="proto-card">
              <div class="proto-card-ico" [style.--cc]="c.color">
                @if (c.name === 'Wallet') { <lucide-icon name="wallet" [size]="16" /> }
                @else if (c.logo) { <img [src]="c.logo" [alt]="c.name" (error)="hideImg($event)" /> }
                @else { <span class="proto-card-dot" [style.background]="c.color"></span> }
              </div>
              <div class="proto-card-text">
                <span class="proto-card-name">{{ c.name }}</span>
                <span class="proto-card-usd">{{ c.usd | currency:'USD':'symbol':'1.2-2' }}</span>
              </div>
            </div>
          }
        </div>

        <!-- ── Active positions & rewards — the exact Solana summary panel ── -->
        <app-claimable-rewards [protocolPositions]="protoPositions()" />

        <!-- ── Tabs (Portfolio / Transactions) like the Solana view ── -->
        <div class="evm-tabs">
          <button class="evm-tab" [class.on]="evmTab() === 'portfolio'" (click)="evmTab.set('portfolio')">Portfolio</button>
          <button class="evm-tab" [class.on]="evmTab() === 'nfts'" (click)="evmTab.set('nfts')">NFTs</button>
          <button class="evm-tab" [class.on]="evmTab() === 'transactions'" (click)="evmTab.set('transactions')">Transactions</button>
        </div>

        @if (evmTab() === 'nfts') {
          @if (visNfts().length === 0) {
            <div class="evm-empty">No NFTs found on the selected chain{{ chainFilter() === 'all' ? 's' : '' }}.</div>
          } @else {
            <div class="nft-grid">
              @for (n of visNfts(); track n.chain + n.image + n.tokenId) {
                <div class="nft-card">
                  <div class="nft-img" [style.--cc]="chainColor(n.chain)">
                    <img [src]="n.image" [alt]="n.name" loading="lazy" (error)="hideImg($event)" />
                    <span class="nft-chain-dot" [style.background]="chainColor(n.chain)" [title]="n.chain"></span>
                  </div>
                  <div class="nft-name">{{ n.name }}</div>
                  <div class="nft-coll">{{ n.collection }}</div>
                </div>
              }
            </div>
          }
        }

        @if (evmTab() === 'portfolio') {
        <!-- ── Wallet tokens table (Solana columns) — comes first, like Solana ── -->
        @if (visTokens().length > 0 || spamCount() > 0) {
          <section class="tok">
            <div class="tok-head">
              <div class="tok-title"><span class="tok-ico">◧</span> Wallet</div>
              <div class="tok-head-right">
                @if (spamCount() > 0) {
                  <button class="tok-spam-toggle" (click)="toggleSpam()">
                    {{ showSpam() ? 'Hide spam' : 'Show spam (' + spamCount() + ')' }}
                  </button>
                }
                <span class="tok-total">{{ walletUsd() | currency:'USD':'symbol':'1.2-2' }}</span>
              </div>
            </div>
            <div class="tok-table">
              <div class="tok-r tok-hr">
                <span>Token</span><span class="tok-num">Price</span><span class="tok-num">Amount</span>
                <span class="tok-num">%</span><span class="tok-num">USD Value</span>
              </div>
              @for (t of visTokens(); track t.network + t.address) {
                <div class="tok-r" [class.tok-spammy]="t.spam">
                  <span class="tok-name">
                    <span class="tok-logo" [style.--cc]="chainColor(t.chain)">
                      <span class="tok-logo-txt">{{ (t.symbol || '?').slice(0,3) }}</span>
                      @if (t.logo || dexLogo(t)) {
                        <img class="tok-logo-img" [src]="t.logo || dexLogo(t)" [alt]="t.symbol" (error)="onTokenLogoErr($event, t)" />
                      }
                      <span class="tok-chain-dot" [style.background]="chainColor(t.chain)" [title]="t.chain"></span>
                    </span>
                    <span class="tok-sym">{{ t.symbol || 'Unknown' }} <span class="tok-chain">{{ chainLabel(t.chain) }}</span>
                      @if (t.spam) { <span class="tok-spam-badge">spam</span> }</span>
                  </span>
                  <span class="tok-num">{{ t.priceUsd > 0 ? (t.priceUsd | currency:'USD':'symbol':'1.2-4') : '—' }}</span>
                  <span class="tok-num">{{ t.uiAmount | number:'1.0-4' }}</span>
                  <span class="tok-num tok-dim">{{ pct(t.valueUsd) | number:'1.0-1' }}%</span>
                  <span class="tok-num tok-val">{{ t.valueUsd | currency:'USD':'symbol':'1.2-2' }}</span>
                </div>
              }
            </div>
          </section>
        }

        <!-- ── Positions detail tables (Solana defi-positions) — below the wallet ── -->
        <app-defi-positions [protocolPositions]="protoPositions()" [loading]="false" />
        }

        <!-- ── Transactions tab ── -->
        @if (evmTab() === 'transactions') {
          <section class="tok">
            @if (visTxs().length === 0) { <div class="evm-empty">No recent transactions.</div> }
            @for (t of visTxs(); track t.hash + t.chain) {
              <div class="evm-tx">
                <div class="evm-tx-logo" [style.--cc]="chainColor(t.chain)">
                  @if (t.platformLogo) { <img [src]="t.platformLogo" [alt]="t.platform || ''" (error)="hideImg($event)" /> }
                  @else { <span>{{ categoryIcon(t.category) }}</span> }
                  <span class="tok-chain-dot" [style.background]="chainColor(t.chain)"></span>
                </div>
                <div class="evm-tx-main">
                  <div class="evm-tx-sum">{{ t.summary || t.category }}</div>
                  <div class="evm-tx-meta">
                    @if (t.platform) { <span class="evm-tx-plat">{{ t.platform }}</span> }
                    <span class="evm-tx-cat">{{ t.category }}</span><span class="tok-chain">{{ t.chain }}</span>
                  </div>
                </div>
                @if (!t.success) { <span class="evm-tx-failed">failed</span> }
              </div>
            }
          </section>
        }

        @if (tokens().length === 0 && positions().length === 0) {
          <div class="evm-empty">No balances or positions on Ethereum, Base, Arbitrum, Optimism or Polygon.</div>
        }
      }
    }
  `,
  styles: [`
    :host { display:block; }
    .evm-skel { margin-top:20px; } .evm-skel .sk { height:44px; border-radius:12px; margin-bottom:8px; background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:evsh 1.3s infinite; } .evm-skel .sk-lg { height:130px; }
    @keyframes evsh { 0%{background-position:200% 0} 100%{background-position:-200% 0} }

    .hero { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:28px 0 12px; flex-wrap:wrap; }
    .hero-row { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
    .hero-avatar { width:40px; height:40px; border-radius:50%; flex:none; display:grid; place-items:center; overflow:hidden; background:color-mix(in srgb, var(--cc,#627eea) 20%, transparent); }
    .hero-avatar img { width:40px; height:40px; border-radius:50%; object-fit:cover; }
    .hero-address { display:inline-flex; align-items:center; gap:8px; background:none; border:0; padding:0; cursor:pointer; font-family:var(--op-font-display); font-size:14px; font-weight:600; letter-spacing:-0.1px; color:var(--op-text-secondary); }
    .hero-address:hover { color:var(--op-brand,#5b5fc7); }
    .hero-total { font-size:42px; font-weight:800; color:var(--op-text-primary); font-family:var(--op-font-display); letter-spacing:-1.5px; font-variant-numeric:tabular-nums; line-height:1.1; }
    @media (max-width:640px) { .hero-total { font-size:32px; } }
    .hero-sub { font-size:.82rem; color:var(--op-text-secondary); margin-top:6px; }
    .hero-right { flex:none; }

    .chain-tabs { display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 2px; }
    .chain-tab { display:inline-flex; align-items:center; gap:6px; padding:6px 12px; border:1px solid var(--op-border, rgba(255,255,255,.1)); border-radius:999px; background:transparent; color:var(--op-text-secondary); font-size:.8rem; font-weight:500; cursor:pointer; transition:.12s; }
    .chain-tab:hover { color:var(--op-text-primary); }
    .chain-tab.on { border-color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand,#5b5fc7) 10%, transparent); color:var(--op-text-primary); }
    .chain-tab-dot { width:8px; height:8px; border-radius:50%; }
    .tok-head-right { display:flex; align-items:center; gap:12px; }
    .tok-spam-toggle { background:none; border:0; color:var(--op-text-secondary); font-size:.76rem; cursor:pointer; text-decoration:underline; }
    .tok-spam-toggle:hover { color:var(--op-text-primary); }
    .tok-spammy { opacity:.62; }
    .tok-spam-badge { font-size:.56rem; font-weight:700; text-transform:uppercase; color:#ef4444; background:color-mix(in srgb, #ef4444 15%, transparent); padding:1px 5px; border-radius:5px; }
    .evm-tabs { display:flex; gap:24px; padding:6px 0 0; margin:16px 0 4px; border-bottom:1px solid var(--op-border, rgba(125,125,150,.15)); }
    .evm-tab { background:none; border:0; padding:0 0 10px; font-size:.92rem; font-weight:600; color:var(--op-text-secondary); cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px; }
    .evm-tab:hover { color:var(--op-text-primary); }
    .evm-tab.on { color:var(--op-brand,#5b5fc7); border-bottom-color:var(--op-brand,#5b5fc7); }
    .proto-cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:10px; margin:14px 0 4px; }
    @media (max-width:520px) { .proto-cards { grid-template-columns:repeat(auto-fill, minmax(120px, 1fr)); gap:8px; } }
    .proto-card { display:flex; align-items:center; gap:10px; padding:10px 12px; border:1px solid var(--op-border-subtle, rgba(255,255,255,.08)); border-radius:var(--op-radius-md, 12px); background:var(--op-bg-surface-1); }
    .proto-card-ico { width:30px; height:30px; border-radius:9px; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 16%, transparent); overflow:hidden; }
    .proto-card-ico img { width:30px; height:30px; border-radius:9px; object-fit:cover; }
    .proto-card-dot { width:12px; height:12px; border-radius:4px; }
    .proto-card-text { display:flex; flex-direction:column; min-width:0; }
    .proto-card-name { font-size:.78rem; color:var(--op-text-secondary); }
    .proto-card-usd { font-size:1.05rem; font-weight:700; color:var(--op-text-primary); font-variant-numeric:tabular-nums; }

    .tok { margin-top:16px; }
    .tok-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
    .tok-title { display:flex; align-items:center; gap:8px; font-weight:700; font-size:1.05rem; color:var(--op-text-primary); }
    .tok-ico { color:var(--op-text-secondary); }
    .tok-total { font-weight:700; color:var(--op-text-primary); font-variant-numeric:tabular-nums; }
    .tok-table { border:1px solid var(--op-border-subtle, rgba(255,255,255,.08)); border-radius:var(--op-radius-lg, 16px); overflow:hidden; background:var(--op-bg-surface-1); box-shadow:var(--op-shadow-sm); }
    .tok-r { display:grid; grid-template-columns:2.2fr 1fr 1fr .7fr 1fr; align-items:center; gap:8px; padding:12px 16px; }
    .tok-r:not(.tok-hr):hover { background:var(--op-bg-surface-2, rgba(125,125,150,.05)); }
    .tok-r + .tok-r { border-top:1px solid var(--op-border, rgba(255,255,255,.05)); }
    .tok-hr { font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; color:var(--op-text-secondary); border-bottom:1px solid var(--op-border-subtle, rgba(125,125,150,.12)); }
    .tok-num { text-align:right; font-variant-numeric:tabular-nums; font-size:14px; font-family:var(--op-font-body); color:var(--op-text-primary); }
    .tok-dim { color:var(--op-text-secondary); } .tok-val { font-weight:700; }
    .tok-name { display:flex; align-items:center; gap:10px; min-width:0; }
    .tok-logo { position:relative; width:32px; height:32px; border-radius:50%; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 18%, transparent); color:var(--op-text-primary); font-size:.58rem; font-weight:700; overflow:visible; }
    .tok-logo-txt { z-index:0; }
    .tok-logo-img { position:absolute; inset:0; width:32px; height:32px; border-radius:50%; object-fit:cover; background:var(--op-bg-surface-1); z-index:1; }
    .tok-chain-dot { position:absolute; right:-2px; bottom:-2px; width:11px; height:11px; border-radius:50%; border:2px solid var(--op-bg-surface-1); }
    .tok-sym { font-weight:600; color:var(--op-text-primary); font-size:.9rem; display:flex; align-items:center; gap:7px; }
    .tok-chain { font-size:.62rem; text-transform:capitalize; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:1px 6px; border-radius:5px; }

    .evm-tx { display:flex; align-items:center; gap:12px; padding:8px 4px; border-radius:10px; }
    .evm-tx:hover { background:var(--op-bg-surface-2, rgba(125,125,150,.06)); }
    .evm-tx-logo { position:relative; width:30px; height:30px; border-radius:50%; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 16%, transparent); color:var(--op-text-primary); font-size:.85rem; }
    .evm-tx-logo img { width:30px; height:30px; border-radius:50%; object-fit:cover; }
    .evm-tx-main { flex:1; min-width:0; }
    .evm-tx-sum { font-size:.85rem; color:var(--op-text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .evm-tx-meta { display:flex; align-items:center; gap:6px; margin-top:2px; }
    .evm-tx-plat { font-size:.64rem; font-weight:700; color:var(--op-brand,#5b5fc7); }
    .evm-tx-cat { font-size:.64rem; text-transform:capitalize; color:var(--op-text-secondary); }
    .evm-tx-failed { font-size:.64rem; font-weight:700; color:#ef4444; text-transform:uppercase; }
    .evm-empty { font-size:.85rem; color:var(--op-text-secondary); padding:16px 2px; }
    .nft-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:14px; margin-top:14px; }
    .nft-card { min-width:0; }
    .nft-img { position:relative; aspect-ratio:1; border-radius:14px; overflow:hidden; background:color-mix(in srgb, var(--cc) 12%, var(--op-bg-surface-2, rgba(125,125,150,.08))); }
    .nft-img img { width:100%; height:100%; object-fit:cover; display:block; }
    .nft-chain-dot { position:absolute; right:8px; bottom:8px; width:14px; height:14px; border-radius:50%; border:2px solid var(--op-bg-surface-1); }
    .nft-name { font-size:.85rem; font-weight:600; color:var(--op-text-primary); margin-top:8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .nft-coll { font-size:.74rem; color:var(--op-text-secondary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    @media (max-width:640px) { .tok-r { grid-template-columns:1.6fr 1fr 1fr; } .tok-r > :nth-child(4), .tok-r > :nth-child(5) { display:none; } }
  `],
})
export class EvmHoldingsComponent implements OnInit {
  private account = inject(AccountService);
  private evm = inject(EvmPortfolioService);
  private api = inject(ApiService);
  private lighterPerp = inject(LighterPerpService);

  loading = signal(true);
  walletCount = signal(0);
  address = signal<string | null>(null);
  tokens = signal<EvmToken[]>([]);
  positions = signal<EvmPosition[]>([]);
  txs = signal<EvmTx[]>([]);
  nfts = signal<EvmNft[]>([]);
  copied = signal(false);
  showSpam = signal(false);
  evmTab = signal<'portfolio' | 'nfts' | 'transactions'>('portfolio');
  chainFilter = signal<string>('all');
  // Driven by the portfolio's top chain switcher: 'all' or a specific chain.
  @Input() set chain(c: string | null | undefined) { this.chainFilter.set(c || 'all'); }

  // Chains that actually have holdings/positions — drives the top filter tabs.
  availableChains = computed(() => {
    const set = new Set<string>();
    for (const t of this.tokens()) if (!t.spam) set.add(t.chain);
    for (const p of this.positions()) set.add(p.chain);
    return [...set].sort((a, b) => CHAIN_ORDER.indexOf(a) - CHAIN_ORDER.indexOf(b));
  });
  spamCount = computed(() => this.tokens().filter((t) => t.spam && this.chainMatch(t.chain)).length);

  private chainMatch(chain: string): boolean {
    const f = this.chainFilter();
    return f === 'all' || f === chain;
  }

  // Visible sets after chain + spam filtering — everything downstream keys on these.
  visTokens = computed(() =>
    this.tokens().filter((t) => this.chainMatch(t.chain) && (this.showSpam() || !t.spam)));
  visPositions = computed(() => this.positions().filter((p) => this.chainMatch(p.chain)));
  visTxs = computed(() => this.txs().filter((t) => this.chainMatch(t.chain)));
  visNfts = computed(() => this.nfts().filter((n) => this.chainMatch(n.chain)));

  walletUsd = computed(() => this.visTokens().filter((t) => !t.spam).reduce((s, t) => s + (t.valueUsd || 0), 0));
  positionsUsd = computed(() => this.visPositions().reduce((s, p) => s + (p.balanceUsd || 0), 0));
  totalUsd = computed(() => this.walletUsd() + this.positionsUsd());

  /** Category cards + donut source: Wallet + each protocol, biggest first. */
  cards = computed(() => {
    const cats: { name: string; usd: number; logo?: string }[] = [];
    const wallet = this.walletUsd();
    if (wallet > 0) cats.push({ name: 'Wallet', usd: wallet });
    const byProto = new Map<string, { usd: number; logo?: string }>();
    for (const p of this.visPositions()) {
      const cur = byProto.get(p.protocol) || { usd: 0, logo: p.logo };
      cur.usd += p.balanceUsd || 0;
      if (!cur.logo && p.logo) cur.logo = p.logo;
      byProto.set(p.protocol, cur);
    }
    for (const [name, v] of byProto) cats.push({ name, usd: v.usd, logo: v.logo });
    cats.sort((a, b) => b.usd - a.usd);
    return cats.map((c, i) => ({ ...c, color: ALLOC_COLORS[i % ALLOC_COLORS.length] }));
  });

  allocationSegments = computed<ChartSegment[]>(() =>
    this.cards().filter((c) => c.usd > 0).map((c) => ({ label: c.name, value: c.usd, color: c.color })));

  /** EVM positions mapped into the Solana ProtocolPosition shape so the exact
   *  DefiPositionsComponent panel renders them. */
  protoPositions = computed<ProtocolPosition[]>(() => {
    const byProto = new Map<string, ProtocolPosition>();
    for (const p of this.visPositions()) {
      let pp = byProto.get(p.protocol);
      if (!pp) {
        pp = {
          protocolId: p.protocolId || p.protocol.toLowerCase().replace(/\s+/g, '-'),
          protocolName: p.protocol,
          protocolLogoUri: p.logo || null,
          category: categoryFor(p.label),
          positions: [],
          totalUsdValue: 0,
          totalClaimableUsd: 0,
          claimableCount: 0,
        };
        byProto.set(p.protocol, pp);
      }
      pp.positions.push({
        label: `${p.label}${p.chain ? ' · ' + p.chain : ''}`,
        tokens: (p.tokens || []).map((t) => ({ symbol: t.symbol, amount: t.amount, logoUri: t.logo || null })),
        totalUsdValue: p.balanceUsd,
        metadata: {},
        claimableUsd: p.unclaimedUsd > 0 ? p.unclaimedUsd : null,
        feesUsd: p.unclaimedUsd > 0 ? p.unclaimedUsd : null,
      });
      pp.totalUsdValue += p.balanceUsd || 0;
      if (p.unclaimedUsd > 0) { pp.totalClaimableUsd = (pp.totalClaimableUsd || 0) + p.unclaimedUsd; pp.claimableCount = (pp.claimableCount || 0) + 1; }
    }
    return [...byProto.values()].sort((a, b) => b.totalUsdValue - a.totalUsdValue);
  });

  ngOnInit(): void {
    this.account.getMe().subscribe({
      next: (me) => {
        const evmWallets = (me.identities || []).filter((i) => i.type === 'evm_wallet').map((i) => i.identifier);
        this.walletCount.set(evmWallets.length);
        this.address.set(evmWallets[0] ?? null);
        if (evmWallets.length === 0) { this.loading.set(false); return; }

        forkJoin({
          perWallet: forkJoin(
            evmWallets.map((addr) =>
              forkJoin({
                portfolio: this.evm.getPortfolio(addr).pipe(catchError(() => of({ address: addr, totalUsd: 0, tokens: [] }))),
                positions: this.evm.getPositions(addr).pipe(catchError(() => of({ address: addr, totalUsd: 0, positions: [] }))),
                transactions: this.evm.getTransactions(addr).pipe(catchError(() => of({ address: addr, transactions: [] }))),
                nfts: this.evm.getNfts(addr).pipe(catchError(() => of({ address: addr, nfts: [] }))),
              }),
            ),
          ),
          // Uniswap V2/V3/V4 LP positions (all chains, incl. Robinhood) —
          // Moralis doesn't cover them, so pull from Uniswap's own feed. Loaded
          // together with the wallet so positions never pop in after the wallet.
          uniswap: this.uniswapPositions$(),
          // Lighter perps live on Robinhood Chain but aren't in Moralis/Alchemy
          // (they're L2 sequencer positions), so pull them from Lighter's own
          // API and fold them into the same positions pipeline.
          lighter: this.lighterPositions$(evmWallets[0]),
        })
          .pipe(map(({ perWallet, uniswap, lighter }) => ({
            tokens: perWallet.flatMap((r) => r.portfolio.tokens || []).sort((a, b) => b.valueUsd - a.valueUsd),
            positions: [...uniswap, ...lighter, ...perWallet.flatMap((r) => r.positions.positions || [])].sort((a, b) => b.balanceUsd - a.balanceUsd),
            txs: perWallet.flatMap((r) => r.transactions.transactions || []).sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1)).slice(0, 25),
            nfts: perWallet.flatMap((r) => r.nfts.nfts || []),
          })))
          .subscribe(({ tokens, positions, txs, nfts }) => {
            this.tokens.set(tokens);
            this.positions.set(positions);
            this.txs.set(txs);
            this.nfts.set(nfts);
            this.loading.set(false);
          });
      },
      error: () => { this.loading.set(false); },
    });
  }

  /** Uniswap LP positions (V2/V3/V4, all chains) mapped to EvmPosition. Emits
   *  [] on any error so it never blocks the combined wallet+positions load. */
  /** Lighter perp positions (Robinhood Chain, chain 4663) mapped into the EVM
   *  positions pipeline so they render under the Robinhood tab like Uniswap.
   *  Value = collateral + unrealised PnL (the position's live equity, not the
   *  leveraged notional). */
  private lighterPositions$(addr: string | undefined) {
    if (!addr) return of([] as EvmPosition[]);
    return from(this.lighterPerp.getPositions(addr)).pipe(
      catchError(() => of([] as LighterPosition[])),
      map((positions): EvmPosition[] => (positions || []).filter((p) => !p.closed).map((p) => ({
        chain: 'robinhood',
        protocol: 'Lighter',
        protocolId: 'lighter',
        logo: 'assets/protocols/lighter.png',
        label: `${p.market}-PERP ${p.side}${p.leverage ? ' · ' + p.leverage.toFixed(0) + 'x' : ''}`,
        balanceUsd: (Number(p.collateral) || 0) + (Number(p.unrealizedPnl) || 0),
        unclaimedUsd: 0,
        tokens: [{ symbol: p.market, type: p.side, amount: Number(p.baseAmount) || 0, logo: undefined }],
      }))),
    );
  }

  private uniswapPositions$() {
    return this.api.post<{ positions: any[] }>('/actions/uniswap/lp/positions', {}).pipe(
      catchError(() => of({ positions: [] })),
      map((res): EvmPosition[] => (res?.positions || []).map((p) => ({
        chain: p.chain,
        protocol: 'Uniswap',
        protocolId: 'uniswap',
        logo: 'assets/protocols/uniswap.jpg',
        label: `${p.pair} ${String(p.version || '').toUpperCase()}${p.inRange ? '' : ' · out of range'}`,
        balanceUsd: Number(p.valueUsd) || 0,
        unclaimedUsd: Number(p.uncollectedFeesUsd) || 0,
        tokens: [
          { symbol: p.token0?.symbol, type: 'supplied', amount: Number(p.token0?.amountDisplay) || 0, logo: undefined },
          { symbol: p.token1?.symbol, type: 'supplied', amount: Number(p.token1?.amountDisplay) || 0, logo: undefined },
        ],
      }))),
    );
  }

  heroChain(): string { const c = this.chainFilter(); return c === 'all' ? 'ethereum' : c; }
  heroIcon(): string { return chainLogo(this.heroChain()); }
  chainColor(chain: string): string { return CHAIN_COLOR[chain] ?? '#7e8298'; }
  chainLabel(chain: string): string { return CHAIN_LABEL[chain] ?? chain; }
  setChainFilter(c: string): void { this.chainFilter.set(c); }
  toggleSpam(): void { this.showSpam.update((v) => !v); }
  shortAddr(a: string | null): string { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : ''; }
  pct(usd: number): number { const t = this.totalUsd() || 1; return (usd / t) * 100; }
  copyAddr(): void {
    const a = this.address(); if (!a) return;
    navigator.clipboard?.writeText(a);
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 1400);
  }
  categoryIcon(category: string): string {
    const c = (category || '').toLowerCase();
    if (c.includes('swap')) return '⇄'; if (c.includes('nft')) return '◈';
    if (c.includes('send')) return '↑'; if (c.includes('receive') || c.includes('airdrop')) return '↓';
    if (c.includes('approve')) return '✓'; return '•';
  }
  hideImg(ev: Event): void { (ev.target as HTMLImageElement).style.display = 'none'; }

  /** DexScreener token image — great memecoin/long-tail coverage where Trust
   *  Wallet has nothing (cbBTC, memecoins, …). Accepts lowercase addresses. */
  dexLogo(t: EvmToken): string {
    if (t.native || !t.address || t.address === 'native') return '';
    return `https://dd.dexscreener.com/ds-data/tokens/${t.chain}/${t.address.toLowerCase()}.png`;
  }

  /** Logo fallback cascade: Trust Wallet → DexScreener → text badge (the img is
   *  hidden and the symbol underneath shows through). */
  onTokenLogoErr(ev: Event, t: EvmToken): void {
    const img = ev.target as HTMLImageElement;
    const dex = this.dexLogo(t);
    if (dex && img.src !== dex) { img.src = dex; return; }
    img.style.display = 'none';
  }
}
