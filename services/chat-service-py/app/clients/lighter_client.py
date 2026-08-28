"""Lighter perps — write client (Robinhood Chain Domain).

Model B (server-side agent key), fully non-custodial:

  Onboarding is a delegated "agent" key. OPRAI generates a fresh Lighter API
  keypair and asks the user's EVM wallet to authorise it — the wallet only ever
  personal_signs a message the native signer hands us (see the SDK's
  __decode_and_sign_tx_info: L1Sig = personal_sign(messageToSign)). The user's
  L1 private key never leaves their wallet. OPRAI then holds the *agent* key
  (encrypted, per account) and signs orders with it — no wallet popup per trade,
  gas-free. The agent key can trade but not withdraw to an arbitrary address
  (withdrawals are gated to the L1 owner), so funds cannot be stolen with it.

Trading is off-chain/gas-free (create_order → send_tx). Collateral deposit is a
separate on-chain step (USDC via Circle CCTP from Arbitrum/Base/Avalanche, or
native Ethereum/Arbitrum) — handled in the deposit flow, not here.

Same SDK, base-URL only: this points at the Robinhood domain by default; the
zkLighter L2 would just be a different LIGHTER_BASE.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

# The SDK bundles a native signer (linux/amd64 .so) used for order + change-pubkey
# signatures. Imported lazily so the module loads even where the SDK is absent
# (e.g. unit envs); the write paths require it.
try:  # pragma: no cover - import guarded for envs without the native lib
    import lighter  # type: ignore
    from lighter import SignerClient  # type: ignore
    _SDK_ERR: str | None = None
except Exception as e:  # pragma: no cover
    lighter = None  # type: ignore
    SignerClient = None  # type: ignore
    _SDK_ERR = str(e)

# ── Deployment (Robinhood Chain Lighter Domain) ──────────────────────────────
LIGHTER_BASE = "https://api.rh.lighter.xyz"
LIGHTER_CHAIN_ID = 466324            # Robinhood Lighter domain chain id
AGENT_API_KEY_INDEX = 250            # OPRAI's delegated agent slot (2..254 are user-assignable)

# ── SDK constants (mirrored from signer_client.py so callers stay readable) ──
ORDER_TYPE_LIMIT = 0
ORDER_TYPE_MARKET = 1
TIF_IMMEDIATE_OR_CANCEL = 0          # market orders clear now or cancel
TIF_GOOD_TILL_TIME = 1
CROSS_MARGIN_MODE = 0
ISOLATED_MARGIN_MODE = 1
USDC_SCALE = 1_000_000               # collateral is quoted in 1e6 units

_TIMEOUT = 20.0


def sdk_available() -> bool:
    return SignerClient is not None


async def _get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{LIGHTER_BASE}{path}", params=params or {})
        r.raise_for_status()
        return r.json()


async def account_index_for(l1_address: str) -> int | None:
    """Lighter account index for an EVM address, or None if the wallet has no
    Lighter account yet (must be created before trading). The API returns 400/404
    for an address with no account — that is the common not-yet-onboarded case,
    so treat it as None rather than an error."""
    try:
        data = await _get("/api/v1/account", {"by": "l1_address", "value": l1_address})
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code in (400, 404):
            return None
        raise
    accts = data.get("accounts") or []
    if not accts:
        return None
    return accts[0].get("account_index")


async def markets() -> list[dict]:
    """All active Lighter markets with everything the trade card needs: live mark
    price, size decimals, minimum order size, per-market max leverage (derived
    from the initial-margin fraction), maintenance-margin fraction (for liq price)
    and fees. Prices are live — safe to poll for the card's 3s refresh."""
    data = await _get("/api/v1/orderBookDetails")
    out: list[dict] = []
    for o in data.get("order_book_details") or []:
        if o.get("status") != "active":
            continue
        imf = _f(o.get("min_initial_margin_fraction"))   # bps-like int, e.g. 500 = 5%
        mmf = _f(o.get("maintenance_margin_fraction"))
        max_lev = int(round(10000 / imf)) if imf else 50
        out.append({
            "symbol": (o.get("symbol") or "").upper(),
            "market_id": o.get("market_id"),
            "mark_price": _f(o.get("mark_price")) or _f(o.get("last_trade_price")) or _f(o.get("index_price")),
            "last_price": _f(o.get("last_trade_price")),
            "size_decimals": int(o.get("size_decimals", o.get("supported_size_decimals", 0)) or 0),
            "price_decimals": int(o.get("price_decimals", o.get("supported_price_decimals", 2)) or 2),
            "min_base_amount": _f(o.get("min_base_amount")),
            "min_quote_amount": _f(o.get("min_quote_amount")),   # min USD notional (~$10)
            "max_leverage": max_lev,
            "maintenance_margin_fraction": (mmf / 10000) if mmf else None,
            "taker_fee": _f(o.get("taker_fee")),
            "maker_fee": _f(o.get("maker_fee")),
            "daily_change_pct": _f(o.get("daily_price_change")),
        })
    out.sort(key=lambda m: m["symbol"])
    return out


