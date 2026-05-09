import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Kamino Market (Lending Pool) info — from GET /v2/kamino-market */
export interface KaminoMarket {
  lendingMarket: string;   // public key (was: address)
  isPrimary: boolean;
  name: string;
  description: string;
  lookupTable: string;
  isCurated: boolean;
  // legacy alias
  get address(): string;
}

/** Kamino Reserve (Lending Asset) info — from GET /kamino-market/{pk}/reserves/metrics */
export interface KaminoReserve {
  reserve: string;             // reserve pubkey (was: address)
  market: string;              // parent market pubkey
  liquidityToken: string;      // symbol, e.g. "USDC"
  liquidityTokenMint: string;  // mint address (was: mint)
  maxLtv: string;              // decimal string, e.g. "0.80"
  borrowApy: string;           // decimal string e.g. "0.0489"
  supplyApy: string;           // decimal string
  totalSupply: string;         // human-readable token amount
  totalBorrow: string;
  totalSupplyUsd: number;
  totalBorrowUsd: string;
  // computed helpers
  get address(): string;
  get symbol(): string;
  get supplyApyNum(): number;
  get borrowApyNum(): number;
  get ltvNum(): number;
}

/** Kamino leverage pair metrics — from GET /kamino-market/{pk}/leverage/metrics */
export interface KaminoLeverageMetrics {
  depositReserve: string;
  borrowReserve: string;
  tag: string;           // "Leverage" | "Multiply" | etc.
  tvl: string;
  avgLeverage: string;
  totalBorrowed: string;
  totalBorrowedUsd: string;
  totalDepositedUsd: string;
  totalObligations: string;
  updatedOn: string;
}

/** Kamino Obligation (User Position) — from GET /kamino-market/{mk}/users/{user}/obligations */
export interface KaminoObligation {
  obligationAddress: string;
  market: string;
  owner: string;
  depositedValue: string;   // USD string
  borrowedValue: string;    // USD string
  healthFactor: string;     // ratio string
  deposits: KaminoObligationDeposit[];
  borrows: KaminoObligationBorrow[];
}

/** User deposit in obligation */
export interface KaminoObligationDeposit {
  reserveAddress: string;
  mintAddress: string;
  symbol: string;
  amount: string;
  valueUsd: string;
}

/** User borrow in obligation */
export interface KaminoObligationBorrow {
  reserveAddress: string;
  mintAddress: string;
  symbol: string;
  amount: string;
  valueUsd: string;
}

/** kVault (automated liquidity vault) — from GET /kvaults/vaults */
export interface KaminoVault {
  address: string;
  tokenMint: string;
  tokenMintDecimals: number;
  sharesMint: string;
  tokenAvailable: string;
  sharesIssued: string;
  performanceFeeBps: number;
  managementFeeBps: number;
}

/** kVault APY metrics — from GET /kvaults/vaults/{pubkey}/metrics */
export interface KaminoVaultMetrics {
  address: string;
  apy: string;
  apy7d: string;
  apy24h: string;
  apy30d: string;
  tokenPrice: string;
  tokensAvailableUsd: string;
  tokensInvestedUsd: string;
  sharePrice: string;
  tokensPerShare: string;
  numberOfHolders: number;
  sharesIssued: string;
}

/** User position in a kVault — from GET /kvaults/users/{pubkey}/positions */
export interface KaminoVaultPosition {
  vaultAddress: string;
  tokenMint: string;
  shares: string;
  sharesUsd: string;
}

/** LST staking yield — from GET /v2/staking-yields */
export interface KaminoStakingYield {
  tokenMint: string;
  apy: string;   // decimal string e.g. "0.062"
}

/** Oracle price — from GET /oracles/prices */
export interface KaminoOraclePrice {
  mint: string;
  name: string;
  price: string;
}

/** Build response from backend */
export interface KaminoBuildResponse {
  preview: {
    id: string;
    type: string;
    description: string;
    estimatedFee: string;
    params: Record<string, unknown>;
    warnings: string[];
    requiresApproval: boolean;
  };
  transaction: string; // base64-encoded V0 versioned transaction
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const KAMINO_API = 'https://api.kamino.finance';

/** Main lending market pubkey (primary market on mainnet) */
export const KAMINO_MAIN_MARKET = '7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF';

// KMNO token mint
export const KMNO_MINT = 'KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class KaminoService {
  private readonly http = inject(HttpClient);

  // ─── Market & Reserve Queries ────────────────────────────────────────────────

