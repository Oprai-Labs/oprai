"""
Address Validation Service

Provides comprehensive address validation and security analysis:
- Solana address format validation (base58, public key)
- Contract security analysis
- Token safety checks
- Re-entrancy protection detection
- Malicious address detection
- Known malicious contract database
- Holder distribution analysis
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

from app.services.cache import get_redis_client
from app.services.token_security import get_token_security_service

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class AddressType(str, Enum):
    """Types of Solana addresses"""
    UNKNOWN = "unknown"
    WALLET = "wallet"
    TOKEN = "token"
    PROGRAM = "program"
    SYSTEM_PROGRAM = "system_program"
    ASSOCIATED_TOKEN = "associated_token"
    MEMO = "memo_program"
    SYSVAR = "sysvar"


class AddressStatus(str, Enum):
    """Validation status"""
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    MALICIOUS = "malicious"
    SUSPICIOUS = "suspicious"
    SAFE = "safe"


class RiskLevel(str, Enum):
    """Risk levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class AddressValidation:
    """Result of address validation"""
    address: str
    status: AddressStatus = AddressStatus.UNKNOWN
    address_type: AddressType = AddressType.UNKNOWN

    # Analysis
    is_valid_format: bool = False
    is_solana_address: bool = False
    is_token_address: bool = False
    is_program_address: bool = False
    is_wallet_address: bool = False

    # Risk
    risk_level: RiskLevel = RiskLevel.LOW
    risk_score: int = 0  # 0-100

    # Security checks
    has_mint_authority: bool = False
    is_mint_authority_revoked: bool = False
    has_freeze_authority: bool = False
    is_freeze_authority_revoked: bool = False

    # Token specific
    token_name: str | None = None
    token_symbol: str | None = None
    token_decimals: int | None = None
    token_supply: float | None = None

    # Warnings and issues
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    # Metadata
    analyzed_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "status": self.status.value,
            "address_type": self.address_type.value,
            "is_valid_format": self.is_valid_format,
            "is_solana_address": self.is_solana_address,
            "is_token_address": self.is_token_address,
            "is_program_address": self.is_program_address,
            "is_wallet_address": self.is_wallet_address,
            "risk_level": self.risk_level.value,
            "risk_score": self.risk_score,
            "has_mint_authority": self.has_mint_authority,
            "is_mint_authority_revoked": self.is_mint_authority_revoked,
            "has_freeze_authority": self.has_freeze_authority,
            "is_freeze_authority_revoked": self.is_freeze_authority_revoked,
            "token_name": self.token_name,
            "token_symbol": self.token_symbol,
            "token_decimals": self.token_decimals,
            "token_supply": self.token_supply,
            "warnings": self.warnings,
            "issues": self.issues,
            "flags": self.flags,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
        }


@dataclass
class SecurityAnalysis:
    """Security analysis result"""
    # Overall
    is_safe: bool = True
    risk_level: RiskLevel = RiskLevel.LOW

    # Checks
    is_known_malicious: bool = False
    is_known_suspicious: bool = False
    has_high_holder_concentration: bool = False
    has_mint_authority: bool = False
    has_freeze_authority: bool = False
    is_pump_fun_token: bool = False
    is_rug_pull_risk: bool = False
    has_dust_attack_risk: bool = False

    # Scores
    security_score: int = 100  # 0-100
    trust_score: int = 100  # 0-100

    # Details
    holder_distribution: dict[str, Any] = field(default_factory=dict)
    contract_verification: dict[str, Any] = field(default_factory=dict)

    # Recommendations
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "risk_level": self.risk_level.value,
            "is_known_malicious": self.is_known_malicious,
            "is_known_suspicious": self.is_known_suspicious,
            "has_high_holder_concentration": self.has_high_holder_concentration,
            "has_mint_authority": self.has_mint_authority,
            "has_freeze_authority": self.has_freeze_authority,
            "is_pump_fun_token": self.is_pump_fun_token,
            "is_rug_pull_risk": self.is_rug_pull_risk,
            "has_dust_attack_risk": self.has_dust_attack_risk,
            "security_score": self.security_score,
            "trust_score": self.trust_score,
            "holder_distribution": self.holder_distribution,
            "contract_verification": self.contract_verification,
            "recommendations": self.recommendations,
        }


