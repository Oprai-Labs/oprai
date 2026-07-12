/**
 * Jupiter Lend Service
 *
 * Integrates with Jupiter's on-chain lending protocol:
 * - Earn (deposit / withdraw) — isolated earn vaults, yield-bearing fTokens
 * - Borrow — isolated collateral/debt vaults, up to 95% LTV
 *
 * Program IDs (mainnet):
 *   Earn:   jup3YeL8QhtSx1e253b2FDvsMNC87fDrgQZivbrndc9
 *   Borrow: jupr81YtYssSyPt8jbnGuiWon5f6x9TcDEFxYe3Bdzi
 *
 * API: https://lite-api.jup.ag/lend
 *   Rate format: integer, divide by 100 → APY % (e.g. 409 → 4.09%)
 *   Collateral factor format: integer/1000 → ratio (e.g. 750 → 75%)
 *   convertToShares/Assets: integer / 10^decimals → ratio (e.g. 965732/1e6 → 0.9657)
 */
import { Injectable, inject } from '@angular/core';
import {
  ComputeBudgetProgram,
  Connection,
  PublicKey,
  SystemProgram,
  Transaction,
  VersionedTransaction,
} from '@solana/web3.js';
import {
  NATIVE_MINT,
  TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountIdempotentInstruction,
  createSyncNativeInstruction,
  createCloseAccountInstruction,
} from '@solana/spl-token';
import BN from 'bn.js';
import { getDepositIxs, getWithdrawIxs } from '@jup-ag/lend/earn';
import { MAX_REPAY_AMOUNT, MAX_WITHDRAW_AMOUNT } from '@jup-ag/lend/borrow';
import { WalletService } from '@core/services/wallet.service';
import { createSolanaConnection } from '@core/utils/solana-connection';

const LEND_API = 'https://lite-api.jup.ag/lend';

/**
 * fetch() with a hard timeout. Jupiter's lite-api occasionally hangs a request;
 * a bare fetch has no timeout, so a single stalled call would freeze the borrow
 * card on "Loading collateral options…" forever. Aborting after a few seconds
 * lets the caller fall back to its empty/error state gracefully.
 */
