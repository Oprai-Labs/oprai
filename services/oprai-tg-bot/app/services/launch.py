"""Launching tokens on Robinhood Chain (Pons bonding curve).

Pons is contract-only: the service ABI-encodes the launch and hands back a
single unsigned transaction carrying the live launch fee as its value. It does
NOT include a gas estimate, so we supply one.

The token image is a plain URL stored on-chain, so it must be somewhere that
stays fetchable — we upload it to our own gateway and pass that URL, rather
than anything ephemeral. (A blob: URL once reached the chain on the Solana side
and could never be fixed, because the metadata authority was burned.)
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.gateway_client import GatewayError, gateway
from app.services import evm

# Transfer(address,uint256) — a mint shows up as a transfer out of the zero
# address, which is how we learn the new token's address from the receipt.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64


class LaunchError(RuntimeError):
    pass


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


async def upload_image(jwt: str, data: bytes, filename: str) -> str:
    """Put the image somewhere permanent and return its public URL."""
    url = f"{settings.GATEWAY_URL.rstrip('/')}/upload/image"
    headers = {"X-Requested-With": "XMLHttpRequest", "Authorization": f"Bearer {jwt}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                url, headers=headers, files={"file": (filename, data)}
            )
    except httpx.HTTPError as e:
        raise LaunchError(f"image upload failed: {e}") from e
    if r.status_code != 200:
        raise LaunchError(f"image upload failed: {_error_text(r)}")
    out = r.json().get("url")
    if not out:
        raise LaunchError("image upload returned no URL")
    return out


def to_square_png_data_uri(data: bytes, size: int = 512) -> str:
    """Center-crop to a square PNG data URI.

    pools.trade takes the image inline and pins it itself — it rejects http and
    ipfs URLs — so a Telegram photo (JPEG) has to be converted here rather than
    hosted. Square because every launchpad renders it as one.
    """
    import base64
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001 — Pillow raises a family of errors
        raise LaunchError("that image couldn't be read") from e

    img = img.convert("RGB")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.LANCZOS
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


async def pools_launch(
    jwt: str,
    *,
    name: str,
    symbol: str,
    wallet: str,
    image_data_uri: str | None = None,
    description: str = "",
    website: str | None = None,
    x_url: str | None = None,
    mode: str = "instant",
) -> dict:
    """Launch on pools.trade — no bonding curve, a Uniswap pool from block one.

    -> {transactions: [...], predictedTokenAddress}. The transaction list can
    have more than one entry, and they must run in order.
    """
    body: dict = {
        "mode": mode,
        "tokenName": name,
        "tokenSymbol": symbol,
        "walletAddress": wallet,
        "description": description,
    }
    if image_data_uri:
        body["imageUrl"] = image_data_uri
    if website:
        body["website"] = website
    if x_url:
        body["xUrl"] = x_url

    try:
        r = await gateway.post("/actions/uniswap/launch/create", body, jwt=jwt)
    except GatewayError as e:
        raise LaunchError(str(e)) from e
    if r.status_code != 200:
        raise LaunchError(_error_text(r))

    res = r.json()
    if not res.get("transactions"):
        raise LaunchError("the launchpad returned no transaction")
    return res


async def pons_launch(
    jwt: str,
    *,
    name: str,
    symbol: str,
    wallet: str,
    logo: str | None = None,
    description: str | None = None,
    website: str | None = None,
    twitter: str | None = None,
    telegram: str | None = None,
) -> dict:
    """-> {transactions:[{to,data,value,chainId}], launchFeeWei, version}."""
    body: dict = {
        "name": name,
        "symbol": symbol,
        # Pons cross-checks this against the JWT wallet, and it is ours.
        "walletAddress": wallet,
    }
    for key, val in (
        ("logo", logo), ("description", description), ("website", website),
        ("twitter", twitter), ("telegram", telegram),
    ):
        if val:
            body[key] = val

    try:
        r = await gateway.post("/actions/pons/launch", body, jwt=jwt)
    except GatewayError as e:
        raise LaunchError(str(e)) from e
    if r.status_code != 200:
        raise LaunchError(_error_text(r))

    res = r.json()
    if not res.get("transactions"):
        raise LaunchError("the launchpad returned no transaction")
    return res


async def execute(enc_key_ref: str, from_addr: str, res: dict, on_step=None) -> list[str]:
    """Sign and confirm each transaction in order."""
    txs = res.get("transactions") or []
    hashes: list[str] = []
    for i, data in enumerate(txs, start=1):
        if on_step:
            await on_step(i, len(txs))
        tx = await evm.build_tx_from_provider(from_addr, data)
        try:
            hashes.append(
                await evm.send_and_confirm(enc_key_ref, tx, f"launch step {i}/{len(txs)}")
            )
        except evm.EvmError as e:
            raise LaunchError(str(e)) from e
    return hashes


async def token_address_from_receipt(tx_hash: str) -> str | None:
    """Find the new token in the launch receipt.

    The launchpad mints the initial supply, and a mint is a Transfer out of the
    zero address — the log's own address is the token.
    """
    receipt = await evm.rpc("eth_getTransactionReceipt", [tx_hash])
    for log in (receipt or {}).get("logs", []):
        topics = log.get("topics") or []
        if len(topics) >= 3 and topics[0] == TRANSFER_TOPIC and topics[1] == ZERO_TOPIC:
            return log.get("address")
    return None


async def token_meta(jwt: str, token_address: str) -> dict | None:
    try:
        r = await gateway.post(
            "/actions/pons/token-meta", {"tokenAddress": token_address}, jwt=jwt
        )
    except GatewayError:
        return None
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("token")
