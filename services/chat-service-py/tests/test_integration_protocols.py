"""
Integration tests -- real HTTP call protocol validation.

Each test sends a POST /actions/build request to the Rust solana-service (localhost:3030).
If the service is not running, all tests are skipped.

Validation strategy:
  - 200 -> preview + (transaction or execution_steps) present?
  - 4xx/5xx -> response must NOT contain "Unsupported action type" (action type must be registered)
  - If "Unsupported action type" is seen -> test FAIL -> missing case in builder.rs
"""

from __future__ import annotations

import os
import pytest
import httpx

# ─── Config ────────────────────────────────────────────────────────────────

SOLANA_SERVICE_URL = os.getenv("SOLANA_SERVICE_URL", "http://localhost:3030")
INTERNAL_API_KEY   = os.getenv("OPRAI_INTERNAL_API_KEY", "")

# Test wallet -- real mainnet address, sufficient for unsigned TX building
TEST_WALLET = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
RECIPIENT   = "4Nd1mBQtrMJVYVfKf2PX59E3AKuQYXxCNuAPoFnLBWaj"

# Token mint addresses
SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
JUPSOL_MINT  = "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v"

# Known mainnet pool/market addresses
ORCA_SOL_USDC_POOL  = "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"
METEORA_SOL_USDC    = "HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1"
RAYDIUM_USDC_SOL    = "6UmmUiYoBjSrhakAobJw8BYUs93Q5YL7kvRkzoiHFTWu"
KAMINO_MAIN_MARKET  = "7u3HeL2XNPhEeWrMnLKGSXqosBo6bQHGHenHujKpNzff"
KAMINO_USDC_RESERVE = "d4A2prbA2whesmvHaL88BH6Ewn5N4bJ8LcaKDqnXVHm"
DRIFT_SOL_MARKET    = "SOL-PERP"
DUMMY_NFT_MINT      = "4mKSoDDqApmF1DqXvVTSL6tu2zixrSSNjqMxUnwvVzy2"
DUMMY_POSITION      = "3VEnzMZKe9HdPYpp7gPnY9cXjjEpHjj6iMhbkM8VZBWV"
DUMMY_POOL          = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3dMJqbv4s"
DUMMY_STREAM_ID     = "8HoXuFsYDLtGP3dDV4sNGp5EqBVDZ2Hnow3EaReqXbXg"
DUMMY_VAULT         = "Gg7aNqr5WCTfVKMJaUNBdFxBtJJSsZ7MtUjFwJhHp6as"
DUMMY_STRATEGY      = "7a8Kk9eLaAp5WH7UjJJFVk1mBmoPPk3BqmsFvwrJnJK4"

# ─── Helpers ───────────────────────────────────────────────────────────────

def headers() -> dict:
    return {
        "X-Internal-Api-Key": INTERNAL_API_KEY,
        "X-User-Wallet": TEST_WALLET,
        "Content-Type": "application/json",
    }

def build_payload(action_type: str, params: dict) -> dict:
    """All param values stringified — matches _build_action behaviour in defi_plugins.py"""
    return {
        "type": action_type,
        "params": {k: str(v) for k, v in params.items() if v is not None},
    }

# ─── Service availability fixture ──────────────────────────────────────────

@pytest.fixture(scope="session")
def service_url():
    """Return service URL or skip all tests if service is not reachable."""
    try:
        resp = httpx.get(f"{SOLANA_SERVICE_URL}/health", timeout=3.0)
        if resp.status_code not in (200, 204):
            pytest.skip(f"solana-service health check failed: {resp.status_code}")
    except Exception as exc:
        pytest.skip(f"solana-service unreachable ({SOLANA_SERVICE_URL}): {exc}")
    return SOLANA_SERVICE_URL


@pytest.fixture(scope="session")
def client(service_url):
    with httpx.Client(base_url=service_url, timeout=20.0) as c:
        yield c


# ─── Core assertion helpers ─────────────────────────────────────────────────

def assert_action_recognized(resp: httpx.Response, action_type: str) -> dict | None:
    """
    Core assertion: action type must be registered in builder.rs.
    - "Unsupported action type" must NEVER appear
    - 200: verifies preview structure, preview.type matches, transaction/steps present
    Returns parsed JSON if 200, else None.
    """
    body = resp.text
    assert "Unsupported action type" not in body, (
        f"[{action_type}] missing case in builder.rs!\n"
        f"HTTP {resp.status_code}: {body[:400]}"
    )
    if resp.status_code == 200:
        data = resp.json()
        assert "preview" in data, f"[{action_type}] returned 200 but 'preview' missing: {data}"
        preview = data["preview"]
        assert preview.get("description"), \
            f"[{action_type}] preview.description is empty or missing: {preview}"
        assert "type" in preview, f"[{action_type}] preview.type missing: {preview}"
        # Either transaction or execution_steps must be present
        has_tx    = "transaction" in data and data["transaction"]
        has_steps = "executionSteps" in data and data["executionSteps"]
        has_quote = "quote" in data and data["quote"]
        assert has_tx or has_steps or has_quote, (
            f"[{action_type}] 200 returned but transaction/executionSteps/quote missing: {list(data.keys())}"
        )
        if has_tx:
            import base64
            tx_b64 = data["transaction"]
            assert len(tx_b64) > 50, f"[{action_type}] transaction string too short: {tx_b64!r}"
            # Must be valid base64
            try:
                decoded = base64.b64decode(tx_b64)
                assert len(decoded) > 0, f"[{action_type}] transaction decodes to empty bytes"
            except Exception as e:
                raise AssertionError(f"[{action_type}] transaction is not valid base64: {e}")
        return data
    return None


def assert_build_ok(resp: httpx.Response, action_type: str) -> dict:
    """Strict: must return HTTP 200 with valid preview and transaction/steps."""
    assert resp.status_code == 200, (
        f"[{action_type}] expected 200, got {resp.status_code}: {resp.text[:400]}"
    )
    data = assert_action_recognized(resp, action_type)
    assert data is not None
    return data


def assert_invalid_params(resp: httpx.Response, action_type: str,
                           expected_in_error: str | None = None):
    """
    Only 400 (InvalidParams) is accepted for invalid params.
    500 = server error = test failure (a bug that needs fixing).
    Optional: expected_in_error checks that the error message contains specific text.
    """
    body = resp.text
    assert "Unsupported action type" not in body, (
        f"[{action_type}] action type not registered (builder.rs missing): {body[:200]}"
    )
    assert resp.status_code == 400, (
        f"[{action_type}] invalid params → expected 400, got {resp.status_code}.\n"
        f"500 = server bug; 200 = missing validation.\nBody: {body[:400]}"
    )
    # Error JSON: {"error": "Invalid parameters: ..."}
    try:
        data = resp.json()
        assert "error" in data, \
            f"[{action_type}] 400 returned but no 'error' field in JSON: {data}"
        assert "Invalid parameters" in data["error"] or len(data["error"]) > 0, \
            f"[{action_type}] empty error message: {data}"
        if expected_in_error:
            assert expected_in_error.lower() in data["error"].lower(), (
                f"[{action_type}] error message should contain '{expected_in_error}' expected: {data['error']}"
            )
    except (ValueError, KeyError):
        pass  # Non-JSON 400 is also acceptable


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN UTILITY
# ══════════════════════════════════════════════════════════════════════════════

