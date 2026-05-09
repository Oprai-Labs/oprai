/**
 * SolanaErrorDecoderService
 *
 * Translates raw Solana program errors into actionable, human-readable
 * messages. The wallet (Phantom etc.) and the RPC return things like
 * `Custom program error: 0x1771` or
 * `Transaction simulation failed: ... InstructionError [0, {Custom: 6005}]`.
 * That's correct, but useless to the user.
 *
 * This decoder maps the most common error codes from the protocols we
 * route through (Jupiter, Raydium, Orca, Meteora, Kamino, MarginFi, Pump.fun,
 * SystemProgram, TokenProgram) to a user-facing string. Unknown codes fall
 * through to a generic "transaction reverted" message that still includes
 * the original code so the user can search support docs.
 */

import { Injectable } from '@angular/core';

interface DecodedError {
  /** Short user-facing summary, suitable for an error toast. */
  summary: string;
  /** Optional follow-up action the user can take. */
  hint?: string;
  /** The raw code we matched on (kept for support / Sentry breadcrumbs). */
  rawCode?: string;
}

/** Known program errors. Keyed by either decimal code OR `0x`-prefixed hex. */
// Sources: each protocol's error.json / IDL as published on docs sites.
// Kept intentionally short — only entries we have actually seen in the wild
// or that map to a clear remediation. Unknown codes fall through.
const ERROR_TABLE: Record<string, DecodedError> = {
  // ── System Program ─────────────────────────────────────────────────
  '0x1':  { summary: 'Account doesn\'t have enough SOL for this transaction.', hint: 'Fund your wallet with a bit more SOL (transaction fees + token account rent).' },
  '0x2':  { summary: 'Transaction signature missing or invalid.' },

  // ── SPL Token ──────────────────────────────────────────────────────
  // 0x10 = TokenProgram error 16 = AccountFrozen
  '0x10': { summary: 'This token account is frozen by its issuer and can\'t be transferred.' },
  '0x11': { summary: 'Token mint mismatch — the source and destination accounts hold different tokens.' },

  // ── Jupiter aggregator ─────────────────────────────────────────────
  // Common: 0x1771 = SlippageToleranceExceeded
  '0x1771': { summary: 'Price moved outside your slippage tolerance.', hint: 'Try again with a higher slippage, or wait for the market to settle.' },
  '0x1788': { summary: 'Routing path exhausted — Jupiter couldn\'t complete the swap.', hint: 'Smaller amount, fewer hops, or a different output token usually fixes this.' },
  '0x1789': { summary: 'Output amount fell below the configured minimum.', hint: 'Same as a slippage hit — raise the limit or retry.' },

  // ── Raydium AMM ────────────────────────────────────────────────────
  '0x1f4':  { summary: 'Raydium pool has insufficient liquidity for this trade size.', hint: 'Split into smaller chunks or pick a deeper pool.' },
  '0x1f5':  { summary: 'Raydium price impact would exceed the configured limit.' },

  // ── Orca Whirlpool ─────────────────────────────────────────────────
  '0x1781': { summary: 'Orca position price range is outside the current price.', hint: 'Adjust the lower/upper tick boundaries.' },
  '0x1782': { summary: 'Orca tick array not initialised — pool warming up.', hint: 'Try again in a few seconds.' },

  // ── Meteora DLMM ───────────────────────────────────────────────────
  '0x1770': { summary: 'Meteora bin is empty for this price range.', hint: 'Pick a wider bin range or a more liquid pool.' },
  '0x1772': { summary: 'Meteora swap exceeded max bin crossings.', hint: 'Smaller swap or a different pool.' },

  // ── Kamino Lend ────────────────────────────────────────────────────
  '0x1747': { summary: 'Kamino: borrow would exceed your collateral capacity.' },
  '0x1748': { summary: 'Kamino: repay amount exceeds outstanding debt.' },
  '0x1749': { summary: 'Kamino: position is unhealthy; deposit more collateral or repay first.' },

  // ── MarginFi ───────────────────────────────────────────────────────
  '0x178c': { summary: 'MarginFi: account would become unhealthy after this action.' },
  '0x178d': { summary: 'MarginFi: bank deposit cap reached.' },

  // ── Pump.fun ───────────────────────────────────────────────────────
  '0x1771_pf': { summary: 'Pump.fun bonding curve slippage hit.', hint: 'Retry with a slightly higher SOL amount or wait a moment.' },
  '0x6005':    { summary: 'Pump.fun token has migrated to PumpSwap; use the AMM, not the bonding curve.' },
};

