"""Magic Eden API v2 client."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

_BASE = "https://api-mainnet.magiceden.dev/v2"
_log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    h = {"accept": "application/json"}
    if settings.MAGIC_EDEN_API_KEY:
        h["Authorization"] = f"Bearer {settings.MAGIC_EDEN_API_KEY}"
    return h


async def get_magic_ticket_burn_instructions(
    wallet_address: str,
    mint_addresses: list[str],
) -> dict[str, Any]:
    """GET /v2/instructions/magic_ticket/burns

    Constructs and returns burn transactions for given Magic Ticket mint addresses.

    Args:
        wallet_address: Wallet address that owns the tickets.
        mint_addresses: List of mint addresses to burn (sent as comma-separated string).
    """
    params: dict[str, Any] = {
        "walletAddress": wallet_address,
        "mintAddresses": ",".join(mint_addresses),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{_BASE}/instructions/magic_ticket/burns",
            params=params,
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_withdraw_instruction(
    buyer: str,
    amount: float,
    auction_house_address: str | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/withdraw

    Returns an unsigned transaction to withdraw SOL from the Magic Eden escrow.

    Args:
        buyer: Buyer wallet address.
        amount: Amount in SOL to withdraw.
        auction_house_address: Auction house address (defaults to ME default).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "amount": amount,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/withdraw",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_deposit_instruction(
    buyer: str,
    amount: float,
    auction_house_address: str | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/deposit

    Returns an unsigned transaction to deposit SOL into the Magic Eden escrow.

    Args:
        buyer: Buyer wallet address.
        amount: Amount in SOL to deposit.
        auction_house_address: Auction house address (defaults to ME default).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "amount": amount,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/deposit",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_sell_cancel_instruction(
    seller: str,
    token_mint: str,
    token_account: str,
    price: float,
    auction_house_address: str | None = None,
    seller_referral: str | None = None,
    expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/sell_cancel

    Returns an unsigned transaction to cancel an NFT listing on Magic Eden.

    Args:
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_account: Token account holding the NFT.
        price: Listed price in SOL (must match original listing).
        auction_house_address: Auction house address (defaults to ME default).
        seller_referral: Seller referral wallet.
        expiry: Expiry timestamp in seconds (0 = no expiry).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "seller": seller,
        "tokenMint": token_mint,
        "tokenAccount": token_account,
        "price": price,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if expiry is not None:
        params["expiry"] = expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/sell_cancel",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_sell_now_instruction(
    buyer: str,
    seller: str,
    token_mint: str,
    token_ata: str,
    price: float,
    new_price: float,
    seller_expiry: int,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    seller_referral: str | None = None,
    buyer_expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/sell_now

    Returns an unsigned transaction to accept an existing buyer offer (sell now).

    Args:
        buyer: Buyer wallet address.
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_ata: Associate token account.
        price: Current price in SOL.
        new_price: New price in SOL.
        seller_expiry: Seller expiry timestamp in seconds (0 = no expiry).
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        seller_referral: Seller referral wallet.
        buyer_expiry: Buyer expiry timestamp (0 = no expiry).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "seller": seller,
        "tokenMint": token_mint,
        "tokenATA": token_ata,
        "price": price,
        "newPrice": new_price,
        "sellerExpiry": seller_expiry,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if buyer_expiry is not None:
        params["buyerExpiry"] = buyer_expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/sell_now",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_sell_change_price_instruction(
    seller: str,
    token_mint: str,
    token_account: str,
    price: float,
    new_price: float,
    expiry: int,
    auction_house_address: str | None = None,
    seller_referral: str | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/sell_change_price

    Returns an unsigned transaction to change the listing price of an NFT on Magic Eden.

    Args:
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_account: Token account holding the NFT.
        price: Current (old) listing price in SOL.
        new_price: New listing price in SOL.
        expiry: Expiry timestamp in seconds (0 = no expiry).
        auction_house_address: Auction house address (defaults to ME default).
        seller_referral: Seller referral wallet.
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "seller": seller,
        "tokenMint": token_mint,
        "tokenAccount": token_account,
        "price": price,
        "newPrice": new_price,
        "expiry": expiry,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/sell_change_price",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_sell_instruction(
    seller: str,
    token_mint: str,
    token_account: str,
    price: float,
    auction_house_address: str | None = None,
    seller_referral: str | None = None,
    expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/sell

    Returns an unsigned transaction to list an NFT for sale on Magic Eden.

    Args:
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_account: Token account holding the NFT.
        price: Listing price in SOL.
        auction_house_address: Auction house address (defaults to ME default).
        seller_referral: Seller referral wallet.
        expiry: Expiry timestamp in seconds (0 = no expiry).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "seller": seller,
        "tokenMint": token_mint,
        "tokenAccount": token_account,
        "price": price,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if expiry is not None:
        params["expiry"] = expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/sell",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_buy_change_price_instruction(
    buyer: str,
    token_mint: str,
    price: float,
    new_price: float,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/buy_change_price

    Returns an unsigned transaction to change the price of an existing buy offer.

    Args:
        buyer: Buyer wallet address.
        token_mint: Token mint address.
        price: Current (old) offer price in SOL.
        new_price: New offer price in SOL.
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        expiry: Expiry timestamp in seconds (0 = no expiry).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "tokenMint": token_mint,
        "price": price,
        "newPrice": new_price,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if expiry is not None:
        params["expiry"] = expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/buy_change_price",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_buy_cancel_instruction(
    buyer: str,
    token_mint: str,
    price: float,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/buy_cancel

    Returns an unsigned transaction to cancel a buy offer on Magic Eden.

    Args:
        buyer: Buyer wallet address.
        token_mint: Token mint address.
        price: Price in SOL (must match the original offer price).
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        expiry: Expiry timestamp in seconds (0 = no expiry).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "tokenMint": token_mint,
        "price": price,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if expiry is not None:
        params["expiry"] = expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/buy_cancel",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_buy_now_instruction(
    buyer: str,
    seller: str,
    token_mint: str,
    token_ata: str,
    price: float,
    seller_expiry: int,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    seller_referral: str | None = None,
    buyer_expiry: int | None = None,
    buyer_creator_royalty_percent: int | None = None,
    priority_fee: int | None = None,
    spl_price: str | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/buy_now

    Returns an unsigned buy-now transaction for an NFT on Magic Eden.

    Args:
        buyer: Buyer wallet address.
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_ata: Seller's Associated Token Account.
        price: Price in SOL.
        seller_expiry: Seller expiry timestamp in seconds (0 = no expiry).
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        seller_referral: Seller referral wallet.
        buyer_expiry: Buyer expiry timestamp (0 = no expiry).
        buyer_creator_royalty_percent: Royalty percent (0–100).
        priority_fee: Priority fee in microlamports.
        spl_price: SPL token price information (JSON string).
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "seller": seller,
        "tokenMint": token_mint,
        "tokenATA": token_ata,
        "price": price,
        "sellerExpiry": seller_expiry,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if buyer_expiry is not None:
        params["buyerExpiry"] = buyer_expiry
    if buyer_creator_royalty_percent is not None:
        params["buyerCreatorRoyaltyPercent"] = buyer_creator_royalty_percent
    if priority_fee is not None:
        params["priorityFee"] = priority_fee
    if spl_price is not None:
        params["splPrice"] = spl_price

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/buy_now",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_buy_now_transfer_nft_instruction(
    buyer: str,
    seller: str,
    token_mint: str,
    token_ata: str,
    price: float,
    destination_ata: str,
    destination_owner: str,
    create_ata: bool,
    seller_expiry: int,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    seller_referral: str | None = None,
    buyer_expiry: int | None = None,
    buyer_creator_royalty_percent: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/buy_now_transfer_nft

    Returns an unsigned buy-and-transfer transaction for an NFT on Magic Eden.

    Args:
        buyer: Buyer wallet address.
        seller: Seller wallet address.
        token_mint: Token mint address.
        token_ata: Associated Token Account of the token.
        price: Price in SOL.
        destination_ata: Associated token account to send the bought NFT to.
        destination_owner: Owner of the destination token account.
        create_ata: Whether to include create ATA instructions.
        seller_expiry: Seller expiry timestamp in seconds (0 = no expiry).
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        seller_referral: Seller referral wallet.
        buyer_expiry: Buyer expiry timestamp (0 = no expiry).
        buyer_creator_royalty_percent: Royalty percent (0–100).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "seller": seller,
        "tokenMint": token_mint,
        "tokenATA": token_ata,
        "price": price,
        "destinationATA": destination_ata,
        "destinationOwner": destination_owner,
        "createATA": str(create_ata).lower(),
        "sellerExpiry": seller_expiry,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if seller_referral is not None:
        params["sellerReferral"] = seller_referral
    if buyer_expiry is not None:
        params["buyerExpiry"] = buyer_expiry
    if buyer_creator_royalty_percent is not None:
        params["buyerCreatorRoyaltyPercent"] = buyer_creator_royalty_percent
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/buy_now_transfer_nft",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_buy_instruction(
    buyer: str,
    token_mint: str,
    price: float,
    auction_house_address: str | None = None,
    buyer_referral: str | None = None,
    buyer_creator_royalty_percent: int | None = None,
    expiry: int | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/buy

    Returns an unsigned buy transaction for an NFT on Magic Eden.

    Args:
        buyer: Buyer wallet address.
        token_mint: Token mint address.
        price: Price in SOL.
        auction_house_address: Auction house address (defaults to ME default).
        buyer_referral: Buyer referral wallet.
        buyer_creator_royalty_percent: Royalty percent (0–100).
        expiry: Timestamp in seconds (0 = 7 days).
        priority_fee: Priority fee in microlamports.
    """
    params: dict[str, Any] = {
        "buyer": buyer,
        "tokenMint": token_mint,
        "price": price,
    }
    if auction_house_address is not None:
        params["auctionHouseAddress"] = auction_house_address
    if buyer_referral is not None:
        params["buyerReferral"] = buyer_referral
    if buyer_creator_royalty_percent is not None:
        params["buyerCreatorRoyaltyPercent"] = buyer_creator_royalty_percent
    if expiry is not None:
        params["expiry"] = expiry
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/buy",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_collection_leaderboard(symbol: str, limit: int = 100) -> dict[str, Any]:
    """GET /v2/collections/{symbol}/leaderboard

    Returns holder leaderboard for a collection (top holders with token counts).

    Args:
        symbol: Collection symbol.
        limit: Number of top holders to return (default 100, max 100).
    """
    params: dict[str, Any] = {"limit": min(limit, 100)}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{symbol}/leaderboard",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_collection_holder_stats(symbol: str) -> dict[str, Any]:
    """GET /v2/collections/{symbol}/holder_stats

    Returns holder statistics for a collection (supply, unique holders, top holders).

    Args:
        symbol: Collection symbol.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{symbol}/holder_stats",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_collections_batch_listings(
    symbols: list[str],
    offset: int = 0,
    limit: int = 20,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str = "listPrice",
    sort_direction: str = "asc",
    listing_agg_mode: bool = False,
) -> list[dict[str, Any]]:
    """POST /v2/collections/batch/listings

    Returns active listings for multiple collections in a single request.

    Args:
        symbols: List of collection symbols to query.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 20, max 100).
        min_price: Filter listings below this price (SOL).
        max_price: Filter listings above this price (SOL).
        sort: Sort field — 'listPrice' (default) or 'updatedAt'.
        sort_direction: 'asc' (default) or 'desc'.
        listing_agg_mode: True to aggregate listings from all marketplaces.
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 100),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if listing_agg_mode:
        params["listingAggMode"] = "true"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/collections/batch/listings",
            headers={**_headers(), "content-type": "application/json"},
            params=params,
            json={"symbols": symbols},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_collection_listings(
    symbol: str,
    offset: int = 0,
    limit: int = 20,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str = "listPrice",
    sort_direction: str = "asc",
    listing_agg_mode: bool = False,
) -> list[dict[str, Any]]:
    """GET /v2/collections/{symbol}/listings

    Returns active listings for a collection.

    Args:
        symbol: Collection symbol.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 20, max 100).
        min_price: Filter listings below this price (SOL).
        max_price: Filter listings above this price (SOL).
        sort: Sort field — 'listPrice' (default) or 'updatedAt'.
        sort_direction: 'asc' (default) or 'desc'.
        listing_agg_mode: True to aggregate listings from all marketplaces.
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 100),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if listing_agg_mode:
        params["listingAggMode"] = "true"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{symbol}/listings",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_collections(
    offset: int = 0,
    limit: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """GET /v2/collections

    Returns a paginated list of NFT collections on Magic Eden.

    Args:
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 200, max 500).

    Returns:
        Tuple of (collections list, paging metadata from ME-Pub-API-Metadata header).
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        paging = {}
        raw_meta = resp.headers.get("ME-Pub-API-Metadata")
        if raw_meta:
            import json as _json
            try:
                paging = _json.loads(raw_meta)
            except Exception:
                pass
        return (data if isinstance(data, list) else []), paging


