"""Live test for deep-link account linking (0.7a). Needs Postgres + tg_schema."""

from __future__ import annotations

import random
import uuid

import pytest

from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import linking


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_create_consume_binds_and_is_single_use(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    account_id = str(uuid.uuid4())
    await upsert_tg_user(tg_id, "pytest-link")
    try:
        token = await linking.create_link_token(account_id)

        # first consume binds the account
        bound = await linking.consume_link_token(token, tg_id)
        assert bound == account_id
        assert await linking.linked_account(tg_id) == account_id

        # single-use: a second consume of the same token is rejected
        assert await linking.consume_link_token(token, tg_id) is None

        # unknown token is rejected
        assert await linking.consume_link_token("does-not-exist", tg_id) is None
    finally:
        await pool().execute("DELETE FROM tg_link_tokens WHERE account_id = $1::uuid", account_id)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


@pytest.mark.asyncio
async def test_expired_token_rejected(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    account_id = str(uuid.uuid4())
    await upsert_tg_user(tg_id, "pytest-link-exp")
    try:
        token = await linking.create_link_token(account_id, ttl_minutes=15)
        # force-expire it
        await pool().execute(
            "UPDATE tg_link_tokens SET expires_at = now() - interval '1 minute' WHERE token = $1",
            token,
        )
        assert await linking.consume_link_token(token, tg_id) is None
        assert await linking.linked_account(tg_id) is None
    finally:
        await pool().execute("DELETE FROM tg_link_tokens WHERE account_id = $1::uuid", account_id)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
