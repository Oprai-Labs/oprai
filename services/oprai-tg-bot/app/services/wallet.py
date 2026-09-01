"""Custodial wallet service: create/import/lookup, persisted in tg_wallets.

The bot stores only the public address + the signer's opaque enc_key_ref
(a Vault ciphertext). Private keys live nowhere on the bot side.
"""

from __future__ import annotations

import asyncpg

from app.db import pool
from app.signer_client import signer

CHAINS = ("solana", "evm")


async def get_wallet(telegram_id: int, chain: str) -> asyncpg.Record | None:
    return await pool().fetchrow(
        "SELECT chain, address, enc_key_ref, imported FROM tg_wallets "
        "WHERE telegram_id = $1 AND chain = $2",
        telegram_id,
        chain,
    )


async def get_wallets(telegram_id: int) -> list[asyncpg.Record]:
    return await pool().fetch(
        "SELECT chain, address, imported FROM tg_wallets WHERE telegram_id = $1 "
        "ORDER BY chain",
        telegram_id,
    )


async def get_or_create_wallet(telegram_id: int, chain: str) -> asyncpg.Record:
    """Return the user's wallet for `chain`, creating it via the signer if absent.

    Idempotent under a unique(telegram_id, chain) constraint: a concurrent create
    races to ON CONFLICT DO NOTHING and both callers converge on the same row.
    """
    if chain not in CHAINS:
        raise ValueError(f"unsupported chain: {chain}")
    existing = await get_wallet(telegram_id, chain)
    if existing:
        return existing

    created = await signer.create_wallet(chain)  # {address, enc_key_ref}
    await pool().execute(
        """
        INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref, imported)
        VALUES ($1, $2, $3, $4, FALSE)
        ON CONFLICT (telegram_id, chain) DO NOTHING
        """,
        telegram_id,
        chain,
        created["address"],
        created["enc_key_ref"],
    )
    return await get_wallet(telegram_id, chain)


async def import_wallet(telegram_id: int, chain: str, secret: str) -> asyncpg.Record:
    """Import an existing key. Overwrites any current wallet for that chain."""
    if chain not in CHAINS:
        raise ValueError(f"unsupported chain: {chain}")
    imported = await signer.import_wallet(chain, secret)
    await pool().execute(
        """
        INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref, imported)
        VALUES ($1, $2, $3, $4, TRUE)
        ON CONFLICT (telegram_id, chain)
        DO UPDATE SET address = EXCLUDED.address,
                      enc_key_ref = EXCLUDED.enc_key_ref,
                      imported = TRUE
        """,
        telegram_id,
        chain,
        imported["address"],
        imported["enc_key_ref"],
    )
    return await get_wallet(telegram_id, chain)


async def ensure_all_wallets(telegram_id: int) -> dict[str, str]:
    """Ensure the user has a wallet on every chain; return {chain: address}."""
    out: dict[str, str] = {}
    for chain in CHAINS:
        row = await get_or_create_wallet(telegram_id, chain)
        out[chain] = row["address"]
    return out
