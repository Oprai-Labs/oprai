import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AccountService } from '../../../../core/services/account.service';
import { EvmPortfolioService, EvmToken, EvmPosition } from '../../services/evm-portfolio.service';

const CHAIN_COLOR: Record<string, string> = {
  ethereum: '#627eea',
  base: '#0052ff',
  arbitrum: '#28a0f0',
  optimism: '#ff0420',
  polygon: '#8247e5',
};

@Component({
  selector: 'app-evm-holdings',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (walletCount() > 0) {
      <section class="evm">
        <header class="evm-head">
          <div class="evm-title">
            <span class="evm-badge">EVM</span>
            <span>Ethereum &amp; L2s</span>
            <span class="evm-wallets">{{ walletCount() }} wallet{{ walletCount() > 1 ? 's' : '' }}</span>
          </div>
          <div class="evm-total">{{ totalUsd() | currency:'USD':'symbol':'1.2-2' }}</div>
        </header>

        @if (loading()) {
          <div class="evm-skel"><div class="sk"></div><div class="sk"></div></div>
        } @else if (tokens().length === 0) {
          <div class="evm-empty">No token balances found on Ethereum, Base, Arbitrum, Optimism or Polygon.</div>
        } @else {
          <div class="evm-rows">
            @for (t of tokens(); track t.network + t.address) {
              <div class="evm-row">
                <div class="evm-logo" [style.--cc]="chainColor(t.chain)">
                  @if (t.logo) {
                    <img [src]="t.logo" [alt]="t.symbol" (error)="onLogoError($event)" />
                  } @else {
                    <span>{{ (t.symbol || '?').slice(0, 3) }}</span>
                  }
                  <span class="evm-chain-dot" [style.background]="chainColor(t.chain)" [title]="t.chain"></span>
                </div>
                <div class="evm-main">
                  <div class="evm-sym">{{ t.symbol || 'Unknown' }} <span class="evm-chain">{{ t.chain }}</span></div>
                  <div class="evm-amt">{{ t.uiAmount | number:'1.0-4' }} {{ t.symbol }}</div>
                </div>
                <div class="evm-val">
                  <div class="evm-usd">{{ t.valueUsd | currency:'USD':'symbol':'1.2-2' }}</div>
                  @if (t.priceUsd > 0) {
                    <div class="evm-price">{{ t.priceUsd | currency:'USD':'symbol':'1.2-4' }}</div>
                  }
                </div>
              </div>
            }
          </div>
        }

        @if (positions().length > 0) {
          <div class="evm-defi">
            <div class="evm-defi-head">
              <span>DeFi Positions</span>
              <span class="evm-defi-total">{{ positionsUsd() | currency:'USD':'symbol':'1.2-2' }}</span>
            </div>
            @for (p of positions(); track p.chain + p.protocol + p.label + $index) {
              <div class="evm-pos">
                <div class="evm-pos-logo" [style.--cc]="chainColor(p.chain)">
                  @if (p.logo) {
                    <img [src]="p.logo" [alt]="p.protocol" (error)="onLogoError($event)" />
                  } @else {
                    <span>{{ (p.protocol || '?').slice(0, 2) }}</span>
                  }
                  <span class="evm-chain-dot" [style.background]="chainColor(p.chain)" [title]="p.chain"></span>
                </div>
                <div class="evm-pos-main">
                  <div class="evm-pos-proto">
                    {{ p.protocol }}
                    <span class="evm-pos-label">{{ p.label }}</span>
                  </div>
                  <div class="evm-pos-toks">
                    {{ tokenSummary(p) }}
                    <span class="evm-pos-chain">{{ p.chain }}</span>
                  </div>
                </div>
                <div class="evm-pos-val">
                  <div class="evm-usd">{{ p.balanceUsd | currency:'USD':'symbol':'1.2-2' }}</div>
                  @if (p.unclaimedUsd > 0) {
                    <div class="evm-pos-unclaimed">+{{ p.unclaimedUsd | currency:'USD':'symbol':'1.2-2' }} rewards</div>
                  }
                </div>
              </div>
            }
          </div>
        }
      </section>
    }
  `,
  styles: [`
    .evm { background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:16px; padding:18px; margin-top:16px; }
    .evm-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
    .evm-title { display:flex; align-items:center; gap:9px; font-weight:600; color:var(--op-text-primary); font-size:.95rem; }
    .evm-badge { font-size:.62rem; font-weight:700; color:#fff; background:linear-gradient(90deg,#627eea,#8247e5); padding:2px 7px; border-radius:6px; letter-spacing:.03em; }
    .evm-wallets { font-size:.72rem; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:2px 8px; border-radius:999px; }
    .evm-total { font-size:1.15rem; font-weight:700; color:var(--op-text-primary); font-variant-numeric:tabular-nums; }
    .evm-rows { display:flex; flex-direction:column; gap:2px; }
    .evm-row { display:flex; align-items:center; gap:12px; padding:9px 6px; border-radius:10px; }
    .evm-row:hover { background:var(--op-bg-surface-2, rgba(125,125,150,.06)); }
    .evm-logo { position:relative; width:34px; height:34px; border-radius:50%; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 18%, transparent); color:var(--op-text-primary); font-size:.6rem; font-weight:700; overflow:visible; }
    .evm-logo img { width:34px; height:34px; border-radius:50%; object-fit:cover; }
    .evm-chain-dot { position:absolute; right:-2px; bottom:-2px; width:12px; height:12px; border-radius:50%; border:2px solid var(--op-bg-surface-1); }
    .evm-main { flex:1; min-width:0; }
    .evm-sym { font-weight:600; color:var(--op-text-primary); font-size:.9rem; display:flex; align-items:center; gap:7px; }
    .evm-chain { font-size:.64rem; text-transform:capitalize; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:1px 6px; border-radius:5px; }
    .evm-amt { font-size:.78rem; color:var(--op-text-secondary); font-variant-numeric:tabular-nums; }
    .evm-val { text-align:right; }
    .evm-usd { font-weight:600; color:var(--op-text-primary); font-size:.9rem; font-variant-numeric:tabular-nums; }
    .evm-price { font-size:.72rem; color:var(--op-text-secondary); font-variant-numeric:tabular-nums; }
    .evm-empty { font-size:.83rem; color:var(--op-text-secondary); padding:6px 2px; }
    .evm-skel .sk { height:44px; border-radius:10px; margin-bottom:6px; background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:evsh 1.3s infinite; }
    @keyframes evsh { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
    .evm-defi { margin-top:14px; padding-top:12px; border-top:1px solid var(--op-border, rgba(255,255,255,.07)); }
    .evm-defi-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--op-text-secondary); }
    .evm-defi-total { font-variant-numeric:tabular-nums; }
    .evm-pos { display:flex; align-items:center; gap:12px; padding:9px 6px; border-radius:10px; }
    .evm-pos:hover { background:var(--op-bg-surface-2, rgba(125,125,150,.06)); }
    .evm-pos-logo { position:relative; width:34px; height:34px; border-radius:9px; flex:none; display:grid; place-items:center; background:color-mix(in srgb, var(--cc) 18%, transparent); color:var(--op-text-primary); font-size:.62rem; font-weight:700; }
    .evm-pos-logo img { width:34px; height:34px; border-radius:9px; object-fit:cover; }
    .evm-pos-main { flex:1; min-width:0; }
    .evm-pos-proto { font-weight:600; color:var(--op-text-primary); font-size:.9rem; display:flex; align-items:center; gap:7px; }
    .evm-pos-label { font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand,#5b5fc7) 15%, transparent); padding:1px 6px; border-radius:5px; }
    .evm-pos-toks { font-size:.78rem; color:var(--op-text-secondary); display:flex; align-items:center; gap:7px; }
    .evm-pos-chain { font-size:.64rem; text-transform:capitalize; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:1px 6px; border-radius:5px; }
    .evm-pos-val { text-align:right; }
    .evm-pos-unclaimed { font-size:.7rem; color:#22c55e; font-variant-numeric:tabular-nums; }
  `],
})
export class EvmHoldingsComponent implements OnInit {
  private account = inject(AccountService);
  private evm = inject(EvmPortfolioService);

  loading = signal(true);
  walletCount = signal(0);
  totalUsd = signal(0);
  tokens = signal<EvmToken[]>([]);
  positions = signal<EvmPosition[]>([]);
  positionsUsd = signal(0);

  ngOnInit(): void {
    this.account.getMe().subscribe({
      next: (me) => {
        const evmWallets = (me.identities || [])
          .filter((i) => i.type === 'evm_wallet')
          .map((i) => i.identifier);
        this.walletCount.set(evmWallets.length);
        if (evmWallets.length === 0) { this.loading.set(false); return; }

        // Each wallet: balances (Alchemy) + DeFi positions (Moralis) together.
        forkJoin(
          evmWallets.map((addr) =>
            forkJoin({
              portfolio: this.evm.getPortfolio(addr).pipe(
                catchError(() => of({ address: addr, totalUsd: 0, tokens: [] })),
              ),
              positions: this.evm.getPositions(addr).pipe(
                catchError(() => of({ address: addr, totalUsd: 0, positions: [] })),
              ),
            }),
          ),
        )
          .pipe(
            map((results) => {
              const tokens = results.flatMap((r) => r.portfolio.tokens || []);
              tokens.sort((a, b) => b.valueUsd - a.valueUsd);
              const positions = results.flatMap((r) => r.positions.positions || []);
              positions.sort((a, b) => b.balanceUsd - a.balanceUsd);
              const tokensTotal = results.reduce((s, r) => s + (r.portfolio.totalUsd || 0), 0);
              const posTotal = results.reduce((s, r) => s + (r.positions.totalUsd || 0), 0);
              return { tokens, positions, tokensTotal, posTotal };
            }),
          )
          .subscribe(({ tokens, positions, tokensTotal, posTotal }) => {
            this.tokens.set(tokens);
            this.positions.set(positions);
            this.positionsUsd.set(posTotal);
            this.totalUsd.set(tokensTotal + posTotal);
            this.loading.set(false);
          });
      },
      error: () => { this.loading.set(false); },
    });
  }

  chainColor(chain: string): string {
    return CHAIN_COLOR[chain] ?? '#7e8298';
  }

  tokenSummary(p: EvmPosition): string {
    const syms = (p.tokens || [])
      .map((t) => t.symbol)
      .filter((s): s is string => !!s);
    const uniq = Array.from(new Set(syms));
    return uniq.slice(0, 3).join(' + ') || '—';
  }

  onLogoError(ev: Event): void {
    (ev.target as HTMLImageElement).style.display = 'none';
  }
}
