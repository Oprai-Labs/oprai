import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** marginfi bank (asset pool) info */
export interface MarginfiBank {
  address: string;
  mint: string;
  symbol: string;
  name: string;
  decimals: number;
  totalDeposits: string;
  totalBorrows: string;
  depositApy: number;
  borrowApy: number;
  ltvRatio: number;
  liquidationThreshold: number;
  oraclePrice: number;
}

/** marginfi user account info */
export interface MarginfiAccount {
  address: string;
  owner: string;
  group: string;
  deposits: MarginfiPosition[];
  borrows: MarginfiPosition[];
  healthFactor: number;
  totalCollateralUsd: number;
  totalBorrowUsd: number;
  liquidationThreshold: number;
}

/** marginfi position */
export interface MarginfiPosition {
  bank: string;
  mint: string;
  symbol: string;
  amount: string;
  amountUsd: number;
  side: 'deposit' | 'borrow';
}

/** marginfi health check */
export interface MarginfiHealth {
  totalCollateral: number;
  totalBorrow: number;
  healthFactor: number;
  liquidationThreshold: number;
  isHealthy: boolean;
}

/** marginfi points program info */
export interface MarginfiPoints {
  wallet: string;
  totalPoints: number;
  rank: number;
  depositsPoints: number;
  borrowsPoints: number;
}

