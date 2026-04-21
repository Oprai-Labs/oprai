import type { Metadata } from 'next';
import './globals.css';
import { Footer } from '../components/Footer';

export const metadata: Metadata = {
  metadataBase: new URL('https://oprai.app'),
  title: {
    default: 'Oprai | On-chain AI Agent on Solana',
    template: '%s | Oprai'
  },
  description:
    'Oprai is a Solana-native AI agent that can execute everything on-chain: NFT trading, token swaps, perp DEX, token launch, and more — through natural language.',
  keywords: [
    'Oprai',
    'Solana',
    'AI Agent',
    'NFT trading',
    'token swap',
    'perp dex',
    'token launch',
    'DeFi',
    'crypto'
  ],
  authors: [{ name: 'Oprai' }],
  creator: 'Oprai',
  openGraph: {
    type: 'website',
    url: 'https://oprai.app',
    title: 'Oprai | On-chain AI Agent on Solana',
    description:
      'A comprehensive AI agent for Solana that executes NFT trades, token swaps, perp DEX, token launches and more via chat.',
    images: [
      {
        url: '/oprai-og.svg',
        width: 1200,
        height: 630,
        alt: 'Oprai'
      }
    ]
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Oprai | On-chain AI Agent on Solana',
    description:
      'A comprehensive AI agent for Solana that executes NFT trades, token swaps, perp DEX, token launches and more via chat.',
    images: ['/oprai-og.svg'],
    creator: '@oprai'
  },
  icons: {
    icon: '/favicon.svg',
    apple: '/apple-touch-icon.png'
  },
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'white' },
    { media: '(prefers-color-scheme: dark)', color: 'black' }
  ]
};

function ThemeScript() {
  // Lightweight theme init before hydration (no dependency on next-themes)
  const script = `
  try {
    const d = document.documentElement;
    const ls = localStorage.getItem('theme');
    const preferDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = ls || (preferDark ? 'dark' : 'light');
    d.classList.remove('light', 'dark');
    d.classList.add(theme);
    d.style.colorScheme = theme;
  } catch (e) {}
  `;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}

function ExtensionErrorFilterScript() {
  const script = `
  (function(){
    try {
      var extPrefix = 'chrome-extension://';
      function isExtError(msg, src, reason){
        msg = String(msg||'');
        src = String(src||'');
        var r = '';
        try { r = reason ? String((reason.stack||reason.message||reason)) : ''; } catch(_){}
        return msg.indexOf('Talisman extension')>-1 || src.indexOf(extPrefix)===0 || r.indexOf(extPrefix)>-1 || r.indexOf('Talisman extension')>-1;
      }
      if (typeof window !== 'undefined' && location && (location.hostname==='localhost' || location.hostname==='127.0.0.1')){
        window.addEventListener('error', function(e){
          var src = e && (e.filename||'');
          if (isExtError(e && e.message, src, e && e.error)){
            if (e.preventDefault) e.preventDefault();
            if (e.stopImmediatePropagation) e.stopImmediatePropagation();
          }
        }, true);
        window.addEventListener('unhandledrejection', function(e){
          if (isExtError('', '', e && e.reason)){
            if (e.preventDefault) e.preventDefault();
            if (e.stopImmediatePropagation) e.stopImmediatePropagation();
          }
        }, true);
      }
    } catch (e) {}
  })();
  `;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="min-h-screen bg-background font-sans antialiased overflow-x-hidden">
        <ThemeScript />
        <ExtensionErrorFilterScript />
        <main>{children}</main>
        <Footer className="final-footer w-full" variant="default" />
      </body>
    </html>
  );
}