async def account_summary(account_index: int) -> dict:
    """Balance + open positions for a Lighter account in one read — backs the
    card's 'Available to Trade' and the positions list."""
    data = await _get("/api/v1/account", {"by": "index", "value": str(account_index)})
    accts = data.get("accounts") or []
    if not accts:
        return {"available_balance": None, "collateral": None,
                "total_asset_value": None, "positions": []}
    a = accts[0]
    return {
        "available_balance": _f(a.get("available_balance")),
        "collateral": _f(a.get("collateral")),
        "total_asset_value": _f(a.get("total_asset_value")),
        "positions": _norm_positions(a.get("positions") or []),
    }


async def positions(account_index: int) -> list[dict]:
    """Open positions for a Lighter account index, normalised for the UI card."""
    data = await _get("/api/v1/account", {"by": "index", "value": str(account_index)})
    accts = data.get("accounts") or []
    if not accts:
        return []
    return _norm_positions(accts[0].get("positions") or [])


def _norm_positions(raw: list) -> list[dict]:
    out: list[dict] = []
    for p in raw:
        try:
            size = float(p.get("position") or p.get("size") or 0)
        except (TypeError, ValueError):
            size = 0.0
        if size == 0:
            continue
        out.append({
            "symbol": p.get("symbol") or p.get("market") or "",
            "market_id": p.get("market_id"),
            "side": "long" if (p.get("sign", 1) >= 0 and size > 0) else "short",
            "size": abs(size),
            "entry_price": _f(p.get("avg_entry_price") or p.get("entry_price")),
            "mark_price": _f(p.get("mark_price")),
            "liquidation_price": _f(p.get("liquidation_price")),
            "unrealized_pnl": _f(p.get("unrealized_pnl")),
            "leverage": _f(p.get("leverage")),
            "margin": _f(p.get("margin") or p.get("allocated_margin")),
        })
    return out


def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


async def market_index_for(symbol: str) -> tuple[int | None, dict]:
    """Resolve a symbol (NVDA, TSLA, BTC…) to its Lighter market_id + the market
    detail (decimals, mark price) needed to size an order correctly."""
    data = await _get("/api/v1/orderBookDetails")
    s = (symbol or "").upper().strip()
    for o in data.get("order_book_details") or []:
        sym = (o.get("symbol") or "").upper()
        if sym == s or sym.split("/")[0] == s:
            return o.get("market_id"), o
    return None, {}


# ── Onboarding (non-custodial agent-key registration) ────────────────────────
# Two steps so the user's wallet does the signing:
#   1) onboard_build  → server mints an agent keypair + returns the exact text the
#                       wallet must personal_sign, plus the pending tx envelope.
#   2) onboard_submit → server injects the wallet's L1 signature and broadcasts.
# The agent PRIVATE key is returned by onboard_build to the caller to persist
# (encrypted) — it is the credential OPRAI uses to sign this account's orders.

def onboard_build(account_index: int, *, api_key_index: int = AGENT_API_KEY_INDEX) -> dict:
    """Mint an agent keypair and produce the change-pubkey message the user's
    wallet must personal_sign. Returns {agent_private_key, agent_public_key,
    message_to_sign, tx_type, tx_info}. Persist agent_private_key encrypted; hand
    message_to_sign to the frontend for the wallet signature."""
    if SignerClient is None:
        raise RuntimeError(f"Lighter SDK unavailable: {_SDK_ERR}")
    priv, pub, err = lighter.create_api_key()
    if err:
        raise RuntimeError(f"agent keypair generation failed: {err}")
    # A SignerClient bound to the *new* agent key lets us drive the native signer.
    signer = SignerClient(
        url=LIGHTER_BASE,
        account_index=account_index,
        api_private_keys={api_key_index: priv},
        chain_id=LIGHTER_CHAIN_ID,
    )
    res = signer.signer.SignChangePubKey(
        _cstr(pub), 0, signer.DEFAULT_NONCE if hasattr(signer, "DEFAULT_NONCE") else -1,
        api_key_index, account_index,
    )
    from lighter.signer_client import decode_and_free  # type: ignore
    err_s = decode_and_free(res.err)
    if err_s:
        raise RuntimeError(f"SignChangePubKey failed: {err_s}")
    tx_info = decode_and_free(res.txInfo)
    message = decode_and_free(res.messageToSign)
    return {
        "agent_private_key": priv,
        "agent_public_key": pub,
        "api_key_index": api_key_index,
        "account_index": account_index,
        "tx_type": res.txType,
        "tx_info": tx_info,
        "message_to_sign": message,
    }


