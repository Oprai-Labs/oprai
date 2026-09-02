/**
 * Turning what went wrong into something a person can act on.
 *
 * This lived inside the action card, which meant only the action card could
 * use it — so the pages that build their own transactions (burn, stake) put
 * raw server text on screen instead. Same failure, different component. It is
 * shared now, and every surface that shows an error goes through it.
 *
 * The rule it enforces: everything a user can do something about is mapped to
 * a sentence; anything still reading like machinery afterwards is, by
 * definition, unmapped — and goes to the console rather than the screen.
 */

/**
 * Per-action MIN AMOUNT guardrails — surfaced on the amount input so users
 * see the required floor BEFORE submitting (and before the on-chain "below
 * protocol's minimum" rejection). Values reflect protocol-side minimums
 * plus typical rent-exempt overheads on Solana:
 *   - 0.000891 SOL = wallet account creation rent (transfer to a new wallet)
 *   - 0.00204 SOL  = SPL token ATA rent (recipient or LST receiving account)
 *   - 0.001 SOL    = practical floor for most LST + bonding-curve flows
 *   - 1 SOL        = native delegated stake minimum (cluster policy)
 *
 * The `hint` is what shows above the input. The `min` enforces HTML5
 * client-side validation on the number field. Both are advisory — final
 * authority is the on-chain program — but most "amount below minimum"
 * errors come from users typing 0 or sub-dust values.
 */
export const ACTION_MIN_AMOUNT: Record<string, { amount: number; hint: string; unit?: string }> = {
  // Pump.fun bonding curve buys/sells
  pumpfun_buy:    { amount: 0.001,    hint: 'Minimum 0.001 SOL — sub-dust trades fail on the bonding curve', unit: 'SOL' },
  pumpswap_buy:   { amount: 0.001,    hint: 'Minimum 0.001 SOL — PumpSwap AMM rejects micro trades',         unit: 'SOL' },
  pumpfun_sell:   { amount: 1,        hint: 'Token units — sub-1 amounts round to zero given pump.fun decimals' },
  pumpswap_sell:  { amount: 1,        hint: 'Token units' },
  // Jupiter aggregator
  swap:           { amount: 0.000001, hint: '' },
  // Plain transfers — account creation rent matters if recipient is brand-new
  transfer:       { amount: 0.000001, hint: 'New wallet recipients require ~0.000891 SOL for account rent' },
  // Liquid staking (jito / marinade / jupSOL / bSOL)
  stake:          { amount: 0.001,    hint: 'Minimum 0.001 SOL — most LSTs reject smaller deposits',  unit: 'SOL' },
  unstake:        { amount: 0.001,    hint: 'Minimum 0.001 token' },
  jito_stake:     { amount: 0.001,    hint: 'Minimum 0.001 SOL', unit: 'SOL' },
  marinade_stake: { amount: 0.001,    hint: 'Minimum 0.001 SOL', unit: 'SOL' },
  jupsol_stake:   { amount: 0.001,    hint: 'Minimum 0.001 SOL', unit: 'SOL' },
  bsol_stake:     { amount: 0.001,    hint: 'Minimum 0.001 SOL', unit: 'SOL' },
  // Native delegated stake (cluster minimum)
  native_stake:   { amount: 1,        hint: 'Minimum 1 SOL (Solana cluster rule)', unit: 'SOL' },
  lend:           { amount: 0.000001, hint: 'Smallest meaningful amount; markets reject true zero' },
  withdraw:       { amount: 0.000001, hint: 'Use "all" to withdraw the full balance' },
  borrow:         { amount: 0.000001, hint: 'Below dust threshold borrows revert' },
  repay:          { amount: 0.000001, hint: 'Use "all" to clear the debt completely' },
  // Liquidity provision (generic)
  add_liquidity:    { amount: 0.001, hint: 'Each side at least 0.001 token to clear pool minimums' },
  remove_liquidity: { amount: 0.000001, hint: 'Use "all" to close the position' },
  // Raydium CLMM / AMM
  raydium_open_position:     { amount: 0.001, hint: 'Each side ≥ 0.001 token; very tight ranges need larger deposits to clear pool liquidity minimums. Try a wider range if it fails.' },
  raydium_add_liquidity:     { amount: 0.001, hint: 'Each side ≥ 0.001 token. Wider range = easier minimum.' },
  raydium_increase_position: { amount: 0.001, hint: 'Minimum 0.001 token' },
  raydium_decrease_position: { amount: 0.000001, hint: 'Use "all" to remove all liquidity' },
  // Meteora DLMM / DAMM
  meteora_dlmm_add_liquidity:   { amount: 0.001, hint: 'Each bin needs ≥ 0.001 token; wider bin spread = easier minimum' },
  meteora_dammv2_add_liquidity: { amount: 0.001, hint: 'Each side ≥ 0.001 token' },
  meteora_open_position:        { amount: 0.001, hint: 'Each side ≥ 0.001 token' },
  meteora_add_to_position:      { amount: 0.001, hint: 'Minimum 0.001 token' },
  // Orca Whirlpool
  orca_open_position:        { amount: 0.001, hint: 'Each side ≥ 0.001 token; tight ranges need larger deposits' },
  orca_increase_liquidity:   { amount: 0.001, hint: 'Minimum 0.001 token' },
  orca_decrease_liquidity:   { amount: 0.000001, hint: 'Use "all" to remove all' },
  // Cross-chain
  bridge:           { amount: 0.5,   hint: 'Bridge solvers typically require ≥ 0.5 USDC equivalent', unit: 'USDC' },
  relay_bridge:     { amount: 0.5,   hint: 'Bridge solvers typically require ≥ 0.5 USDC equivalent', unit: 'USDC' },
};

