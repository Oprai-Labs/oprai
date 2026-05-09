import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

export interface PumpFunToken {
  mint: string;
  name: string;
  symbol: string;
  description: string;
  image_uri: string;
  creator: string;
  created_timestamp: number;
  market_cap: number;
  usd_market_cap: number;
  virtual_sol_reserves: number;
  virtual_token_reserves: number;
  real_sol_reserves: number;
  real_token_reserves: number;
  bonding_curve: string;
  associated_bonding_curve: string;
  complete: boolean;           // true = graduated (to Raydium or PumpSwap AMM)
  raydium_pool?: string;       // set for pre-March-2025 tokens that graduated to Raydium
  pump_amm?: string;           // set for post-March-2025 tokens that graduated to PumpSwap AMM
  king_of_the_hill_timestamp?: number;
  reply_count: number;
  last_reply?: number;
  total_supply: number;
  twitter?: string;
  telegram?: string;
  website?: string;
  show_name: boolean;
  nsfw: boolean;
}

export interface BondingCurveState {
  virtualSolReserves: number;
  virtualTokenReserves: number;
  realSolReserves: number;
  realTokenReserves: number;
  fillPercent: number;       // 0–100, how full the bonding curve is
  pricePerToken: number;     // SOL per token
  graduationThresholdSol: number;
  solToGraduation: number;   // SOL remaining until graduation
  graduated: boolean;
}

export interface PumpFunTokenWithCurve extends PumpFunToken {
  bondingCurve?: BondingCurveState;
}

export interface PumpFunComment {
  id: number;
  mint: string;
  user: string;
  text: string;
  timestamp: number;
  profile_image?: string;
  username?: string;
  likes: number;
}

export interface PumpFunUserProfile {
  address: string;
  username?: string;
  profile_image?: string;
  bio?: string;
  created_tokens: number;
  coins_created: PumpFunToken[];
  coins_held: Array<{ mint: string; balance: number; balance_usd: number }>;
  followers?: number;
  following?: number;
}

/** Build response from backend (for launch action) */
export interface PumpFunBuildResponse {
  preview: {
    id: string;
    type: string;
    description: string;
    estimatedFee: string;
    params: Record<string, unknown>;
    warnings: string[];
    requiresApproval: boolean;
  };
  transaction?: string;
  additionalSignersRequired?: number;
}

