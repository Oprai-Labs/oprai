/**
 * DLMM bin-distribution math.
 *
 * Meteora DLMM concentrates liquidity into discrete price *bins*. Each bin
 * sits at price `p_i = (1 + binStep/10000)^i`, where `binStep` is in basis
 * points (e.g. 8 → 0.08% per bin). At any moment exactly one bin is the
 * "active bin" — that's where trades currently fire. The active bin holds
 * BOTH tokens (in a ratio determined by the spot price within the bin);
 * bins below the active bin hold only token Y (the quote, e.g. SOL); bins
 * above hold only token X (the base, e.g. jupSOL). A position is a slice
 * of bins `[minBinId, maxBinId]` weighted by a *strategy* shape (Spot,
 * Curve, Bid-Ask), and the totals of X and Y a user must deposit follow
 * directly from those weights and the bin-price geometry.
 *
 * The math here is the simplification commonly used by DLMM front-ends
 * (Meteora's own UI included): treat each bin's effective price as the bin
 * geometric mid-price, treat the active bin's split as 50/50 (the true
 * split depends on where current price falls inside the bin range — a
 * detail that's noise compared to slippage and rounding), and compute
 * required token amounts by accumulating bin contributions.
 *
 * The result lets the action card answer the only two questions that
 * matter to the user:
 *   1. "Given my chosen range, do I need both tokens, or just one?"
 *   2. "If I type X amount of one token, how much of the other do I need?"
 */

export type DlmmStrategy = 'spot' | 'curve' | 'bidask';

export interface DlmmRatioInput {
  /** Active bin id at the time we render the form. */
  activeBinId: number;
  /** Inclusive lower bound of the position range. */
  minBinId: number;
  /** Inclusive upper bound of the position range. */
  maxBinId: number;
  /** Bin step in basis points (1 bp = 0.01%). 1 / 8 / 25 / 100 are common. */
  binStep: number;
  /** Distribution shape across bins. */
  strategy: DlmmStrategy;
}

export interface DlmmRatioResult {
  /** Active bin price (Y per X, e.g. SOL per jupSOL). */
  activePrice: number;
  /**
   * Whether any bin in the range sits at or above active — meaning the
   * position needs token X (e.g. jupSOL). If false, the position is
   * single-sided in Y only and amountA should be locked at 0.
   */
  needsX: boolean;
  /** Mirror — whether any bin sits at or below active. */
  needsY: boolean;
  /**
   * X-token-amount per Y-token-amount that the position requires for the
   * chosen distribution. `null` when one side is zero (single-sided
   * range). Uses native token units (no decimal scaling).
   */
  xPerY: number | null;
  /** Reciprocal of `xPerY`. `null` when X side is zero. */
  yPerX: number | null;
  /**
   * Share of total *Y-units of value* that lives on the X side. 0 means
   * single-sided Y (range entirely below active); 1 means single-sided X
   * (range entirely above active); 0.5 means roughly balanced.
   * Useful for the UI to render a small "60% jupSOL · 40% SOL" hint.
   */
  xShareOfValue: number;
  /** How many bins are in the range (≥ 1 once min/max are valid). */
  binCount: number;
}

/**
 * Build raw distribution weights for a bin range under one of the three
 * built-in strategies. Weights are unnormalised so the caller can decide
 * whether to normalise; we always normalise inside `computeDlmmRatio`.
 *
 * - **Spot**: equal weight per bin. Cheapest, predictable, what most
 *   retail users want when they don't know what to pick.
 * - **Curve**: bell shape centred on `activeBinId`. Concentrates depth at
 *   the current price — good for stable / pegged pairs.
 * - **Bid-Ask**: U-shape (heavier at the edges). Good for volatile pairs
 *   where you want to be available on big moves but light at spot.
 */
