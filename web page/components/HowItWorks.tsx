const steps = [
  {
    id: '01',
    title: 'Prompt → Execution Brief',
    headline: 'Intent becomes a MEV-protected execution plan.',
    details: [
      'Natural language parsed into Solana instructions',
      'Multi-protocol routing optimization',
      'Gas estimation and priority fee calculation'
    ]
  },
  {
    id: '02',
    title: 'Simulate Before Deploy',
    headline: 'Every path backtested against live mempool.',
    details: [
      'Slippage simulation across all DEX routes',
      'Impermanent loss calculations',
      'Smart contract vulnerability scanning'
    ]
  },
  {
    id: '03',
    title: 'Confirm With Context',
    headline: 'Transparent pricing with institutional analytics.',
    details: [
      'Real-time price impact analysis',
      'MEV extraction prevention',
      'Cross-venue arbitrage detection'
    ]
  },
  {
    id: '04',
    title: 'Monitor And Review',
    headline: 'Real-time P&L with on-chain attribution.',
    details: [
      'Transaction success rate tracking',
      'Profit/loss attribution by strategy',
      'Automated tax reporting exports'
    ]
  }
];

const deskPanels = [
  {
    title: 'Intent',
    items: [
      { value: 'Launch token on Pump.fun' },
      { value: 'Add $50k liquidity on Raydium' },
      { value: 'Lock LP for 30 days' },
      { value: 'Enable trading with anti-rug mechanics' }
    ]
  },
  {
    title: 'Policies',
    items: [
      { label: 'Max slippage', value: '0.5%' },
      { label: 'MEV protection', value: 'Enabled' },
      { label: 'Multi-sig threshold', value: '$10,000' },
      { label: 'Audit requirement', value: 'CertiK verified only' }
    ]
  },
  {
    title: 'Simulation',
    items: [
      { label: 'Expected APR', value: '127.3%' },
      { label: 'Gas cost', value: '0.0023 SOL' },
      { label: 'Success probability', value: '98.7%' },
      { label: 'Risk score', value: 'Medium (IL exposure)' }
    ]
  },
  {
    title: 'Execution',
    items: [
      { label: 'Route', value: 'Jito Bundle #4829184' },
      { label: 'Saved', value: '$127 vs direct execution' },
      { label: 'Performance', value: '+2.3% vs benchmark' },
      { label: 'Status', value: 'Multi-sig pending (2/3)' }
    ]
  }
] as const;

type DeskItem = (typeof deskPanels)[number]['items'][number];

function renderDeskItem(panelTitle: string, item: DeskItem) {
  const itemKey = `${panelTitle}-${'label' in item ? item.label : item.value}`;
  const hasLabel = 'label' in item;

  if (hasLabel) {
    return (
      <li key={itemKey} className="workflow-desk__item workflow-desk__item--stacked">
        <span className="workflow-desk__item-label">{item.label}</span>
        <span className="workflow-desk__item-value">{item.value}</span>
      </li>
    );
  }

  return (
    <li key={itemKey} className="workflow-desk__item workflow-desk__item--bullet">
      <span className="workflow-desk__bullet" aria-hidden="true" />
      <span className="workflow-desk__item-value">{item.value}</span>
    </li>
  );
}

export function HowItWorks() {
  return (
    <div className="container flex flex-col gap-8 sm:gap-10 md:gap-12">
      <div className="text-left md:text-center px-2 sm:px-4">
        <span className="section-eyebrow reveal-eyebrow">Workflow</span>
        <h2 className="section-heading mt-3 sm:mt-4 text-balance reveal-heading">Institutional-Grade AI Execution Engine for Solana</h2>
        <p className="mt-3 sm:mt-4 text-pretty text-sm text-white/70 md:mx-auto md:max-w-3xl md:text-base reveal-text">
          Transform natural language into battle-tested DeFi strategies. Every transaction is simulated, optimized, and validated through our 4-layer security framework before execution.
        </p>
      </div>

      <div className="workflow-layout">
        <div className="workflow-steps">
          {steps.map((step) => (
            <div key={step.id} className="workflow-step reveal">
              <div className="workflow-step__header">
                <p className="workflow-step__label reveal-eyebrow">{step.title}</p>
                <h3 className="workflow-step__headline reveal-heading">{step.headline}</h3>
              </div>
              <ul className="workflow-step__list">
                {step.details.map((detail, idx) => (
                  <li key={detail} className="reveal-cascade" style={{ transitionDelay: `${0.2 + idx * 0.1}s` }}>{detail}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="workflow-desk reveal">
          <div className="workflow-desk__tabs">
            <span className="workflow-desk__tab reveal-eyebrow">Runbook Preview</span>
            <span className="workflow-desk__tab workflow-desk__tab--ghost reveal-eyebrow">Oprai Desk</span>
          </div>
          <div className="workflow-desk__grid">
            {deskPanels.map((panel, idx) => (
              <div key={panel.title} className="workflow-desk__card reveal-cascade" style={{ transitionDelay: `${idx * 0.1}s` }}>
                <h4 className="reveal-heading">{panel.title}</h4>
                <ul className="workflow-desk__list">
                  {panel.items.map((item) => renderDeskItem(panel.title, item))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