/**
 * User-facing strings for the simulation error codes we already know how
 * to classify in `SolanaActionService.parseSimulationError`. Anything not
 * in this map falls through to the generic-fallback branch below.
 */
const SIM_ERROR_MESSAGES: Record<string, string> = {
  insufficient_tokens:
    "Not enough balance for this action. Check the token amount, fees, and the rent buffer left for SOL.",
  slippage_exceeded:
    "Price moved too much during quote. Try increasing slippage tolerance, or split into smaller trades.",
};

/**
 * Best-effort English translation of common low-numbered custom error
 * codes. Many Solana programs return `ProgramError::Custom(N)` for
 * generic preconditions — keep the wording PROTOCOL-NEUTRAL because
 * the same code (e.g. 101) means different things on different
 * programs (Marinade: min stake; Raydium CLMM: invalid input). Mention
 * a specific protocol only inside the action's own handler, not here.
 */
const SIM_GENERIC_HINTS: Record<string, string> = {
  '101':
    "The amount is below the protocol's minimum, or one of the input accounts isn't in the state the program expects. Try a slightly larger amount.",
  '102':
    "Account state precondition failed. Make sure the right wallet is connected and that no conflicting transaction is in flight.",
};

/**
 * Action-specific overrides for known simulation error codes. Raydium /
 * Meteora / Orca all reuse low Anchor framework codes (101 = ConstraintRaw,
 * 102 = ConstraintSeeds, etc.) for protocol-level constraint failures.
 * Those codes have NOTHING to do with "minimum amount" — surfacing the
 * generic hint actively misleads the user. When we know the action and the
 * code, use the more accurate explanation.
 */
/**
 * Orca Whirlpool program errors, shared by every Orca position action.
 *
 * Without these, 6015 fell through to the generic hint — "insufficient
 * balance, or an amount below the protocol's minimum, try a slightly larger
 * amount" — which is advice you cannot act on when the action is a CLOSE with
 * no amount at all.
 */
const ORCA_WHIRLPOOL_HINTS: Record<string, string> = {
  '6005': "This position still holds liquidity, so it can't be closed yet. Withdraw it first, then close.",
  '6015': "The position's liquidity changed while this was being prepared — usually another deposit or withdrawal landing first. Ask for your positions again and retry.",
  '6016': "The pool's liquidity moved mid-transaction. Retry in a moment.",
  '6017': "The deposit would need more tokens than the slippage allows. Raise the slippage or lower the amount.",
  '6018': "The withdrawal would return less than the slippage allows. Raise the slippage or retry — the price is moving.",
  '6020': "This wallet doesn't hold the position's NFT, so it can't act on it.",
  '6035': "The amount rounds to zero at this pool's precision. Use a larger amount.",
  '6036': "Price moved and the swap would return less than the minimum. Raise the slippage or retry.",
  '6037': "Price moved and the swap would cost more than the maximum. Raise the slippage or retry.",
  // Anchor's generic constraint failure — for a Whirlpool this is almost
  // always tick alignment or stale pool state.
  '101': "Whirlpool constraint failed — usually tick alignment or stale pool data. Try a wider range, or refresh the pool and retry.",
};

