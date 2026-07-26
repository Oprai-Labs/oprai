import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ProtocolDetectionService } from './protocol-detection.service';
import { JupiterLendService, type LendPosition } from '@core/services/market/jupiter-lend.service';
import { JupiterPortfolioService } from '@core/services/market/jupiter-portfolio.service';
import { ApiService } from '@core/services/api.service';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { BirdeyeService } from './birdeye.service';
import { LiveYieldsService } from './live-yields.service';
import { SolanaRpcService } from './solana-rpc.service';
import type {
  EnhancedTokenAccount,
  ProtocolPosition,
  PositionItem,
  ProtocolCategory,
  JupiterPortfolioElement,
  JupiterPortfolioTokenInfo,
  PumpfunCreatorRewards,
} from '../models/portfolio.models';

// Kamino markets list — `/v2/kamino-market` returns ~6KB of all markets
// (Main, JLP, Altcoins, Ethena, Jito, etc.). Cached for 5min so we only
// pay the ~370ms once per session. Required for per-market obligation
// lookups, since the legacy `/v2/user-metadata/<wallet>` catch-all was
// retired in 2026.
const KAMINO_MARKETS_URL = 'https://api.kamino.finance/v2/kamino-market';
// Meteora datapi — replaces deprecated `dlmm-api.meteora.ag/position/user/<wallet>`
// (returns 404 since early 2026). The new portfolio/open endpoint groups
// positions by pool: { pools: [{ pool_address, name, positions: [...], ... }] }.
const METEORA_DATAPI = 'https://dlmm.datapi.meteora.ag';

interface KaminoMarketEntry {
  pubkey: string;
  name: string;
}

interface OrcaWhirlpoolMeta {
  tokenAMint: string;
  tokenBMint: string;
  tokenADecimals: number;
  tokenBDecimals: number;
  tokenASymbol: string;
  tokenBSymbol: string;
  sqrtPrice: number;
  price: number;
  tickCurrentIndex: number;
}

@Injectable({ providedIn: 'root' })
export class DefiPositionsService {
  private readonly protocolDetection = inject(ProtocolDetectionService);
  private readonly jupiterLend = inject(JupiterLendService);