class TestTransferIntegration:
    def test_transfer_sol_builds(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "0.001", "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "transfer")
        if data:
            assert data["transaction"], "expected base64 transaction for SOL transfer"
            assert "preview" in data
            assert data["preview"]["type"] == "transfer"

    def test_transfer_usdc_builds(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1", "token": "USDC"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "transfer")
        if data:
            assert data["preview"]["type"] == "transfer"

    def test_transfer_using_mint_address(self, client):
        """Token can be specified as mint address instead of symbol."""
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1", "token": USDC_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "transfer")

    def test_transfer_missing_to_is_rejected(self, client):
        payload = build_payload("transfer", {"amount": "1", "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "transfer")

    def test_transfer_missing_amount_is_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "transfer")

    def test_transfer_missing_token_is_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "transfer")

    def test_transfer_zero_amount_is_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "0", "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "transfer")

    def test_transfer_negative_amount_is_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "-1", "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "transfer")


class TestBurnIntegration:
    def test_burn_builds(self, client):
        payload = build_payload("burn", {"mint": USDC_MINT, "amount": "1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "burn")
        if data:
            assert data["preview"]["type"] == "burn"

    def test_burn_all_builds(self, client):
        payload = build_payload("burn", {"mint": USDC_MINT, "amount": "all"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "burn")

    def test_burn_missing_mint_rejected(self, client):
        payload = build_payload("burn", {"amount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "burn")

    def test_burn_missing_amount_rejected(self, client):
        payload = build_payload("burn", {"mint": USDC_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "burn")

    def test_burn_zero_amount_rejected(self, client):
        payload = build_payload("burn", {"mint": USDC_MINT, "amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "burn")


# ══════════════════════════════════════════════════════════════════════════════
# SWAP
# ══════════════════════════════════════════════════════════════════════════════

class TestSwapIntegration:
    def test_swap_sol_to_usdc(self, client):
        payload = build_payload("swap", {
            "inputMint": SOL_MINT,
            "outputMint": USDC_MINT,
            "amount": "0.01",
            "slippageBps": "50",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "swap")
        if data:
            assert data["preview"]["type"] == "swap"

    def test_swap_usdc_to_sol(self, client):
        payload = build_payload("swap", {
            "inputMint": USDC_MINT,
            "outputMint": SOL_MINT,
            "amount": "1",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "swap")

    def test_swap_usdc_to_usdt(self, client):
        payload = build_payload("swap", {
            "inputMint": USDC_MINT,
            "outputMint": USDT_MINT,
            "amount": "5",
            "slippageBps": "30",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "swap")

    def test_swap_same_mint_rejected(self, client):
        """Swapping a token to itself must be rejected."""
        payload = build_payload("swap", {
            "inputMint": SOL_MINT, "outputMint": SOL_MINT, "amount": "1"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_missing_input_mint_rejected(self, client):
        payload = build_payload("swap", {"outputMint": USDC_MINT, "amount": "1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_missing_output_mint_rejected(self, client):
        payload = build_payload("swap", {"inputMint": SOL_MINT, "amount": "1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_missing_amount_rejected(self, client):
        payload = build_payload("swap", {"inputMint": SOL_MINT, "outputMint": USDC_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_missing_mints_rejected(self, client):
        payload = build_payload("swap", {"amount": "1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_zero_amount_rejected(self, client):
        payload = build_payload("swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")

    def test_swap_negative_amount_rejected(self, client):
        payload = build_payload("swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "-1"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "swap")


# ══════════════════════════════════════════════════════════════════════════════
# JUPITER DCA + LIMIT ORDERS
# ══════════════════════════════════════════════════════════════════════════════

class TestJupiterDCAIntegration:
    def test_dca_builds(self, client):
        payload = build_payload("dca", {
            "inputMint": USDC_MINT,
            "outputMint": SOL_MINT,
            "totalAmount": "100",
            "numberOfOrders": "10",
            "intervalSeconds": "86400",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "dca")
        if data:
            assert data["preview"]["type"] == "dca"

    def test_dca_missing_total_amount_rejected(self, client):
        payload = build_payload("dca", {
            "inputMint": USDC_MINT, "outputMint": SOL_MINT,
            "numberOfOrders": "10", "intervalSeconds": "86400",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "dca")

    def test_dca_missing_number_of_orders_rejected(self, client):
        payload = build_payload("dca", {
            "inputMint": USDC_MINT, "outputMint": SOL_MINT,
            "totalAmount": "100", "intervalSeconds": "86400",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "dca")

    def test_dca_zero_orders_rejected(self, client):
        payload = build_payload("dca", {
            "inputMint": USDC_MINT, "outputMint": SOL_MINT,
            "totalAmount": "100", "numberOfOrders": "0", "intervalSeconds": "86400",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "dca")

    def test_dca_missing_params_rejected(self, client):
        payload = build_payload("dca", {"inputMint": USDC_MINT, "outputMint": SOL_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "dca")

    def test_cancel_dca_builds(self, client):
        payload = build_payload("cancel_dca", {"order": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "cancel_dca")

    def test_cancel_dca_no_order_still_recognized(self, client):
        """cancel_dca without order ID should be recognized (cancels all or returns error)."""
        payload = build_payload("cancel_dca", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert "Unsupported action type" not in resp.text


class TestJupiterLimitOrderIntegration:
    def test_limit_order_builds(self, client):
        payload = build_payload("limit_order", {
            "inputMint": SOL_MINT,
            "outputMint": USDC_MINT,
            "amount": "0.1",
            "targetPrice": "200",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "limit_order")
        if data:
            assert data["preview"]["type"] == "limit_order"

    def test_limit_order_missing_amount_rejected(self, client):
        payload = build_payload("limit_order", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "targetPrice": "200"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "limit_order")

    def test_limit_order_missing_target_price_rejected(self, client):
        payload = build_payload("limit_order", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0.1"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "limit_order")

    def test_limit_order_missing_params_rejected(self, client):
        payload = build_payload("limit_order", {"inputMint": SOL_MINT, "outputMint": USDC_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "limit_order")

    def test_limit_order_zero_amount_rejected(self, client):
        payload = build_payload("limit_order", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT,
            "amount": "0", "targetPrice": "200",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "limit_order")

    def test_cancel_limit_order_no_params(self, client):
        payload = build_payload("cancel_limit_order", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "cancel_limit_order")

    def test_cancel_limit_order_with_order(self, client):
        payload = build_payload("cancel_limit_order", {"order": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "cancel_limit_order")


# ══════════════════════════════════════════════════════════════════════════════
# JUPSOL
# ══════════════════════════════════════════════════════════════════════════════

class TestJupSolIntegration:
    def test_jupsol_stake_builds(self, client):
        payload = build_payload("jupsol_stake", {"amount": "0.01", "slippageBps": "50"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "jupsol_stake")

    def test_jupsol_stake_zero_rejected(self, client):
        payload = build_payload("jupsol_stake", {"amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jupsol_stake")

    def test_jupsol_unstake_builds(self, client):
        payload = build_payload("jupsol_unstake", {"amount": "0.01", "slippageBps": "50"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "jupsol_unstake")


# ══════════════════════════════════════════════════════════════════════════════
# JUPITER LEND (Earn / Borrow)
# ══════════════════════════════════════════════════════════════════════════════

class TestJupiterLendIntegration:
    def test_lend_deposit_usdc(self, client):
        payload = build_payload("lend", {"token": "USDC", "amount": "10", "operation": "deposit"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "lend")

    def test_lend_withdraw(self, client):
        payload = build_payload("withdraw_lend", {"token": "USDC", "amount": "10"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "withdraw_lend")

    def test_borrow(self, client):
        payload = build_payload("borrow", {"token": "USDC", "amount": "5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "borrow")

    def test_repay(self, client):
        payload = build_payload("repay", {"token": "USDC", "amount": "5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "repay")

    def test_lend_missing_token_rejected(self, client):
        payload = build_payload("lend", {"amount": "10"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "lend")


# ══════════════════════════════════════════════════════════════════════════════
# JUPITER PERP
# ══════════════════════════════════════════════════════════════════════════════

class TestJupiterPerpIntegration:
    def test_perp_open_long_sol(self, client):
        payload = build_payload("perp_open", {
            "market": "SOL",
            "side": "long",
            "collateralAmount": "100",
            "leverage": "3",
            "operation": "open",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "perp_open")

    def test_perp_open_short_eth(self, client):
        payload = build_payload("perp_open", {
            "market": "wETH",
            "side": "short",
            "collateralAmount": "50",
            "operation": "open",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "perp_open")

    def test_perp_close(self, client):
        payload = build_payload("perp_close", {
            "market": "SOL",
            "side": "long",
            "collateralAmount": "100",
            "operation": "close",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "perp_close")

    def test_perp_missing_market_rejected(self, client):
        payload = build_payload("perp_open", {"side": "long", "collateralAmount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "perp_open")

    def test_jlp_add(self, client):
        payload = build_payload("jlp_add", {"amount": "100", "operation": "add"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "jlp_add")

    def test_jlp_remove(self, client):
        payload = build_payload("jlp_remove", {"amount": "50", "operation": "remove"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "jlp_remove")

    def test_jlp_add_zero_rejected(self, client):
        payload = build_payload("jlp_add", {"amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jlp_add")


# ══════════════════════════════════════════════════════════════════════════════
# PUMP.FUN
# ══════════════════════════════════════════════════════════════════════════════

class TestPumpFunIntegration:
    def test_launch_token_builds(self, client):
        payload = build_payload("launch_token", {
            "name": "TestToken",
            "symbol": "TEST",
            "description": "A test token for integration testing",
            "imageUrl": "https://arweave.net/test",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "launch_token")

    def test_launch_token_missing_fields_rejected(self, client):
        payload = build_payload("launch_token", {"name": "TestToken"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "launch_token")

    def test_pumpfun_buy_builds(self, client):
        payload = build_payload("pumpfun_buy", {
            "mint": DUMMY_NFT_MINT,
            "solAmount": "0.001",
            "slippageBps": "1000",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "pumpfun_buy")

    def test_pumpfun_sell_builds(self, client):
        payload = build_payload("pumpfun_sell", {
            "mint": DUMMY_NFT_MINT,
            "tokenAmount": "1000000",
            "slippageBps": "1000",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "pumpfun_sell")


# ══════════════════════════════════════════════════════════════════════════════
# MARINADE
# ══════════════════════════════════════════════════════════════════════════════

class TestMarinadeIntegration:
    def test_marinade_stake_builds(self, client):
        payload = build_payload("marinade_stake", {"amount": "0.01"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "marinade_stake")
        if data:
            assert data["preview"]["type"] == "marinade_stake"

    def test_marinade_stake_missing_amount_rejected(self, client):
        payload = build_payload("marinade_stake", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_stake")

    def test_marinade_stake_zero_rejected(self, client):
        payload = build_payload("marinade_stake", {"amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_stake")

    def test_marinade_stake_negative_rejected(self, client):
        payload = build_payload("marinade_stake", {"amount": "-1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_stake")

    def test_marinade_unstake_builds(self, client):
        payload = build_payload("marinade_unstake", {"amount": "0.01"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "marinade_unstake")
        if data:
            assert data["preview"]["type"] == "marinade_unstake"

    def test_marinade_unstake_missing_amount_rejected(self, client):
        payload = build_payload("marinade_unstake", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_unstake")

    def test_marinade_unstake_zero_rejected(self, client):
        payload = build_payload("marinade_unstake", {"amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_unstake")

    def test_marinade_delayed_unstake_builds(self, client):
        payload = build_payload("marinade_delayed_unstake", {"amount": "0.1"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "marinade_delayed_unstake")
        if data:
            assert data["preview"]["type"] == "marinade_delayed_unstake"

    def test_marinade_delayed_unstake_missing_amount_rejected(self, client):
        payload = build_payload("marinade_delayed_unstake", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marinade_delayed_unstake")


# ══════════════════════════════════════════════════════════════════════════════
# JITO
# ══════════════════════════════════════════════════════════════════════════════

class TestJitoIntegration:
    def test_jito_stake_builds(self, client):
        payload = build_payload("jito_stake", {"amount": "0.01"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "jito_stake")
        if data:
            assert data["preview"]["type"] == "jito_stake"

    def test_jito_stake_missing_amount_rejected(self, client):
        payload = build_payload("jito_stake", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jito_stake")

    def test_jito_stake_zero_rejected(self, client):
        payload = build_payload("jito_stake", {"amount": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jito_stake")

    def test_jito_stake_negative_rejected(self, client):
        payload = build_payload("jito_stake", {"amount": "-0.5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jito_stake")

    def test_jito_unstake_builds(self, client):
        payload = build_payload("jito_unstake", {"amount": "0.01"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "jito_unstake")

    def test_jito_tip_builds(self, client):
        payload = build_payload("jito_tip", {"tipLamports": "10000"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "jito_tip")
        if data:
            assert data["preview"]["type"] == "jito_tip"

    def test_jito_tip_missing_lamports_rejected(self, client):
        payload = build_payload("jito_tip", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jito_tip")

    def test_jito_tip_zero_rejected(self, client):
        payload = build_payload("jito_tip", {"tipLamports": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "jito_tip")


# ══════════════════════════════════════════════════════════════════════════════
# MARGINFI
# ══════════════════════════════════════════════════════════════════════════════

class TestMarginfiIntegration:
    ACCOUNT = "4Nd1mBQtrMJVYVfKf2PX59E3AKuQYXxCNuAPoFnLBWaj"
    BANK_USDC = "2s37akK2eyBbp8DZgCm7RtsaEz8eJP3Nxd4urLHQv7yB"

    def test_create_account_builds(self, client):
        payload = build_payload("marginfi_create_account", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "marginfi_create_account")
        if data:
            assert data["preview"]["type"] == "marginfi_create_account"

    def test_deposit_builds(self, client):
        payload = build_payload("marginfi_deposit", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "10"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "marginfi_deposit")
        if data:
            assert data["preview"]["type"] == "marginfi_deposit"

    def test_deposit_missing_bank_rejected(self, client):
        payload = build_payload("marginfi_deposit", {"account": self.ACCOUNT, "amount": "10"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marginfi_deposit")

    def test_deposit_missing_amount_rejected(self, client):
        payload = build_payload("marginfi_deposit", {"account": self.ACCOUNT, "bank": self.BANK_USDC})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marginfi_deposit")

    def test_deposit_zero_amount_rejected(self, client):
        payload = build_payload("marginfi_deposit", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "0"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marginfi_deposit")

    def test_withdraw_builds(self, client):
        payload = build_payload("marginfi_withdraw", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_withdraw")

    def test_withdraw_missing_account_rejected(self, client):
        payload = build_payload("marginfi_withdraw", {"bank": self.BANK_USDC, "amount": "5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marginfi_withdraw")

    def test_borrow_builds(self, client):
        payload = build_payload("marginfi_borrow", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_borrow")

    def test_repay_builds(self, client):
        payload = build_payload("marginfi_repay", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_repay")

    def test_deposit_collateral_builds(self, client):
        payload = build_payload("marginfi_deposit_collateral", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_deposit_collateral")

    def test_withdraw_collateral_builds(self, client):
        payload = build_payload("marginfi_withdraw_collateral", {
            "account": self.ACCOUNT, "bank": self.BANK_USDC, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_withdraw_collateral")

    def test_close_account_builds(self, client):
        payload = build_payload("marginfi_close_account", {"account": self.ACCOUNT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "marginfi_close_account")

    def test_close_account_missing_account_rejected(self, client):
        payload = build_payload("marginfi_close_account", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "marginfi_close_account")


# ══════════════════════════════════════════════════════════════════════════════
# ORCA WHIRLPOOLS
# ══════════════════════════════════════════════════════════════════════════════

class TestOrcaIntegration:
    def test_orca_swap_builds(self, client):
        payload = build_payload("orca_swap", {
            "whirlpoolAddress": ORCA_SOL_USDC_POOL,
            "inputMint": SOL_MINT,
            "outputMint": USDC_MINT,
            "amount": "0.01",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_swap")

    def test_orca_swap_missing_whirlpool_rejected(self, client):
        payload = build_payload("orca_swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0.01"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "orca_swap")

    def test_orca_add_liquidity_builds(self, client):
        payload = build_payload("orca_add_liquidity", {
            "whirlpoolAddress": ORCA_SOL_USDC_POOL,
            "tokenAAmount": "0.01",
            "tokenBAmount": "1",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_add_liquidity")

    def test_orca_open_position_builds(self, client):
        payload = build_payload("orca_open_position", {
            "whirlpoolAddress": ORCA_SOL_USDC_POOL,
            "tickLower": "-10",
            "tickUpper": "10",
            "tokenAAmount": "0.01",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_open_position")

    def test_orca_close_position_builds(self, client):
        payload = build_payload("orca_close_position", {
            "positionMint": DUMMY_POSITION, "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_close_position")

    def test_orca_increase_position_builds(self, client):
        payload = build_payload("orca_increase_position", {
            "positionMint": DUMMY_POSITION,
            "tokenAAmount": "0.01",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_increase_position")

    def test_orca_decrease_position_builds(self, client):
        payload = build_payload("orca_decrease_position", {
            "positionMint": DUMMY_POSITION, "liquidityAmount": "1000", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_decrease_position")

    def test_orca_collect_fees_builds(self, client):
        payload = build_payload("orca_collect_fees", {"positionMint": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_collect_fees")

    def test_orca_collect_rewards_builds(self, client):
        payload = build_payload("orca_collect_rewards", {"positionMint": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_collect_rewards")

    def test_orca_remove_liquidity_builds(self, client):
        payload = build_payload("orca_remove_liquidity", {
            "positionMint": DUMMY_POSITION, "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "orca_remove_liquidity")


# ══════════════════════════════════════════════════════════════════════════════
# KAMINO
# ══════════════════════════════════════════════════════════════════════════════

class TestKaminoIntegration:
    def test_deposit_builds(self, client):
        payload = build_payload("kamino_deposit", {
            "market": KAMINO_MAIN_MARKET, "reserve": KAMINO_USDC_RESERVE, "amount": "10"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "kamino_deposit")
        if data:
            assert data["preview"]["type"] == "kamino_deposit"

    def test_deposit_missing_market_rejected(self, client):
        payload = build_payload("kamino_deposit", {
            "reserve": KAMINO_USDC_RESERVE, "amount": "10"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_deposit")

    def test_deposit_missing_reserve_rejected(self, client):
        payload = build_payload("kamino_deposit", {
            "market": KAMINO_MAIN_MARKET, "amount": "10"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_deposit")

    def test_deposit_zero_rejected(self, client):
        payload = build_payload("kamino_deposit", {
            "market": KAMINO_MAIN_MARKET, "reserve": KAMINO_USDC_RESERVE, "amount": "0"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_deposit")

    def test_borrow_builds(self, client):
        payload = build_payload("kamino_borrow", {
            "market": KAMINO_MAIN_MARKET, "reserve": KAMINO_USDC_RESERVE, "amount": "5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_borrow")

    def test_borrow_missing_params_rejected(self, client):
        payload = build_payload("kamino_borrow", {"amount": "5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_borrow")

    def test_multiply_open_builds(self, client):
        payload = build_payload("kamino_multiply_open", {
            "strategy": DUMMY_STRATEGY, "amount": "0.1", "leverage": "2.0", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_multiply_open")

    def test_multiply_open_bad_leverage_rejected(self, client):
        """Leverage < 1 makes no economic sense and must be rejected."""
        payload = build_payload("kamino_multiply_open", {
            "strategy": DUMMY_STRATEGY, "amount": "0.1", "leverage": "0.5"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_multiply_open")

    def test_multiply_open_missing_strategy_rejected(self, client):
        payload = build_payload("kamino_multiply_open", {"amount": "0.1", "leverage": "2.0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "kamino_multiply_open")

    def test_multiply_add_builds(self, client):
        payload = build_payload("kamino_multiply_add", {
            "position": DUMMY_POSITION, "amount": "0.05", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_multiply_add")

    def test_multiply_withdraw_builds(self, client):
        payload = build_payload("kamino_multiply_withdraw", {
            "position": DUMMY_POSITION, "amount": "0.05", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_multiply_withdraw")

    def test_multiply_close_builds(self, client):
        payload = build_payload("kamino_multiply_close", {
            "position": DUMMY_POSITION, "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_multiply_close")

    def test_long_open_builds(self, client):
        payload = build_payload("kamino_long_open", {
            "collateralToken": "SOL",
            "debtToken": "USDC",
            "amount": "0.1",
            "leverage": "2.0",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_long_open")

    def test_short_open_builds(self, client):
        payload = build_payload("kamino_short_open", {
            "collateralToken": "USDC",
            "debtToken": "SOL",
            "amount": "10",
            "leverage": "2.0",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_short_open")

    def test_position_close_builds(self, client):
        payload = build_payload("kamino_position_close", {
            "position": DUMMY_POSITION, "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_position_close")

    def test_stake_builds(self, client):
        payload = build_payload("kamino_stake", {"amount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_stake")

    def test_unstake_builds(self, client):
        payload = build_payload("kamino_unstake", {"amount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_unstake")

    def test_vault_deposit_builds(self, client):
        payload = build_payload("kamino_vault_deposit", {"vault": DUMMY_VAULT, "amount": "10"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_vault_deposit")

    def test_vault_withdraw_builds(self, client):
        payload = build_payload("kamino_vault_withdraw", {"vault": DUMMY_VAULT, "sharesAmount": "5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "kamino_vault_withdraw")



# ══════════════════════════════════════════════════════════════════════════════
# RAYDIUM
# ══════════════════════════════════════════════════════════════════════════════

class TestRaydiumIntegration:
    def test_swap_builds(self, client):
        payload = build_payload("raydium_swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0.01", "slippageBps": "50"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_swap")

    def test_swap_zero_rejected(self, client):
        payload = build_payload("raydium_swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "raydium_swap")

    def test_add_liquidity_builds(self, client):
        payload = build_payload("raydium_add_liquidity", {
            "poolId": RAYDIUM_USDC_SOL, "amount": "0.01", "baseIn": "true", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_add_liquidity")

    def test_remove_liquidity_builds(self, client):
        payload = build_payload("raydium_remove_liquidity", {
            "poolId": RAYDIUM_USDC_SOL, "lpAmount": "100", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_remove_liquidity")

    def test_create_pool_builds(self, client):
        payload = build_payload("raydium_create_pool", {
            "mintA": SOL_MINT, "mintB": USDC_MINT,
            "amountA": "0.01", "amountB": "2", "startTime": "0"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_create_pool")

    def test_open_position_builds(self, client):
        payload = build_payload("raydium_open_position", {
            "poolId": RAYDIUM_USDC_SOL,
            "inputMint": SOL_MINT,
            "inputAmount": "0.01",
            "tickLower": "-100",
            "tickUpper": "100",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_open_position")

    def test_close_position_builds(self, client):
        payload = build_payload("raydium_close_position", {"positionId": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_close_position")

    def test_increase_position_builds(self, client):
        payload = build_payload("raydium_increase_position", {
            "positionId": DUMMY_POSITION,
            "inputMint": SOL_MINT,
            "inputAmount": "0.01",
            "slippageBps": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_increase_position")

    def test_decrease_position_builds(self, client):
        payload = build_payload("raydium_decrease_position", {
            "positionId": DUMMY_POSITION, "liquidity": "1000000", "slippageBps": "100"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "raydium_decrease_position")


# ══════════════════════════════════════════════════════════════════════════════
# METEORA
# ══════════════════════════════════════════════════════════════════════════════

class TestMeteoraIntegration:
    def test_swap_builds(self, client):
        payload = build_payload("meteora_swap", {
            "inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": "0.01",
            "pool": METEORA_SOL_USDC, "slippageBps": "50",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_swap")

    def test_swap_missing_mints_rejected(self, client):
        payload = build_payload("meteora_swap", {"amount": "0.01"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "meteora_swap")

    def test_open_position_builds(self, client):
        payload = build_payload("meteora_open_position", {
            "pool": METEORA_SOL_USDC, "amountX": "0.01", "amountY": "2",
            "minBinId": "-10", "maxBinId": "10", "slippageBps": "50",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_open_position")

    def test_close_position_builds(self, client):
        payload = build_payload("meteora_close_position", {"position": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_close_position")

    def test_add_to_position_builds(self, client):
        payload = build_payload("meteora_add_to_position", {
            "position": DUMMY_POSITION, "amountX": "0.01", "amountY": "2"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_add_to_position")

    def test_add_liquidity_builds(self, client):
        payload = build_payload("meteora_add_liquidity", {
            "pool": DUMMY_POOL, "amountA": "0.01", "amountB": "2"
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_add_liquidity")

    def test_remove_liquidity_builds(self, client):
        payload = build_payload("meteora_remove_liquidity", {"position": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_remove_liquidity")

    def test_create_pool_builds(self, client):
        payload = build_payload("meteora_create_pool", {
            "tokenXMint": SOL_MINT, "tokenYMint": USDC_MINT,
            "binStep": "25", "initialPrice": "1.5",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_create_pool")

    def test_claim_fees_builds(self, client):
        payload = build_payload("meteora_claim_fees", {"position": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_claim_fees")

    def test_claim_rewards_builds(self, client):
        payload = build_payload("meteora_claim_rewards", {"position": DUMMY_POSITION})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_claim_rewards")

    def test_stake_builds(self, client):
        payload = build_payload("meteora_stake", {"farm": DUMMY_POOL, "amount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_stake")

    def test_unstake_builds(self, client):
        payload = build_payload("meteora_unstake", {"farm": DUMMY_POOL, "amount": "100"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_unstake")

    def test_harvest_builds(self, client):
        payload = build_payload("meteora_harvest", {"farm": DUMMY_POOL})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_harvest")

    # ── DLMM GET Query Actions ────────────────────────────────────────────

    # meteora_dlmm_get_pairs — new /pools API

    def test_dlmm_get_pairs_no_params_builds(self, client):
        """No params → default pool list from /pools"""
        payload = build_payload("meteora_dlmm_get_pairs", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_with_query_builds(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {"query": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_sort_volume_desc(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {
            "sortBy": "volume_24h:desc", "page": "1", "pageSize": "10",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_sort_tvl_asc(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {
            "sortBy": "tvl:asc", "pageSize": "5",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_with_filter(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {"filterBy": "tvl>100000"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_page_size_oversized_clamped(self, client):
        """page_size > 1000 is clamped to 1000, not rejected"""
        payload = build_payload("meteora_dlmm_get_pairs", {"pageSize": "9999"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_second_page(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {"page": "2", "pageSize": "20"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    def test_dlmm_get_pairs_all_params_combined(self, client):
        payload = build_payload("meteora_dlmm_get_pairs", {
            "query": "USDC", "sortBy": "apy:desc",
            "filterBy": "has_farm=true", "page": "1", "pageSize": "25",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pairs")

    # meteora_dlmm_get_pool_groups

    def test_dlmm_get_pool_groups_no_params(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_sort_by_tvl(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {"sortBy": "total_tvl:desc"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_with_7d_volume_window(self, client):
        """volumeTw lets user compare pairs by 7-day volume instead of 24h"""
        payload = build_payload("meteora_dlmm_get_pool_groups", {
            "sortBy": "total_volume:desc", "volumeTw": "volume_7d",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_with_fee_tvl_ratio_tw(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {
            "feeTvlRatioTw": "fee_tvl_ratio_24h", "sortBy": "max_fee_tvl_ratio:desc",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_with_query(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {"query": "SOL"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_pagination(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {"page": "2", "pageSize": "20"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_page_size_over_100_clamped(self, client):
        """max is 100, clamped not rejected"""
        payload = build_payload("meteora_dlmm_get_pool_groups", {"pageSize": "500"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    def test_dlmm_get_pool_groups_all_params(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {
            "query": "USDC", "sortBy": "total_tvl:desc",
            "filterBy": "has_farm=true", "volumeTw": "volume_24h",
            "feeTvlRatioTw": "fee_tvl_ratio_24h",
            "page": "1", "pageSize": "10",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_groups")

    # meteora_dlmm_get_pool_group

    def test_dlmm_get_pool_group_valid_mints(self, client):
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{SOL_MINT}-{USDC_MINT}",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_missing_mints_rejected(self, client):
        """lexicalOrderMints is required — empty payload must be rejected"""
        payload = build_payload("meteora_dlmm_get_pool_group", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_sort_by_apy(self, client):
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{SOL_MINT}-{USDC_MINT}",
            "sortBy": "apy:desc",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_sort_by_volume(self, client):
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{SOL_MINT}-{USDC_MINT}",
            "sortBy": "volume_24h:desc",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_with_filter(self, client):
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{SOL_MINT}-{USDC_MINT}",
            "filterBy": "tvl>10000",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_with_pagination(self, client):
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{SOL_MINT}-{USDC_MINT}",
            "page": "1", "pageSize": "10",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_group")

    def test_dlmm_get_pool_group_unknown_pair_propagates(self, client):
        """Unknown pair key is forwarded to Meteora API; may return 200 empty or 4xx — neither 'Unsupported action type'"""
        payload = build_payload("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{USDT_MINT}-{MSOL_MINT}",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert "Unsupported action type" not in resp.text

    # meteora_dlmm_get_pool_ohlcv

    def test_dlmm_get_pool_ohlcv_default_timeframe(self, client):
        """No timeframe → defaults to 24h inside Meteora API"""
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {"address": METEORA_SOL_USDC})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_all_valid_timeframes(self, client):
        for tf in ("5m", "30m", "1h", "2h", "4h", "12h", "24h"):
            payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
                "address": METEORA_SOL_USDC, "timeframe": tf,
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_action_recognized(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_invalid_timeframe_rejected(self, client):
        for bad_tf in ("3d", "1w", "monthly", "1", ""):
            if not bad_tf:
                continue  # empty string skipped at serialisation level
            payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
                "address": METEORA_SOL_USDC, "timeframe": bad_tf,
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_invalid_params(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_invalid_address_rejected(self, client):
        for bad_addr in ("not-a-pubkey", "123", "0x1234", "AAAA"):
            payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
                "address": bad_addr, "timeframe": "1h",
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_invalid_params(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_missing_address_rejected(self, client):
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {"timeframe": "1h"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_with_24h_time_range(self, client):
        import time
        now = int(time.time())
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": str(now - 86400), "endTime": str(now),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_with_7d_time_range(self, client):
        import time
        now = int(time.time())
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "timeframe": "4h",
            "startTime": str(now - 604800), "endTime": str(now),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_ohlcv")

    def test_dlmm_get_pool_ohlcv_only_start_time(self, client):
        """Only startTime without endTime is allowed (API uses now as default end)"""
        import time
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": str(int(time.time()) - 3600),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_ohlcv")

    # meteora_dlmm_get_pool_volume_history

    def test_dlmm_get_pool_volume_history_default(self, client):
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {"address": METEORA_SOL_USDC})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_all_valid_timeframes(self, client):
        for tf in ("5m", "30m", "1h", "2h", "4h", "12h", "24h"):
            payload = build_payload("meteora_dlmm_get_pool_volume_history", {
                "address": METEORA_SOL_USDC, "timeframe": tf,
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_invalid_timeframe_rejected(self, client):
        for bad_tf in ("1d", "1w", "weekly", "0"):
            payload = build_payload("meteora_dlmm_get_pool_volume_history", {
                "address": METEORA_SOL_USDC, "timeframe": bad_tf,
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_invalid_params(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_invalid_address_rejected(self, client):
        for bad_addr in ("not-a-pubkey", "123abc", "AAAAAAAA"):
            payload = build_payload("meteora_dlmm_get_pool_volume_history", {
                "address": bad_addr, "timeframe": "24h",
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_invalid_params(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_missing_address_rejected(self, client):
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {"timeframe": "24h"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_with_24h_range(self, client):
        import time
        now = int(time.time())
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": str(now - 86400), "endTime": str(now),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_with_7d_range(self, client):
        import time
        now = int(time.time())
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "4h",
            "startTime": str(now - 604800), "endTime": str(now),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_with_30d_range(self, client):
        import time
        now = int(time.time())
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "24h",
            "startTime": str(now - 2592000), "endTime": str(now),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    def test_dlmm_get_pool_volume_history_only_start_time(self, client):
        import time
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": str(int(time.time()) - 7200),
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "meteora_dlmm_get_pool_volume_history")

    # ── Cross-cutting edge cases ──────────────────────────────────────────

    def test_dlmm_query_actions_registered_not_unknown(self, client):
        """Smoke test: all 8 DLMM query action types must be registered in builder.rs"""
        action_types = [
            "meteora_dlmm_get_pairs",
            "meteora_dlmm_get_pair",
            "meteora_dlmm_get_user_positions",
            "meteora_dlmm_get_active_bin",
            "meteora_dlmm_get_pool_groups",
            "meteora_dlmm_get_pool_group",
            "meteora_dlmm_get_pool_ohlcv",
            "meteora_dlmm_get_pool_volume_history",
        ]
        for at in action_types:
            payload = build_payload(at, {})
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert "Unsupported action type" not in resp.text, (
                f"[{at}] is not registered in builder.rs"
            )

    def test_dlmm_ohlcv_and_volume_address_is_same_pool(self, client):
        """OHLCV and volume history for same pool must both work"""
        for action in ("meteora_dlmm_get_pool_ohlcv", "meteora_dlmm_get_pool_volume_history"):
            payload = build_payload(action, {
                "address": METEORA_SOL_USDC, "timeframe": "24h",
            })
            resp = client.post("/actions/build", json=payload, headers=headers())
            assert_action_recognized(resp, action)

    def test_dlmm_get_pairs_response_has_data_field(self, client):
        """GET query actions must return data field (not transaction)"""
        payload = build_payload("meteora_dlmm_get_pairs", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        if resp.status_code == 200:
            body = resp.json()
            assert "data" in body, f"GET query action should return 'data' field: {list(body.keys())}"

    def test_dlmm_get_pool_groups_response_has_data_field(self, client):
        payload = build_payload("meteora_dlmm_get_pool_groups", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        if resp.status_code == 200:
            body = resp.json()
            assert "data" in body, f"GET query action should return 'data' field: {list(body.keys())}"

    def test_dlmm_get_pool_ohlcv_response_has_data_field(self, client):
        payload = build_payload("meteora_dlmm_get_pool_ohlcv", {"address": METEORA_SOL_USDC})
        resp = client.post("/actions/build", json=payload, headers=headers())
        if resp.status_code == 200:
            body = resp.json()
            assert "data" in body, f"GET query action should return 'data' field: {list(body.keys())}"

    def test_dlmm_get_pool_volume_history_response_has_data_field(self, client):
        payload = build_payload("meteora_dlmm_get_pool_volume_history", {"address": METEORA_SOL_USDC})
        resp = client.post("/actions/build", json=payload, headers=headers())
        if resp.status_code == 200:
            body = resp.json()
            assert "data" in body, f"GET query action should return 'data' field: {list(body.keys())}"


# ══════════════════════════════════════════════════════════════════════════════
# MAGIC EDEN
# ══════════════════════════════════════════════════════════════════════════════

class TestMagicEdenIntegration:
    def test_list_builds(self, client):
        payload = build_payload("me_list", {"mintAddress": DUMMY_NFT_MINT, "price": "1.5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_list")

    def test_list_zero_price_rejected(self, client):
        payload = build_payload("me_list", {"mintAddress": DUMMY_NFT_MINT, "price": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "me_list")

    def test_buy_builds(self, client):
        payload = build_payload("me_buy", {"mintAddress": DUMMY_NFT_MINT, "price": "1.5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_buy")

    def test_cancel_listing_builds(self, client):
        payload = build_payload("me_cancel_listing", {"mintAddress": DUMMY_NFT_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_cancel_listing")

    def test_make_offer_builds(self, client):
        payload = build_payload("me_make_offer", {"mintAddress": DUMMY_NFT_MINT, "price": "1.0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_make_offer")

    def test_accept_offer_builds(self, client):
        payload = build_payload("me_accept_offer", {
            "mintAddress": DUMMY_NFT_MINT, "buyer": RECIPIENT
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_accept_offer")

    def test_cancel_offer_builds(self, client):
        payload = build_payload("me_cancel_offer", {"mintAddress": DUMMY_NFT_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "me_cancel_offer")


# ══════════════════════════════════════════════════════════════════════════════
# TENSOR
# ══════════════════════════════════════════════════════════════════════════════

class TestTensorIntegration:
    def test_buy_builds(self, client):
        payload = build_payload("tensor_buy", {"mint": DUMMY_NFT_MINT, "maxPrice": "1.5"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "tensor_buy")

    def test_buy_zero_price_rejected(self, client):
        payload = build_payload("tensor_buy", {"mint": DUMMY_NFT_MINT, "maxPrice": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "tensor_buy")

    def test_list_builds(self, client):
        payload = build_payload("tensor_list", {"mint": DUMMY_NFT_MINT, "price": "2.0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "tensor_list")

    def test_list_zero_price_rejected(self, client):
        payload = build_payload("tensor_list", {"mint": DUMMY_NFT_MINT, "price": "0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "tensor_list")

    def test_cancel_listing_builds(self, client):
        payload = build_payload("tensor_cancel_listing", {"mint": DUMMY_NFT_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "tensor_cancel_listing")

    def test_make_offer_builds(self, client):
        payload = build_payload("tensor_make_offer", {"mint": DUMMY_NFT_MINT, "price": "1.0"})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "tensor_make_offer")

    def test_cancel_offer_builds(self, client):
        payload = build_payload("tensor_cancel_offer", {"mint": DUMMY_NFT_MINT})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "tensor_cancel_offer")


# ══════════════════════════════════════════════════════════════════════════════
# STREAMFLOW
# ══════════════════════════════════════════════════════════════════════════════

class TestStreamflowIntegration:
    def test_create_stream_builds(self, client):
        payload = build_payload("streamflow_create", {
            "recipient": RECIPIENT,
            "mint": USDC_MINT,
            "amount": "1000",
            "period": "86400",
            "amountPerPeriod": "100",
            "name": "Test Vesting",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        data = assert_action_recognized(resp, "streamflow_create")
        if data:
            assert data["preview"]["type"] == "streamflow_create"

    def test_create_stream_missing_recipient_rejected(self, client):
        payload = build_payload("streamflow_create", {
            "mint": USDC_MINT, "amount": "1000",
            "period": "86400", "amountPerPeriod": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_create")

    def test_create_stream_missing_mint_rejected(self, client):
        payload = build_payload("streamflow_create", {
            "recipient": RECIPIENT, "amount": "1000",
            "period": "86400", "amountPerPeriod": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_create")

    def test_create_stream_missing_amount_rejected(self, client):
        payload = build_payload("streamflow_create", {
            "recipient": RECIPIENT, "mint": USDC_MINT,
            "period": "86400", "amountPerPeriod": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_create")

    def test_create_stream_zero_amount_rejected(self, client):
        payload = build_payload("streamflow_create", {
            "recipient": RECIPIENT, "mint": USDC_MINT, "amount": "0",
            "period": "86400", "amountPerPeriod": "100",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_create")

    def test_cancel_stream_builds(self, client):
        payload = build_payload("streamflow_cancel", {"streamId": DUMMY_STREAM_ID})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "streamflow_cancel")

    def test_cancel_stream_missing_id_rejected(self, client):
        payload = build_payload("streamflow_cancel", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_cancel")

    def test_withdraw_stream_builds(self, client):
        payload = build_payload("streamflow_withdraw", {"streamId": DUMMY_STREAM_ID})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "streamflow_withdraw")

    def test_withdraw_stream_missing_id_rejected(self, client):
        payload = build_payload("streamflow_withdraw", {})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_withdraw")

    def test_transfer_stream_builds(self, client):
        payload = build_payload("streamflow_transfer", {
            "streamId": DUMMY_STREAM_ID, "newRecipient": RECIPIENT
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "streamflow_transfer")

    def test_transfer_stream_missing_recipient_rejected(self, client):
        payload = build_payload("streamflow_transfer", {"streamId": DUMMY_STREAM_ID})
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "streamflow_transfer")


# ══════════════════════════════════════════════════════════════════════════════
# SQUID (Cross-chain)
# ══════════════════════════════════════════════════════════════════════════════

class TestSquidIntegration:
    def test_cross_chain_solana_to_eth_builds(self, client):
        payload = build_payload("cross_chain_swap", {
            "provider": "squid",
            "originChainId": "7565164",
            "destinationChainId": "1",
            "originToken": SOL_MINT,
            "destinationToken": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
            "amount": "0.01",
            "recipient": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
            "slippage": "1.0",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "cross_chain_swap")

    def test_cross_chain_eth_to_solana_builds(self, client):
        payload = build_payload("cross_chain_swap", {
            "provider": "squid",
            "originChainId": "1",
            "destinationChainId": "7565164",
            "originToken": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
            "destinationToken": SOL_MINT,
            "amount": "0.001",
            "recipient": TEST_WALLET,
            "slippage": "1.0",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "cross_chain_swap")

    def test_cross_chain_same_chain_rejected(self, client):
        payload = build_payload("cross_chain_swap", {
            "provider": "squid",
            "originChainId": "1",
            "destinationChainId": "1",
            "originToken": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
            "destinationToken": USDC_MINT,
            "amount": "0.01",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_invalid_params(resp, "cross_chain_swap")

    def test_bridge_alias_works(self, client):
        payload = build_payload("bridge", {
            "provider": "squid",
            "originChainId": "7565164",
            "destinationChainId": "42161",
            "originToken": SOL_MINT,
            "destinationToken": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
            "amount": "0.01",
        })
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert_action_recognized(resp, "bridge")


# ══════════════════════════════════════════════════════════════════════════════
# UNKNOWN ACTION TYPE — always fails with "Unsupported action type"
# ══════════════════════════════════════════════════════════════════════════════

class TestUnknownActionType:
    def test_unknown_action_type_returns_unsupported(self, client):
        """Completely unknown action type must be clearly rejected with 'Unsupported action type'."""
        payload = {"type": "nonexistent_protocol_xyz", "params": {}}
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert resp.status_code in (400, 422, 500), \
            f"Unknown action should return error, got {resp.status_code}"
        assert "Unsupported action type" in resp.text, \
            f"Expected 'Unsupported action type' in response, got: {resp.text[:300]}"

    def test_empty_action_type_rejected(self, client):
        payload = {"type": "", "params": {}}
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert resp.status_code in (400, 422, 500)

    def test_numeric_action_type_rejected(self, client):
        payload = {"type": "12345", "params": {}}
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert resp.status_code in (400, 422, 500)

    def test_sql_injection_in_action_type_rejected(self, client):
        payload = {"type": "' OR '1'='1", "params": {}}
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert resp.status_code in (400, 422, 500)

    def test_missing_type_field_rejected(self, client):
        payload = {"params": {"to": RECIPIENT, "amount": "1"}}
        resp = client.post("/actions/build", json=payload, headers=headers())
        assert resp.status_code in (400, 422, 500)

    def test_missing_wallet_header_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1", "token": "SOL"})
        h = {"X-Internal-Api-Key": INTERNAL_API_KEY, "Content-Type": "application/json"}
        resp = client.post("/actions/build", json=payload, headers=h)
        assert resp.status_code in (400, 401, 422), \
            f"Missing wallet header should be rejected, got {resp.status_code}"

    def test_bad_internal_api_key_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1", "token": "SOL"})
        h = {"X-Internal-Api-Key": "wrong-key", "X-User-Wallet": TEST_WALLET}
        resp = client.post("/actions/build", json=payload, headers=h)
        assert resp.status_code in (401, 403), \
            f"Wrong API key should be 401/403, got {resp.status_code}"

    def test_no_headers_rejected(self, client):
        payload = build_payload("transfer", {"to": RECIPIENT, "amount": "1", "token": "SOL"})
        resp = client.post("/actions/build", json=payload, headers={"Content-Type": "application/json"})
        assert resp.status_code in (400, 401, 403, 422)

    def test_empty_body_rejected(self, client):
        resp = client.post("/actions/build", content=b"", headers=headers())
        assert resp.status_code in (400, 422)

    def test_malformed_json_rejected(self, client):
        h = headers()
        resp = client.post("/actions/build", content=b"{invalid json", headers=h)
        assert resp.status_code in (400, 422)


# ══════════════════════════════════════════════════════════════════════════════
# PROTOCOL REGISTRY API — GET /protocols, GET /protocols/{id}/stats
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_PROTOCOLS = {
    "jupiter", "jupiter_lend", "jupiter_perp",
    "pump_fun", "raydium", "marinade", "jito",
    "orca", "meteora", "marginfi", "kamino",
}


class TestProtocolsApi:
    def test_list_protocols_returns_200(self, client):
        resp = client.get("/protocols", headers=headers())
        assert resp.status_code == 200, f"GET /protocols failed: {resp.status_code}"

    def test_list_protocols_has_protocols_key(self, client):
        resp = client.get("/protocols", headers=headers())
        data = resp.json()
        assert "protocols" in data, f"Missing 'protocols' key: {data.keys()}"

    def test_list_protocols_is_non_empty(self, client):
        resp = client.get("/protocols", headers=headers())
        protocols = resp.json()["protocols"]
        assert len(protocols) >= 10, f"Expected ≥10 protocols, got {len(protocols)}"

    def test_list_protocols_no_bonkfun(self, client):
        """BonkFun was removed — must not appear in protocol list."""
        resp = client.get("/protocols", headers=headers())
        ids = {p["id"] for p in resp.json()["protocols"]}
        assert "bonk_fun" not in ids, "bonk_fun must not be in protocol list after removal"
        assert "bonkfun" not in ids, "bonkfun must not be in protocol list after removal"

    def test_list_protocols_each_has_required_fields(self, client):
        resp = client.get("/protocols", headers=headers())
        for p in resp.json()["protocols"]:
            assert "id" in p, f"Protocol missing 'id': {p}"
            assert "name" in p, f"Protocol {p.get('id')} missing 'name'"
            assert "description" in p, f"Protocol {p.get('id')} missing 'description'"
            assert "category" in p, f"Protocol {p.get('id')} missing 'category'"
            assert "website" in p, f"Protocol {p.get('id')} missing 'website'"
            assert "actions" in p, f"Protocol {p.get('id')} missing 'actions'"
            assert len(p["actions"]) > 0, f"Protocol {p['id']} has 0 actions"

    def test_list_protocols_contains_expected_protocols(self, client):
        resp = client.get("/protocols", headers=headers())
        ids = {p["id"] for p in resp.json()["protocols"]}
        missing = EXPECTED_PROTOCOLS - ids
        assert not missing, f"Missing expected protocols: {missing}"

    def test_get_jupiter_stats(self, client):
        resp = client.get("/protocols/jupiter/stats", headers=headers())
        assert resp.status_code == 200, f"GET /protocols/jupiter/stats: {resp.status_code}"
        data = resp.json()
        assert "protocol" in data, f"Missing 'protocol': {data}"
        assert "actions" in data, f"Missing 'actions': {data}"
        assert data["protocol"]["id"] == "jupiter"
        assert "swap" in data["actions"], "Jupiter must have 'swap' action"
        assert "dca" in data["actions"], "Jupiter must have 'dca' action"

    def test_get_marinade_stats(self, client):
        resp = client.get("/protocols/marinade/stats", headers=headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["protocol"]["id"] == "marinade"
        assert "stake" in data["actions"]
        assert "unstake" in data["actions"]
        assert "delayed_unstake" in data["actions"]

    def test_get_nonexistent_protocol_returns_404(self, client):
        resp = client.get("/protocols/nonexistent_xyz/stats", headers=headers())
        assert resp.status_code == 404, \
            f"Nonexistent protocol should return 404, got {resp.status_code}"

    def test_get_bonkfun_protocol_returns_404(self, client):
        """After removal, bonk_fun must return 404."""
        for protocol_id in ("bonk_fun", "bonkfun"):
            resp = client.get(f"/protocols/{protocol_id}/stats", headers=headers())
            assert resp.status_code == 404, \
                f"Removed protocol {protocol_id!r} should return 404, got {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN REGISTRY API — GET /tokens, GET /tokens/{symbol}
# ══════════════════════════════════════════════════════════════════════════════

class TestTokensApi:
    def test_list_tokens_returns_200(self, client):
        resp = client.get("/tokens", headers=headers())
        assert resp.status_code == 200

    def test_list_tokens_has_tokens_key(self, client):
        resp = client.get("/tokens", headers=headers())
        data = resp.json()
        assert "tokens" in data, f"Missing 'tokens': {data.keys()}"

    def test_list_tokens_is_non_empty(self, client):
        resp = client.get("/tokens", headers=headers())
        tokens = resp.json()["tokens"]
        assert len(tokens) > 0, "Token list must not be empty"

    def test_list_tokens_each_has_required_fields(self, client):
        resp = client.get("/tokens", headers=headers())
        for t in resp.json()["tokens"]:
            assert "symbol" in t, f"Token missing 'symbol': {t}"
            assert "mint" in t, f"Token {t.get('symbol')} missing 'mint'"

    def test_get_sol_token(self, client):
        resp = client.get("/tokens/SOL", headers=headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["token"]["symbol"] == "SOL"
        assert data["token"]["mint"] == SOL_MINT

    def test_get_usdc_token(self, client):
        resp = client.get("/tokens/USDC", headers=headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"]["mint"] == USDC_MINT

    def test_get_token_case_insensitive(self, client):
        """Token lookup should be case-insensitive (route uppercases the symbol)."""
        resp_upper = client.get("/tokens/SOL", headers=headers())
        resp_lower = client.get("/tokens/sol", headers=headers())
        assert resp_upper.status_code == 200
        assert resp_lower.status_code == 200
        assert resp_upper.json()["token"]["mint"] == resp_lower.json()["token"]["mint"]

    def test_get_nonexistent_token_returns_404(self, client):
        resp = client.get("/tokens/FAKEXYZ999", headers=headers())
        assert resp.status_code == 404, \
            f"Unknown token should return 404, got {resp.status_code}"

    def test_get_msol_token(self, client):
        resp = client.get("/tokens/MSOL", headers=headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"]["mint"] == MSOL_MINT