async def get_collection_attributes(collection_symbol: str) -> dict[str, Any]:
    """GET /v2/collections/{collectionSymbol}/attributes

    Returns available trait attributes and their floor prices for a collection.

    Args:
        collection_symbol: Collection symbol.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{collection_symbol}/attributes",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_collection_stats(
    symbol: str,
    time_window: str | None = None,
    listing_agg_mode: bool = False,
) -> dict[str, Any]:
    """GET /v2/collections/{symbol}/stats

    Returns stats (floor price, listed count, volume) for a collection.

    Args:
        symbol: Collection symbol.
        time_window: '24h', '7d', '30d', or 'all' (default 'all').
        listing_agg_mode: True to aggregate listings across all marketplaces.
    """
    params: dict[str, Any] = {}
    if time_window is not None:
        params["timeWindow"] = time_window
    if listing_agg_mode:
        params["listingAggMode"] = "true"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{symbol}/stats",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_collection_activities(
    symbol: str,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """GET /v2/collections/{symbol}/activities

    Returns trading activities for a collection.

    Args:
        symbol: Collection symbol.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 1000).
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 1000),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/collections/{symbol}/activities",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_wallet_escrow_balance(wallet_address: str) -> dict[str, Any]:
    """GET /v2/wallets/{wallet_address}/escrow_balance

    Returns the Magic Eden escrow balance for a wallet.

    Args:
        wallet_address: Wallet address to look up.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}/escrow_balance",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_wallet_offers_received(
    wallet_address: str,
    min_price: float | None = None,
    max_price: float | None = None,
    offset: int = 0,
    limit: int = 100,
    sort: str = "updatedAt",
    sort_direction: str = "desc",
) -> list[dict[str, Any]]:
    """GET /v2/wallets/{wallet_address}/offers_received

    Returns offers (bids) received on NFTs owned by a wallet.

    Args:
        wallet_address: Wallet address to look up.
        min_price: Filter offers below this price (SOL).
        max_price: Filter offers above this price (SOL).
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
        sort: Sort field — 'updatedAt' (default) or 'bidAmount'.
        sort_direction: 'asc' or 'desc' (default 'desc').
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}/offers_received",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_wallet_offers_made(
    wallet_address: str,
    min_price: float | None = None,
    max_price: float | None = None,
    offset: int = 0,
    limit: int = 100,
    sort: str = "bidAmount",
    sort_direction: str = "desc",
) -> list[dict[str, Any]]:
    """GET /v2/wallets/{wallet_address}/offers_made

    Returns offers (bids) made by a wallet.

    Args:
        wallet_address: Wallet address to look up.
        min_price: Filter offers below this price (SOL).
        max_price: Filter offers above this price (SOL).
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
        sort: Sort field — 'updatedAt' or 'bidAmount' (default 'bidAmount').
        sort_direction: 'asc' or 'desc' (default 'desc').
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}/offers_made",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_owner_activities(
    owner: str,
    created_at: str | None = None,
) -> list[dict[str, Any]]:
    """GET /v2/wallets/owner/activities

    Returns trading activities for a wallet filtered by owner query param.

    Args:
        owner: Wallet address (required query param).
        created_at: Filter activities created after this time (ISO string or timestamp).
    """
    params: dict[str, Any] = {"owner": owner}
    if created_at is not None:
        params["createdAt"] = created_at

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/owner/activities",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_wallet_activities(
    wallet_address: str,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """GET /v2/wallets/{wallet_address}/activities

    Returns trading activities (buyNow, bid, list, etc.) for a wallet.

    Args:
        wallet_address: Wallet address to look up.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}/activities",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_wallet(wallet_address: str) -> dict[str, Any]:
    """GET /v2/wallets/{wallet_address}

    Returns Magic Eden profile info for a wallet (displayName, avatar).

    Args:
        wallet_address: Wallet address to look up.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_wallet_tokens(
    wallet_address: str,
    collection_symbol: str | None = None,
    mcc_address: str | None = None,
    offset: int = 0,
    limit: int = 100,
    min_price: float | None = None,
    max_price: float | None = None,
    list_status: str | None = None,
    sort: str = "updatedAt",
    sort_direction: str = "desc",
) -> list[dict[str, Any]]:
    """GET /v2/wallets/{wallet_address}/tokens

    Returns NFT tokens owned by a wallet.

    Args:
        wallet_address: Wallet address to look up.
        collection_symbol: Filter by collection symbol (max 255 chars).
        mcc_address: Filter by Metaplex Certified Collection address (max 48 chars).
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
        min_price: Filter listings below this price (SOL).
        max_price: Filter listings above this price (SOL).
        list_status: 'listed', 'unlisted', or 'both' (default 'both').
        sort: Sort field — 'updatedAt' or 'listPrice'.
        sort_direction: 'asc' or 'desc' (default 'desc').
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if collection_symbol is not None:
        params["collection_symbol"] = collection_symbol
    if mcc_address is not None:
        params["mcc_address"] = mcc_address
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if list_status is not None:
        params["listStatus"] = list_status

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/wallets/{wallet_address}/tokens",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_token(token_mint: str) -> dict[str, Any]:
    """GET /v2/tokens/{token_mint}

    Returns metadata for a specific NFT token.

    Args:
        token_mint: Mint address of the token or asset ID of compressed NFT.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/tokens/{token_mint}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_token_listings(
    token_mint: str,
    listing_agg_mode: bool = False,
) -> list[dict[str, Any]]:
    """GET /v2/tokens/{token_mint}/listings

    Returns active marketplace listings for a specific NFT token.

    Args:
        token_mint: Mint address of the token or asset ID of compressed NFT.
        listing_agg_mode: True to return aggregated marketplace listings
            (Tensor, Magic Eden, etc.), False for Magic Eden only.
    """
    params: dict[str, str] = {}
    if listing_agg_mode:
        params["listingAggMode"] = "true"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/tokens/{token_mint}/listings",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_token_offers_received(
    token_mint: str,
    min_price: float | None = None,
    max_price: float | None = None,
    offset: int = 0,
    limit: int = 100,
    sort: str = "updatedAt",
    sort_direction: str = "desc",
) -> list[dict[str, Any]]:
    """GET /v2/tokens/{token_mint}/offers_received

    Returns offers (bids) received on a specific NFT token.

    Args:
        token_mint: Mint address of the token.
        min_price: Filter out offers below this price (SOL).
        max_price: Filter out offers above this price (SOL).
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
        sort: Sort field — 'updatedAt' or 'bidAmount'.
        sort_direction: 'asc' or 'desc' (default 'desc').
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
        "sort": sort,
        "sort_direction": sort_direction,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/tokens/{token_mint}/offers_received",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_token_activities(
    token_mint: str,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """GET /v2/tokens/{token_mint}/activities

    Returns trading activities (listings, sales, bids, cancellations) for a specific NFT.

    Args:
        token_mint: Mint address of the token.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/tokens/{token_mint}/activities",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def get_mmm_pools(
    collection_symbol: str | None = None,
    pools: list[str] | None = None,
    owner: str | None = None,
    offset: int = 0,
    limit: int = 100,
    field: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    """GET /v2/mmm/pools

    Returns MMM (market maker) pools filtered by collection or owner.

    Args:
        collection_symbol: Collection symbol. At least this or owner required.
        pools: List of pool keys to filter by.
        owner: Pool owner public key. At least this or collection_symbol required.
        offset: Number of items to skip (default 0).
        limit: Number of items to return (default 100, max 500).
        field: Sort field: "0"=NONE, "1"=ADDRESS, "2"=SPOT_PRICE, "5"=BUYSIDE_ADJUSTED_PRICE.
        direction: Sort direction: "0"=NONE, "1"=DESC, "2"=INC.
    """
    params: dict[str, Any] = {
        "offset": offset,
        "limit": min(limit, 500),
    }
    if collection_symbol is not None:
        params["collectionSymbol"] = collection_symbol
    if owner is not None:
        params["owner"] = owner
    if field is not None:
        params["field"] = field
    if direction is not None:
        params["direction"] = direction
    if pools:
        params["pools"] = pools

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/mmm/pools",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_sol_fulfill_sell_instruction(
    pool: str,
    asset_amount: float,
    max_payment_amount: float,
    buyside_creator_royalty_bp: int,
    buyer: str,
    asset_mint: str,
    allowlist_aux_account: str | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/sol-fulfill-sell

    Returns an unsigned transaction for a buyer to fulfill an MMM pool sell order.
    """
    params: dict[str, Any] = {
        "pool": pool,
        "assetAmount": asset_amount,
        "maxPaymentAmount": max_payment_amount,
        "buysideCreatorRoyaltyBp": buyside_creator_royalty_bp,
        "buyer": buyer,
        "assetMint": asset_mint,
    }
    if allowlist_aux_account is not None:
        params["allowlistAuxAccount"] = allowlist_aux_account
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/sol-fulfill-sell",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_sol_fulfill_buy_instruction(
    pool: str,
    asset_amount: float,
    min_payment_amount: float,
    seller: str,
    asset_mint: str,
    asset_token_account: str,
    allowlist_aux_account: str | None = None,
    skip_delist: bool | None = None,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/sol-fulfill-buy

    Returns an unsigned transaction for a seller to fulfill an MMM pool buy order.
    """
    params: dict[str, Any] = {
        "pool": pool,
        "assetAmount": asset_amount,
        "minPaymentAmount": min_payment_amount,
        "seller": seller,
        "assetMint": asset_mint,
        "assetTokenAccount": asset_token_account,
    }
    if allowlist_aux_account is not None:
        params["allowlistAuxAccount"] = allowlist_aux_account
    if skip_delist is not None:
        params["skipDelist"] = str(skip_delist).lower()
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/sol-fulfill-buy",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_sol_close_pool_instruction(
    pool: str,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/sol-close-pool

    Returns an unsigned transaction to close an MMM pool and reclaim funds.
    """
    params: dict[str, Any] = {"pool": pool}
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/sol-close-pool",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_sol_withdraw_buy_instruction(
    pool: str,
    sol_amount: float,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/sol-withdraw-buy

    Returns an unsigned transaction to withdraw SOL from an MMM pool's buy-side.
    """
    params: dict[str, Any] = {"pool": pool, "solAmount": sol_amount}
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/sol-withdraw-buy",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_sol_deposit_buy_instruction(
    pool: str,
    sol_amount: float,
    priority_fee: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/sol-deposit-buy

    Returns an unsigned transaction to deposit SOL into an MMM pool's buy-side.
    """
    params: dict[str, Any] = {"pool": pool, "solAmount": sol_amount}
    if priority_fee is not None:
        params["priorityFee"] = priority_fee

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/sol-deposit-buy",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_update_pool_instruction(
    pool: str,
    spot_price: float,
    curve_type: str,
    curve_delta: float,
    reinvest_buy: bool,
    reinvest_sell: bool,
    expiry: int,
    lp_fee_bp: int,
    buyside_creator_royalty_bp: int,
    priority_fee: int | None = None,
    shared_escrow_count: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/update-pool

    Returns an unsigned transaction to update an existing MMM pool.
    """
    params: dict[str, Any] = {
        "pool": pool,
        "spotPrice": spot_price,
        "curveType": curve_type,
        "curveDelta": curve_delta,
        "reinvestBuy": str(reinvest_buy).lower(),
        "reinvestSell": str(reinvest_sell).lower(),
        "expiry": expiry,
        "lpFeeBp": lp_fee_bp,
        "buysideCreatorRoyaltyBp": buyside_creator_royalty_bp,
    }
    if priority_fee is not None:
        params["priorityFee"] = priority_fee
    if shared_escrow_count is not None:
        params["sharedEscrowCount"] = shared_escrow_count

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/update-pool",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_create_pool_instruction(
    spot_price: float,
    curve_type: str,
    curve_delta: float,
    reinvest_buy: bool,
    reinvest_sell: bool,
    lp_fee_bp: int,
    buyside_creator_royalty_bp: int,
    payment_mint: str,
    collection_symbol: str,
    owner: str,
    expiry: int | None = None,
    sol_deposit: float | None = None,
    priority_fee: int | None = None,
    shared_escrow_count: int | None = None,
) -> dict[str, Any]:
    """GET /v2/instructions/mmm/create-pool

    Returns an unsigned transaction to create a new MMM pool.
    """
    params: dict[str, Any] = {
        "spotPrice": spot_price,
        "curveType": curve_type,
        "curveDelta": curve_delta,
        "reinvestBuy": str(reinvest_buy).lower(),
        "reinvestSell": str(reinvest_sell).lower(),
        "lpFeeBp": lp_fee_bp,
        "buysideCreatorRoyaltyBp": buyside_creator_royalty_bp,
        "paymentMint": payment_mint,
        "collectionSymbol": collection_symbol,
        "owner": owner,
    }
    if expiry is not None:
        params["expiry"] = expiry
    if sol_deposit is not None:
        params["solDeposit"] = sol_deposit
    if priority_fee is not None:
        params["priorityFee"] = priority_fee
    if shared_escrow_count is not None:
        params["sharedEscrowCount"] = shared_escrow_count

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/instructions/mmm/create-pool",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_mmm_token_pools(
    mint_address: str,
    limit: int = 1,
) -> dict[str, Any]:
    """GET /v2/mmm/token/{mint_address}/pools

    Returns best MMM buy pools for a specific NFT mint.

    Args:
        mint_address: Mint address of the NFT.
        limit: Total best offers to return (default 1, max 5). Best offer comes first.
    """
    params: dict[str, Any] = {"limit": min(limit, 5)}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/mmm/token/{mint_address}/pools",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()
