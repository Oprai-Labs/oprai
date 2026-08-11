"""
Tests for Yield Aggregator module.

Tests yield data fetching and comparison from Solana DeFi protocols.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.yield_aggregator import (
    get_yield_comparison,
    _extract_apy,
    PROTOCOLS,
)


class TestProtocols:
    """Test protocol registry"""

    def test_protocols_defined(self):
        """Test protocols dictionary is defined"""
        assert isinstance(PROTOCOLS, dict)
        assert len(PROTOCOLS) > 0

    def test_liquid_staking_protocols(self):
        """Test liquid staking protocols exist"""
        staking_protocols = {k: v for k, v in PROTOCOLS.items() if v["category"] == "liquid_staking"}

        assert "jito" in staking_protocols
        assert "marinade" in staking_protocols
        assert "jupsol" in staking_protocols

    def test_lending_protocols(self):
        """Test lending protocols exist"""
        lending_protocols = {k: v for k, v in PROTOCOLS.items() if v["category"] == "lending"}

        assert "kamino_sol" in lending_protocols

    def test_protocol_structure(self):
        """Test each protocol has required fields"""
        for key, protocol in PROTOCOLS.items():
            assert "name" in protocol
            assert "url" in protocol
            assert "category" in protocol


class TestExtractApy:
    """Test _extract_apy function"""

    def test_extract_marinade_apy(self):
        """Test extracting APY from Marinade response"""
        data = {"apy": 8.5}
        result = _extract_apy("marinade", data)

        assert result == 8.5

    def test_extract_marinade_missing_apy(self):
        """Test extracting APY when missing"""
        data = {}
        result = _extract_apy("marinade", data)

        assert result == 0.0

    def test_extract_jupsol_apy(self):
        """Test extracting APY from JupSOL response"""
        data = {"apy": 7.2}
        result = _extract_apy("jupsol", data)

        assert result == 7.2

    def test_extract_jupsol_annualized_apy(self):
        """Test extracting APY from JupSOL with annualizedApy"""
        data = {"annualizedApy": 8.0}
        result = _extract_apy("jupsol", data)

        assert result == 8.0

    def test_extract_jito_apy(self):
        """Test extracting APY from Jito validators"""
        data = {
            "validators": [
                {"apy": 8.0},
                {"apy": 7.5},
                {"apy": 9.0},
            ]
        }
        result = _extract_apy("jito", data)

        # Average of 8.0, 7.5, 9.0 = 8.166...
        assert result is not None
        assert 8.0 <= result <= 9.0

    def test_extract_jito_list(self):
        """Test Jito with list response"""
        data = [
            {"apy": 8.0},
            {"apy": 7.0},
        ]
        result = _extract_apy("jito", data)

        assert result == 7.5

    def test_extract_jito_no_validators(self):
        """Test Jito with no validators"""
        data = {"validators": []}
        result = _extract_apy("jito", data)

        assert result is None

    def test_extract_kamino_sol_reserve(self):
        """Test extracting APY from Kamino SOL reserve"""
        data = {
            "reserves": [
                {"symbol": "SOL", "supplyApy": 5.5},
                {"symbol": "USDC", "supplyApy": 3.0},
            ]
        }
        result = _extract_apy("kamino_sol", data)

        assert result == 5.5

    def test_extract_kamino_apy_field(self):
        """Test Kamino with apy field"""
        data = {
            "reserves": [
                {"symbol": "SOL", "apy": 6.0},
            ]
        }
        result = _extract_apy("kamino_sol", data)

        assert result == 6.0



    def test_extract_invalid_data(self):
        """Test extraction with invalid data types"""
        result = _extract_apy("marinade", "invalid string")
        assert result is None

        result = _extract_apy("marinade", 123)
        assert result is None

    def test_extract_unknown_protocol(self):
        """Test extraction for unknown protocol"""
        data = {"apy": 5.0}
        result = _extract_apy("unknown_protocol", data)

        assert result is None


class TestGetYieldComparison:
    """Test get_yield_comparison function"""

    @pytest.mark.asyncio
    async def test_get_yield_comparison_empty_response(self):
        """Test yield comparison with empty responses"""
        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 500

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_yield_comparison("liquid_staking")

            # Should return results with None APY for failed fetches
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_yield_comparison_sorted_by_apy(self):
        """Test results are sorted by APY descending"""
        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"apy": 8.0}

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_yield_comparison("liquid_staking")

            # Results should be sorted (None values should be last)
            apy_values = [r["apy"] for r in result if r["apy"] is not None]
            assert apy_values == sorted(apy_values, reverse=True)

    @pytest.mark.asyncio
    async def test_get_yield_comparison_category(self):
        """Test filtering by category"""
        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_yield_comparison("lending")

            # Should only return lending protocols
            for r in result:
                assert r["category"] == "lending"

    @pytest.mark.asyncio
    async def test_get_yield_comparison_includes_protocol_info(self):
        """Test result includes protocol metadata"""
        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"apy": 5.0}

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_yield_comparison("liquid_staking")

            # Check for required fields
            if result:
                assert "protocol" in result[0]
                assert "name" in result[0]
                assert "category" in result[0]
                assert "apy" in result[0]


class TestYieldAggregatorEdgeCases:
    """Test edge cases"""

    @pytest.mark.asyncio
    async def test_connection_timeout(self):
        """Test handling of connection timeout"""
        import httpx

        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = httpx.TimeoutException("timeout")

            result = await get_yield_comparison("liquid_staking")

            # Should still return results (with None APY)
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_invalid_category(self):
        """Test with invalid category returns empty list"""
        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock()

            result = await get_yield_comparison("nonexistent_category")

            # No protocols match
            assert result == []

    @pytest.mark.asyncio
    async def test_partial_failures(self):
        """Test when some protocols fail but others succeed"""
        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1

            mock_response = MagicMock()
            if call_count == 1:
                # First call fails
                mock_response.status_code = 500
            else:
                # Subsequent calls succeed
                mock_response.status_code = 200
                mock_response.json.return_value = {"apy": 7.5}

            return mock_response

        with patch("app.services.yield_aggregator.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = mock_get

            result = await get_yield_comparison("liquid_staking")

            # Should have mix of None and non-None APY values
            apy_values = [r["apy"] for r in result]
            assert None in apy_values or 7.5 in apy_values