async def onboard_submit(account_index: int, tx_type: int, tx_info: str,
                         l1_signature: str, *, api_key_index: int = AGENT_API_KEY_INDEX,
                         agent_private_key: str = "") -> dict:
    """Inject the wallet's personal_sign signature and broadcast the change-pubkey
    tx, registering the agent key on the account."""
    if SignerClient is None:
        raise RuntimeError(f"Lighter SDK unavailable: {_SDK_ERR}")
    info = json.loads(tx_info)
    info["L1Sig"] = l1_signature if l1_signature.startswith("0x") else "0x" + l1_signature
    signer = SignerClient(
        url=LIGHTER_BASE, account_index=account_index,
        api_private_keys={api_key_index: agent_private_key}, chain_id=LIGHTER_CHAIN_ID,
    )
    resp = await signer.send_tx(tx_type=tx_type, tx_info=json.dumps(info))
    return {"ok": True, "response": _jsonable(resp)}


# ── Trading (agent key, gas-free) ────────────────────────────────────────────
def _signer(account_index: int, agent_private_key: str,
            api_key_index: int = AGENT_API_KEY_INDEX):
    return SignerClient(
        url=LIGHTER_BASE, account_index=account_index,
        api_private_keys={api_key_index: agent_private_key}, chain_id=LIGHTER_CHAIN_ID,
    )


def _scale(amount: float, decimals: int) -> int:
    return int(round(float(amount) * (10 ** int(decimals))))


def _mark_price(detail: dict) -> float | None:
    for k in ("mark_price", "last_trade_price", "index_price", "oracle_price", "price"):
        v = _f(detail.get(k))
        if v:
            return v
    return None


async def open_position(*, account_index: int, agent_private_key: str, symbol: str,
                        side: str, collateral_usd: float | None = None,
                        base_amount: float | None = None, leverage: int | None = None,
                        order_type: str = "market", limit_price: float | None = None,
                        reduce_only: bool = False,
                        api_key_index: int = AGENT_API_KEY_INDEX) -> dict:
    """Open (or add to) a perp. side: 'long' (buy) | 'short' (sell).

    Sizing: pass EITHER ``collateral_usd`` (+ ``leverage``) — position notional is
    collateral × leverage, converted to base units — OR an explicit ``base_amount``.
    order_type: 'market' (fills now) | 'limit' (rests at ``limit_price``). Limit
    sizing uses limit_price; market uses the mark price.
    """
    market_id, detail = await market_index_for(symbol)
    if market_id is None:
        return {"error": f"unknown Lighter market: {symbol}"}
    is_limit = str(order_type).lower() == "limit"
    if is_limit and (not limit_price or limit_price <= 0):
        return {"error": "limit_price is required for a limit order"}
    lev = int(leverage or 1)
    mark = _mark_price(detail)
    ref_price = float(limit_price) if is_limit else mark
    if not ref_price:
        return {"error": f"could not resolve a price for {symbol}"}
    if base_amount is None:
        if not collateral_usd or collateral_usd <= 0:
            return {"error": "collateral_usd or base_amount is required"}
        base_amount = (float(collateral_usd) * lev) / ref_price
    notional = base_amount * ref_price
    # Enforce the market minimums so a doomed order fails early with a number the
    # user can act on, not an opaque exchange reject.
    min_base = _f(detail.get("min_base_amount"))
    min_quote = _f(detail.get("min_quote_amount"))
    if min_quote and notional < min_quote:
        need_coll = min_quote / max(1, lev)
        return {"error": f"below Lighter's ${min_quote:.0f} minimum order value for "
                         f"{symbol} (this order ≈ ${notional:.2f}). "
                         f"Use ≥ ${need_coll:.2f} collateral at {lev}x, or raise leverage."}
    if min_base and base_amount < min_base:
        return {"error": f"below Lighter's minimum size for {symbol}: min {min_base} "
                         f"{symbol} (~${(min_base * ref_price):.2f}). Increase collateral."}
    signer = _signer(account_index, agent_private_key, api_key_index)
    if lev > 1:
        await _set_leverage_inner(signer, market_id, lev)
    size_dec = int(detail.get("size_decimals", detail.get("supported_size_decimals", 0)))
    price_dec = int(detail.get("price_decimals", detail.get("supported_price_decimals", 2)))
    base = _scale(base_amount, size_dec)
    if base <= 0:
        return {"error": "position size rounds to zero — increase collateral"}
    is_ask = side.lower() in ("short", "sell", "ask")
    if is_limit:
        price_i = _scale(limit_price, price_dec)
        order_ty, tif = ORDER_TYPE_LIMIT, TIF_GOOD_TILL_TIME
        expiry = int(time.time() * 1000) + 30 * 24 * 60 * 60 * 1000  # 30 days
    else:
        price_i, order_ty, tif, expiry = 0, ORDER_TYPE_MARKET, TIF_IMMEDIATE_OR_CANCEL, -1
    _order, resp, err = await signer.create_order(
        market_index=market_id,
        client_order_index=int(time.time() * 1000) % 2_000_000_000,
        base_amount=base, price=price_i, is_ask=is_ask,
        order_type=order_ty, time_in_force=tif,
        reduce_only=reduce_only, order_expiry=expiry, api_key_index=api_key_index,
    )
    if err:
        return {"error": err}
    return {"ok": True, "market": symbol, "side": side, "base_amount": base_amount,
            "leverage": lev, "order_type": "limit" if is_limit else "market",
            "limit_price": limit_price if is_limit else None,
            "notional": notional, "response": _jsonable(resp)}


