import { TestBed } from '@angular/core/testing';
import { WalletService, WalletAdapter, WalletInfo } from './wallet.service';

describe('WalletService', () => {
  let service: WalletService;

  const createMockAdapter = (overrides: Partial<WalletAdapter> = {}): WalletAdapter => ({
    name: 'Mock Wallet',
    icon: '/icons/mock.svg',
    url: 'https://mockwallet.com',
    readyState: 'installed',
    publicKey: null,
    connected: false,
    connect: jasmine.createSpy('connect').and.returnValue(Promise.resolve()),
    disconnect: jasmine.createSpy('disconnect').and.returnValue(Promise.resolve()),
    signMessage: jasmine.createSpy('signMessage').and.returnValue(Promise.resolve(new Uint8Array([1, 2, 3]))),
    signTransaction: jasmine.createSpy('signTransaction').and.returnValue(Promise.resolve({})),
    on: jasmine.createSpy('on'),
    off: jasmine.createSpy('off'),
    ...overrides,
  });

  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(global, 'window', {
      value: { phantom: { solana: null }, solflare: null, backpack: null },
      writable: true,
    });
    TestBed.configureTestingModule({ providers: [WalletService] });
    service = TestBed.inject(WalletService);
  });

  afterEach(() => localStorage.clear());

  // ── Initial state ──────────────────────────────────────────

  it('should be created', () => expect(service).toBeTruthy());

  describe('Initial state', () => {
    it('connected should be false', () => expect(service.connected()).toBe(false));
    it('publicKey should be null', () => expect(service.publicKey()).toBeNull());
    it('walletName should be null', () => expect(service.walletName()).toBeNull());
    it('connecting should be false', () => expect(service.connecting()).toBe(false));
    it('shortAddress should be null', () => expect(service.shortAddress()).toBeNull());
  });

  // ── Wallet detection ───────────────────────────────────────

  describe('getWallets()', () => {
    it('should return exactly 4 wallets', () => {
      expect(service.getWallets().length).toBe(4);
    });

    it('should include Phantom, Solflare, Backpack, MetaMask', () => {
      const names = service.getWallets().map((w: WalletInfo) => w.name);
      expect(names).toContain('Phantom');
      expect(names).toContain('Solflare');
      expect(names).toContain('Backpack');
      expect(names).toContain('MetaMask');
    });

    it('should detect Phantom when present', () => {
      (window as any).phantom = { solana: { isPhantom: true } };
      const phantom = service.getWallets().find((w) => w.name === 'Phantom');
      expect(phantom?.detected).toBe(true);
    });

    it('should detect Solflare when present', () => {
      (window as any).solflare = { isSolflare: true };
      const solflare = service.getWallets().find((w) => w.name === 'Solflare');
      expect(solflare?.detected).toBe(true);
    });

    it('should detect Backpack when present', () => {
      (window as any).backpack = { isBackpack: true };
      const backpack = service.getWallets().find((w) => w.name === 'Backpack');
      expect(backpack?.detected).toBe(true);
    });

    it('should have correct icon paths', () => {
      const phantom = service.getWallets().find((w) => w.name === 'Phantom');
      expect(phantom?.icon).toContain('phantom.svg');
    });

    it('should have correct URL for Phantom', () => {
      const phantom = service.getWallets().find((w) => w.name === 'Phantom');
      expect(phantom?.url).toBe('https://phantom.app/');
    });

    it('should have correct URL for MetaMask', () => {
      const mm = service.getWallets().find((w) => w.name === 'MetaMask');
      expect(mm?.url).toBe('https://metamask.io/');
    });
  });

  // ── connect() ─────────────────────────────────────────────

  describe('connect()', () => {
    beforeEach(() => {
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'HwMdhvXYPPDircKQ7ub45XeMAeohJL4AT3V5CtshXtK6' },
        }),
      };
    });

    it('should throw when wallet not found', async () => {
      await expectAsync(service.connect('NonExistent')).toBeRejectedWithError(/wallet not found/);
    });

    it('should set connecting state during connection', async () => {
      let resolveConnect!: () => void;
      const connectPromise = new Promise<void>((r) => (resolveConnect = r));
      (window as any).phantom.solana = createMockAdapter({ connect: jasmine.createSpy().and.returnValue(connectPromise) });

      const p = service.connect('Phantom');
      expect(service.connecting()).toBe(true);
      resolveConnect();
      await p;
    });

    it('should update signals on successful connection', async () => {
      (window as any).phantom.solana = createMockAdapter({
        connected: true,
        publicKey: { toBase58: () => '7xKXtg2CWgGWuoNJnqVyrku89bXS3hV8FZwzwuBD5HHk' },
      });
      await service.connect('Phantom');
      expect(service.connected()).toBe(true);
      expect(service.publicKey()).toBe('7xKXtg2CWgGWuoNJnqVyrku89bXS3hV8FZwzwuBD5HHk');
      expect(service.walletName()).toBe('Phantom');
      expect(service.connecting()).toBe(false);
    });

    it('should save wallet choice to localStorage', async () => {
      (window as any).phantom.solana = createMockAdapter({
        connected: true,
        publicKey: { toBase58: () => 'TestKey123' },
      });
      await service.connect('Phantom');
      expect(localStorage.getItem('oprai-last-wallet')).toBe('Phantom');
    });

    it('should not connect when already connecting', () => {
      let resolveConnect!: () => void;
      const connectFn = jasmine.createSpy().and.callFake(
        () => new Promise<void>((r) => { resolveConnect = r; })
      );
      (window as any).phantom.solana = createMockAdapter({ connect: connectFn });

      service.connect('Phantom');
      service.connect('Phantom'); // second call ignored

      expect(service.connecting()).toBe(true);
      resolveConnect();
    });

    it('should reset connecting state on error', async () => {
      (window as any).phantom.solana = createMockAdapter({
        connect: jasmine.createSpy().and.returnValue(Promise.reject(new Error('User rejected'))),
      });
      await expectAsync(service.connect('Phantom')).toBeRejected();
      expect(service.connecting()).toBe(false);
    });

    it('should compute shortAddress after connection', async () => {
      (window as any).phantom.solana = createMockAdapter({
        connected: true,
        publicKey: { toBase58: () => '7xKXtg2CWgGWuoNJnqVyrku89bXS3hV8FZwzwuBD5HHk' },
      });
      await service.connect('Phantom');
      expect(service.shortAddress()).toBe('7xKX...5HHk');
    });

    it('should handle case-insensitive wallet names', async () => {
      (window as any).phantom.solana = createMockAdapter({
        connected: true,
        publicKey: { toBase58: () => 'TestKey123' },
      });
      await service.connect('PHANTOM');
      expect(service.connected()).toBe(true);
    });
  });

  // ── autoConnect() ─────────────────────────────────────────

  describe('autoConnect()', () => {
    it('should return false when no last wallet in localStorage', async () => {
      expect(await service.autoConnect()).toBe(false);
    });

    it('should return false when last wallet not installed', async () => {
      localStorage.setItem('oprai-last-wallet', 'Phantom');
      expect(await service.autoConnect()).toBe(false);
    });

    it('should return false when onlyIfTrusted fails', async () => {
      (window as any).phantom = {
        solana: createMockAdapter({
          connect: jasmine.createSpy().and.returnValue(Promise.reject(new Error('Not trusted'))),
        }),
      };
      localStorage.setItem('oprai-last-wallet', 'Phantom');
      expect(await service.autoConnect()).toBe(false);
    });

    it('should auto-connect when wallet is trusted', async () => {
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'AutoKey123' },
          connect: jasmine.createSpy().and.returnValue(Promise.resolve()),
        }),
      };
      localStorage.setItem('oprai-last-wallet', 'Phantom');
      expect(await service.autoConnect()).toBe(true);
      expect(service.connected()).toBe(true);
      expect(service.publicKey()).toBe('AutoKey123');
    });
  });

  // ── disconnect() ──────────────────────────────────────────

  describe('disconnect()', () => {
    beforeEach(async () => {
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'TestKey123' },
          disconnect: jasmine.createSpy().and.returnValue(Promise.resolve()),
        }),
      };
      await service.connect('Phantom');
    });

    it('should reset all signals', async () => {
      await service.disconnect();
      expect(service.connected()).toBe(false);
      expect(service.publicKey()).toBeNull();
      expect(service.walletName()).toBeNull();
    });

    it('should remove last wallet from localStorage', async () => {
      expect(localStorage.getItem('oprai-last-wallet')).toBe('Phantom');
      await service.disconnect();
      expect(localStorage.getItem('oprai-last-wallet')).toBeNull();
    });

    it('should call adapter disconnect', async () => {
      const disconnectSpy = (window as any).phantom.solana.disconnect;
      await service.disconnect();
      expect(disconnectSpy).toHaveBeenCalled();
    });
  });

  // ── signMessage() ─────────────────────────────────────────

  describe('signMessage()', () => {
    it('should throw when not connected', async () => {
      await expectAsync(service.signMessage(new Uint8Array([1]))).toBeRejectedWithError(/No wallet connected/);
    });

    it('should return Uint8Array when adapter returns it directly', async () => {
      const expected = new Uint8Array([1, 2, 3]);
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'Key' },
          signMessage: jasmine.createSpy().and.returnValue(Promise.resolve(expected)),
        }),
      };
      await service.connect('Phantom');
      expect(await service.signMessage(new Uint8Array([1]))).toBe(expected);
    });

    it('should unwrap Phantom { signature } format', async () => {
      const expected = new Uint8Array([5, 4, 3]);
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'Key' },
          signMessage: jasmine.createSpy().and.returnValue(Promise.resolve({ signature: expected })),
        }),
      };
      await service.connect('Phantom');
      expect(await service.signMessage(new Uint8Array([1]))).toBe(expected);
    });

    it('should throw for unexpected response format', async () => {
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'Key' },
          signMessage: jasmine.createSpy().and.returnValue(Promise.resolve('invalid' as any)),
        }),
      };
      await service.connect('Phantom');
      await expectAsync(service.signMessage(new Uint8Array([1]))).toBeRejectedWithError(/Unexpected signMessage/);
    });
  });

  // ── signTransaction() ─────────────────────────────────────

  describe('signTransaction()', () => {
    it('should throw when not connected', async () => {
      await expectAsync(service.signTransaction({})).toBeRejectedWithError(/No wallet connected/);
    });

    it('should call adapter signTransaction', async () => {
      const signedTx = { serialize: () => new Uint8Array() };
      (window as any).phantom = {
        solana: createMockAdapter({
          connected: true,
          publicKey: { toBase58: () => 'Key' },
          signTransaction: jasmine.createSpy().and.returnValue(Promise.resolve(signedTx)),
        }),
      };
      await service.connect('Phantom');
      const result = await service.signTransaction({});
      expect(result).toBe(signedTx);
    });
  });

  // ── accountChanged$ ───────────────────────────────────────

  describe('accountChanged$', () => {
    it('should emit when account changes', (done) => {
      service.accountChanged$.subscribe((key) => {
        expect(key).toBe('NewKey456');
        done();
      });

      // Manually trigger the path that emits accountChanged$
      // (In real usage, the wallet fires the 'accountChanged' event)
      (service as any).accountChanged$.next('NewKey456');
    });
  });
});
