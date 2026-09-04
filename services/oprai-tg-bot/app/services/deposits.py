"""Notice money arriving, without the user asking.

We run the chain's node, so a deposit should surface in seconds. Both detectors
cost a constant number of RPC calls per cycle, whatever the number of users:

  • native ETH — one batched `eth_getBalance` over every wallet; an increase is
    a deposit. (Native transfers emit no logs, and a balance diff also catches
    money that arrived while the bot was down.)
  • ERC-20 (tokens and tokenized stocks) — one `eth_getLogs` filtered to
    Transfer events whose recipient is one of our wallets.

Balance diffs are deliberately one-directional: we announce increases and
silently record decreases, because a decrease is the user's own spending.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db import pool
from app.logging_config import log
from app.services import evm, tokens

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# A cold start shouldn't replay history; begin near the tip.
FIRST_RUN_LOOKBACK_BLOCKS = 200
# Bound the catch-up so a long outage can't ask the node for a huge range.
MAX_BLOCKS_PER_CYCLE = 2_000
# And bound the work inside a range: one busy address can emit thousands of
# transfers in a few blocks. The cursor trails behind the cap, so the remainder
# is picked up next cycle instead of being dropped.
MAX_LOGS_PER_CYCLE = 200


@dataclass
class Deposit:
    telegram_id: int
    amount: int
    decimals: int
    symbol: str
    tx_hash: str | None = None

    @property
    def display(self) -> str:
        s = f"{Decimal(self.amount) / (10**self.decimals):f}".rstrip("0").rstrip(".")
        return f"{s or '0'} {self.symbol}"


def _pad_address(addr: str) -> str:
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


async def _wallets() -> list[tuple[int, str]]:
    rows = await pool().fetch(
        "SELECT telegram_id, address FROM tg_wallets WHERE chain = 'evm'"
    )
    return [(r["telegram_id"], r["address"]) for r in rows]


# ── native ETH ──────────────────────────────────────────────────────────────
async def check_native() -> list[Deposit]:
    """Compare every wallet's balance with what we last saw."""
    wallets = await _wallets()
    if not wallets:
        return []

    balances = await evm.rpc_batch(
        [("eth_getBalance", [addr, "latest"]) for _, addr in wallets]
    )
    known = {
        r["telegram_id"]: int(r["wei"])
        for r in await pool().fetch("SELECT telegram_id, wei FROM tg_balance_watch")
    }

    deposits: list[Deposit] = []
    updates: list[tuple[int, str]] = []
    for (telegram_id, _addr), raw in zip(wallets, balances):
        if raw is None:
            continue  # a failed read is not a balance change
        now = evm.to_int(raw)
        before = known.get(telegram_id)
        if before is None:
            # First sighting: record the baseline, never announce it — the money
            # may have been there for weeks.
            updates.append((telegram_id, str(now)))
            continue
        if now > before:
            deposits.append(Deposit(telegram_id, now - before, 18, "ETH"))
        if now != before:
            updates.append((telegram_id, str(now)))

    for telegram_id, wei in updates:
        await pool().execute(
            """
            INSERT INTO tg_balance_watch (telegram_id, wei) VALUES ($1, $2::numeric)
            ON CONFLICT (telegram_id)
            DO UPDATE SET wei = EXCLUDED.wei, updated_at = now()
            """,
            telegram_id,
            wei,
        )
    return deposits


# ── ERC-20 ──────────────────────────────────────────────────────────────────
async def _cursor() -> int:
    row = await pool().fetchrow("SELECT last_block FROM tg_deposit_cursor WHERE id = 1")
    return int(row["last_block"]) if row else 0


async def _set_cursor(block: int) -> None:
    await pool().execute(
        """
        INSERT INTO tg_deposit_cursor (id, last_block) VALUES (1, $1)
        ON CONFLICT (id) DO UPDATE SET last_block = EXCLUDED.last_block, updated_at = now()
        """,
        block,
    )


def _is_too_many_logs(message: str) -> bool:
    """Nodes phrase the log cap differently and we must recognise all of them —
    an unrecognised one freezes the cursor and deposits stop being noticed:

      geth/nitro : "logs matched by query exceeds limit of 10000"
      erigon     : "query returned more than 10000 results"
      providers  : "too many results", "response size exceeded"
    """
    m = message.lower()
    return any(
        marker in m
        for marker in ("exceed", "limit", "too many", "more than", "response size")
    )