export interface LaunchTokenParams {
  name: string;
  symbol: string;
  description: string;
  imageUrl?: string;
  metadataUri?: string; // pre-uploaded metadata JSON URL
  twitter?: string;
  telegram?: string;
  website?: string;
  bannerUrl?: string;
  initialBuyAmount?: string;
  slippage?: number;
  priorityFee?: number;
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

export const PUMPFUN_PROGRAM_ID = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
export const PUMPFUN_FEE_RECIPIENT = 'CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM';
export const GRADUATION_SOL_THRESHOLD = 85;
const INITIAL_VIRTUAL_SOL_RESERVES = 30;

const SOLANA_ADDRESS_REGEX = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
function isValidSolanaAddress(address: string): boolean {
  return !!address && SOLANA_ADDRESS_REGEX.test(address.trim());
}

// ──────────────────────────────────────────────────────────────────────────────
// Bonding curve helpers (pure, no HTTP)
// ──────────────────────────────────────────────────────────────────────────────

export function calcBondingCurve(token: Partial<PumpFunToken>): BondingCurveState {
  const INITIAL_VSR = INITIAL_VIRTUAL_SOL_RESERVES * 1e9;
  const vsr = token.virtual_sol_reserves ?? INITIAL_VSR;
  const vtr = token.virtual_token_reserves ?? 1_073_000_191 * 1e6;
  const rsr = token.real_sol_reserves ?? 0;
  const graduated = token.complete === true || !!token.raydium_pool || !!token.pump_amm;

  const vsrSol = vsr / 1e9;
  const solToGrad = Math.max(0, GRADUATION_SOL_THRESHOLD - vsrSol);
  const fillPercent = graduated
    ? 100
    : Math.max(0, Math.min(100,
        ((vsrSol - INITIAL_VIRTUAL_SOL_RESERVES) /
         (GRADUATION_SOL_THRESHOLD - INITIAL_VIRTUAL_SOL_RESERVES)) * 100));

  const pricePerToken = vtr > 0 ? (vsr / 1e9) / (vtr / 1e6) : 0;

  return {
    virtualSolReserves: vsr,
    virtualTokenReserves: vtr,
    realSolReserves: rsr,
    realTokenReserves: token.real_token_reserves ?? 0,
    fillPercent,
    pricePerToken,
    graduationThresholdSol: GRADUATION_SOL_THRESHOLD,
    solToGraduation: solToGrad,
    graduated,
  };
}

/** Estimate tokens received for a given SOL amount on a bonding curve. */
export function estimateTokensForSol(solAmount: number, token?: Partial<PumpFunToken>): number {
  const vsr = token?.virtual_sol_reserves ?? INITIAL_VIRTUAL_SOL_RESERVES * 1e9;
  const vtr = token?.virtual_token_reserves ?? 1_073_000_191 * 1e6;
  const solLamports = solAmount * 1e9;
  return ((vtr * solLamports) / (vsr + solLamports)) / 1e6;
}

/** Returns true if the token has graduated (to Raydium or PumpSwap AMM). */
export function isGraduated(token: Pick<PumpFunToken, 'complete' | 'raydium_pool' | 'pump_amm'>): boolean {
  return token.complete === true || !!token.raydium_pool || !!token.pump_amm;
}

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class PumpFunService {
  private readonly http = inject(HttpClient);
  private readonly api = environment.apiBase;

  // ─── Token Data ──────────────────────────────────────────────────────────────

  /** Get token info + bonding curve state. */
  async getToken(mint: string): Promise<PumpFunTokenWithCurve | null> {
    try {
      const token = await firstValueFrom(
        this.http.get<PumpFunToken>(`${this.api}/pumpfun/token/${mint}`)
      );
      return { ...token, bondingCurve: calcBondingCurve(token) };
    } catch {
      return null;
    }
  }

  /** Get trending tokens (by market cap). */
  async getTrending(limit = 50): Promise<PumpFunTokenWithCurve[]> {
    return this.fetchTokenList(`${this.api}/pumpfun/tokens/trending?limit=${limit}`);
  }

  /** Get newest token launches. */
  async getNew(limit = 50): Promise<PumpFunTokenWithCurve[]> {
    return this.fetchTokenList(`${this.api}/pumpfun/tokens/new?limit=${limit}`);
  }

  /** Get all tokens (sorted by last trade). */
  async listTokens(limit = 50, offset = 0): Promise<PumpFunTokenWithCurve[]> {
    return this.fetchTokenList(`${this.api}/pumpfun/tokens?limit=${limit}&offset=${offset}`);
  }

  /** Get tokens close to Raydium graduation. */
  async getGraduating(limit = 20): Promise<PumpFunTokenWithCurve[]> {
    return this.fetchTokenList(`${this.api}/pumpfun/tokens/graduating?limit=${limit}`);
  }

  /** Search tokens by name or symbol. */
  async search(query: string, limit = 20): Promise<PumpFunTokenWithCurve[]> {
    const q = encodeURIComponent(query);
    return this.fetchTokenList(`${this.api}/pumpfun/tokens/search?q=${q}&limit=${limit}`);
  }

  /** Get the current King of the Hill token. */
  async getKingOfHill(): Promise<PumpFunTokenWithCurve | null> {
    try {
      const token = await firstValueFrom(
        this.http.get<PumpFunToken>(`${this.api}/pumpfun/koth`)
      );
      return { ...token, bondingCurve: calcBondingCurve(token) };
    } catch {
      return null;
    }
  }

  /** Get comments/replies for a token. */
  async getComments(mint: string, limit = 25, offset = 0): Promise<PumpFunComment[]> {
    try {
      return await firstValueFrom(
        this.http.get<PumpFunComment[]>(
          `${this.api}/pumpfun/token/${mint}/comments?limit=${limit}&offset=${offset}`
        )
      );
    } catch {
      return [];
    }
  }

  /** Get a user's pump.fun profile (created tokens, holdings). */
  async getUserProfile(wallet: string): Promise<PumpFunUserProfile | null> {
    try {
      return await firstValueFrom(
        this.http.get<PumpFunUserProfile>(`${this.api}/pumpfun/user/${wallet}`)
      );
    } catch {
      return null;
    }
  }

  // ─── Trade Transaction Builder ───────────────────────────────────────────────

  /**
   * Build a buy transaction for a bonding curve token.
   * If the token has graduated to Raydium, throws with TOKEN_GRADUATED error.
   *
   * @returns base64 unsigned transaction string or null on error
   */
  async buildBuy(
    wallet: string,
    mint: string,
    amount: number,
    denominatedInSol = true,
    slippage = 10,
    priorityFee = 0.0005
  ): Promise<PumpFunBuildResponse | null> {
    if (!isValidSolanaAddress(wallet) || !isValidSolanaAddress(mint)) return null;
    if (!Number.isFinite(amount) || amount <= 0) return null;

    return firstValueFrom(
      this.http.post<PumpFunBuildResponse>(`${this.api}/actions/build`, {
        type: 'pumpfun_buy',
        params: { mint, amount: String(amount), denominatedInSol, slippage, priorityFee },
      })
    ).catch(err => {
      const msg = err?.error?.error ?? err?.message ?? 'Failed to build pump.fun transaction';
      throw new Error(msg);
    });
  }

  /**
   * Build a trade transaction for a graduated token on PumpSwap AMM.
   * Uses PumpPortal trade-local API with pool: "pump-amm".
   */
  async buildPumpSwap(
    wallet: string,
    mint: string,
    amount: number,
    denominatedInSol = true,
    slippage = 10,
    priorityFee = 0.0005,
    side: 'buy' | 'sell' = 'buy'
  ): Promise<PumpFunBuildResponse | null> {
    if (!isValidSolanaAddress(wallet) || !isValidSolanaAddress(mint)) return null;
    if (!Number.isFinite(amount) || amount <= 0) return null;

    const actionType = side === 'buy' ? 'pumpswap_buy' : 'pumpswap_sell';
    return firstValueFrom(
      this.http.post<PumpFunBuildResponse>(`${this.api}/actions/build`, {
        type: actionType,
        params: { mint, amount: String(amount), denominatedInSol, slippage, priorityFee },
      })
    ).catch(err => {
      const msg = err?.error?.error ?? err?.message ?? 'Failed to build PumpSwap transaction';
      throw new Error(msg);
    });
  }

  /**
   * Build a sell transaction for a bonding curve token.
   * If the token has graduated, throws with TOKEN_GRADUATED error.
   */
  async buildSell(
    wallet: string,
    mint: string,
    amount: number,
    denominatedInSol = false,
    slippage = 10,
    priorityFee = 0.0005
  ): Promise<PumpFunBuildResponse | null> {
    if (!isValidSolanaAddress(wallet) || !isValidSolanaAddress(mint)) return null;
    if (!Number.isFinite(amount) || amount <= 0) return null;

    return firstValueFrom(
      this.http.post<PumpFunBuildResponse>(`${this.api}/actions/build`, {
        type: 'pumpfun_sell',
        params: { mint, amount: String(amount), denominatedInSol, slippage, priorityFee },
      })
    ).catch(err => {
      const msg = err?.error?.error ?? err?.message ?? 'Failed to build pump.fun transaction';
      throw new Error(msg);
    });
  }

  // ─── Launch Transaction Builder (via Backend) ─────────────────────────────

  /**
   * Build a token launch transaction.
   *
   * Recommended flow before calling this:
   *   1. Upload image: UploadService.uploadImage(file) → imageUrl
   *   2. Upload metadata: UploadService.uploadMetadata({...}) → metadataUri
   *   3. Call buildLaunch({ ..., imageUrl, metadataUri })
   */
  async buildLaunch(params: LaunchTokenParams): Promise<PumpFunBuildResponse | null> {
    return firstValueFrom(
      this.http.post<PumpFunBuildResponse>(`${this.api}/actions/build`, {
        type: 'launch_token',
        params: {
          name: params.name,
          symbol: params.symbol,
          description: params.description,
          imageUrl: params.imageUrl,
          metadataUri: params.metadataUri,
          twitter: params.twitter,
          telegram: params.telegram,
          website: params.website,
          bannerUrl: params.bannerUrl,
          initialBuyAmount: params.initialBuyAmount,
          slippage: params.slippage,
          priorityFee: params.priorityFee,
        },
      })
    ).catch(err => {
      console.error('[PumpFun] buildLaunch error:', err);
      return null;
    });
  }

  // ─── Private helpers ───────────────────────────────────────────────────────

  private async fetchTokenList(url: string): Promise<PumpFunTokenWithCurve[]> {
    try {
      const tokens = await firstValueFrom(this.http.get<PumpFunToken[]>(url));
      return (tokens ?? []).map(t => ({ ...t, bondingCurve: calcBondingCurve(t) }));
    } catch {
      return [];
    }
  }
}
