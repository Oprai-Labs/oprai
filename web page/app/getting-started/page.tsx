export default function GettingStartedPage() {
  return (
    <div className="container py-16 max-w-3xl">
      <h1 className="text-3xl md:text-4xl font-bold mb-4">Getting Started</h1>
      <p className="text-muted-foreground">This page will include onboarding guides and FAQs.</p>
      <div className="prose prose-invert mt-6">
        <ol>
          <li>Connect your Solana wallet</li>
          <li>Set guardrails (size, slippage, leverage)</li>
          <li>Chat your intent and confirm</li>
        </ol>
      </div>
    </div>
  );
}