/**
 * pump.fun's bonding curve prices every trade off reserves that move with each
 * buy. A transaction carries a ceiling; when the curve has moved past it by the
 * time the transaction lands, the program rejects it rather than overspending.
 */
const PUMPFUN_CURVE_HINTS: Record<string, string> = {
  '6002': "The token's price moved before this trade landed, so it would have cost more SOL than the transaction allowed. Raise the slippage a little and try again.",
  '6003': "The token's price moved before this trade landed, so the sale would have returned less SOL than the transaction allowed. Raise the slippage a little and try again.",
};

const SIM_HINTS_BY_ACTION: Record<string, Record<string, string>> = {
  orca_open_position:     ORCA_WHIRLPOOL_HINTS,
  orca_increase_position: ORCA_WHIRLPOOL_HINTS,
  orca_decrease_position: ORCA_WHIRLPOOL_HINTS,
  orca_close_position:    ORCA_WHIRLPOOL_HINTS,
  orca_collect_fees:      ORCA_WHIRLPOOL_HINTS,
  orca_collect_rewards:   ORCA_WHIRLPOOL_HINTS,
  orca_swap:              ORCA_WHIRLPOOL_HINTS,
  raydium_open_position: {
    '101':
      "Pool returned a constraint error. Most common causes: tick range falls outside the pool's tracked observation window, the pool isn't a CLMM pool (no concentrated-liquidity support for this pair), or the deposit ratio doesn't match the current price. Try the Full range preset, refresh the pool data, or use Raydium Add Liquidity (CPMM) instead.",
    '102':
      "Pool seed/account constraint failed. Likely a stale pool reference. Refresh the page and re-pick the pool.",
  },
  raydium_add_liquidity: {
    '101':
      "Pool constraint failed. Verify the pair is a Raydium CPMM/standard pool and that both deposit amounts are non-zero.",
  },
  meteora_dlmm_add_liquidity: {
    '101':
      "DLMM bin constraint failed. The price range may cross more bins than the per-tx limit (commonly ≤ 70 bins). Narrow the range or split into multiple positions.",
  },
  borrow: {
    // Jupiter Lend vaults program: position would exceed the vault's collateral
    // factor (borrow too large / collateral too small).
    '6011':
      "This borrow would exceed what your collateral supports — the position's LTV is above the vault's limit. Add more collateral or borrow a smaller amount.",
  },
  // Kamino K-Lend (klend program) error codes. These 60xx codes mean something
  // DIFFERENT here than in the Jupiter Lend vaults program (e.g. 6018 is
  // ObligationReserveLimit for KLend but VaultExcessDebtPayback for Jupiter),
  // so they MUST stay action-scoped and never fall through to a shared map.
  kamino_borrow: {
    '6012': "Borrow amount is too small — after Kamino's origination fee you'd receive nothing. Borrow a larger amount.",
    '6013': "This borrow is too large for your deposited collateral. Borrow a smaller amount, or deposit more collateral on Kamino first.",
    '6008': "Kamino's reserve doesn't have enough of this token available to borrow right now. Try a smaller amount or a different token.",
    '6020': "You have no collateral deposited on Kamino yet. Deposit a token first, then borrow against it.",
  },
  kamino_deposit: {
    '6018': "Kamino's deposit cap for this reserve is reached. Try a smaller amount or a different token.",
  },
  kamino_withdraw: {
    '6010': "Withdraw amount is too small. Enter a larger amount.",
    '6011': "You can't withdraw this much — either it exceeds your deposit or it would drop your collateral below what your Kamino borrows require. Withdraw less, or repay debt first.",
  },
  kamino_withdraw_collateral: {
    '6011': "Withdrawing this much collateral would push your Kamino position under its required health. Withdraw less, or repay some debt first.",
  },
  kamino_repay: {
    '6014': "Repay amount is too small to transfer. Enter a larger amount.",
    // NetValueRemainingTooSmall. Kamino tracks debt as a continuously-growing
    // scaled fraction, so a fixed decimal repay always leaves sub-unit dust and
    // this fires. Use "Max" / "repay all" (it closes the whole line at once). If
    // that still fails, your token balance is a hair below the accrued debt —
    // top up a tiny amount of the token, then repay all.
    '6092': "Kamino won't leave a sub-cent dust balance behind. Use \"Max\" to repay the full debt in one go. If that still fails, your token balance is just under the accrued interest — add a tiny amount of the token and repay all.",
    // SPL Token InsufficientFunds during a repay-all: the wallet holds slightly
    // less of the token than the debt + its accrued interest (Kamino rounds the
    // debt up to fully close). Action-scoped: in a repay, Custom 1 = not enough
    // of the repaid token, never a SOL-fee issue.
    '1': "Your wallet is just short of the full debt — Kamino rounds up to close it completely, and you don't quite have enough of the token. Add a tiny amount of it and repay all.",
  },
  // Kamino Multiply (leveraged looping). Exact KLend codes:
  //   6089 = Cannot borrow above borrow limit (the debt reserve's borrow cap)
  //   6091 = Reserve does not accept new borrows outside its elevation group
  //   6011 = borrow exceeds the position's allowed value (LTV/health)
  // 6089 can mean EITHER the user borrows too much (lower leverage helps) OR the
  // reserve is globally maxed (no amount helps → different pool), so say both.
  kamino_multiply_open: {
    '6089': "Kamino won't let this position borrow more from the pool's debt reserve. Lower the leverage or reduce the collateral amount. If it still fails, this pool's debt reserve is at its borrow cap — pick a different pool.",
    '6091': "This pool's debt reserve only accepts borrows inside its elevation group, which this pair can't use for a leveraged position. Pick a different collateral/debt pool.",
    '6011': "At this leverage the position would exceed its allowed loan-to-value. Lower the leverage or increase the collateral amount.",
  },
  kamino_multiply_add: {
    '6089': "Kamino won't let this position borrow more from the pool's debt reserve. Add a smaller amount, or reduce leverage first. If it still fails, the reserve is at its borrow cap.",
    '6091': "This pool's debt reserve only accepts borrows inside its elevation group, which this position can't use. You can't add leverage to this pool.",
    '6011': "This addition would push the position past its allowed loan-to-value. Add a smaller amount.",
  },
  // pump.fun bonding curve. 6002 = TooMuchSolRequired on a buy, 6003 =
  // TooLittleSolReceived on a sell — both are the price moving past the
  // ceiling the transaction carried. Action-scoped because 6003 means
  // something else entirely on a Jupiter route.
  pumpfun_buy:  { ...PUMPFUN_CURVE_HINTS },
  pumpfun_sell: { ...PUMPFUN_CURVE_HINTS },
  launch_token: { ...PUMPFUN_CURVE_HINTS },
  pumpfun_launch: { ...PUMPFUN_CURVE_HINTS },
};

