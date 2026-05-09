"""
Jupiter tools comprehensive test suite.

Tests every parameter combination for all 6 Jupiter tools:
  jup_prices, jup_search, jup_recent, jup_trending, jup_tokens_tag, jup_verify_eligibility

Run:
  cd services/defi-query-service
  python3 -m pytest tests/test_jupiter_tools.py -v

Or run live API tests (requires internet):
  python3 -m pytest tests/test_jupiter_tools.py -v -m live
"""

import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from tools import (
    jup_prices, jup_search, jup_recent, jup_trending,
    jup_tokens_tag, jup_verify_eligibility, dispatch, TOOL_SCHEMAS,
)

# ─── Well-known mints ─────────────────────────────────────────────────────────

SOL   = "So11111111111111111111111111111111111111112"
USDC  = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
JUP   = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
WIF   = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JITOSOL = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MSOL  = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"


# ─── Fixtures: mock Jupiter API responses ─────────────────────────────────────

PRICE_RESPONSE = {
    "data": {
        SOL:  {"usdPrice": 85.55, "priceChange24h": -1.05, "liquidity": 52000000.0, "decimals": 9,  "blockId": 300000000, "createdAt": "2020-03-16T00:00:00Z"},
        BONK: {"usdPrice": 6.04e-06, "priceChange24h": -2.08, "liquidity": 18000000.0, "decimals": 5, "blockId": 300000001, "createdAt": "2022-12-25T00:00:00Z"},
        JUP:  {"usdPrice": 0.1759, "priceChange24h": -2.61, "liquidity": 8000000.0,  "decimals": 6,  "blockId": 300000002, "createdAt": "2024-01-31T00:00:00Z"},
    }
}

SEARCH_RESPONSE = [
    {
        "id": BONK,
        "name": "Bonk",
        "symbol": "BONK",
        "icon": "https://arweave.net/bonk.png",
        "decimals": 5,
        "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "createdAt": "2022-12-25T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
        "twitter": "@bonk_inu",
        "telegram": None,
        "website": "https://bonkcoin.com",
        "discord": None,
        "instagram": None,
        "tiktok": None,
        "otherUrl": None,
        "dev": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "mintAuthority": None,
        "freezeAuthority": None,
        "circSupply": 66889000000000000.0,
        "totalSupply": 100000000000000000.0,
        "holderCount": 1005689,
        "fdv": 603000000.0,
        "mcap": 531253120.69,
        "usdPrice": 6.037e-06,
        "liquidity": 18000000.0,
        "priceBlockId": 300000003,
        "stats5m": {"priceChange": 0.2, "holderChange": 5, "liquidityChange": 1000, "volumeChange": 5000, "buyVolume": 50000, "sellVolume": 45000, "buyOrganicVolume": 48000, "sellOrganicVolume": 43000, "numBuys": 120, "numSells": 100, "numTraders": 80, "numOrganicBuyers": 70, "numNetBuyers": 20},
        "stats1h": {"priceChange": 0.8, "holderChange": 15, "liquidityChange": 5000, "volumeChange": 30000, "buyVolume": 512000, "sellVolume": 480000, "buyOrganicVolume": 490000, "sellOrganicVolume": 460000, "numBuys": 850, "numSells": 780, "numTraders": 400, "numOrganicBuyers": 380, "numNetBuyers": 70},
        "stats6h": {"priceChange": -1.2, "holderChange": 50, "liquidityChange": 15000, "volumeChange": 80000, "buyVolume": 2100000, "sellVolume": 2300000, "buyOrganicVolume": 2000000, "sellOrganicVolume": 2200000, "numBuys": 3500, "numSells": 3800, "numTraders": 1200, "numOrganicBuyers": 1100, "numNetBuyers": -100},
        "stats24h": {"priceChange": -2.08, "holderChange": 120, "liquidityChange": 50000, "volumeChange": 300000, "buyVolume": 6500000, "sellVolume": 7200000, "buyOrganicVolume": 6200000, "sellOrganicVolume": 6900000, "numBuys": 12000, "numSells": 13500, "numTraders": 4500, "numOrganicBuyers": 4200, "numNetBuyers": -250},
        "firstPool": {"id": "pool_abc123", "createdAt": "2022-12-25T06:00:00Z"},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 35.42, "devBalancePercentage": 0.0000001, "devMints": 10},
        "organicScore": 82,
        "organicScoreLabel": "high",
        "isVerified": True,
        "tags": ["verified", "community"],
        "launchpad": None,
        "graduatedPool": None,
    }
]

