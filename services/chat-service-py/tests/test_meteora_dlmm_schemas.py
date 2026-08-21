"""
Unit tests for Meteora DLMM action schemas and parameter validation.

Tests cover:
- New ActionType enum values (pool_groups, pool_group, ohlcv, volume_history)
- validate_action_params() for each action with valid, invalid, and edge-case inputs
- No external service required — pure in-process validation
"""

from __future__ import annotations

import pytest
from app.services.action_schemas import ActionType, validate_action_params

# ── Real Solana addresses used as test fixtures ─────────────────────────────

METEORA_SOL_USDC   = "HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1"
SOL_MINT           = "So11111111111111111111111111111111111111112"
USDC_MINT          = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT          = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
LEXICAL_SOL_USDC   = f"{SOL_MINT}-{USDC_MINT}"


# ══════════════════════════════════════════════════════════════════════════════
# ActionType enum completeness
# ══════════════════════════════════════════════════════════════════════════════

class TestActionTypeEnumCompleteness:
    """All 8 DLMM GET query action types must exist in the enum."""

    EXPECTED_DLMM_QUERY_TYPES = [
        "meteora_dlmm_get_pairs",
        "meteora_dlmm_get_pair",
        "meteora_dlmm_get_user_positions",
        "meteora_dlmm_get_active_bin",
        "meteora_dlmm_get_pool_groups",
        "meteora_dlmm_get_pool_group",
        "meteora_dlmm_get_pool_ohlcv",
        "meteora_dlmm_get_pool_volume_history",
    ]

    def test_all_dlmm_query_types_in_enum(self):
        all_values = {at.value for at in ActionType}
        missing = [t for t in self.EXPECTED_DLMM_QUERY_TYPES if t not in all_values]
        assert not missing, f"Missing ActionType values: {missing}"

    def test_new_pool_groups_type_exists(self):
        assert ActionType("meteora_dlmm_get_pool_groups") == ActionType.METEORA_DLMM_GET_POOL_GROUPS

    def test_new_pool_group_type_exists(self):
        assert ActionType("meteora_dlmm_get_pool_group") == ActionType.METEORA_DLMM_GET_POOL_GROUP

    def test_new_pool_ohlcv_type_exists(self):
        assert ActionType("meteora_dlmm_get_pool_ohlcv") == ActionType.METEORA_DLMM_GET_POOL_OHLCV

    def test_new_pool_volume_history_type_exists(self):
        assert ActionType("meteora_dlmm_get_pool_volume_history") == ActionType.METEORA_DLMM_GET_POOL_VOLUME_HISTORY

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            ActionType("meteora_dlmm_get_nonexistent")


# ══════════════════════════════════════════════════════════════════════════════
# validate_action_params — meteora_dlmm_get_pairs
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateDlmmGetPairs:

    def test_empty_params_valid(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {})
        assert result == {}

    def test_query_only(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {"query": "SOL"})
        assert result["query"] == "SOL"

    def test_sort_by_volume_desc(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {"sortBy": "volume_24h:desc"})
        assert "sortBy" in result

    def test_pagination_params(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {"page": "1", "pageSize": "10"})
        assert result["page"] == "1"
        assert result["pageSize"] == "10"

    def test_filter_by_param(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {"filterBy": "tvl>100000"})
        assert "filterBy" in result

    def test_all_valid_params_combined(self):
        params = {
            "query": "USDC", "sortBy": "apy:desc",
            "filterBy": "has_farm=true", "page": "1", "pageSize": "25",
        }
        result = validate_action_params("meteora_dlmm_get_pairs", params)
        assert len(result) == 5

    def test_numeric_values_keep_their_type(self):
        """Numbers stay numbers. validate_action_params used to stringify every
        value; it now preserves int/float/bool so the payload deserialises
        against the Rust structs (serde will not read "1" into a u32). Do not
        "restore" the string form — the Rust side is what depends on this."""
        result = validate_action_params("meteora_dlmm_get_pairs", {"page": 1, "pageSize": 10})
        assert result.get("page") == 1

    def test_value_too_long_rejected(self):
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pairs", {"query": "x" * 600})

    def test_none_values_skipped(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {"query": None, "page": "1"})
        assert "query" not in result
        assert result.get("page") == "1"

    def test_non_string_keys_skipped(self):
        result = validate_action_params("meteora_dlmm_get_pairs", {123: "val", "page": "1"})
        assert "page" in result
        assert len(result) == 1