/**
 * Raw parameter keys → what the user actually sees on the card. A backend
 * validation error names the wire field (`amountY`, `poolId`); repeating that
 * at the user tells them nothing, because no label on screen says "amountY".
 */
const PARAM_LABELS: Record<string, string> = {
  amount: 'amount', amountA: 'first token amount', amountB: 'second token amount',
  amountX: 'first token amount', amountY: 'second token amount',
  amountIn: 'amount', amountOut: 'amount', collateralAmount: 'collateral amount',
  lpAmount: 'LP amount', bpsToRemove: 'withdrawal percentage',
  pool: 'pool', poolId: 'pool', position: 'position', positionId: 'position',
  vault: 'vault', market: 'market', reserve: 'reserve', obligation: 'loan',
  mint: 'token', tokenA: 'first token', tokenB: 'second token',
  inputMint: 'token you pay', outputMint: 'token you receive',
  to: 'recipient address', recipient: 'recipient address', wallet: 'wallet',
  minBinId: 'price range', maxBinId: 'price range',
  minPrice: 'minimum price', maxPrice: 'maximum price',
  slippageBps: 'slippage', slippage: 'slippage', leverage: 'leverage',
  strategy: 'strategy', symbol: 'symbol', name: 'name',
};

/**
 * Anchor error NAMES that reach us in a simulation log tail. The numeric codes
 * are mapped elsewhere; these are the ones that arrive as text.
 */
