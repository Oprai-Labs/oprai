import Image from 'next/image';
import Link from 'next/link';
import { Navbar } from '../components/Navbar';
import { Features } from '../components/Features';
import { HowItWorks } from '../components/HowItWorks';
import { FAQ } from '../components/FAQ';
import { ScrollReveal } from '../components/ScrollReveal';
const heroHighlights = [];

const dialogExamples = [
  'Borrow USDC against SOL collateral at the best available rate.',
  'Simulate a cross-DEX swap from BONK to SOL and execute if slippage < 0.5%.',
  'Stake 100 SOL with Jito and auto-compound rewards daily.'
];

const strategyHighlights = [
  {
    heading: 'Composable DeFi Automation',
    body: 'Chain NFT launches, token trades, perps, and lending moves in a single orchestrated canvas.'
  },
  {
    heading: 'Yield + Risk Intelligence',
    body: 'Build strategies driven by live APR, liquidity, and protocol risk scoring across Solana venues.'
  },
  {
    heading: 'Human-in-the-loop Guardrails',
    body: 'Enforce spend limits, approvals, and policy context without losing speed or visibility.'
  }
];

const dialogHighlights = [
  'Connect your wallet once; Oprai interprets commands and executes across all Solana protocols.',
  'Conversational intents become routed strategies across DEX, lending, perps, and NFT markets.',
  'Instant intelligence on new launches with holder health, bundle flow, and volatility signals.'
];

export default function HomePage() {
  return (
    <div className="relative text-white">
      <ScrollReveal />
      <Navbar />
      <main className="relative flex-1 flex flex-col">
        <section className="section-theme section-theme--hero relative overflow-hidden py-[clamp(40px,5vw,72px)]">
          <div className="relative mx-auto grid w-full max-w-[1600px] gap-12 px-4 sm:px-6 lg:px-10 xl:px-12 md:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] md:items-center lg:gap-20">
            <div className="flex h-full flex-col justify-center gap-10 sm:gap-12">
              <div className="space-y-7 sm:space-y-8">
                <span className="capsule reveal-eyebrow">
                  <span className="pulse-dot">
                    <span className="h-2 w-2 rounded-full bg-sky-400" />
                  </span>
                  Conversational AI Layer for solana
                </span>
                <h1 className="text-4xl font-extrabold tracking-tight text-slate-50 sm:text-5xl md:text-[3.25rem] md:leading-[1.05] lg:text-[3.85rem] reveal-heading">
                  <span className="hero-headline block leading-tight">Turning language into</span>
                  <span className="hero-headline block leading-tight">on-chain actions</span>
                </h1>
                <p className="max-w-xl text-sm text-white/80 sm:text-base reveal-text">
                  Connect your wallet once and command Solana's entire DeFi ecosystem through natural language. From token launches to complex yield strategies—execute institutional-grade operations with conversational simplicity.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3 reveal-text">
                <Link href="https://x.com/oprai_" target="_blank" className="btn btn-primary">
                  Join the waitlist
                </Link>
              </div>
            </div>
            <div className="relative flex h-full w-full items-center justify-center">
              <div className="hero-media-shell relative z-10 w-full max-w-[560px] overflow-hidden rounded-[28px] sm:rounded-[32px] lg:max-w-none reveal">
                <div className="hero-media-wrapper relative h-full w-full">
                  <Image
                    src="/brand/hero-execution.png"
                    alt="Oprai agent execution workflow illustration"
                    fill
                    priority
                    className="hero-media"
                    sizes="(max-width: 768px) 90vw, (max-width: 1280px) 50vw, (min-width: 1281px) 780px"
                  />
                </div>
                <div className="hero-media-overlay pointer-events-none absolute inset-0 rounded-[38px]" />
              </div>
            </div>
          </div>
        </section>

        <div className="border-t border-white/5"></div>

        <section id="dialog" className="section-theme section-theme--dialog py-[clamp(80px,10vw,160px)]">
          <div className="container relative flex flex-col justify-between gap-12 sm:gap-14 md:gap-16">
            <div className="max-w-2xl space-y-5 sm:space-y-6">
              <span className="section-eyebrow reveal-eyebrow">Natural dialogue input</span>
              <h2 className="section-heading dialog-heading text-balance reveal-heading">Run Solana, powered by language.</h2>
              <p className="dialog-subtitle reveal-text">
                Oprai is the conversational layer for Solana—connect once to brief, price, and execute strategies without dashboards or manual sequencing.
              </p>
            </div>
            <div className="reveal glass-panel relative flex flex-1 flex-col overflow-hidden px-4 py-5 sm:px-6 sm:py-6 md:px-8">
              <div className="absolute -top-24 -right-24 h-72 w-72 rounded-full bg-sky-400/20 blur-3xl" />
              <div className="absolute -bottom-28 left-6 h-80 w-80 rounded-full bg-indigo-500/20 blur-3xl" />
              <div className="relative grid h-full gap-6 sm:gap-7 lg:grid-cols-2">
                <div className="grid gap-4">
                  <div className="dialog-meta reveal-text">What Oprai handles</div>
                  <p className="dialog-body reveal-text">
                    Oprai sits between your prompt and compliant execution—translating treasury rules, policy guardrails, and venue routing into a single signature-ready runbook.
                  </p>
                  <div className="dialog-annotation dialog-annotation--primary reveal-text">
                    <ul className="dialog-highlights">
                      {dialogHighlights.map((item, idx) => (
                        <li key={item} className="reveal-cascade" style={{ transitionDelay: `${0.3 + idx * 0.1}s` }}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                <div className="grid content-start gap-[1.75rem]">
                  <div className="dialog-meta reveal-text">You can say</div>
                  {dialogExamples.map((example, index) => (
                    <div
                      key={example}
                      className="dialog-prompt reveal-cascade"
                      style={{ transitionDelay: `${0.3 + index * 0.15}s` }}
                    >
                      <span className="dialog-prompt__caret">$</span>
                      <span className="dialog-prompt__text">{example}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="border-t border-white/5"></div>

        <section id="features" className="section-theme section-theme--features relative py-[clamp(72px,9vw,160px)]">
          <div className="reveal w-full">
            <Features />
          </div>
        </section>

        <div className="border-t border-white/5"></div>

        <section id="workflow" className="section-theme section-theme--workflow py-[clamp(80px,10vw,168px)]">
          <HowItWorks />
        </section>

        <div className="border-t border-white/5"></div>

        {/* Roadmap temporarily removed (component not available) */}

        <section id="faq" className="section-theme section-theme--faq flex flex-col items-stretch py-0">
          <div className="w-full px-4 sm:px-6 md:px-10 pt-10 pb-8 sm:pt-12 sm:pb-10 md:pt-14 md:pb-12">
            <FAQ className="mx-auto w-full max-w-4xl" />
          </div>
        </section>
      </main>
    </div>
  );
}