  // Last-known-good Jupiter Lend supply positions per wallet. Lets a transient
  // lite-api failure fall back to the previously-fetched supply instead of
  // dropping the user's real position — a clean (successful) empty result
  // still overwrites it, so a genuinely closed position clears correctly.
  private readonly _lastLendSupply = new Map<string, LendPosition[]>();
  private readonly jupiterPortfolio = inject(JupiterPortfolioService);
  private readonly apiService = inject(ApiService);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly birdeyeService = inject(BirdeyeService);
  private readonly liveYields = inject(LiveYieldsService);
  private readonly solanaRpc = inject(SolanaRpcService);
  // Kamino markets list cache (~6KB) — refreshed every 5min so we don't
  // re-fetch the same 8-market catalog on every portfolio refresh.
  private kaminoMarketsCache: KaminoMarketEntry[] | null = null;
  private kaminoMarketsCacheTs = 0;
  private kaminoMarketsCachePromise: Promise<KaminoMarketEntry[]> | null = null;
  private readonly KAMINO_MARKETS_TTL = 300_000;

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
    // Raydium Standard AMM / CPMM LP positions are held as LP tokens (not a
    // position NFT). The old detection downloaded the 493 MB /main/pairs dump;
    // instead, map the wallet's token mints against Raydium's per-mint
    // `pools/info/lps` endpoint (returns only the pools whose lpMint matches).
    try {
      const held = (tokenAccounts ?? []).filter(t => t.mint && (t.balance ?? 0) > 0);
      if (!held.length) return [];
      const res = await fetch(
        `https://api-v3.raydium.io/pools/info/lps?lps=${held.map(t => t.mint).join(',')}`,
      );
      if (!res.ok) return [];
      const json = await res.json();
      const pools = ((json?.data ?? []) as Array<Record<string, unknown>>).filter(Boolean);
      if (!pools.length) return [];
      const logo = this.protocolDetection.getProtocolLogo('raydium');
      const out: ProtocolPosition[] = [];
      for (const pool of pools) {
        const lpMintObj = pool['lpMint'] as { address?: string } | string | undefined;
        const lpMint = typeof lpMintObj === 'string' ? lpMintObj : lpMintObj?.address;
        const bal = held.find(t => t.mint === lpMint);
        if (!bal) continue;
        const mintA = pool['mintA'] as { symbol?: string; address?: string; logoURI?: string } | undefined;
        const mintB = pool['mintB'] as { symbol?: string; address?: string; logoURI?: string } | undefined;
        const supply = Number(pool['lpAmount']) || 0;
        const tvl = Number(pool['tvl']) || 0;
        // User's share of the pool: their LP tokens / total LP supply.
        const share = supply > 0 ? bal.balance / supply : 0;
        const value = share * tvl || null;
        // Underlying token breakdown = share × the pool's live reserves
        // (mintAmountA/B are already in UI units). Without this the position card
        // showed "0.0000 WSOL / 0.0000 USDC" even though the USD value resolved.
        const reserveA = Number(pool['mintAmountA']) || 0;
        const reserveB = Number(pool['mintAmountB']) || 0;
        const amountA = share * reserveA;
        const amountB = share * reserveB;
        const apr = (pool['day'] as { apr?: number } | undefined)?.apr ?? null;
        const pair = `${mintA?.symbol ?? '?'}/${mintB?.symbol ?? '?'}`;
        out.push({
          protocolId: 'raydium',
          protocolName: 'Raydium',
          protocolLogoUri: logo,
          category: 'liquidity-pool',
          positions: [{
            label: pair,
            tokens: [
              { symbol: mintA?.symbol ?? '?', amount: amountA, logoUri: mintA?.logoURI ?? null, mint: mintA?.address },
              { symbol: mintB?.symbol ?? '?', amount: amountB, logoUri: mintB?.logoURI ?? null, mint: mintB?.address },
            ],
            totalUsdValue: value,
            metadata: { poolId: String(pool['id'] ?? ''), lpMint: lpMint ?? '', lpAmount: bal.balance },
            apy: apr,
          }],
          totalUsdValue: value ?? 0,
        });
      }
      return out;
    } catch {
      return [];
    }
  }

  async getLendingPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      // Kick off all three concurrently. Supply is awaited separately (not in
      // the Promise.all) because it THROWS on a hard API failure — vs. returning
      // [] for genuinely no position — so we can fall back to the last-known-good
      // supply through a transient lite-api hiccup instead of dropping the user's
      // real ~$77 Lend position. A clean (successful) empty still overwrites the
      // cache, so a genuinely closed position clears.
      const earnP = this.jupiterLend.getAllEarnPositions(wallet);
      const borrowP = this.jupiterLend.getBorrowPositions(wallet);
      // Borrow-market supply (collateral earning yield, e.g. 1 wSOL supplied
      // with 0 borrowed) — Jupiter's UI shows it under "Lending". The catch is
      // attached at creation (not a later await) so an early rejection never
      // surfaces as an unhandled promise rejection.
      const supplyP = this.jupiterLend
        .getLendSupplyPositions(wallet)
        .then((pos) => {
          this._lastLendSupply.set(wallet, pos);
          return pos;
        })
        .catch(() => this._lastLendSupply.get(wallet) ?? []);

      const [earnPositions, borrowPositions, supplyPositions] = await Promise.all([
        earnP,
        borrowP,
        supplyP,
      ]);

      const positions: ProtocolPosition[] = [];
      const logo = this.protocolDetection.getProtocolLogo('jupiter');

      // Drop empty/dust earn accounts — a fully-withdrawn Jupiter Lend position
      // leaves a 0-balance account behind, which rendered as a phantom
      // "$0.00 / 0.0000" row. Anything that rounds to 0 at display precision is
      // not a real position. Merge the Earn/Vault balances with the borrow-market
      // supply into one "Jupiter Lend" grouping.
      const liveEarn = [
        ...earnPositions.filter(p => (p.depositedAmount ?? 0) >= 0.00005),
        ...supplyPositions,
      ];
      if (liveEarn.length > 0) {
        const items: PositionItem[] = liveEarn.map(p => ({
          label: p.asset.symbol,
          tokens: [{
            symbol: p.asset.symbol,
            amount: p.depositedAmount,
            logoUri: this.resolveTokenLogo(p.asset.symbol, p.asset.mint),
            mint: p.asset.mint,
          }],
          totalUsdValue: null,
          metadata: { depositedAmount: p.depositedAmount },
          // Surface the lend APY at the row level so the rewards dashboard
          // can render it without digging into protocol-specific metadata.
          apy: p.apy ?? null,
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
            { symbol: p.collateralAsset.symbol, amount: p.collateralAmount, logoUri: this.resolveTokenLogo(p.collateralAsset.symbol, p.collateralAsset.mint), mint: p.collateralAsset.mint },
            { symbol: p.debtAsset.symbol, amount: p.debtAmount, logoUri: this.resolveTokenLogo(p.debtAsset.symbol, p.debtAsset.mint), mint: p.debtAsset.mint },
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

      // LSTs are roughly 1:1 with SOL, use token's usdValue if available
      const usdValue = token.usdValue ?? (solPrice ? token.balance * solPrice : null);

      // Hide dust positions (< 1 cent). These are leftovers from rounding
      // or test transactions and just clutter the DeFi tab with rows that
      // read "0.0000 jupSOL  $0.00" — actively misleading because they
      // suggest the user is staking when in practice they're not.
      if (usdValue !== null && usdValue < 0.01) continue;

      const protocol = info.protocol;
      const items = protocolMap.get(protocol) ?? [];

      // Live APY from DefiLlama yields snapshot (refreshed in loadPortfolio
      // before this method runs). Fall back to the registry default — kept
      // only as a static safety net so a DefiLlama outage doesn't blank the
      // APY column. The fallback values are explicitly stale, not invented.
      const liveApy = this.liveYields.getApyByMint(token.mint);
      const registryDefault = this.protocolDetection.getLstDefaultApy(token.mint);

      items.push({
        label: info.name,
        tokens: [{ symbol: info.symbol, amount: token.balance, logoUri: token.logoUri, mint: token.mint }],
        totalUsdValue: usdValue,
        metadata: {},
        apy: liveApy ?? registryDefault,
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
    // Kamino dropped the legacy `/v2/user-metadata/<wallet>/obligations`
    // catch-all in 2026. Current API requires a *per-market* lookup:
    //   /kamino-market/<marketPubkey>/users/<wallet>/obligations
    // We fetch the markets list (cached 5min), fan out one parallel request
    // per market, then merge. This is still 200ms–500ms per market with
    // an 8-market portfolio, all parallel — so the whole call resolves in
    // ~600ms total, comfortably under our 10s per-protocol budget.
    try {
      const markets = await this.fetchKaminoMarkets();
      if (markets.length === 0) return [];

      const obligationFetches = markets.map(async (market) => {
        try {
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), 5_000);
          const res = await fetch(
            `https://api.kamino.finance/kamino-market/${market.pubkey}/users/${wallet}/obligations`,
            { signal: controller.signal },
          );
          clearTimeout(timeout);
          if (!res.ok) return [];
          const json = await res.json() as any;
          const arr = Array.isArray(json) ? json : (json?.data ?? []);
          // Tag each obligation with its market name so the position label
          // can render "USDC — Main Market" / "JLP Market" etc.
          return arr.map((obl: any) => ({ ...obl, _marketName: market.name }));
        } catch {
          return [];
        }
      });

      const results = await Promise.all(obligationFetches);
      const obligations: any[] = results.flat();
      if (!obligations.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('kamino') ?? null;
      const supplyItems: PositionItem[] = [];
      const borrowItems: PositionItem[] = [];

      for (const obl of obligations) {
        const market = obl._marketName ?? obl.marketName ?? obl.lendingMarket ?? 'Kamino Market';
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
            supplyItems.push({
              label: `${sym} — ${market}`,
              tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint), mint: mint ?? undefined }],
              totalUsdValue: dep.usdValue ?? null,
              metadata: {},
              apy: dep.apy != null ? Number(dep.apy) : null,
            });
          }
        }
        for (const bor of (obl.borrows ?? obl.liabilities ?? [])) {
          const sym: string = bor.symbol ?? bor.mintSymbol ?? 'UNKNOWN';
          const mint: string | null = bor.mint ?? bor.mintAddress ?? null;
          const amt: number = bor.amount ?? bor.borrowedAmount ?? 0;
          if (amt > 0) {
            borrowItems.push({
              label: `${sym} — ${market}`,
              tokens: [{ symbol: sym, amount: amt, logoUri: this.resolveTokenLogo(sym, mint), mint: mint ?? undefined }],
              totalUsdValue: bor.usdValue ?? null,
              metadata: { ...riskMeta },
              // Borrow APY is a cost, not yield — render it for transparency
              // but the rewards-dashboard's weighted average treats it as
              // negative naturally because the position's USD value is
              // negative in the price pass.
              apy: bor.apy != null ? Number(bor.apy) : null,
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

  /**
   * MarginFi v2 detailed balances via the TS service (mrgnLabs SDK).
   * Returns one PositionItem per active balance, split across `lending`
   * (deposits) and `borrowing` (debts) ProtocolPositions so the UI groups
   * them the same way Kamino / Jupiter Lend already do.
   */
  async getMarginFiPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const resp = await firstValueFrom(
        this.apiService.post<{ data?: { balances?: any[] } }>(
          '/actions/build',
          { action_type: 'marginfi_user_balances', params: {} },
        ),
      );
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const balances: any[] = (resp as any)?.data?.balances ?? [];
      if (balances.length === 0) return [];

      const logo = this.protocolDetection.getProtocolLogo('marginfi');
      const lendItems: PositionItem[] = [];
      const borrowItems: PositionItem[] = [];

      for (const b of balances) {
        const amount = Number(b.amount ?? 0);
        if (!(amount > 0)) continue;
        const usd = Number(b.usdValue ?? 0);
        const apy = Number(b.apy ?? 0);
        const item: PositionItem = {
          label: `${b.tokenSymbol} (${b.accountAddress?.slice(0, 6) ?? 'mfi'}…)`,
          tokens: [{
            symbol: b.tokenSymbol,
            amount,
            logoUri: this.resolveTokenLogo(b.tokenSymbol, b.tokenMint),
            mint: b.tokenMint,
          }],
          totalUsdValue: usd > 0 ? usd : null,
          metadata: {
            healthFactor: b.healthFactor != null ? Number(b.healthFactor) : null,
            account: b.accountAddress ?? null,
            bank: b.bankAddress ?? null,
            weight: b.weight != null ? Number(b.weight) : null,
          },
          apy: Number.isFinite(apy) ? apy : null,
        };
        if (b.side === 'borrow') borrowItems.push(item);
        else lendItems.push(item);
      }

      const out: ProtocolPosition[] = [];
      if (lendItems.length > 0) {
        out.push({
          protocolId: 'marginfi',
          protocolName: 'MarginFi',
          protocolLogoUri: logo,
          category: 'lending',
          positions: lendItems,
          totalUsdValue: 0,
        });
      }
      if (borrowItems.length > 0) {
        out.push({
          protocolId: 'marginfi',
          protocolName: 'MarginFi',
          protocolLogoUri: logo,
          category: 'borrowing',
          positions: borrowItems,
          totalUsdValue: 0,
        });
      }
      return out;
    } catch {
      return [];
    }
  }

  // ──── Orca Whirlpool LP Positions (via authenticated backend) ────

  // Cache the Orca whirlpool list (~14k entries, ~600KB gzipped) for 5min.
  // Single fetch per session amortises across all positions / refreshes.
  private orcaWhirlpoolCache: Map<string, OrcaWhirlpoolMeta> | null = null;
  private orcaWhirlpoolCacheTs = 0;
  private orcaWhirlpoolCachePromise: Promise<Map<string, OrcaWhirlpoolMeta>> | null = null;

  private async fetchOrcaWhirlpoolMap(): Promise<Map<string, OrcaWhirlpoolMeta>> {
    const now = Date.now();
    if (this.orcaWhirlpoolCache && now - this.orcaWhirlpoolCacheTs < 5 * 60_000) {
      return this.orcaWhirlpoolCache;
    }
    if (this.orcaWhirlpoolCachePromise) return this.orcaWhirlpoolCachePromise;

    this.orcaWhirlpoolCachePromise = (async () => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 10_000);
        const res = await fetch('https://api.mainnet.orca.so/v1/whirlpool/list', { signal: ctrl.signal });
        clearTimeout(t);
        if (!res.ok) return new Map<string, OrcaWhirlpoolMeta>();
        const json = await res.json() as { whirlpools?: any[] };
        const map = new Map<string, OrcaWhirlpoolMeta>();
        for (const w of json.whirlpools ?? []) {
          if (!w?.address) continue;
          map.set(w.address, {
            tokenAMint: typeof w.tokenA === 'string' ? w.tokenA : w.tokenA?.mint ?? '',
            tokenBMint: typeof w.tokenB === 'string' ? w.tokenB : w.tokenB?.mint ?? '',
            tokenADecimals: w.tokenA?.decimals ?? 9,
            tokenBDecimals: w.tokenB?.decimals ?? 6,
            tokenASymbol: w.tokenA?.symbol ?? '',
            tokenBSymbol: w.tokenB?.symbol ?? '',
            sqrtPrice: parseFloat(w.sqrtPrice ?? '0'),
            price: w.price ?? 0,
            tickCurrentIndex: w.tickCurrentIndex ?? 0,
          });
        }
        this.orcaWhirlpoolCache = map;
        this.orcaWhirlpoolCacheTs = Date.now();
        return map;
      } catch {
        return new Map<string, OrcaWhirlpoolMeta>();
      } finally {
        this.orcaWhirlpoolCachePromise = null;
      }
    })();
    return this.orcaWhirlpoolCachePromise;
  }

  /**
   * CLMM token-amount math. Given the user's liquidity L plus the position's
   * tick range and the pool's current sqrt price, return how much of token A
   * and token B the position currently holds. Mirrors the Whirlpool SDK's
   * `getTokenAmountsFromLiquidity` so we don't have to ship the 200KB SDK
   * just for this calculation.
   */
  private orcaPositionAmounts(
    liquidity: number,
    tickLower: number,
    tickUpper: number,
    tickCurrent: number,
    decA: number,
    decB: number,
  ): { amountA: number; amountB: number } {
    const sqrtPriceLower = Math.pow(1.0001, tickLower / 2);
    const sqrtPriceUpper = Math.pow(1.0001, tickUpper / 2);
    const sqrtPriceCurrent = Math.pow(1.0001, tickCurrent / 2);
    let rawA = 0;
    let rawB = 0;
    if (tickCurrent < tickLower) {
      // All liquidity in token A
      rawA = liquidity * (sqrtPriceUpper - sqrtPriceLower) / (sqrtPriceLower * sqrtPriceUpper);
    } else if (tickCurrent >= tickUpper) {
      // All liquidity in token B
      rawB = liquidity * (sqrtPriceUpper - sqrtPriceLower);
    } else {
      // Split — current price in range
      rawA = liquidity * (sqrtPriceUpper - sqrtPriceCurrent) / (sqrtPriceCurrent * sqrtPriceUpper);
      rawB = liquidity * (sqrtPriceCurrent - sqrtPriceLower);
    }
    return {
      amountA: rawA / Math.pow(10, decA),
      amountB: rawB / Math.pow(10, decB),
    };
  }

  async getOrcaPositions(): Promise<ProtocolPosition[]> {
    try {
      // Step 1: ask the backend for Orca positions only. The whirlpool
      // metadata list is ~18MB and 5s on the wire — skipping it whenever
      // the wallet has no Orca positions (the common case) saves both
      // bandwidth and time on the critical-path refresh.
      const res = await firstValueFrom(
        this.apiService.post<any>('/actions/build', {
          action_type: 'orca_get_user_positions',
          params: {},
        })
      );
      const rawPositions: any[] = res?.data?.positions ?? [];
      if (!rawPositions.length) return [];

      // Step 2: positions exist → now we need pool metadata for amount math.
      const whirlpoolMap = await this.fetchOrcaWhirlpoolMap();

      const logo = this.protocolDetection.getProtocolLogo('orca');
      const items: PositionItem[] = [];

      for (const pos of rawPositions) {
        const flatPositions: any[] = pos.type === 'bundle' ? (pos.positions ?? []) : [pos];
        for (const p of flatPositions) {
          const poolKey: string = p.whirlpool ?? '';
          const wp = whirlpoolMap.get(poolKey);
          const tickLower = p.tickLowerIndex ?? 0;
          const tickUpper = p.tickUpperIndex ?? 0;
          const liquidity = parseFloat(p.liquidity ?? '0');
          const tickCurrent = wp?.tickCurrentIndex ?? Math.floor((tickLower + tickUpper) / 2);
          const decA = wp?.tokenADecimals ?? 9;
          const decB = wp?.tokenBDecimals ?? 6;
          const symA = wp?.tokenASymbol || (wp?.tokenAMint ? wp.tokenAMint.slice(0, 4) + '…' : 'A');
          const symB = wp?.tokenBSymbol || (wp?.tokenBMint ? wp.tokenBMint.slice(0, 4) + '…' : 'B');
          const inRange = tickCurrent >= tickLower && tickCurrent < tickUpper;
          const { amountA, amountB } = this.orcaPositionAmounts(
            liquidity, tickLower, tickUpper, tickCurrent, decA, decB,
          );
          // Add unclaimed fees on top of position amounts so totalUsdValue
          // captures both notional + earned fees.
          const feeA = (Number(p.feeOwedA ?? 0)) / Math.pow(10, decA);
          const feeB = (Number(p.feeOwedB ?? 0)) / Math.pow(10, decB);

          // Resolve unit prices for both legs so we can derive the
          // claimable USD without leaning on the post-process price pass
          // (priceAllPositions trusts our totalUsdValue when present, so
          // we'd lose the fee signal entirely if we baked them into
          // amountA/amountB). Birdeye prices arrive later asynchronously
          // — for the first render we use null and let the post-process
          // step fill in.
          items.push({
            label: `${symA}/${symB} ${inRange ? '● In Range' : '○ Out of Range'}`,
            tokens: [
              {
                symbol: symA,
                amount: amountA,
                logoUri: this.resolveTokenLogo(symA, wp?.tokenAMint ?? null),
                mint: wp?.tokenAMint || undefined,
              },
              {
                symbol: symB,
                amount: amountB,
                logoUri: this.resolveTokenLogo(symB, wp?.tokenBMint ?? null),
                mint: wp?.tokenBMint || undefined,
              },
            ],
            totalUsdValue: null, // priceAllPositions fills this in
            metadata: {
              whirlpool: poolKey,
              liquidity: p.liquidity ?? '0',
              priceLower: p.priceLower ?? null,
              priceUpper: p.priceUpper ?? null,
              feeOwedA: feeA,
              feeOwedB: feeB,
              inRange: inRange ? 1 : 0,
            },
            // First-class signals for the rewards dashboard. priceAllPositions
            // recomputes claimableUsd in its second pass with Birdeye prices
            // when both fee legs have mints.
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            claimableUsd: (() => {
              const mintsKnown = Boolean(wp?.tokenAMint && wp?.tokenBMint);
              return mintsKnown ? null : (feeA + feeB > 0 ? 0 : null);
            })(),
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

  /**
   * Raydium farm-stake + locked-CLMM positions via the Rust backend.
   * Active **unlocked** CLMM positions still aren't covered — those are
   * NFT-based and need a Helius DAS scan + per-position PDA decode
   * (parallel to the Orca whirlpool implementation). Until that ships,
   * this fills in the two surfaces that do have a public owner-API.
   */
  async getRaydiumClmmPositions(_wallet: string): Promise<ProtocolPosition[]> {
    try {
      const logo = this.protocolDetection.getProtocolLogo('raydium');
      const out: ProtocolPosition[] = [];

      // Run both backend builders in parallel — the connected wallet is
      // already on the auth context, so neither call needs an explicit
      // `wallet` param.
      const [farmRes, lockedRes] = await Promise.allSettled([
        firstValueFrom(
          this.apiService.post<{ data?: { params?: any } }>('/actions/build', {
            action_type: 'raydium_get_user_positions',
            params: {},
          }),
        ),
        firstValueFrom(
          this.apiService.post<{ data?: { params?: any } }>('/actions/build', {
            action_type: 'raydium_get_clmm_positions',
            params: {},
          }),
        ),
      ]);

      // Farm positions surface as ProtocolPosition under category:'lending'
      // (closest existing bucket — Raydium farm staking is "deposit token,
      // earn rewards" semantically). Each item carries pending rewards as
      // claimableUsd.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const farmPayload: any = farmRes.status === 'fulfilled'
        ? (farmRes.value as any)?.data?.params
        : null;
      const farms: any[] = Array.isArray(farmPayload?.data) ? farmPayload.data
        : (Array.isArray(farmPayload) ? farmPayload : []);
      const farmItems: PositionItem[] = farms
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .map((f: any): PositionItem | null => {
          const stakedAmount = Number(f.stakedAmount ?? f.staked ?? 0);
          if (!(stakedAmount > 0)) return null;
          const lpMint: string | undefined = f.lpMint ?? f.mint ?? undefined;
          const symbol: string = f.symbol ?? f.poolName ?? (lpMint ? lpMint.slice(0, 4) + '…' : 'LP');
          const apy = this.pickNum(f.apr, f.apy);
          const pending = Number(f.pendingRewardUsd ?? f.pendingReward ?? 0);
          return {
            label: symbol,
            tokens: [{ symbol, amount: stakedAmount, logoUri: this.resolveTokenLogo(symbol, lpMint ?? null), mint: lpMint }],
            totalUsdValue: this.pickNum(f.usdValue, f.value),
            metadata: { farmId: f.farmId ?? null, poolId: f.poolId ?? null },
            apy,
            claimableUsd: pending > 0 ? pending : null,
          };
        })
        .filter((p): p is PositionItem => p !== null);
      if (farmItems.length > 0) {
        out.push({
          protocolId: 'raydium',
          protocolName: 'Raydium Farms',
          protocolLogoUri: logo,
          category: 'lending',
          positions: farmItems,
          totalUsdValue: 0,
        });
      }

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const lockedPayload: any = lockedRes.status === 'fulfilled'
        ? (lockedRes.value as any)?.data?.params
        : null;
      const locks: any[] = Array.isArray(lockedPayload?.data) ? lockedPayload.data
        : (Array.isArray(lockedPayload) ? lockedPayload : []);
      const lockItems: PositionItem[] = locks
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .map((l: any): PositionItem | null => {
          const totalValue = this.pickNum(l.totalValueUsd, l.usdValue, l.value);
          const apy = this.pickNum(l.apr, l.apy);
          const claim = Number(l.unclaimedFeeUsd ?? l.unclaimedFee ?? 0);
          const mintA: string | undefined = l.tokenAMint ?? l.mintA ?? undefined;
          const mintB: string | undefined = l.tokenBMint ?? l.mintB ?? undefined;
          const symA: string = l.tokenASymbol ?? l.symbolA ?? (mintA ? mintA.slice(0, 4) + '…' : 'A');
          const symB: string = l.tokenBSymbol ?? l.symbolB ?? (mintB ? mintB.slice(0, 4) + '…' : 'B');
          const amountA = Number(l.amountA ?? 0);
          const amountB = Number(l.amountB ?? 0);
          if (totalValue == null && amountA === 0 && amountB === 0) return null;
          return {
            label: `${symA}/${symB} (locked)`,
            tokens: [
              { symbol: symA, amount: amountA, logoUri: this.resolveTokenLogo(symA, mintA ?? null), mint: mintA },
              { symbol: symB, amount: amountB, logoUri: this.resolveTokenLogo(symB, mintB ?? null), mint: mintB },
            ],
            totalUsdValue: totalValue,
            metadata: { poolId: l.poolId ?? null, lockNftMint: l.lockNftMint ?? null, unlockTs: l.unlockTs ?? null },
            apy,
            claimableUsd: claim > 0 ? claim : null,
          };
        })
        .filter((p): p is PositionItem => p !== null);
      if (lockItems.length > 0) {
        out.push({
          protocolId: 'raydium',
          protocolName: 'Raydium Locked CLMM',
          protocolLogoUri: logo,
          category: 'liquidity-pool',
          positions: lockItems,
          totalUsdValue: 0,
        });
      }

      return out;
    } catch {
      return [];
    }
  }

  // ──── Meteora DLMM Positions ────

  /**
   * Fetch open DLMM positions from Meteora datapi `/portfolio/open?user=`.
   *
   * Real response shape (verified against live API, May 2026):
   * ```
   * { totalPositions, total: { balances, balancesSol, unclaimedFees, pnl,...},
   *   solPrice,
   *   pools: [{
   *     poolAddress, binStep, baseFee,
   *     tokenXMint, tokenYMint,
   *     tokenX, tokenY,                    // symbols, top-level
   *     tokenXIcon, tokenYIcon,            // CDN urls
   *     balances, balancesSol,             // USD + SOL value of THIS pool's position
   *     unclaimedFees, unclaimedFeesSol,
   *     pnl, pnlPctChange,
   *     poolPrice,
   *     openPositionCount,
   *     listPositions: [posAddr, ...],     // just addresses, no per-position amounts
   *     outOfRange, positionsOutOfRange,
   *   }]
   * }
   * ```
   * The previous parser assumed legacy `pool.token_x.address` nesting and
   * `pool.positions[]` arrays — both wrong, which is why every row collapsed
   * to "A-B 0.0000 A 0.0000 B". Each `pools[]` element here is itself the
   * user's position in that pool (one position rolled up — Meteora UI
   * groups positions by pool too).
   */
  async getMeteoraPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 6_000);
      const res = await fetch(
        `${METEORA_DATAPI}/portfolio/open?user=${wallet}`,
        { signal: controller.signal }
      );
      clearTimeout(timeout);
      if (!res.ok) return [];
      const data = await res.json() as any;
      const pools: any[] = Array.isArray(data?.pools) ? data.pools : [];
      if (!pools.length) return [];

      const logo = this.protocolDetection.getProtocolLogo('meteora');
      const items: PositionItem[] = [];

      for (const pool of pools) {
        // Defensive: tolerate both the live shape (top-level `tokenX/Y` +
        // `tokenXMint`) and any future nested shape (`token_x.symbol`).
        const mintA: string | null = pool.tokenXMint ?? pool.token_x?.address ?? pool.mint_x ?? null;
        const mintB: string | null = pool.tokenYMint ?? pool.token_y?.address ?? pool.mint_y ?? null;
        const symA: string = pool.tokenX ?? pool.token_x?.symbol ?? (mintA ? mintA.slice(0, 4) + '…' : 'A');
        const symB: string = pool.tokenY ?? pool.token_y?.symbol ?? (mintB ? mintB.slice(0, 4) + '…' : 'B');
        const iconA: string | null = pool.tokenXIcon ?? pool.token_x?.icon ?? this.resolveTokenLogo(symA, mintA);
        const iconB: string | null = pool.tokenYIcon ?? pool.token_y?.icon ?? this.resolveTokenLogo(symB, mintB);
        if (mintA && !this.tokenRegistry.getToken(mintA)) this.tokenRegistry.resolveAsync(mintA);
        if (mintB && !this.tokenRegistry.getToken(mintB)) this.tokenRegistry.resolveAsync(mintB);

        const poolAddress: string = pool.poolAddress ?? pool.address ?? '';
        const binStep: number = pool.binStep ?? 0;
        const positionCount: number = pool.openPositionCount ?? (pool.listPositions?.length ?? 1);

        // `balances` is *human-units* of token-Y (the pair's quote leg)
        // representing the position's value in Y. To get USD: when Y is
        // SOL/USD-like we already have a price; the API also gives us
        // `balancesSol` for the SOL conversion. We treat `balances` as
        // already in USD when token Y is a USD stable, otherwise multiply
        // by the position's balancesSol × solPrice.
        const balances = parseFloat(pool.balances ?? '0');
        const balancesSol = parseFloat(pool.balancesSol ?? '0');
        const solPrice = parseFloat(data.solPrice ?? '0');
        const unclaimedFeesSol = parseFloat(pool.unclaimedFeesSol ?? '0');
        const poolPrice = parseFloat(pool.poolPrice ?? '0');

        // Total USD = position notional + unclaimed fees, both via SOL leg
        // (balancesSol is the most reliable cross-pair conversion).
        let usdValue: number | null = null;
        if (balancesSol > 0 && solPrice > 0) {
          usdValue = (balancesSol + unclaimedFeesSol) * solPrice;
        } else if (balances > 0) {
          usdValue = balances; // assume USD-like Y when no SOL leg available
        }

        if (!usdValue || usdValue <= 0) continue;

        // Per-token X / Y breakdown — Meteora datapi /portfolio/open ONLY
        // returns aggregate `balancesSol` (X-converted-to-SOL + Y-as-SOL)
        // and `poolPrice` (X price in Y units). True bin distribution
        // requires reading the on-chain LbPosition PDA + Borsh-decoding
        // the bin liquidity array — separate effort. For in-range single-
        // bin positions (the common case) X-value ≈ Y-value ≈ half the
        // total, so we split the SOL-equivalent value 50/50 between legs.
        // Worst-case error ≈ 10–15% on a wide deeply-asymmetric range,
        // which is still far better than the previous "0.0000 X" display.
        const halfInSol = balancesSol / 2;
        const amountY = halfInSol; // Y is SOL/USD-like
        const amountX = poolPrice > 0 ? halfInSol / poolPrice : 0;

        // Clean pair label only — protocol-specific bin / pool-address
        // metadata stays in `metadata` for the detail row but doesn't
        // pollute the headline (user feedback: "bin 1, BoEm…" was noise).
        const pairLabel = positionCount > 1
          ? `${symA}-${symB} · ${positionCount} positions`
          : `${symA}-${symB}`;

        // Meteora datapi surfaces both a daily fee APR (`feeApr24h`) and a
        // 7-day average (`feeApr` / `feeAprWeek`) depending on the pool's
        // age. Prefer 24h, fall back to weekly. Both are decimals (0.07 =
        // 7%) — convert to % for the rewards dashboard.
        const feeAprRaw = parseFloat(pool.feeApr24h ?? pool.feeApr ?? pool.apr ?? '0');
        const apyPct = Number.isFinite(feeAprRaw) && feeAprRaw > 0
          ? feeAprRaw * 100
          : null;
        const unclaimedFeesUsd = unclaimedFeesSol * solPrice;
        const pnlUsd = parseFloat(pool.pnl ?? '0') || null;
        const pnlPct = parseFloat(pool.pnlPctChange ?? '0') || null;

        items.push({
          label: pairLabel,
          tokens: [
            { symbol: symA, amount: amountX, logoUri: iconA, mint: mintA ?? undefined },
            { symbol: symB, amount: amountY, logoUri: iconB, mint: mintB ?? undefined },
          ],
          totalUsdValue: usdValue,
          metadata: {
            poolAddress,
            binStep,
            positionCount,
            outOfRange: pool.outOfRange ? 1 : 0,
          },
          apy: apyPct,
          claimableUsd: unclaimedFeesUsd > 0 ? unclaimedFeesUsd : null,
          feesUsd: unclaimedFeesUsd > 0 ? unclaimedFeesUsd : null,
          pnlUsd: pnlUsd != null ? pnlUsd * solPrice : null,
          pnlPct,
        });
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

  /**
   * Drift v2 positions via the TS service (@drift-labs/sdk). Returns:
   *   - perp positions → `perpetuals` category, side=long/short, mark price,
   *     unrealised PnL, liquidation price, leverage
   *   - spot positions → `lending` (deposit) or `borrowing` (borrow),
   *     with APY and USD value
   * Both sets coexist as separate ProtocolPosition entries so the rewards
   * dashboard treats perps + spots independently.
   */
  async getDriftPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const resp = await firstValueFrom(
        this.apiService.post<{ data?: { perpPositions?: any[]; spotPositions?: any[] } }>(
          '/actions/build',
          { action_type: 'drift_list_positions', params: {} },
        ),
      );
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const perps: any[] = (resp as any)?.data?.perpPositions ?? [];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const spots: any[] = (resp as any)?.data?.spotPositions ?? [];
      if (perps.length === 0 && spots.length === 0) return [];

      const logo = this.protocolDetection.getProtocolLogo('drift');
      const out: ProtocolPosition[] = [];

      if (perps.length > 0) {
        const items: PositionItem[] = perps.map(p => {
          const base = Math.abs(Number(p.baseAssetAmount ?? 0));
          const mark = Number(p.markPrice ?? 0);
          const notional = base * mark;
          const pnl = Number(p.unrealizedPnl ?? 0);
          const pnlPct = notional > 0 ? (pnl / notional) * 100 : null;
          return {
            label: `${p.marketSymbol} · ${p.side === 'long' ? '↑ Long' : '↓ Short'}${p.leverage > 0 ? ' · ' + (Number(p.leverage)).toFixed(2) + '×' : ''}`,
            tokens: [{
              symbol: p.marketSymbol,
              amount: base,
              logoUri: null,
            }],
            totalUsdValue: notional > 0 ? notional : null,
            metadata: {
              side: p.side,
              entryPrice: p.entryPrice ?? null,
              markPrice: mark,
              liquidationPrice: p.liquidationPrice ?? null,
              subAccount: p.subAccountId ?? 0,
              unsettledPnl: p.unsettledPnl ?? null,
            },
            pnlUsd: pnl,
            pnlPct,
          };
        });
        out.push({
          protocolId: 'drift',
          protocolName: 'Drift Perps',
          protocolLogoUri: logo,
          category: 'perpetuals',
          positions: items,
          totalUsdValue: 0,
        });
      }

      if (spots.length > 0) {
        const deposits: PositionItem[] = [];
        const borrows: PositionItem[] = [];
        for (const s of spots) {
          const amount = Number(s.amount ?? 0);
          if (!(amount > 0)) continue;
          const usd = Number(s.usdValue ?? 0);
          const apy = Number(s.apy ?? 0);
          const item: PositionItem = {
            label: `${s.marketSymbol}${s.subAccountId > 0 ? ` (sub-${s.subAccountId})` : ''}`,
            tokens: [{
              symbol: s.marketSymbol,
              amount,
              logoUri: this.resolveTokenLogo(s.marketSymbol, s.tokenMint),
              mint: s.tokenMint,
            }],
            totalUsdValue: usd > 0 ? usd : null,
            metadata: { subAccount: s.subAccountId ?? 0 },
            apy: Number.isFinite(apy) && apy !== 0 ? apy : null,
          };
          if (s.side === 'borrow') borrows.push(item);
          else deposits.push(item);
        }
        if (deposits.length > 0) {
          out.push({
            protocolId: 'drift',
            protocolName: 'Drift Spot',
            protocolLogoUri: logo,
            category: 'lending',
            positions: deposits,
            totalUsdValue: 0,
          });
        }
        if (borrows.length > 0) {
          out.push({
            protocolId: 'drift',
            protocolName: 'Drift Spot',
            protocolLogoUri: logo,
            category: 'borrowing',
            positions: borrows,
            totalUsdValue: 0,
          });
        }
      }

      return out;
    } catch {
      return [];
    }
  }

  // ──── Streamflow Streams & Vesting ────

  /**
   * Streamflow vesting / payment streams via the TS service (@streamflow/stream).
   * Each stream becomes a `streaming` PositionItem with:
   *   - locked: total deposited − vested
   *   - claimable: vested − withdrawn (drives the Claimable column)
   *   - apy: not applicable; streams aren't yield-bearing
   * We pull both incoming and outgoing streams (recipient-side + sender-side).
   */
  async getStreamflowPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const resp = await firstValueFrom(
        this.apiService.post<{ data?: { streams?: any[] } }>(
          '/actions/build',
          { action_type: 'streamflow_list', params: { direction: 'recipient' } },
        ),
      );
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const streams: any[] = (resp as any)?.data?.streams ?? [];
      if (streams.length === 0) return [];

      const logo = this.protocolDetection.getProtocolLogo('streamflow');
      // Cache token prices in a single Birdeye call so we can value lock /
      // claimable amounts in USD per stream.
      const mints = Array.from(new Set(streams.map(s => s.mint).filter(Boolean)));
      const priceMap = mints.length > 0
        ? await this.birdeyeService.getTokenPrices(mints).catch(() => new Map())
        : new Map();

      const items: PositionItem[] = streams
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .map((s: any): PositionItem | null => {
          const deposited = Number(s.depositedAmount ?? 0);
          const withdrawn = Number(s.withdrawnAmount ?? 0);
          if (deposited === 0) return null;

          // Vested at "now" via piecewise schedule: cliff + linear per period.
          const nowSec = Math.floor(Date.now() / 1000);
          const start = Number(s.start ?? 0);
          const end = Number(s.end ?? 0);
          const cliff = Number(s.cliff ?? 0);
          const cliffAmt = Number(s.cliffAmount ?? 0);
          const period = Number(s.period ?? 1);
          const perPeriod = Number(s.amountPerPeriod ?? 0);
          let vested = 0;
          if (s.closed) {
            vested = deposited;
          } else if (nowSec < cliff || cliff === 0 && nowSec < start) {
            vested = 0;
          } else if (nowSec >= end) {
            vested = deposited;
          } else {
            const linearStart = cliff > 0 ? cliff : start;
            const elapsed = Math.max(0, nowSec - linearStart);
            const periodsElapsed = Math.floor(elapsed / period);
            vested = cliffAmt + periodsElapsed * perPeriod;
            if (vested > deposited) vested = deposited;
          }
          const claimable = Math.max(0, vested - withdrawn);
          const locked = Math.max(0, deposited - vested);

          const mint = s.mint;
          const decimals = Number(s.decimals ?? 0);
          const divisor = decimals > 0 ? Math.pow(10, decimals) : 1;
          const lockedUi = locked / divisor;
          const claimableUi = claimable / divisor;
          const totalUi = deposited / divisor;
          const price = mint ? (priceMap.get(mint)?.price ?? 0) : 0;
          const lockedUsd = price > 0 ? lockedUi * price : null;
          const claimableUsd = price > 0 ? claimableUi * price : null;
          const totalUsd = price > 0 ? totalUi * price : null;

          // Stream name is sometimes a UTF-8 byte array, sometimes a string.
          // Both shapes show up depending on SDK version — normalise to a
          // human label.
          const rawName: unknown = s.name;
          const name: string = typeof rawName === 'string'
            ? rawName
            : Array.isArray(rawName)
              ? new TextDecoder().decode(new Uint8Array(rawName as number[])).replace(/\0+$/, '')
              : 'Stream';

          return {
            label: name || 'Stream',
            tokens: [{
              symbol: s.symbol ?? (mint ? mint.slice(0, 4) + '…' : '?'),
              amount: totalUi,
              logoUri: this.resolveTokenLogo(s.symbol ?? null, mint ?? null),
              mint,
            }],
            totalUsdValue: totalUsd,
            metadata: {
              streamId: s.id ?? null,
              sender: s.sender ?? null,
              recipient: s.recipient ?? null,
              start, end, cliff,
              locked: lockedUi,
              lockedUsd,
              vested: vested / divisor,
              closed: s.closed ? 1 : 0,
            },
            claimableUsd,
            pnlUsd: null,
          };
        })
        .filter((p): p is PositionItem => p !== null);

      if (items.length === 0) return [];
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

  // ──── Jupiter Portfolio API (Jupiter products only) ────

  /**
   * Fetch the wallet's Jupiter-product positions (DCA, limit orders, perp,
   * lend, JUP/JupSOL stake, Jupiter LP) and translate the element soup into
   * the existing ProtocolPosition model. Each platformId becomes its own
   * ProtocolPosition; element type → category mapping:
   *
   *   liquidity   → 'liquidity-pool'
   *   borrowlend  → 'lending'  (deposits, suppliedAssets[])
   *                + 'borrowing' (debts, borrowedAssets[])
   *   leverage    → 'perpetuals'
   *   trade       → 'orders'         (DCA / limit)
   *   multiple    → recurse into sub-elements
   *
   * Each element exposes whatever signals the fetcher had — value, apy,
   * unclaimed fees, pnl — and we surface them as first-class fields on
   * PositionItem so the table can render APY / Fees / PnL columns.
   */
  async getJupiterPortfolioPositions(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    const resp = await this.jupiterPortfolio.getPositions(wallet);
    if (!resp || !Array.isArray(resp.elements) || resp.elements.length === 0) return [];

    // platformId+category → ProtocolPosition. Splitting borrowlend into two
    // protocol entries (lending vs borrowing) keeps the UI grouping consistent
    // with how Kamino renders today.
    const buckets = new Map<string, ProtocolPosition>();
    const tokenInfo = resp.tokenInfo ?? {};

    const walk = (el: JupiterPortfolioElement | undefined): void => {
      if (!el || typeof el !== 'object') return;

      if (el.type === 'multiple') {
        const sub = (el.data && Array.isArray(el.data.elements)) ? el.data.elements : [];
        for (const child of sub) {
          // Inherit platformId when sub-element omits it. Bare elements happen
          // when a fetcher groups multiple positions on the same platform.
          walk({ ...child, platformId: child.platformId ?? el.platformId });
        }
        return;
      }

      // Borrowlend splits into two visual buckets so the table can label
      // them separately. Other types are single-bucket.
      const buildOne = (
        category: ProtocolCategory,
        item: PositionItem,
        protocolNameSuffix?: string,
      ): void => {
        const key = `${el.platformId}::${category}`;
        let bucket = buckets.get(key);
        if (!bucket) {
          const platformId = el.platformId || 'jupiter';
          const protocolLogo = this.protocolDetection.getProtocolLogo(platformId)
            ?? this.protocolDetection.getProtocolLogo('jupiter');
          bucket = {
            protocolId: platformId,
            protocolName: this.prettyPlatformName(platformId) + (protocolNameSuffix ?? ''),
            protocolLogoUri: protocolLogo,
            category,
            positions: [],
            totalUsdValue: 0,
          };
          buckets.set(key, bucket);
        }
        bucket.positions.push(item);
      };

      switch (el.type) {
        case 'liquidity': {
          const item = this.buildJupiterLiquidityItem(el, tokenInfo);
          if (item) buildOne('liquidity-pool', item);
          break;
        }
        case 'borrowlend': {
          const split = this.buildJupiterBorrowLendItems(el, tokenInfo);
          for (const supply of split.supplies) buildOne('lending', supply);
          for (const borrow of split.borrows) buildOne('borrowing', borrow);
          break;
        }
        case 'leverage': {
          const item = this.buildJupiterLeverageItem(el, tokenInfo);
          if (item) buildOne('perpetuals', item);
          break;
        }
        case 'trade': {
          const item = this.buildJupiterTradeItem(el, tokenInfo);
          if (item) buildOne('orders', item);
          break;
        }
        default:
          // Unknown element type. Don't drop it on the floor — surface as a
          // generic "rewards" row so the user sees it; further parsing can
          // land later.
          buildOne('rewards', this.buildJupiterGenericItem(el, tokenInfo));
      }
    };

    for (const el of resp.elements) walk(el);

    // Drop empty buckets (defensive — every buildOne pushes at least one item,
    // but a parser miss could leave an empty bucket if all sub-items returned
    // null).
    return Array.from(buckets.values()).filter(b => b.positions.length > 0);
  }

  // ──── Jupiter element parsers ────
  // The element data shape varies per fetcher and isn't strongly typed
  // upstream. Every accessor is defensive: optional-chain through the data,
  // fall back to empty arrays, accept both camelCase and snake_case where
  // Sonarwatch fetchers haven't been normalised. Returning null = drop the
  // element rather than render a row with garbage.

  private buildJupiterLiquidityItem(
    el: JupiterPortfolioElement,
    tokenInfo: Record<string, JupiterPortfolioTokenInfo>,
  ): PositionItem | null {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = el.data ?? {};
    // Sonarwatch liquidity shape: data.liquidities[].assets[]; assets carry
    // {address, amount, price, value}. Some fetchers nest as data.assets[]
    // directly when the position has no sub-liquidities.
    const liquidities: any[] = Array.isArray(data.liquidities) ? data.liquidities : [];
    const assetSource: any[] = liquidities.length > 0
      ? liquidities.flatMap((l: any) => Array.isArray(l.assets) ? l.assets : [])
      : (Array.isArray(data.assets) ? data.assets : []);
    const tokens = assetSource
      .map((a: any) => this.assetToToken(a, tokenInfo))
      .filter((t): t is { symbol: string; amount: number; logoUri: string | null; mint?: string } => t !== null);
    if (tokens.length === 0) return null;

    // Unclaimed rewards can live on the element root (Meteora-flavoured) or
    // on each liquidity (Orca-flavoured).
    const rewardsAssets: any[] = Array.isArray(data.rewardAssets) ? data.rewardAssets
      : (Array.isArray(data.unclaimedRewards) ? data.unclaimedRewards : []);
    const claimable = rewardsAssets.reduce((s: number, a: any) => s + (Number(a.value) || 0), 0);

    return {
      label: el.label || 'Liquidity Position',
      tokens,
      totalUsdValue: typeof el.value === 'number' ? el.value : null,
      metadata: { platformId: el.platformId, elementType: el.type },
      apy: this.pickNum(data.apy, data.apr),
      claimableUsd: claimable > 0 ? claimable : null,
      feesUsd: this.pickNum(data.unclaimedFeesUsd, data.feesUsd),
    };
  }

  private buildJupiterBorrowLendItems(
    el: JupiterPortfolioElement,
    tokenInfo: Record<string, JupiterPortfolioTokenInfo>,
  ): { supplies: PositionItem[]; borrows: PositionItem[] } {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = el.data ?? {};
    const supplied: any[] = Array.isArray(data.suppliedAssets) ? data.suppliedAssets
      : (Array.isArray(data.deposits) ? data.deposits : []);
    const borrowed: any[] = Array.isArray(data.borrowedAssets) ? data.borrowedAssets
      : (Array.isArray(data.borrows) ? data.borrows : []);
    const healthFactor = this.pickNum(data.healthFactor, data.healthRatio);
    const ltv = this.pickNum(data.value && data.borrowedValue ? (data.borrowedValue / data.value) * 100 : null);

    const mkItem = (asset: any, side: 'supply' | 'borrow'): PositionItem | null => {
      const tok = this.assetToToken(asset, tokenInfo);
      if (!tok) return null;
      return {
        label: `${tok.symbol} ${side === 'supply' ? 'Supplied' : 'Borrowed'}`,
        tokens: [tok],
        totalUsdValue: typeof asset.value === 'number' ? asset.value : null,
        metadata: { platformId: el.platformId, elementType: el.type, side },
        apy: this.pickNum(asset.apy, asset.apr, data.apy),
        claimableUsd: side === 'supply' ? this.pickNum(asset.unclaimedRewardsUsd) : null,
        feesUsd: null,
        pnlUsd: side === 'borrow' && healthFactor !== null
          // Encode health-factor in pnlUsd? No — keep pnlUsd numeric and
          // surface healthFactor via metadata for the borrow row template.
          ? null
          : null,
      };
    };

    const supplies = supplied.map(a => mkItem(a, 'supply')).filter((x): x is PositionItem => x !== null);
    const borrows = borrowed.map(a => {
      const it = mkItem(a, 'borrow');
      if (it) {
        it.metadata['healthFactor'] = healthFactor;
        it.metadata['ltv'] = ltv;
      }
      return it;
    }).filter((x): x is PositionItem => x !== null);

    return { supplies, borrows };
  }

  private buildJupiterLeverageItem(
    el: JupiterPortfolioElement,
    tokenInfo: Record<string, JupiterPortfolioTokenInfo>,
  ): PositionItem | null {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = el.data ?? {};
    const assets: any[] = Array.isArray(data.assets) ? data.assets
      : (Array.isArray(data.positions) ? data.positions : []);
    const tokens = assets
      .map(a => this.assetToToken(a, tokenInfo))
      .filter((t): t is { symbol: string; amount: number; logoUri: string | null; mint?: string } => t !== null);
    // Perp positions without a token leg can still have notional value;
    // render them with a synthetic label so they don't disappear.
    if (tokens.length === 0 && (!data.collateral || !data.market)) return null;

    const pnl = this.pickNum(data.pnl, data.unrealizedPnl);
    const pnlPct = this.pickNum(data.pnlPercent, data.pnlPct);
    return {
      label: el.label || data.market || 'Leverage Position',
      tokens,
      totalUsdValue: typeof el.value === 'number' ? el.value : null,
      metadata: {
        platformId: el.platformId,
        elementType: el.type,
        leverage: this.pickNum(data.leverage),
        side: data.side ?? null,
        market: data.market ?? null,
      },
      apy: null,
      claimableUsd: null,
      feesUsd: this.pickNum(data.borrowFee, data.fundingFee),
      pnlUsd: pnl,
      pnlPct,
    };
  }

  private buildJupiterTradeItem(
    el: JupiterPortfolioElement,
    tokenInfo: Record<string, JupiterPortfolioTokenInfo>,
  ): PositionItem | null {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = el.data ?? {};
    // Open DCA / limit orders carry inputAsset + targetAsset; some carry
    // the unswapped-input directly under `assets[]`.
    const assets: any[] = Array.isArray(data.assets) ? data.assets : [];
    const explicit = [data.inputAsset, data.targetAsset, data.outputAsset].filter(Boolean);
    const tokens = (assets.length > 0 ? assets : explicit)
      .map(a => this.assetToToken(a, tokenInfo))
      .filter((t): t is { symbol: string; amount: number; logoUri: string | null; mint?: string } => t !== null);
    if (tokens.length === 0) return null;
    return {
      label: el.label || 'Open Order',
      tokens,
      totalUsdValue: typeof el.value === 'number' ? el.value : null,
      metadata: {
        platformId: el.platformId,
        elementType: el.type,
        orderType: data.orderType ?? data.type ?? null,
      },
    };
  }

  private buildJupiterGenericItem(
    el: JupiterPortfolioElement,
    tokenInfo: Record<string, JupiterPortfolioTokenInfo>,
  ): PositionItem {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = el.data ?? {};
    const assets: any[] = Array.isArray(data.assets) ? data.assets : [];
    const tokens = assets
      .map(a => this.assetToToken(a, tokenInfo))
      .filter((t): t is { symbol: string; amount: number; logoUri: string | null; mint?: string } => t !== null);
    return {
      label: el.label || `${el.platformId} position`,
      tokens,
      totalUsdValue: typeof el.value === 'number' ? el.value : null,
      metadata: { platformId: el.platformId, elementType: el.type },
    };
  }

  /**
   * Translate a Jupiter Portfolio asset entry into our token shape. Handles
   * both the canonical Sonarwatch shape ({address, amount, price, value, ...})
   * and the looser variants some fetchers emit.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private assetToToken(asset: any, tokenInfo: Record<string, JupiterPortfolioTokenInfo>): { symbol: string; amount: number; logoUri: string | null; mint?: string } | null {
    if (!asset || typeof asset !== 'object') return null;
    const mint: string | undefined = asset.address ?? asset.mint ?? asset.mintAddress;
    const amount = Number(asset.amount ?? asset.balance ?? 0);
    if (!amount || amount <= 0) return null;
    const info = mint ? tokenInfo[mint] : undefined;
    const symbol: string = info?.symbol ?? asset.symbol ?? (mint ? mint.slice(0, 4) + '…' : 'UNK');
    let logoUri: string | null = info?.logoURI ?? asset.logoURI ?? asset.image ?? null;
    if (!logoUri && mint) {
      const registryTok = this.tokenRegistry.getToken(mint);
      logoUri = registryTok?.logoURI ?? null;
      if (!registryTok) this.tokenRegistry.resolveAsync(mint);
    }
    return { symbol, amount, logoUri, mint };
  }

  // Coalesce a series of candidate values, returning the first finite number
  // or null. Saves a lot of `?? a ?? b ?? null` chains in the parsers.
  private pickNum(...candidates: Array<number | string | null | undefined>): number | null {
    for (const c of candidates) {
      if (c === null || c === undefined || c === '') continue;
      const n = typeof c === 'number' ? c : Number(c);
      if (Number.isFinite(n)) return n;
    }
    return null;
  }

  // Turn a platformId like "jupiter-perpetuals" into "Jupiter Perpetuals".
  private prettyPlatformName(id: string): string {
    if (!id) return 'Jupiter';
    return id
      .split(/[-_]/)
      .map(part => part.length > 0 ? part[0].toUpperCase() + part.slice(1) : part)
      .join(' ');
  }

  // ──── Pump.fun Creator Rewards (on-chain decode) ────

  /**
   * Read the wallet's Pump.fun creator-vault PDAs and return the claimable
   * SOL balance as a ProtocolPosition.
   *
   * Pump.fun emits creator royalties into two PDAs derived from the wallet:
   *   - **bonding curve vault**: seeds=["creator-vault", creator], program
   *     = `6EF8rrec…F6P` (PUMP_FUN). Holds royalties accrued while the
   *     token is still on the bonding curve.
   *   - **PumpSwap AMM vault**: seeds=["creator_vault", creator], program
   *     = `pAMMBay…fXEA`. Holds royalties from post-graduation AMM trades.
   *
   * Both PDAs use the wallet as the only variable seed, so every
   * creator-vault for a given wallet collapses to two account reads — no
   * need to enumerate the wallet's created tokens. The vault is a system-
   * owned account holding only SOL; rent-exempt baseline (~890k lamports)
   * is subtracted so we don't surface non-claimable rent as a reward.
   */
  async getPumpfunRewards(wallet: string): Promise<ProtocolPosition[]> {
    if (!wallet) return [];
    try {
      const web3 = await import('@solana/web3.js');
      const walletPk = new web3.PublicKey(wallet);
      const PUMP_FUN = new web3.PublicKey('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
      const PUMP_AMM = new web3.PublicKey('pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA');

      const [bondingVault] = web3.PublicKey.findProgramAddressSync(
        [new TextEncoder().encode('creator-vault'), walletPk.toBytes()],
        PUMP_FUN,
      );
      const [ammVault] = web3.PublicKey.findProgramAddressSync(
        [new TextEncoder().encode('creator_vault'), walletPk.toBytes()],
        PUMP_AMM,
      );

      const accounts = await this.solanaRpc.getMultipleAccountInfo([
        bondingVault.toBase58(),
        ammVault.toBase58(),
      ]);

      // System-owned 0-data accounts hit ~890_880 lamports rent floor.
      // Anything above that is the creator's claimable share — anything
      // below is just protocol bookkeeping, not a reward.
      const RENT_EXEMPT_SYS = 890_880;
      const claim = (acct: { lamports: number } | null): number => {
        if (!acct) return 0;
        const excess = acct.lamports - RENT_EXEMPT_SYS;
        return excess > 0 ? excess : 0;
      };
      const bondingLamports = claim(accounts[0]);
      const ammLamports = claim(accounts[1]);
      const totalLamports = bondingLamports + ammLamports;
      if (totalLamports <= 0) return [];

      // SOL price for USD valuation. We use the priceAllPositions pipeline
      // for everything else, but rewards-only rows have no token-mint to
      // hand to Birdeye — pull the SOL price directly from the cached
      // service map so the first render already carries USD.
      const SOL_MINT = 'So11111111111111111111111111111111111111112';
      const priceMap = await this.birdeyeService.getTokenPrices([SOL_MINT]).catch(() => new Map());
      const solPrice = priceMap.get(SOL_MINT)?.price ?? null;
      const totalSol = totalLamports / 1e9;
      const usdValue = solPrice ? totalSol * solPrice : null;

      const items: PositionItem[] = [];
      if (bondingLamports > 0) {
        const amount = bondingLamports / 1e9;
        items.push({
          label: 'Bonding-curve royalties',
          tokens: [{ symbol: 'SOL', amount, logoUri: this.protocolDetection.getSolLogo(), mint: SOL_MINT }],
          totalUsdValue: solPrice ? amount * solPrice : null,
          metadata: { vault: bondingVault.toBase58(), source: 'bonding-curve' },
          claimableUsd: solPrice ? amount * solPrice : null,
        });
      }
      if (ammLamports > 0) {
        const amount = ammLamports / 1e9;
        items.push({
          label: 'PumpSwap AMM royalties',
          tokens: [{ symbol: 'SOL', amount, logoUri: this.protocolDetection.getSolLogo(), mint: SOL_MINT }],
          totalUsdValue: solPrice ? amount * solPrice : null,
          metadata: { vault: ammVault.toBase58(), source: 'pump-amm' },
          claimableUsd: solPrice ? amount * solPrice : null,
        });
      }

      return [{
        protocolId: 'pumpfun',
        protocolName: 'Pump.fun',
        protocolLogoUri: this.protocolDetection.getProtocolLogo('pumpfun'),
        category: 'rewards',
        positions: items,
        totalUsdValue: usdValue ?? 0,
        totalClaimableUsd: usdValue,
        claimableCount: items.length,
      }];
    } catch {
      return [];
    }
  }

  private async fetchKaminoMarkets(): Promise<KaminoMarketEntry[]> {
    const now = Date.now();
    if (this.kaminoMarketsCache && now - this.kaminoMarketsCacheTs < this.KAMINO_MARKETS_TTL) {
      return this.kaminoMarketsCache;
    }
    if (this.kaminoMarketsCachePromise) return this.kaminoMarketsCachePromise;

    this.kaminoMarketsCachePromise = (async () => {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4_000);
        const res = await fetch(KAMINO_MARKETS_URL, { signal: controller.signal });
        clearTimeout(timeout);
        if (!res.ok) return [];
        const data = await res.json() as any[];
        const markets: KaminoMarketEntry[] = (Array.isArray(data) ? data : []).map((m: any) => ({
          // Kamino /v2/kamino-market response: { lendingMarket, name, ... }
          pubkey: m.lendingMarket ?? m.pubkey ?? '',
          name: m.name ?? 'Kamino Market',
        })).filter(m => m.pubkey);
        this.kaminoMarketsCache = markets;
        this.kaminoMarketsCacheTs = Date.now();
        return markets;
      } catch {
        return [];
      } finally {
        this.kaminoMarketsCachePromise = null;
      }
    })();
    return this.kaminoMarketsCachePromise;
  }

  /**
   * Centralised dollar-value pass over every protocol position. Each
   * protocol's own API may or may not return USD values per row — this
   * pass guarantees that *every* position with a known mint and amount
   * gets a `totalUsdValue` derived from BirdeyeService prices.
   *
   * Protocol-level totals (`ProtocolPosition.totalUsdValue`) are recomputed
   * from the priced item rows so the wallet "Total Value" headline + the
   * donut chart + the per-protocol summary cards all reconcile.
   *
   * Mutates `positions` in place; the caller doesn't need to swap arrays.
   */
  async priceAllPositions(positions: ProtocolPosition[]): Promise<void> {
    const mints = new Set<string>();
    for (const proto of positions) {
      for (const pos of proto.positions) {
        for (const tok of pos.tokens) {
          if (tok.mint && tok.amount > 0) mints.add(tok.mint);
        }
      }
    }
    if (mints.size === 0) return;

    const priceMap = await this.birdeyeService.getTokenPrices(Array.from(mints))
      .catch(() => new Map());

    for (const proto of positions) {
      let protoTotal = 0;
      let anyItemPriced = false;
      let totalClaimable = 0;
      let claimableCount = 0;
      for (const pos of proto.positions) {
        // Trust the protocol-API value when present (those services often
        // report position-level USD that includes accrued fees / interest
        // we wouldn't capture from raw amounts × prices).
        if (pos.totalUsdValue != null && pos.totalUsdValue > 0) {
          protoTotal += pos.totalUsdValue;
          anyItemPriced = true;
        } else {
          let itemTotal = 0;
          let priced = false;
          for (const tok of pos.tokens) {
            if (!tok.mint) continue;
            const entry = priceMap.get(tok.mint);
            if (!entry || !entry.price) continue;
            // For borrow positions, the "amount" is debt — count as
            // negative against the position's notional. Borrow rows live in
            // protocol category 'borrowing', so we can detect via the parent.
            const sign = proto.category === 'borrowing' ? -1 : 1;
            itemTotal += sign * tok.amount * entry.price;
            priced = true;
          }
          if (priced) {
            pos.totalUsdValue = itemTotal;
            protoTotal += itemTotal;
            anyItemPriced = true;
          }
        }

        // Orca CLMM stashes the unclaimed fee amounts in metadata.feeOwedA/B
        // (token units, decimal-normalised). Convert to USD using the
        // already-fetched Birdeye prices so the rewards dashboard surfaces
        // them as claimable. Skip if a protocol-specific claimableUsd is
        // already set (Meteora, Jupiter Portfolio, Pumpfun).
        if (pos.claimableUsd == null && proto.protocolId === 'orca' && pos.tokens.length >= 2) {
          const feeA = Number(pos.metadata['feeOwedA'] ?? 0);
          const feeB = Number(pos.metadata['feeOwedB'] ?? 0);
          if (feeA > 0 || feeB > 0) {
            const priceA = pos.tokens[0]?.mint ? priceMap.get(pos.tokens[0].mint!)?.price ?? 0 : 0;
            const priceB = pos.tokens[1]?.mint ? priceMap.get(pos.tokens[1].mint!)?.price ?? 0 : 0;
            const claimUsd = feeA * priceA + feeB * priceB;
            if (claimUsd > 0) {
              pos.claimableUsd = claimUsd;
              pos.feesUsd = claimUsd;
            }
          }
        }

        if (pos.claimableUsd != null && pos.claimableUsd > 0) {
          totalClaimable += pos.claimableUsd;
          claimableCount += 1;
        }
      }
      if (anyItemPriced) {
        proto.totalUsdValue = protoTotal;
      }
      // Recompute claimable aggregates at the protocol level so the "X Reclaim"
      // badge always matches the per-row claimable values. We overwrite any
      // upstream-supplied total — the per-row data is authoritative.
      if (totalClaimable > 0) {
        proto.totalClaimableUsd = totalClaimable;
        proto.claimableCount = claimableCount;
      }
    }
  }
}