RECENT_RESPONSE = [
    {
        "id": "pump111111111111111111111111111111111111111",
        "name": "PumpToken",
        "symbol": "PUMP",
        "decimals": 6,
        "icon": None,
        "usdPrice": 0.0000123,
        "liquidity": 4500.0,
        "holderCount": 23,
        "organicScore": 15,
        "organicScoreLabel": "low",
        "isVerified": False,
        "firstPool": {"id": "pool_newpool", "createdAt": "2025-04-19T12:00:00Z"},
    },
    {
        "id": "meme2222222222222222222222222222222222222222",
        "name": "MemeRocket",
        "symbol": "MRKT",
        "decimals": 9,
        "icon": None,
        "usdPrice": 0.00000045,
        "liquidity": 800.0,
        "holderCount": 7,
        "organicScore": 8,
        "organicScoreLabel": "low",
        "isVerified": False,
        "firstPool": {"id": "pool_newpool2", "createdAt": "2025-04-19T11:55:00Z"},
    },
]

TRENDING_RESPONSE = [
    {
        "id": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
        "name": "Fartcoin",
        "symbol": "Fartcoin",
        "decimals": 6,
        "usdPrice": 0.2055,
        "liquidity": 12000000.0,
        "mcap": 204000000.0,
        "fdv": 204000000.0,
        "holderCount": 85000,
        "stats1h": {"priceChange": 4.2, "buyVolume": 3200000, "sellVolume": 2800000, "buyOrganicVolume": 3100000, "sellOrganicVolume": 2700000, "numBuys": 2400, "numSells": 1800, "numTraders": 950, "numOrganicBuyers": 900, "numNetBuyers": 150},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 22.1, "devBalancePercentage": 0.001, "devMints": 1},
        "organicScore": 88,
        "organicScoreLabel": "high",
        "isVerified": False,
        "tags": ["community"],
        "launchpad": "pump.fun",
        "graduatedPool": "graduate_pool_abc",
    },
    {
        "id": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "name": "dogwifhat",
        "symbol": "WIF",
        "decimals": 6,
        "usdPrice": 0.72,
        "liquidity": 25000000.0,
        "mcap": 720000000.0,
        "fdv": 720000000.0,
        "holderCount": 200000,
        "stats1h": {"priceChange": 2.1, "buyVolume": 5100000, "sellVolume": 4800000, "buyOrganicVolume": 4900000, "sellOrganicVolume": 4600000, "numBuys": 4500, "numSells": 3900, "numTraders": 1800, "numOrganicBuyers": 1750, "numNetBuyers": 200},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 18.5, "devBalancePercentage": 0.0, "devMints": 1},
        "organicScore": 92,
        "organicScoreLabel": "high",
        "isVerified": True,
        "tags": ["verified"],
        "launchpad": None,
        "graduatedPool": None,
    },
]

LST_TAG_RESPONSE = [
    {
        "id": JITOSOL,
        "name": "Jito Staked SOL",
        "symbol": "JitoSOL",
        "decimals": 9,
        "icon": "https://jito.network/jitosol.png",
        "usdPrice": 108.9,
        "liquidity": 45000000.0,
        "mcap": 1800000000.0,
        "fdv": 1800000000.0,
        "holderCount": 120000,
        "apy": {"value": 8.2, "label": "8.2% APY"},
        "stats24h": {"priceChange": -1.0, "buyVolume": 4500000, "sellVolume": 4200000, "buyOrganicVolume": 4400000, "sellOrganicVolume": 4100000, "numBuys": 3200, "numSells": 2900, "numTraders": 1100, "numOrganicBuyers": 1080, "numNetBuyers": 80},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 45.0, "devBalancePercentage": 0.0, "devMints": 1},
        "organicScore": 91,
        "organicScoreLabel": "high",
        "isVerified": True,
        "tags": ["lst", "verified"],
        "launchpad": None,
    },
    {
        "id": MSOL,
        "name": "Marinade staked SOL",
        "symbol": "mSOL",
        "decimals": 9,
        "icon": "https://marinade.finance/msol.png",
        "usdPrice": 117.7,
        "liquidity": 30000000.0,
        "mcap": 1100000000.0,
        "fdv": 1100000000.0,
        "holderCount": 80000,
        "apy": {"value": 7.8, "label": "7.8% APY"},
        "stats24h": {"priceChange": -1.1, "buyVolume": 2100000, "sellVolume": 2000000, "buyOrganicVolume": 2050000, "sellOrganicVolume": 1950000, "numBuys": 1500, "numSells": 1400, "numTraders": 600, "numOrganicBuyers": 590, "numNetBuyers": 40},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 52.0, "devBalancePercentage": 0.0, "devMints": 1},
        "organicScore": 85,
        "organicScoreLabel": "high",
        "isVerified": True,
        "tags": ["lst", "verified"],
        "launchpad": None,
    },
]

