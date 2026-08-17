import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { ApiService } from '../../core/services/api.service';
import { AnalyticsService } from '../../core/services/analytics.service';
import { TierBadgeComponent } from './tier-badge.component';

interface Rewards {
  tier: number;
  volumeUsd: number;
  points: { own: number; referral: number; total: number };
  referralCode: string | null;
  referralCount: number;
}

interface TierDef {
  n: number;
  name: string;
  min: number;
  color: string;
  feeDiscountPct: number; // % off the trading fee at trade time — display-only until fees.rs enforces it
  referralPct: number;    // % of referees' volume you earn as points (matches v_user_points view)
}

// Mirrors analytics_schema.tier_config thresholds. referralPct is LIVE (v_user_points);
// feeDiscountPct is the intended dynamic-commission model, display-only ("Coming soon")
// until fee enforcement lands in fees.rs.
const TIERS: TierDef[] = [
  { n: 1, name: 'Bronze',   min: 0,        color: '#cd7f32', feeDiscountPct: 0,  referralPct: 20 },
  { n: 2, name: 'Silver',   min: 1_000,    color: '#9aa4b2', feeDiscountPct: 5,  referralPct: 25 },
  { n: 3, name: 'Gold',     min: 10_000,   color: '#f59e0b', feeDiscountPct: 10, referralPct: 30 },
  { n: 4, name: 'Platinum', min: 50_000,   color: '#22d3ee', feeDiscountPct: 15, referralPct: 35 },
  { n: 5, name: 'Diamond',  min: 250_000,  color: '#818cf8', feeDiscountPct: 20, referralPct: 40 },
  { n: 6, name: 'Legend',   min: 1_000_000, color: '#a855f7', feeDiscountPct: 25, referralPct: 50 },
];

