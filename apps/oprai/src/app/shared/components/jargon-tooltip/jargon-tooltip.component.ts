/**
 * JargonTooltipComponent
 *
 * Wraps a DeFi jargon term (LTV, Health Factor, Slippage, etc.) with a help
 * icon that reveals a definition on hover or focus. The dictionary is local
 * so we avoid a network round-trip on every action card render — DeFi
 * vocabulary is small enough to bake in.
 *
 * Two ways to use:
 *   1. Wrap a label:        <app-jargon term="LTV">LTV</app-jargon>
 *   2. Override the gloss:  <app-jargon term="LTV" [gloss]="'Custom'">LTV</app-jargon>
 *
 * Tooltip is keyboard-accessible (focusable button + aria-describedby) and
 * dismissable with Escape. CSS positions the bubble; no Angular CDK overlay
 * required, which keeps it cheap to render in repeated rows.
 */
import { Component, Input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

const GLOSSARY: Record<string, string> = {
  ltv: 'Loan-to-Value: how much you borrowed vs. your collateral. Higher LTV = closer to liquidation.',
  'health factor': 'How safe your borrow position is. 1.0 = liquidation. Anything < 1.5 is risky.',
  slippage: 'How much price movement you tolerate while the swap lands. Too low = swap fails; too high = sandwich-attack window.',
  'price impact': 'How much your trade moves the pool price. >1% on a major pair signals a thin pool or oversized trade.',
  apy: 'Annual Percentage Yield — the rate compounded over a year. APR ignores compounding; APY includes it.',
  apr: 'Annual Percentage Rate — the simple yearly rate, without compounding.',
  tvl: 'Total Value Locked — how much capital is sitting in the protocol or pool right now.',
  'compute units': 'How much CPU/IO time Solana charges your transaction. Like gas on Ethereum, but per-instruction.',
  'priority fee': 'Bribe paid per compute unit so validators include your tx faster. Spikes during congestion.',
  cu: 'Compute Units — Solana\'s metric for tx processing cost.',
  mev: 'Maximal Extractable Value: bots front-run profitable txs. Jito bundles + low slippage protect you.',
  'liquidation ltv': 'The LTV at which the protocol force-closes your loan to recover its capital.',
  'jito bundle': 'A package of txs sent to Jito-Solana validators atomically; protects against sandwich attacks.',
  jitosol: 'Jito\'s liquid-staked SOL — earns staking yield while remaining tradable.',
  jupsol: 'Jupiter\'s liquid-staked SOL token; claims a slice of Jupiter validator MEV rewards.',
  ata: 'Associated Token Account — the on-chain account holding a specific SPL token for your wallet.',
  rent: 'Solana charges accounts a small SOL deposit to cover storage costs. Refundable on close.',
  bps: 'Basis points: 1 bps = 0.01%. 50 bps = 0.5%.',
  bonding_curve: 'Pump.fun\'s pricing model — buying raises the price along a fixed curve until migration to AMM.',
  amm: 'Automated Market Maker — pool-based DEX where price is set by the ratio of two tokens, not an order book.',
  clmm: 'Concentrated Liquidity Market Maker — LPs choose a price range; capital efficient but can drift "out of range".',
  dlmm: 'Dynamic Liquidity Market Maker — Meteora\'s concentrated-liquidity bins with on-the-fly fee tiers.',
};

@Component({
  selector: 'app-jargon',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span class="jargon-wrapper">
      <ng-content />
      <button
        type="button"
        class="jargon-trigger"
        [attr.aria-label]="'What is ' + term + '?'"
        [attr.aria-expanded]="open()"
        (click)="toggle($event)"
        (keydown.escape)="open.set(false)"
        (mouseenter)="open.set(true)"
        (mouseleave)="open.set(false)"
        (focus)="open.set(true)"
        (blur)="open.set(false)"
      >?</button>
      @if (open()) {
        <span class="jargon-bubble" role="tooltip">{{ resolvedGloss }}</span>
      }
    </span>
  `,
  styles: [`
    .jargon-wrapper {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      position: relative;
    }
    .jargon-trigger {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      padding: 0;
      border: 1px solid var(--op-border-subtle, rgba(0,0,0,.18));
      border-radius: 50%;
      background: var(--op-bg-surface-2, rgba(0,0,0,.05));
      color: var(--op-text-secondary, #6b7280);
      font-size: 9px;
      font-weight: 700;
      cursor: help;
      line-height: 1;
    }
    .jargon-trigger:hover, .jargon-trigger:focus {
      border-color: var(--op-brand);
      color: var(--op-brand);
      outline: none;
    }
    .jargon-bubble {
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      transform: translateX(-50%);
      max-width: 240px;
      padding: 8px 10px;
      background: var(--op-bg-surface-3, #1a1a1a);
      color: var(--op-text-primary, #fff);
      border: 1px solid var(--op-border-subtle);
      border-radius: 6px;
      font-size: 11px;
      font-weight: 400;
      line-height: 1.4;
      white-space: normal;
      width: max-content;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      z-index: 10000;
      pointer-events: none;
    }
  `],
})
export class JargonTooltipComponent {
  @Input({ required: true }) term!: string;
  /** Override the dictionary lookup. */
  @Input() gloss?: string;

  readonly open = signal(false);

  get resolvedGloss(): string {
    if (this.gloss) return this.gloss;
    return GLOSSARY[this.term.toLowerCase()] ?? `${this.term} — no definition available.`;
  }

  toggle(event: Event): void {
    event.stopPropagation();
    this.open.update((v) => !v);
  }
}
