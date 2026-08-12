import { Injectable, signal, computed, NgZone, inject } from '@angular/core';
import { Subject } from 'rxjs';
import bs58 from 'bs58';
import { MemoryService } from './memory.service';

export interface WalletAdapter {
  name: string;
  icon: string;
  url: string;
  readyState: 'installed' | 'loadable' | 'notDetected' | 'unsupported';
  publicKey: { toBase58(): string } | null;
  connected: boolean;
  connect(opts?: { onlyIfTrusted?: boolean }): Promise<void>;
  disconnect(): Promise<void>;
  signMessage(message: Uint8Array): Promise<Uint8Array | { signature: Uint8Array }>;
  signTransaction(transaction: unknown): Promise<unknown>;
  on(event: string, callback: (...args: unknown[]) => void): void;
  off(event: string, callback: (...args: unknown[]) => void): void;
}

export interface WalletInfo {
  name: string;
  icon: string;
  detected: boolean;
  url: string;
}

// Curated catalog of well-known Solana wallets. Two jobs: supply an install
// link and a bundled icon for wallets the user does NOT have (a Standard-
// registered wallet already carries its own name + icon), and fix display
// order among the popular ones. Any Standard wallet not listed here still
// appears — it just uses the icon the wallet itself provides and sorts after
// the catalog. Names are matched case-insensitively against the Standard
// wallet's own `name`.
interface WalletCatalogEntry {
  name: string;
  icon: string;
  url: string;
}

const WALLET_CATALOG: WalletCatalogEntry[] = [
  { name: 'Phantom', icon: '/icons/wallets/phantom.svg', url: 'https://phantom.app/' },
  { name: 'Solflare', icon: '/icons/wallets/solflare.svg', url: 'https://solflare.com/' },
  { name: 'Backpack', icon: '/icons/wallets/backpack.svg', url: 'https://backpack.app/' },
  { name: 'OKX Wallet', icon: '/icons/wallets/okx.png', url: 'https://www.okx.com/web3' },
  { name: 'MetaMask', icon: '/icons/wallets/metamask.svg', url: 'https://metamask.io/' },
  { name: 'Coinbase Wallet', icon: '/icons/wallets/coinbase.svg', url: 'https://www.coinbase.com/wallet' },
  { name: 'Trust', icon: '/icons/wallets/trust.svg', url: 'https://trustwallet.com/' },
  { name: 'Bitget Wallet', icon: '/icons/wallets/bitget.svg', url: 'https://web3.bitget.com/' },
  { name: 'Coin98', icon: '/icons/wallets/coin98.svg', url: 'https://coin98.com/wallet' },
  { name: 'Magic Eden', icon: '/icons/wallets/magic-eden.svg', url: 'https://wallet.magiceden.io/' },
  { name: 'Ledger', icon: '/icons/wallets/ledger.svg', url: 'https://www.ledger.com/' },
  { name: 'Frontier', icon: '/icons/wallets/frontier.svg', url: 'https://frontier.xyz/' },
];

/** Match a Standard wallet's name to a catalog entry, tolerant of suffixes
 *  ("OKX" vs "OKX Wallet", "Coinbase" vs "Coinbase Wallet"). */
function catalogFor(name: string): WalletCatalogEntry | undefined {
  const n = name.toLowerCase();
  return WALLET_CATALOG.find(
    (c) => c.name.toLowerCase() === n || n.includes(c.name.toLowerCase()) || c.name.toLowerCase().includes(n),
  );
}

// ──────────────────────────────────────────────────────────────
// Wallet Standard types (MetaMask v13.5+ registers via this spec)
// ──────────────────────────────────────────────────────────────
interface WalletStandardAccount {
  address: string;
  publicKey: Uint8Array;
  chains: string[];
  features: string[];
}

// Typed Wallet Standard feature interfaces replace `as any` casts.
interface WalletConnectFeature {
  connect(opts: { silent: boolean }): Promise<{ accounts: WalletStandardAccount[] }>;
}
interface WalletDisconnectFeature {
  disconnect(): Promise<void>;
}
interface WalletEventsFeature {
  on(event: 'change', cb: (props: { accounts?: WalletStandardAccount[] }) => void): () => void;
}
interface WalletSignMessageFeature {
  signMessage(opts: { account: WalletStandardAccount; message: Uint8Array }): Promise<[{ signature: Uint8Array }]>;
}
interface WalletSignTransactionFeature {
  signTransaction(opts: {
    account: WalletStandardAccount;
    transaction: Uint8Array;
    chain: string;
  }): Promise<[{ signedTransaction: Uint8Array }]>;
}

