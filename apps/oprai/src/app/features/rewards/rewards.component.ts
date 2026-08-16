import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { ApiService } from '../../core/services/api.service';

interface Rewards {
  tier: number;
  volumeUsd: number;
  points: { own: number; referral: number; total: number };
  referralCode: string | null;
  referralCount: number;
}

const TIER_NAMES = ['—', 'Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond', 'Legend'];
// Cumulative-volume thresholds mirror analytics_schema.tier_config (display only).
const TIER_MIN = [0, 0, 1000, 10000, 50000, 250000, 1000000];

@Component({
  selector: 'app-rewards',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule],
  template: `
    <div class="rewards-page">
      <header class="rewards-head">
        <h1><lucide-icon name="trophy" [size]="22" /> Rewards</h1>
        <p>Your trading volume raises your tier and earns points. Invite friends and earn 30% of their volume as points.</p>
      </header>

      @if (loading()) {
        <div class="rw-muted">Loading…</div>
      } @else if (error()) {
        <div class="rw-error">{{ error() }} <button (click)="load()">Retry</button></div>
      } @else {
        @if (data(); as d) {
        <div class="rw-grid">
          <!-- Tier -->
          <section class="rw-card rw-tier">
            <span class="rw-card-label">Tier</span>
            <div class="rw-tier-name">{{ tierName(d.tier) }}</div>
            <div class="rw-tier-num">Tier {{ d.tier }}</div>
            <div class="rw-bar"><div class="rw-bar-fill" [style.width.%]="tierProgress(d)"></div></div>
            <div class="rw-muted rw-sm">
              @if (d.tier < 6) { {{ nextTierGap(d) | currency:'USD':'symbol':'1.0-0' }} more → {{ tierName(d.tier + 1) }} }
              @else { Top tier reached 🎉 }
            </div>
          </section>

          <!-- Points -->
          <section class="rw-card">
            <span class="rw-card-label">Points</span>
            <div class="rw-points-total">{{ d.points.total | number }}</div>
            <div class="rw-points-split">
              <span>From your volume: <b>{{ d.points.own | number }}</b></span>
              <span>From referrals: <b>{{ d.points.referral | number }}</b></span>
            </div>
            <div class="rw-muted rw-sm">Total volume: {{ d.volumeUsd | currency:'USD':'symbol':'1.2-2' }}</div>
          </section>

          <!-- Referral -->
          <section class="rw-card rw-referral">
            <span class="rw-card-label">Referral</span>
            <div class="rw-muted rw-sm">Your invite code</div>
            <div class="rw-code" (click)="copyCode()" title="Copy">
              <span>{{ d.referralCode || '—' }}</span>
              <lucide-icon [name]="copied() ? 'check' : 'copy'" [size]="16" />
            </div>
            <div class="rw-muted rw-sm">{{ d.referralCount }} invited</div>

            <div class="rw-redeem">
              <div class="rw-muted rw-sm">Have an invite code?</div>
              <div class="rw-redeem-row">
                <input [(ngModel)]="code" placeholder="CODE" maxlength="8" (keyup.enter)="redeem()" />
                <button (click)="redeem()" [disabled]="!code.trim()">Apply</button>
              </div>
              @if (redeemMsg()) { <div class="rw-redeem-msg">{{ redeemMsg() }}</div> }
            </div>
          </section>
        </div>
        }
      }
    </div>
  `,
  styles: [`
    :host { display:block; }
    .rewards-page { max-width: 920px; margin: 0 auto; padding: 24px 20px 48px; }
    .rewards-head h1 { display:flex; align-items:center; gap:10px; font-size:1.5rem; font-weight:700; color: var(--op-text-primary); margin:0 0 6px; }
    .rewards-head p { color: var(--op-text-secondary); margin:0 0 24px; font-size:.9rem; max-width:560px; }
    .rw-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:16px; }
    .rw-card { background: var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:16px; padding:20px; display:flex; flex-direction:column; gap:8px; }
    .rw-card-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color: var(--op-text-secondary); }
    .rw-tier { background: linear-gradient(135deg, rgba(91,95,199,.14), rgba(6,182,212,.10)); }
    .rw-tier-name { font-size:1.7rem; font-weight:800; background:linear-gradient(90deg,#5b5fc7,#06B6D4); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
    .rw-tier-num { color: var(--op-text-secondary); font-size:.85rem; }
    .rw-bar { height:8px; background: rgba(255,255,255,.08); border-radius:999px; overflow:hidden; margin-top:4px; }
    .rw-bar-fill { height:100%; background:linear-gradient(90deg,#5b5fc7,#06B6D4); border-radius:999px; transition:width .4s; }
    .rw-points-total { font-size:2rem; font-weight:800; color: var(--op-text-primary); }
    .rw-points-split { display:flex; flex-direction:column; gap:2px; font-size:.85rem; color: var(--op-text-secondary); }
    .rw-points-split b { color: var(--op-text-primary); }
    .rw-code { display:flex; align-items:center; justify-content:space-between; gap:12px; cursor:pointer; font-family:monospace; font-size:1.25rem; font-weight:700; letter-spacing:.15em; color: var(--op-text-primary); background: rgba(255,255,255,.05); border:1px dashed var(--op-brand,#5b5fc7); border-radius:10px; padding:10px 14px; }
    .rw-redeem { margin-top:12px; border-top:1px solid var(--op-border, rgba(255,255,255,.08)); padding-top:12px; display:flex; flex-direction:column; gap:8px; }
    .rw-redeem-row { display:flex; gap:8px; }
    .rw-redeem-row input { flex:1; background: var(--op-bg-surface-2, rgba(255,255,255,.04)); border:1px solid var(--op-border, rgba(255,255,255,.12)); border-radius:8px; padding:8px 10px; color: var(--op-text-primary); text-transform:uppercase; font-family:monospace; letter-spacing:.1em; }
    .rw-redeem-row button, .rw-error button { background:linear-gradient(90deg,#5b5fc7,#06B6D4); color:#fff; border:0; border-radius:8px; padding:8px 16px; font-weight:600; cursor:pointer; }
    .rw-redeem-row button:disabled { opacity:.5; cursor:not-allowed; }
    .rw-redeem-msg { font-size:.82rem; color: var(--op-brand,#5b5fc7); }
    .rw-muted { color: var(--op-text-secondary); }
    .rw-sm { font-size:.8rem; }
    .rw-error { color:#ef4444; display:flex; gap:12px; align-items:center; }
  `],
})
export class RewardsComponent implements OnInit {
  private api = inject(ApiService);