@Component({
  selector: 'app-rewards',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule, TierBadgeComponent],
  template: `
    <div class="rw">
      <header class="rw-head">
        <h1><lucide-icon name="trophy" [size]="22" /> Rewards</h1>
        <p>Trade to climb tiers and earn points. Invite friends to earn a share of their trading volume.</p>
      </header>

      @if (loading()) {
        <div class="rw-skeleton">
          <div class="sk sk-hero"></div>
          <div class="sk-row"><div class="sk sk-tile"></div><div class="sk sk-tile"></div><div class="sk sk-tile"></div></div>
        </div>
      } @else if (error()) {
        <div class="rw-error"><lucide-icon name="triangle-alert" [size]="16" /> {{ error() }} <button (click)="load()">Retry</button></div>
      } @else {
        @if (data(); as d) {

        <!-- HERO -->
        <section class="hero">
          <div class="hero-badge">
            <app-tier-badge [tier]="d.tier" [color]="tierColor(d.tier)" [size]="66" [glow]="true" />
          </div>
          <div class="hero-main">
            <div class="hero-tierline">
              <span class="hero-tier" [style.color]="tierColor(d.tier)">{{ tierName(d.tier) }}</span>
              <span class="hero-tiernum">Tier {{ d.tier }} of 6</span>
            </div>
            @if (d.tier < 6) {
              <div class="hero-progress">
                <div class="hero-bar"><div class="hero-fill" [style.width.%]="tierProgress(d)" [style.background]="tierColor(d.tier + 1)"></div></div>
                <div class="hero-progress-labels">
                  <span>{{ d.volumeUsd | currency:'USD':'symbol':'1.0-0' }}</span>
                  <span class="rw-muted">{{ nextTierGap(d) | currency:'USD':'symbol':'1.0-0' }} to {{ tierName(d.tier + 1) }}</span>
                </div>
              </div>
            } @else {
              <div class="hero-max"><lucide-icon name="sparkles" [size]="14" /> Top tier reached — maximum benefits unlocked</div>
            }
          </div>
        </section>

        <!-- STAT TILES -->
        <section class="tiles">
          <div class="tile">
            <div class="tile-ico" style="--tc:#818cf8"><lucide-icon name="star" [size]="18" /></div>
            <div class="tile-val">{{ d.points.total | number }}</div>
            <div class="tile-lbl">Total points</div>
          </div>
          <div class="tile">
            <div class="tile-ico" style="--tc:#06b6d4"><lucide-icon name="trending-up" [size]="18" /></div>
            <div class="tile-val">{{ d.volumeUsd | currency:'USD':'symbol':'1.2-2' }}</div>
            <div class="tile-lbl">Trading volume</div>
          </div>
          <div class="tile">
            <div class="tile-ico" style="--tc:#22c55e"><lucide-icon name="users" [size]="18" /></div>
            <div class="tile-val">{{ d.referralCount | number }}</div>
            <div class="tile-lbl">Friends invited</div>
          </div>
          <div class="tile">
            <div class="tile-ico" style="--tc:#f59e0b"><lucide-icon name="gift" [size]="18" /></div>
            <div class="tile-val">{{ d.points.referral | number }}</div>
            <div class="tile-lbl">Referral points</div>
          </div>
        </section>

        <div class="cols">
          <!-- REFERRAL (star of the page) -->
          <section class="card referral">
            <div class="card-title"><lucide-icon name="user-plus" [size]="18" /> Invite friends</div>
            <p class="card-sub">Earn <b>{{ referralPct(d.tier) }}%</b> of everything your friends trade, credited as points.</p>

            <label class="fld-lbl">Your invite link</label>
            <div class="copybox" (click)="copy(inviteLink(d), 'link')" title="Copy link">
              <span class="copybox-txt">{{ inviteLink(d) }}</span>
              <button class="copybox-btn"><lucide-icon [name]="copied()==='link' ? 'check' : 'copy'" [size]="16" /></button>
            </div>

            <div class="ref-row">
              <div class="ref-code-wrap">
                <label class="fld-lbl">Invite code</label>
                <div class="copybox code" (click)="copy(d.referralCode || '', 'code')" title="Copy code">
                  <span class="copybox-txt mono">{{ d.referralCode || '—' }}</span>
                  <button class="copybox-btn"><lucide-icon [name]="copied()==='code' ? 'check' : 'copy'" [size]="16" /></button>
                </div>
              </div>
              @if (canShare) {
                <button class="share-btn" (click)="share(d)"><lucide-icon name="share-2" [size]="16" /> Share</button>
              }
            </div>

            <div class="steps">
              <div class="step"><span class="step-n">1</span><div><b>Share your link</b><span>Send it to friends and communities.</span></div></div>
              <div class="step"><span class="step-n">2</span><div><b>They start trading</b><span>They join OPRAI with your code.</span></div></div>
              <div class="step"><span class="step-n">3</span><div><b>You earn points</b><span>{{ referralPct(d.tier) }}% of their volume, forever.</span></div></div>
            </div>

            <div class="redeem">
              <label class="fld-lbl">Have an invite code?</label>
              <div class="redeem-row">
                <input [(ngModel)]="code" placeholder="ENTER CODE" maxlength="8" (keyup.enter)="redeem()" />
                <button (click)="redeem()" [disabled]="!code.trim() || redeeming()">
                  {{ redeeming() ? 'Applying…' : 'Apply' }}
                </button>
              </div>
              @if (redeemMsg()) { <div class="redeem-msg" [class.ok]="redeemOk()">{{ redeemMsg() }}</div> }
            </div>
          </section>

          <!-- TIER LADDER -->
          <section class="card ladder">
            <div class="card-title"><lucide-icon name="layers" [size]="18" /> Tier benefits</div>
            <p class="card-sub">Your all-time volume sets your tier. Higher tiers earn more.</p>

            <div class="ladder-head">
              <span>Tier</span>
              <span class="num">Volume</span>
              <span class="num">Referral</span>
              <span class="num">Fee off</span>
            </div>
            <div class="ladder-rows">
              @for (t of tiers; track t.n) {
                <div class="ladder-row" [class.here]="d.tier === t.n">
                  <span class="lr-tier">
                    <app-tier-badge [tier]="t.n" [color]="t.color" [size]="26" />
                    {{ t.name }}
                    @if (d.tier === t.n) { <span class="lr-here">You</span> }
                  </span>
                  <span class="num">{{ t.min === 0 ? '$0' : (t.min | currency:'USD':'symbol':'1.0-0') }}</span>
                  <span class="num">{{ t.referralPct }}%</span>
                  <span class="num cb">{{ t.feeDiscountPct }}%</span>
                </div>
              }
            </div>
            <div class="ladder-foot">
              <lucide-icon name="info" [size]="13" />
              <span><b>Fee off</b> lowers the trading fee you pay as your tier rises. <span class="soon">Coming soon</span></span>
            </div>
          </section>
        </div>
        }
      }
    </div>
  `,
  styles: [`
    /* .main-content is a flex column with overflow:hidden, so the page must scroll itself. */
    :host { display:block; flex:1 1 auto; min-height:0; overflow-y:auto; }
    .rw { max-width: 1040px; margin:0 auto; padding: 24px 20px 56px; }
    .rw-head h1 { display:flex; align-items:center; gap:10px; font-size:1.55rem; font-weight:700; color:var(--op-text-primary); margin:0 0 6px; }
    .rw-head p { color:var(--op-text-secondary); margin:0 0 24px; font-size:.9rem; max-width:600px; }
    .rw-muted { color:var(--op-text-secondary); }

    /* HERO */
    .hero { display:flex; align-items:center; gap:20px; background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:20px; padding:22px 24px; margin-bottom:16px; position:relative; overflow:hidden; }
    .hero::before { content:''; position:absolute; inset:0; background:radial-gradient(120% 140% at 0% 0%, rgba(91,95,199,.14), transparent 55%); pointer-events:none; }
    .hero-badge { flex:none; display:grid; place-items:center; line-height:0; }
    .hero-main { flex:1; min-width:0; }
    .hero-tierline { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
    .hero-tier { font-size:1.7rem; font-weight:800; letter-spacing:-.01em; }
    .hero-tiernum { font-size:.82rem; color:var(--op-text-secondary); }
    .hero-progress { margin-top:12px; }
    .hero-bar { height:9px; background:rgba(125,125,150,.16); border-radius:999px; overflow:hidden; }
    .hero-fill { height:100%; border-radius:999px; transition:width .5s ease; }
    .hero-progress-labels { display:flex; justify-content:space-between; margin-top:7px; font-size:.82rem; color:var(--op-text-primary); font-weight:600; }
    .hero-progress-labels .rw-muted { font-weight:500; }
    .hero-max { margin-top:8px; display:inline-flex; align-items:center; gap:6px; font-size:.85rem; color:#f59e0b; font-weight:600; }

    /* TILES */
    .tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
    .tile { background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:16px; padding:16px; }
    .tile-ico { width:34px; height:34px; border-radius:10px; display:grid; place-items:center; color:var(--tc); background:color-mix(in srgb, var(--tc) 15%, transparent); margin-bottom:10px; }
    .tile-val { font-size:1.5rem; font-weight:800; color:var(--op-text-primary); line-height:1.1; }
    .tile-lbl { font-size:.78rem; color:var(--op-text-secondary); margin-top:2px; }

    /* COLUMNS */
    .cols { display:grid; grid-template-columns:1.1fr 1fr; gap:16px; align-items:stretch; }
    @media (max-width:820px){ .cols{ grid-template-columns:1fr; } }
    .card { background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:18px; padding:20px; }
    .card-title { display:flex; align-items:center; gap:8px; font-size:1.02rem; font-weight:700; color:var(--op-text-primary); }
    .card-sub { color:var(--op-text-secondary); font-size:.85rem; margin:6px 0 16px; }
    .card-sub b { color:var(--op-text-primary); }
    .fld-lbl { display:block; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--op-text-secondary); margin:0 0 6px; }

    /* copybox */
    .copybox { display:flex; align-items:center; gap:10px; cursor:pointer; background:var(--op-bg-surface-2, rgba(125,125,150,.06)); border:1px solid var(--op-border, rgba(255,255,255,.1)); border-radius:10px; padding:9px 10px 9px 13px; transition:border-color .15s; }
    .copybox:hover { border-color:var(--op-brand,#5b5fc7); }
    .copybox-txt { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--op-text-primary); font-size:.9rem; }
    .copybox-txt.mono { font-family:monospace; font-size:1.15rem; font-weight:700; letter-spacing:.12em; }
    .copybox.code { border-style:dashed; border-color:var(--op-brand,#5b5fc7); }
    .copybox-btn { flex:none; background:transparent; border:0; color:var(--op-text-secondary); cursor:pointer; display:grid; place-items:center; padding:4px; }
    .copybox:hover .copybox-btn { color:var(--op-brand,#5b5fc7); }

    .ref-row { display:flex; gap:12px; align-items:flex-end; margin-top:14px; }
    .ref-code-wrap { flex:1; min-width:0; }
    .share-btn { flex:none; display:inline-flex; align-items:center; gap:7px; background:linear-gradient(90deg,#5b5fc7,#06b6d4); color:#fff; border:0; border-radius:10px; padding:10px 15px; font-weight:600; font-size:.88rem; cursor:pointer; }

    /* steps */
    .steps { display:flex; flex-direction:column; gap:12px; margin:18px 0 4px; }
    .step { display:flex; gap:11px; align-items:flex-start; }
    .step-n { flex:none; width:24px; height:24px; border-radius:50%; display:grid; place-items:center; font-size:.78rem; font-weight:700; color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand, #5b5fc7) 16%, transparent); }
    .step > div { display:flex; flex-direction:column; }
    .step b { font-size:.88rem; color:var(--op-text-primary); }
    .step span { font-size:.8rem; color:var(--op-text-secondary); }

    /* redeem */
    .redeem { margin-top:18px; border-top:1px solid var(--op-border, rgba(255,255,255,.08)); padding-top:16px; }
    .redeem-row { display:flex; gap:8px; }
    .redeem-row input { flex:1; background:var(--op-bg-surface-2, rgba(125,125,150,.06)); border:1px solid var(--op-border, rgba(255,255,255,.12)); border-radius:10px; padding:10px 12px; color:var(--op-text-primary); text-transform:uppercase; font-family:monospace; letter-spacing:.1em; font-size:.9rem; }
    .redeem-row input:focus { outline:none; border-color:var(--op-brand,#5b5fc7); }
    .redeem-row button, .rw-error button { background:linear-gradient(90deg,#5b5fc7,#06b6d4); color:#fff; border:0; border-radius:10px; padding:10px 18px; font-weight:600; cursor:pointer; font-size:.88rem; }
    .redeem-row button:disabled { opacity:.5; cursor:not-allowed; }
    .redeem-msg { font-size:.82rem; margin-top:8px; color:#ef4444; }
    .redeem-msg.ok { color:#22c55e; }

    /* ladder */
    .ladder-head, .ladder-row { display:grid; grid-template-columns:1.4fr .9fr .8fr .8fr; align-items:center; gap:8px; }
    .ladder-head { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--op-text-secondary); padding:0 10px 8px; }
    .ladder-rows { flex:1; display:flex; flex-direction:column; }
    .ladder-row { flex:1; min-height:44px; padding:6px 10px; border-radius:10px; font-size:.9rem; color:var(--op-text-primary); }
    .ladder-row + .ladder-row { border-top:1px solid var(--op-border, rgba(255,255,255,.05)); }
    .ladder-row.here { background:color-mix(in srgb, var(--op-brand,#5b5fc7) 12%, transparent); border-top-color:transparent; }
    .num { text-align:right; font-variant-numeric:tabular-nums; }
    .cb { color:var(--op-text-secondary); }
    .lr-tier { display:flex; align-items:center; gap:9px; font-weight:600; }
    .lr-tier app-tier-badge { flex:none; line-height:0; }
    .lr-here { font-size:.64rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand,#5b5fc7) 20%, transparent); padding:1px 6px; border-radius:6px; }
    .ladder { display:flex; flex-direction:column; }
    .ladder-foot { display:flex; align-items:flex-start; gap:7px; margin-top:auto; padding-top:14px; border-top:1px solid var(--op-border, rgba(255,255,255,.08)); font-size:.78rem; color:var(--op-text-secondary); }
    .ladder-foot b { color:var(--op-text-primary); }
    .soon { display:inline-block; font-size:.66rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#f59e0b; background:color-mix(in srgb, #f59e0b 16%, transparent); padding:1px 6px; border-radius:6px; margin-left:4px; }

    /* states */
    .rw-error { color:#ef4444; display:flex; gap:10px; align-items:center; font-size:.9rem; }
    .rw-skeleton .sk { background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:shim 1.3s infinite; border-radius:16px; }
    .sk-hero { height:110px; margin-bottom:16px; } .sk-row { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; } .sk-tile { height:96px; }
    @keyframes shim { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
  `],
})
export class RewardsComponent implements OnInit {
  private api = inject(ApiService);
  private analytics = inject(AnalyticsService);

