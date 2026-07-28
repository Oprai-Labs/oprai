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
// MetaMask Wallet Standard Adapter
//
// MetaMask Extension v13.5+ registers itself via the Wallet Standard
// spec instead of injecting window.solana. We wrap its features into
// our WalletAdapter interface so the rest of the app is unaware.
// ──────────────────────────────────────────────────────────────
class MetaMaskStandardAdapter implements WalletAdapter {
  readonly name = 'MetaMask';
  readonly icon: string;
  readonly url = 'https://metamask.io/';
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

  constructor(wallet: WalletStandardWallet) {
    this._wallet = wallet;
    this.icon = wallet.icon; // data URI from Wallet Standard
  }

  async connect(opts?: { onlyIfTrusted?: boolean }): Promise<void> {
    const feature = this._wallet.features['standard:connect'];
    if (!isConnectFeature(feature)) throw new Error('MetaMask: standard:connect not supported');

    const result = await feature.connect({ silent: opts?.onlyIfTrusted ?? false });

    const solanaAccount = result.accounts.find((a: WalletStandardAccount) =>
      a.chains.some((c: string) => c.startsWith('solana:'))
    );
    if (!solanaAccount) {
      throw new Error(
        'No Solana account found in MetaMask. ' +
        'Go to MetaMask → Settings → Experimental → Enable Solana accounts.'
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
    if (!this._account) throw new Error('MetaMask: not connected');
    const feature = this._wallet.features['solana:signMessage'];
    if (!isSignMessageFeature(feature)) throw new Error('MetaMask: solana:signMessage not supported');

    const [result] = await feature.signMessage({ account: this._account, message });
    return result.signature;
  }

  /**
   * Signs a web3.js Transaction or VersionedTransaction.
   * Serializes to bytes → Wallet Standard sign → returns { serialize() } wrapper
   * so SolanaActionService can call signedTx.serialize() normally.
   */
  async signTransaction(transaction: unknown): Promise<unknown> {
    if (!this._account) throw new Error('MetaMask: not connected');
    const feature = this._wallet.features['solana:signTransaction'];
    if (!isSignTransactionFeature(feature)) throw new Error('MetaMask: solana:signTransaction not supported');

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
   * Cached MetaMask Wallet Standard detection result.
   * Populated on first call to detectMetaMaskStandard() — avoids dispatching
   * the wallet-standard:app-ready event on every getWallets() / getAdapter() call.
   */
  private _metamaskStdCache: WalletStandardWallet | null | undefined = undefined;

  /**
   * Singleton MetaMask adapter wrapper.
   * Re-using the same instance prevents duplicate MetaMask event listeners
   * that would accumulate if a new adapter were created on every getAdapter() call.
   */
  private _metamaskAdapter: MetaMaskStandardAdapter | null = null;

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

  /** Whitelisted wallet names that can be stored in localStorage. */
  private static readonly KNOWN_WALLETS = ['phantom', 'solflare', 'backpack', 'metamask'] as const;
  private static isKnownWallet(name: string): name is typeof WalletService.KNOWN_WALLETS[number] {
    return (WalletService.KNOWN_WALLETS as readonly string[]).includes(name.toLowerCase());
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
   * Returns the 4 supported Solana wallets.
   * MetaMask detection is via Wallet Standard (Extension v13.5+).
   */
  getWallets(): WalletInfo[] {
    const win = window as Window & {
      phantom?: { solana?: { isPhantom?: boolean } };
      solflare?: { isSolflare?: boolean };
      backpack?: { isBackpack?: boolean };
    };
    const metamaskStd = this.detectMetaMaskStandard();

    return [
      {
        name: 'Phantom',
        icon: '/icons/wallets/phantom.svg',
        detected: !!win?.phantom?.solana?.isPhantom,
        url: 'https://phantom.app/',
      },
      {
        name: 'Solflare',
        icon: '/icons/wallets/solflare.svg',
        detected: !!win?.solflare?.isSolflare,
        url: 'https://solflare.com/',
      },
      {
        name: 'Backpack',
        icon: '/icons/wallets/backpack.svg',
        detected: !!win?.backpack?.isBackpack,
        url: 'https://backpack.app/',
      },
      {
        name: 'MetaMask',
        icon: '/icons/wallets/metamask.svg',
        detected: !!metamaskStd,
        url: 'https://metamask.io/',
      },
    ];
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
      // Carry the wallet's own code/message through. "Unexpected error" is the
      // same string for a locked wallet, a stale connection and a transaction
      // the wallet's guard layer choked on — without the raw pair there is
      // nothing to tell them apart, and asking the user to open DevTools for
      // it is a worse trade than one ugly line in the message.
      const raw = [
        e?.code !== undefined ? `code=${e.code}` : null,
        msg ? `"${msg.slice(0, 120)}"` : null,
        (err as { data?: unknown })?.data !== undefined
          ? `data=${String(JSON.stringify((err as { data?: unknown }).data)).slice(0, 120)}`
          : null,
      ]
        .filter(Boolean)
        .join(' ');
      return new Error(
        'Your wallet hit an internal error before signing — the transaction itself was built and simulated fine. ' +
          'Unlock the wallet and try again; if it repeats, reload the page, or remove this site from the wallet’s ' +
          `connected apps and reconnect. [wallet: ${raw || 'no detail'}]`,
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
    const win = window as Window & {
      phantom?: { solana?: WalletAdapter };
      solflare?: WalletAdapter;
      backpack?: WalletAdapter;
    };

    switch (walletName.toLowerCase()) {
      case 'phantom':
        return win?.phantom?.solana ?? null;
      case 'solflare':
        return win?.solflare ?? null;
      case 'backpack':
        return win?.backpack ?? null;
      case 'metamask': {
        const wallet = this.detectMetaMaskStandard();
        if (!wallet) return null;
        this._metamaskAdapter ??= new MetaMaskStandardAdapter(wallet);
        return this._metamaskAdapter;
      }
      default:
        return null;
    }
  }

  /**
   * Discover MetaMask's Solana provider via the Wallet Standard spec.
   *
   * MetaMask Extension v13.5+ registers itself by listening for the
   * 'wallet-standard:app-ready' event and calling register(walletObject)
   * synchronously. Result is cached — MetaMask presence doesn't change
   * at runtime, and dispatching the event on every getWallets() is wasteful.
   */
  private detectMetaMaskStandard(): WalletStandardWallet | null {
    if (this._metamaskStdCache !== undefined) return this._metamaskStdCache;
    if (typeof window === 'undefined') return (this._metamaskStdCache = null);

    let found: WalletStandardWallet | null = null;

    try {
      window.dispatchEvent(
        new CustomEvent('wallet-standard:app-ready', {
          bubbles: false,
          cancelable: false,
          composed: false,
          detail: {
            register: (wallet: WalletStandardWallet) => {
              if (
                wallet.name === 'MetaMask' &&
                Array.isArray(wallet.chains) &&
                wallet.chains.some((c) => c.startsWith('solana:')) &&
                'solana:signTransaction' in wallet.features
              ) {
                found = wallet;
              }
            },
          },
        })
      );
    } catch {
      // Wallet Standard not supported in this environment
    }

    return (this._metamaskStdCache = found);
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