  data = signal<Rewards | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  redeemMsg = signal<string | null>(null);
  copied = signal(false);
  code = '';

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.get<Rewards>('/me/rewards').subscribe({
      next: (d) => {
        this.data.set(d);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Could not load rewards.');
        this.loading.set(false);
      },
    });
  }

  redeem(): void {
    const c = (this.code || '').trim().toUpperCase();
    if (!c) return;
    this.api.post<{ ok: boolean; linked: boolean }>('/referral/redeem', { code: c }).subscribe({
      next: (r) => {
        this.redeemMsg.set(r.linked ? 'Referral linked! 🎉' : 'You already have a referrer.');
        this.code = '';
        this.load();
      },
      error: (e) => {
        this.redeemMsg.set(
          e?.status === 404 ? 'Invalid code.'
          : e?.status === 400 ? "You can't use your own code."
          : 'Could not apply the code.',
        );
      },
    });
  }

  copyCode(): void {
    const c = this.data()?.referralCode;
    if (!c) return;
    navigator.clipboard?.writeText(c);
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 1500);
  }

  tierName(t: number): string {
    return TIER_NAMES[t] ?? `Tier ${t}`;
  }

  tierProgress(d: Rewards): number {
    if (d.tier >= 6) return 100;
    const lo = TIER_MIN[d.tier];
    const hi = TIER_MIN[d.tier + 1];
    if (hi <= lo) return 100;
    return Math.max(0, Math.min(100, ((d.volumeUsd - lo) / (hi - lo)) * 100));
  }

  nextTierGap(d: Rewards): number {
    if (d.tier >= 6) return 0;
    return Math.max(0, TIER_MIN[d.tier + 1] - d.volumeUsd);
  }
}
