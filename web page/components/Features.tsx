'use client';

import Image from 'next/image';

const stackFeatures = [
  {
    title: 'NFT Trading Module',
    image: '/stack/stack-01.png',
    details: 'Curated drops, allowlist automation, floor sweeps, and exit ladders executed with policy guardrails.'
  },
  {
    title: 'DEX Aggregation Module',
    image: '/stack/stack-02.png',
    details: 'Route intents across AMM, RFQ, and CLMM venues with live liquidity sensing and slippage locks.'
  },
  {
    title: 'Token Launch Module',
    image: '/stack/stack-04.png',
    details: 'Ship SPL launches end-to-end—tokenomics drafts, pool deployment, and launch surface orchestration.'
  },
  {
    title: 'Perpetual Trading Module',
    image: '/stack/stack-05.png',
    details: 'Execute hedges, basis plays, and kill-switches with automated funding and risk thresholds.'
  },
  {
    title: 'Lending & Borrowing Module',
    image: '/stack/stack-03.png',
    details: 'Loop collateral, farm incentives, and auto-rebalance health factors across Solana money markets.'
  },
  {
    title: 'Bridge Module',
    image: '/stack/bridge-module.png',
    details: 'Synchronize cross-chain liquidity, monitor bridge risk signals, and approve vault transfers with live attestations.'
  },
  {
    title: 'Liquid Staking Module',
    image: '/stack/liquid-staking-custom.png',
    details: 'Rotate stake across validators, harvest rewards, and redeploy yield-bearing assets into Solana-native strategies.'
  },
  {
    title: 'DeFi Strategy Engine',
    image: '/stack/stack-06.png',
    details: 'Compose multi-step automations, simulate guardrails, and capture telemetry for policy sign-off.'
  }
];

export function Features() {
  return (
    <section id="features" className="container flex flex-col justify-center gap-5 sm:gap-6 py-8 sm:py-10 lg:py-12">
      <div className="space-y-3 sm:space-y-4 text-center">
        <span className="section-eyebrow reveal-eyebrow">Capability suite</span>
        <h2 className="section-heading reveal-heading">One agent for the entire Solana DeFi stack.</h2>
        <p className="mt-3 sm:mt-4 text-pretty text-sm text-white/70 md:mx-auto md:max-w-3xl md:text-base reveal-text px-4">
          Execute complex DeFi strategies across every major Solana protocol through a single conversational interface.
        </p>
      </div>

      <div className="neo-stack-grid">
        {stackFeatures.map((feature, idx) => (
          <div key={feature.title} className="neo-stack-card reveal">
            <div className="neo-stack-media">
              <Image
                src={feature.image}
                alt={feature.title}
                fill
                sizes="(max-width: 640px) 92vw, (max-width: 1024px) 44vw, (min-width: 1180px) 23vw, 320px"
                priority={idx < 2}
                className="neo-stack-image"
              />
              <div className="neo-stack-media__veil" />
            </div>
            <div className="neo-stack-caption">
              <h3 className="reveal-heading">{feature.title}</h3>
              <p className="reveal-text">{feature.details}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
