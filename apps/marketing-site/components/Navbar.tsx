import Link from 'next/link';
import Image from 'next/image';
import type { Route } from 'next';

const nav: Array<{ label: string; href: Route | `#${string}` }> = [
  { label: 'Home', href: '/' },
  { label: 'Features', href: '#features' },
  { label: 'Workflow', href: '#workflow' },
  { label: 'FAQ', href: '#faq' }
];

export function Navbar() {
  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-[#050b1d]/80 backdrop-blur-2xl">
      <nav className="py-4">
        <div className="container hidden items-center justify-between md:flex">
          <div className="flex items-center gap-8">
            <Link href="/" className="flex items-center gap-2">
              <span className="brand-badge h-10 w-10">
                <Image src="/brand/oprai_agent.jpg" alt="Oprai" fill className="brand-mark" />
              </span>
              <span className="hidden text-2xl font-semibold tracking-tight text-white sm:inline-block">
                Oprai
              </span>
            </Link>
            <ul className="flex items-center gap-2 text-white/70">
              {nav.map((item) => (
                <li key={item.label}>
                  {typeof item.href === 'string' && item.href.startsWith('#') ? (
                    <a
                      href={item.href}
                      className="relative inline-flex h-9 items-center justify-center rounded-full px-4 text-sm font-medium transition-colors duration-200 hover:bg-white/10 hover:text-white"
                    >
                      {item.label}
                    </a>
                  ) : (
                    <Link
                      href={item.href as Route}
                      className="relative inline-flex h-9 items-center justify-center rounded-full px-4 text-sm font-medium transition-colors duration-200 hover:bg-white/10 hover:text-white"
                    >
                      {item.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex items-center gap-4 text-white/70">
            <div className="mr-4 flex items-center gap-4">
              <Link href="https://x.com/oprai_" target="_blank" aria-label="Oprai on X" className="transition-colors hover:text-white">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6"><path d="M22 4s-.7 2.1-2 3.4c1.6 10-9.4 17.3-18 11.6 2.2.1 4.4-.6 6-2C3 15.5.5 9.6 3 5c2.2 2.6 5.6 4.1 9 4-.9-4.2 4-6.6 7-3.8 1.1 0 3-1.2 3-1.2z"></path></svg>
              </Link>
              <Link href="https://discord.gg/dW8S6dVS6c" target="_blank" aria-label="Oprai on Discord" className="transition-colors hover:text-white">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" className="h-6 w-6"><path d="M20.317 4.369a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.078.037c-.211.375-.444.864-.608 1.249a18.27 18.27 0 00-5.472 0 12.3 12.3 0 00-.617-1.249.077.077 0 00-.078-.037c-1.7.3-3.3.83-4.885 1.515a.07.07 0 00-.032.027C1.395 9.062.63 13.58 1.11 18.053a.082.082 0 00.031.056 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028c.461-.63.873-1.295 1.226-1.994a.076.076 0 00-.041-.105 12.7 12.7 0 01-1.806-.86.077.077 0 01-.008-.128c.121-.091.242-.186.357-.28a.074.074 0 01-.077-.01c3.78 1.732 7.87 1.732 11.605 0a.074.074 0 01-.078.01c.115.094.236.189.357.28a.077.077 0 01-.006.128c-.577.333-1.19.62-1.807.86a.076.076 0 00-.04.106c.36.699.772 1.364 1.225 1.994a.078.078 0 00.084.028 19.88 19.88 0 005.994-3.03.078.078 0 00.03-.055c.5-5.177-.838-9.665-3.549-13.657a.061.061 0 00-.032-.028zM8.02 15.333c-1.157 0-2.107-1.06-2.107-2.364 0-1.305.93-2.364 2.107-2.364 1.187 0 2.128 1.07 2.107 2.364 0 1.305-.93 2.364-2.107 2.364zm7.975 0c-1.157 0-2.107-1.06-2.107-2.364 0-1.305.93-2.364 2.107-2.364 1.187 0 2.128 1.07 2.107 2.364 0 1.305-.92 2.364-2.107 2.364z"/></svg>
              </Link>
              <Link href="https://github.com/Oprai-Labs" target="_blank" aria-label="Oprai on GitHub" className="transition-colors hover:text-white">
                <svg width="24" height="24" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" className="h-6 w-6"><path d="M7.49933 0.25C3.49635 0.25 0.25 3.49593 0.25 7.50024C0.25 10.703 2.32715 13.4206 5.2081 14.3797C5.57084 14.446 5.70302 14.2222 5.70302 14.0299C5.70302 13.8576 5.69679 13.4019 5.69323 12.797C3.67661 13.235 3.25112 11.825 3.25112 11.825C2.92132 10.9874 2.44599 10.7644 2.44599 10.7644C1.78773 10.3149 2.49584 10.3238 2.49584 10.3238C3.22353 10.375 3.60629 11.0711 3.60629 11.0711C4.25298 12.1788 5.30335 11.8588 5.71638 11.6732C5.78225 11.205 5.96962 10.8854 6.17658 10.7043C4.56675 10.5209 2.87415 9.89918 2.87415 7.12104C2.87415 6.32925 3.15677 5.68257 3.62053 5.17563C3.54576 4.99226 3.29697 4.25521 3.69174 3.25691C3.69174 3.25691 4.30015 3.06196 5.68522 3.99973C6.26337 3.83906 6.8838 3.75895 7.50022 3.75583C8.1162 3.75895 8.73619 3.83906 9.31523 3.99973C10.6994 3.06196 11.3069 3.25691 11.3069 3.25691C11.7026 4.25521 11.4538 4.99226 11.3795 5.17563C11.8441 5.68257 12.1245 6.32925 12.1245 7.12104C12.1245 9.9063 10.4292 10.5192 8.81452 10.6985C9.07444 10.9224 9.30633 11.3648 9.30633 12.0413C9.30633 13.0102 9.29742 13.7922 9.29742 14.0299C9.29742 14.2239 9.42828 14.4496 9.79591 14.3788C12.6746 13.4179 14.75 10.7025 14.75 7.50024C14.75 3.49593 11.5036 0.25 7.49933 0.25Z" fill="currentColor" fillRule="evenodd" clipRule="evenodd"></path></svg>
              </Link>
            </div>
            <span className="inline-flex h-9 items-center justify-center whitespace-nowrap rounded-full border border-white/10 bg-white/[0.05] px-4 text-sm font-medium text-white/70">
              Get Started — Soon
            </span>
          </div>
        </div>

        {/* Mobile */}
        <div className="container flex items-center justify-between md:hidden">
          <Link href="/" className="flex items-center gap-2 py-2 text-white">
            <span className="brand-badge h-9 w-9">
              <Image src="/brand/oprai_agent.jpg" alt="Oprai" fill className="brand-mark" />
            </span>
            <span className="font-extrabold text-xl">Oprai</span>
          </Link>
          <span className="inline-flex h-8 items-center justify-center whitespace-nowrap rounded-full border border-white/10 bg-white/[0.05] px-3 text-xs font-medium text-white/70">
            Launch — Soon
          </span>
        </div>
      </nav>
    </header>
  );
}
