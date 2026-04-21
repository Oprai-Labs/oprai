export default function TermsPage() {
  return (
    <div className="container py-16 max-w-3xl">
      <h1 className="text-3xl md:text-4xl font-bold mb-4">Terms of Service</h1>
      <p className="text-muted-foreground">Updated: {new Date().toISOString().slice(0, 10)}</p>
      <div className="prose prose-invert mt-6">
        <p>These terms are a placeholder and will be finalized prior to public availability.</p>
      </div>
    </div>
  );
}












