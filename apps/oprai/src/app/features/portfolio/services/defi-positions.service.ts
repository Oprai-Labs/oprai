import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ProtocolDetectionService } from './protocol-detection.service';
import { JupiterLendService } from '@core/services/market/jupiter-lend.service';
import { ApiService } from '@core/services/api.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import type { EnhancedTokenAccount, ProtocolPosition, PositionItem } from '../models/portfolio.models';

const RAYDIUM_PAIRS_URL = 'https://api.raydium.io/v2/main/pairs';
const KAMINO_OBLIGATIONS_URL = 'https://api.kamino.finance/v2/user-metadata';
const MARGINFI_ACCOUNTS_URL = 'https://production.marginfi.com/marginfi_accounts';
const RAYDIUM_V3_API = 'https://api-v3.raydium.io';
// Meteora datapi — replaces deprecated `dlmm-api.meteora.ag/position/user/<wallet>`
// (returns 404 since early 2026). The new portfolio/open endpoint groups
// positions by pool: { pools: [{ pool_address, name, positions: [...], ... }] }.
const METEORA_DATAPI = 'https://dlmm.datapi.meteora.ag';
const STREAMFLOW_API = 'https://api.streamflow.finance/api/v2';
const DRIFT_API = 'https://mainnet-beta.drift.trade/v2';

interface RaydiumPair {
  ammId: string;
  lpMint: string;
  name: string;
  tokenAmountCoin: number;
  tokenAmountPc: number;
  lpPrice: number;
  tokenMintCoin: string;
  tokenMintPc: string;
}

@Injectable({ providedIn: 'root' })
export class DefiPositionsService {
  private readonly protocolDetection = inject(ProtocolDetectionService);
  private readonly jupiterLend = inject(JupiterLendService);
  private readonly apiService = inject(ApiService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private raydiumPairsCache: RaydiumPair[] | null = null;
  private raydiumCacheTimestamp = 0;
  private readonly CACHE_TTL = 120_000;

  /**
   * Resolve a token's logo URI from the Jupiter token registry. Falls back
   * to mint-based lookup when the symbol-keyed lookup misses (some pools
   * report only the mint, e.g. Meteora DLMM /portfolio/open). Triggers an
   * async resolve so the registry warms up for the next render even if the
   * current call returns null.
   */
  private resolveTokenLogo(symbol: string | null | undefined, mint?: string | null): string | null {
    if (mint) {
      const byMint = this.tokenRegistry.getToken(mint);
      if (byMint?.logoURI) return byMint.logoURI;
      this.tokenRegistry.resolveAsync(mint);
    }
    if (symbol) {
      const bySym = this.tokenRegistry.getBySymbol(symbol);
      if (bySym?.logoURI) return bySym.logoURI;
    }
    return null;
  }

  async getLpPositions(
    _wallet: string,
    tokenAccounts: EnhancedTokenAccount[]
  ): Promise<ProtocolPosition[]> {
    const positions: ProtocolPosition[] = [];

    try {
      const raydiumPairs = await this.fetchRaydiumPairs();
      if (raydiumPairs.length > 0) {
        const lpMintMap = new Map(raydiumPairs.map((p) => [p.lpMint, p]));
        const matchedItems: PositionItem[] = [];

        for (const token of tokenAccounts) {
          const pair = lpMintMap.get(token.mint);
          if (pair && token.balance > 0) {
            const usdValue = pair.lpPrice > 0 ? token.balance * pair.lpPrice : token.usdValue;
            const symA = pair.name.split('-')[0]?.trim() ?? '?';
            const symB = pair.name.split('-')[1]?.trim() ?? '?';
            matchedItems.push({
              label: pair.name,
              tokens: [
                { symbol: symA, amount: 0, logoUri: this.resolveTokenLogo(symA, pair.tokenMintCoin) },
                { symbol: symB, amount: 0, logoUri: this.resolveTokenLogo(symB, pair.tokenMintPc) },
              ],
              totalUsdValue: usdValue,
              metadata: { lpAmount: token.balance },
            });
          }
        }

        if (matchedItems.length > 0) {
          const totalValue = matchedItems.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0);
          positions.push({
            protocolId: 'raydium',
            protocolName: 'Raydium',
            protocolLogoUri: this.protocolDetection.getProtocolLogo('raydium'),
            category: 'liquidity-pool',
            positions: matchedItems,
            totalUsdValue: totalValue,
          });
        }
      }
    } catch {
      // Graceful fallback
    }

    return positions;
  }