const ANCHOR_NAME_HINTS: Array<[RegExp, string]> = [
  [/NonEmptyPosition/i, 'This position still holds liquidity. Withdraw it first, then close the position.'],
  [/AccountNotInitialized/i, 'An account this action needs doesn’t exist yet. It has to be created first — try again in a moment.'],
  [/NotEnoughAccountKeys|AccountNotEnoughKeys/i, 'This action couldn’t be prepared correctly. Please try again in a moment.'],
  [/InstructionDidNotDeserialize/i, 'This action couldn’t be prepared correctly. Please try again in a moment.'],
  [/ConstraintSeeds|ConstraintOwner|ConstraintMut/i, 'The protocol rejected one of the accounts for this action. Please try again in a moment.'],
  [/InsufficientFunds|insufficient lamports|insufficient funds/i, 'Not enough balance to complete this — check the amount and that the wallet has some SOL for fees.'],
  [/ExceededBinSlippageTolerance/i, 'The pool price moved past the allowed range while preparing this. Try again, or widen the slippage.'],
  [/SlippageToleranceExceeded|slippage/i, 'The price moved more than your slippage allows. Raise the slippage or try again.'],
];

/**
 * True when a string still reads like machinery rather than a sentence:
 * identifiers, field names, codes, payloads. Everything genuinely useful is
 * mapped to prose before this point, so anything tripping these markers is by
 * definition unmapped — and a user can do nothing with it.
 */