# ══════════════════════════════════════════════════════════════════════════════
# validate_action_params — meteora_dlmm_get_pool_groups
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateDlmmGetPoolGroups:

    def test_empty_params_valid(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {})
        assert result == {}

    def test_sort_by_valid(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {"sortBy": "total_tvl:desc"})
        assert result["sortBy"] == "total_tvl:desc"

    def test_volume_tw_param(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {"volumeTw": "volume_7d"})
        assert result["volumeTw"] == "volume_7d"

    def test_fee_tvl_ratio_tw_param(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {
            "feeTvlRatioTw": "fee_tvl_ratio_24h"
        })
        assert result["feeTvlRatioTw"] == "fee_tvl_ratio_24h"

    def test_all_params_combined(self):
        params = {
            "query": "SOL", "sortBy": "total_tvl:desc",
            "filterBy": "pool_count>2", "volumeTw": "volume_24h",
            "feeTvlRatioTw": "fee_tvl_ratio_24h",
            "page": "1", "pageSize": "20",
        }
        result = validate_action_params("meteora_dlmm_get_pool_groups", params)
        assert len(result) == 7

    def test_query_with_token_name(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {"query": "BONK"})
        assert result["query"] == "BONK"

    def test_value_too_long_rejected(self):
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pool_groups", {"query": "z" * 600})


# ══════════════════════════════════════════════════════════════════════════════
# validate_action_params — meteora_dlmm_get_pool_group
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateDlmmGetPoolGroup:

    def test_lexical_order_mints_valid(self):
        result = validate_action_params("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": LEXICAL_SOL_USDC,
        })
        assert result["lexicalOrderMints"] == LEXICAL_SOL_USDC

    def test_with_sort_and_pagination(self):
        result = validate_action_params("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": LEXICAL_SOL_USDC,
            "sortBy": "apy:desc", "page": "1", "pageSize": "10",
        })
        assert len(result) == 4

    def test_with_filter_by(self):
        result = validate_action_params("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": LEXICAL_SOL_USDC,
            "filterBy": "tvl>5000",
        })
        assert "filterBy" in result

    def test_usdt_msol_pair(self):
        result = validate_action_params("meteora_dlmm_get_pool_group", {
            "lexicalOrderMints": f"{USDT_MINT}-{SOL_MINT}",
        })
        assert "lexicalOrderMints" in result

    def test_value_too_long_rejected(self):
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pool_group", {
                "lexicalOrderMints": "a" * 600,
            })


# ══════════════════════════════════════════════════════════════════════════════
# validate_action_params — meteora_dlmm_get_pool_ohlcv
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateDlmmGetPoolOhlcv:

    VALID_TIMEFRAMES = ("5m", "30m", "1h", "2h", "4h", "12h", "24h")

    def test_address_only_valid(self):
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC,
        })
        assert result["address"] == METEORA_SOL_USDC

    def test_all_valid_timeframes_accepted(self):
        for tf in self.VALID_TIMEFRAMES:
            result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
                "address": METEORA_SOL_USDC, "timeframe": tf,
            })
            assert result["timeframe"] == tf

    def test_with_start_and_end_time(self):
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": "1700000000", "endTime": "1700086400",
        })
        assert result["startTime"] == "1700000000"
        assert result["endTime"] == "1700086400"

    def test_start_time_only(self):
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "startTime": "1700000000",
        })
        assert "startTime" in result

    def test_all_params_combined(self):
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC, "timeframe": "4h",
            "startTime": "1700000000", "endTime": "1700604800",
        })
        assert len(result) == 4

    def test_timestamps_keep_their_type(self):
        """See TestValidateDlmmGetPairs.test_numeric_values_keep_their_type."""
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC,
            "startTime": 1700000000, "endTime": 1700086400,
        })
        assert result["startTime"] == 1700000000

    def test_sol_mint_as_address_passes_schema_validation(self):
        """Schema layer only checks string length; Rust validates pubkey format"""
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": SOL_MINT,
        })
        assert result["address"] == SOL_MINT

    def test_value_too_long_rejected(self):
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pool_ohlcv", {
                "address": "A" * 600,
            })


