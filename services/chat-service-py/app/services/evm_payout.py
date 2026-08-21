"""EVM cashback payout — a native-token transfer from a treasury hot wallet.

Mirrors the Solana cashback treasury (services/solana-service-rs cashback.rs): a
claim's NET amount (the claim fee already deducted) is paid in the destination
chain's native token — ETH on Ethereum/Base/Arbitrum/Optimism/Robinhood, BNB on
BNB Chain, POL on Polygon — signed with a key THIS service holds.

Safety, same as the fee model: no treasury key configured means EVM payouts are
simply unavailable, never a crash. The treasury is a hot wallet and must hold the
native token ON EACH CHAIN it pays; OPRAI funds it by claiming its Relay app-fee
balances per chain. `EvmPayoutError` marks a rejection that happened BEFORE any
broadcast, so the caller can safely release the claim reservation; any other
exception is ambiguous (the transfer may have gone out) and leaves the claim
'pending' for review — never a double payout.
"""

import os

import httpx

# chain_key -> (chainId, public RPC, CoinGecko id for the native token's USD price)
EVM_CHAINS: dict[str, tuple[int, str, str]] = {
    "ethereum": (1, "https://eth.llamarpc.com", "ethereum"),
    "base": (8453, "https://mainnet.base.org", "ethereum"),
    "arbitrum": (42161, "https://arb1.arbitrum.io/rpc", "ethereum"),
    "optimism": (10, "https://mainnet.optimism.io", "ethereum"),
    "polygon": (137, "https://polygon-rpc.com", "polygon-ecosystem-token"),
    "bsc": (56, "https://bsc-dataseed.binance.org", "binancecoin"),
    "robinhood": (4663, "https://rpc.mainnet.chain.robinhood.com", "ethereum"),
}


class EvmPayoutError(Exception):
    """Rejected before any transaction was broadcast (safe to release the claim)."""


def treasury_configured() -> bool:
    return bool(os.getenv("EVM_CASHBACK_TREASURY_KEY", "").strip())


def _treasury_account():
    """The treasury eth_account, or None if unset/invalid (payouts then off)."""
    key = os.getenv("EVM_CASHBACK_TREASURY_KEY", "").strip()
    if not key:
        return None
    if not key.startswith("0x"):
        key = "0x" + key
    try:
        from eth_account import Account

        return Account.from_key(key)
    except Exception:  # noqa: BLE001 — a bad key must disable payouts, not crash
        return None


def is_evm_chain(chain: str) -> bool:
    return chain in EVM_CHAINS


async def _rpc(client: httpx.AsyncClient, rpc_url: str, method: str, params: list):
    r = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    d = r.json()
    if d.get("error"):
        raise RuntimeError(f"{method}: {d['error']}")
    return d["result"]


async def _native_price_usd(client: httpx.AsyncClient, coingecko_id: str) -> float:
    r = await client.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coingecko_id, "vs_currencies": "usd"},
    )
    r.raise_for_status()
    return float(r.json()[coingecko_id]["usd"])


async def payout(chain: str, recipient: str, amount_usd: float) -> str:
    """Send `amount_usd` worth of native token to `recipient` on `chain`.

    Returns the transaction hash. Raises `EvmPayoutError` for pre-broadcast
    rejections; other exceptions mean the broadcast state is unknown.
    """
    acct = _treasury_account()
    if acct is None:
        raise EvmPayoutError("EVM cashback payouts are not enabled yet.")
    if chain not in EVM_CHAINS:
        raise EvmPayoutError(f"{chain} payouts are not supported.")
    if not (isinstance(recipient, str) and recipient.startswith("0x") and len(recipient) == 42):
        raise EvmPayoutError("An EVM wallet is required to receive this payout.")

    chain_id, rpc_url, cg_id = EVM_CHAINS[chain]
    from eth_account import Account

    async with httpx.AsyncClient(timeout=20) as client:
        price = await _native_price_usd(client, cg_id)
        if price <= 0:
            raise RuntimeError("could not price the native token")
        value_wei = int(amount_usd / price * 1e18)
        if value_wei <= 0:
            raise EvmPayoutError("Cashback amount is too small to pay out.")

        nonce = int(await _rpc(client, rpc_url, "eth_getTransactionCount", [acct.address, "pending"]), 16)
        gas_price = int(await _rpc(client, rpc_url, "eth_gasPrice", []), 16)
        tx = {
            "to": recipient,
            "value": value_wei,
            "gas": 21000,  # a plain native transfer
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": chain_id,
        }
        signed = Account.sign_transaction(tx, acct.key)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:  # older eth-account
            raw = signed.rawTransaction
        raw_hex = raw.hex()
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        return await _rpc(client, rpc_url, "eth_sendRawTransaction", [raw_hex])
