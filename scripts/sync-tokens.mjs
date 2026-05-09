#!/usr/bin/env node
// Generate language-specific token registries from shared/tokens.json.
//
// Outputs:
//   - apps/oprai/src/app/core/services/market/tokens.generated.ts
//   - services/chat-service-py/app/services/tokens_generated.py
//
// The Rust service reads shared/tokens.json directly via include_str! at compile
// time, so it does NOT need a generated file.
//
// CI enforcement: run this script, then `git diff --exit-code`. If the diff is
// non-empty, the canonical JSON drifted from a generated file — fail the build.
//
// Usage:
//   node scripts/sync-tokens.mjs
//   node scripts/sync-tokens.mjs --check   (exit 1 if any output would change)

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, '..');
const TOKENS_PATH = resolve(REPO, 'shared', 'tokens.json');
const TS_PATH = resolve(
  REPO,
  'apps',
  'oprai',
  'src',
  'app',
  'core',
  'services',
  'market',
  'tokens.generated.ts',
);
const PY_PATH = resolve(
  REPO,
  'services',
  'chat-service-py',
  'app',
  'services',
  'tokens_generated.py',
);
const CHECK = process.argv.includes('--check');

const HEADER_LINES = [
  'AUTO-GENERATED FROM shared/tokens.json — DO NOT EDIT BY HAND.',
  'Run `node scripts/sync-tokens.mjs` after editing the JSON.',
  'CI enforces that the JSON and these generated files stay in sync.',
];

function tsHeader() {
  return [
    '/**',
    ...HEADER_LINES.map((l) => ` * ${l}`),
    ' */',
    '',
  ].join('\n');
}

function pyHeader() {
  return [
    '"""', ...HEADER_LINES, '"""', '',
  ].join('\n');
}

function buildTs(tokens) {
  const entries = tokens.map((t) => {
    const logo = t.logoURI ? JSON.stringify(t.logoURI) : 'null';
    const tags = t.tags ? `, tags: ${JSON.stringify(t.tags)}` : '';
    const aliases = t.aliases ? `, aliases: ${JSON.stringify(t.aliases)}` : '';
    return `  { address: ${JSON.stringify(t.address)}, symbol: ${JSON.stringify(
      t.symbol,
    )}, name: ${JSON.stringify(t.name)}, decimals: ${t.decimals}, logoURI: ${logo}${aliases}${tags} },`;
  });
  return [
    tsHeader(),
    'export interface VerifiedToken {',
    '  readonly address: string;',
    '  readonly symbol: string;',
    '  readonly name: string;',
    '  readonly decimals: number;',
    '  readonly logoURI: string | null;',
    '  readonly aliases?: readonly string[];',
    '  readonly tags?: readonly string[];',
    '}',
    '',
    'export const VERIFIED_TOKENS: readonly VerifiedToken[] = [',
    ...entries,
    '] as const;',
    '',
    'const _byAddress = new Map<string, VerifiedToken>(VERIFIED_TOKENS.map(t => [t.address, t]));',
    'const _bySymbol = new Map<string, VerifiedToken>(VERIFIED_TOKENS.map(t => [t.symbol.toUpperCase(), t]));',
    '',
    '/** Look up a verified token by mint address (returns null if not in the registry). */',
    'export function getVerifiedTokenByAddress(addr: string): VerifiedToken | null {',
    '  return _byAddress.get(addr) ?? null;',
    '}',
    '',
    '/** Look up a verified token by symbol (case-insensitive). */',
    'export function getVerifiedTokenBySymbol(sym: string): VerifiedToken | null {',
    '  return _bySymbol.get(sym?.toUpperCase()) ?? null;',
    '}',
    '',
  ].join('\n');
}

function buildPy(tokens) {
  const entries = tokens.map((t) => {
    const logo = t.logoURI ? JSON.stringify(t.logoURI) : 'None';
    return [
      '    {',
      `        "address": ${JSON.stringify(t.address)},`,
      `        "symbol": ${JSON.stringify(t.symbol)},`,
      `        "name": ${JSON.stringify(t.name)},`,
      `        "decimals": ${t.decimals},`,
      `        "logoURI": ${logo},`,
      '    },',
    ].join('\n');
  });
  return [
    pyHeader(),
    'from typing import Optional, TypedDict',
    '',
    '',
    'class VerifiedToken(TypedDict):',
    '    address: str',
    '    symbol: str',
    '    name: str',
    '    decimals: int',
    '    logoURI: Optional[str]',
    '',
    '',
    'VERIFIED_TOKENS: list[VerifiedToken] = [',
    ...entries,
    ']',
    '',
    '_BY_ADDRESS = {t["address"]: t for t in VERIFIED_TOKENS}',
    '_BY_SYMBOL = {t["symbol"].upper(): t for t in VERIFIED_TOKENS}',
    '',
    '',
    'def get_verified_token_by_address(addr: str) -> Optional[VerifiedToken]:',
    '    """Look up a verified token by mint address."""',
    '    return _BY_ADDRESS.get(addr)',
    '',
    '',
    'def get_verified_token_by_symbol(sym: str) -> Optional[VerifiedToken]:',
    '    """Look up a verified token by symbol (case-insensitive)."""',
    '    if not sym:',
    '        return None',
    '    return _BY_SYMBOL.get(sym.upper())',
    '',
  ].join('\n');
}

function main() {
  const data = JSON.parse(readFileSync(TOKENS_PATH, 'utf8'));
  const tokens = data.tokens ?? [];
  const ts = buildTs(tokens);
  const py = buildPy(tokens);

  const targets = [
    [TS_PATH, ts],
    [PY_PATH, py],
  ];

  let drift = 0;
  for (const [path, expected] of targets) {
    let actual = '';
    try { actual = readFileSync(path, 'utf8'); } catch { /* not yet generated */ }
    if (actual !== expected) {
      drift += 1;
      if (CHECK) {
        console.error(`DRIFT: ${path} is out of sync with shared/tokens.json`);
      } else {
        writeFileSync(path, expected);
        console.log(`wrote ${path}`);
      }
    }
  }

  if (CHECK && drift > 0) {
    console.error(`\nFAIL: ${drift} generated file(s) drifted. Run 'node scripts/sync-tokens.mjs' and commit.`);
    process.exit(1);
  }
  if (drift === 0) console.log('All generated token files are up to date.');
}

main();
