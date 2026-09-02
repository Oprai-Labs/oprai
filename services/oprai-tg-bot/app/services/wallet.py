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
    return await pool().fetchrow(
        "SELECT chain, address, enc_key_ref, imported FROM tg_wallets "
        "WHERE telegram_id = $1 AND chain = $2",
        telegram_id,
        CHAIN,
    )


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
        ON CONFLICT (telegram_id, chain) DO NOTHING
        """,
        telegram_id,
        CHAIN,
        created["address"],
        created["enc_key_ref"],
    )
    return await get_wallet(telegram_id)


async def import_wallet(telegram_id: int, secret: str) -> asyncpg.Record:
    """Import an existing key (hex). Overwrites the current Robinhood wallet."""
    imported = await signer.import_wallet(CHAIN, secret)
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
        CHAIN,
        imported["address"],
        imported["enc_key_ref"],
    )
    return await get_wallet(telegram_id)


async def wallet_address(telegram_id: int) -> str:
    """The user's Robinhood address, creating the wallet on first use."""
    return (await get_or_create_wallet(telegram_id))["address"]
