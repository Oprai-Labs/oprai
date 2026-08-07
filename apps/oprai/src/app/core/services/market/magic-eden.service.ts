import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

/**
 * Magic Eden reads, through our backend.
 *
 * Everything Magic Eden returns falls into four shapes, and the cards are
 * built around those rather than around the twenty-odd endpoint names:
 *
 *   collections — a picture, a floor price, volume
 *   tokens      — an NFT: picture, name, rarity, an asking price
 *   activities  — a ledger: who did what, at what price, when
 *   offers      — a bid: who, how much, expiring when
 *
 * The endpoint list is deliberately not modelled one-type-per-endpoint. Twenty
 * near-identical interfaces is how a field gets read from the wrong one.
 */

export interface MeCollectionRow {
  symbol: string;
  name: string;
  image?: string | null;
  description?: string | null;
  floorPrice?: number | null;      // lamports on /collections/{s}/stats
  listedCount?: number | null;
  /** `/stats` returns volume7d and avgPrice24hr — NOT an all-time or 24h
   *  volume. Rendering fields the endpoint never sends is how a card ends up
   *  showing two dashes where its headline numbers should be. */
  volume7d?: number | null;
  avgPrice24hr?: number | null;
  volumeAll?: number | null;
  volume24hr?: number | null;
  /** The collection's on-chain size, and the share of it that is for sale.
   *  Present only when the chain reports a size — MPL Core collections do,
   *  Token Metadata ones do not, and a guessed supply poisons every
   *  percentage derived from it. */
  supply?: number | null;
  listedShare?: number | null;
  twitter?: string | null;
  discord?: string | null;
  website?: string | null;
  categories?: string[];
  isBadged?: boolean;
  hasCNFTs?: boolean;
}

export interface MeTokenRow {
  mintAddress: string;
  name: string;
  image?: string | null;
  collection?: string | null;
  collectionName?: string | null;
  owner?: string | null;
  price?: number | null;
  listStatus?: string | null;
  tokenAddress?: string | null;
  rarityRank?: number | null;
  attributes?: Array<{ trait_type?: string; traitType?: string; value: unknown }>;
  sellerFeeBasisPoints?: number | null;
}

export interface MeActivityRow {
  signature?: string;
  type?: string;
  source?: string | null;
  tokenMint?: string;
  collection?: string | null;
  slot?: number;
  blockTime?: number;
  buyer?: string | null;
  seller?: string | null;
  price?: number | null;
  image?: string | null;
  tokenName?: string | null;
}

export interface MeOfferRow {
  pdaAddress?: string;
  tokenMint?: string;
  auctionHouse?: string;
  buyer?: string | null;
  seller?: string | null;
  price?: number | null;
  expiry?: number | null;
  tokenAddress?: string | null;
  collectionSymbol?: string | null;
  /** Resolved server-side from the mint, since Magic Eden's offer endpoints
   *  carry only an address. The card falls back to the mint. */
  name?: string | null;
  image?: string | null;
  collectionName?: string | null;
}

export interface MeEscrowBalance {
  buyerEscrow?: number | null;
  balance?: number | null;
}

@Injectable({ providedIn: 'root' })
export class MagicEdenService {
  private readonly http = inject(HttpClient);

  /**
   * The reason the last read failed, in the backend's own words.
   *
   * It knows things the card cannot infer — that an NFT is not listed, that a
   * collection does not exist — and swallowing them left every failure
   * reading as "could not reach Magic Eden", which was usually untrue and
   * never actionable.
   */
  private readonly _lastError = signal<string | null>(null);
  lastError(): string | null { return this._lastError(); }

  /** Run any Magic Eden read through /actions/build and hand back the payload. */
  async read<T = unknown>(type: string, params: Record<string, unknown> = {}): Promise<T | null> {
    this._lastError.set(null);
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: T }>(
          `${environment.apiBase}/actions/build`,
          { type, params },
          { withCredentials: true },
        ),
      );
      return (resp?.data ?? null) as T | null;
    } catch (err) {
      const msg = (err as { error?: { error?: string } })?.error?.error;
      // Strip the classifier the API prefixes onto every error.
      this._lastError.set(msg ? msg.replace(/^(Not found|Invalid parameters):\s*/i, '') : null);
      console.error(`Magic Eden ${type} failed:`, err);
      return null;
    }
  }

  /**
   * Magic Eden quotes floors in lamports on the stats endpoints and in SOL
   * almost everywhere else. Reading one as the other is a 1,000,000,000x
   * error on a price, so normalise once, here, rather than at each call site.
   */
  static solFromMaybeLamports(v: number | null | undefined): number | null {
    if (v === null || v === undefined || !Number.isFinite(v)) return null;
    // No NFT trades for a billion SOL, and none trades for a billionth of one.
    return v > 1e5 ? v / 1e9 : v;
  }

  /** Collections come back under different keys depending on the endpoint. */
  static collectionsFrom(data: unknown): MeCollectionRow[] {
    if (Array.isArray(data)) return data as MeCollectionRow[];
    const d = data as Record<string, unknown> | null;
    for (const key of ['collections', 'results', 'data']) {
      const v = d?.[key];
      if (Array.isArray(v)) return v as MeCollectionRow[];
    }
    return [];
  }

  static rowsFrom<T>(data: unknown, ...keys: string[]): T[] {
    if (Array.isArray(data)) return data as T[];
    const d = data as Record<string, unknown> | null;
    for (const key of [...keys, 'results', 'data']) {
      const v = d?.[key];
      if (Array.isArray(v)) return v as T[];
    }
    return [];
  }
}
