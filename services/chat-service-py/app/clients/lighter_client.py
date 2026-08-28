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
    Lighter account yet (must be created before trading)."""
    data = await _get("/api/v1/account", {"by": "l1_address", "value": l1_address})
    accts = data.get("accounts") or []
    if not accts:
        return None
    return accts[0].get("account_index")


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


async def open_position(*, account_index: int, agent_private_key: str, symbol: str,
                        side: str, base_amount: float, leverage: int | None = None,
                        api_key_index: int = AGENT_API_KEY_INDEX) -> dict:
    """Open (or add to) a perp — a market order. side: 'long' (buy) | 'short' (sell)."""
    market_id, detail = await market_index_for(symbol)
    if market_id is None:
        return {"error": f"unknown Lighter market: {symbol}"}
    signer = _signer(account_index, agent_private_key, api_key_index)
    if leverage:
        await _set_leverage_inner(signer, market_id, leverage)
    size_dec = int(detail.get("size_decimals", detail.get("supported_size_decimals", 0)))
    base = _scale(base_amount, size_dec)
    is_ask = side.lower() in ("short", "sell", "ask")
    _order, resp, err = await signer.create_order(
        market_index=market_id,
        client_order_index=int(time.time() * 1000) % 2_000_000_000,
        base_amount=base, price=0, is_ask=is_ask,
        order_type=ORDER_TYPE_MARKET, time_in_force=TIF_IMMEDIATE_OR_CANCEL,
        reduce_only=False, api_key_index=api_key_index,
    )
    if err:
        return {"error": err}
    return {"ok": True, "market": symbol, "side": side, "base_amount": base_amount,
            "response": _jsonable(resp)}


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