/** Build response from backend */
export interface MarginfiBuildResponse {
  preview: {
    id: string;
    type: string;
    description: string;
    estimatedFee: string;
    params: Record<string, unknown>;
    warnings: string[];
    requiresApproval: boolean;
  };
  transaction?: string; // base64-encoded legacy transaction (null for query actions)
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

export const MARGINFI_PROGRAM_ID = 'MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA';
export const MARGINFI_GROUP = '4qp6Fx6tnzdkKfVgYCyVvk1bhUfLR1PrWCtL6h9s6w3M';

// MarginFi does not expose a public REST API for bank rates.
// Bank metadata (address, token, symbol) is available from GCS:
const MARGINFI_BANK_METADATA_URL =
  'https://storage.googleapis.com/mrgn-public/mrgn-bank-metadata-cache.json';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class MarginfiService {
  private readonly http = inject(HttpClient);

  // ─── Query Methods ────────────────────────────────────────────────────────────

  /**
   * Get bank metadata list from the public GCS cache.
   * Returns bank address, token address, token name, and symbol.
   * NOTE: APY/rate data is not available via public REST — it requires on-chain reads.
   */
  async getBanks(limit?: number): Promise<MarginfiBank[]> {
    try {
      const raw = await firstValueFrom(
        this.http.get<any[]>(MARGINFI_BANK_METADATA_URL)
      );
      const banks: MarginfiBank[] = (raw ?? []).map((b: any) => ({
        address: b.bankAddress ?? '',
        mint: b.tokenAddress ?? '',
        symbol: b.tokenSymbol ?? '',
        name: b.tokenName ?? b.tokenSymbol ?? '',
        decimals: b.tokenDecimals ?? 6,
        totalDeposits: '0',
        totalBorrows: '0',
        depositApy: 0,
        borrowApy: 0,
        ltvRatio: 0,
        liquidationThreshold: 0,
        oraclePrice: 0,
      }));
      return limit ? banks.slice(0, limit) : banks;
    } catch (err) {
      console.error('Failed to fetch marginfi banks:', err);
      return [];
    }
  }

  /** Get bank info by mint address. */
  async getBankByMint(mint: string): Promise<MarginfiBank | null> {
    const banks = await this.getBanks();
    return banks.find(b => b.mint === mint) ?? null;
  }

  /** Get user account info (on-chain read via backend). */
  async getAccountInfo(account: string): Promise<MarginfiAccount | null> {
    try {
      return await firstValueFrom(
        this.http.get<MarginfiAccount>(
          `${environment.apiBase}/solana/marginfi/account/${account}`
        )
      );
    } catch (err) {
      console.error('Failed to fetch marginfi account:', err);
      return null;
    }
  }

  /** Get all marginfi accounts for a wallet (on-chain read via backend). */
  async getAccountsByWallet(wallet: string): Promise<MarginfiAccount[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<MarginfiAccount[]>(
          `${environment.apiBase}/solana/marginfi/accounts?wallet=${wallet}`
        )
      );
      return resp ?? [];
    } catch (err) {
      console.error('Failed to fetch marginfi accounts by wallet:', err);
      return [];
    }
  }

  /** Get account health metrics. */
  async getHealth(account: string): Promise<MarginfiHealth | null> {
    const info = await this.getAccountInfo(account);
    if (!info) return null;
    return {
      totalCollateral: info.totalCollateralUsd,
      totalBorrow: info.totalBorrowUsd,
      healthFactor: info.healthFactor,
      liquidationThreshold: info.liquidationThreshold,
      isHealthy: info.healthFactor >= 1.0,
    };
  }

  /** Get points program info for a wallet (via backend). */
  async getPoints(wallet: string): Promise<MarginfiPoints | null> {
    try {
      return await firstValueFrom(
        this.http.get<MarginfiPoints>(
          `${environment.apiBase}/solana/marginfi/points/${wallet}`
        )
      );
    } catch (err) {
      console.error('Failed to fetch marginfi points:', err);
      return null;
    }
  }

  // ─── Transaction Builders (via Backend) ────────────────────────────────────

  /** Create a new marginfi lending account. */
  async buildCreateAccount(referralCode?: string): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_create_account', { referralCode });
  }

  /** Deposit assets into a marginfi bank. */
  async buildDeposit(
    account: string,
    bank: string,
    amount: string
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_deposit', { account, bank, amount });
  }

  /** Withdraw assets from a marginfi bank. */
  async buildWithdraw(
    account: string,
    bank: string,
    amount: string,
    allowBorrow?: boolean
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_withdraw', { account, bank, amount, allowBorrow });
  }

  /** Borrow assets from a marginfi bank. */
  async buildBorrow(
    account: string,
    bank: string,
    amount: string
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_borrow', { account, bank, amount });
  }

  /** Repay borrowed assets to a marginfi bank. */
  async buildRepay(
    account: string,
    bank: string,
    amount: string
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_repay', { account, bank, amount });
  }

  /** Deposit assets as collateral only (no yield). */
  async buildDepositCollateral(
    account: string,
    bank: string,
    amount: string
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_deposit_collateral', { account, bank, amount });
  }

  /** Withdraw collateral from a marginfi bank. */
  async buildWithdrawCollateral(
    account: string,
    bank: string,
    amount: string
  ): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_withdraw_collateral', { account, bank, amount });
  }

  /** Close a marginfi account (must have zero deposits and borrows). */
  async buildCloseAccount(account: string): Promise<MarginfiBuildResponse | null> {
    return this.buildAction('marginfi_close_account', { account });
  }

  // ─── Helper Methods ────────────────────────────────────────────────────────

  /** Check if an account is at risk of liquidation. */
  isAtRisk(account: MarginfiAccount, threshold = 1.2): boolean {
    return account.healthFactor < threshold;
  }

  /** Get the best deposit APY across all banks for a given mint. */
  async getBestDepositApy(mint: string): Promise<number> {
    const banks = await this.getBanks();
    const matching = banks.filter(b => b.mint === mint);
    if (matching.length === 0) return 0;
    return Math.max(...matching.map(b => b.depositApy));
  }

  /** Get the best borrow APY across all banks for a given mint. */
  async getBestBorrowApy(mint: string): Promise<number> {
    const banks = await this.getBanks();
    const matching = banks.filter(b => b.mint === mint);
    if (matching.length === 0) return 0;
    return Math.min(...matching.map(b => b.borrowApy));
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<MarginfiBuildResponse | null> {
    try {
      return await firstValueFrom(
        this.http.post<MarginfiBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
    } catch (err) {
      console.error(`MarginFi build ${type} error:`, err);
      return null;
    }
  }
}