// Type guards for Wallet Standard features
function isConnectFeature(f: unknown): f is WalletConnectFeature {
  return typeof f === 'object' && f !== null && typeof (f as WalletConnectFeature).connect === 'function';
}
function isDisconnectFeature(f: unknown): f is WalletDisconnectFeature {
  return typeof f === 'object' && f !== null && typeof (f as WalletDisconnectFeature).disconnect === 'function';
}
function isEventsFeature(f: unknown): f is WalletEventsFeature {
  return typeof f === 'object' && f !== null && typeof (f as WalletEventsFeature).on === 'function';
}
function isSignMessageFeature(f: unknown): f is WalletSignMessageFeature {
  return typeof f === 'object' && f !== null && typeof (f as WalletSignMessageFeature).signMessage === 'function';
}
function isSignTransactionFeature(f: unknown): f is WalletSignTransactionFeature {
  return typeof f === 'object' && f !== null && typeof (f as WalletSignTransactionFeature).signTransaction === 'function';
}

interface WalletStandardFeatures {
  'standard:connect'?: unknown;
  'standard:disconnect'?: unknown;
  'standard:events'?: unknown;
  'solana:signMessage'?: unknown;
  'solana:signTransaction'?: unknown;
  'solana:signAndSendTransaction'?: unknown;
  [key: string]: unknown;
}

interface WalletStandardWallet {
  name: string;
  icon: string;
  chains: string[];
  features: WalletStandardFeatures;
  accounts: WalletStandardAccount[];
}

// ──────────────────────────────────────────────────────────────
// Standard Wallet Adapter
//
// Every modern Solana wallet — Phantom, Solflare, Backpack, OKX, Glow,
// Coinbase, Trust and more — registers itself via the Wallet Standard spec
// (`wallet-standard:app-ready` → `register(wallet)`) rather than injecting a
// bespoke `window.*` object. One adapter wraps any of them into our
// WalletAdapter interface, so the rest of the app is unaware of which wallet
// is connected. This was written for MetaMask first and generalised — the
// signing logic never had anything MetaMask-specific in it.
// ──────────────────────────────────────────────────────────────
class StandardWalletAdapter implements WalletAdapter {
  readonly name: string;
  readonly icon: string;
  readonly url: string;
  readonly readyState = 'installed' as const;

  private readonly _wallet: WalletStandardWallet;
  private _account: WalletStandardAccount | null = null;
  private _unsubscribeEvents: (() => void) | null = null;

  // event name → callback (for on/off)
  private readonly _listeners = new Map<string, (...args: unknown[]) => void>();

  get publicKey(): { toBase58(): string } | null {
    if (!this._account) return null;
    return { toBase58: () => bs58.encode(this._account!.publicKey) };
  }

  get connected(): boolean {
    return this._account !== null;
  }

  constructor(wallet: WalletStandardWallet, url = '') {
    this._wallet = wallet;
    this.name = wallet.name;
    this.icon = wallet.icon; // data URI from Wallet Standard
    this.url = url;
  }

  async connect(opts?: { onlyIfTrusted?: boolean }): Promise<void> {
    const feature = this._wallet.features['standard:connect'];
    if (!isConnectFeature(feature)) throw new Error(`${this.name}: standard:connect not supported`);

    const result = await feature.connect({ silent: opts?.onlyIfTrusted ?? false });

    const solanaAccount = result.accounts.find((a: WalletStandardAccount) =>
      a.chains.some((c: string) => c.startsWith('solana:'))
    );
    if (!solanaAccount) {
      // MetaMask ships Solana behind an experimental flag; other wallets that
      // registered a Solana chain but returned no Solana account are simply
      // set to a non-Solana network. Point each at the right fix.
      throw new Error(
        this.name === 'MetaMask'
          ? 'No Solana account found in MetaMask. Go to MetaMask → Settings → Experimental → Enable Solana accounts.'
          : `No Solana account found in ${this.name}. Switch it to a Solana account and try again.`
      );
    }
    this._account = solanaAccount;
    this._subscribeToAccountChanges();
  }

  async disconnect(): Promise<void> {
    this._unsubscribeEvents?.();
    this._unsubscribeEvents = null;
    const feature = this._wallet.features['standard:disconnect'];
    if (isDisconnectFeature(feature)) await feature.disconnect();
    this._account = null;
  }

