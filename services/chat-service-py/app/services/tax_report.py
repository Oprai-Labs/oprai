"""
Tax Report Service

Generates tax reports for cryptocurrency transactions:
- Capital gains/losses calculation
- Transaction categorization
- Cost basis methods (FIFO, LIFO, HIFO)
- Export to CSV, JSON, PDF
- Income tracking (staking, farming rewards)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

from app.services.cache import get_redis_client

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class CostBasisMethod(str, Enum):
    """Cost basis calculation methods"""
    FIFO = "fifo"      # First in, first out
    LIFO = "lifo"      # Last in, first out
    HIFO = "hifo"      # Highest in, first out
    AVERAGE = "average"  # Average cost


class TransactionType(str, Enum):
    """Transaction types for tax purposes"""
    BUY = "buy"
    SELL = "sell"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    STAKE_REWARD = "stake_reward"
    YIELD_FARMING = "yield_farming"
    NFT_SALE = "nft_sale"
    NFT_PURCHASE = "nft_purchase"
    AIRDROP = "airdrop"
    FEE = "fee"
    OTHER = "other"


class TaxYear(str, Enum):
    """Tax years"""
    YEAR_2023 = "2023"
    YEAR_2024 = "2024"
    YEAR_2025 = "2025"
    YEAR_2026 = "2026"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class TaxableEvent:
    """A taxable event"""
    id: str
    date: datetime
    transaction_type: TransactionType

    # Token info
    token_address: str
    token_symbol: str

    # Amounts
    quantity: float
    price_usd: float
    total_value_usd: float

    # Fee info
    fee_usd: float = 0

    # Cost basis (for sells)
    cost_basis_usd: float = 0
    gain_loss_usd: float = 0

    # Metadata
    tx_signature: str = ""
    description: str = ""
    wallet_address: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "transaction_type": self.transaction_type.value,
            "token_address": self.token_address,
            "token_symbol": self.token_symbol,
            "quantity": self.quantity,
            "price_usd": self.price_usd,
            "total_value_usd": self.total_value_usd,
            "fee_usd": self.fee_usd,
            "cost_basis_usd": self.cost_basis_usd,
            "gain_loss_usd": self.gain_loss_usd,
            "tx_signature": self.tx_signature,
            "description": self.description,
            "wallet_address": self.wallet_address,
        }


@dataclass
class TaxSummary:
    """Tax summary for a period"""
    year: int
    wallet_address: str

    # Income (taxable)
    staking_rewards: float = 0
    farming_rewards: float = 0
    airdrops: float = 0
    nft_sales: float = 0
    other_income: float = 0

    # Capital gains/losses
    short_term_gains: float = 0
    short_term_losses: float = 0
    long_term_gains: float = 0
    long_term_losses: float = 0

    # Totals
    total_income: float = 0
    total_short_term_gain: float = 0
    total_long_term_gain: float = 0
    net_gain_loss: float = 0

    # By token
    by_token: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "wallet_address": self.wallet_address,
            "income": {
                "staking_rewards": self.staking_rewards,
                "farming_rewards": self.farming_rewards,
                "airdrops": self.airdrops,
                "nft_sales": self.nft_sales,
                "other": self.other_income,
            },
            "capital_gains": {
                "short_term_gains": self.short_term_gains,
                "short_term_losses": self.short_term_losses,
                "long_term_gains": self.long_term_gains,
                "long_term_losses": self.long_term_losses,
            },
            "totals": {
                "total_income": self.total_income,
                "short_term_net": self.total_short_term_gain,
                "long_term_net": self.total_long_term_gain,
                "net_gain_loss": self.net_gain_loss,
            },
            "by_token": self.by_token,
        }


@dataclass
class TaxReportConfig:
    """Configuration for tax report"""
    wallet_address: str
    year: int

    # Cost basis method
    cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO

    # Include
    include_staking: bool = True
    include_farming: bool = True
    include_nfts: bool = True
    include_airdrops: bool = True

    # Short-term threshold (days)
    short_term_days: int = 365

    # Tax rates (percent)
    short_term_rate: float = 35.0
    long_term_rate: float = 15.0

    # Fiat currency
    fiat_currency: str = "USD"


# ============================================================================
# Service Class
# ============================================================================

class TaxReportService:
    """
    Service for generating tax reports.

    Features:
    - Capital gains/losses calculation
    - Multiple cost basis methods
    - Income tracking
    - Export to multiple formats
    """

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def generate_tax_report(
        self,
        config: TaxReportConfig,
    ) -> TaxSummary:
        """
        Generate a tax report for the given configuration.

        Args:
            config: Tax report configuration

        Returns:
            TaxSummary with all calculations
        """
        # Get transactions from database
        transactions = await self._get_transactions(config)

        # Calculate cost basis and gains/losses
        events = await self._calculate_events(transactions, config)

        # Generate summary
        summary = await self._generate_summary(events, config)

        return summary

    async def _get_transactions(
        self,
        config: TaxReportConfig,
    ) -> list[dict[str, Any]]:
        """
        Get transactions for the wallet and year.

        In production, this would query the database.
        """
        # Placeholder - would get from database
        return []

    async def _calculate_events(
        self,
        transactions: list[dict[str, Any]],
        config: TaxReportConfig,
    ) -> list[TaxableEvent]:
        """
        Calculate taxable events from transactions.
        """
        events = []
        holdings: dict[str, list[dict]] = {}  # token -> list of purchases

        for tx in transactions:
            event = TaxableEvent(
                id=tx.get("id", ""),
                date=tx.get("date", datetime.utcnow()),
                transaction_type=TransactionType(tx.get("type", "other")),
                token_address=tx.get("token_address", ""),
                token_symbol=tx.get("token_symbol", ""),
                quantity=tx.get("quantity", 0),
                price_usd=tx.get("price_usd", 0),
                total_value_usd=tx.get("total_value_usd", 0),
                fee_usd=tx.get("fee_usd", 0),
                tx_signature=tx.get("signature", ""),
                wallet_address=config.wallet_address,
            )

            # Process based on transaction type
            if event.transaction_type == TransactionType.SELL:
                # Calculate cost basis using selected method
                cost_basis = self._calculate_cost_basis(
                    holdings.get(event.token_symbol, []),
                    event.quantity,
                    config.cost_basis_method,
                )
                event.cost_basis_usd = cost_basis
                event.gain_loss_usd = event.total_value_usd - cost_basis - event.fee_usd

            elif event.transaction_type in [
                TransactionType.BUY,
                TransactionType.TRANSFER_IN,
                TransactionType.STAKE_REWARD,
                TransactionType.YIELD_FARMING,
                TransactionType.AIRDROP,
            ]:
                # Add to holdings
                if event.token_symbol not in holdings:
                    holdings[event.token_symbol] = []

                holdings[event.token_symbol].append({
                    "quantity": event.quantity,
                    "price_usd": event.price_usd,
                    "date": event.date,
                })

            events.append(event)

        return events

    def _calculate_cost_basis(
        self,
        holdings: list[dict],
        quantity: float,
        method: CostBasisMethod,
    ) -> float:
        """
        Calculate cost basis using the specified method.
        """
        if not holdings:
            return 0

        # Sort holdings based on method
        if method == CostBasisMethod.FIFO:
            sorted_holdings = sorted(holdings, key=lambda x: x["date"])
        elif method == CostBasisMethod.LIFO:
            sorted_holdings = sorted(holdings, key=lambda x: x["date"], reverse=True)
        elif method == CostBasisMethod.HIFO:
            sorted_holdings = sorted(holdings, key=lambda x: x["price_usd"], reverse=True)
        else:  # AVERAGE
            total_quantity = sum(h["quantity"] for h in holdings)
            total_value = sum(h["quantity"] * h["price_usd"] for h in holdings)
            return (total_value / total_quantity * quantity) if total_quantity > 0 else 0

        # Calculate cost basis
        remaining = quantity
        cost_basis = 0

        for holding in sorted_holdings:
            if remaining <= 0:
                break

            use_quantity = min(remaining, holding["quantity"])
            cost_basis += use_quantity * holding["price_usd"]
            remaining -= use_quantity

        return cost_basis

    async def _generate_summary(
        self,
        events: list[TaxableEvent],
        config: TaxReportConfig,
    ) -> TaxSummary:
        """Generate tax summary from events"""
        summary = TaxSummary(
            year=config.year,
            wallet_address=config.wallet_address,
        )

        by_token: dict[str, dict[str, float]] = {}

        for event in events:
            # Initialize token if not exists
            if event.token_symbol not in by_token:
                by_token[event.token_symbol] = {
                    "buys": 0,
                    "sells": 0,
                    "gains": 0,
                    "losses": 0,
                    "income": 0,
                }

            # Process based on transaction type
            if event.transaction_type == TransactionType.SELL:
                by_token[event.token_symbol]["sells"] += 1
                if event.gain_loss_usd >= 0:
                    by_token[event.token_symbol]["gains"] += event.gain_loss_usd
                else:
                    by_token[event.token_symbol]["losses"] += abs(event.gain_loss_usd)

                # Determine short or long term
                # Simplified - would need actual purchase dates
                if event.gain_loss_usd >= 0:
                    summary.short_term_gains += event.gain_loss_usd
                else:
                    summary.short_term_losses += abs(event.gain_loss_usd)

            elif event.transaction_type == TransactionType.STAKE_REWARD:
                summary.staking_rewards += event.total_value_usd
                by_token[event.token_symbol]["income"] += event.total_value_usd

            elif event.transaction_type == TransactionType.YIELD_FARMING:
                summary.farming_rewards += event.total_value_usd
                by_token[event.token_symbol]["income"] += event.total_value_usd

            elif event.transaction_type == TransactionType.AIRDROP:
                summary.airdrops += event.total_value_usd
                by_token[event.token_symbol]["income"] += event.total_value_usd

            elif event.transaction_type == TransactionType.NFT_SALE:
                summary.nft_sales += event.total_value_usd

        summary.by_token = by_token

        # Calculate totals
        summary.total_income = (
            summary.staking_rewards +
            summary.farming_rewards +
            summary.airdrops +
            summary.nft_sales +
            summary.other_income
        )

        summary.total_short_term_gain = summary.short_term_gains - summary.short_term_losses
        summary.total_long_term_gain = summary.long_term_gains - summary.long_term_losses

        summary.net_gain_loss = (
            summary.total_income +
            summary.total_short_term_gain +
            summary.total_long_term_gain
        )

        return summary

    async def export_report(
        self,
        config: TaxReportConfig,
        format: str = "json",
    ) -> str:
        """
        Export tax report in specified format.

        Args:
            config: Tax report configuration
            format: Export format (json, csv)

        Returns:
            Exported data as string
        """
        summary = await self.generate_tax_report(config)

        if format == "json":
            import json
            return json.dumps(summary.to_dict(), indent=2, default=str)

        elif format == "csv":
            import csv
            import io

            output = io.StringIO()

            # Write summary header
            output.write(f"Tax Report - {config.year}\n")
            output.write(f"Wallet: {config.wallet_address}\n")
            output.write(f"Cost Basis Method: {config.cost_basis_method.value}\n\n")

            # Write income section
            output.write("INCOME\n")
            output.write(f"Staking Rewards,{summary.staking_rewards}\n")
            output.write(f"Farming Rewards,{summary.farming_rewards}\n")
            output.write(f"Airdrops,{summary.airdrops}\n")
            output.write(f"NFT Sales,{summary.nft_sales}\n")
            output.write(f"Other Income,{summary.other_income}\n")
            output.write(f"Total Income,{summary.total_income}\n\n")

            # Write gains/losses
            output.write("CAPITAL GAINS/LOSSES\n")
            output.write(f"Short-term Gains,{summary.short_term_gains}\n")
            output.write(f"Short-term Losses,{summary.short_term_losses}\n")
            output.write(f"Short-term Net,{summary.total_short_term_gain}\n")
            output.write(f"Long-term Gains,{summary.long_term_gains}\n")
            output.write(f"Long-term Losses,{summary.long_term_losses}\n")
            output.write(f"Long-term Net,{summary.total_long_term_gain}\n\n")

            # Write net
            output.write(f"NET GAIN/LOSS,{summary.net_gain_loss}\n")

            return output.getvalue()

        return ""

    async def get_taxable_events(
        self,
        wallet_address: str,
        year: int,
        token: str | None = None,
    ) -> list[TaxableEvent]:
        """
        Get detailed taxable events.
        """
        config = TaxReportConfig(
            wallet_address=wallet_address,
            year=year,
        )

        transactions = await self._get_transactions(config)
        events = await self._calculate_events(transactions, config)

        # Filter by token if specified
        if token:
            events = [e for e in events if e.token_symbol == token]

        return events


# ============================================================================
# Global Instance
# ============================================================================

_tax_service: TaxReportService | None = None


def get_tax_service() -> TaxReportService:
    """Get or create tax service"""
    global _tax_service
    if _tax_service is None:
        _tax_service = TaxReportService()
    return _tax_service