VERIFIED_TAG_RESPONSE = [
    {
        "id": JUP,
        "name": "Jupiter",
        "symbol": "JUP",
        "decimals": 6,
        "usdPrice": 0.1759,
        "liquidity": 8000000.0,
        "mcap": 175900000.0,
        "fdv": 1759000000.0,
        "holderCount": 450000,
        "apy": None,
        "stats24h": {"priceChange": -2.61, "buyVolume": 3500000, "sellVolume": 3800000, "numBuys": 5200, "numSells": 5800, "numTraders": 2100, "numOrganicBuyers": 2050, "numNetBuyers": -150},
        "audit": {"isSus": False, "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "topHoldersPercentage": 28.0, "devBalancePercentage": 0.02, "devMints": 1},
        "organicScore": 90,
        "organicScoreLabel": "high",
        "isVerified": True,
        "tags": ["verified"],
    },
]

VERIFY_ELIGIBLE_RESPONSE = {
    "tokenExists": True,
    "isVerified": False,
    "canVerify": True,
    "canMetadata": True,
    "verificationError": None,
    "metadataError": None,
}

VERIFY_ALREADY_VERIFIED = {
    "tokenExists": True,
    "isVerified": True,
    "canVerify": False,
    "canMetadata": True,
    "verificationError": "Token is already verified",
    "metadataError": None,
}

VERIFY_NOT_FOUND = {
    "tokenExists": False,
    "isVerified": False,
    "canVerify": False,
    "canMetadata": False,
    "verificationError": "Token does not exist on-chain",
    "metadataError": "Token does not exist on-chain",
}


# ─── Schema validation helpers ────────────────────────────────────────────────

def get_schema(name: str) -> dict:
    return next(t for t in TOOL_SCHEMAS if t["name"] == name)


def assert_required(schema: dict, *params):
    required = schema["input_schema"].get("required", [])
    for p in params:
        assert p in required, f"'{p}' should be required in {schema['name']}"


def assert_optional(schema: dict, *params):
    props = schema["input_schema"].get("properties", {})
    required = schema["input_schema"].get("required", [])
    for p in params:
        assert p in props, f"'{p}' should be in properties of {schema['name']}"
        assert p not in required, f"'{p}' should be OPTIONAL in {schema['name']}"


def assert_no_params(schema: dict):
    props = schema["input_schema"].get("properties", {})
    assert props == {}, f"{schema['name']} should have no parameters, got: {list(props.keys())}"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Schema correctness tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemas:
    def test_jup_prices_schema(self):
        s = get_schema("jup_prices")
        assert_required(s, "mints")
        desc = s["description"]
        assert "usdPrice" in desc
        assert "priceChange24h" in desc
        assert "liquidity" in desc
        assert "50" in desc  # max 50

    def test_jup_search_schema(self):
        s = get_schema("jup_search")
        assert_required(s, "query")
        # limit must NOT exist (not in Jupiter API)
        assert "limit" not in s["input_schema"].get("properties", {})
        desc = s["description"]
        assert "organicScore" in desc
        assert "audit" in desc
        assert "100" in desc  # max 100 mints

    def test_jup_recent_schema(self):
        s = get_schema("jup_recent")
        assert_no_params(s)
        assert "30" in s["description"]  # always 30

    def test_jup_trending_schema(self):
        s = get_schema("jup_trending")
        assert_optional(s, "category", "interval", "limit")
        props = s["input_schema"]["properties"]
        assert "toptrending" in props["category"]["description"]
        assert "toptraded" in props["category"]["description"]
        assert "toporganicscore" in props["category"]["description"]
        assert "5m" in props["interval"]["description"]
        assert "1h" in props["interval"]["description"]
        assert "6h" in props["interval"]["description"]
        assert "24h" in props["interval"]["description"]
        assert "100" in props["limit"]["description"]  # max 100

    def test_jup_tokens_tag_schema(self):
        s = get_schema("jup_tokens_tag")
        assert_required(s, "query")
        props = s["input_schema"]["properties"]
        # param name must be 'query' not 'tag'
        assert "query" in props
        assert "tag" not in props
        # only lst and verified are valid
        assert "lst" in props["query"]["description"]
        assert "verified" in props["query"]["description"]
        # invalid values must NOT be listed
        for invalid in ["strict", "clone", "community", "pump"]:
            assert invalid not in props["query"]["description"], \
                f"'{invalid}' is not a valid tag per Jupiter docs"

    def test_jup_verify_eligibility_schema(self):
        s = get_schema("jup_verify_eligibility")
        assert_required(s, "token_id")
        desc = s["description"]
        assert "tokenExists" in desc
        assert "isVerified" in desc
        assert "canVerify" in desc
        assert "canMetadata" in desc

    def test_all_jupiter_tools_present(self):
        names = {t["name"] for t in TOOL_SCHEMAS}
        expected = {"jup_prices", "jup_search", "jup_recent", "jup_trending",
                    "jup_tokens_tag", "jup_verify_eligibility"}
        missing = expected - names
        assert not missing, f"Missing Jupiter tools: {missing}"

    def test_no_invalid_params_jup_search(self):
        s = get_schema("jup_search")
        # limit is not in Jupiter search API
        assert "limit" not in s["input_schema"].get("properties", {})

    def test_no_invalid_params_jup_recent(self):
        s = get_schema("jup_recent")
        # limit is not in Jupiter recent API
        assert "limit" not in s["input_schema"].get("properties", {})


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Unit tests with mocked HTTP
# ═══════════════════════════════════════════════════════════════════════════════

class TestJupPricesUnit:
    @pytest.mark.asyncio
    async def test_single_mint(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=PRICE_RESPONSE):
            result = await jup_prices(SOL)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_multiple_mints(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=PRICE_RESPONSE):
            result = await jup_prices(f"{SOL},{BONK},{JUP}")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_passes_ids_param(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=PRICE_RESPONSE) as mock_get:
            await jup_prices(f"{SOL},{BONK}")
        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        assert "ids" in params
        assert SOL in params["ids"]

    @pytest.mark.asyncio
    async def test_dispatch_jup_prices(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=PRICE_RESPONSE):
            result = await dispatch("jup_prices", {"mints": SOL})
        assert isinstance(result, dict)


class TestJupSearchUnit:
    @pytest.mark.asyncio
    async def test_search_by_symbol(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await jup_search("BONK")
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_search_by_mint_address(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await jup_search(BONK)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_search_multiple_mints(self):
        multi_response = SEARCH_RESPONSE + SEARCH_RESPONSE[:1]
        with patch("tools._get", new_callable=AsyncMock, return_value=multi_response):
            result = await jup_search(f"{BONK},{JUP},{WIF}")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_response_contains_all_fields(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await jup_search("BONK")
        token = result[0]
        # Verify all documented fields are accessible
        assert "id" in token
        assert "name" in token
        assert "symbol" in token
        assert "usdPrice" in token
        assert "mcap" in token
        assert "fdv" in token
        assert "holderCount" in token
        assert "liquidity" in token
        assert "circSupply" in token
        assert "totalSupply" in token
        assert "audit" in token
        assert "organicScore" in token
        assert "organicScoreLabel" in token
        assert "isVerified" in token
        assert "stats5m" in token
        assert "stats1h" in token
        assert "stats6h" in token
        assert "stats24h" in token

    @pytest.mark.asyncio
    async def test_stats24h_contains_all_swapstats_fields(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await jup_search("BONK")
        stats = result[0]["stats24h"]
        for field in ["priceChange", "holderChange", "liquidityChange", "volumeChange",
                      "buyVolume", "sellVolume", "buyOrganicVolume", "sellOrganicVolume",
                      "numBuys", "numSells", "numTraders", "numOrganicBuyers", "numNetBuyers"]:
            assert field in stats, f"stats24h missing field: {field}"

    @pytest.mark.asyncio
    async def test_audit_contains_all_fields(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await jup_search("BONK")
        audit = result[0]["audit"]
        for field in ["isSus", "mintAuthorityDisabled", "freezeAuthorityDisabled",
                      "topHoldersPercentage", "devBalancePercentage", "devMints"]:
            assert field in audit, f"audit missing field: {field}"

    @pytest.mark.asyncio
    async def test_no_limit_param_sent(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE) as mock_get:
            await jup_search("BONK")
        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        assert "limit" not in params, "jup_search must NOT send limit param (not in Jupiter API)"

    @pytest.mark.asyncio
    async def test_dispatch_jup_search(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=SEARCH_RESPONSE):
            result = await dispatch("jup_search", {"query": "BONK"})
        assert isinstance(result, list)


class TestJupRecentUnit:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=RECENT_RESPONSE):
            result = await jup_recent()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_no_params_sent(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=RECENT_RESPONSE) as mock_get:
            await jup_recent()
        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get("params") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
        # params should be None or empty — no limit, no other params
        if params:
            assert "limit" not in params, "jup_recent must NOT send limit (not supported by API)"

    @pytest.mark.asyncio
    async def test_response_has_firstpool(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=RECENT_RESPONSE):
            result = await jup_recent()
        token = result[0]
        assert "firstPool" in token
        assert "id" in token["firstPool"]
        assert "createdAt" in token["firstPool"]

    @pytest.mark.asyncio
    async def test_response_fields(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=RECENT_RESPONSE):
            result = await jup_recent()
        token = result[0]
        for field in ["id", "name", "symbol", "decimals", "usdPrice", "liquidity",
                      "holderCount", "organicScore", "organicScoreLabel", "isVerified"]:
            assert field in token, f"recent response missing field: {field}"

    @pytest.mark.asyncio
    async def test_dispatch_jup_recent_no_args(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=RECENT_RESPONSE):
            result = await dispatch("jup_recent", {})
        assert isinstance(result, list)


class TestJupTrendingUnit:
    @pytest.mark.asyncio
    async def test_default_params(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE):
            result = await jup_trending()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["toptrending", "toptraded", "toporganicscore"])
    async def test_all_categories(self, category):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE) as mock_get:
            await jup_trending(category=category, interval="1h")
        call_args = mock_get.call_args
        url = call_args[0][0]
        assert category in url, f"Category '{category}' not in URL: {url}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interval", ["5m", "1h", "6h", "24h"])
    async def test_all_intervals(self, interval):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE) as mock_get:
            await jup_trending(category="toptrending", interval=interval)
        call_args = mock_get.call_args
        url = call_args[0][0]
        assert interval in url, f"Interval '{interval}' not in URL: {url}"

    @pytest.mark.asyncio
    async def test_url_format_no_category_segment(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE) as mock_get:
            await jup_trending(category="toptrending", interval="1h")
        url = mock_get.call_args[0][0]
        # Must be /tokens/v2/{category}/{interval}, NOT /tokens/v2/category/{category}/{interval}
        assert "/category/" not in url, f"URL has wrong 'category/' segment: {url}"
        assert "/tokens/v2/toptrending/1h" in url

    @pytest.mark.asyncio
    async def test_limit_param_passed(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE) as mock_get:
            await jup_trending(limit=10)
        call_kwargs = mock_get.call_args[1]
        params = call_kwargs.get("params", {})
        assert "limit" in params
        assert params["limit"] == 10

    @pytest.mark.asyncio
    async def test_limit_max_100(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE) as mock_get:
            await jup_trending(limit=100)
        params = mock_get.call_args[1].get("params", {})
        assert params["limit"] == 100

    @pytest.mark.asyncio
    async def test_dispatch_with_all_params(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE):
            result = await dispatch("jup_trending", {
                "category": "toptraded",
                "interval": "24h",
                "limit": 20,
            })
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_dispatch_empty_params_uses_defaults(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=TRENDING_RESPONSE):
            result = await dispatch("jup_trending", {})
        assert isinstance(result, list)


class TestJupTokensTagUnit:
    @pytest.mark.asyncio
    async def test_lst_tag(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=LST_TAG_RESPONSE):
            result = await jup_tokens_tag(query="lst")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_verified_tag(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFIED_TAG_RESPONSE):
            result = await jup_tokens_tag(query="verified")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_param_name_is_query_not_tag(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=LST_TAG_RESPONSE) as mock_get:
            await jup_tokens_tag(query="lst")
        call_kwargs = mock_get.call_args[1]
        params = call_kwargs.get("params", {})
        assert "query" in params, "API param must be 'query', not 'tag'"
        assert "tag" not in params, "Must NOT use 'tag' param name"

    @pytest.mark.asyncio
    async def test_param_value_passed_correctly(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=LST_TAG_RESPONSE) as mock_get:
            await jup_tokens_tag(query="lst")
        params = mock_get.call_args[1].get("params", {})
        assert params["query"] == "lst"

    @pytest.mark.asyncio
    async def test_lst_response_has_apy_field(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=LST_TAG_RESPONSE):
            result = await jup_tokens_tag(query="lst")
        # LST tokens can have APY data
        token = result[0]
        assert "apy" in token

    @pytest.mark.asyncio
    async def test_limit_applied(self):
        large_response = LST_TAG_RESPONSE * 30  # 60 items
        with patch("tools._get", new_callable=AsyncMock, return_value=large_response):
            result = await jup_tokens_tag(query="lst", limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_dispatch_lst(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=LST_TAG_RESPONSE):
            result = await dispatch("jup_tokens_tag", {"query": "lst"})
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_dispatch_verified(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFIED_TAG_RESPONSE):
            result = await dispatch("jup_tokens_tag", {"query": "verified"})
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_dispatch_with_limit(self):
        large_response = VERIFIED_TAG_RESPONSE * 10
        with patch("tools._get", new_callable=AsyncMock, return_value=large_response):
            result = await dispatch("jup_tokens_tag", {"query": "verified", "limit": 3})
        assert len(result) == 3


class TestJupVerifyEligibilityUnit:
    @pytest.mark.asyncio
    async def test_eligible_token(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFY_ELIGIBLE_RESPONSE):
            with patch("tools._JUP_API_KEY", "test_key"):
                result = await jup_verify_eligibility(token_id=WIF)
        assert result["tokenExists"] is True
        assert result["canVerify"] is True

    @pytest.mark.asyncio
    async def test_already_verified_token(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFY_ALREADY_VERIFIED):
            with patch("tools._JUP_API_KEY", "test_key"):
                result = await jup_verify_eligibility(token_id=BONK)
        assert result["isVerified"] is True
        assert result["canVerify"] is False
        assert "verificationError" in result

    @pytest.mark.asyncio
    async def test_nonexistent_token(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFY_NOT_FOUND):
            with patch("tools._JUP_API_KEY", "test_key"):
                result = await jup_verify_eligibility(token_id="invalid_mint_111111111111111111")
        assert result["tokenExists"] is False
        assert result["canVerify"] is False
        assert result["canMetadata"] is False

    @pytest.mark.asyncio
    async def test_requires_api_key(self):
        import tools as tools_mod
        original_key = tools_mod._JUP_API_KEY
        try:
            tools_mod._JUP_API_KEY = ""
            result = await jup_verify_eligibility(token_id=BONK)
            assert "error" in result
            assert "JUPITER_API_KEY" in result["error"]
        finally:
            tools_mod._JUP_API_KEY = original_key

    @pytest.mark.asyncio
    async def test_response_has_all_fields(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFY_ELIGIBLE_RESPONSE):
            with patch("tools._JUP_API_KEY", "test_key"):
                result = await jup_verify_eligibility(token_id=WIF)
        for field in ["tokenExists", "isVerified", "canVerify", "canMetadata"]:
            assert field in result, f"verify_eligibility response missing field: {field}"

    @pytest.mark.asyncio
    async def test_dispatch_verify_eligibility(self):
        with patch("tools._get", new_callable=AsyncMock, return_value=VERIFY_ELIGIBLE_RESPONSE):
            with patch("tools._JUP_API_KEY", "test_key"):
                result = await dispatch("jup_verify_eligibility", {"token_id": WIF})
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Dispatcher routing tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDispatcherRouting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,tool_input,mock_response", [
        ("jup_prices",           {"mints": SOL},                              PRICE_RESPONSE),
        ("jup_search",           {"query": "BONK"},                           SEARCH_RESPONSE),
        ("jup_recent",           {},                                           RECENT_RESPONSE),
        ("jup_trending",         {"category": "toptrending", "interval": "1h"}, TRENDING_RESPONSE),
        ("jup_trending",         {"category": "toptraded",   "interval": "24h"}, TRENDING_RESPONSE),
        ("jup_trending",         {"category": "toporganicscore", "interval": "5m"}, TRENDING_RESPONSE),
        ("jup_trending",         {"category": "toptrending", "interval": "6h", "limit": 10}, TRENDING_RESPONSE),
        ("jup_tokens_tag",       {"query": "lst"},                             LST_TAG_RESPONSE),
        ("jup_tokens_tag",       {"query": "verified"},                        VERIFIED_TAG_RESPONSE),
        ("jup_tokens_tag",       {"query": "lst", "limit": 5},                LST_TAG_RESPONSE),
    ])
    async def test_dispatch_routes_correctly(self, tool_name, tool_input, mock_response):
        with patch("tools._get", new_callable=AsyncMock, return_value=mock_response):
            result = await dispatch(tool_name, tool_input)
        assert result is not None
        assert "error" not in str(result)[:50]

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self):
        result = await dispatch("jup_nonexistent", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_dispatch_jup_verify_no_key(self):
        import tools as tools_mod
        original = tools_mod._JUP_API_KEY
        try:
            tools_mod._JUP_API_KEY = ""
            result = await dispatch("jup_verify_eligibility", {"token_id": BONK})
            assert "error" in result
        finally:
            tools_mod._JUP_API_KEY = original


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: LLM interpretation verification (response field coverage)
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponseFieldCoverage:
    """Verify that the system prompt mentions all critical response fields."""

    def _load_system_prompt(self) -> str:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "orchestrator",
            __file__.replace("tests/test_jupiter_tools.py", "orchestrator.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.SYSTEM_PROMPT

    def test_jup_prices_fields_in_prompt(self):
        prompt = self._load_system_prompt()
        for field in ["usdPrice", "priceChange24h", "liquidity", "decimals", "blockId"]:
            assert field in prompt, f"System prompt missing jup_prices field: {field}"

    def test_jup_search_fields_in_prompt(self):
        prompt = self._load_system_prompt()
        for field in ["mcap", "fdv", "holderCount", "organicScore", "organicScoreLabel",
                      "mintAuthorityDisabled", "freezeAuthorityDisabled", "topHoldersPercentage",
                      "devBalancePercentage", "devMints", "isVerified", "launchpad",
                      "buyOrganicVolume", "numOrganicBuyers", "numNetBuyers", "isSus",
                      "circSupply", "totalSupply", "firstPool", "graduatedPool"]:
            assert field in prompt, f"System prompt missing jup_search field: {field}"

    def test_jup_tokens_tag_apy_in_prompt(self):
        prompt = self._load_system_prompt()
        assert "apy" in prompt, "System prompt must mention apy field (returned for LST tokens)"

    def test_jup_verify_eligibility_fields_in_prompt(self):
        prompt = self._load_system_prompt()
        for field in ["tokenExists", "isVerified", "canVerify", "canMetadata",
                      "verificationError", "metadataError"]:
            assert field in prompt, f"System prompt missing jup_verify_eligibility field: {field}"

    def test_jup_recent_firstpool_in_prompt(self):
        prompt = self._load_system_prompt()
        assert "firstPool" in prompt, "System prompt must mention firstPool for jup_recent"

    def test_trending_all_categories_in_prompt(self):
        prompt = self._load_system_prompt()
        for cat in ["toptrending", "toptraded", "toporganicscore"]:
            assert cat in prompt, f"System prompt missing trending category: {cat}"

    def test_trending_all_intervals_in_prompt(self):
        prompt = self._load_system_prompt()
        for interval in ["5m", "1h", "6h", "24h"]:
            assert interval in prompt, f"System prompt missing trending interval: {interval}"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Live API tests (requires internet, skipped by default)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.live
class TestLiveAPI:
    """Real API calls — run with: pytest -m live
    Jupiter rate limit: ~5 req/sec without API key. Tests include delays."""

    @pytest.fixture(autouse=True)
    async def rate_limit_delay(self):
        import asyncio
        yield
        await asyncio.sleep(1.2)  # 1.2s between each test to stay under rate limit

    @pytest.mark.asyncio
    async def test_live_jup_prices_sol(self):
        result = await jup_prices(SOL)
        data = result.get("data") or result
        assert SOL in data or any(True for _ in data.values())
        token_data = list(data.values())[0]
        assert "usdPrice" in token_data
        assert token_data["usdPrice"] > 0

    @pytest.mark.asyncio
    async def test_live_jup_prices_batch(self):
        result = await jup_prices(f"{SOL},{BONK},{JUP},{WIF},{USDC}")
        data = result.get("data") or result
        assert len(data) >= 2

    @pytest.mark.asyncio
    async def test_live_jup_search_bonk(self):
        result = await jup_search("BONK")
        assert isinstance(result, list)
        assert len(result) > 0
        token = result[0]
        assert token["symbol"] == "BONK"
        assert token["isVerified"] is True
        assert token["audit"]["mintAuthorityDisabled"] is True
        assert token["organicScoreLabel"] == "high"

    @pytest.mark.asyncio
    async def test_live_jup_search_by_mint(self):
        result = await jup_search(BONK)
        assert isinstance(result, list)
        assert result[0]["id"] == BONK

    @pytest.mark.asyncio
    async def test_live_jup_search_multi_mint(self):
        result = await jup_search(f"{SOL},{BONK}")
        assert isinstance(result, list)
        assert len(result) >= 2

    @pytest.mark.asyncio
    async def test_live_jup_recent_returns_30(self):
        result = await jup_recent()
        assert isinstance(result, list)
        assert len(result) == 30
        for token in result:
            assert "firstPool" in token
            assert "organicScore" in token

    @pytest.mark.asyncio
    async def test_live_jup_trending_toptrending_1h(self):
        result = await jup_trending(category="toptrending", interval="1h", limit=10)
        assert isinstance(result, list)
        assert len(result) > 0
        token = result[0]
        assert "organicScore" in token
        assert "stats1h" in token

    @pytest.mark.asyncio
    async def test_live_jup_trending_toptraded_24h(self):
        result = await jup_trending(category="toptraded", interval="24h", limit=20)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "stats24h" in result[0]

    @pytest.mark.asyncio
    async def test_live_jup_trending_toporganicscore_6h(self):
        result = await jup_trending(category="toporganicscore", interval="6h", limit=5)
        assert isinstance(result, list)
        for token in result:
            assert token.get("organicScoreLabel") in ("high", "medium", "low")

    @pytest.mark.asyncio
    async def test_live_jup_trending_5m(self):
        result = await jup_trending(category="toptrending", interval="5m", limit=10)
        assert isinstance(result, list)
        for token in result:
            assert "stats5m" in token

    @pytest.mark.asyncio
    async def test_live_jup_tokens_tag_lst(self):
        result = await jup_tokens_tag(query="lst", limit=20)
        assert isinstance(result, list)
        assert len(result) > 0
        symbols = [t["symbol"] for t in result]
        # Must include at least some known LSTs
        assert any(s in symbols for s in ["JitoSOL", "mSOL", "bSOL", "JupSOL", "stSOL"])

    @pytest.mark.asyncio
    async def test_live_jup_tokens_tag_verified(self):
        import asyncio
        await asyncio.sleep(2)  # avoid rate limit after lst call
        result = await jup_tokens_tag(query="verified", limit=10)
        assert isinstance(result, list)
        assert len(result) > 0
        for token in result:
            assert token.get("isVerified") is True

    @pytest.mark.asyncio
    async def test_live_jup_prices_returns_liquidity(self):
        result = await jup_prices(f"{SOL},{BONK}")
        data = result.get("data") or result
        for mint_data in data.values():
            assert "liquidity" in mint_data, "Price v3 must return liquidity field"
            assert "usdPrice" in mint_data
            assert "priceChange24h" in mint_data
            assert "decimals" in mint_data


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: LLM query simulation (prompts that should trigger specific tools)
# ═══════════════════════════════════════════════════════════════════════════════

LLM_QUERY_CASES = [
    # (user_query, expected_tools, description)
    ("SOL fiyatı nedir?",                               ["jup_prices"],                     "price query → jup_prices"),
    ("SOL, BONK ve JUP fiyatlarını karşılaştır",        ["jup_prices"],                     "batch price → jup_prices"),
    ("BONK token analizi yap",                          ["jup_search"],                     "token analysis → jup_search"),
    ("BONK güvenli mi?",                                ["jup_search"],                     "safety check → jup_search + token_security"),
    ("Jupiter'de trend olan tokenlar",                  ["jup_trending"],                   "trending → jup_trending"),
    ("Son 5 dakikada en çok işlem gören tokenlar",      ["jup_trending"],                   "5m traded → jup_trending(toptraded,5m)"),
    ("En organik büyüyen tokenlar",                     ["jup_trending"],                   "organic → jup_trending(toporganicscore)"),
    ("24 saatlik trending tokenlar",                    ["jup_trending"],                   "24h trending → jup_trending(interval=24h)"),
    ("Yeni çıkmış tokenlar",                            ["jup_recent"],                     "new tokens → jup_recent"),
    ("Son pump.fun launche'ları",                       ["jup_recent"],                     "pump launches → jup_recent"),
    ("Jupiter'deki liquid staking tokenları",           ["jup_tokens_tag"],                 "LST → jup_tokens_tag(lst)"),
    ("Doğrulanmış tokenlar listesi",                    ["jup_tokens_tag"],                 "verified → jup_tokens_tag(verified)"),
    ("Bu token Jupiter'de verify edilebilir mi?",       ["jup_verify_eligibility"],         "eligibility → jup_verify_eligibility"),
    ("Bu token zaten verified mı?",                     ["jup_search", "jup_verify_eligibility"], "verified check → search first"),
]


class TestLLMQueryMapping:
    """Verify that query descriptions are in the tool schemas (not actual LLM calls)."""

    def test_trending_tool_describes_all_categories(self):
        s = get_schema("jup_trending")
        desc = s["description"]
        assert "toptrending" in desc
        assert "toptraded" in desc
        assert "toporganicscore" in desc

    def test_trending_tool_describes_all_intervals(self):
        s = get_schema("jup_trending")
        props = s["input_schema"]["properties"]
        interval_desc = props["interval"]["description"]
        for interval in ["5m", "1h", "6h", "24h"]:
            assert interval in interval_desc

    def test_recent_tool_describes_launchpads(self):
        s = get_schema("jup_recent")
        desc = s["description"]
        assert "pump.fun" in desc

    def test_tokens_tag_tool_describes_lst_context(self):
        s = get_schema("jup_tokens_tag")
        desc = s["description"]
        assert "lst" in desc
        assert "liquid staking" in desc.lower()

    def test_tokens_tag_tool_describes_verified_context(self):
        s = get_schema("jup_tokens_tag")
        desc = s["description"]
        assert "verified" in desc

    def test_verify_eligibility_describes_use_cases(self):
        s = get_schema("jup_verify_eligibility")
        desc = s["description"]
        assert "canVerify" in desc
        assert "isVerified" in desc

    def test_search_tool_mentions_batch_lookup(self):
        s = get_schema("jup_search")
        desc = s["description"]
        assert "100" in desc  # max 100 mints

    def test_prices_tool_mentions_max_50(self):
        s = get_schema("jup_prices")
        desc = s["description"]
        assert "50" in desc

    @pytest.mark.parametrize("query,expected_tools,description", LLM_QUERY_CASES)
    def test_query_case_documented(self, query, expected_tools, description):
        """Ensure each query case has the expected tools available in TOOL_SCHEMAS."""
        available = {t["name"] for t in TOOL_SCHEMAS}
        for tool in expected_tools:
            assert tool in available, \
                f"Query '{query}' needs tool '{tool}' but it's not in TOOL_SCHEMAS"