  async signMessage(message: Uint8Array): Promise<Uint8Array | { signature: Uint8Array }> {
    if (!this._account) throw new Error(`${this.name}: not connected`);
    const feature = this._wallet.features['solana:signMessage'];
    if (!isSignMessageFeature(feature)) throw new Error(`${this.name}: solana:signMessage not supported`);

    const [result] = await feature.signMessage({ account: this._account, message });
    return result.signature;
  }

  /**
   * Signs a web3.js Transaction or VersionedTransaction.
   * Serializes to bytes → Wallet Standard sign → returns { serialize() } wrapper
   * so SolanaActionService can call signedTx.serialize() normally.
   */
  async signTransaction(transaction: unknown): Promise<unknown> {
    if (!this._account) throw new Error(`${this.name}: not connected`);
    const feature = this._wallet.features['solana:signTransaction'];
    if (!isSignTransactionFeature(feature)) throw new Error(`${this.name}: solana:signTransaction not supported`);

    const tx = transaction as { serialize(opts?: { requireAllSignatures?: boolean }): Uint8Array };
    const serialized = tx.serialize({ requireAllSignatures: false });

    const [result] = await feature.signTransaction({
      account: this._account,
      transaction: serialized,
      chain: 'solana:mainnet',
    });

    return { serialize: () => result.signedTransaction };
  }

  on(event: string, callback: (...args: unknown[]) => void): void {
    this._listeners.set(event, callback);
  }

  off(event: string, _callback: (...args: unknown[]) => void): void {
    this._listeners.delete(event);
  }

  private _subscribeToAccountChanges(): void {
    // Unsubscribe any previous listener before creating a new one.
    // Guards against duplicate listeners if connect() is called more than once.
    this._unsubscribeEvents?.();
    this._unsubscribeEvents = null;

    const eventsFeature = this._wallet.features['standard:events'];
    if (!isEventsFeature(eventsFeature)) return;

    this._unsubscribeEvents = eventsFeature.on(
      'change',
      (props: { accounts?: WalletStandardAccount[] }) => {
        if (props.accounts === undefined) return;

        const newAccount = props.accounts.find((a) =>
          a.chains.some((c: string) => c.startsWith('solana:'))
        ) ?? null;

        this._account = newAccount;

        if (!newAccount) {
          this._listeners.get('disconnect')?.();
        } else {
          this._listeners.get('accountChanged')?.();
        }
      }
    );
  }
}

// ──────────────────────────────────────────────────────────────
// WalletService
// ──────────────────────────────────────────────────────────────
@Injectable({ providedIn: 'root' })
export class WalletService {
  private readonly zone = inject(NgZone);
  private readonly memory = inject(MemoryService);

  private readonly _connected = signal(false);
  private readonly _publicKey = signal<string | null>(null);
  private readonly _walletName = signal<string | null>(null);
  private readonly _connecting = signal(false);
  private _adapter: WalletAdapter | null = null;

  /**
   * Cached Wallet Standard detection: lowercase wallet name → wallet object.
   * Populated on first call to detectStandardWallets(). The registry can grow
   * after page load (a wallet extension finishing its own init), so a wallet
   * that also injects late is picked up on a later dispatch — but the common
   * case is all wallets are present by first user interaction.
   */
  private _standardCache: Map<string, WalletStandardWallet> | null = null;

  /**
   * Singleton adapters keyed by lowercase wallet name. Re-using the same
   * instance prevents duplicate Standard event listeners that would accumulate
   * if a new adapter were created on every getAdapter() call.
   */
  private readonly _standardAdapters = new Map<string, StandardWalletAdapter>();

  /**
   * Stored event callbacks — needed to properly remove listeners via off()
   * when the adapter changes (e.g. disconnect → new wallet connect).
   */
  private _onDisconnect: (() => void) | null = null;
  private _onAccountChanged: (() => void) | null = null;

  readonly connected = this._connected.asReadonly();
  readonly publicKey = this._publicKey.asReadonly();
  readonly walletName = this._walletName.asReadonly();
  readonly connecting = this._connecting.asReadonly();

  /** Emits the new public key when the user switches accounts in their wallet. */
  readonly accountChanged$ = new Subject<string | null>();