function buildWeights(
  bins: number[],
  activeBinId: number,
  strategy: DlmmStrategy,
): number[] {
  const n = bins.length;
  if (n === 0) return [];

  if (strategy === 'spot') {
    return bins.map(() => 1);
  }

  if (strategy === 'curve') {
    // Bell curve. σ ≈ N/6 keeps ~99% of weight inside the chosen range.
    const sigma = Math.max(1, n / 6);
    return bins.map((id) => {
      const d = id - activeBinId;
      return Math.exp(-(d * d) / (2 * sigma * sigma));
    });
  }

  // bidask: |distance| + 1 grows linearly away from active.
  return bins.map((id) => Math.abs(id - activeBinId) + 1);
}

/**
 * The core ratio computation. See file-level docstring for the model.
 *
 * Returns sensible defaults (single-sided / null ratios) for degenerate
 * inputs rather than throwing — the caller is a UI form, and a half-typed
 * range shouldn't crash the card. Validate inputs upstream if you want
 * harder guarantees.
 */
export function computeDlmmRatio(input: DlmmRatioInput): DlmmRatioResult {
  const { activeBinId, minBinId, maxBinId, binStep, strategy } = input;

  const lo = Math.min(minBinId, maxBinId);
  const hi = Math.max(minBinId, maxBinId);
  const bins: number[] = [];
  for (let i = lo; i <= hi; i++) bins.push(i);

  const stepFactor = 1 + binStep / 10_000;
  const activePrice = stepFactor > 0 ? Math.pow(stepFactor, activeBinId) : 0;

  if (bins.length === 0 || stepFactor <= 0 || activePrice <= 0) {
    return {
      activePrice: 0,
      needsX: false,
      needsY: false,
      xPerY: null,
      yPerX: null,
      xShareOfValue: 0,
      binCount: 0,
    };
  }

  const rawWeights = buildWeights(bins, activeBinId, strategy);
  const totalW = rawWeights.reduce((a, b) => a + b, 0) || 1;
  const weights = rawWeights.map((w) => w / totalW);

  // Accumulate Y-unit-value contributions per side. We measure value in Y
  // (not USD) because that's the unit `activePrice` is already in.
  //
  // For a bin below active: contributes only Y. value_Y_i = w_i.
  // For a bin above active: contributes only X. To express in Y-units we
  //   multiply by p_active (the price the user will see on confirm).
  // For the active bin: split 50/50 by value (see file docstring).
  let yShare = 0; // sum of Y-value weights on the Y side
  let xShareInY = 0; // sum of Y-value weights on the X side, expressed in Y units

  for (let i = 0; i < bins.length; i++) {
    const id = bins[i];
    const w = weights[i];
    if (id < activeBinId) {
      yShare += w;
    } else if (id > activeBinId) {
      xShareInY += w; // value-weight; conversion to X amount is /p_active
    } else {
      yShare += w * 0.5;
      xShareInY += w * 0.5;
    }
  }

  // Convert "X-side value in Y-units" to "X-amount per unit of Y-amount":
  //   y_total = yShare * V    (V = total value in Y units)
  //   x_total_in_Y = xShareInY * V
  //   x_total = x_total_in_Y / activePrice = xShareInY * V / activePrice
  //   ⇒ x_total / y_total = xShareInY / (yShare * activePrice)
  const xPerY = yShare > 0 && activePrice > 0
    ? (xShareInY / yShare) / activePrice
    : null;
  const yPerX = xShareInY > 0 && activePrice > 0
    ? (yShare / xShareInY) * activePrice
    : null;

  return {
    activePrice,
    needsX: xShareInY > 0,
    needsY: yShare > 0,
    xPerY,
    yPerX,
    xShareOfValue: xShareInY, // already a fraction of total since weights sum to 1
    binCount: bins.length,
  };
}

/**
 * Convenience: derive `[minBinId, maxBinId]` from a "spread" — the number
 * of bins to either side of the active bin. So `spread = 15` produces
 * `[active - 15, active + 15]` (31 bins total). Most users think in
 * "± N bins", which is what the form exposes.
 */
export function rangeFromSpread(
  activeBinId: number,
  spread: number,
): { minBinId: number; maxBinId: number } {
  const s = Math.max(0, Math.floor(spread));
  return {
    minBinId: activeBinId - s,
    maxBinId: activeBinId + s,
  };
}