async function jupFetch(url: string, timeoutMs = 7000, init?: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...(init ?? {}), signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

// ─── Program IDs ────────────────────────────────────────────────────────────

// Supported assets (mainnet)
export const LEND_SUPPORTED_ASSETS: LendAsset[] = [
  { symbol: 'USDC',   mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', decimals: 6 },
  { symbol: 'USDT',   mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', decimals: 6 },
  { symbol: 'jupSOL', mint: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v',  decimals: 9 },
  { symbol: 'jitoSOL',mint: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', decimals: 9 },
  { symbol: 'EURC',   mint: 'HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr', decimals: 6 },
];

// ─── Types ──────────────────────────────────────────────────────────────────

export interface LendAsset {
  symbol: string;
  mint: string;
  decimals: number;
}

export interface LendPosition {
  asset: LendAsset;
  depositedAmount: number;
  apy: number;
  earnedInterest: number;
}

export interface BorrowPosition {
  collateralAsset: LendAsset;
  debtAsset: LendAsset;
  collateralAmount: number;
  debtAmount: number;
  ltv: number;
  liquidationThreshold: number;
  healthFactor: number;
}

export interface LendQuote {
  asset: LendAsset;
  amount: number;
  estimatedApy: number;
  operation: 'deposit' | 'withdraw' | 'borrow' | 'repay';
}

/** Info shown on lend/withdraw_lend action cards */
export interface LendEarnInfo {
  jlSymbol: string;          // e.g. "jlUSDC"
  assetSymbol: string;       // e.g. "USDC"
  supplyApy: number;         // e.g. 2.57 (%)
  rewardsApy: number;        // e.g. 1.52 (%)
  totalApy: number;          // e.g. 4.09 (%)
  jlTokensPerAsset: number;  // e.g. 0.9657  (1 USDC → 0.9657 jlUSDC)
  assetsPerJlToken: number;  // e.g. 1.0355  (1 jlUSDC → 1.0355 USDC)
  // User position (filled when walletAddress provided)
  userJlBalance?: number;
  userDepositedAssets?: number;
  userEarnings?: number;
}

/** Info shown on borrow/repay action cards */
export interface LendBorrowInfo {
  debtSymbol: string;
  collateralSymbol: string;
  collateralMint: string;
  collateralLogo: string;        // Jupiter's authoritative token logo URL (may be '')
  collateralPrice: number;       // USD price of collateral
  debtPrice: number;             // USD price of debt token
  borrowApy: number;              // e.g. 3.89 (%)
  maxLtv: number;                 // e.g. 0.75 (75%)
  liquidationThreshold: number;  // e.g. 0.80 (80%)
  liquidationPenalty: number;    // e.g. 0.10 (10%)
  availableLiquidity: number;    // human-readable (e.g. 14167150)
  minimumBorrow: number;         // human-readable
  vaultId: number;
}

export type LendActionInfo =
  | { kind: 'earn';   data: LendEarnInfo }
  | { kind: 'borrow'; data: LendBorrowInfo[] };

// ─── Service ────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class JupiterLendService {
  private readonly walletService = inject(WalletService);

  private _connection: Connection | null = null;
  private get connection(): Connection {
    // Route through the shared factory so the /api/rpc proxy receives the auth
    // cookie (credentials: 'include' via fetchMiddleware). A bare Connection
    // omits it → the gateway's RequireWallet on /rpc returns 401 and the
    // borrow SDK fails with "failed to get info about account …: 401" or
    // "failed to get recent blockhash: 401". Memoized to a single instance so
    // every call in a build/submit flow shares the same credentialed transport.
    return (this._connection ??= createSolanaConnection('confirmed'));
  }

  /** Return the list of supported lending assets (static seed — for display
   *  fallbacks only; the authoritative set comes from loadLiveAssets()). */
  getSupportedAssets(): LendAsset[] {
    return LEND_SUPPORTED_ASSETS;
  }

  /** In-memory cache of the LIVE earn-asset list. */
  private liveAssets: LendAsset[] | null = null;
  private liveAssetsAt = 0;

  /** Fetch the live Jupiter Lend earn-asset list (cached ~5 min). Jupiter adds
   *  assets (WSOL/USDG/USDS/JupUSD…) over time, so this — not the static seed —
   *  is the source of truth. Falls back to the seed only if the API is down. */
  private async loadLiveAssets(): Promise<LendAsset[]> {
    const FRESH_MS = 5 * 60 * 1000;
    if (this.liveAssets && Date.now() - this.liveAssetsAt < FRESH_MS) return this.liveAssets;
    try {
      const res = await jupFetch(`${LEND_API}/v1/earn/tokens`);
      if (res.ok) {
        const tokens: any[] = await res.json();
        const assets: LendAsset[] = tokens
          .map(t => t.asset)
          .filter(a => a?.address)
          .map(a => ({
            symbol: (a.uiSymbol || a.symbol || '').toString(),
            mint: a.address as string,
            decimals: typeof a.decimals === 'number' ? a.decimals : 6,
          }));
        if (assets.length) {
          this.liveAssets = assets;
          this.liveAssetsAt = Date.now();
        }
      }
    } catch {
      /* network failure — fall through to the seed list */
    }
    return this.liveAssets ?? LEND_SUPPORTED_ASSETS;
  }

  /** Resolve a lend asset by symbol or mint against the LIVE supported list.
   *  Treats SOL and WSOL as equivalent (the API lists native SOL as WSOL). */
  async resolveAsset(symbolOrMint: string): Promise<LendAsset | undefined> {
    const assets = await this.loadLiveAssets();
    const q = symbolOrMint.trim();
    const upper = q.toUpperCase();
    const wantSol = upper === 'SOL' || upper === 'WSOL';
    return assets.find(
      a =>
        a.symbol.toUpperCase() === upper ||
        a.mint === q ||
        (wantSol && (a.symbol.toUpperCase() === 'SOL' || a.symbol.toUpperCase() === 'WSOL')),
    );
  }

  // ─── Market Data API ────────────────────────────────────────────────────

  /** Fetch earn token market data for a given underlying asset symbol or mint. */
  async getEarnInfo(symbolOrMint: string, walletAddress?: string): Promise<LendEarnInfo | null> {
    try {
      const res = await jupFetch(`${LEND_API}/v1/earn/tokens`);
      if (!res.ok) return null;
      const tokens: any[] = await res.json();
      const upper = symbolOrMint.toUpperCase();
      // The API lists native SOL as WSOL (asset.symbol="WSOL", uiSymbol="SOL"),
      // so match symbol OR uiSymbol OR mint, with SOL<->WSOL treated as equal —
      // otherwise "lend SOL" leaves the rate stuck on "Loading rate...".
      const wantSol = upper === 'SOL' || upper === 'WSOL';
      const t = tokens.find(x => {
        const sym = (x.asset?.symbol ?? '').toUpperCase();
        const ui = (x.asset?.uiSymbol ?? '').toUpperCase();
        return (
          sym === upper ||
          ui === upper ||
          x.asset?.address === symbolOrMint ||
          (wantSol && (sym === 'SOL' || sym === 'WSOL' || ui === 'SOL' || ui === 'WSOL'))
        );
      });
      if (!t) return null;

      const decimals = t.asset?.decimals ?? 6;
      const info: LendEarnInfo = {
        jlSymbol:          t.symbol ?? '',
        assetSymbol:       t.asset?.uiSymbol ?? t.asset?.symbol ?? upper,
        supplyApy:         (t.supplyRate ?? 0) / 100,
        rewardsApy:        ((t.totalRate ?? 0) - (t.supplyRate ?? 0)) / 100,
        totalApy:          (t.totalRate ?? 0) / 100,
        jlTokensPerAsset:  (t.convertToShares ?? 0) / Math.pow(10, decimals),
        assetsPerJlToken:  (t.convertToAssets ?? 0) / Math.pow(10, decimals),
      };

      if (walletAddress) {
        try {
          const posRes = await jupFetch(`${LEND_API}/v1/earn/positions?users[]=${walletAddress}`);
          if (posRes.ok) {
            const positions: any[] = await posRes.json();
            const pos = positions.find(p => p.token?.asset?.address === t.asset?.address);
            if (pos) {
              info.userJlBalance      = parseFloat(pos.shares ?? '0') / Math.pow(10, decimals);
              info.userDepositedAssets = parseFloat(pos.underlyingAssets ?? '0') / Math.pow(10, decimals);
            }
          }
          const earnRes = await jupFetch(`${LEND_API}/v1/earn/earnings?user=${walletAddress}&positions[]=${t.address}`);
          if (earnRes.ok) {
            const earnings: any[] = await earnRes.json();
            const e = earnings[0];
            if (e) info.userEarnings = parseFloat(e.earnings ?? '0') / Math.pow(10, decimals);
          }
        } catch { /* ignore */ }
      }
      return info;
    } catch {
      return null;
    }
  }

  /** Fetch borrow vault data for a given debt token (symbol or mint). */
  async getBorrowInfo(debtSymbolOrMint: string): Promise<LendBorrowInfo[]> {
    // Jupiter's lite-api is occasionally flaky (a request hangs, then recovers).
    // Retry once on timeout/failure before giving up so a single stall doesn't
    // leave the borrow card empty.
    let vaults: any[] | null = null;
    for (let attempt = 0; attempt < 2 && vaults === null; attempt++) {
      try {
        const res = await jupFetch(`${LEND_API}/v1/borrow/vaults`);
        if (res.ok) vaults = await res.json();
      } catch {
        /* timeout/network — retry once, then fall through */
      }
    }
    if (!vaults) return [];
    try {
      const upper = debtSymbolOrMint.toUpperCase();
      return vaults
        .filter(v =>
          v.borrowToken?.symbol?.toUpperCase() === upper ||
          v.borrowToken?.uiSymbol?.toUpperCase() === upper ||
          v.borrowToken?.address === debtSymbolOrMint
        )
        .map(v => {
          const dec = v.borrowToken?.decimals ?? 6;
          return {
            vaultId:               v.id,
            debtSymbol:            v.borrowToken?.uiSymbol ?? v.borrowToken?.symbol ?? '',
            collateralSymbol:      v.supplyToken?.uiSymbol ?? v.supplyToken?.symbol ?? '',
            collateralMint:        v.supplyToken?.address ?? '',
            collateralLogo:        v.supplyToken?.logoUrl ?? '',
            collateralPrice:       parseFloat(v.supplyToken?.price ?? '0'),
            debtPrice:             parseFloat(v.borrowToken?.price ?? '1'),
            borrowApy:             (v.borrowRate ?? 0) / 100,
            maxLtv:                (v.collateralFactor ?? 0) / 1000,
            liquidationThreshold:  (v.liquidationThreshold ?? 0) / 1000,
            liquidationPenalty:    (v.liquidationPenalty ?? 0) / 1000,
            availableLiquidity:    parseFloat(v.borrowable ?? '0') / Math.pow(10, dec),
            minimumBorrow:         parseFloat(v.minimumBorrowing ?? '0') / Math.pow(10, dec),
          } as LendBorrowInfo;
        });
    } catch {
      return [];
    }
  }

  /** Fetch all earn (deposit) positions for a wallet. */
  async getAllEarnPositions(walletAddress: string): Promise<LendPosition[]> {
    try {
      const [posRes, tokensRes] = await Promise.all([
        jupFetch(`${LEND_API}/v1/earn/positions?users[]=${walletAddress}`),
        jupFetch(`${LEND_API}/v1/earn/tokens`),
      ]);
      if (!posRes.ok) return [];
      const positions: any[] = await posRes.json();
      const tokens: any[] = tokensRes.ok ? await tokensRes.json() : [];

      return positions
        .filter(p => parseFloat(p.shares ?? '0') > 0)
        .map(p => {
          const tokenMeta = tokens.find(t => t.address === p.token?.address);
          const decimals = p.token?.asset?.decimals ?? tokenMeta?.asset?.decimals ?? 6;
          const assetSymbol = p.token?.asset?.symbol ?? tokenMeta?.asset?.symbol ?? '?';
          const assetMint = p.token?.asset?.address ?? tokenMeta?.asset?.address ?? '';
          const asset: LendAsset = LEND_SUPPORTED_ASSETS.find(a => a.mint === assetMint) ?? {
            symbol: assetSymbol, mint: assetMint, decimals,
          };
          const depositedAmount = parseFloat(p.underlyingAssets ?? '0') / Math.pow(10, decimals);
          const apy = (tokenMeta?.totalRate ?? 0) / 100;
          return { asset, depositedAmount, apy, earnedInterest: 0 } as LendPosition;
        })
        .filter(p => p.depositedAmount > 0);
    } catch {
      return [];
    }
  }

  /** Fetch all borrow positions for a wallet. */
  async getBorrowPositions(walletAddress: string): Promise<BorrowPosition[]> {
    try {
      const res = await jupFetch(`${LEND_API}/v1/borrow/positions?users[]=${walletAddress}`);
      if (!res.ok) return [];
      const positions: any[] = await res.json();

      return positions
        .filter(p => parseFloat(p.debtAmount ?? '0') > 0)
        .map(p => {
          const colMint: string = p.collateralToken?.address ?? '';
          const debtMint: string = p.borrowToken?.address ?? '';
          const colAsset: LendAsset = LEND_SUPPORTED_ASSETS.find(a => a.mint === colMint)
            ?? { symbol: p.collateralToken?.uiSymbol ?? p.collateralToken?.symbol ?? '?', mint: colMint, decimals: 9 };
          const debtAsset: LendAsset = LEND_SUPPORTED_ASSETS.find(a => a.mint === debtMint)
            ?? { symbol: p.borrowToken?.uiSymbol ?? p.borrowToken?.symbol ?? '?', mint: debtMint, decimals: 6 };

          const collateralAmount = parseFloat(p.collateralAmount ?? '0') / Math.pow(10, colAsset.decimals);
          const debtAmount = parseFloat(p.debtAmount ?? '0') / Math.pow(10, debtAsset.decimals);
          const ltv = parseFloat(p.ltv ?? '0');
          const liqThresh = (p.liquidationThreshold ?? 0) / 1000;
          const healthFactor = liqThresh > 0 && ltv > 0 ? liqThresh / ltv : 999;

          return { collateralAsset: colAsset, debtAsset, collateralAmount, debtAmount, ltv, liquidationThreshold: liqThresh, healthFactor } as BorrowPosition;
        });
    } catch {
      return [];
    }
  }

  /** Find asset metadata by symbol or mint address. */
  findAsset(symbolOrMint: string): LendAsset | undefined {
    const upper = symbolOrMint.toUpperCase();
    return LEND_SUPPORTED_ASSETS.find(
      (a) => a.symbol.toUpperCase() === upper || a.mint === symbolOrMint
    );
  }

  // ─── Earn: Deposit ──────────────────────────────────────────────────────

  /**
   * Build a deposit (earn) transaction using @jup-ag/lend SDK.
   *
   * @param asset   The asset to deposit (from LEND_SUPPORTED_ASSETS).
   * @param amount  Human-readable amount (e.g. 100 for 100 USDC).
   */
  async buildDepositTransaction(
    asset: LendAsset,
    amount: number
  ): Promise<{ transaction: Transaction; description: string }> {
    const walletPubkey = this.walletService.publicKey();
    if (!walletPubkey) throw new Error('No wallet connected');

    const user       = new PublicKey(walletPubkey);
    const assetMint  = new PublicKey(asset.mint);
    const connection = this.connection;

    const amountLamports = new BN(Math.floor(amount * 10 ** asset.decimals).toString());
    const isNativeSol = asset.mint === NATIVE_MINT.toBase58();

    const { ixs } = await getDepositIxs({
      amount: amountLamports,
      asset: assetMint,
      signer: user,
      connection,
    });

    const tx = new Transaction();
    const { blockhash } = await connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash;
    tx.feePayer = user;

    // getDepositIxs creates only the OUTPUT jlToken ATA; it neither creates the
    // input WSOL account nor wraps SOL. For native SOL we must wrap first —
    // create the WSOL ATA (idempotent), fund it with exactly the deposit
    // amount, and syncNative — or the deposit references an uninitialized WSOL
    // account and simulation fails with Anchor 3012 (AccountNotInitialized).
    if (isNativeSol) {
      const wsolAta = getAssociatedTokenAddressSync(NATIVE_MINT, user);
      tx.add(
        createAssociatedTokenAccountIdempotentInstruction(user, wsolAta, user, NATIVE_MINT),
        SystemProgram.transfer({
          fromPubkey: user,
          toPubkey: wsolAta,
          lamports: BigInt(amountLamports.toString()),
        }),
        createSyncNativeInstruction(wsolAta),
      );
    }

    tx.add(...ixs);

    // The deposit drained the wrapped balance; close the WSOL ATA to reclaim
    // its rent back to the user as native SOL.
    if (isNativeSol) {
      const wsolAta = getAssociatedTokenAddressSync(NATIVE_MINT, user);
      tx.add(createCloseAccountInstruction(wsolAta, user, user));
    }

    return {
      transaction: tx,
      description: `Deposit ${amount} ${asset.symbol} into Jupiter Lend Earn`,
    };
  }

  // ─── Earn: Withdraw ─────────────────────────────────────────────────────

  /**
   * Build a withdraw (earn) transaction using @jup-ag/lend SDK.
   *
   * @param asset  The asset to withdraw.
   * @param amount Human-readable amount to withdraw.
   */
  async buildWithdrawTransaction(
    asset: LendAsset,
    amount: number
  ): Promise<{ transaction: Transaction; description: string }> {
    const walletPubkey = this.walletService.publicKey();
    if (!walletPubkey) throw new Error('No wallet connected');

    const user       = new PublicKey(walletPubkey);
    const assetMint  = new PublicKey(asset.mint);
    const connection = this.connection;

    const amountLamports = new BN(Math.floor(amount * 10 ** asset.decimals).toString());
    const isNativeSol = asset.mint === NATIVE_MINT.toBase58();

    const { ixs } = await getWithdrawIxs({
      amount: amountLamports,
      asset: assetMint,
      signer: user,
      connection,
    });

    const tx = new Transaction();
    const { blockhash } = await connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash;
    tx.feePayer = user;
    tx.add(...ixs);

    // getWithdrawIxs returns the underlying to the user's WSOL ATA. For native
    // SOL, close it afterwards so the user receives spendable SOL, not WSOL.
    if (isNativeSol) {
      const wsolAta = getAssociatedTokenAddressSync(NATIVE_MINT, user);
      tx.add(createCloseAccountInstruction(wsolAta, user, user));
    }

    return {
      transaction: tx,
      description: `Withdraw ${amount} ${asset.symbol} from Jupiter Lend Earn`,
    };
  }

  // ─── Borrow: Operate ────────────────────────────────────────────────────

  /**
   * Resolve the SPL token program that owns a mint (standard Token vs
   * Token-2022) so ATAs are derived/created with the same program the vault
   * uses. Falls back to the classic Token program if the mint can't be read.
   */
  private async getTokenProgram(mint: PublicKey): Promise<PublicKey> {
    if (mint.equals(NATIVE_MINT)) return TOKEN_PROGRAM_ID;
    try {
      const info = await this.connection.getAccountInfo(mint);
      if (info?.owner.equals(TOKEN_2022_PROGRAM_ID)) return TOKEN_2022_PROGRAM_ID;
    } catch {
      /* fall through to classic Token program */
    }
    return TOKEN_PROGRAM_ID;
  }

  // Collateral setups completed this session, keyed by `${wallet}:${vaultId}:${colAmount}`,
  // so a retry after a failed operate doesn't wrap the collateral a second time.
  private readonly _setupCache = new Set<string>();

  /**
   * Jupiter's operate tx assumes the user's collateral (supply) and debt
   * (borrow) token accounts already exist, and — for native SOL — that the WSOL
   * account is funded with the collateral. A first-time borrower has none of
   * that, so pre-flight sim fails with 3012 (signer_supply_token_account). Do
   * the setup in its OWN lightweight, wallet-previewable transaction: create
   * both ATAs (idempotent) and, for SOL collateral, wrap the deposit amount.
   * Skips entirely when nothing is needed (non-SOL collateral, both ATAs exist).
   */
  private async ensureBorrowSetup(
    vaultId: number,
    user: PublicKey,
    colAsset: LendAsset,
    debtAsset: LendAsset,
    colAmount: number,
    hooks?: { onSetupSign?: () => void; onProgress?: () => void },
  ): Promise<void> {
    if (colAmount <= 0) return; // only collateral deposits need funding/setup

    const colMint = new PublicKey(colAsset.mint);
    const debtMint = new PublicKey(debtAsset.mint);
    const colIsNativeSol = colAsset.mint === NATIVE_MINT.toBase58();
    const [colProgram, debtProgram] = await Promise.all([
      this.getTokenProgram(colMint),
      this.getTokenProgram(debtMint),
    ]);
    const supplyAta = getAssociatedTokenAddressSync(colMint, user, true, colProgram);
    const debtAta = getAssociatedTokenAddressSync(debtMint, user, true, debtProgram);
    const colLamports = BigInt(Math.round(colAmount * 10 ** colAsset.decimals));

    // Read the ACTUAL on-chain precondition via the reliable Helius proxy
    // (this.connection), not a signature lookup: does the debt ATA exist, and —
    // for native SOL — does the WSOL account already hold enough collateral?
    // This drives both the "already funded, skip approval" fast path and the
    // post-submit confirmation, so a wrap that landed but was slow to confirm
    // isn't re-done and the flow can proceed straight to the operate step.
    const readState = async (): Promise<{ debtExists: boolean; supplyExists: boolean; wsolBal: bigint }> => {
      const [supplyInfo, debtInfo] = await Promise.all([
        this.connection.getAccountInfo(supplyAta).catch(() => null),
        this.connection.getAccountInfo(debtAta).catch(() => null),
      ]);
      let wsolBal = 0n;
      if (colIsNativeSol && supplyInfo) {
        try { wsolBal = BigInt((await this.connection.getTokenAccountBalance(supplyAta)).value.amount); }
        catch { wsolBal = 0n; }
      }
      return { debtExists: !!debtInfo, supplyExists: !!supplyInfo, wsolBal };
    };
    const isReady = (s: { debtExists: boolean; supplyExists: boolean; wsolBal: bigint }): boolean =>
      s.debtExists && (colIsNativeSol ? s.wsolBal >= colLamports : s.supplyExists);

    // Fast path: SPL ATAs persist, so cache "both exist". Native SOL is never
    // cached — the wrapped WSOL is consumed into the vault by operate.
    const cacheKey = `${user.toBase58()}:${vaultId}`;
    if (!colIsNativeSol && this._setupCache.has(cacheKey)) return;

    const state = await readState();
    if (isReady(state)) { if (!colIsNativeSol) this._setupCache.add(cacheKey); return; }

    // Build a LEGACY transaction — exactly like the working deposit/withdraw
    // flows. A v0 tx with NO address lookup tables is an unusual shape that
    // Phantom's balance-change preview simulator mishandles (it hangs on grey
    // skeletons with Approve disabled); legacy txs and v0-with-ALT (swaps)
    // both preview fine. This setup tx is tiny, so legacy stays well under
    // the 1232-byte limit. For native SOL, wrap only the MISSING amount so a
    // partially-funded WSOL account (e.g. from a prior attempt) isn't stacked.
    const wrapLamports = colIsNativeSol && colLamports > state.wsolBal ? colLamports - state.wsolBal : 0n;
    const tx = new Transaction();
    const { blockhash } = await this.connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash;
    tx.feePayer = user;
    tx.add(
      ComputeBudgetProgram.setComputeUnitLimit({ units: 120_000 }),
      ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 50_000 }),
      createAssociatedTokenAccountIdempotentInstruction(user, supplyAta, user, colMint, colProgram),
    );
    if (wrapLamports > 0n) {
      tx.add(
        SystemProgram.transfer({ fromPubkey: user, toPubkey: supplyAta, lamports: wrapLamports }),
        createSyncNativeInstruction(supplyAta),
      );
    }
    tx.add(createAssociatedTokenAccountIdempotentInstruction(user, debtAta, user, debtMint, debtProgram));

    hooks?.onSetupSign?.();                      // card → "signing"; also resets its stall timer
    await this.signAndSubmit(tx);               // approval #1 — trivial, wallet previews instantly

    // Confirm by polling the ACTUAL precondition state via Helius — reliable,
    // and it doesn't matter which RPC the wallet broadcast through. Must be
    // ready before the operate tx references these accounts.
    const start = Date.now();
    while (Date.now() - start < 60_000) {
      hooks?.onProgress?.();                     // keep the card's stall timer alive
      await new Promise(r => setTimeout(r, 2500));
      try { if (isReady(await readState())) { if (!colIsNativeSol) this._setupCache.add(cacheKey); return; } }
      catch { /* transient RPC error — keep polling */ }
    }
    throw new Error('Collateral setup did not confirm in time. Please try again.');
  }

  /**
   * Build a borrow-vault operation transaction using @jup-ag/lend/borrow SDK.
   *
   * Supports:
   * - Borrow: colAmount > 0, debtAmount > 0
   * - Repay: colAmount = 0, debtAmount < 0
   * - Add collateral: colAmount > 0, debtAmount = 0
   * - Withdraw collateral: colAmount < 0, debtAmount = 0
   * - Full repay: debtAmount = MAX_REPAY_AMOUNT (-1 as BN)
   * - Max withdraw: colAmount = MAX_WITHDRAW_AMOUNT (-2 as BN)
   *
   * @param vaultId - The vault ID from getBorrowInfo(). If not provided, auto-selects best vault.
   * @param positionId - Position ID (0 for new position, or existing position ID)
   * @param colAmount - Collateral amount change (positive = add, negative = withdraw, 0 = no change)
   * @param debtAmount - Debt amount change (positive = borrow, negative = repay)
   * @param colAsset - Collateral asset
   * @param debtAsset - Debt asset
   */
  async buildBorrowOperateTransaction(
    vaultId: number = -1,  // -1 means auto-select best vault
    positionId: number = 0,
    colAmount: number,
    debtAmount: number,
    colAsset: LendAsset,
    debtAsset: LendAsset,
    hooks?: { onSetupSign?: () => void; onProgress?: () => void },
  ): Promise<{ transaction: VersionedTransaction; description: string }> {
    const walletPubkey = this.walletService.publicKey();
    if (!walletPubkey) throw new Error('No wallet connected');

    const user = new PublicKey(walletPubkey);

    // Auto-select best vault if not provided. CRITICAL: a Jupiter Lend borrow
    // vault pairs a SPECIFIC collateral with the debt token, and the operate ix
    // references that collateral's token account. Selecting purely by rate can
    // land on a different collateral's vault than the user picked, leaving
    // signer_supply_token_account uninitialized (3012). So filter to vaults
    // whose collateral matches colAsset first, then pick the cheapest of those.
    if (vaultId <= 0) {
      const vaults = await this.getBorrowInfo(debtAsset.symbol);
      if (vaults.length === 0) {
        throw new Error(`No borrow vault found for ${debtAsset.symbol}`);
      }
      const colUpper = colAsset.symbol.toUpperCase();
      const matching = vaults.filter(v =>
        v.collateralMint === colAsset.mint ||
        v.collateralSymbol.toUpperCase() === colUpper ||
        (colAsset.mint === NATIVE_MINT.toBase58() && v.collateralSymbol.toUpperCase() === 'SOL')
      );
      const pool = matching.length > 0 ? matching : vaults;
      pool.sort((a, b) => a.borrowApy - b.borrowApy);
      vaultId = pool[0].vaultId;
    }

    // Jupiter's operate tx assumes the collateral/debt token accounts exist (and
    // for SOL, that WSOL is funded). Create/fund them in a separate lightweight
    // tx first — folding them into the operate tx makes it ~9 heavy instructions
    // the wallet can't preview. Skips when nothing is needed.
    await this.ensureBorrowSetup(vaultId, user, colAsset, debtAsset, colAmount, hooks);
    hooks?.onProgress?.();  // setup done — keep the card's stall timer alive into the operate build

    // Build the operate transaction via Jupiter's OFFICIAL borrow API. Its
    // backend returns a lean, wallet-previewable v0 tx — ComputeBudget + init
    // position + operate, ~3 instructions, ~866 bytes — that Phantom simulates
    // and approves fine. This is the same endpoint jup.ag/lend/borrow uses
    // (Client.borrow.operate).
    const amtStr = (amt: number, decimals: number, maxNegSentinel: BN): string => {
      // -1 = MAX_REPAY, -2 = MAX_WITHDRAW app-level sentinels → program's max value.
      if (amt === -1 || amt === -2) return maxNegSentinel.toString();
      return Math.round(amt * 10 ** decimals).toString(); // sign-preserving (borrow/add +, repay/withdraw -)
    };
    const colStr = amtStr(colAmount, colAsset.decimals, MAX_WITHDRAW_AMOUNT);
    const debtStr = amtStr(debtAmount, debtAsset.decimals, MAX_REPAY_AMOUNT);

    const opRes = await jupFetch(`${LEND_API}/v1/borrow/operate`, 12_000, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signer: user.toBase58(), vaultId, positionId, colAmount: colStr, debtAmount: debtStr }),
    });
    if (!opRes.ok) throw new Error('Jupiter borrow service is unavailable right now. Please try again.');
    const opData = await opRes.json() as { nftId?: number; transaction?: string };
    if (!opData.transaction) throw new Error('Jupiter borrow service returned no transaction.');
    const tx = VersionedTransaction.deserialize(
      Uint8Array.from(atob(opData.transaction), c => c.charCodeAt(0)),
    );

    // Build description
    let description: string;
    if (debtAmount > 0 && colAmount > 0) {
      description = `Borrow ${debtAmount} ${debtAsset.symbol}, deposit ${colAmount} ${colAsset.symbol} collateral`;
    } else if (debtAmount > 0) {
      description = `Borrow ${debtAmount} ${debtAsset.symbol}`;
    } else if (debtAmount < 0 && colAmount === 0) {
      description = `Repay ${Math.abs(debtAmount)} ${debtAsset.symbol}`;
    } else if (debtAmount < 0) {
      description = `Repay ${Math.abs(debtAmount)} ${debtAsset.symbol}, withdraw ${Math.abs(colAmount)} ${colAsset.symbol}`;
    } else if (colAmount > 0) {
      description = `Add ${colAmount} ${colAsset.symbol} collateral`;
    } else if (colAmount < 0) {
      description = `Withdraw ${Math.abs(colAmount)} ${colAsset.symbol} collateral`;
    } else {
      description = 'Manage collateral';
    }

    return { transaction: tx, description };
  }

  // ─── Sign & Submit ───────────────────────────────────────────────────────

  async signAndSubmit(transaction: Transaction | VersionedTransaction): Promise<string> {
    // A hung wallet dialog must not leave the card in "waiting for wallet
    // signature" forever — race every wallet call against a timeout so it
    // surfaces a retryable error instead.
    const SIGN_TIMEOUT_MS = 60_000;
    const withTimeout = <T>(p: Promise<T>): Promise<T> =>
      Promise.race([
        p,
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Wallet signing timed out. Please try again.')), SIGN_TIMEOUT_MS),
        ),
      ]);

    // Try the wallet's native signAndSendTransaction first (it resolves the
    // ALTs and broadcasts via the wallet's own RPC — no dependency on our
    // authenticated /api/rpc proxy). Fall back to sign + broadcast when the
    // wallet lacks it.
    try {
      const directSig = await withTimeout(
        this.walletService.signAndSendTransaction(transaction, { skipPreflight: true }),
      );
      if (directSig) return directSig;
    } catch (e: any) {
      const msg = e?.message ?? '';
      if (/reject|denied|cancel|declined|user refused|user rejected|timed out/i.test(msg)) {
        throw e;
      }
      // Any other failure → fall through to the manual sign + send path.
    }

    const signed = await withTimeout(
      this.walletService.signTransaction(transaction as Transaction),
    );
    const raw = (signed as Transaction | VersionedTransaction).serialize();

    // Broadcasting a fully-signed tx needs no auth (any RPC accepts it), so send
    // through a PUBLIC endpoint rather than the auth-gated /api/rpc proxy — the
    // cookie handshake there was intermittently 401'ing the submit. Fall back to
    // the proxy connection only if the public endpoint is unreachable.
    let signature: string;
    try {
      signature = await this.broadcastRaw(raw);
    } catch (err: any) {
      const m = err?.message ?? String(err ?? '');
      if (/blockhash not found|block height exceeded/i.test(m)) {
        throw new Error('BLOCKHASH_EXPIRED');
      }
      throw err;
    }

    this.connection
      .getLatestBlockhash()
      .then(({ blockhash, lastValidBlockHeight }) =>
        this.connection.confirmTransaction(
          { signature, blockhash, lastValidBlockHeight },
          'confirmed'
        )
      )
      .catch(() => {});

    return signature;
  }

  /**
   * Broadcast a signed transaction through a public RPC (no auth) so the submit
   * never depends on the /api/rpc proxy's cookie handshake. Tries the public
   * mainnet endpoint first, then the proxy connection as a last resort.
   */
  private async broadcastRaw(raw: Uint8Array): Promise<string> {
    const publicConn = new Connection('https://api.mainnet-beta.solana.com', 'confirmed');
    try {
      return await publicConn.sendRawTransaction(raw, { skipPreflight: true, preflightCommitment: 'confirmed' });
    } catch (e: any) {
      const m = e?.message ?? String(e ?? '');
      // A real on-chain/blockhash error should surface, not trigger a proxy retry.
      if (/blockhash not found|block height exceeded|custom program error|insufficient/i.test(m)) throw e;
      return await this.connection.sendRawTransaction(raw, { skipPreflight: true, preflightCommitment: 'confirmed' });
    }
  }
}
