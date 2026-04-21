import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

// ── Types ────────────────────────────────────────────────────────────────────

export interface SolendDeposit {
  assetSymbol: string;
  mint: string;
  depositedAmount: number;
  depositedValueUsd: number;
}

export interface SolendBorrow {
  assetSymbol: string;
  mint: string;
  borrowedAmount: number;
  borrowedValueUsd: number;
}

export interface SolendObligation {
  address: string;
  totalCollateralValue: number;
  totalBorrowedValue: number;
  healthFactor: number | null;
  liquidationValue: number;
  deposits: SolendDeposit[];
  borrows: SolendBorrow[];
}

// ── Service ──────────────────────────────────────────────────────────────────

/**
 * SolendService — data-only queries against the Solend API.
 * Transactional actions (deposit/withdraw/borrow/repay) are NOT supported
 * because the Rust backend only has stub implementations.
 */
@Injectable({ providedIn: 'root' })
export class SolendService {
  private readonly http = inject(HttpClient);

  private readonly BASE = 'https://api.solend.fi';

  /**
   * Fetch all lending obligations for a wallet.
   * Returns empty array on error (API unavailable, no obligations, etc.).
   */
  async getObligations(wallet: string): Promise<SolendObligation[]> {
    try {
      const url = `${this.BASE}/v1/obligations?wallet=${wallet}`;
      const data = await firstValueFrom(
        this.http.get<any>(url)
      );

      const obligations: any[] = Array.isArray(data?.results)
        ? data.results
        : Array.isArray(data)
          ? data
          : [];

      return obligations.map((o: any): SolendObligation => {
        const deposits: SolendDeposit[] = (o.deposits ?? []).map((d: any) => ({
          assetSymbol:       d.symbol ?? d.mintAddress?.slice(0, 4) ?? 'Unknown',
          mint:              d.mintAddress ?? '',
          depositedAmount:   Number(d.amount ?? 0),
          depositedValueUsd: Number(d.amountUSD ?? 0),
        }));

        const borrows: SolendBorrow[] = (o.borrows ?? []).map((b: any) => ({
          assetSymbol:    b.symbol ?? b.mintAddress?.slice(0, 4) ?? 'Unknown',
          mint:           b.mintAddress ?? '',
          borrowedAmount: Number(b.amount ?? 0),
          borrowedValueUsd: Number(b.amountUSD ?? 0),
        }));

        const totalCollateral = deposits.reduce((s, d) => s + d.depositedValueUsd, 0);
        const totalBorrowed   = borrows.reduce((s, b) => s + b.borrowedValueUsd, 0);

        const hf = totalBorrowed > 0
          ? (o.healthFactor != null ? Number(o.healthFactor) : totalCollateral / totalBorrowed)
          : null;

        return {
          address:              o.publicKey ?? o.address ?? '',
          totalCollateralValue: totalCollateral,
          totalBorrowedValue:   totalBorrowed,
          healthFactor:         hf,
          liquidationValue:     Number(o.liquidationThreshold ?? totalCollateral * 0.8),
          deposits,
          borrows,
        };
      });
    } catch {
      // API unavailable or no obligations — fail silently
      return [];
    }
  }
}