# ══════════════════════════════════════════════════════════════════════════════
# validate_action_params — meteora_dlmm_get_pool_volume_history
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateDlmmGetPoolVolumeHistory:

    VALID_TIMEFRAMES = ("5m", "30m", "1h", "2h", "4h", "12h", "24h")

    def test_address_only_valid(self):
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC,
        })
        assert result["address"] == METEORA_SOL_USDC

    def test_all_valid_timeframes_accepted(self):
        for tf in self.VALID_TIMEFRAMES:
            result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
                "address": METEORA_SOL_USDC, "timeframe": tf,
            })
            assert result["timeframe"] == tf

    def test_24h_range(self):
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "1h",
            "startTime": "1700000000", "endTime": "1700086400",
        })
        assert len(result) == 4

    def test_7d_range_with_4h_buckets(self):
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "4h",
            "startTime": "1700000000", "endTime": "1700604800",
        })
        assert result["timeframe"] == "4h"

    def test_30d_range_with_24h_buckets(self):
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "timeframe": "24h",
            "startTime": "1697408000", "endTime": "1700000000",
        })
        assert result["timeframe"] == "24h"

    def test_only_start_time(self):
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC, "startTime": "1700000000",
        })
        assert "startTime" in result

    def test_timestamps_keep_their_type(self):
        """See TestValidateDlmmGetPairs.test_numeric_values_keep_their_type."""
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC,
            "startTime": 1700000000, "endTime": 1700086400,
        })
        assert result["startTime"] == 1700000000

    def test_value_too_long_rejected(self):
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pool_volume_history", {
                "address": "B" * 600,
            })


# ══════════════════════════════════════════════════════════════════════════════
# Cross-cutting edge cases across all new DLMM actions
# ══════════════════════════════════════════════════════════════════════════════

class TestDlmmEdgeCases:

    NEW_DLMM_ACTIONS = [
        "meteora_dlmm_get_pool_groups",
        "meteora_dlmm_get_pool_group",
        "meteora_dlmm_get_pool_ohlcv",
        "meteora_dlmm_get_pool_volume_history",
    ]

    def test_empty_string_values_excluded(self):
        """Empty string values should not cause errors, just pass through or be skipped"""
        for action in self.NEW_DLMM_ACTIONS:
            # Should not raise — empty string key is silently excluded
            validate_action_params(action, {"": "value"})

    def test_dict_value_skipped(self):
        """Dict values are not str/int/float → skipped"""
        for action in self.NEW_DLMM_ACTIONS:
            result = validate_action_params(action, {"query": {"nested": "object"}})
            assert "query" not in result

    def test_scalar_list_is_joined_not_skipped(self):
        """A list of scalars is joined, not dropped — close_accounts sends its
        mints that way. Only a list containing objects becomes a JSON string,
        and a bare dict is still skipped (test_dict_value_skipped)."""
        for action in self.NEW_DLMM_ACTIONS:
            result = validate_action_params(action, {"query": ["a", "b"]})
            assert result["query"] == "a,b"

    def test_boolean_value_for_numeric_param_rejected(self):
        """bool True → str 'True' → float('True') fails → ValueError"""
        with pytest.raises(ValueError):
            validate_action_params("meteora_dlmm_get_pool_groups", {"page": True})

    def test_zero_page_is_valid(self):
        """page=0 is a non-negative numeric string → accepted"""
        result = validate_action_params("meteora_dlmm_get_pairs", {"page": "0"})
        assert result.get("page") == "0"

    def test_negative_page_rejected_by_schema(self):
        """Numeric params must be non-negative — schema enforces this"""
        with pytest.raises(ValueError, match="non-negative"):
            validate_action_params("meteora_dlmm_get_pairs", {"page": "-1"})

    def test_sql_injection_in_query_passes_schema(self):
        """Schema allows any string up to 512 chars — Rust sends it to API as-is"""
        malicious = "'; DROP TABLE pools; --"
        result = validate_action_params("meteora_dlmm_get_pairs", {"query": malicious})
        assert result["query"] == malicious

    def test_unicode_in_query_passes_schema(self):
        result = validate_action_params("meteora_dlmm_get_pool_groups", {"query": "SOL/USDC 🚀"})
        assert "query" in result

    def test_512_char_value_accepted(self):
        """Exactly at limit — should pass"""
        result = validate_action_params("meteora_dlmm_get_pairs", {"query": "x" * 512})
        assert result["query"] == "x" * 512

    def test_513_char_value_rejected(self):
        """One over limit — should fail"""
        with pytest.raises(ValueError, match="maximum length"):
            validate_action_params("meteora_dlmm_get_pairs", {"query": "x" * 513})

    def test_float_timestamp_coerced(self):
        result = validate_action_params("meteora_dlmm_get_pool_ohlcv", {
            "address": METEORA_SOL_USDC,
            "startTime": 1700000000.5,
        })
        assert "startTime" in result

    def test_volume_history_same_start_end_passes_schema(self):
        """Same start and end time is allowed at schema level"""
        result = validate_action_params("meteora_dlmm_get_pool_volume_history", {
            "address": METEORA_SOL_USDC,
            "startTime": "1700000000", "endTime": "1700000000",
        })
        assert result["startTime"] == result["endTime"]