function looksTechnical(raw: string): boolean {
  // Already-shortened addresses ("4jFv…pxcJV") are a deliberate, readable
  // reference — don't let their mixed case trip the identifier heuristics.
  const text = raw.replace(/\S*…\S*/g, '');
  return (
    /`[^`]+`/.test(text) ||                       // backtick-quoted identifiers
    /\b[a-z]+(?:_[a-z0-9]+){1,}\b/.test(text) ||  // snake_case: meteora_add_to_position
    /\b[a-z]+[A-Z][a-zA-Z]*\b/.test(text) ||      // camelCase: amountY, poolId
    /\b[A-Z][a-z]+[A-Z][a-zA-Z]*\b/.test(text) || // PascalCase error names
    /error decoding response|failed to parse|reqwest|panicked|unwrap|deserialize/i.test(text) ||
    /status \d{3}|\bhttp\b|\b\d{3}:\s|\{\s*"|\[\s*\{|https?:\/\/|<[a-z!/]/i.test(text) ||
    /0x[0-9a-f]+/i.test(text) ||
    /\bnull\b|\bundefined\b|\bNone\b|context canceled/i.test(text) ||
    // Implementation vocabulary. A sentence can be perfectly grammatical and
    // still be written for whoever maintains the service: "position lookup by
    // id is temporarily unsupported after the datapi migration" tells a user
    // nothing they can do.
    /\b(migration|endpoint|upstream|proxy|gateway|discriminator|serializ|deserializ|unsupported|not implemented|datapi|sdk|rpc|payload|schema|struct|enum)\b/i.test(text)
  );
}

export function sanitizeErrorMessage(msg: string, actionType?: string): string {
  // Strip internal API instructions (e.g. "Upload via POST /upload/...") that
  // leak from backend errors. "See" is deliberately NOT in this list: "See
  // explorer for details" is written for the user, and stripping it left the
  // card saying only "The transaction failed on-chain."
  let out = msg.replace(/\.\s+(Upload|Call|Use|POST|GET|Retry)\s+.*/i, '.').trim();

  // Base58 addresses are for explorers, not sentences. Shorten rather than
  // discard: the surrounding message is often the useful part.
  out = out.replace(/\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g, m => `${m.slice(0, 4)}…${m.slice(-4)}`);

  // web3.js SendTransactionError stringifies with the FULL program-log array and
  // a developer-only "call getLogs()" tail. Never surface raw logs to users —
  // drop the "Logs: [...]" dump and that tail up front, before any classification.
  out = out
    .replace(/\.?\s*Logs:\s*\[[\s\S]*?\]\.?/i, '')
    .replace(/\s*Catch the [`']?SendTransactionError[`']?[\s\S]*$/i, '')
    .trim();

  // EVM actions (Uniswap, Relay, cross-chain) pay gas in the CHAIN'S native
  // token — ETH/BNB/… — never SOL. Their "insufficient funds" errors must not
  // be routed through the Solana-worded balance/sim messages below, which talk
  // about SOL and rent. Handle them here, up front, with chain-neutral wording.
  const isEvmAction = /^uniswap_|^relay_|^cross_chain|^bridge$/.test(actionType ?? '');
  if (isEvmAction && /insufficient funds|InsufficientFunds|exceeds balance|transfer amount exceeds|insufficient allowance|gas required exceeds|cannot estimate gas|execution reverted|UNPREDICTABLE_GAS_LIMIT/i.test(out)) {
    // Keep the raw wallet/RPC error in the console — the friendly text hides the
    // real cause (a gas-estimation revert reads identically to a true shortfall).
    console.error('[action] EVM tx failed:', { actionType, raw: msg });
    if (/insufficient allowance|transfer amount exceeds|cannot estimate gas|execution reverted|UNPREDICTABLE_GAS_LIMIT/i.test(out)) {
      return 'The transaction would fail on-chain right now — the token approval may still be settling, or the amount/price moved. Try again in a few seconds.';
    }
    return 'Not enough balance for this — check the amount, and that the wallet has enough of the chain’s native token (e.g. ETH) to cover gas.';
  }

  // Raw on-chain program error thrown at SUBMIT time (not preflight sim, which
  // arrives as a `sim:*` machine code below). Classify the custom error code
  // from hex ("0x1771") or decimal form and reuse the same friendly text, so a
  // technical program dump never reaches the user. Action-specific hints win,
  // then well-known Jupiter slippage codes (6001 = 0x1771, 6003).
  {
    const raw = out.match(/custom program error:\s*(?:0x([0-9a-f]+)|(\d+))/i);
    if (raw) {
      const code = raw[1] ? parseInt(raw[1], 16) : parseInt(raw[2], 10);
      const actionHint = actionType ? SIM_HINTS_BY_ACTION[actionType]?.[String(code)] : undefined;
      if (actionHint) return actionHint;
      if (code === 6001 || code === 6003) return SIM_ERROR_MESSAGES['slippage_exceeded'];
      if (code === 1) return SIM_ERROR_MESSAGES['insufficient_tokens'];
      const gh = SIM_GENERIC_HINTS[String(code)];
      if (gh) return gh;
      return `The transaction failed on-chain (program error ${code}). This is usually a slippage, minimum-amount, or account-state issue — adjust the amount or slippage and retry.`;
    }
  }

  // Pre-flight SOL-fee-shortfall thrown by solana-action.service before signing.
  // Format: "insufficient_fee:need=0.000010,have=0.000000"
  // Translate to plain English with concrete numbers and an actionable next step.
  const feeMatch = out.match(/insufficient_fee:need=([\d.]+),have=([\d.]+)/);
  if (feeMatch) {
    const need = feeMatch[1];
    const have = feeMatch[2];
    const haveNum = parseFloat(have);
    const shortage = (parseFloat(need) - haveNum).toFixed(6);
    // The figure now includes rent on accounts the action creates, not just
    // the network fee — on a position that is most of it, and most of it
    // comes back. Calling all of it a "fee" would read as money burned and
    // talk someone out of an action that costs them almost nothing to hold.
    const opensPosition = /open_position|add_liquidity|_stake$/.test(actionType ?? '');
    const what = opensPosition
      ? 'to open this — most of that is a deposit on the accounts it creates, which comes back when you close'
      : 'to cover the transaction fee';
    if (haveNum === 0) {
      return `Your wallet has 0 SOL — this needs at least ${need} SOL ${what}. Top up the connected wallet and try again.`;
    }
    return `Wallet has ${have} SOL but needs ${need} SOL ${what}. Add about ${shortage} more SOL and retry.`;
  }

  // Build a "concrete floor" suffix from the action catalog so error
  // messages tell the user the EXACT minimum to type (e.g. "Try at least
  // 0.001 SOL"), not just the generic "try a slightly larger amount."
  const minCfg = actionType ? ACTION_MIN_AMOUNT[actionType] : undefined;
  const floorSuffix = minCfg
    ? ` This action's minimum is ${minCfg.amount}${minCfg.unit ? ` ${minCfg.unit}` : ''}.`
    : '';

  // The spend guard speaks for itself: it compared what the transaction would
  // actually take against what the card promised, and the numbers it names are
  // the point. Strip the marker and pass it through untouched.
  const guarded = out.match(/guard:(.+)$/s);
  if (guarded) {
    return guarded[1].trim();
  }

  // Translate machine codes from `parseSimulationError`. The shape is one of:
  //   sim:insufficient_tokens
  //   sim:slippage_exceeded
  //   sim:generic:<code-or-text>
  // We look for the prefix anywhere in the string so it still works after a
  // wrapper has prepended its own "Failed to execute action: " label.
  const known = out.match(/sim:(insufficient_tokens|slippage_exceeded)/);
  if (known) {
    return SIM_ERROR_MESSAGES[known[1]] ?? out;
  }
  // Anchor AccountNotInitialized (3012) — name the account so the exact missing
  // setup (a token account or lending position) is visible instead of a vague
  // "below minimum" message.
  const acctNotInit = out.match(/sim:account_not_init:([A-Za-z0-9_]+)/);
  if (acctNotInit) {
    const acct = acctNotInit[1] === 'unknown' ? 'a required account' : `the "${acctNotInit[1]}" account`;
    return `Simulation failed: ${acct} isn't initialized yet. A token account or lending position still needs to be created first.${floorSuffix}`;
  }
  const generic = out.match(/sim:generic:([A-Za-z0-9_-]+)/);
  if (generic) {
    const code = generic[1];
    // Action-specific override takes priority — protocol-level codes
    // (Raydium / Meteora / Orca 101 etc.) get accurate context instead of
    // the misleading "below minimum" fallback.
    const actionHints = actionType ? SIM_HINTS_BY_ACTION[actionType] : undefined;
    const actionHint = actionHints?.[code];
    if (actionHint) return actionHint;
    const hint = SIM_GENERIC_HINTS[code];
    if (hint) return hint + floorSuffix;
    if (/^\d+$/.test(code)) {
      return `The transaction simulation failed (program error ${code}). The most common causes are insufficient balance for fees + rent, or an amount below the protocol's minimum.${floorSuffix} Try a slightly larger amount.`;
    }
    return `The transaction simulation failed before signing. Check that your wallet has enough SOL for fees and that the amount meets the protocol's minimum.${floorSuffix}`;
  }

  // Backend parameter validation, e.g.
  //   "Invalid meteora_add_to_position params: missing field `amountY`"
  // Name the field the way the CARD names it — nothing on screen says
  // "amountY", so echoing the wire key tells the user nothing they can act on.
  {
    const missing = out.match(/missing field\s*[`'"]?([A-Za-z0-9_]+)/);
    if (missing) {
      const label = PARAM_LABELS[missing[1]];
      return label
        ? `This action still needs the ${label}. Fill it in and try again.`
        : 'This action is missing one of its required values. Check the fields above and try again.';
    }
    const invalidField = out.match(/invalid (?:value for )?(?:field\s*)?[`'"]([A-Za-z0-9_]+)[`'"]/i);
    if (invalidField) {
      const label = PARAM_LABELS[invalidField[1]];
      return label
        ? `The ${label} isn’t valid. Check it and try again.`
        : 'One of the values for this action isn’t valid. Check the fields above and try again.';
    }
    if (/^invalid\s+[a-z0-9_]+\s+params/i.test(out)) {
      return 'Some of the values for this action aren’t valid. Check the fields above and try again.';
    }
  }

  // Simulation could not be reached at all. The status/detail tail is for the
  // console, not the card.
  if (/sim:unavailable/i.test(out)) {
    return 'Couldn’t check this transaction before signing. Please try again in a moment.';
  }

  // Anchor error NAMES surfaced in a log tail.
  for (const [re, hint] of ANCHOR_NAME_HINTS) {
    if (re.test(out)) return hint;
  }

  // ── Never surface a raw upstream API response body to the user ────────────
  // Backend errors arrive wrapped in an AppError Display prefix, e.g.
  // "Jupiter API error: <message>" or "Internal error: Jupiter Perps API error
  // (500): <message>". Strip those prefixes so a clean tail shows through, then
  // map known raw shapes to friendly text and nuke anything still raw-looking.
  out = out
    .replace(/^(Failed to execute action:\s*)/i, '')
    .replace(/^((Jupiter( Perps)?|Relay|PumpFun|Pump\.fun) API error|Internal error|Protocol error|Solana RPC error|Invalid parameters?)(\s*\(\d+\))?:\s*/i, '')
    .replace(/^((Jupiter( Perps)?|Relay) API error|Internal error)(\s*\(\d+\))?:\s*/i, '')
    .trim();

  const lower = out.toLowerCase();

  // Known raw upstream shapes → friendly text.
  if (/quote failed|could not find any route|no route.*(found|available)|not enough liquidity|routeplan|no liquidity for this pair/i.test(lower)) {
    return 'No route available for this trade right now — the pair may lack liquidity or the amount may be too small. Try a different amount or token.';
  }
  if (/swap transaction failed|failed to (build|parse).*(swap|quote)|couldn.t build this transaction/i.test(lower)) {
    return 'Couldn’t build this transaction right now. Please try again in a moment.';
  }
  if (/dca order creation failed|create.*dca.*failed|set up the dca order/i.test(lower)) {
    return 'Couldn’t set up the DCA order right now. Please check the amount and try again.';
  }
  if (/cancel dca failed|cancel the dca order/i.test(lower)) {
    return 'Couldn’t cancel the DCA order right now. Please try again in a moment.';
  }
  // Kept without the PumpPortal clause: nothing waits on a third party's
  // indexing any more, but a launch can still race the create confirmation.
  if (/initial buy build failed|may not be indexed yet|isn.t ready to trade yet/i.test(lower)) {
    return 'The token isn’t ready to trade yet — give it a few seconds after launch and try again.';
  }
  if (/pumpfun api|pump\.fun (api|is temporarily)/i.test(lower)) {
    return 'Pump.fun is temporarily unavailable. Please try again in a moment.';
  }
  // pools.trade launch field validation (name/ticker) — surface the fixable rule.
  if (/tokensymbol/i.test(lower)) {
    return 'The ticker must be 1–8 letters or numbers (no spaces or symbols).';
  }
  if (/tokenname/i.test(lower)) {
    return 'That token name isn’t accepted — try a shorter, simpler name.';
  }
  if (/description/i.test(lower) && /pools\.trade|invalid|required|pattern/i.test(lower)) {
    return 'Add a short description for your token, then try again.';
  }
  // pools.trade Crowd Launch (CCA): a bid on an auction that's over / not open.
  if (/submitbid|prepare ?bid|estimate gas for bid|auction (has )?(ended|closed|not)/i.test(lower)) {
    return 'This crowd launch isn’t accepting commitments right now — the auction has ended or hasn’t opened yet.';
  }
  if (/no (swap )?route|no pay-token route|tradingapinoroute/i.test(lower)) {
    return 'This token can’t be traded yet — it isn’t live on a pool. If it’s a crowd launch, commit to the auction instead.';
  }

  // OpenSea order price increment: "Bids at this price are not allowed. Please
  // place a bid in the nearest allowed increment. 4 decimals allowed. Received
  // 0.000991 per unit, expected 0.001." — tell them the exact step and number.
  if (/nearest allowed increment|decimals allowed|bids at this price are not allowed/i.test(lower)) {
    const dec = /(\d+)\s*decimals? allowed/i.exec(msg)?.[1];
    const expected = /expected\s+([0-9.]+)/i.exec(msg)?.[1];
    return `OpenSea only accepts prices with up to ${dec ?? 4} decimals`
      + (expected ? ` — try ${expected}.` : ' — round your amount and try again.');
  }
  // Catch-all. Everything a user can act on has been mapped to prose above, so
  // whatever is still here is unmapped by definition — and if it still reads
  // like machinery (identifiers, field names, codes, payloads) it is worse
  // than useless on a card: it looks like the app broke rather than like
  // something the user can fix. Keep the original in the console for us.
  if (looksTechnical(out)) {
    console.error('[action] unmapped error:', { actionType, raw: msg });
    return 'Something went wrong completing this action. Please try again in a moment.';
  }

  return out;
}
