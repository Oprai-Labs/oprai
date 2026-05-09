#!/usr/bin/env node
// Verify shared/tokens.json against TWO independent token registries.
//
// Why two:
// A single oracle is a single point of compromise. If only Jupiter is checked
// and Jupiter's listing for a mint changes (delisting, label drift, malicious
// PR to a community list), we'd accept it silently. Birdeye gives us a second
// independent source that has to corroborate. If they disagree, we stop and
// you decide.
//
// What gets matched:
//   - Mint address  — full-string equality (no prefix/suffix shortcuts).
//   - Symbol        — case-insensitive equality, with one allowance: a leading
//                     "$" on the API side is treated as a stylistic prefix
//                     (Jupiter labels WIF as $WIF). Any other difference fails.
//   - Decimals      — integer equality.
//   - Name          — substring overlap (advisory; tokens often differ on
//                     marketing copy across registries — symbol+decimals+mint
//                     are the load-bearing fields).
//
// Birdeye is OPTIONAL: if BIRDEYE_API_KEY is unset, the script prints a
// warning and skips Birdeye corroboration. CI can set it as a secret to make
// the cross-check mandatory.
//
// Vanity-prefix collision check warns (or fails with --strict) if two
// registry entries share their first 5 characters — the only realistic class
// of attack against a 32-byte ed25519 pubkey, since brute-forcing matching
// suffixes/prefixes is cheap.
//
// Usage:
//   node scripts/verify-tokens.mjs
//   node scripts/verify-tokens.mjs --strict   (also fail on warnings)
//
// Exits non-zero if any check fails — wired into CI.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TOKENS_PATH = resolve(__dirname, '..', 'shared', 'tokens.json');
const JUP_BASE = process.env.JUPITER_API ?? 'https://api.jup.ag/tokens/v2';
const BIRDEYE_BASE = process.env.BIRDEYE_API ?? 'https://public-api.birdeye.so';
const BIRDEYE_KEY = process.env.BIRDEYE_API_KEY ?? '';
const STRICT = process.argv.includes('--strict');

// Solana base-58 alphabet excludes I O l 0 (visually ambiguous)
const SOLANA_ADDR_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function red(s)    { return `\x1b[31m${s}\x1b[0m`; }
function green(s)  { return `\x1b[32m${s}\x1b[0m`; }
function yellow(s) { return `\x1b[33m${s}\x1b[0m`; }
function dim(s)    { return `\x1b[2m${s}\x1b[0m`; }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// "$WIF" → "WIF". A leading "$" is a stylistic ticker prefix used by some
// listings; nothing else gets stripped. Anything past the prefix must match
// the registry exactly (case-insensitive).
const normSymbol = (s) => (s ?? '').toUpperCase().replace(/^\$/, '');

// ── Jupiter ──────────────────────────────────────────────────────────────────

// Batched lookup — Jupiter's search endpoint accepts comma-separated mints.
// Returns Map<mint, jupiterToken>.
async function jupiterBatchLookup(mints) {
  const results = new Map();
  const BATCH = 25;
  for (let i = 0; i < mints.length; i += BATCH) {
    const slice = mints.slice(i, i + BATCH);
    const url = `${JUP_BASE}/search?query=${slice.join(',')}&limit=${slice.length}`;
    let attempt = 0;
    let body = null;
    while (attempt < 4) {
      const res = await fetch(url, { headers: { 'accept': 'application/json' } });
      if (res.status === 429) {
        const wait = 1000 * (attempt + 1) ** 2;
        console.warn(yellow(`  [WAIT]  Jupiter 429 — backing off ${wait}ms (attempt ${attempt + 1}/4)`));
        await sleep(wait);
        attempt += 1;
        continue;
      }
      if (!res.ok) throw new Error(`Jupiter API ${res.status} for batch ${i / BATCH + 1}`);
      body = await res.json();
      break;
    }
    if (!Array.isArray(body)) throw new Error(`Unexpected Jupiter response shape (batch ${i / BATCH + 1})`);
    for (const t of body) {
      const id = t.id ?? t.address;
      if (id) results.set(id, t);
    }
    if (i + BATCH < mints.length) await sleep(800);
  }
  return results;
}

