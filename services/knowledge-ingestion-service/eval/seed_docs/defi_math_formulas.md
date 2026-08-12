# DeFi Math Formulas — Liquidation, Leverage, LTV, Health Factor

This document is the canonical reference for the formulas the OPRAI assistant must use when answering analytical or hypothetical DeFi math questions. Use these formulas literally — do not approximate "X% LTV gap = X% price drop"; that is wrong.

## Loan-to-Value (LTV) and Liquidation Price

### Definitions
- **Collateral value (C)**: USD value of the asset deposited as collateral. Tracks the price of the collateral asset.
- **Debt (D)**: USD value of the borrowed asset. If borrowing a stablecoin (USDC/USDT), D is constant. If borrowing a non-stable asset (e.g. SOL), D moves with that asset's price.
- **Current LTV**: `D / C` at the current price.
- **Liquidation LTV (Lₗᵢ𝑞)**: protocol-set threshold. When current LTV reaches this, the position is eligible for liquidation. Typical Solana lending values: Kamino 80–87%, Solend 75–85%, depending on asset.

### Liquidation price formula (stable debt)

When the debt is in a stablecoin and the collateral is a volatile asset (e.g. SOL, JitoSOL, JupSOL):

```
P_liq = P_current × (LTV_current / LTV_liq)
```

Derivation:
```
LTV_new      = D / (C × P_new / P_current)
             = LTV_current × (P_current / P_new)
At liquidation: LTV_new = LTV_liq
LTV_liq      = LTV_current × (P_current / P_liq)
P_liq        = P_current × (LTV_current / LTV_liq)
```

### Worked example

Setup:
- 100 SOL collateral (i.e. position-side asset = SOL or LST)
- SOL price = $180
- Borrowed USDC such that current LTV = 78%
- Liquidation LTV = 85%

Computation:
```
P_liq = 180 × (0.78 / 0.85)
      = 180 × 0.91765
      = $165.18
```

**Liquidation price ≈ $165.18.** Tolerated drop = $180 − $165.18 = **$14.82 (≈8.24%)**.

### Common wrong shortcut (DO NOT USE)

A frequent mistake is `P_liq = P_current × (1 − (LTV_liq − LTV_current))` — i.e. treating the LTV percentage gap as a price-percentage drop. This is wrong because LTV is a ratio of two values, not a price level. The error is small when both LTVs are close to each other (~1–2 dollars on the example above) but blows up when they diverge:

| LTV_current | LTV_liq | Wrong shortcut | Correct |
|-------------|---------|----------------|---------|
| 78% | 85% | $167.40 | $165.18 |
| 50% | 85% | $117.00 | $105.88 |
| 30% | 85% | $81.00 | $63.53 |

Always use `P_current × (LTV_current / LTV_liq)`.

### Liquidation price (volatile debt)

When BOTH collateral and debt are volatile (e.g. SOL collateral, ETH debt), use the price ratio instead of absolute prices:

```
R_liq = R_current × (LTV_current / LTV_liq)
```
where `R = P_collateral / P_debt`. Liquidation triggers when the *ratio* falls to R_liq, not when either asset alone moves.

If collateral and debt are the SAME asset (e.g. SOL collateral, SOL debt), price moves cancel out — you cannot be liquidated from price action alone. Liquidation can still occur from accrued borrow interest pushing LTV up over time.

### Liquidation price (correlated assets, e.g. JitoSOL collateral, SOL debt)

For LST/SOL pairs, the price ratio is near 1.0 but drifts upward over time as the LST accrues staking yield. The "liquidation price" should be expressed as the LST/SOL exchange-rate floor:

```
R_liq = R_current × (LTV_current / LTV_liq)
```
With R typically near 1.05–1.15. A depeg event (LST trading below its NAV) is the main liquidation trigger here, not raw SOL price.

## Leverage and Position Sizing

### Notional, equity, debt
For a leveraged long opened against a stable-debt protocol:
```
notional         = equity × leverage          # total position size
debt             = notional − equity          # how much you borrowed
LTV_after_open   = debt / notional            # initial LTV
```

### Worked example
Equity = $18,000 (100 SOL @ $180), leverage = 5×:
```
notional = 18,000 × 5 = $90,000
debt     = 90,000 − 18,000 = $72,000
LTV_open = 72,000 / 90,000 = 80%
```

If protocol's max LTV is 85%, you have only 5 percentage points of headroom at open — very tight. A 5.9% price drop (= 80/85 ratio) would liquidate.

### Leverage effect on price tolerance
Tolerated drop before liquidation:
```
drop_pct = 1 − (LTV_current / LTV_liq)
```