  /** Cancel a pending connect (resets _connecting UI state only). */
  cancelConnection(): void {
    this._connecting.set(false);
  }

  /** Shortened address for display: "HwM3...k9f2" */
  readonly shortAddress = computed(() => {
    const key = this._publicKey();
    if (!key) return null;
    return `${key.slice(0, 4)}...${key.slice(-4)}`;
  });

  /**
   * A stored wallet name is only trusted for auto-reconnect if it is one we
   * know — this bounds what a tampered localStorage value can feed into
   * getAdapter(). The catalog is the source of truth, so adding a wallet there
   * is all it takes; getAdapter() still validates the wallet actually exists
   * before doing anything with it.
   */
  private static isKnownWallet(name: string): boolean {
    const n = name.toLowerCase();
    return WALLET_CATALOG.some(
      (c) => c.name.toLowerCase() === n || n.includes(c.name.toLowerCase()) || c.name.toLowerCase().includes(n),
    );
  }

  /**
   * Auto-reconnect: silently reconnect if user previously approved this site.
   * Uses onlyIfTrusted: true so no popup appears.
   * Call this on app init.
   */
  async autoConnect(): Promise<boolean> {
    const stored = localStorage.getItem('oprai-last-wallet');
    // Only reconnect to explicitly whitelisted wallet names to prevent stored value injection.
    if (!stored || !WalletService.isKnownWallet(stored)) return false;
    const lastWallet = stored;

    const adapter = this.getAdapter(lastWallet);
    if (!adapter) return false;

    try {
      await adapter.connect({ onlyIfTrusted: true });

      if (adapter.publicKey) {
        this.removeEvents();
        this._adapter = adapter;
        const newKey = adapter.publicKey?.toBase58() ?? null;
        this.zone.run(() => {
          this._connected.set(true);
          this._publicKey.set(newKey);
          this._walletName.set(lastWallet);
        });
        // Fire accountChanged so the auth layer re-binds to this wallet
        // even on silent (trusted) reconnect at page load.
        this.accountChanged$.next(newKey);
        this.attachEvents(adapter);
        return true;
      }
    } catch {
      // User hasn't trusted this site yet — silent fail is expected
    }
    return false;
  }

  /**
   * The wallet list the connect modal renders. Detected wallets come first
   * (Jupiter-style), each carrying its real icon; then the rest of the curated
   * catalog as install prompts. A Standard wallet the catalog doesn't know
   * still shows — it just sorts after the known ones.
   *
   * `force` re-scans the Standard registry, used when the modal opens so a
   * wallet that finished initialising after page load is not missed.
   */
  getWallets(force = false): WalletInfo[] {
    const standard = this.detectStandardWallets(force);
    // A few wallets still only inject the legacy `window.*` object without
    // registering via Standard. Treat those as detected too, so nothing that
    // actually works is shown as "Install".
    const win = window as Window & {
      phantom?: { solana?: { isPhantom?: boolean } };
      solflare?: { isSolflare?: boolean };
      backpack?: { isBackpack?: boolean };
      okxwallet?: { solana?: unknown };
    };
    const legacyDetected = (name: string): boolean => {
      switch (name.toLowerCase()) {
        case 'phantom': return !!win?.phantom?.solana?.isPhantom;
        case 'solflare': return !!win?.solflare?.isSolflare;
        case 'backpack': return !!win?.backpack?.isBackpack;
        case 'okx wallet': return !!win?.okxwallet?.solana;
        default: return false;
      }
    };

    const seen = new Set<string>();
    const detected: WalletInfo[] = [];

    // 1. Everything the Standard registry reports, in catalog order first.
    for (const entry of WALLET_CATALOG) {
      const std = [...standard.entries()].find(([n]) =>
        n === entry.name.toLowerCase() ||
        n.includes(entry.name.toLowerCase()) ||
        entry.name.toLowerCase().includes(n),
      );
      const isDetected = !!std || legacyDetected(entry.name);
      if (isDetected) {
        detected.push({
          name: std ? std[1].name : entry.name,
          icon: std ? std[1].icon : entry.icon,
          detected: true,
          url: entry.url,
        });
        seen.add((std ? std[1].name : entry.name).toLowerCase());
      }
    }

    // 2. Standard wallets the catalog has never heard of — brand-new or niche.
    for (const [key, w] of standard) {
      if (seen.has(key)) continue;
      detected.push({ name: w.name, icon: w.icon, detected: true, url: '' });
      seen.add(key);
    }

    // 3. The rest of the catalog as install prompts, in catalog order.
    const installable: WalletInfo[] = WALLET_CATALOG
      .filter((c) => !seen.has(c.name.toLowerCase()) && ![...seen].some((s) => s.includes(c.name.toLowerCase()) || c.name.toLowerCase().includes(s)))
      .map((c) => ({ name: c.name, icon: c.icon, detected: false, url: c.url }));

    return [...detected, ...installable];
  }

