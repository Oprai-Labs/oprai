'use client';
import { useState } from 'react';

type FAQProps = {
  className?: string;
};

const faqs = [
  {
    q: 'What is Oprai?',
    a: `A Solana-native AI agent that executes DeFi, crafts strategies, and analyzes tokens through natural language.`
  },
  {
    q: 'What can I execute with it?',
    a: `Launch NFTs and tokens, route swaps, run perps, manage lending loops, and trigger automation—Oprai stitches the full flow together.`
  },
  {
    q: 'Does Oprai help with strategy?',
    a: `Yes. It scans APRs, liquidity, and protocol risk to propose profitable routes, then applies guardrails before you confirm anything.`
  },
  {
    q: 'Can it analyze new tokens?',
    a: `Drop Pump.fun, Bonk.fun, or SPL contracts to see holder cohorts, bundle activity, whale scores, and other on-chain intelligence instantly.`
  }
];

export function FAQ({ className = '' }: FAQProps) {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <div className={`faq-shell relative flex flex-col gap-4 sm:gap-5 md:gap-6 rounded-[20px] sm:rounded-[24px] md:rounded-[28px] px-4 py-4 sm:px-6 sm:py-6 md:px-8 md:py-8 ${className}`}>
      <div className="space-y-2 sm:space-y-3 text-center -mt-1 sm:-mt-2 md:-mt-3 reveal-eyebrow">
        <h2 className="section-heading text-balance reveal-heading">Frequently Asked Questions</h2>
      </div>
      <div className="faq-panel reveal">
        <div className="relative divide-y divide-white/10 rounded-[16px] sm:rounded-[18px] md:rounded-[20px] border border-white/10 bg-white/[0.04]">
          {faqs.map((f, i) => {
            const isOpen = open === i;
            return (
              <div key={f.q} className="reveal-cascade">
                <button
                  type="button"
                  className={`flex w-full items-center justify-between gap-3 sm:gap-4 md:gap-6 px-4 py-3 sm:px-5 sm:py-3.5 md:px-6 md:py-4 text-left transition-colors ${isOpen ? 'bg-white/6' : 'hover:bg-white/5'}`}
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : i)}
                >
                  <span className="text-sm font-semibold text-white sm:text-base md:text-lg">{f.q}</span>
                  <svg className={`h-4 w-4 flex-shrink-0 text-white/70 transition-transform ${isOpen ? 'rotate-180' : ''}`} width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3.13523 6.15803C3.3241 5.95657 3.64052 5.94637 3.84197 6.13523L7.5 9.56464L11.158 6.13523C11.3595 5.94637 11.6759 5.95657 11.8648 6.15803C12.0536 6.35949 12.0434 6.67591 11.842 6.86477L7.84197 10.6148C7.64964 10.7951 7.35036 10.7951 7.15803 10.6148L3.15803 6.86477C2.95657 6.67591 2.94637 6.35949 3.13523 6.15803Z" fill="currentColor" fillRule="evenodd" clipRule="evenodd" />
                  </svg>
                </button>
                {isOpen && (
                  <div className="px-4 pb-3 pt-1 sm:px-5 sm:pb-3.5 md:px-6 md:pb-4 text-xs sm:text-sm text-white/70">
                    {f.a}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
