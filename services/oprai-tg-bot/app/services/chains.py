"""EVM chains the bot can reach.

Robinhood Chain is home; the others exist only so funds sitting elsewhere can
be bridged in. The custodial wallet is a single secp256k1 key, so it is the
SAME address on every one of these chains — a user can send to their OPRAI
address on Base and bridge it over without a second wallet.

RPC URLs match the set already used elsewhere in the monorepo
(chat-service evm_payout), and each is overridable by env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ROBINHOOD = 4663


@dataclass(frozen=True)
class Chain:
    id: int
    key: str
    name: str
    native: str
    default_rpc: str
    aliases: tuple[str, ...] = ()

    @property
    def rpc(self) -> str:
        """Env override wins — in prod Robinhood points at our own node."""
        return os.getenv(f"{self.key.upper()}_RPC") or self.default_rpc


CHAINS: tuple[Chain, ...] = (
    Chain(ROBINHOOD, "robinhood", "Robinhood Chain", "ETH",
          "https://rpc.mainnet.chain.robinhood.com", ("rh", "robinhood-chain")),
    # Public endpoints churn: llamarpc 403s non-browser clients, Cloudflare's
    # returns an internal error, Ankr now demands a key. drpc answers plainly.
    Chain(1, "ethereum", "Ethereum", "ETH", "https://eth.drpc.org", ("eth", "mainnet")),
    Chain(8453, "base", "Base", "ETH", "https://mainnet.base.org"),
    Chain(42161, "arbitrum", "Arbitrum", "ETH", "https://arb1.arbitrum.io/rpc", ("arb",)),
    Chain(10, "optimism", "Optimism", "ETH", "https://mainnet.optimism.io", ("op",)),
    # polygon-rpc.com now 403s ("API key disabled").
    Chain(137, "polygon", "Polygon", "POL", "https://polygon.drpc.org", ("matic",)),
    Chain(56, "bsc", "BNB Chain", "BNB", "https://bsc-dataseed.binance.org", ("bnb", "binance")),
)

_BY_ID = {c.id: c for c in CHAINS}


def by_id(chain_id: int) -> Chain | None:
    return _BY_ID.get(int(chain_id))


def resolve(ref: str) -> Chain | None:
    """Accept a name, an alias or a numeric id ("base", "arb", "8453")."""
    r = ref.strip().lower()
    if r.isdigit():
        return by_id(int(r))
    for c in CHAINS:
        if r == c.key or r in c.aliases or r == c.name.lower():
            return c
    return None


def rpc_for(chain_id: int) -> str:
    c = by_id(chain_id)
    if c is None:
        raise KeyError(f"no RPC configured for chain {chain_id}")
    return c.rpc


def name_for(chain_id: int) -> str:
    c = by_id(chain_id)
    return c.name if c else f"chain {chain_id}"


def source_chains() -> tuple[Chain, ...]:
    """Chains you can bridge FROM (everything except home)."""
    return tuple(c for c in CHAINS if c.id != ROBINHOOD)