  async getLendingPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const [earnPositions, borrowPositions] = await Promise.all([
        this.jupiterLend.getAllEarnPositions(wallet),
        this.jupiterLend.getBorrowPositions(wallet),
      ]);

      const positions: ProtocolPosition[] = [];
      const logo = this.protocolDetection.getProtocolLogo('jupiter');

      if (earnPositions.length > 0) {
        const items: PositionItem[] = earnPositions.map(p => ({
          label: p.asset.symbol,
          tokens: [{ symbol: p.asset.symbol, amount: p.depositedAmount, logoUri: this.resolveTokenLogo(p.asset.symbol) }],
          totalUsdValue: null,
          metadata: { apy: p.apy, depositedAmount: p.depositedAmount },
        }));
        positions.push({
          protocolId: 'jupiter',
          protocolName: 'Jupiter Lend',
          protocolLogoUri: logo,
          category: 'lending',
          positions: items,
          totalUsdValue: 0,
        });
      }

      if (borrowPositions.length > 0) {
        const items: PositionItem[] = borrowPositions.map(p => ({
          label: `${p.collateralAsset.symbol} / ${p.debtAsset.symbol}`,
          tokens: [
            { symbol: p.collateralAsset.symbol, amount: p.collateralAmount, logoUri: this.resolveTokenLogo(p.collateralAsset.symbol) },
            { symbol: p.debtAsset.symbol, amount: p.debtAmount, logoUri: this.resolveTokenLogo(p.debtAsset.symbol) },
          ],
          totalUsdValue: null,
          metadata: {
            healthFactor: p.healthFactor,
            ltv: p.ltv,
            liquidationThreshold: p.liquidationThreshold,
          },
        }));
        positions.push({
          protocolId: 'jupiter',
          protocolName: 'Jupiter Lend',
          protocolLogoUri: logo,
          category: 'borrowing',
          positions: items,
          totalUsdValue: 0,
        });
      }

