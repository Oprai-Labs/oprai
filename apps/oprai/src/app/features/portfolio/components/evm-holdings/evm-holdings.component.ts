import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AccountService } from '../../../../core/services/account.service';
import { EvmPortfolioService, EvmToken, EvmPosition, EvmTx } from '../../services/evm-portfolio.service';

const CHAIN_COLOR: Record<string, string> = {
  ethereum: '#627eea',
  base: '#0052ff',
  arbitrum: '#28a0f0',
  optimism: '#ff0420',
  polygon: '#8247e5',
};

// Allocation palette (Wallet first, then protocols) — mirrors the Solana donut.
const ALLOC_COLORS = ['#5b5fc7', '#06b6d4', '#22c55e', '#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#ef4444'];

interface AllocCat {
  name: string;
  usd: number;
  color: string;
  logo?: string;
  pct: number;
}

@Component({
  selector: 'app-evm-holdings',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (walletCount() > 0) {
      @if (loading()) {
        <div class="evm-skel"><div class="sk sk-lg"></div><div class="sk"></div><div class="sk"></div></div>
      } @else {
        <!-- ── Overview: address · total · allocation donut ── -->
        <section class="evm-overview">
          <div class="evm-ov-left">
            @if (address()) {
              <button class="evm-addr" (click)="copyAddr()" title="Copy address">
                <span class="evm-addr-ico"><svg width="16" height="16" viewBox="0 0 256 417" aria-hidden="true"><path fill="#627eea" d="M127.9 0l-2.7 9.5v275.7l2.7 2.7 127.9-75.6z"/><path fill="#8a92b2" d="M127.9 0L0 212.3l127.9 75.6V154.2z"/><path fill="#627eea" d="M127.9 312.2l-1.5 1.9v98.2l1.5 4.5L256 236.6z"/><path fill="#8a92b2" d="M127.9 416.9v-104.7L0 236.6z"/></svg></span>
                {{ shortAddr(address()) }}
                <span class="evm-copy">{{ copied() ? '✓' : '⧉' }}</span>
              </button>
            }
            <div class="evm-total">{{ totalUsd() | currency:'USD':'symbol':'1.2-2' }}</div>
            <div class="evm-sub">Ethereum &amp; L2s · {{ tokens().length }} tokens · {{ positions().length }} positions</div>
          </div>

          @if (categories().length > 0) {
            <div class="evm-alloc">
              <svg viewBox="0 0 120 120" class="evm-donut" aria-hidden="true">
                @for (seg of donut(); track seg.name) {
                  <circle cx="60" cy="60" r="54" fill="none" [attr.stroke]="seg.color" stroke-width="12"
                          [attr.stroke-dasharray]="seg.dash" [attr.stroke-dashoffset]="seg.offset" transform="rotate(-90 60 60)" />
                }
                <text x="60" y="64" text-anchor="middle" class="evm-donut-mid">{{ totalUsd() | currency:'USD':'symbol':'1.0-0' }}</text>
              </svg>
              <div class="evm-legend">
                @for (c of categories(); track c.name) {
                  <div class="evm-leg">
                    <span class="evm-leg-dot" [style.background]="c.color"></span>
                    <span class="evm-leg-name">{{ c.name }}</span>
                    <span class="evm-leg-pct">{{ c.pct | number:'1.0-1' }}%</span>
                  </div>
                }
              </div>
            </div>
          }
        </section>

        <!-- ── Category cards ── -->
        @if (categories().length > 0) {
          <div class="evm-cards">
            @for (c of categories(); track c.name) {
              <div class="evm-card">
                <div class="evm-card-ico" [style.--cc]="c.color">
                  @if (c.logo) { <img [src]="c.logo" [alt]="c.name" (error)="hideImg($event)" /> }
                  @else { <span class="evm-card-dot" [style.background]="c.color"></span> }
                </div>
                <div class="evm-card-text">
                  <span class="evm-card-name">{{ c.name }}</span>
                  <span class="evm-card-usd">{{ c.usd | currency:'USD':'symbol':'1.2-2' }}</span>
                </div>
              </div>
            }
          </div>
        }

        <!-- ── Active positions & rewards ── -->
        @if (positions().length > 0) {
          <section class="evm-block">
            <div class="evm-block-head">
              <div class="evm-block-title">
                <span>Active positions</span>
                <span class="evm-block-sub">{{ positions().length }} across {{ protocolCount() }} protocols</span>
              </div>
              <div class="evm-block-total">{{ positionsUsd() | currency:'USD':'symbol':'1.2-2' }}</div>
            </div>
            @for (p of positions(); track p.chain + p.protocol + p.label + $index) {
              <div class="evm-pos">
                <div class="evm-pos-logo" [style.--cc]="chainColor(p.chain)">
                  @if (p.logo) { <img [src]="p.logo" [alt]="p.protocol" (error)="hideImg($event)" /> }
                  @else { <span>{{ (p.protocol || '?').slice(0, 2) }}</span> }
                  <span class="evm-chain-dot" [style.background]="chainColor(p.chain)" [title]="p.chain"></span>
                </div>
                <div class="evm-pos-main">
                  <div class="evm-pos-proto">{{ p.protocol }} <span class="evm-pos-label">{{ p.label }}</span></div>
                  <div class="evm-pos-toks">{{ tokenSummary(p) }} <span class="evm-pos-chain">{{ p.chain }}</span></div>
                </div>
                <div class="evm-pos-val">
                  <div class="evm-usd">{{ p.balanceUsd | currency:'USD':'symbol':'1.2-2' }}</div>
                  @if (p.unclaimedUsd > 0) { <div class="evm-pos-unclaimed">+{{ p.unclaimedUsd | currency:'USD':'symbol':'1.2-2' }} rewards</div> }
                </div>
              </div>
            }
          </section>
        }

        <!-- ── Tokens ── -->
        @if (tokens().length > 0) {
          <section class="evm-block">
            <div class="evm-block-head"><div class="evm-block-title"><span>Tokens</span></div>
              <div class="evm-block-total">{{ walletUsd() | currency:'USD':'symbol':'1.2-2' }}</div></div>
            @for (t of tokens(); track t.network + t.address) {
              <div class="evm-row">
                <div class="evm-logo" [style.--cc]="chainColor(t.chain)">
                  @if (t.logo) { <img [src]="t.logo" [alt]="t.symbol" (error)="hideImg($event)" /> }
                  @else { <span>{{ (t.symbol || '?').slice(0, 3) }}</span> }
                  <span class="evm-chain-dot" [style.background]="chainColor(t.chain)" [title]="t.chain"></span>
                </div>
                <div class="evm-main">
                  <div class="evm-sym">{{ t.symbol || 'Unknown' }} <span class="evm-chain">{{ t.chain }}</span></div>
                  <div class="evm-amt">{{ t.uiAmount | number:'1.0-4' }} {{ t.symbol }}</div>
                </div>
                <div class="evm-val">
                  <div class="evm-usd">{{ t.valueUsd | currency:'USD':'symbol':'1.2-2' }}</div>
                  @if (t.priceUsd > 0) { <div class="evm-price">{{ t.priceUsd | currency:'USD':'symbol':'1.2-4' }}</div> }
                </div>
              </div>
            }
          </section>
        }

        <!-- ── Recent activity ── -->
        @if (txs().length > 0) {
          <section class="evm-block">
            <div class="evm-block-head"><div class="evm-block-title"><span>Recent Activity</span></div></div>
            @for (t of txs(); track t.hash + t.chain) {
              <div class="evm-tx">
                <div class="evm-tx-logo" [style.--cc]="chainColor(t.chain)">
                  @if (t.platformLogo) { <img [src]="t.platformLogo" [alt]="t.platform || ''" (error)="hideImg($event)" /> }
                  @else { <span>{{ categoryIcon(t.category) }}</span> }
                  <span class="evm-chain-dot" [style.background]="chainColor(t.chain)" [title]="t.chain"></span>
                </div>
                <div class="evm-tx-main">
                  <div class="evm-tx-sum">{{ t.summary || t.category }}</div>
                  <div class="evm-tx-meta">
                    @if (t.platform) { <span class="evm-tx-plat">{{ t.platform }}</span> }
                    <span class="evm-tx-cat">{{ t.category }}</span>
                    <span class="evm-pos-chain">{{ t.chain }}</span>
                  </div>
                </div>
                @if (!t.success) { <span class="evm-tx-failed" title="Reverted">failed</span> }
              </div>
            }
          </section>
        }

        @if (tokens().length === 0 && positions().length === 0) {
          <div class="evm-empty">No balances or positions found on Ethereum, Base, Arbitrum, Optimism or Polygon.</div>
        }
      }
    }
  `,
  styles: [`
    :host { display:block; }
    .evm-skel { margin-top:16px; } .evm-skel .sk { height:44px; border-radius:12px; margin-bottom:8px; background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:evsh 1.3s infinite; } .evm-skel .sk-lg { height:120px; }
    @keyframes evsh { 0%{background-position:200% 0} 100%{background-position:-200% 0} }

    .evm-overview { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:24px 0 8px; flex-wrap:wrap; }
    .evm-addr { display:inline-flex; align-items:center; gap:8px; background:none; border:0; padding:0; cursor:pointer; font-family:ui-monospace,monospace; font-size:.85rem; color:var(--op-text-secondary); margin-bottom:8px; }
    .evm-addr:hover { color:var(--op-brand,#5b5fc7); }
    .evm-addr-ico { display:inline-flex; }
    .evm-copy { font-size:.9rem; }
    .evm-total { font-size:2.2rem; font-weight:700; color:var(--op-text-primary); letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
    .evm-sub { font-size:.82rem; color:var(--op-text-secondary); margin-top:4px; }
    .evm-alloc { display:flex; align-items:center; gap:16px; }
    .evm-donut { width:118px; height:118px; flex:none; }
    .evm-donut-mid { font-size:15px; font-weight:700; fill:var(--op-text-primary); }
    .evm-legend { display:flex; flex-direction:column; gap:5px; }
    .evm-leg { display:flex; align-items:center; gap:8px; font-size:.8rem; }
    .evm-leg-dot { width:9px; height:9px; border-radius:3px; flex:none; }
    .evm-leg-name { color:var(--op-text-primary); min-width:64px; }
    .evm-leg-pct { color:var(--op-text-secondary); font-variant-numeric:tabular-nums; }

    .evm-cards { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0 4px; }
    .evm-card { display:flex; align-items:center; gap:10px; flex:1; min-width:150px; padding:12px 14px; border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:14px; background:var(--op-bg-surface-1); }
    .evm-card-ico { width:30px; height:30px; border-radius:9px; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 16%, transparent); overflow:hidden; }
    .evm-card-ico img { width:30px; height:30px; border-radius:9px; object-fit:cover; }
    .evm-card-dot { width:12px; height:12px; border-radius:4px; }
    .evm-card-text { display:flex; flex-direction:column; min-width:0; }
    .evm-card-name { font-size:.78rem; color:var(--op-text-secondary); }
    .evm-card-usd { font-size:1rem; font-weight:700; color:var(--op-text-primary); font-variant-numeric:tabular-nums; }

    .evm-block { margin-top:16px; background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:16px; padding:14px 16px; }
    .evm-block-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
    .evm-block-title { display:flex; align-items:center; gap:8px; font-weight:600; font-size:.95rem; color:var(--op-text-primary); }
    .evm-block-sub { font-size:.75rem; color:var(--op-text-secondary); font-weight:400; }
    .evm-block-total { font-weight:700; color:var(--op-text-primary); font-variant-numeric:tabular-nums; }

    .evm-row, .evm-pos, .evm-tx { display:flex; align-items:center; gap:12px; padding:9px 4px; border-radius:10px; }
    .evm-row:hover, .evm-pos:hover, .evm-tx:hover { background:var(--op-bg-surface-2, rgba(125,125,150,.06)); }
    .evm-logo, .evm-pos-logo, .evm-tx-logo { position:relative; width:34px; height:34px; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 18%, transparent); color:var(--op-text-primary); font-size:.6rem; font-weight:700; }
    .evm-logo { border-radius:50%; } .evm-pos-logo { border-radius:9px; } .evm-tx-logo { border-radius:50%; font-size:.85rem; width:30px; height:30px; }
    .evm-logo img, .evm-pos-logo img, .evm-tx-logo img { width:100%; height:100%; object-fit:cover; border-radius:inherit; }
    .evm-chain-dot { position:absolute; right:-2px; bottom:-2px; width:12px; height:12px; border-radius:50%; border:2px solid var(--op-bg-surface-1); }
    .evm-main, .evm-pos-main, .evm-tx-main { flex:1; min-width:0; }
    .evm-sym, .evm-pos-proto { font-weight:600; color:var(--op-text-primary); font-size:.9rem; display:flex; align-items:center; gap:7px; }
    .evm-chain, .evm-pos-chain { font-size:.64rem; text-transform:capitalize; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:1px 6px; border-radius:5px; }
    .evm-pos-label { font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand,#5b5fc7) 15%, transparent); padding:1px 6px; border-radius:5px; }
    .evm-amt, .evm-pos-toks, .evm-tx-meta { font-size:.78rem; color:var(--op-text-secondary); display:flex; align-items:center; gap:7px; }
    .evm-val, .evm-pos-val { text-align:right; }
    .evm-usd { font-weight:600; color:var(--op-text-primary); font-size:.9rem; font-variant-numeric:tabular-nums; }
    .evm-price { font-size:.72rem; color:var(--op-text-secondary); font-variant-numeric:tabular-nums; }
    .evm-pos-unclaimed { font-size:.7rem; color:#22c55e; font-variant-numeric:tabular-nums; }
    .evm-tx-sum { font-size:.85rem; color:var(--op-text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .evm-tx-plat { font-size:.64rem; font-weight:700; color:var(--op-brand,#5b5fc7); }
    .evm-tx-cat { font-size:.64rem; text-transform:capitalize; color:var(--op-text-secondary); }
    .evm-tx-failed { font-size:.64rem; font-weight:700; color:#ef4444; text-transform:uppercase; }
    .evm-empty { font-size:.85rem; color:var(--op-text-secondary); padding:16px 2px; }
  `],
})
export class EvmHoldingsComponent implements OnInit {
  private account = inject(AccountService);
  private evm = inject(EvmPortfolioService);

  loading = signal(true);
  walletCount = signal(0);
  address = signal<string | null>(null);
  tokens = signal<EvmToken[]>([]);
  positions = signal<EvmPosition[]>([]);
  txs = signal<EvmTx[]>([]);
  copied = signal(false);

  walletUsd = computed(() => this.tokens().reduce((s, t) => s + (t.valueUsd || 0), 0));
  positionsUsd = computed(() => this.positions().reduce((s, p) => s + (p.balanceUsd || 0), 0));
  totalUsd = computed(() => this.walletUsd() + this.positionsUsd());
  protocolCount = computed(() => new Set(this.positions().map((p) => p.protocol)).size);

  // Allocation by category: Wallet + each protocol (biggest first).
  categories = computed<AllocCat[]>(() => {
    const total = this.totalUsd() || 1;
    const cats: { name: string; usd: number; logo?: string }[] = [];
    const wallet = this.walletUsd();
    if (wallet > 0) cats.push({ name: 'Wallet', usd: wallet });
    const byProto = new Map<string, { usd: number; logo?: string }>();
    for (const p of this.positions()) {
      const cur = byProto.get(p.protocol) || { usd: 0, logo: p.logo };
      cur.usd += p.balanceUsd || 0;
      if (!cur.logo && p.logo) cur.logo = p.logo;
      byProto.set(p.protocol, cur);
    }
    for (const [name, v] of byProto) cats.push({ name, usd: v.usd, logo: v.logo });
    cats.sort((a, b) => b.usd - a.usd);
    return cats.map((c, i) => ({ ...c, color: ALLOC_COLORS[i % ALLOC_COLORS.length], pct: (c.usd / total) * 100 }));
  });

  // Donut stroke segments (circumference of r=54).
  donut = computed(() => {
    const C = 2 * Math.PI * 54;
    let acc = 0;
    return this.categories().filter((c) => c.pct > 0).map((c) => {
      const len = (c.pct / 100) * C;
      const seg = { name: c.name, color: c.color, dash: `${len} ${C - len}`, offset: -acc };
      acc += len;
      return seg;
    });
  });

  ngOnInit(): void {
    this.account.getMe().subscribe({
      next: (me) => {
        const evmWallets = (me.identities || []).filter((i) => i.type === 'evm_wallet').map((i) => i.identifier);
        this.walletCount.set(evmWallets.length);
        this.address.set(evmWallets[0] ?? null);
        if (evmWallets.length === 0) { this.loading.set(false); return; }

        forkJoin(
          evmWallets.map((addr) =>
            forkJoin({
              portfolio: this.evm.getPortfolio(addr).pipe(catchError(() => of({ address: addr, totalUsd: 0, tokens: [] }))),
              positions: this.evm.getPositions(addr).pipe(catchError(() => of({ address: addr, totalUsd: 0, positions: [] }))),
              transactions: this.evm.getTransactions(addr).pipe(catchError(() => of({ address: addr, transactions: [] }))),
            }),
          ),
        )
          .pipe(map((results) => ({
            tokens: results.flatMap((r) => r.portfolio.tokens || []).sort((a, b) => b.valueUsd - a.valueUsd),
            positions: results.flatMap((r) => r.positions.positions || []).sort((a, b) => b.balanceUsd - a.balanceUsd),
            txs: results.flatMap((r) => r.transactions.transactions || []).sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1)).slice(0, 25),
          })))
          .subscribe(({ tokens, positions, txs }) => {
            this.tokens.set(tokens);
            this.positions.set(positions);
            this.txs.set(txs);
            this.loading.set(false);
          });
      },
      error: () => { this.loading.set(false); },
    });
  }

  chainColor(chain: string): string { return CHAIN_COLOR[chain] ?? '#7e8298'; }
  shortAddr(a: string | null): string { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : ''; }
  copyAddr(): void {
    const a = this.address();
    if (!a) return;
    navigator.clipboard?.writeText(a);
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 1400);
  }
  tokenSummary(p: EvmPosition): string {
    const uniq = Array.from(new Set((p.tokens || []).map((t) => t.symbol).filter((s): s is string => !!s)));
    return uniq.slice(0, 3).join(' + ') || '—';
  }
  categoryIcon(category: string): string {
    const c = (category || '').toLowerCase();
    if (c.includes('swap')) return '⇄';
    if (c.includes('nft')) return '◈';
    if (c.includes('send')) return '↑';
    if (c.includes('receive') || c.includes('airdrop')) return '↓';
    if (c.includes('approve')) return '✓';
    return '•';
  }
  hideImg(ev: Event): void { (ev.target as HTMLImageElement).style.display = 'none'; }
}