| Leverage | Initial LTV | Drop tolerance @ LTV_liq=85% |
|----------|-------------|------------------------------|
| 2× | 50% | 41.2% |
| 3× | 67% | 21.6% |
| 4× | 75% | 11.8% |
| 5× | 80% | 5.9% |
| 8× | 87.5% | already past |
| 10× | 90% | already past |

## Health Factor

Most lending protocols expose a health factor (HF) instead of raw LTV. Definitions vary slightly:

### Aave-style (most common)
```
HF = (collateral_value × LTV_liq) / debt
```
- HF > 1 → safe
- HF = 1 → liquidation threshold reached
- HF < 1 → eligible for liquidation

Some protocols expose `health = 1 − (LTV_current / LTV_liq)` so that 1.0 means freshly opened with zero debt and 0.0 means liquidation imminent.

Always check which convention the protocol uses before quoting a number to the user. Kamino UI shows "health: 0.27" meaning 27% buffer remaining (so 73% of the way to liquidation), NOT Aave's HF=0.27 (which would mean already insolvent).

## Net APY for Looped Positions

When you supply asset A, borrow asset B, swap to more A, and re-supply (a "loop" or "multiply" strategy):

```
net_APY = supply_APY × position_multiplier
        − borrow_APY × (position_multiplier − 1)
        + emission_yield_supply × position_multiplier
        − emission_cost_borrow × (position_multiplier − 1)
```
where `position_multiplier = leverage = notional / equity`.

### Worked example
- JitoSOL supply APY = 7.8% (real yield from staking + lending)
- USDC borrow APY = 5.2%
- Leverage = 3× (so multiplier = 3, borrow ratio = 2)

```
net_APY = 7.8% × 3 − 5.2% × 2
        = 23.4% − 10.4%
        = 13.0%
```

This is the *gross* APY before slippage on the looping swaps and protocol fees. Real net is typically 0.5–2 percentage points lower per loop iteration.

### Looping doesn't multiply APY linearly forever
Each loop iteration costs swap slippage + gas + Jupiter fee. With a 0.3% slippage per loop and 3 loops, you start ~0.9% behind. Loop yield only beats hold-and-stake when the spread (supply_APY − borrow_APY) > total_loop_cost / leverage.

## Funding Rates (Perps)

Drift, Mango, Zeta perpetuals charge/credit funding hourly. Funding payment per hour:

```
hourly_funding_$ = position_notional × funding_rate_hourly
                 = position_size × mark_price × funding_rate_hourly
```

### Worked example
- 50 SOL perp long, mark = $180, funding rate hourly = +0.01% (longs pay shorts)

```
notional        = 50 × 180 = $9,000
hourly_funding  = 9,000 × 0.0001 = $0.90
daily_funding   = $0.90 × 24 = $21.60
annualized      = 0.0001 × 24 × 365 = 87.6% APR
```

Funding is volatile — often flips sign. Use 7-day average for any sustained-strategy estimate, not the current hourly rate alone.

### Delta-neutral yield
A common Solana strategy: long spot SOL (or hold JLP) + short SOL perp on Drift. Net yield ≈ `LST_yield + (−funding_paid)`. When funding is negative (shorts pay longs) the strategy earns on both legs.

## Impermanent Loss (Concentrated Liquidity, e.g. Meteora DLMM, Orca Whirlpool)

For a 50/50 constant-product LP (legacy AMM), with price ratio `r = P_new / P_old`:
```
IL_pct = (2 × √r) / (1 + r) − 1
```

| Price change | IL |
|--------------|-----|
| +25% | -0.6% |
| +50% | -2.0% |
| +100% | -5.7% |
| +200% | -13.4% |
| +500% | -25.5% |
| -50% | -2.0% |

For **concentrated liquidity** (Uniswap v3 / Orca Whirlpool / Meteora DLMM), IL within the active range is amplified by the concentration multiplier. When price exits the range, your position becomes 100% of the underperforming side and IL stops accruing — it is now realized as a one-sided position.

### Worked example (DLMM, tight range)
- Range: $150 – $200
- Initial deposit: 50/50 split at SOL = $175
- SOL moves to $190 (still in range)
- Effective IL ≈ 4–6× the constant-product IL for the same price move (concentration ~5×)
- Constant-product IL at +8.6% ≈ -0.09% → DLMM IL ≈ -0.4 to -0.6%

When SOL exits the range upward at $200+:
- Position is 100% USDC at the exit boundary
- Missed all upside above $200
- IL frozen until you rebalance or price re-enters

## JLP (Jupiter Perps LP) Yield Math

JLP composition (rebalanced regularly): roughly 45% SOL, 10% ETH, 9% BTC, 36% USDC/USDT.

NAV change formula (ignoring trader PnL):
```
NAV_new = NAV_old × (
    0.45 × (1 + r_SOL) +
    0.10 × (1 + r_ETH) +
    0.09 × (1 + r_BTC) +
    0.36 × 1.0
)
```
where r_X = pct change in price of asset X.