      return positions;
    } catch {
      return [];
    }
  }

  getLiquidStakingPositions(
    tokenAccounts: EnhancedTokenAccount[],
    solPrice: number | null
  ): ProtocolPosition[] {
    const protocolMap = new Map<string, PositionItem[]>();

    for (const token of tokenAccounts) {
      if (!this.protocolDetection.isLiquidStakingToken(token.mint)) continue;
      if (token.balance <= 0) continue;

      const info = this.protocolDetection.getLstInfo(token.mint);
      if (!info) continue;

      const protocol = info.protocol;
      const items = protocolMap.get(protocol) ?? [];

      // LSTs are roughly 1:1 with SOL, use token's usdValue if available
      const usdValue = token.usdValue ?? (solPrice ? token.balance * solPrice : null);

      items.push({
        label: info.name,
        tokens: [{ symbol: info.symbol, amount: token.balance, logoUri: token.logoUri }],
        totalUsdValue: usdValue,
        metadata: {},
      });

      protocolMap.set(protocol, items);
    }

    const positions: ProtocolPosition[] = [];

    for (const [protocol, items] of protocolMap) {
      const totalValue = items.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0);
      const protocolId = protocol.toLowerCase().replace(/\s+/g, '-');
      positions.push({
        protocolId,
        protocolName: protocol,
        protocolLogoUri: this.protocolDetection.getProtocolLogo(protocolId),
        category: 'liquid-staking',
        positions: items,
        totalUsdValue: totalValue,
      });
    }

    return positions;
  }

  async getKaminoPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(`${KAMINO_OBLIGATIONS_URL}/${wallet}/obligations`, { signal: controller.signal });
      clearTimeout(timeout);
      if (!res.ok) return [];
      const data = await res.json() as any;
      const obligations: any[] = Array.isArray(data) ? data : (data?.data ?? []);
      if (!obligations.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('kamino') ?? null;
      const supplyItems: PositionItem[] = [];
      const borrowItems: PositionItem[] = [];

      for (const obl of obligations) {
        const market = obl.marketName ?? obl.lendingMarket ?? 'Kamino Market';
        // Risk signals (Kamino obligation): currentLtv vs liquidationLtv. Health
        // factor = liquidationLtv / currentLtv when both exist.
        const currentLtv = Number(obl.loanToValue ?? obl.currentLtv ?? 0) || null;
        const liqLtv = Number(obl.liquidationLtv ?? obl.unhealthyLtv ?? 0) || null;
        const healthFactor =
          obl.healthFactor != null
            ? Number(obl.healthFactor)
            : (currentLtv && liqLtv && currentLtv > 0 ? liqLtv / currentLtv : null);
        const riskMeta = {
          healthFactor: healthFactor != null ? Number(healthFactor.toFixed(3)) : null,
          ltv: currentLtv != null ? Number((currentLtv * 100).toFixed(2)) : null,
          liquidationLtv: liqLtv != null ? Number((liqLtv * 100).toFixed(2)) : null,
        };
        for (const dep of (obl.deposits ?? obl.collaterals ?? [])) {
          const sym: string = dep.symbol ?? dep.mintSymbol ?? 'UNKNOWN';
          const mint: string | null = dep.mint ?? dep.mintAddress ?? null;
          const amt: number = dep.amount ?? dep.depositedAmount ?? 0;
          if (amt > 0) {
            supplyItems.push({ label: `${sym} — ${market}`, tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint) }], totalUsdValue: dep.usdValue ?? null, metadata: { apy: dep.apy ?? null } });
          }
        }
        for (const bor of (obl.borrows ?? obl.liabilities ?? [])) {
          const sym: string = bor.symbol ?? bor.mintSymbol ?? 'UNKNOWN';
          const mint: string | null = bor.mint ?? bor.mintAddress ?? null;
          const amt: number = bor.amount ?? bor.borrowedAmount ?? 0;
          if (amt > 0) {
            borrowItems.push({
              label: `${sym} — ${market}`,
              tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint) }],
              totalUsdValue: bor.usdValue ?? null,
              metadata: { apy: bor.apy ?? null, ...riskMeta },
            });
          }
        }
      }

      const positions: ProtocolPosition[] = [];
      if (supplyItems.length > 0) {
        positions.push({ protocolId: 'kamino', protocolName: 'Kamino Lend', protocolLogoUri: logo, category: 'lending', positions: supplyItems, totalUsdValue: supplyItems.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0) });
      }
      if (borrowItems.length > 0) {
        positions.push({ protocolId: 'kamino', protocolName: 'Kamino Lend', protocolLogoUri: logo, category: 'borrowing', positions: borrowItems, totalUsdValue: borrowItems.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0) });
      }
      return positions;
    } catch {
      return [];
    }
  }

  async getMarginFiPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(`${MARGINFI_ACCOUNTS_URL}?authority=${wallet}`, { signal: controller.signal });
      clearTimeout(timeout);
      if (!res.ok) return [];
      const data = await res.json() as any;
      const accounts: any[] = Array.isArray(data) ? data : (data?.data ?? data?.marginfi_accounts ?? []);
      if (!accounts.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('marginfi') ?? null;
      const lendItems: PositionItem[] = [];
      const borrowItems: PositionItem[] = [];

      for (const acc of accounts) {
        // Account-level health (MarginFi). API may surface either `health`
        // (a 0..1 ratio where 1.0 = healthy, 0 = liquidatable) or
        // collateral/liability amounts we can derive from. Health factor we
        // expose to the UI is the standard "liquidation = 1.0" convention,
        // so we map: hf = 1 / (1 - health) when health < 1, else infinity.
        const accHealthRaw =
          acc.health != null ? Number(acc.health)
          : acc.account_health != null ? Number(acc.account_health)
          : null;
        const totalCollat = Number(acc.total_collateral_usd ?? acc.totalCollateralValue ?? 0) || null;
        const totalDebt = Number(acc.total_liabilities_usd ?? acc.totalLiabilitiesValue ?? 0) || null;
        let healthFactor: number | null = null;
        if (accHealthRaw != null && accHealthRaw > 0 && accHealthRaw < 1) {
          healthFactor = Number((1 / (1 - accHealthRaw)).toFixed(3));
        } else if (totalCollat && totalDebt && totalDebt > 0) {
          healthFactor = Number((totalCollat / totalDebt).toFixed(3));
        }
        const riskMeta = {
          healthFactor,
          ltv: totalCollat && totalCollat > 0 && totalDebt != null
            ? Number(((totalDebt / totalCollat) * 100).toFixed(2))
            : null,
        };

        const balances: any[] = acc.balances ?? acc.active_assets ?? [];
        for (const bal of balances) {
          const sym: string = bal.symbol ?? bal.token_symbol ?? 'UNKNOWN';
          const mint: string | null = bal.mint ?? bal.token_mint ?? null;
          const side: string = bal.side ?? (bal.liability_shares ? 'borrow' : 'deposit');
          const amt: number = bal.quantity_deposited ?? bal.deposit_quantity ?? bal.quantity ?? 0;
          const borrowAmt: number = bal.quantity_borrowed ?? bal.borrow_quantity ?? 0;
          if (amt > 0) {
            lendItems.push({ label: sym, tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint) }], totalUsdValue: bal.usd_value ?? null, metadata: { apy: bal.deposit_apy ?? null } });
          }
          if (borrowAmt > 0 || side === 'borrow') {
            const ba = borrowAmt || amt;
            borrowItems.push({
              label: sym,
              tokens: [{ symbol: sym, amount: ba, logoUri: this.resolveTokenLogo(sym, mint) }],
              totalUsdValue: bal.usd_value ?? null,
              metadata: { apy: bal.borrow_apy ?? null, ...riskMeta },
            });
          }
        }
      }

      const positions: ProtocolPosition[] = [];
      if (lendItems.length > 0) {
        positions.push({ protocolId: 'marginfi', protocolName: 'MarginFi', protocolLogoUri: logo, category: 'lending', positions: lendItems, totalUsdValue: lendItems.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0) });
      }
      if (borrowItems.length > 0) {
        positions.push({ protocolId: 'marginfi', protocolName: 'MarginFi', protocolLogoUri: logo, category: 'borrowing', positions: borrowItems, totalUsdValue: borrowItems.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0) });
      }
      return positions;
    } catch {
      return [];
    }
  }

  // ──── Orca Whirlpool LP Positions (via authenticated backend) ────

  async getOrcaPositions(): Promise<ProtocolPosition[]> {
    try {
      const res = await firstValueFrom(
        this.apiService.post<any>('/actions/build', {
          action_type: 'orca_get_user_positions',
          params: {},
        })
      );
      const rawPositions: any[] = res?.data?.positions ?? [];
      if (!rawPositions.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('orca');
      const items: PositionItem[] = [];

      for (const pos of rawPositions) {
        const flatPositions: any[] = pos.type === 'bundle' ? (pos.positions ?? []) : [pos];
        for (const p of flatPositions) {
          const poolKey: string = p.whirlpool ?? '';
          items.push({
            label: `Orca LP (${poolKey.slice(0, 6)}...)`,
            tokens: [],
            totalUsdValue: null,
            metadata: {
              whirlpool: poolKey,
              liquidity: p.liquidity ?? '0',
              priceLower: p.priceLower ?? null,
              priceUpper: p.priceUpper ?? null,
              feeOwedA: p.feeOwedA ?? 0,
              feeOwedB: p.feeOwedB ?? 0,
            },
          });
        }
      }

      if (!items.length) return [];
      return [{
        protocolId: 'orca',
        protocolName: 'Orca Whirlpool',
        protocolLogoUri: logo,
        category: 'liquidity-pool',
        positions: items,
        totalUsdValue: 0,
      }];
    } catch {
      return [];
    }
  }

  // ──── Raydium CLMM Positions ────

  async getRaydiumClmmPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(
        `${RAYDIUM_V3_API}/position/list?ownerAddress=${wallet}`,
        { signal: controller.signal }
      );
      clearTimeout(timeout);
      if (!res.ok) return [];
      const json = await res.json() as any;
      const data: any[] = json?.data?.data ?? json?.data ?? [];
      if (!data.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('raydium');
      const items: PositionItem[] = data.map((pos: any) => {
        const mintA: string | null = pos.poolInfo?.mintA?.address ?? null;
        const mintB: string | null = pos.poolInfo?.mintB?.address ?? null;
        const mintASymbol: string = pos.poolInfo?.mintA?.symbol ?? 'Token A';
        const mintBSymbol: string = pos.poolInfo?.mintB?.symbol ?? 'Token B';
        const decA: number = pos.poolInfo?.mintA?.decimals ?? 9;
        const decB: number = pos.poolInfo?.mintB?.decimals ?? 6;
        const amtA = Number(pos.tokenAmountA ?? 0) / Math.pow(10, decA);
        const amtB = Number(pos.tokenAmountB ?? 0) / Math.pow(10, decB);
        const feeA = Number(pos.tokenFeeAmountA ?? 0) / Math.pow(10, decA);
        const feeB = Number(pos.tokenFeeAmountB ?? 0) / Math.pow(10, decB);
        const inRange: boolean = pos.inRange ?? false;
        return {
          label: `${mintASymbol}/${mintBSymbol} ${inRange ? '● In Range' : '○ Out of Range'}`,
          tokens: [
            { symbol: mintASymbol, amount: amtA, logoUri: pos.poolInfo?.mintA?.logoURI ?? this.resolveTokenLogo(mintASymbol, mintA) },
            { symbol: mintBSymbol, amount: amtB, logoUri: pos.poolInfo?.mintB?.logoURI ?? this.resolveTokenLogo(mintBSymbol, mintB) },
          ],
          totalUsdValue: null,
          metadata: {
            poolId: pos.poolId ?? '',
            priceLower: pos.priceLower ?? null,
            priceUpper: pos.priceUpper ?? null,
            inRange: inRange ? 1 : 0,
            feeOwedA: feeA,
            feeOwedB: feeB,
          },
        };
      });

      return [{
        protocolId: 'raydium',
        protocolName: 'Raydium CLMM',
        protocolLogoUri: logo,
        category: 'liquidity-pool',
        positions: items,
        totalUsdValue: 0,
      }];
    } catch {
      return [];
    }
  }

  // ──── Meteora DLMM Positions ────

  /**
   * Fetch open DLMM positions from Meteora datapi. The legacy
   * `dlmm-api.meteora.ag/position/user/<wallet>` route was retired in early
   * 2026; the replacement is `/portfolio/open?user=<wallet>`, which groups
   * positions by pool and reports per-position bin range, deposited amounts
   * and unclaimed fees in raw units (decimals supplied by the pool).
   *
   * Response shape (defensive parsing — fields may be camelCase or
   * snake_case depending on indexer build):
   *   { pools: [{
   *       address|pool_address, name, token_x{address,symbol,decimals},
   *       token_y{...}, current_price, tvl,
   *       positions: [{
   *         address, lower_bin_id, upper_bin_id,
   *         total_x_amount, total_y_amount,
   *         fee_x, fee_y,
   *         total_usd_value
   *       }]
   *   }] }
   */
  async getMeteoraPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(
        `${METEORA_DATAPI}/portfolio/open?user=${wallet}`,
        { signal: controller.signal }
      );
      clearTimeout(timeout);
      if (!res.ok) return [];
      const data = await res.json() as any;
      const pools: any[] = Array.isArray(data?.pools) ? data.pools
        : Array.isArray(data) ? data
        : (data?.positions ?? data?.userPositions ?? []);
      if (!pools.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('meteora');
      const items: PositionItem[] = [];

      for (const pool of pools) {
        const tokenX = pool.token_x ?? pool.tokenX ?? {};
        const tokenY = pool.token_y ?? pool.tokenY ?? {};
        const symA: string = tokenX.symbol ?? pool.name?.split('-')?.[0]?.trim() ?? 'A';
        const symB: string = tokenY.symbol ?? pool.name?.split('-')?.[1]?.trim() ?? 'B';
        const mintA: string | null = tokenX.address ?? pool.mint_x ?? null;
        const mintB: string | null = tokenY.address ?? pool.mint_y ?? null;
        const decX: number = tokenX.decimals ?? pool.decimals_x ?? 9;
        const decY: number = tokenY.decimals ?? pool.decimals_y ?? 9;
        const pairAddress: string = pool.address ?? pool.pool_address ?? '';
        const pairName: string = pool.name ?? `${symA}-${symB}`;

        const positions: any[] = pool.positions ?? pool.userPositions ?? [];
        // No per-pool position list: treat the pool itself as a single row.
        const list = positions.length > 0 ? positions : [pool];

        for (const pos of list) {
          const amtX = Number(pos.total_x_amount ?? pos.totalXAmount ?? 0) / Math.pow(10, decX);
          const amtY = Number(pos.total_y_amount ?? pos.totalYAmount ?? 0) / Math.pow(10, decY);
          const feeX = Number(pos.fee_x ?? pos.total_fee_x_pending ?? pos.feeX ?? 0) / Math.pow(10, decX);
          const feeY = Number(pos.fee_y ?? pos.total_fee_y_pending ?? pos.feeY ?? 0) / Math.pow(10, decY);
          const usdValue: number | null =
            pos.total_usd_value != null ? Number(pos.total_usd_value)
            : pos.usd_value != null ? Number(pos.usd_value)
            : pos.totalUsdValue != null ? Number(pos.totalUsdValue)
            : null;
          items.push({
            label: pairName,
            tokens: [
              { symbol: symA, amount: amtX, logoUri: this.resolveTokenLogo(symA, mintA) },
              { symbol: symB, amount: amtY, logoUri: this.resolveTokenLogo(symB, mintB) },
            ],
            totalUsdValue: usdValue,
            metadata: {
              pairAddress,
              lowerBinId: pos.lower_bin_id ?? pos.lowerBinId ?? null,
              upperBinId: pos.upper_bin_id ?? pos.upperBinId ?? null,
              feeOwedA: feeX,
              feeOwedB: feeY,
            },
          });
        }
      }

      if (!items.length) return [];
      const totalUsdValue = items.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0);
      return [{
        protocolId: 'meteora',
        protocolName: 'Meteora DLMM',
        protocolLogoUri: logo,
        category: 'liquidity-pool',
        positions: items,
        totalUsdValue,
      }];
    } catch {
      return [];
    }
  }

  // ──── Drift Perpetuals & Spot Positions ────

  async getDriftPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(
        `${DRIFT_API}/user?authority=${wallet}`,
        { signal: controller.signal }
      );
      clearTimeout(timeout);
      if (!res.ok) return [];
      const raw = await res.json() as any;
      const user = Array.isArray(raw) ? raw[0] : raw;
      if (!user) return [];

      const logo = this.protocolDetection.getProtocolLogo('drift');
      const items: PositionItem[] = [];

      // Spot balances
      for (const bal of (user.spotPositions ?? user.spot_positions ?? [])) {
        const sym: string = bal.symbol ?? bal.token_symbol ?? `Spot #${bal.marketIndex ?? bal.market_index ?? '?'}`;
        const mint: string | null = bal.mint ?? bal.token_mint ?? null;
        const amt = Number(bal.scaledBalance ?? bal.balance ?? 0);
        if (amt === 0) continue;
        const usd = Number(bal.usdValue ?? bal.usd_value ?? 0) || null;
        items.push({
          label: `${sym} (Spot)`,
          tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint) }],
          totalUsdValue: usd,
          metadata: { type: 'spot', market: bal.marketIndex ?? bal.market_index ?? null },
        });
      }

      // Perp positions
      for (const pos of (user.perpPositions ?? user.perp_positions ?? [])) {
        const marketIdx = pos.marketIndex ?? pos.market_index ?? '?';
        const baseAmt = Number(pos.baseAssetAmount ?? pos.base_asset_amount ?? 0);
        const sizeLots = baseAmt / 1e9;
        if (sizeLots === 0) continue;
        const pnl = Number(pos.unsettledPnl ?? pos.unsettled_pnl ?? pos.quoteAssetAmount ?? 0) / 1e6;
        const side = sizeLots > 0 ? 'Long' : 'Short';
        items.push({
          label: `Perp #${marketIdx} ${side}`,
          // Perp markets don't map to a single SPL mint; fall back to the
          // generic Drift logo so the row isn't iconless.
          tokens: [{ symbol: `PERP-${marketIdx}`, amount: Math.abs(sizeLots), logoUri: this.protocolDetection.getProtocolLogo('drift') }],
          totalUsdValue: pnl !== 0 ? pnl : null,
          metadata: { type: 'perp', market: marketIdx, side: side.toLowerCase(), size: Math.abs(sizeLots), pnl },
        });
      }

      if (!items.length) return [];
      return [{
        protocolId: 'drift',
        protocolName: 'Drift',
        protocolLogoUri: logo,
        category: 'perpetuals',
        positions: items,
        totalUsdValue: items.reduce((s, p) => s + (p.totalUsdValue ?? 0), 0),
      }];
    } catch {
      return [];
    }
  }

  // ──── Streamflow Streams & Vesting ────

  async getStreamflowPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      const res = await fetch(
        `${STREAMFLOW_API}/solana-mainnet/streams?recipient=${wallet}&sender=${wallet}&offset=0&limit=50`,
        { signal: controller.signal }
      );
      clearTimeout(timeout);
      if (!res.ok) return [];
      const raw = await res.json() as any;
      const streams: any[] = Array.isArray(raw) ? raw : (raw?.streams ?? raw?.data ?? []);
      const active = streams.filter((s: any) => !s.closed && !s.cancelled);
      if (!active.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('streamflow');
      const items: PositionItem[] = active.map((s: any) => {
        const decimals: number = s.decimals ?? 6;
        const deposited = Number(s.deposited_amount ?? 0);
        const withdrawn = Number(s.withdrawn_amount ?? 0);
        const remaining = Math.max(0, deposited - withdrawn) / Math.pow(10, decimals);
        const mint: string | null = s.mint ?? null;
        const sym: string = s.token_symbol ?? (mint ? mint.slice(0, 6) + '...' : 'TOKEN');
        const isSender: boolean = (s.sender ?? '').toLowerCase() === wallet.toLowerCase();
        const role = isSender ? 'Sender' : 'Recipient';
        return {
          label: s.name || `Stream ${(s.id ?? s.publicKey ?? '').slice(0, 8)}`,
          tokens: [{ symbol: sym, amount: remaining, logoUri: this.resolveTokenLogo(sym, mint) }],
          totalUsdValue: null,
          metadata: {
            role,
            streamId: s.id ?? s.publicKey ?? '',
            sender: s.sender ?? '',
            recipient: s.recipient ?? '',
            startTime: s.start ?? null,
            endTime: s.end ?? null,
            cliff: s.cliff ?? null,
          },
        };
      });

      return [{
        protocolId: 'streamflow',
        protocolName: 'Streamflow',
        protocolLogoUri: logo,
        category: 'streaming',
        positions: items,
        totalUsdValue: 0,
      }];
    } catch {
      return [];
    }
  }

  private async fetchRaydiumPairs(): Promise<RaydiumPair[]> {
    const now = Date.now();
    if (this.raydiumPairsCache && now - this.raydiumCacheTimestamp < this.CACHE_TTL) {
      return this.raydiumPairsCache;
    }

    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      const response = await fetch(RAYDIUM_PAIRS_URL, { signal: controller.signal });
      clearTimeout(timeout);
      if (!response.ok) return [];
      const data = await response.json() as RaydiumPair[];
      const pairs: RaydiumPair[] = Array.isArray(data) ? data : [];
      this.raydiumPairsCache = pairs;
      this.raydiumCacheTimestamp = now;
      return pairs;
    } catch {
      return [];
    }
  }
}