  tiers = TIERS;
  data = signal<Rewards | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  redeemMsg = signal<string | null>(null);
  redeemOk = signal(false);
  redeeming = signal(false);
  copied = signal<'code' | 'link' | null>(null);
  code = '';
  canShare = typeof navigator !== 'undefined' && !!(navigator as any).share;

  ngOnInit(): void {
    // Pre-fill an invite code arriving via ?ref=CODE (do not auto-submit — user confirms).
    try {
      const ref = new URLSearchParams(window.location.search).get('ref');
      if (ref) this.code = ref.toUpperCase().slice(0, 8);
    } catch { /* SSR / no window */ }
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.get<Rewards>('/me/rewards').subscribe({
      next: (d) => { this.data.set(d); this.loading.set(false); },
      error: () => { this.error.set('Could not load rewards.'); this.loading.set(false); },
    });
  }

  redeem(): void {
    const c = (this.code || '').trim().toUpperCase();
    if (!c || this.redeeming()) return;
    this.redeeming.set(true);
    this.redeemMsg.set(null);
    this.api.post<{ ok: boolean; linked: boolean }>('/referral/redeem', { code: c }).subscribe({
      next: (r) => {
        this.redeemOk.set(!!r.linked);
        this.analytics.featureUsed('referral_redeem', { linked: !!r.linked });
        this.redeemMsg.set(r.linked ? 'Referral linked — welcome aboard!' : 'You already have a referrer.');
        this.code = '';
        this.redeeming.set(false);
        this.load();
      },
      error: (e) => {
        this.redeemOk.set(false);
        this.redeemMsg.set(
          e?.status === 404 ? 'Invalid code.'
          : e?.status === 400 ? "You can't use your own code."
          : 'Could not apply the code.',
        );
        this.redeeming.set(false);
      },
    });
  }