### Worked example
SOL drops 20%, BTC drops 10%, ETH drops 10%, stables flat:
```
NAV_new / NAV_old = 0.45 × 0.80 + 0.10 × 0.90 + 0.09 × 0.90 + 0.36 × 1.0
                  = 0.360 + 0.090 + 0.081 + 0.360
                  = 0.891
```

So JLP NAV drops ~10.9% before counting trader PnL. Trader PnL is added on top: when traders are net losing (most of the time), JLP gains additionally ~30–80% APR equivalent.

## Pump.fun Bonding Curve

Pump.fun uses a constant-product AMM with **virtual** reserves. Tokens migrate to PumpSwap AMM after ~85 SOL of bonded buys accumulate. The OPRAI tool `pumpfun_curve_global` returns the live on-chain constants — never hardcode them, they have changed (fee bps was 100, now 95; graduation threshold has shifted before).

### Core formulas

```
tokens_out  = (v_tok × sol_in) / (v_sol + sol_in)            # buy: SOL → tokens
sol_in_for  = (v_sol × tokens_out) / (v_tok − tokens_out)    # buy: SOL needed for N tokens
sol_out     = (v_sol × tokens_in) / (v_tok + tokens_in)      # sell: tokens → SOL
mc_sol      = (v_sol × token_total_supply) / v_tok           # market cap in SOL
v_sol × v_tok = constant                                      # invariant (constant-product)
```

`v_sol` and `v_tok` are the CURRENT virtual reserves. They start at the initial values returned by `pumpfun_curve_global` (typically `v_sol_init=30 SOL`, `v_tok_init≈1.073B`) and evolve as buyers add SOL: `v_sol_new ≈ v_sol_old + sol_in_after_fee`, `v_tok_new = const / v_sol_new`.

### Worked example — "How much SOL to push market cap from 50 SOL to 100 SOL?"

Step 1 — fetch live constants via `pumpfun_curve_global`:
- `v_sol_init` = 30 SOL
- `v_tok_init` = 1,073,000,000 tokens
- `token_total_supply` = 1,000,000,000
- `protocol_fee_bps` = 95 (0.95%)

Step 2 — invariant: `const = v_sol_init × v_tok_init = 30 × 1.073e9 = 3.219e10`

Step 3 — solve `mc = (v_sol² × supply) / const` for `v_sol`:
```
v_sol = √(mc × const / supply)
```

Step 4 — apply at both market caps:
```
At  50 SOL mc:  v_sol = √(50  × 3.219e10 / 1e9) = √1609.5 ≈ 40.12 SOL
At 100 SOL mc:  v_sol = √(100 × 3.219e10 / 1e9) = √3219   ≈ 56.74 SOL
```

Step 5 — net SOL added to the curve:
```
Δv_sol = 56.74 − 40.12 = 16.62 SOL
```

Step 6 — gross SOL paid by the buyer (account for the 0.95% protocol fee):
```
gross = 16.62 / (1 − 0.0095) ≈ 16.78 SOL
```

**Answer: ~16.6 SOL net (~16.8 SOL gross with fees).**

### Common WRONG shortcut — DO NOT USE

Saying "market cap doubles, so v_sol doubles, so ~50 SOL needed" is wrong by a factor of 3. The constant-product invariant `v_sol × v_tok = const` means:
- Market cap depends on **v_sol²** (since `mc = v_sol² × supply / const`)
- So `v_sol` scales with `√(mc)`, not linearly
- Doubling mc multiplies v_sol by `√2 ≈ 1.414`, NOT by 2

This shortcut wildly over-estimates the required buy pressure and is the most common mistake in pump.fun math.

### Other handy results

- **Starting price**: `price_sol = v_sol_init / v_tok_init = 30 / 1.073e9 ≈ 2.8 × 10⁻⁸ SOL/token`
- **Tokens for 1 SOL on a fresh curve**: `1.073e9 × 1 / (30 + 1) ≈ 34.6M tokens`
- **Graduation**: occurs at ~85 SOL of bonded buys, then liquidity migrates to PumpSwap AMM
- **Bonded supply at graduation**: ~793.1M tokens (the rest seeds the migration LP)

## Sources and Verification

- Kamino docs: https://docs.kamino.finance/products/multiply/risks
- Drift docs: https://docs.drift.trade/trading/perpetual-futures
- Jupiter docs: https://station.jup.ag/docs/perpetual-exchange/jlp-pool
- Marinade docs: https://docs.marinade.finance
- General: Aave whitepaper (LTV / health factor formulation), Uniswap v3 whitepaper (concentrated liquidity IL)

When unsure between two formulas, prefer the protocol's own docs over a generic one. Aave-style HF and Kamino-style health are different — quoting the wrong convention misleads the user about position safety.