  /**
   * Connect to a specific wallet by name.
   * This is just the connect step — no signature, no auth.
   */
  async connect(walletName: string = 'Phantom'): Promise<void> {
    if (this._connecting()) return;
    this._connecting.set(true);

    try {
      const adapter = this.getAdapter(walletName);
      if (!adapter) {
        this._connecting.set(false);
        throw new Error(`${walletName} wallet not found. Please install it first.`);
      }

      // Connect first — only replace _adapter on success.
      // If connect() throws, the old adapter remains intact.
      await adapter.connect();

      // Remove old adapter's listeners before replacing it.
      this.removeEvents();
      this._adapter = adapter;

      const newKey = adapter.publicKey?.toBase58() ?? null;
      this.zone.run(() => {
        this._connected.set(true);
        this._publicKey.set(newKey);
        this._walletName.set(walletName);
        this._connecting.set(false);
      });

      // SECURITY: notify subscribers (app.component logout + re-auth) that the
      // active wallet may have changed. Without this, a disconnect → connect
      // with a different wallet flow leaves the previous JWT in memory; backend
      // session-list calls still read the OLD wallet's data via X-User-Wallet
      // injected from the stale JWT — i.e. the new wallet sees the previous
      // wallet's chat history. accountChanged$ only fired on adapter native
      // events before; we explicitly emit on every successful connect now.
      this.accountChanged$.next(newKey);

      localStorage.setItem('oprai-last-wallet', walletName);
      void this.updateMemoryConsent(); // fire-and-forget: non-critical consent update
      this.attachEvents(adapter);
    } catch (err) {
      this._connecting.set(false);
      const e = err as { code?: number; message?: string };
      console.error('[wallet] connect failed:', walletName, 'code=', e?.code, 'msg=', e?.message, err);
      // Phantom/Solana wallets surface an internal failure as JSON-RPC code
      // -32603 with the opaque text "Unexpected error". It's a wallet-side /
      // connection-state issue (not a user rejection), so give the user an
      // actionable next step instead of the raw string.
      if (e?.code === -32603 || /unexpected error/i.test(e?.message ?? '')) {
        throw new Error(
          'Your wallet hit an internal error. Reload the page and make sure the wallet is unlocked, then try again. If it keeps failing, open your wallet, remove this site from its connected apps, and reconnect.',
        );
      }
      throw err;
    }
  }

  /** Disconnect the current wallet. */
  async disconnect(): Promise<void> {
    if (this._adapter) {
      try {
        await this._adapter.disconnect();
      } catch {
        // Adapter rejected the call (e.g. already disconnected) — proceed with local cleanup.
      }
    }
    localStorage.removeItem('oprai-last-wallet');
    this.handleDisconnect();
  }

  /**
   * Sign a message with the connected wallet.
   * Handles Phantom's { signature } wrapper and plain Uint8Array returns.
   */
  async signMessage(message: Uint8Array): Promise<Uint8Array> {
    if (!this._adapter) throw new Error('No wallet connected');

    const result = await this._adapter.signMessage(message);

    const sig = WalletService.coerceSignatureBytes(result);
    if (sig && sig.length > 0) return sig;

    // Log the actual shape so an unexpected wallet return is diagnosable.
    console.error('[wallet] unexpected signMessage response:', result);
    throw new Error('Unexpected signMessage response format');
  }

