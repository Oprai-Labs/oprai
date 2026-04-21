export default function PrivacyPage() {
  return (
    <div className="container py-16 max-w-3xl">
      <h1 className="text-3xl md:text-4xl font-bold mb-4">Privacy Policy</h1>
      <p className="text-muted-foreground">Updated: {new Date().toISOString().slice(0, 10)}</p>
      <div className="prose prose-invert mt-6">
        <p>We respect your privacy. This placeholder page will be updated before public launch.</p>
      </div>
    </div>
  );
}