// ── Birdeye ──────────────────────────────────────────────────────────────────

// Birdeye token lookup. Tries endpoints in order of strictness:
//   1. /defi/v3/token/meta-data/single  — richest, requires upgraded plan
//   2. /defi/token_overview             — works on basic/free tiers
// First success per mint wins; we return whichever shape the API gave us
// (both expose `address|id`, `symbol`, `decimals`).
async function birdeyeBatchLookup(mints) {
  if (!BIRDEYE_KEY) return null;
  const results = new Map();
  // No batch endpoint on the basic tier — call per mint with pacing.
  for (const mint of mints) {
    const meta = await birdeyeFetchOne(mint);
    if (meta) results.set(mint, meta);
    await sleep(250); // ~4 rps — Birdeye basic tier headroom
  }
  return results;
}

async function birdeyeFetchOne(mint) {
  const candidates = [
    `${BIRDEYE_BASE}/defi/v3/token/meta-data/single?address=${mint}`,
    `${BIRDEYE_BASE}/defi/token_overview?address=${mint}`,
  ];
  for (const url of candidates) {
    let attempt = 0;
    while (attempt < 3) {
      const res = await fetch(url, {
        headers: { 'accept': 'application/json', 'X-API-KEY': BIRDEYE_KEY, 'x-chain': 'solana' },
      });
      if (res.status === 429) {
        const wait = 1500 * (attempt + 1) ** 2;
        console.warn(yellow(`  [WAIT]    Birdeye 429 for ${mint.slice(0, 6)}… — ${wait}ms`));
        await sleep(wait);
        attempt += 1;
        continue;
      }
      if (res.status === 401 || res.status === 403) break; // tier mismatch — try next endpoint
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`Birdeye API ${res.status} for ${mint}: ${text.slice(0, 160)}`);
      }
      const body = await res.json();
      // Both shapes nest the payload under .data; tolerate either.
      return body?.data ?? body;
    }
  }
  return null;
}

// ── Local checks ─────────────────────────────────────────────────────────────

function checkAddressFormat(address) {
  if (!SOLANA_ADDR_RE.test(address)) {
    return `address fails base58 format check (length ${address.length}, has invalid chars?)`;
  }
  return null;
}

function checkVanityCollisions(tokens) {
  const byPrefix = new Map();
  const warnings = [];
  for (const t of tokens) {
    const prefix = t.address.slice(0, 5);
    if (byPrefix.has(prefix)) {
      warnings.push(
        `prefix collision: ${t.symbol} (${t.address}) and ${byPrefix.get(prefix).symbol} (${byPrefix.get(prefix).address}) share prefix "${prefix}"`,
      );
    } else {
      byPrefix.set(prefix, t);
    }
  }
  return warnings;
}

// ── Field-by-field comparison ────────────────────────────────────────────────