  /**
   * Normalize a wallet's signMessage return into raw signature bytes. Wallets
   * return this in several shapes: a plain Uint8Array (standard adapter), a
   * `{ signature }` wrapper (Phantom), a Buffer-JSON `{ type:'Buffer', data }`,
   * a number[], or an array-like object. Critically, a Uint8Array minted inside
   * the extension's realm can fail `instanceof Uint8Array` in the page — so we
   * detect typed arrays via `ArrayBuffer.isView` instead of instanceof.
   */
  private static coerceSignatureBytes(input: unknown): Uint8Array | null {
    if (input == null) return null;
    if (ArrayBuffer.isView(input)) {
      const view = input as ArrayBufferView;
      return new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    }
    if (input instanceof ArrayBuffer) return new Uint8Array(input);
    if (Array.isArray(input)) return Uint8Array.from(input as number[]);
    if (typeof input === 'object') {
      const obj = input as Record<string, unknown>;
      if ('signature' in obj) return WalletService.coerceSignatureBytes(obj['signature']);
      if (Array.isArray(obj['data'])) return Uint8Array.from(obj['data'] as number[]);
      // Array-like plain object: { 0: .., 1: .., length? }
      const numeric = Object.keys(obj)
        .filter(k => /^\d+$/.test(k))
        .sort((a, b) => Number(a) - Number(b))
        .map(k => obj[k]);
      if (numeric.length > 0 && numeric.every(v => typeof v === 'number')) {
        return Uint8Array.from(numeric as number[]);
      }
    }
    return null;
  }

  /** Sign a transaction with the connected wallet. */
  async signTransaction(transaction: unknown): Promise<unknown> {
    if (!this._adapter) throw new Error('No wallet connected');
    try {
      return await this._adapter.signTransaction(transaction);
    } catch (err) {
      throw WalletService.describeWalletFailure(err, 'sign');
    }
  }

  /**
   * Turn a wallet's opaque internal failure into something the user can act
   * on. Phantom reports its own scanning / connection faults as JSON-RPC
   * -32603 with the text "Unexpected error" — indistinguishable, as written,
   * from a bug in the transaction we built. `connect()` already translated it;
   * signing did not, so the same fault read as an app error there.
   *
   * A user rejection is passed through untouched: that is a decision, not a
   * failure, and callers detect it by message.
   */
  private static describeWalletFailure(err: unknown, phase: 'sign' | 'send'): Error {
    const e = err as { code?: number; message?: string };
    const msg = e?.message ?? '';
    console.error(`[wallet] ${phase} failed: code=`, e?.code, 'msg=', msg, err);
    if (/reject|denied|cancel|declined|user refused/i.test(msg)) {
      return err instanceof Error ? err : new Error(msg || 'Rejected in wallet');
    }
    if (e?.code === -32603 || /unexpected error/i.test(msg)) {
      // A short reference code, not the raw payload. "Unexpected error" is the
      // same string for a locked wallet, a stale connection and a transaction
      // the wallet's guard layer choked on, so the code is worth keeping — but
      // the message and data belong in the console above, not on a card.
      const ref = typeof e?.code === 'number' ? ` (wallet code ${e.code})` : '';
      return new Error(
        'Your wallet couldn’t complete the signature. The transaction itself is fine — this is the wallet’s own ' +
          'error. Make sure it’s unlocked and try again; if it keeps happening, reload the page, or disconnect this ' +
          `site in your wallet’s settings and reconnect.${ref}`,
      );
    }
    return err instanceof Error ? err : new Error(msg || 'Wallet error');
  }

  /**
   * Sign and send a transaction in one call, optionally skipping preflight simulation.
   * Supported by Phantom; falls back to null for wallets that don't expose this method.
   * Returns the transaction signature on success, or null if the wallet doesn't support it.
   */
  async signAndSendTransaction(
    transaction: unknown,
    options?: { skipPreflight?: boolean },
  ): Promise<string | null> {
    if (!this._adapter) throw new Error('No wallet connected');
    const fn = (this._adapter as any).signAndSendTransaction;
    if (typeof fn !== 'function') return null;
    let result: any;
    try {
      result = await fn.call(this._adapter, transaction, options ?? {});
    } catch (err) {
      throw WalletService.describeWalletFailure(err, 'send');
    }
    if (typeof result === 'string') return result;
    if (result?.signature && typeof result.signature === 'string') return result.signature;
    if (result?.publicKey === undefined && result?.signature === undefined) {
      // Some adapters return { signature: Uint8Array } — encode to base58
      const sig = result?.signature as Uint8Array | undefined;
      if (sig instanceof Uint8Array) {
        const bs58 = await import('bs58');
        return bs58.default.encode(sig);
      }
    }
    return null;
  }

  // ── Private ──────────────────────────────────────────────────

