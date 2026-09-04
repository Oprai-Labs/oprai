"""Custodial wallet service — Robinhood Chain only.

OPRAI's Telegram bot is Robinhood-Chain-native: one custodial wallet per user,
an EVM (secp256k1) key used on Robinhood Chain (4663). The bot stores only the
public address + the signer's opaque enc_key_ref (a Vault ciphertext); private
keys live nowhere on the bot side.

The DB `chain` column stays 'evm' (the signer's secp256k1 scheme); everything
user-facing is framed as "Robinhood".
"""

from __future__ import annotations

import asyncpg

from app.db import pool
from app.signer_client import signer

CHAIN = "evm"  # secp256k1 key, used on Robinhood Chain (4663)


async def get_wallet(telegram_id: int) -> asyncpg.Record | None:
    """The wallet in use. Archived ones are still here but no longer active."""
    return await pool().fetchrow(
        "SELECT chain, address, enc_key_ref, imported FROM tg_wallets "
        "WHERE telegram_id = $1 AND chain = $2 AND archived_at IS NULL",
        telegram_id,
        CHAIN,
    )


async def archive_active(telegram_id: int) -> asyncpg.Record | None:
    """Step the current wallet aside without destroying it.

    Replacing a wallet row outright loses the only copy of its key, and with it
    anything the old address still holds. Archived rows keep their key, stay
    exportable, and simply stop being the one we sign with.
    """
    return await pool().fetchrow(
        "UPDATE tg_wallets SET archived_at = now() "
        "WHERE telegram_id = $1 AND chain = $2 AND archived_at IS NULL "
        "RETURNING chain, address, enc_key_ref, imported",
        telegram_id,
        CHAIN,
    )


async def list_wallets(telegram_id: int) -> list[asyncpg.Record]:
    """In the order they were created, oldest first — always.

    Sorting by which one is active renumbered them on every switch: W1 became
    W2 and back again. The number is how someone tells two addresses apart, so
    it has to belong to the wallet, not to its current state.
    """
    return await pool().fetch(
        "SELECT address, imported, archived_at, created_at, label FROM tg_wallets "
        "WHERE telegram_id = $1 AND chain = $2 "
        "ORDER BY created_at, id",
        telegram_id,
        CHAIN,
    )


async def rename(telegram_id: int, address: str, label: str | None) -> bool:
    """Give a wallet a name of the user's choosing. An empty name clears it,
    falling back to its number."""
    result = await pool().execute(
        "UPDATE tg_wallets SET label = $3 WHERE telegram_id = $1 AND chain = $2 "
        "AND lower(address) = lower($4)",
        telegram_id, CHAIN, (label or None), address,
    )
    return result.endswith("1")


async def get_or_create_wallet(telegram_id: int) -> asyncpg.Record:
    """Return the user's Robinhood wallet, creating it via the signer if absent.

    Idempotent under unique(telegram_id, chain): a concurrent create races to
    ON CONFLICT DO NOTHING and both callers converge on the same row.
    """
    existing = await get_wallet(telegram_id)
    if existing:
        return existing

    created = await signer.create_wallet(CHAIN)  # {address, enc_key_ref}
    await pool().execute(
        """
        INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref, imported)
        VALUES ($1, $2, $3, $4, FALSE)
        ON CONFLICT (telegram_id, chain) WHERE archived_at IS NULL DO NOTHING
        """,
        telegram_id,
        CHAIN,
        created["address"],
        created["enc_key_ref"],
    )
    return await get_wallet(telegram_id)


async def import_wallet(telegram_id: int, secret: str) -> asyncpg.Record:
    """Import an existing key and make it the wallet in use.

    The previous wallet is archived, not overwritten. Overwriting discarded the
    only copy of its key — so anything still sitting at the old address became
    unreachable the moment someone imported.
    """
    imported = await signer.import_wallet(CHAIN, secret)
    await archive_active(telegram_id)
    await pool().execute(
        """
        INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref, imported)
        VALUES ($1, $2, $3, $4, TRUE)
        """,
        telegram_id,
        CHAIN,
        imported["address"],
        imported["enc_key_ref"],
    )
    return await get_wallet(telegram_id)


async def new_wallet(telegram_id: int) -> asyncpg.Record:
    """Generate a fresh wallet, archiving whatever was in use."""
    created = await signer.create_wallet(CHAIN)
    await archive_active(telegram_id)
    await pool().execute(
        """
        INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref, imported)
        VALUES ($1, $2, $3, $4, FALSE)
        """,
        telegram_id,
        CHAIN,
        created["address"],
        created["enc_key_ref"],
    )
    return await get_wallet(telegram_id)


async def activate(telegram_id: int, address: str) -> asyncpg.Record | None:
    """Make one of their wallets the one we sign with.

    Archiving is reversible: a wallet stepped aside can be brought back, which
    is what makes several wallets usable rather than a one-way door. The swap
    is two statements, so the partial unique index over active rows is what
    keeps two from ever being active at once — the archive runs first.
    """
    target = await pool().fetchrow(
        "SELECT id FROM tg_wallets WHERE telegram_id = $1 AND chain = $2 "
        "AND lower(address) = lower($3)",
        telegram_id, CHAIN, address,
    )
    if target is None:
        return None
    await archive_active(telegram_id)
    await pool().execute(
        "UPDATE tg_wallets SET archived_at = NULL WHERE id = $1", target["id"]
    )
    return await get_wallet(telegram_id)


async def export_secret(telegram_id: int, address: str | None = None) -> dict:
    """The private key for a wallet of theirs — active by default.

    Archived wallets are exportable too: that is the whole point of keeping
    them, so funds left at an old address can still be recovered.
    """
    if address:
        row = await pool().fetchrow(
            "SELECT chain, address, enc_key_ref FROM tg_wallets "
            "WHERE telegram_id = $1 AND chain = $2 AND lower(address) = lower($3)",
            telegram_id, CHAIN, address,
        )
    else:
        row = await get_wallet(telegram_id)
    if row is None:
        raise ValueError("no such wallet")
    return await signer.export_wallet(CHAIN, row["enc_key_ref"])


async def wallet_address(telegram_id: int) -> str:
    """The user's Robinhood address, creating the wallet on first use."""
    return (await get_or_create_wallet(telegram_id))["address"]