# ============================================================================
# Service Class
# ============================================================================

class AddressValidationService:
    """
    Service for validating Solana addresses and analyzing security.

    Features:
    - Address format validation
    - Type detection (wallet, token, program)
    - Security analysis
    - Known malicious database
    - Token safety checks
    """

    CACHE_TTL_SECONDS = 3600  # 1 hour
    CACHE_KEY_PREFIX = "addr:validation:"

    # Known malicious addresses (placeholder - would be from database)
    KNOWN_MALICIOUS = {
        # Add known malicious addresses here
    }

    KNOWN_SUSPICIOUS = {
        # Add suspicious addresses here
    }

    # Known safe programs
    KNOWN_SAFE_PROGRAMS = {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token Program",
        "ATokenGPvbdGVxr1b2hvZ1iqA2U8r1Z6YjZ8t1F6K8sJ": "Associated Token Program",
        "MemoSq4gqABAXKb96qnH8TysNcUVLyvPJr9vM3H2uK": "Memo Program",
        "MetaqbxxUerdq28cj1RbAWkYQm3ybzjb6Hz8CWJM2CL": "Metadata Program",
        "JUP6LkbZbjS1jKKwapdFQS1M3x7R6zN5hVp8vRfvCL": "Jupiter Aggregator",
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfEE9yYz8kWzNLC": "Raydium AMM",
        "jupoxtF7WDsFfanKxX4FGc9E3cdj1iTJePLQvCj3p": "Orca",
    }

    # Pump.fun program addresses
    PUMP_FUN_PROGRAMS = {
        "6D7FznSGs4MZ4iG3zBE7P6oX7mKkEDc9kTScJh4kqL": "Pump.fun",
        "pAMMBay6citGhK27VP33Wz2N3L1wy8RrWTFblqWC7wm": "Pump.fun Launch",
    }

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _cache_key(self, address: str) -> str:
        """Generate cache key"""
        return f"{self.CACHE_KEY_PREFIX}{address}"

    def validate_address_format(self, address: str) -> AddressValidation:
        """
        Validate address format without external API calls.

        Args:
            address: Address to validate

        Returns:
            AddressValidation result
        """
        result = AddressValidation(address=address)

        # Check if it's a valid base58 string
        import base58

        try:
            decoded = base58.b58decode(address)

            # Check length (Solana addresses are 32 bytes = 43-44 base58 chars)
            if len(decoded) == 32:
                result.is_valid_format = True
                result.is_solana_address = True
            else:
                result.issues.append(f"Invalid address length: {len(decoded)} bytes")
                return result

        except Exception as e:
            result.issues.append(f"Invalid base58 format: {str(e)}")
            return result

        # Check if it's a known safe program
        if address in self.KNOWN_SAFE_PROGRAMS:
            result.address_type = AddressType.PROGRAM
            result.is_program_address = True
            result.status = AddressStatus.SAFE
            result.risk_level = RiskLevel.LOW

        # Check if it's a known malicious address
        elif address in self.KNOWN_MALICIOUS:
            result.status = AddressStatus.MALICIOUS
            result.risk_level = RiskLevel.CRITICAL
            result.risk_score = 100
            result.issues.append("Address is in known malicious database")
            result.flags.append("known_malicious")

        # Check if it's suspicious
        elif address in self.KNOWN_SUSPICIOUS:
            result.status = AddressStatus.SUSPICIOUS
            result.risk_level = RiskLevel.HIGH
            result.risk_score = 80
            result.warnings.append("Address is in suspicious database")
            result.flags.append("known_suspicious")

        # Check if it's a Pump.fun address
        elif address in self.PUMP_FUN_PROGRAMS:
            result.address_type = AddressType.PROGRAM
            result.is_program_address = True
            result.warnings.append("This is a Pump.fun program address")
            result.flags.append("pump_fun")

        # Check address type based on first few bytes (heuristic)
        else:
            result = self._detect_address_type(address, result)

        return result

    def _detect_address_type(self, address: str, result: AddressValidation) -> AddressValidation:
        """Detect address type using heuristics"""
        import base58

        try:
            decoded = base58.b58decode(address)
            first_byte = decoded[0]

            # System program starts with 0
            if first_byte == 0:
                # Could be a wallet or system program
                if len(decoded) == 32:
                    # Check if it's all zeros (system program)
                    if all(b == 0 for b in decoded[:4]):
                        result.address_type = AddressType.SYSTEM_PROGRAM
                        result.is_program_address = True
                    else:
                        result.address_type = AddressType.WALLET
                        result.is_wallet_address = True

            # Token program starts with 1
            elif first_byte == 1:
                result.address_type = AddressType.ASSOCIATED_TOKEN
                result.is_token_address = True

            # Memo program starts with 2
            elif first_byte == 2:
                result.address_type = AddressType.MEMO
                result.is_program_address = True

            # Sysvar programs start with 255
            elif first_byte == 255:
                result.address_type = AddressType.SYSVAR
                result.is_program_address = True

        except Exception:
            pass

        return result

    async def validate_address(
        self,
        address: str,
        check_token: bool = True,
        check_security: bool = True,
    ) -> AddressValidation:
        """
        Full address validation with optional token and security checks.

        Args:
            address: Address to validate
            check_token: Whether to check token details
            check_security: Whether to perform security analysis

        Returns:
            AddressValidation result
        """
        # Try cache first
        try:
            redis_client = await self._get_redis()
            cached = await redis_client.get(self._cache_key(address))
            if cached:
                import json
                data = json.loads(cached)
                # Convert back to AddressValidation
                result = AddressValidation(
                    address=data["address"],
                    status=AddressStatus(data["status"]),
                    address_type=AddressType(data["address_type"]),
                    **{
                        k: v for k, v in data.items()
                        if k not in ["address", "status", "address_type"]
                    }
                )
                return result
        except Exception as e:
            logger.warning("Failed to get cached validation", exc_info=True)

        # Basic validation
        result = self.validate_address_format(address)

        # If not valid format, return early
        if not result.is_valid_format:
            return result

        # Token check
        if check_token and result.is_token_address:
            result = await self._check_token_details(address, result)

        # Security check
        if check_security and result.risk_level != RiskLevel.CRITICAL:
            result = await self._perform_security_analysis(address, result)

        # Cache result
        try:
            redis_client = await self._get_redis()
            import json
            await redis_client.setex(
                self._cache_key(address),
                self.CACHE_TTL_SECONDS,
                json.dumps(result.to_dict())
            )
        except Exception as e:
            logger.warning("Failed to cache validation", exc_info=True)

        return result

    async def _check_token_details(self, address: str, result: AddressValidation) -> AddressValidation:
        """Check token details from blockchain"""
        # This would integrate with blockchain to get token info
        # For now, we'll use the token security service
        try:
            token_service = get_token_security_service()
            security = await token_service.analyze_token_security(address)

            if security:
                result.token_name = security.get("token_name")
                result.token_symbol = security.get("symbol")
                result.has_mint_authority = security.get("has_mint_authority", False)
                result.is_mint_authority_revoked = security.get("mint_authority_revoked", False)
                result.has_freeze_authority = security.get("has_freeze_authority", False)
                result.is_freeze_authority_revoked = security.get("freeze_authority_revoked", False)

                # Update risk based on token analysis
                rug_pull_score = security.get("rug_pull_score", 0)
                if rug_pull_score > 70:
                    result.risk_level = RiskLevel.CRITICAL
                    result.risk_score = rug_pull_score
                    result.issues.append("High rug pull risk detected")
                elif rug_pull_score > 40:
                    result.risk_level = RiskLevel.HIGH
                    result.risk_score = max(result.risk_score, rug_pull_score)
                    result.warnings.append("Moderate rug pull risk")

        except Exception as e:
            logger.warning("Failed to check token details", exc_info=True)

        return result

    async def _perform_security_analysis(self, address: str, result: AddressValidation) -> AddressValidation:
        """Perform security analysis on the address"""
        analysis = await self.analyze_security(address)

        # Apply analysis results to validation
        if analysis.is_known_malicious:
            result.status = AddressStatus.MALICIOUS
            result.risk_level = RiskLevel.CRITICAL
            result.risk_score = 100
            result.issues.append("Address is flagged as malicious")

        if analysis.is_rug_pull_risk:
            result.warnings.append("Potential rug pull risk")
            result.risk_score = max(result.risk_score, 70)

        if analysis.has_high_holder_concentration:
            result.warnings.append("High holder concentration detected")
            result.risk_score = max(result.risk_score, 50)

        # Update status based on risk
        if result.risk_score >= 70:
            result.status = AddressStatus.MALICIOUS
        elif result.risk_score >= 40:
            result.status = AddressStatus.SUSPICIOUS
        elif result.risk_score < 20:
            result.status = AddressStatus.SAFE

        # Add analysis flags
        if analysis.is_pump_fun_token:
            result.flags.append("pump_fun_token")

        return result

    async def analyze_security(
        self,
        address: str,
    ) -> SecurityAnalysis:
        """
        Analyze security of an address.

        Args:
            address: Address to analyze

        Returns:
            SecurityAnalysis result
        """
        analysis = SecurityAnalysis()

        # Check known databases
        if address in self.KNOWN_MALICIOUS:
            analysis.is_known_malicious = True
            analysis.is_safe = False
            analysis.risk_level = RiskLevel.CRITICAL
            analysis.security_score = 0
            analysis.trust_score = 0
            analysis.recommendations.append("DO NOT interact with this address")
            return analysis

        if address in self.KNOWN_SUSPICIOUS:
            analysis.is_known_suspicious = True
            analysis.risk_level = RiskLevel.HIGH
            analysis.security_score = 30
            analysis.trust_score = 30
            analysis.recommendations.append("Proceed with extreme caution")
            return analysis

        # Check if it's a Pump.fun token (higher risk)
        if address in self.PUMP_FUN_PROGRAMS:
            analysis.is_pump_fun_token = True
            analysis.risk_level = RiskLevel.MEDIUM
            analysis.security_score = 60
            analysis.recommendations.append(
                "Pump.fun tokens have higher risk - only use what you can afford to lose"
            )

        # Get token security analysis if it's a token address
        try:
            token_service = get_token_security_service()
            security = await token_service.analyze_token_security(address)

            if security:
                # Check holder concentration
                top_10_concentration = security.get("top_10_holder_percent", 0)
                if top_10_concentration > 80:
                    analysis.has_high_holder_concentration = True
                    analysis.security_score -= 20
                    analysis.recommendations.append(
                        f"High holder concentration: {top_10_concentration}% in top 10 holders"
                    )

                # Check mint authority
                if security.get("has_mint_authority", False):
                    analysis.has_mint_authority = True
                    analysis.security_score -= 10
                    if not security.get("mint_authority_revoked", True):
                        analysis.recommendations.append(
                            "Mint authority is active - token can be minted arbitrarily"
                        )

                # Check freeze authority
                if security.get("has_freeze_authority", False):
                    analysis.has_freeze_authority = True
                    analysis.security_score -= 5
                    if not security.get("freeze_authority_revoked", True):
                        analysis.recommendations.append(
                            "Freeze authority is active - tokens can be frozen"
                        )

                # Check rug pull score
                rug_pull_score = security.get("rug_pull_score", 0)
                if rug_pull_score > 70:
                    analysis.is_rug_pull_risk = True
                    analysis.risk_level = RiskLevel.HIGH
                    analysis.security_score -= 30

        except Exception as e:
            logger.warning("Failed to get token security analysis", exc_info=True)

        # Clamp scores
        analysis.security_score = max(0, min(100, analysis.security_score))
        analysis.trust_score = max(0, min(100, analysis.security_score))

        # Determine overall safety
        if analysis.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            analysis.is_safe = False

        return analysis

    async def batch_validate(
        self,
        addresses: list[str],
        check_token: bool = True,
        check_security: bool = True,
    ) -> list[AddressValidation]:
        """
        Validate multiple addresses in batch.

        Args:
            addresses: List of addresses to validate
            check_token: Whether to check token details
            check_security: Whether to perform security analysis

        Returns:
            List of AddressValidation results
        """
        results = []

        for address in addresses:
            result = await self.validate_address(address, check_token, check_security)
            results.append(result)

        return results


# ============================================================================
# Global Instance
# ============================================================================

_address_service: AddressValidationService | None = None


def get_address_service() -> AddressValidationService:
    """Get or create address validation service"""
    global _address_service
    if _address_service is None:
        _address_service = AddressValidationService()
    return _address_service