/** Phrases that classify a generic error message even without a code. */
const PHRASE_RULES: Array<{ match: RegExp; result: DecodedError }> = [
  {
    match: /block ?height (?:exceeded|expired)|blockhash not found|blockhash expired/i,
    result: {
      summary: 'Network was too busy and your transaction expired before landing.',
      hint: 'Retry — the next attempt will fetch a fresh blockhash.',
    },
  },
  {
    match: /User rejected|User denied|rejected the request/i,
    result: { summary: 'You rejected the transaction in your wallet.' },
  },
  {
    match: /insufficient (?:funds|lamports|sol)/i,
    result: {
      summary: 'Not enough SOL in this wallet to cover the transaction fee.',
      hint: 'Top up by ~0.01 SOL and retry.',
    },
  },
  {
    match: /Simulation failed/i,
    result: {
      summary: 'The transaction would fail on-chain — Solana refused to broadcast it.',
      hint: 'Common causes: stale data, slippage, or a paused pool. Wait a few seconds and retry.',
    },
  },
  {
    match: /^sim:unavailable$/,
    result: {
      summary: 'Transaction safety check is temporarily unavailable.',
      hint: 'Pre-sign simulation could not run on either RPC or backend. Retry in a few seconds.',
    },
  },
  {
    match: /^BLOCKHASH_EXPIRED$|blockhash not found|block height exceeded/i,
    result: {
      summary: 'The transaction expired before it reached the network.',
      hint: 'Solana blockhashes are only valid for ~60 seconds. Click Retry — a fresh transaction will be built and you\'ll be asked to sign again.',
    },
  },
  {
    match: /^sim:insufficient_tokens$/,
    result: {
      summary: 'Not enough tokens to complete this swap or transfer.',
      hint: 'Lower the amount or top up the source wallet.',
    },
  },
  {
    match: /^sim:slippage_exceeded$/,
    result: {
      summary: 'Price moved outside your slippage tolerance during simulation.',
      hint: 'Raise the slippage setting and retry.',
    },
  },
  {
    match: /^sim:/,
    result: {
      summary: 'Transaction simulation indicated this would fail.',
      hint: 'Adjust the inputs and try again, or wait for the market to settle.',
    },
  },
  {
    match: /timed? ?out|timeout/i,
    result: {
      summary: 'Network or RPC timed out before confirming.',
      hint: 'The tx may still land — check your wallet history before retrying.',
    },
  },
];


@Injectable({ providedIn: 'root' })
export class SolanaErrorDecoderService {
  /**
   * Best-effort decode. Always returns *something* useful — never the raw
   * "Custom program error: 0x1771" string the wallet handed us.
   */
  decode(raw: string | null | undefined): DecodedError {
    const text = (raw ?? '').toString();
    if (!text) {
      return { summary: 'The transaction failed but no detail was reported.' };
    }

    // 1. Try to extract a `0x...` or numeric error code.
    const codeMatch =
      text.match(/0x[0-9a-fA-F]{1,8}/) ||
      text.match(/Custom: (\d+)/) ||
      text.match(/error code: (\d+)/i) ||
      text.match(/InstructionError.*?(\d+)/);
    if (codeMatch) {
      const raw = codeMatch[0];
      // Normalise "Custom: 6005" → "0x1775", but accept the decimal lookup too.
      const decimal = codeMatch[1];
      const hex = raw.startsWith('0x') ? raw.toLowerCase() : '0x' + Number(decimal).toString(16);
      const found = ERROR_TABLE[hex] || ERROR_TABLE[decimal ?? ''];
      if (found) {
        return { ...found, rawCode: hex };
      }
    }

    // 2. Phrase-based fallbacks.
    for (const rule of PHRASE_RULES) {
      if (rule.match.test(text)) {
        return rule.result;
      }
    }

    // 3. Last resort — reflect the raw error but in plain language.
    return {
      summary: 'Transaction reverted on-chain.',
      hint:
        'Cause is unclear from the error. Try again in 10 seconds; if it persists, ' +
        'reduce the amount or pick a different route.',
      rawCode: text.slice(0, 120),
    };
  }
}