async def _get_logs_narrowing(
    from_block: int, to_block: int, topics: list
) -> tuple[list, int]:
    """Fetch Transfer logs, halving the window when the node refuses the query.

    Nodes cap how many logs one query may match. Hitting that cap must not fail
    the cycle: the cursor would never advance and deposits would stop being
    noticed entirely. Narrow the range instead, and report how far we actually
    got so the cursor only claims what was scanned.
    """
    lo, hi = from_block, to_block
    while True:
        try:
            logs = await evm.rpc(
                "eth_getLogs",
                [{"fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}],
            )
            return (logs or []), hi
        except evm.EvmError as e:
            if not _is_too_many_logs(str(e)):
                raise
            if hi <= lo:
                # Even one block is too much for this filter — move past it
                # rather than wedging the watcher forever.
                log.warning("deposit_block_too_dense", block=lo)
                return [], lo
            hi = lo + (hi - lo) // 2


async def check_tokens() -> list[Deposit]:
    """One filtered getLogs for token transfers into any of our wallets."""
    wallets = await _wallets()
    if not wallets:
        return []
    by_address = {addr.lower(): tid for tid, addr in wallets}

    head = evm.to_int(await evm.rpc("eth_blockNumber"))
    last = await _cursor()
    if last == 0:
        await _set_cursor(max(head - FIRST_RUN_LOOKBACK_BLOCKS, 0))
        last = max(head - FIRST_RUN_LOOKBACK_BLOCKS, 0)
    if head <= last:
        return []
    to_block = min(head, last + MAX_BLOCKS_PER_CYCLE)

    topics = [TRANSFER_TOPIC, None, [_pad_address(a) for _, a in wallets]]
    logs, to_block = await _get_logs_narrowing(last + 1, to_block, topics)

    # Work per cycle must stay bounded: a single busy address can produce
    # thousands of transfers in a few blocks, and a per-log round-trip would
    # stall the watcher. Take a capped slice and let the cursor trail behind so
    # the rest is picked up next cycle rather than dropped.
    candidates = []
    for lg in logs:
        log_topics = lg.get("topics") or []   # not `topics` — that is the filter
        if len(log_topics) < 3:
            continue
        telegram_id = by_address.get(("0x" + log_topics[2][-40:]).lower())
        amount = evm.to_int(lg.get("data"))
        if telegram_id is None or amount <= 0:
            continue
        candidates.append((lg, telegram_id, amount))

    truncated = len(candidates) > MAX_LOGS_PER_CYCLE
    if truncated:
        candidates = candidates[:MAX_LOGS_PER_CYCLE]
        # Only claim up to the last block we actually processed.
        to_block = evm.to_int(candidates[-1][0].get("blockNumber"), to_block)

    # Claim them all in one statement, so a rescan can't double-announce.
    claimed: set[tuple[str, int]] = set()
    if candidates:
        rows = await pool().fetch(
            """
            INSERT INTO tg_deposit_seen (tx_hash, log_index, telegram_id)
            SELECT * FROM unnest($1::text[], $2::int[], $3::bigint[])
            ON CONFLICT DO NOTHING
            RETURNING tx_hash, log_index
            """,
            [lg.get("transactionHash") or "" for lg, _, _ in candidates],
            [evm.to_int(lg.get("logIndex")) for lg, _, _ in candidates],
            [tid for _, tid, _ in candidates],
        )
        claimed = {(r["tx_hash"], r["log_index"]) for r in rows}

    fresh = [
        (lg, tid, amt)
        for lg, tid, amt in candidates
        if (lg.get("transactionHash") or "", evm.to_int(lg.get("logIndex"))) in claimed
    ]

    # Remember which tokens each wallet has touched. Every arrival passes
    # through here — a swap's output included, since it lands as a Transfer to
    # the wallet — so this is the cheap, complete answer to "what might they
    # hold", and it saves the portfolio from reading the whole registry.
    if fresh:
        await pool().executemany(
            """
            INSERT INTO tg_wallet_tokens (telegram_id, address)
            VALUES ($1, $2) ON CONFLICT DO NOTHING
            """,
            [(tid, (lg.get("address") or "").lower()) for lg, tid, _ in fresh],
        )

    meta = await _token_meta({(lg.get("address") or "").lower() for lg, _, _ in fresh})

    deposits: list[Deposit] = []
    for lg, telegram_id, amount in fresh:
        m = meta.get((lg.get("address") or "").lower())
        if m is None:
            continue  # decimals unknown — we won't state an amount we can't trust
        symbol, decimals = m
        deposits.append(
            Deposit(telegram_id, amount, decimals, symbol, lg.get("transactionHash") or "")
        )

    await _set_cursor(to_block)
    return deposits


async def _token_meta(addresses: set[str]) -> dict[str, tuple[str, int]]:
    """symbol + decimals for each token, from the registry first and the chain
    second — batched, never one round-trip per log."""
    if not addresses:
        return {}
    out: dict[str, tuple[str, int]] = {}
    rows = await pool().fetch(
        "SELECT lower(address) AS a, symbol, decimals FROM tg_token_registry "
        "WHERE lower(address) = ANY($1::text[])",
        list(addresses),
    )
    for r in rows:
        out[r["a"]] = (r["symbol"], r["decimals"])

    unknown = [a for a in addresses if a not in out]
    if not unknown:
        return out
    decs = await evm.rpc_batch(
        [("eth_call", [{"to": a, "data": tokens.SEL_DECIMALS}, "latest"]) for a in unknown]
    )
    syms = await evm.rpc_batch(
        [("eth_call", [{"to": a, "data": tokens.SEL_SYMBOL}, "latest"]) for a in unknown]
    )
    for addr, d, s in zip(unknown, decs, syms):
        try:
            decimals = int(d, 16) if d and d != "0x" else None
        except (TypeError, ValueError):
            decimals = None
        if decimals is None or decimals > 36:
            continue  # can't read it honestly — skip rather than guess
        out[addr] = (tokens._decode_symbol(s) or "tokens", decimals)
    return out


async def poll() -> list[Deposit]:
    """One cycle: native balance diffs + token transfer logs."""
    found: list[Deposit] = []
    found.extend(await check_native())
    found.extend(await check_tokens())
    return found