  /** Fetch all Kamino lending markets. */
  async getMarkets(): Promise<KaminoMarket[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/v2/kamino-market`)
      );
      return (resp ?? []).map((m: any) => ({
        lendingMarket: m.lendingMarket,
        isPrimary: m.isPrimary,
        name: m.name ?? '',
        description: m.description ?? '',
        lookupTable: m.lookupTable ?? '',
        isCurated: m.isCurated ?? false,
        get address() { return m.lendingMarket; },
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino markets:', err);
      return [];
    }
  }

  /** Get reserve metrics for a specific market. */
  async getMarketReserves(marketAddress: string): Promise<KaminoReserve[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/kamino-market/${marketAddress}/reserves/metrics`)
      );
      return (resp ?? []).map((r: any) => ({
        reserve: r.reserve,
        market: marketAddress,
        liquidityToken: r.liquidityToken ?? '',
        liquidityTokenMint: r.liquidityTokenMint ?? '',
        maxLtv: r.maxLtv ?? '0',
        borrowApy: r.borrowApy ?? '0',
        supplyApy: r.supplyApy ?? '0',
        totalSupply: r.totalSupply ?? '0',
        totalBorrow: r.totalBorrow ?? '0',
        totalSupplyUsd: parseFloat(r.totalSupplyUsd ?? '0'),
        totalBorrowUsd: r.totalBorrowUsd ?? '0',
        get address() { return r.reserve; },
        get symbol() { return r.liquidityToken ?? ''; },
        get supplyApyNum() { return parseFloat(r.supplyApy ?? '0'); },
        get borrowApyNum() { return parseFloat(r.borrowApy ?? '0'); },
        get ltvNum() { return parseFloat(r.maxLtv ?? '0'); },
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino reserves:', err);
      return [];
    }
  }

  /** Get main-market reserves (convenience method). */
  async getMainMarketReserves(): Promise<KaminoReserve[]> {
    return this.getMarketReserves(KAMINO_MAIN_MARKET);
  }

  /** Get leverage pair metrics for a market. */
  async getLeverageMetrics(marketAddress = KAMINO_MAIN_MARKET): Promise<KaminoLeverageMetrics[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/kamino-market/${marketAddress}/leverage/metrics`)
      );
      return resp ?? [];
    } catch (err) {
      console.error('Failed to fetch Kamino leverage metrics:', err);
      return [];
    }
  }

  /** Get user obligations (lending positions) across a specific market. */
  async getObligations(walletAddress: string, marketAddress = KAMINO_MAIN_MARKET): Promise<KaminoObligation[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(
          `${KAMINO_API}/kamino-market/${marketAddress}/users/${walletAddress}/obligations`
        )
      );
      return (resp ?? []).map((o: any) => ({
        obligationAddress: o.obligationAddress ?? o.address ?? '',
        market: marketAddress,
        owner: walletAddress,
        depositedValue: o.depositedValue ?? o.userTotalDeposit ?? '0',
        borrowedValue: o.borrowedValue ?? o.userTotalBorrow ?? '0',
        healthFactor: o.healthFactor ?? '0',
        deposits: (o.deposits ?? o.userDeposits ?? []).map((d: any) => ({
          reserveAddress: d.reserveAddress ?? d.reserve ?? '',
          mintAddress: d.mintAddress ?? d.mint ?? '',
          symbol: d.symbol ?? '',
          amount: d.amount ?? '0',
          valueUsd: d.valueUsd ?? d.marketValueRefreshed ?? '0',
        })),
        borrows: (o.borrows ?? o.userBorrows ?? []).map((b: any) => ({
          reserveAddress: b.reserveAddress ?? b.reserve ?? '',
          mintAddress: b.mintAddress ?? b.mint ?? '',
          symbol: b.symbol ?? '',
          amount: b.amount ?? '0',
          valueUsd: b.valueUsd ?? b.marketValueRefreshed ?? '0',
        })),
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino obligations:', err);
      return [];
    }
  }

  // ─── kVaults (Automated Liquidity) ───────────────────────────────────────────

  /** Fetch all kVaults. */
  async getVaults(): Promise<KaminoVault[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/kvaults/vaults`)
      );
      return (resp ?? []).map((v: any) => ({
        address: v.address,
        tokenMint: v.state?.tokenMint ?? '',
        tokenMintDecimals: v.state?.tokenMintDecimals ?? 9,
        sharesMint: v.state?.sharesMint ?? '',
        tokenAvailable: v.state?.tokenAvailable ?? '0',
        sharesIssued: v.state?.sharesIssued ?? '0',
        performanceFeeBps: v.state?.performanceFeeBps ?? 0,
        managementFeeBps: v.state?.managementFeeBps ?? 0,
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino vaults:', err);
      return [];
    }
  }

  /** Get metrics (APY, TVL) for a specific kVault. */
  async getVaultMetrics(vaultAddress: string): Promise<KaminoVaultMetrics | null> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(`${KAMINO_API}/kvaults/vaults/${vaultAddress}/metrics`)
      );
      return resp ? { address: vaultAddress, ...resp } : null;
    } catch (err) {
      console.error('Failed to fetch Kamino vault metrics:', err);
      return null;
    }
  }

  /** Get user positions in kVaults. */
  async getVaultPositions(walletAddress: string): Promise<KaminoVaultPosition[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/kvaults/users/${walletAddress}/positions`)
      );
      return (resp ?? []).map((p: any) => ({
        vaultAddress: p.vaultAddress ?? p.vault ?? p.address ?? '',
        tokenMint: p.tokenMint ?? '',
        shares: p.shares ?? '0',
        sharesUsd: p.sharesUsd ?? p.valueUsd ?? '0',
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino vault positions:', err);
      return [];
    }
  }

  // ─── Staking Yields ───────────────────────────────────────────────────────────

  /** Get LST staking yields tracked by Kamino. */
  async getStakingYields(): Promise<KaminoStakingYield[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/v2/staking-yields`)
      );
      return (resp ?? []).map((s: any) => ({
        tokenMint: s.tokenMint,
        apy: s.apy ?? '0',
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino staking yields:', err);
      return [];
    }
  }

  /** Get staking yield for a specific token mint. */
  async getStakingYieldForMint(tokenMint: string): Promise<number | null> {
    try {
      const yields = await this.getStakingYields();
      const entry = yields.find(y => y.tokenMint === tokenMint);
      return entry ? parseFloat(entry.apy) : null;
    } catch {
      return null;
    }
  }

  // ─── Oracle Prices ────────────────────────────────────────────────────────────

  /** Get oracle prices for one or more token mints. */
  async getOraclePrices(mints: string[]): Promise<KaminoOraclePrice[]> {
    try {
      const params = mints.map(m => `tokens=${m}`).join('&');
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${KAMINO_API}/oracles/prices?${params}`)
      );
      return (resp ?? []).map((p: any) => ({
        mint: p.mint,
        name: p.name ?? '',
        price: p.price ?? '0',
      }));
    } catch (err) {
      console.error('Failed to fetch Kamino oracle prices:', err);
      return [];
    }
  }

  // ─── K-Lend Transaction Builders (via Backend) ────────────────────────────────

  /** Build a deposit transaction. */
  async buildDeposit(
    market: string,
    reserve: string,
    amount: string,
    obligation?: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_deposit', { market, reserve, amount, obligation });
  }

  /** Build a withdraw transaction. */
  async buildWithdraw(
    market: string,
    reserve: string,
    amount: string,
    obligation: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_withdraw', { market, reserve, amount, obligation });
  }

  /** Build a borrow transaction. */
  async buildBorrow(
    market: string,
    reserve: string,
    amount: string,
    obligation: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_borrow', { market, reserve, amount, obligation });
  }

  /** Build a repay transaction. */
  async buildRepay(
    market: string,
    reserve: string,
    amount: string,
    obligation: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_repay', { market, reserve, amount, obligation });
  }

  /** Build add collateral transaction. */
  async buildAddCollateral(
    market: string,
    reserve: string,
    amount: string,
    obligation: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_add_collateral', { market, reserve, amount, obligation });
  }

  /** Build withdraw collateral transaction. */
  async buildWithdrawCollateral(
    market: string,
    reserve: string,
    amount: string,
    obligation: string
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_withdraw_collateral', { market, reserve, amount, obligation });
  }

  // ─── Multiply Vault Transaction Builders ──────────────────────────────────────

  async buildMultiplyOpen(
    vault: string, collateralAmount: string, leverage: number, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_multiply_open', { vault, collateralAmount, leverage, slippageBps });
  }

  async buildMultiplyAdd(
    position: string, amount: string, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_multiply_add', { position, amount, slippageBps });
  }

  async buildMultiplyWithdraw(
    position: string, amount: string, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_multiply_withdraw', { position, amount, slippageBps });
  }

  async buildMultiplyClose(
    position: string, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_multiply_close', { position, slippageBps });
  }

  // ─── Long/Short Vault Transaction Builders ────────────────────────────────────

  async buildLongOpen(
    vault: string, collateralAmount: string, leverage: number, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_long_open', { vault, collateralAmount, leverage, slippageBps });
  }

  async buildShortOpen(
    vault: string, collateralAmount: string, leverage: number, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_short_open', { vault, collateralAmount, leverage, slippageBps });
  }

  async buildPositionClose(
    position: string, closePercent = 100, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_position_close', { position, closePercent, slippageBps });
  }

  // ─── Liquidity Vault Transaction Builders ─────────────────────────────────────

  async buildVaultDeposit(
    vault: string, amountA: string, amountB: string, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_vault_deposit', { vault, amountA, amountB, slippageBps });
  }

  async buildVaultWithdraw(
    vault: string, shares: string, slippageBps = 100
  ): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_vault_withdraw', { vault, shares, slippageBps });
  }

  // ─── Staking Transaction Builders ─────────────────────────────────────────────

  async buildStake(amount: string, lockupSeconds?: number): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_stake', { amount, lockupSeconds });
  }

  async buildUnstake(amount: string): Promise<KaminoBuildResponse | null> {
    return this.buildAction('kamino_unstake', { amount });
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<KaminoBuildResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<KaminoBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
      return resp;
    } catch (err) {
      console.error(`Kamino build ${type} error:`, err);
      return null;
    }
  }
}
