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
  Connection,
  PublicKey,
  SystemProgram,
  Transaction,
} from '@solana/web3.js';
import {
  NATIVE_MINT,
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountIdempotentInstruction,
  createSyncNativeInstruction,
  createCloseAccountInstruction,
} from '@solana/spl-token';
import BN from 'bn.js';
import { getDepositIxs, getWithdrawIxs } from '@jup-ag/lend/earn';
import { getOperateIx, getVaultsProgram, MAX_REPAY_AMOUNT, MAX_WITHDRAW_AMOUNT } from '@jup-ag/lend/borrow';
import { WalletService } from '@core/services/wallet.service';
import { environment } from '../../../../environments/environment';

const LEND_API = 'https://lite-api.jup.ag/lend';

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

  private get connection(): Connection {
    return new Connection(environment.solanaRpc, { commitment: 'confirmed', httpHeaders: { 'X-Requested-With': 'XMLHttpRequest' } });
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
      const res = await fetch(`${LEND_API}/v1/earn/tokens`);
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
      const res = await fetch(`${LEND_API}/v1/earn/tokens`);
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
          const posRes = await fetch(`${LEND_API}/v1/earn/positions?users[]=${walletAddress}`);
          if (posRes.ok) {
            const positions: any[] = await posRes.json();
            const pos = positions.find(p => p.token?.asset?.address === t.asset?.address);
            if (pos) {
              info.userJlBalance      = parseFloat(pos.shares ?? '0') / Math.pow(10, decimals);
              info.userDepositedAssets = parseFloat(pos.underlyingAssets ?? '0') / Math.pow(10, decimals);
            }
          }
          const earnRes = await fetch(`${LEND_API}/v1/earn/earnings?user=${walletAddress}&positions[]=${t.address}`);
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
    try {
      const res = await fetch(`${LEND_API}/v1/borrow/vaults`);
      if (!res.ok) return [];
      const vaults: any[] = await res.json();
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
        fetch(`${LEND_API}/v1/earn/positions?users[]=${walletAddress}`),
        fetch(`${LEND_API}/v1/earn/tokens`),
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
      const res = await fetch(`${LEND_API}/v1/borrow/positions?users[]=${walletAddress}`);
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
    debtAsset: LendAsset
  ): Promise<{ transaction: Transaction; description: string }> {
    const walletPubkey = this.walletService.publicKey();
    if (!walletPubkey) throw new Error('No wallet connected');

    const user = new PublicKey(walletPubkey);

    // Auto-select best vault if not provided
    if (vaultId <= 0) {
      const vaults = await this.getBorrowInfo(debtAsset.symbol);
      if (vaults.length === 0) {
        throw new Error(`No borrow vault found for ${debtAsset.symbol}`);
      }
      // Select vault with best borrow rate (lowest)
      vaults.sort((a, b) => a.borrowApy - b.borrowApy);
      vaultId = vaults[0].vaultId;
    }

    // Convert amounts to lamports
    const colLamports = new BN(Math.floor(Math.abs(colAmount) * 10 ** colAsset.decimals).toString());
    const debtLamports = new BN(Math.floor(Math.abs(debtAmount) * 10 ** debtAsset.decimals).toString());

    // Handle special values
    let colBN: BN;
    let debtBN: BN;

    if (colAmount < 0 && colAmount === -2) {
      // MAX_WITHDRAW_AMOUNT
      colBN = MAX_WITHDRAW_AMOUNT;
      debtBN = debtLamports;
    } else if (debtAmount < 0 && debtAmount === -1) {
      // MAX_REPAY_AMOUNT (repay all debt)
      colBN = colLamports;
      debtBN = MAX_REPAY_AMOUNT;
    } else {
      colBN = colLamports;
      debtBN = debtLamports;
    }

    // Initialize program (needed for side effects)
    await getVaultsProgram({
      connection: this.connection,
      signer: user,
    });

    // Build the operate instruction
    const operateResult = await getOperateIx({
      vaultId,
      positionId,
      colAmount: colBN,
      debtAmount: debtBN,
      connection: this.connection,
      signer: user,
    });
    const ixs = Array.isArray(operateResult) ? operateResult : operateResult.ixs;

    const tx = new Transaction();
    const { blockhash } = await this.connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash;
    tx.feePayer = user;
    tx.add(...ixs);

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

  async signAndSubmit(transaction: Transaction): Promise<string> {
    const signed = await this.walletService.signTransaction(transaction);
    const signature = await this.connection.sendRawTransaction(
      (signed as Transaction).serialize(),
      { skipPreflight: false, preflightCommitment: 'confirmed' }
    );

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
}