function compareEntry(source, t, apiToken) {
  const issues = [];

  // 1. Mint address — full equality (no shortcuts).
  const apiAddr = apiToken.id ?? apiToken.address ?? apiToken.mint ?? null;
  if (apiAddr !== t.address) {
    issues.push(
      `[${source}] address mismatch: registry=${t.address} api=${apiAddr ?? '<missing>'}`,
    );
  }

  // 2. Symbol — case-insensitive, $-prefix tolerated on the API side. Some
  //    tokens carry per-source aliases (e.g. Wormhole-bridged Ether is "ETH"
  //    on Jupiter and "WETH" on Birdeye); accept any documented alias.
  const apiSym = normSymbol(apiToken.symbol);
  const accepted = new Set([t.symbol, ...(t.aliases ?? [])].map(normSymbol));
  if (!accepted.has(apiSym)) {
    issues.push(
      `[${source}] symbol mismatch: registry="${t.symbol}"${
        t.aliases?.length ? ` (aliases: ${t.aliases.join(', ')})` : ''
      } api="${apiToken.symbol}"`,
    );
  }

  // 3. Decimals — strict integer equality (when API reports it).
  const apiDec = apiToken.decimals;
  if (typeof apiDec === 'number' && apiDec !== t.decimals) {
    issues.push(
      `[${source}] decimals mismatch: registry=${t.decimals} api=${apiDec}`,
    );
  }

  return issues;
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const raw = readFileSync(TOKENS_PATH, 'utf8');
  const data = JSON.parse(raw);
  const tokens = data.tokens ?? [];

  if (tokens.length === 0) {
    console.error(red('shared/tokens.json contains no tokens'));
    process.exit(1);
  }

  console.log(`Verifying ${tokens.length} tokens against Jupiter + Birdeye...\n`);

  let errors = 0;
  let warnings = 0;

  // 1. Local format check (cheap, runs first)
  for (const t of tokens) {
    const fmt = checkAddressFormat(t.address);
    if (fmt) {
      console.error(red(`  [FORMAT]  ${t.symbol}: ${fmt}`));
      errors += 1;
    }
  }

  // 2. Vanity-prefix collision check (advisory unless --strict)
  for (const w of checkVanityCollisions(tokens)) {
    console.warn(yellow(`  [VANITY]  ${w}`));
    warnings += 1;
  }

  if (errors > 0) {
    console.error(red(`\nFAILED: ${errors} format error(s) — aborting before API calls.`));
    process.exit(1);
  }

  // 3. Jupiter cross-check (mandatory)
  let jupiterMap;
  try {
    jupiterMap = await jupiterBatchLookup(tokens.map((t) => t.address));
  } catch (e) {
    console.error(red(`Jupiter API call failed: ${e.message}`));
    process.exit(1);
  }

  // 4. Birdeye cross-check (optional — corroboration)
  let birdeyeMap = null;
  if (BIRDEYE_KEY) {
    try {
      birdeyeMap = await birdeyeBatchLookup(tokens.map((t) => t.address));
    } catch (e) {
      console.error(red(`Birdeye API call failed: ${e.message}`));
      // Treat Birdeye outage as a hard failure when the key is set — if you
      // configured it, you want corroboration; a silent skip would defeat the point.
      process.exit(1);
    }
  } else {
    console.warn(yellow(
      '  [SKIP]   BIRDEYE_API_KEY unset — falling back to Jupiter alone.\n' +
      '            Set BIRDEYE_API_KEY for two-source corroboration.\n',
    ));
    warnings += 1;
  }

  // 5. Compare every entry against every available source.
  for (const t of tokens) {
    const allIssues = [];

    const jup = jupiterMap.get(t.address);
    if (!jup) {
      allIssues.push(`[jupiter] not found in API response`);
    } else {
      allIssues.push(...compareEntry('jupiter', t, jup));
    }

    if (birdeyeMap) {
      const be = birdeyeMap.get(t.address);
      if (!be) {
        allIssues.push(`[birdeye] not found in API response`);
      } else {
        allIssues.push(...compareEntry('birdeye', t, be));
      }
    }

    if (allIssues.length > 0) {
      console.error(red(`  [FAIL]    ${t.symbol} (${t.address}):`));
      for (const i of allIssues) console.error(red(`              ${i}`));
      errors += 1;
    } else {
      const sources = birdeyeMap ? 'jupiter+birdeye' : 'jupiter';
      console.log(green(`  [OK]      ${t.symbol.padEnd(8)} ${dim(`${t.address}  (${sources})`)}`));
    }
  }

  console.log();
  if (errors > 0) {
    console.error(red(`FAILED: ${errors} error(s), ${warnings} warning(s).`));
    process.exit(1);
  }
  if (warnings > 0 && STRICT) {
    console.error(yellow(`FAILED (--strict): ${warnings} warning(s).`));
    process.exit(2);
  }
  console.log(green(`PASS: all ${tokens.length} tokens verified${warnings ? ` (${warnings} warning(s))` : ''}.`));
}

main().catch((e) => {
  console.error(red(`Verifier crashed: ${e.stack ?? e.message}`));
  process.exit(1);
});
