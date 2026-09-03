"""OpenSea — NFTs on Robinhood Chain.

Buying is transactions; listing is a signature. Seaport orders live off-chain,
so putting an NFT up for sale means approving the conduit once (a transaction),
signing the order (EIP-712), and handing the signature to OpenSea — nothing is
mined for the listing itself.

The price is in the collection's own currency, not ether. Robinhood collections
are quoted in USDG as often as in ETH, and the builder's field is called
`priceEth` for historical reasons only — passing 12.5 to a USDG collection means
12.5 USDG. Read the listing's `currency` and say what it actually is, or people
will think they are being asked for thousands of dollars.
"""

from __future__ import annotations

from app.gateway_client import GatewayError, gateway
from app.services import evm
from app.signer_client import SignerError, signer

CHAIN_ID = 4663


class OpenSeaError(RuntimeError):
    pass


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


async def _post(jwt: str, path: str, body: dict) -> dict:
    try:
        r = await gateway.post(path, body, jwt=jwt)
    except GatewayError as e:
        raise OpenSeaError(str(e)) from e
    if r.status_code != 200:
        raise OpenSeaError(_error_text(r))
    return r.json() or {}


async def _read(jwt: str, kind: str, params: dict) -> dict:
    res = await _post(jwt, "/actions/build", {"type": kind, "params": params})
    return (res.get("data") or {})


# ── browse ──────────────────────────────────────────────────────────────────
async def trending(jwt: str, limit: int = 8) -> list[dict]:
    return (await _read(jwt, "opensea_trending", {"limit": limit})).get(
        "collections"
    ) or []


async def search(jwt: str, query: str, limit: int = 8) -> list[dict]:
    return (await _read(jwt, "opensea_collections",
                        {"query": query, "limit": limit})).get("collections") or []


async def collection(jwt: str, slug: str) -> dict | None:
    return (await _read(jwt, "opensea_collection", {"slug": slug})).get("collection")


async def listings(jwt: str, slug: str, limit: int = 8) -> list[dict]:
    """Cheapest first — the builder sorts them, and a floor buyer is the
    common case."""
    return (await _read(jwt, "opensea_listings",
                        {"slug": slug, "limit": limit})).get("listings") or []


async def wallet_nfts(jwt: str, wallet: str, limit: int = 20) -> list[dict]:
    return (await _read(jwt, "opensea_wallet_nfts",
                        {"wallet": wallet, "limit": limit})).get("nfts") or []


async def mint_info(jwt: str, slug: str) -> dict:
    return await _read(jwt, "opensea_mint_info", {"slug": slug})


async def resolve_collection(jwt: str, query: str) -> dict | None:
    """Find a collection by slug, contract or name.

    People type what they see — "RH Machines", not `rh-machines-404`. Trying
    the exact handle first and falling back to a search means both work.
    """
    query = query.strip()
    try:
        found = await collection(jwt, query)
        if found:
            return found
    except OpenSeaError:
        pass
    results = await search(jwt, query, limit=1)
    return results[0] if results else None


# ── buy ─────────────────────────────────────────────────────────────────────
async def build_buy(jwt: str, wallet: str, order_hash: str,
                    protocol_address: str | None = None) -> dict:
    body = {"orderHash": order_hash, "walletAddress": wallet}
    if protocol_address:
        body["protocolAddress"] = protocol_address
    return await _post(jwt, "/actions/opensea/buy", body)


async def build_mint(jwt: str, wallet: str, *, slug: str, quantity: int = 1) -> dict:
    return await _post(jwt, "/actions/opensea/mint", {
        "walletAddress": wallet, "collection": slug, "quantity": quantity,
    })


async def execute(enc_key_ref: str, wallet: str, built: dict, on_step=None) -> list[str]:
    """Approval first, purchase last, each confirmed before the next."""
    txs = built.get("transactions") or []
    if not txs:
        raise OpenSeaError("nothing to sign — try again in a moment")
    hashes: list[str] = []
    for i, data in enumerate(txs, start=1):
        if on_step:
            await on_step(i, len(txs))
        tx = await evm.build_tx_from_provider(wallet, data, chain_id=CHAIN_ID)
        try:
            hashes.append(
                await evm.send_and_confirm(enc_key_ref, tx, f"step {i}/{len(txs)}")
            )
        except evm.EvmError as e:
            raise OpenSeaError(str(e)) from e
    return hashes


# ── sell ────────────────────────────────────────────────────────────────────
async def build_listing(jwt: str, wallet: str, *, token: str, token_id: str,
                        price: float, slug: str | None = None,
                        duration_days: int = 30) -> dict:
    """-> {typedData, parameters, protocolAddress, chainId, nftApprove?}.

    `price` is in the collection's currency, whatever that is.
    """
    body = {
        "token": token,
        "tokenId": str(token_id),
        "priceEth": str(price),
        "walletAddress": wallet,
        "durationDays": duration_days,
    }
    if slug:
        body["slug"] = slug
    return await _post(jwt, "/actions/opensea/list", body)


async def submit_order(jwt: str, built: dict, signature: str,
                       kind: str = "listing") -> dict:
    body = {
        "parameters": built["parameters"],
        "signature": signature,
        "kind": kind,
    }
    if built.get("protocolAddress"):
        body["protocolAddress"] = built["protocolAddress"]
    return await _post(jwt, "/actions/opensea/order/submit", body)


async def place_order(jwt: str, enc_key_ref: str, wallet: str, built: dict,
                      *, kind: str = "listing", on_step=None) -> dict:
    """Approve if needed, sign the order, submit it.

    The approval must be mined before the order is signed: a listing whose
    conduit cannot move the NFT is an order that fails the moment someone
    tries to fill it, and by then the seller believes they have sold it.
    """
    approve = built.get("nftApprove") or built.get("wethApprove")
    if approve:
        if on_step:
            await on_step(1, 2)
        tx = await evm.build_tx_from_provider(wallet, approve, chain_id=CHAIN_ID)
        try:
            await evm.send_and_confirm(enc_key_ref, tx, "approval")
        except evm.EvmError as e:
            raise OpenSeaError(str(e)) from e

    if on_step:
        await on_step(2, 2)
    try:
        signed = await signer.sign_typed_data(enc_key_ref, built["typedData"])
    except SignerError as e:
        raise OpenSeaError(f"couldn't sign the order: {e}") from e

    return await submit_order(jwt, built, signed["signature"], kind)


# ── display ─────────────────────────────────────────────────────────────────
def price_of(row: dict) -> tuple[float, str]:
    """(amount, currency). Never assume ETH — a Robinhood collection is as
    likely to be quoted in USDG, and showing '2.25 ETH' for 2.25 USDG is off
    by four thousand dollars."""
    return float(row.get("price") or 0), str(row.get("currency") or "ETH")