async def close_position(*, account_index: int, agent_private_key: str, symbol: str,
                         position_side: str, base_amount: float,
                         api_key_index: int = AGENT_API_KEY_INDEX) -> dict:
    """Close (or reduce) a perp — a reduce-only market order on the opposite side."""
    market_id, detail = await market_index_for(symbol)
    if market_id is None:
        return {"error": f"unknown Lighter market: {symbol}"}
    signer = _signer(account_index, agent_private_key, api_key_index)
    size_dec = int(detail.get("size_decimals", detail.get("supported_size_decimals", 0)))
    base = _scale(base_amount, size_dec)
    # close a long by selling (is_ask=True); close a short by buying.
    is_ask = position_side.lower() in ("long", "buy")
    _order, resp, err = await signer.create_order(
        market_index=market_id,
        client_order_index=int(time.time() * 1000) % 2_000_000_000,
        base_amount=base, price=0, is_ask=is_ask,
        order_type=ORDER_TYPE_MARKET, time_in_force=TIF_IMMEDIATE_OR_CANCEL,
        reduce_only=True, api_key_index=api_key_index,
    )
    if err:
        return {"error": err}
    return {"ok": True, "market": symbol, "closed_side": position_side,
            "base_amount": base_amount, "response": _jsonable(resp)}


async def _set_leverage_inner(signer, market_id: int, leverage: int) -> None:
    # fraction is the initial-margin fraction in bps-like units; leverage = 1/fraction.
    fraction = max(1, int(round(10000 / max(1, leverage))))
    tx_type, tx_info, _h, err = signer.sign_update_leverage(
        market_index=market_id, fraction=fraction, margin_mode=CROSS_MARGIN_MODE)
    if err:
        raise RuntimeError(f"update_leverage failed: {err}")
    await signer.send_tx(tx_type=tx_type, tx_info=tx_info)


async def set_leverage(*, account_index: int, agent_private_key: str, symbol: str,
                       leverage: int, api_key_index: int = AGENT_API_KEY_INDEX) -> dict:
    market_id, _ = await market_index_for(symbol)
    if market_id is None:
        return {"error": f"unknown Lighter market: {symbol}"}
    signer = _signer(account_index, agent_private_key, api_key_index)
    await _set_leverage_inner(signer, market_id, leverage)
    return {"ok": True, "market": symbol, "leverage": leverage}


def _cstr(s: str):
    import ctypes
    return ctypes.c_char_p(s.encode("utf-8"))


def _jsonable(x: Any) -> Any:
    try:
        return json.loads(x) if isinstance(x, str) else (x.to_dict() if hasattr(x, "to_dict") else str(x))
    except Exception:
        return str(x)
