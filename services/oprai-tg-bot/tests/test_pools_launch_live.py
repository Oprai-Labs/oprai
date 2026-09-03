"""Launching on pools.trade (instant Uniswap pool, no bonding curve).

pools.trade takes the image inline as a PNG/WebP data URI and rejects URLs, so
a Telegram JPEG has to be converted here. It also requires a non-empty
description — which is why the quoted message's text becomes one.
"""

from __future__ import annotations

import base64
import io
import random
import types
import urllib.error
import urllib.request

import pytest
from PIL import Image

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers.launch import _description_from
from app.services import auth as auth_svc
from app.services import launch
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)


def _jpeg(w: int = 900, h: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 60, 120)).save(buf, format="JPEG")
    return buf.getvalue()


# ── image conversion ────────────────────────────────────────────────────────
def test_telegram_jpeg_becomes_a_square_png_data_uri():
    """pools.trade rejects http/ipfs URLs, so the bytes must go inline."""
    uri = launch.to_square_png_data_uri(_jpeg())
    assert uri.startswith("data:image/png;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert img.format == "PNG"
    assert img.size == (512, 512), "a non-square photo must be cropped square"


def test_unreadable_image_is_rejected_clearly():
    with pytest.raises(launch.LaunchError):
        launch.to_square_png_data_uri(b"not an image at all")


# ── description ─────────────────────────────────────────────────────────────
def _msg(reply_text: str | None):
    reply = types.SimpleNamespace(text=reply_text, caption=None) if reply_text is not None else None
    return types.SimpleNamespace(reply_to_message=reply)


def test_quoted_message_becomes_the_description():
    assert _description_from(_msg("the dog that runs the town"), "Doge") == (
        "the dog that runs the town"
    )


def test_description_falls_back_to_the_name():
    """pools.trade rejects an empty description, so a launch must never send one."""
    assert _description_from(_msg(None), "Solar Token") == "Solar Token"
    assert _description_from(_msg("   "), "Solar Token") == "Solar Token"


# ── live ────────────────────────────────────────────────────────────────────
@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_live_pools_launch_builds(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-pools")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        jwt = await auth_svc.get_jwt(tg_id)
        res = await launch.pools_launch(
            jwt, name="OPRAI Test Token", symbol="OPRTEST", wallet=addr,
            image_data_uri=launch.to_square_png_data_uri(_jpeg()),
            description="a token launched from a quoted message",
        )
        assert res["transactions"], "a launch must return at least one transaction"
        assert res.get("predictedTokenAddress", "").startswith("0x")
        tx = res["transactions"][0]
        assert tx.get("to", "").startswith("0x")
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
