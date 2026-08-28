"""SQLAlchemy model for Lighter delegated agent keys.

Model B (non-custodial): OPRAI mints a Lighter *agent* API keypair per user and
stores the private half **encrypted** here. The user's EVM wallet authorises the
agent once (a change-pubkey personal_sign); thereafter OPRAI signs the account's
orders with the agent key — no per-trade popup, gas-free. The agent key can
trade but cannot withdraw to an arbitrary address (withdrawals are gated to the
L1 owner), so a compromise of this row cannot drain funds to a thief.

One row per L1 (EVM) address: an L1 address maps to exactly one Lighter account,
which carries exactly one OPRAI agent slot (api_key_index=250).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.session import Base


class LighterAgentKey(Base):
    """A delegated Lighter agent key OPRAI holds on a user's behalf."""

    __tablename__ = "lighter_agent_keys"
    __table_args__ = ({"schema": settings.DB_SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The EVM address that owns the Lighter account — the natural key. Lowercased.
    l1_address: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    # OPRAI attribution: the primary wallet + multichain account that onboarded.
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    lighter_account_index: Mapped[int] = mapped_column(Integer, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False, default=250)
    agent_public_key: Mapped[str] = mapped_column(String, nullable=False)
    # Fernet-encrypted agent private key. Never returned to any client.
    agent_private_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # 'pending'  → keypair minted, change-pubkey not yet confirmed on-chain
    # 'active'   → agent authorised, ready to trade
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