  inviteLink(d: Rewards): string {
    const origin = typeof window !== 'undefined' ? window.location.origin : 'https://app.oprai.xyz';
    return d.referralCode ? `${origin}/?ref=${d.referralCode}` : origin;
  }

  copy(text: string, which: 'code' | 'link'): void {
    if (!text) return;
    navigator.clipboard?.writeText(text);
    this.copied.set(which);
    this.analytics.featureUsed('referral_copy', { which });
    setTimeout(() => this.copied.set(null), 1500);
  }

  share(d: Rewards): void {
    this.analytics.featureUsed('referral_share');
    (navigator as any).share?.({
      title: 'OPRAI',
      text: 'Trade Solana with OPRAI, the DeFi AI assistant. Use my invite:',
      url: this.inviteLink(d),
    }).catch(() => {});
  }

  tierName(t: number): string { return (TIERS.find((x) => x.n === t)?.name) ?? `Tier ${t}`; }
  tierColor(t: number): string { return (TIERS.find((x) => x.n === t)?.color) ?? '#5b5fc7'; }
  referralPct(t: number): number { return (TIERS.find((x) => x.n === t)?.referralPct) ?? 30; }

  tierProgress(d: Rewards): number {
    if (d.tier >= 6) return 100;
    const lo = TIERS[d.tier - 1]?.min ?? 0;
    const hi = TIERS[d.tier]?.min ?? lo;
    if (hi <= lo) return 100;
    return Math.max(2, Math.min(100, ((d.volumeUsd - lo) / (hi - lo)) * 100));
  }

  nextTierGap(d: Rewards): number {
    if (d.tier >= 6) return 0;
    return Math.max(0, (TIERS[d.tier]?.min ?? 0) - d.volumeUsd);
  }
}