  private getAdapter(walletName: string): WalletAdapter | null {
    const key = walletName.toLowerCase();

    // Prefer the Standard-registered wallet — this covers OKX, Phantom,
    // Solflare, Backpack, MetaMask and every other modern wallet with one path.
    const standard = this.detectStandardWallets();
    let std = standard.get(key);
    if (!std) {
      // Tolerate name drift between the stored/catalog name and the wallet's
      // own ("OKX Wallet" vs "OKX").
      for (const [n, w] of standard) {
        if (n.includes(key) || key.includes(n)) { std = w; break; }
      }
    }
    if (std) {
      const cacheKey = std.name.toLowerCase();
      let adapter = this._standardAdapters.get(cacheKey);
      if (!adapter) {
        adapter = new StandardWalletAdapter(std, catalogFor(std.name)?.url ?? '');
        this._standardAdapters.set(cacheKey, adapter);
      }
      return adapter;
    }

    // Legacy fallback: a few wallets still only inject `window.*`.
    const win = window as Window & {
      phantom?: { solana?: WalletAdapter };
      solflare?: WalletAdapter;
      backpack?: WalletAdapter;
      okxwallet?: { solana?: WalletAdapter };
    };
    switch (key) {
      case 'phantom': return win?.phantom?.solana ?? null;
      case 'solflare': return win?.solflare ?? null;
      case 'backpack': return win?.backpack ?? null;
      case 'okx wallet':
      case 'okx': return win?.okxwallet?.solana ?? null;
      default: return null;
    }
  }

  /**
   * Discover every Solana-capable wallet registered via the Wallet Standard
   * spec. Modern wallets listen for the 'wallet-standard:app-ready' event and
   * call register(walletObject) synchronously; we accept any that declares a
   * `solana:` chain and can sign a transaction, which is what actually matters
   * for this app. This is how OKX, Phantom, Solflare, Backpack, Glow, Coinbase
   * and the rest all surface without a per-wallet branch.
   *
   * `force` re-dispatches to pick up a wallet that registered after the first
   * call (extensions can finish init late). Used when the user opens the
   * connect modal, so the list is as fresh as possible at the moment of choice.
   */
  private detectStandardWallets(force = false): Map<string, WalletStandardWallet> {
    if (this._standardCache && !force) return this._standardCache;
    if (typeof window === 'undefined') return (this._standardCache = new Map());

    const found = new Map<string, WalletStandardWallet>();
    try {
      window.dispatchEvent(
        new CustomEvent('wallet-standard:app-ready', {
          bubbles: false,
          cancelable: false,
          composed: false,
          detail: {
            register: (wallet: WalletStandardWallet) => {
              if (
                wallet?.name &&
                Array.isArray(wallet.chains) &&
                wallet.chains.some((c) => c.startsWith('solana:')) &&
                'solana:signTransaction' in wallet.features
              ) {
                found.set(wallet.name.toLowerCase(), wallet);
              }
            },
          },
        })
      );
    } catch {
      // Wallet Standard not supported in this environment
    }

    return (this._standardCache = found);
  }

  /**
   * Attach disconnect / accountChanged event listeners to the adapter.
   * Stores callbacks as instance fields so they can be removed via off()
   * when the adapter is replaced (avoids stale listener accumulation).
   * Callers must call removeEvents() + set this._adapter BEFORE calling this.
   */
  private attachEvents(adapter: WalletAdapter): void {
    this._onDisconnect = () => {
      this.zone.run(() => this.handleDisconnect());
    };
    this._onAccountChanged = () => {
      this.zone.run(() => {
        const newKey = adapter.publicKey?.toBase58() ?? null;
        this._publicKey.set(newKey);
        this.accountChanged$.next(newKey);
      });
    };

    adapter.on('disconnect', this._onDisconnect);
    adapter.on('accountChanged', this._onAccountChanged);
  }

  /** Remove event listeners from the current adapter (if any). */
  private removeEvents(): void {
    if (this._adapter && this._onDisconnect) {
      this._adapter.off('disconnect', this._onDisconnect);
    }
    if (this._adapter && this._onAccountChanged) {
      this._adapter.off('accountChanged', this._onAccountChanged);
    }
    this._onDisconnect = null;
    this._onAccountChanged = null;
  }

  private handleDisconnect(): void {
    this.removeEvents();
    this._connected.set(false);
    this._publicKey.set(null);
    this._walletName.set(null);
    this._adapter = null;
  }

  private async updateMemoryConsent(): Promise<void> {
    if (!this.publicKey()) return;
    await this.memory.updateConsent({ decision: true, preference: true });
  }
}
