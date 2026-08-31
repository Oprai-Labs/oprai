#!/usr/bin/env python3
"""
OPRAI Blockchain Knowledge Base — Comprehensive Bulk Crawler
============================================================
Crawls 68 sources covering Solana, DeFi protocols, blockchain education.
Embeds with OpenAI text-embedding-3-large (3072d) and upserts to Qdrant.

Each page is classified by Claude Haiku before embedding:
  - keep/skip decision (conceptual vs API reference)
  - tags generated from actual content
  - section_anchors: verbatim text quotes marking section boundaries
    → used for accurate, anchor-based chunk splitting

Usage:
    python crawl_all.py [--source GROUP] [--dry-run] [--only ID ...]
Groups: solana, protocols, data, bridges, nft, education, github, blockchain
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.parse import urljoin, urlparse

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import (
    Distance, HnswConfigDiff, PayloadSchemaType,
    ScalarQuantization, ScalarQuantizationConfig,
    ScalarType, SparseIndexParams, SparseVectorParams, VectorParams,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crawl_all")

# ── Constants ─────────────────────────────────────────────────────────────────
COLLECTION = "oprai_blockchain_knowledge"
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 3072
EMBED_BATCH = 128
UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
UA = "OPRAI-Knowledge/1.0 (+https://oprai.io/bot)"
MAX_CHARS = 1200
OVERLAP = 150
MAX_EXCERPT = 500
CLASSIFY_MODEL = "claude-haiku-4-5-20251001"

# ── Env ───────────────────────────────────────────────────────────────────────
def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = os.path.join(os.path.dirname(__file__), "../../.env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env

ENV = _load_env()
OPENAI_KEY = ENV.get("OPRAI_OPENAI_API_KEY", "")
ANTHROPIC_KEY = ENV.get("OPRAI_ANTHROPIC_API_KEY", "")
QDRANT_URL = ENV.get("QDRANT_URL", "http://localhost:6333")

if not OPENAI_KEY:
    log.error("OPRAI_OPENAI_API_KEY not set — aborting"); sys.exit(1)
if not ANTHROPIC_KEY:
    log.error("OPRAI_ANTHROPIC_API_KEY not set — aborting"); sys.exit(1)

# ── Source registry ───────────────────────────────────────────────────────────
@dataclass
class Source:
    id: str
    group: str
    adapter: Literal["sitemap", "crawl", "rss", "github_raw", "defillama", "direct_urls"]
    url: str
    protocol: Optional[str]
    category: str
    license: str
    language: str = "en"
    excerpt_only: bool = False
    crawl_delay: float = 1.0
    max_pages: int = 500
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    # How often this source should be re-checked. The crawler scans every
    # source on each run, but for sources whose freshest point in Qdrant is
    # younger than this interval we short-circuit BEFORE any HTTP fetch or
    # discovery work. That makes hourly cron + per-source cadence cheap
    # (slow-moving docs do zero work most hours, news feeds always work).
    crawl_freq: Literal["hourly", "daily", "weekly", "monthly"] = "weekly"

SOURCES: list[Source] = [
    Source("magiceden_docs", "protocols", "direct_urls",
        "https://docs.magiceden.io", "magic_eden", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=0.8, crawl_freq="weekly",
        tags=["magiceden","magic-eden","nft","marketplace","solana","evm","listings","bids"],
        extra={"skip_classify": True, "urls": [
            "https://docs.magiceden.io/docs/wallet-docs.md",
            "https://docs.magiceden.io/recipes/sol-bid-on-an-individual-nft.md",
            "https://docs.magiceden.io/recipes/sol-list-an-nft.md",
            "https://docs.magiceden.io/recipes/sol-place-collection-bid-from-escrow.md",
            "https://docs.magiceden.io/reference/evm-api-keys.md",
            "https://docs.magiceden.io/reference/evm-api-overview.md",
            "https://docs.magiceden.io/reference/mmm.md",
            "https://docs.magiceden.io/reference/solana-api-keys.md"
        ]}),

    Source("pons_docs", "protocols", "direct_urls",
        "https://docs.ponsfamily.com", "pons", "protocol_documentation", "proprietary-fair-use",
        max_pages=10, crawl_delay=1.0, crawl_freq="weekly",
        tags=["pons","launchpad","robinhood-chain","bonding-curve","noncustodial","weth"],
        extra={"urls": [
            "https://docs.ponsfamily.com",
            "https://docs.ponsfamily.com/v2"
        ]}),

    # ── Recently-added protocols (EVM / multichain) ─────────────────────────
    Source("morpho_docs", "protocols", "sitemap",
        "https://docs.morpho.org", "morpho", "protocol_documentation", "proprietary-fair-use",
        max_pages=180, crawl_delay=1.2, crawl_freq="weekly",
        tags=["morpho","lending","borrow","vaults","markets","multichain","ltv"],
        extra={"sitemap_url": "https://docs.morpho.org/sitemap.xml"}),

    Source("sushi_docs", "protocols", "direct_urls",
        "https://docs.sushi.com", "sushiswap", "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.0, crawl_freq="weekly",
        tags=["sushi","sushiswap","amm","swap","v3","concentrated-liquidity","routeprocessor","liquidity"],
        extra={"skip_classify": True, "urls": ["https://docs.sushi.com/llms-full.txt"]}),

    Source("opensea_docs", "protocols", "direct_urls",
        "https://docs.opensea.io", "opensea", "protocol_documentation", "proprietary-fair-use",
        max_pages=140, crawl_delay=1.0, crawl_freq="weekly",
        tags=["opensea","nft","marketplace","seaport","listings"],
        extra={"urls": [
            "https://docs.opensea.io/docs/agent-accounts",
            "https://docs.opensea.io/docs/agent-tool-registry",
            "https://docs.opensea.io/docs/badges",
            "https://docs.opensea.io/docs/build-with-ai-agents",
            "https://docs.opensea.io/docs/buy-and-sell-nfts",
            "https://docs.opensea.io/docs/collection-offers-and-advanced-trading",
            "https://docs.opensea.io/docs/contract-level-metadata",
            "https://docs.opensea.io/docs/create-a-drop",
            "https://docs.opensea.io/docs/creator-fee-enforcement",
            "https://docs.opensea.io/docs/data-and-events",
            "https://docs.opensea.io/docs/deploy-an-nft-contract",
            "https://docs.opensea.io/docs/deploying-a-seadrop-compatible-contract",
            "https://docs.opensea.io/docs/display-an-nft",
            "https://docs.opensea.io/docs/drops",
            "https://docs.opensea.io/docs/drops-faq",
            "https://docs.opensea.io/docs/locked-and-staked-nfts",
            "https://docs.opensea.io/docs/logos",
            "https://docs.opensea.io/docs/marketplace-trading",
            "https://docs.opensea.io/docs/media-and-traits",
            "https://docs.opensea.io/docs/metadata-standards",
            "https://docs.opensea.io/docs/metadata-storage",
            "https://docs.opensea.io/docs/mint-from-a-drop",
            "https://docs.opensea.io/docs/nft-basics",
            "https://docs.opensea.io/docs/offer-leverage",
            "https://docs.opensea.io/docs/opensea-fees",
            "https://docs.opensea.io/docs/part-1-deploy-a-smart-contract",
            "https://docs.opensea.io/docs/part-1-setup",
            "https://docs.opensea.io/docs/part-1-simple-website-setup",
            "https://docs.opensea.io/docs/part-2-edit-collection-settings",
            "https://docs.opensea.io/docs/part-2-fetch-an-nft-from-opensea",
            "https://docs.opensea.io/docs/part-2-set-up-shipyard",
            "https://docs.opensea.io/docs/part-3-add-metadata-to-your-contract",
            "https://docs.opensea.io/docs/part-3-upload-metadata",
            "https://docs.opensea.io/docs/part-4-edit-drop-settings",
            "https://docs.opensea.io/docs/part-5-customize-drop-page",
            "https://docs.opensea.io/docs/part-5-publish-your-drop",
            "https://docs.opensea.io/docs/query-analytics-and-events",
            "https://docs.opensea.io/docs/seadrop",
            "https://docs.opensea.io/docs/seaport",
            "https://docs.opensea.io/docs/seaport-conduit-controller",
            "https://docs.opensea.io/docs/seaport-enums",
            "https://docs.opensea.io/docs/seaport-events-and-errors",
            "https://docs.opensea.io/docs/seaport-hooks",
            "https://docs.opensea.io/docs/seaport-interface",
            "https://docs.opensea.io/docs/seaport-models",
            "https://docs.opensea.io/docs/search-and-discovery",
            "https://docs.opensea.io/docs/set-up-an-onchain-agent",
            "https://docs.opensea.io/docs/stream-real-time-events",
            "https://docs.opensea.io/docs/swap-tokens",
            "https://docs.opensea.io/docs/tool-manifest",
            "https://docs.opensea.io/docs/transfer-and-manage-nfts",
            "https://docs.opensea.io/docs/updating-metadata",
            "https://docs.opensea.io/docs/x402",
            "https://docs.opensea.io/reference/add_watchlist_entry",
            "https://docs.opensea.io/reference/agent-skill",
            "https://docs.opensea.io/reference/analytics-and-events",
            "https://docs.opensea.io/reference/api-keys",
            "https://docs.opensea.io/reference/api-overview",
            "https://docs.opensea.io/reference/auth",
            "https://docs.opensea.io/reference/build_cross_chain_drop_mint_transactions",
            "https://docs.opensea.io/reference/build_drop_mint_transaction",
            "https://docs.opensea.io/reference/build_offer_v2",
            "https://docs.opensea.io/reference/cancel_order",
            "https://docs.opensea.io/reference/claim_profile_username",
            "https://docs.opensea.io/reference/clear_profile_nft_pfp",
            "https://docs.opensea.io/reference/confirm_agent_relationship",
            "https://docs.opensea.io/reference/create_instant_api_key",
            "https://docs.opensea.io/reference/create_listing_actions",
            "https://docs.opensea.io/reference/create_profile_shelf",
            "https://docs.opensea.io/reference/data-and-discovery",
            "https://docs.opensea.io/reference/declare_agent_account",
            "https://docs.opensea.io/reference/delete_profile_shelf",
            "https://docs.opensea.io/reference/deploy_drop_contract",
            "https://docs.opensea.io/reference/follow_account",
            "https://docs.opensea.io/reference/generate_cross_chain_listing_fulfillment_data",
            "https://docs.opensea.io/reference/generate_listing_fulfillment_data_v2",
            "https://docs.opensea.io/reference/generate_offer_fulfillment_data_v2",
            "https://docs.opensea.io/reference/get_account",
            "https://docs.opensea.io/reference/get_account_followers",
            "https://docs.opensea.io/reference/get_account_following",
            "https://docs.opensea.io/reference/get_account_perpetual_watchlist",
            "https://docs.opensea.io/reference/get_account_relationship",
            "https://docs.opensea.io/reference/get_account_token_activity",
            "https://docs.opensea.io/reference/get_account_token_watchlist",
            "https://docs.opensea.io/reference/get_agent_profile_relationships",
            "https://docs.opensea.io/reference/get_best_listing_nft",
            "https://docs.opensea.io/reference/get_best_listings_collection",
            "https://docs.opensea.io/reference/get_best_offer_nft",
            "https://docs.opensea.io/reference/get_chains",
            "https://docs.opensea.io/reference/get_collection",
            "https://docs.opensea.io/reference/get_collection_floor_prices",
            "https://docs.opensea.io/reference/get_collection_holders",
            "https://docs.opensea.io/reference/get_collection_offer_aggregates",
            "https://docs.opensea.io/reference/get_collection_stats",
            "https://docs.opensea.io/reference/get_collection_traits",
            "https://docs.opensea.io/reference/get_collections_batch",
            "https://docs.opensea.io/reference/get_contract",
            "https://docs.opensea.io/reference/get_deploy_contract_receipt",
            "https://docs.opensea.io/reference/get_drop_by_slug",
            "https://docs.opensea.io/reference/get_drop_eligibility",
            "https://docs.opensea.io/reference/get_drops",
            "https://docs.opensea.io/reference/get_nft",
            "https://docs.opensea.io/reference/get_nft_analytics",
            "https://docs.opensea.io/reference/get_nft_collection",
            "https://docs.opensea.io/reference/get_nft_metadata",
            "https://docs.opensea.io/reference/get_nft_owners",
            "https://docs.opensea.io/reference/get_nfts_batch",
            "https://docs.opensea.io/reference/get_nfts_by_account",
            "https://docs.opensea.io/reference/get_nfts_by_collection",
            "https://docs.opensea.io/reference/get_nfts_by_contract",
            "https://docs.opensea.io/reference/get_offers_collection",
            "https://docs.opensea.io/reference/get_offers_collection_trait",
            "https://docs.opensea.io/reference/get_offers_nft",
            "https://docs.opensea.io/reference/get_order",
            "https://docs.opensea.io/reference/get_payment_token",
            "https://docs.opensea.io/reference/get_portfolio_history",
            "https://docs.opensea.io/reference/get_portfolio_stats",
            "https://docs.opensea.io/reference/get_profile_collections",
            "https://docs.opensea.io/reference/get_profile_favorites",
            "https://docs.opensea.io/reference/get_profile_listings",
            "https://docs.opensea.io/reference/get_profile_offers",
            "https://docs.opensea.io/reference/get_profile_offers_received",
            "https://docs.opensea.io/reference/get_profile_shelves",
            "https://docs.opensea.io/reference/get_swap_quote",
            "https://docs.opensea.io/reference/get_token",
            "https://docs.opensea.io/reference/get_token_activity",
            "https://docs.opensea.io/reference/get_token_activity_stats",
            "https://docs.opensea.io/reference/get_token_balances_by_account",
            "https://docs.opensea.io/reference/get_token_group",
            "https://docs.opensea.io/reference/get_token_groups"
        ]}),

    Source("lighter_docs", "protocols", "direct_urls",
        "https://docs.lighter.xyz", "lighter", "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.0, crawl_freq="weekly",
        tags=["lighter","perps","orderbook","robinhood-chain","non-custodial","deposit","positions","funding"],
        extra={"skip_classify": True, "urls": ["https://docs.lighter.xyz/llms-full.txt"]}),

    Source("relay_docs", "protocols", "sitemap",
        "https://docs.relay.link", "relay", "protocol_documentation", "proprietary-fair-use",
        max_pages=150, crawl_delay=1.2, crawl_freq="weekly",
        tags=["relay","bridge","cross-chain","swap","evm"],
        extra={"sitemap_url": "https://docs.relay.link/sitemap.xml"}),

    # ── Solana Core ─────────────────────────────────────────────────────────
    Source("solana_docs", "solana", "sitemap",
        "https://solana.com/docs", None, "protocol_documentation", "apache-2.0",
        max_pages=800, crawl_delay=1.5, tags=["solana","blockchain","core","accounts","programs","rpc"],
        extra={"sitemap_url": "https://solana.com/sitemap.xml", "include_pattern": r"^/docs/"}),

    Source("anza_docs", "solana", "sitemap",
        "https://docs.anza.xyz", None, "protocol_documentation", "apache-2.0",
        max_pages=300, crawl_delay=1.5, tags=["solana","validator","cli","architecture","anza"]),

    Source("solana_developers", "solana", "sitemap",
        "https://solana.com/developers", None, "protocol_documentation", "apache-2.0",
        max_pages=200, crawl_delay=1.5, tags=["solana","developers","tutorial"]),

    Source("solana_cookbook", "solana", "github_raw",
        "https://raw.githubusercontent.com/solana-developers/solana-cookbook/master",
        None, "guide", "mit",
        max_pages=200, crawl_delay=0.5, tags=["solana","cookbook","tutorial"],
        extra={"repo": "solana-developers/solana-cookbook", "branch": "master", "path": "docs"}),

    Source("solana_validator_docs", "solana", "sitemap",
        "https://solana.com/validators", None, "guide", "apache-2.0",
        max_pages=100, crawl_delay=1.5, tags=["solana","validator","staking","consensus"]),

    Source("solana_blog", "solana", "direct_urls",
        "https://solana.com", None, "guide", "apache-2.0",
        max_pages=30, crawl_delay=2.0, tags=["solana","foundation","updates","development","network"],
        extra={"urls": [
            "https://solana.com/news",
            "https://solana.com/ecosystem",
            "https://solana.com/learn/blockchain-basics",
            "https://solana.com/learn/defi",
            "https://solana.com/learn/nfts",
        ]}),

    # solana_web3js_docs — removed: SDK README changes with every release, stale code risk
    # solana_program_library — removed: SPL developer library, not conceptual knowledge

    # ── DEX / AMM ───────────────────────────────────────────────────────────
    Source("jupiter_station", "protocols", "sitemap",
        "https://docs.jup.ag", "jupiter", "protocol_documentation", "proprietary-fair-use",
        max_pages=400, crawl_delay=1.5, tags=["jupiter","swap","dca","perps"]),

    Source("jupiter_perps", "protocols", "direct_urls",
        "https://docs.jup.ag", "jupiter", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5, tags=["jupiter","perps","perpetuals","jlp","trading"],
        extra={"urls": [
            "https://docs.jup.ag/user-docs/perpetual-trading/how-it-works",
            "https://docs.jup.ag/user-docs/perpetual-trading/jlp",
            "https://docs.jup.ag/user-docs/perpetual-trading/risks",
            "https://docs.jup.ag/user-docs/earn/jupusd",
            "https://docs.jup.ag/user-docs/earn/jupusd/faq",
        ]}),

    Source("Jupiter_litepaper", "protocols", "direct_urls",
        "https://station.jup.ag", "jupiter", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5, tags=["jupiter","tokenomics","jup"],
        extra={"urls": [
            "https://station.jup.ag/guides/jupiter-swap/how-swap-works",
            "https://station.jup.ag/guides/jup-perpetual-exchange/overview",
            "https://station.jup.ag/guides/dca/overview",
            "https://station.jup.ag/guides/limit-order/overview",
        ]}),

    Source("raydium_docs", "protocols", "sitemap",
        "https://docs.raydium.io", "raydium", "protocol_documentation", "proprietary-fair-use",
        max_pages=300, crawl_delay=1.5, tags=["raydium","amm","clmm","defi"]),

    Source("raydium_litepaper", "protocols", "direct_urls",
        "https://docs.raydium.io", "raydium", "protocol_documentation", "proprietary-fair-use",
        max_pages=10, crawl_delay=1.0, tags=["raydium","tokenomics"],
        extra={"urls": [
            "https://docs.raydium.io/raydium/overview/introduction",
            "https://docs.raydium.io/raydium/protocol/concentrated-liquidity-clmm",
        ]}),

    Source("orca_docs", "protocols", "sitemap",
        "https://docs.orca.so", "orca", "protocol_documentation", "proprietary-fair-use",
        max_pages=300, crawl_delay=1.5, tags=["orca","whirlpools","clmm","defi"]),

    Source("meteora_docs", "protocols", "sitemap",
        "https://docs.meteora.ag", "meteora", "protocol_documentation", "proprietary-fair-use",
        max_pages=300, crawl_delay=1.5, tags=["meteora","dlmm","defi"]),

    # ── Lending ─────────────────────────────────────────────────────────────
    # Kamino full docs via llms-full.txt (site is SPA but exposes Mintlify LLM endpoint)
    Source("kamino_docs", "protocols", "direct_urls",
        "https://docs.kamino.finance", "kamino", "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.5,
        tags=["kamino","lending","multiply","vaults","klend","leverage","collateral",
              "liquidity","long-short","loop","solana"],
        extra={"urls": ["https://docs.kamino.finance/llms-full.txt"]}),

    Source("kamino_key_pages", "protocols", "direct_urls",
        "https://docs.kamino.finance", "kamino", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.5, tags=["kamino","lending","multiply","vaults","klend"],
        extra={"urls": [
            "https://docs.kamino.finance/kamino-lend/overview",
            "https://docs.kamino.finance/kamino-lend/how-it-works",
            "https://docs.kamino.finance/products/multiply",
            "https://docs.kamino.finance/products/liquidity-vaults",
            "https://docs.kamino.finance/products/points",
            "https://docs.kamino.finance/kamino-lend/risks",
        ]}),

    # Solend rebranded to Save Finance in 2023
    Source("solend_docs", "protocols", "sitemap",
        "https://docs.save.finance", "solend", "protocol_documentation", "proprietary-fair-use",
        max_pages=100, crawl_delay=1.5,
        tags=["solend","save-finance","lending","defi","solana","borrow","collateral","liquidation","interest-rate"]),
    # solend_key_pages — removed (merged into solend_docs pointing to Save Finance)

    # ── Liquid Staking ──────────────────────────────────────────────────────
    Source("marinade_docs", "protocols", "sitemap",
        "https://docs.marinade.finance", "marinade", "protocol_documentation", "proprietary-fair-use",
        max_pages=300, crawl_delay=1.5, tags=["marinade","staking","msol"]),

    Source("jito_docs", "protocols", "sitemap",
        "https://jito-foundation.gitbook.io/mev", "jito", "protocol_documentation", "proprietary-fair-use",
        max_pages=100, crawl_delay=1.5, tags=["jito","staking","mev","jitosol"],
        extra={"sitemap_url": "https://jito-foundation.gitbook.io/mev/sitemap.xml"}),

    # sanctum_docs — docs.sanctum.so is SPA. Coverage via sanctum_key_pages + sanctum_blog

    Source("sanctum_key_pages", "protocols", "direct_urls",
        "https://docs.sanctum.so", "sanctum", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5, tags=["sanctum","lst","liquid-staking","solana"],
        extra={"urls": [
            "https://docs.sanctum.so/sanctum-docs/products/infinity",
            "https://docs.sanctum.so/sanctum-docs/products/router",
            "https://docs.sanctum.so/sanctum-docs/products/reserve",
            "https://docs.sanctum.so/",
        ]}),

    # ── Other Protocols ──────────────────────────────────────────────────────
    Source("pumpfun_docs", "protocols", "sitemap",
        "https://pump-fun.gitbook.io/pump-fun-docs", "pumpfun", "protocol_documentation", "proprietary-fair-use",
        max_pages=100, crawl_delay=2.0, tags=["pumpfun","token-launch","meme"],
        extra={"sitemap_url": "https://pump-fun.gitbook.io/pump-fun-docs/sitemap.xml"}),

    Source("streamflow_docs", "protocols", "sitemap",
        "https://docs.streamflow.finance", "streamflow", "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["streamflow","vesting","streaming"]),

    Source("realms_docs", "protocols", "sitemap",
        "https://docs.realms.today", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["realms","dao","governance"]),

    Source("squads_docs", "protocols", "sitemap",
        "https://docs.squads.so", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["squads","multisig","security"]),

    Source("sns_docs", "protocols", "sitemap",
        "https://docs.sns.id", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=100, crawl_delay=1.5, tags=["sns","name-service","domains"]),

    Source("makerdao_docs", "protocols", "direct_urls",
        "https://docs.makerdao.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=2.0, tags=["makerdao","sky","dai","usds","cdp","stablecoin","defi","spark","psm"],
        extra={"urls": ["https://docs.makerdao.com/llms-full.txt"]}),

    # ── Data / Oracles / Indexers ────────────────────────────────────────────
    # helius_guides — removed: developer API/webhook/RPC docs, not conceptual knowledge
    # birdeye_docs — removed: market data API, dynamic data fetched via services
    # switchboard_docs — removed: oracle SDK developer docs
    # phantom_docs — removed: wallet developer integration SDK
    # chainlink_docs — removed: oracle API developer docs (EVM-focused)

    Source("pyth_docs", "data", "sitemap",
        "https://docs.pyth.network", None, "protocol_documentation", "apache-2.0",
        max_pages=100, crawl_delay=1.5, tags=["pyth","oracle","price-feed"]),

    # ── NFT ─────────────────────────────────────────────────────────────────
    Source("tensor_docs", "nft", "sitemap",
        "https://docs.tensor.trade", "tensor", "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["tensor","nft","marketplace"]),

    # metaplex_docs — removed: developers.metaplex.com is NFT SDK developer docs

    # ── Bridges ─────────────────────────────────────────────────────────────
    Source("wormhole_docs", "bridges", "sitemap",
        "https://docs.wormhole.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=400, crawl_delay=1.5, tags=["wormhole","bridge","cross-chain"]),

    Source("debridge_docs", "bridges", "direct_urls",
        "https://docs.debridge.finance", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.5, tags=["debridge","bridge","cross-chain","dlp","deswap","solana","evm"],
        extra={"urls": ["https://docs.debridge.finance/llms-full.txt"]}),

    Source("layerzero_docs", "bridges", "sitemap",
        "https://docs.layerzero.network", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=400, crawl_delay=1.5, tags=["layerzero","bridge","omnichain"]),

    Source("circle_cctp_docs", "bridges", "sitemap",
        "https://developers.circle.com/stablecoins/docs", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["circle","cctp","usdc","bridge"]),

    # ── EVM Protocols ───────────────────────────────────────────────────────
    Source("uniswap_docs", "protocols", "sitemap",
        "https://docs.uniswap.org", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["uniswap","amm","liquidity","defi","evm"],
        extra={"sitemap_url": "https://docs.uniswap.org/sitemap.xml"}),

    Source("aave_docs", "protocols", "direct_urls",
        "https://docs.aave.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=2.0, tags=["aave","lending","defi","evm","flash-loans","liquidation","health-factor","e-mode","isolation"],
        extra={"urls": ["https://docs.aave.com/llms-full.txt"]}),

    Source("curve_docs", "protocols", "sitemap",
        "https://docs.curve.fi", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=200, crawl_delay=1.5, tags=["curve","amm","stablecoin","defi","vecrv"],
        extra={"sitemap_url": "https://docs.curve.fi/sitemap.xml"}),

    Source("lido_docs", "protocols", "sitemap",
        "https://docs.lido.fi", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=150, crawl_delay=2.5, tags=["lido","staking","steth","liquid-staking","ethereum"],
        extra={"sitemap_url": "https://docs.lido.fi/sitemap.xml"}),

    Source("gmx_docs", "protocols", "sitemap",
        "https://docs.gmx.io", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=150, crawl_delay=1.5, tags=["gmx","perps","perpetuals","defi","arbitrum"],
        extra={"sitemap_url": "https://docs.gmx.io/sitemap.xml"}),

    # ── Blockchain Ecosystems ────────────────────────────────────────────────
    Source("ethereum_docs", "blockchain", "sitemap",
        "https://ethereum.org/en/developers/docs", None, "protocol_documentation", "cc-by-4.0",
        max_pages=300, crawl_delay=2.0, tags=["ethereum","evm","smart-contracts","solidity","defi"],
        extra={"sitemap_url": "https://ethereum.org/en/sitemap.xml",
               "include_pattern": r"/en/(developers|defi|glossary|nft|staking|dao)/"}),

    # polkadot_wiki — SPA, replaced with GitHub markdown source below
    Source("polkadot_wiki_github", "blockchain", "github_raw",
        "https://raw.githubusercontent.com/w3f/polkadot-wiki/master",
        None, "guide", "gpl-3.0",
        max_pages=90, crawl_delay=0.3,
        tags=["polkadot","parachain","substrate","npos","staking","dot","governance","xcm","cross-chain"],
        extra={"repo": "w3f/polkadot-wiki", "branch": "master", "path": "docs/learn"}),

    Source("near_docs", "blockchain", "sitemap",
        "https://docs.near.org", None, "protocol_documentation", "apache-2.0",
        max_pages=100, crawl_delay=2.0, tags=["near","blockchain","smart-contracts","web3"],
        extra={"sitemap_url": "https://docs.near.org/sitemap.xml"}),

    # ── Education ───────────────────────────────────────────────────────────
    Source("binance_academy", "education", "sitemap",
        "https://academy.binance.com", None, "guide", "proprietary-fair-use",
        max_pages=800, crawl_delay=2.0, tags=["education","binance","crypto","blockchain","defi"],
        extra={"sitemap_url": "https://academy.binance.com/article/sitemap_article_en.xml"}),

    Source("coinbase_learn2", "education", "sitemap",
        "https://www.coinbase.com/learn", None, "guide", "proprietary-fair-use",
        max_pages=400, crawl_delay=3.0, tags=["education","coinbase","crypto","basics"]),

    Source("kraken_learn2", "education", "sitemap",
        "https://www.kraken.com/learn", None, "guide", "proprietary-fair-use",
        max_pages=400, crawl_delay=3.0, tags=["education","kraken","crypto"]),

    Source("coingecko_learn2", "education", "sitemap",
        "https://www.coingecko.com/learn", None, "guide", "proprietary-fair-use",
        max_pages=300, crawl_delay=2.0, tags=["education","coingecko","crypto","glossary"]),

    Source("cmc_alexandria2", "education", "sitemap",
        "https://coinmarketcap.com/academy", None, "guide", "proprietary-fair-use",
        max_pages=500, crawl_delay=2.0, tags=["education","coinmarketcap","crypto"]),

    Source("investopedia_crypto2", "education", "sitemap",
        "https://www.investopedia.com/cryptocurrency-4427699", None, "guide", "proprietary-fair-use",
        max_pages=300, crawl_delay=2.5, tags=["education","investopedia","finance","crypto"]),

    Source("bybit_learn", "education", "sitemap",
        "https://learn.bybit.com", None, "guide", "proprietary-fair-use",
        max_pages=500, crawl_delay=2.0, tags=["education","bybit","crypto","trading"]),

    Source("coindesk_learn", "education", "direct_urls",
        "https://www.coindesk.com/learn", None, "guide", "proprietary-fair-use",
        max_pages=60, crawl_delay=3.0, tags=["education","coindesk","crypto","blockchain"],
        extra={"urls": [
            "https://www.coindesk.com/learn/what-is-bitcoin/",
            "https://www.coindesk.com/learn/what-is-ethereum/",
            "https://www.coindesk.com/learn/what-is-a-blockchain/",
            "https://www.coindesk.com/learn/what-is-defi/",
            "https://www.coindesk.com/learn/what-is-a-smart-contract/",
            "https://www.coindesk.com/learn/what-is-a-dao/",
            "https://www.coindesk.com/learn/what-is-staking/",
            "https://www.coindesk.com/learn/what-are-nfts/",
            "https://www.coindesk.com/learn/what-is-a-crypto-wallet/",
            "https://www.coindesk.com/learn/what-is-defi-lending/",
            "https://www.coindesk.com/learn/what-is-yield-farming/",
            "https://www.coindesk.com/learn/what-is-an-automated-market-maker-amm/",
            "https://www.coindesk.com/learn/what-is-liquidity-mining/",
            "https://www.coindesk.com/learn/what-is-a-layer-2/",
            "https://www.coindesk.com/learn/what-is-a-bridge-in-crypto/",
            "https://www.coindesk.com/learn/what-is-solana/",
            "https://www.coindesk.com/learn/what-is-proof-of-history/",
            "https://www.coindesk.com/learn/what-is-a-memecoin/",
            "https://www.coindesk.com/learn/what-is-tokenomics/",
            "https://www.coindesk.com/learn/what-is-a-crypto-exchange/",
        ]}),

    Source("finematics_blog", "education", "direct_urls",
        "https://finematics.com", None, "guide", "proprietary-fair-use",
        max_pages=50, crawl_delay=2.0, tags=["education","defi","crypto","concepts"],
        extra={"urls": [
            "https://finematics.com/guide-to-decentralized-finance/",
            "https://finematics.com/what-is-an-automated-market-maker-amm/",
            "https://finematics.com/what-is-yield-farming-in-defi/",
            "https://finematics.com/what-is-impermanent-loss/",
            "https://finematics.com/what-is-a-liquidity-pool-in-defi/",
            "https://finematics.com/what-is-defi-staking/",
            "https://finematics.com/what-is-a-flash-loan/",
            "https://finematics.com/defi-liquidations-explained/",
            "https://finematics.com/what-are-wrapped-tokens/",
            "https://finematics.com/what-is-a-dao/",
            "https://finematics.com/uniswap-v3-explained/",
            "https://finematics.com/what-is-aave/",
            "https://finematics.com/what-is-compound-finance/",
            "https://finematics.com/curve-finance-explained/",
            "https://finematics.com/what-is-yearn-finance/",
            "https://finematics.com/what-is-a-token-bridge/",
            "https://finematics.com/layer-2-explained/",
            "https://finematics.com/rollups-explained/",
            "https://finematics.com/what-is-proof-of-stake/",
            "https://finematics.com/what-is-mev-maximal-extractable-value/",
        ]}),

    # ── GitHub ───────────────────────────────────────────────────────────────
    Source("awesome_solana", "github", "github_raw",
        "https://raw.githubusercontent.com/solana-developers/awesome-solana/main/README.md",
        None, "reference", "cc0",
        max_pages=5, crawl_delay=0.5, tags=["solana","awesome","resources"],
        extra={"single_file": True}),

    # awesome_solana_defi, solana_py_docs, anchor_examples — removed: SDK READMEs, stale code risk

    Source("defi_developer_road", "education", "github_raw",
        "https://raw.githubusercontent.com/OffcierCia/DeFi-Developer-Road-Map/main/README.md",
        None, "guide", "cc0",
        max_pages=5, crawl_delay=0.5, tags=["defi","developer","roadmap","education","web3"],
        extra={"single_file": True}),

    Source("awesome_defi", "education", "github_raw",
        "https://raw.githubusercontent.com/yjjnls/awesome-blockchain/master/README.md",
        None, "guide", "mit",
        max_pages=5, crawl_delay=0.5, tags=["blockchain","defi","education","awesome"],
        extra={"single_file": True}),

    # ── Kamino (GitHub alternatives — docs.kamino.finance is SPA) ────────────
    Source("kamino_klend_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/Kamino-Finance/klend/main/README.md",
        "kamino", "reference", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["kamino","klend","lending","collateral","liquidation","borrow","supply","health-factor"],
        extra={"single_file": True}),

    # kamino_sdk_github — removed: SDK README changes with releases, stale code risk

    Source("kamino_multiply_guide", "protocols", "direct_urls",
        "https://app.kamino.finance", "kamino", "guide", "proprietary-fair-use",
        max_pages=10, crawl_delay=2.0,
        tags=["kamino","multiply","leverage","lending","vaults","klend","liquidation"],
        extra={"urls": [
            "https://docs.kamino.finance/kamino-lend/overview",
            "https://docs.kamino.finance/products/multiply",
            "https://docs.kamino.finance/kamino-lend/risks",
            "https://docs.kamino.finance/products/liquidity-vaults",
        ]}),

    # ── Solend (dev.solend.fi static + GitHub) ─────────────────────────────
    Source("how_to_solana_lending", "education", "github_raw",
        "https://raw.githubusercontent.com/ryze-labs/how-to-solana/main/1.%20How%20to%20Solana%20%E2%80%94%20Chapter%201%3A%20Lending%20%26%20Borrowing.md",
        None, "guide", "mit",
        max_pages=5, crawl_delay=0.5,
        tags=["solana","lending","borrowing","kamino","solend","education","isolated-pool","main-pool"],
        extra={"single_file": True}),

    # ── Sanctum (blog — learn.sanctum.so is 403) ────────────────────────────
    Source("sanctum_blog", "protocols", "direct_urls",
        "https://sanctum.so", "sanctum", "guide", "proprietary-fair-use",
        max_pages=15, crawl_delay=2.0,
        tags=["sanctum","lst","liquid-staking","infinity","router","reserve","solana"],
        extra={"urls": [
            "https://sanctum.so/blog/solana-liquid-staking-guide",
            "https://sanctum.so/blog/what-is-sanctum",
            "https://sanctum.so/blog/introducing-infinity",
        ]}),

    # ── pump.fun (official GitHub docs — gitbook is SPA) ─────────────────
    Source("pumpfun_program_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_PROGRAM_README.md",
        "pumpfun", "reference", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["pumpfun","bonding-curve","token-launch","meme","program","buy","sell","graduation"],
        extra={"single_file": True}),

    Source("pumpfun_swap_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_SWAP_README.md",
        "pumpfun", "reference", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["pumpfun","swap","migration","token","amm","graduation","raydium"],
        extra={"single_file": True}),

    Source("pumpfun_faq_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/FAQ.md",
        "pumpfun", "reference", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["pumpfun","faq","bonding-curve","token-launch","fees","graduation"],
        extra={"single_file": True}),

    # ── Helius (GitHub — docs.helius.dev is SPA) ────────────────────────────
    # helius_sdk_github, helius_rust_sdk_github — removed: SDK READMEs, stale code risk

    # ── Firedancer (static HTML docs + GitHub) ──────────────────────────────
    Source("firedancer_github", "solana", "github_raw",
        "https://raw.githubusercontent.com/firedancer-io/firedancer/main/README.md",
        None, "reference", "apache-2.0",
        max_pages=5, crawl_delay=0.5,
        tags=["firedancer","validator","solana","performance","concurrent","frankendancer","jump"],
        extra={"single_file": True}),

    # ── GMX (Docusaurus static — sitemap approach failed) ───────────────────
    Source("gmx_docs_direct", "protocols", "direct_urls",
        "https://docs.gmx.io", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5,
        tags=["gmx","perps","glp","gm","funding","mark-price","index-price","arbitrum","liquidity"],
        extra={"urls": [
            "https://docs.gmx.io/docs/intro/",
            "https://docs.gmx.io/docs/trading/v2/",
            "https://docs.gmx.io/docs/providing-liquidity/v2/",
            "https://docs.gmx.io/docs/tokenomics/gmx-token/",
            "https://docs.gmx.io/docs/tokenomics/glp/",
            "https://docs.gmx.io/docs/trading/v2/funding-fees/",
            "https://docs.gmx.io/docs/trading/v2/borrowing-fees/",
            "https://docs.gmx.io/docs/trading/v2/price-impact/",
        ]}),

    Source("gmx_synthetics_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/gmx-io/gmx-synthetics/main/README.md",
        None, "reference", "mit",
        max_pages=5, crawl_delay=0.5,
        tags=["gmx","perps","mark-price","index-price","funding","oracle","glv","gm","arbitrum"],
        extra={"single_file": True}),

    # ── Jito TipRouter + MEV docs ────────────────────────────────────────────
    Source("jito_tiprouter_github", "protocols", "github_raw",
        "https://raw.githubusercontent.com/jito-foundation/jito-tip-router/master/README.md",
        "jito", "reference", "apache-2.0",
        max_pages=5, crawl_delay=0.5,
        tags=["jito","tip-router","mev","ncn","solana","tips","validators"],
        extra={"single_file": True}),

    Source("jito_omnidocs_tiprouter", "protocols", "github_raw",
        "https://raw.githubusercontent.com/jito-foundation/jito-omnidocs/master/tiprouter/overview/index.md",
        "jito", "guide", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["jito","tip-router","ncn","mev","staking","solana","node-operator"],
        extra={"single_file": True}),

    Source("jito_lowlatency_docs", "protocols", "github_raw",
        "https://raw.githubusercontent.com/jito-labs/jito-docs/main/docs/source/lowlatencytxnsend.md",
        "jito", "guide", "proprietary-fair-use",
        max_pages=5, crawl_delay=0.5,
        tags=["jito","bundles","mev","sandwich","tips","transactions","low-latency","sendbundle"],
        extra={"single_file": True}),

    # jito_mev_bot_github — removed: archived bot code, not conceptual content

    # ── Curve readthedocs (stableswap invariant + veToken + DAO) ────────────
    Source("curve_readthedocs", "protocols", "direct_urls",
        "https://curve.readthedocs.io", "curve", "protocol_documentation", "mit",
        max_pages=30, crawl_delay=1.5,
        tags=["curve","stableswap","invariant","vecrv","gauge","defi","voting","bribe","amm"],
        extra={"urls": [
            "https://curve.readthedocs.io/exchange-overview.html",
            "https://curve.readthedocs.io/exchange-pools.html",
            "https://curve.readthedocs.io/exchange-lp-tokens.html",
            "https://curve.readthedocs.io/exchange-deposits.html",
            "https://curve.readthedocs.io/exchange-swaps.html",
            "https://curve.readthedocs.io/dao-gauges.html",
            "https://curve.readthedocs.io/dao-vecrv.html",
            "https://curve.readthedocs.io/dao-fees.html",
            "https://curve.readthedocs.io/dao-voting.html",
        ]}),

    # ── Impermanent loss formula (Uniswap v2 advanced topics) ───────────────
    Source("uniswap_v2_advanced", "protocols", "direct_urls",
        "https://docs.uniswap.org", None, "protocol_documentation", "gpl-3.0",
        max_pages=15, crawl_delay=1.5,
        tags=["uniswap","amm","impermanent-loss","liquidity","formula","xy-k","constant-product"],
        extra={"urls": [
            "https://docs.uniswap.org/contracts/v2/concepts/advanced-topics/understanding-returns",
            "https://docs.uniswap.org/contracts/v2/concepts/advanced-topics/fees",
            "https://docs.uniswap.org/contracts/v2/concepts/advanced-topics/pricing",
            "https://docs.uniswap.org/contracts/v2/concepts/core-concepts/pools",
            "https://docs.uniswap.org/concepts/protocol/concentrated-liquidity",
        ]}),

    Source("rareskills_defi", "education", "direct_urls",
        "https://www.rareskills.io", None, "guide", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["defi","amm","curve","uniswap","impermanent-loss","formula","technical","math"],
        extra={"urls": [
            "https://www.rareskills.io/post/curve-stableswap-invariant",
            "https://www.rareskills.io/post/uniswap-v2-tutorial",
            "https://www.rareskills.io/post/uniswap-v3-concentrated-liquidity",
            "https://www.rareskills.io/post/impermanent-loss",
            "https://www.rareskills.io/post/defi-liquidations",
        ]}),

    # ── UST / Terra collapse post-mortem ─────────────────────────────────────
    Source("terra_ust_postmortem", "education", "direct_urls",
        "https://medium.com", None, "guide", "proprietary-fair-use",
        max_pages=10, crawl_delay=3.0,
        tags=["terra","ust","luna","stablecoin","algorithmic","collapse","death-spiral","defi","risk"],
        extra={"urls": [
            "https://medium.com/dragonfly-research/the-reign-of-terra-the-rise-and-fall-of-ust-208dabbc8e6e",
            "https://medium.com/cherry-labs/terra-death-spiral-a-post-mortem-eb7f4f143a1e",
        ]}),

    # ── Snapshot voting (off-chain governance) ────────────────────────────────
    Source("snapshot_docs_github", "education", "github_raw",
        "https://raw.githubusercontent.com/snapshot-labs/snapshot-docs/master/README.md",
        None, "reference", "mit",
        max_pages=5, crawl_delay=0.5,
        tags=["snapshot","governance","voting","dao","off-chain","strategies","spaces"],
        extra={"single_file": True}),

    # ── Solana block production & program upgrade deep dives ─────────────────
    # ── DeFi strategy & portfolio management ─────────────────────────────────
    Source("defi_strategy_finematics", "education", "direct_urls",
        "https://finematics.com", None, "guide", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["defi","strategy","portfolio","yield","risk","curve","yearn","aave","education"],
        extra={"urls": [
            "https://finematics.com/curve-finance-explained/",
            "https://finematics.com/what-is-yearn-finance/",
            "https://finematics.com/what-is-mev-maximal-extractable-value/",
            "https://finematics.com/defi-liquidations-explained/",
            "https://finematics.com/what-is-aave/",
            "https://finematics.com/what-is-compound-finance/",
            "https://finematics.com/uniswap-v3-explained/",
            "https://finematics.com/what-are-wrapped-tokens/",
        ]}),

    Source("stablecoin_strategy_education", "education", "direct_urls",
        "https://phemex.com", None, "guide", "proprietary-fair-use",
        max_pages=15, crawl_delay=2.0,
        tags=["stablecoin","yield","strategy","portfolio","defi","risk","allocation"],
        extra={"urls": [
            "https://phemex.com/academy/what-is-impermanent-loss",
            "https://phemex.com/academy/defi-portfolio-management",
            "https://phemex.com/academy/what-is-yield-farming",
            "https://phemex.com/academy/defi-risks",
            "https://phemex.com/academy/stablecoin-yield",
        ]}),

    # ── veToken model deep dive ───────────────────────────────────────────────
    # ── Arbitrage bots & MEV deep dive ────────────────────────────────────────
    Source("solana_mev_education", "education", "direct_urls",
        "https://www.helius.dev", None, "guide", "proprietary-fair-use",
        max_pages=10, crawl_delay=2.0,
        tags=["solana","mev","arbitrage","bot","jito","sandwich","frontrun","backrun"],
        extra={"urls": [
            "https://www.helius.dev/blog/solana-mev-an-introduction",
            "https://www.helius.dev/blog/all-you-need-to-know-about-solana-and-mev",
        ]}),

    # ── Comprehensive DeFi education additions ───────────────────────────────
    # solana_program_examples_github, anchor_docs_github — removed: code-heavy, stale risk

    Source("circle_usdc_docs", "bridges", "direct_urls",
        "https://developers.circle.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["circle","cctp","usdc","native","wrapped","bridge","cross-chain","stablecoin"],
        extra={"urls": [
            "https://developers.circle.com/stablecoins/cctp-getting-started",
            "https://developers.circle.com/stablecoins/usdc-on-solana",
            "https://developers.circle.com/stablecoins/what-is-cctp",
            "https://developers.circle.com/stablecoins/docs/cctp-getting-started",
        ]}),

    # ── DeFi Analyst Knowledge — Yield Strategy Frameworks ───────────────────
    Source("yearn_docs", "protocols", "direct_urls",
        "https://docs.yearn.fi", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=60, crawl_delay=1.5,
        tags=["yearn","vaults","yvault","yield","autocompound","strategy","defi","evm"],
        extra={"urls": [
            "https://docs.yearn.fi/getting-started/intro",
            "https://docs.yearn.fi/getting-started/products/yvaults/overview",
            "https://docs.yearn.fi/getting-started/products/yvaults/vault-tokens",
            "https://docs.yearn.fi/getting-started/products/yeth/overview",
            "https://docs.yearn.fi/developers/v3/overview",
            "https://docs.yearn.fi/developers/v3/strategy_writing_guide",
            "https://docs.yearn.fi/resources/risks/vault-risks",
        ]}),

    Source("compound_docs", "protocols", "direct_urls",
        "https://docs.compound.finance", None, "protocol_documentation", "bsd-3",
        max_pages=60, crawl_delay=1.5,
        tags=["compound","lending","ctoken","interest-rate","collateral","borrow","supply","defi","evm"],
        extra={"urls": [
            "https://docs.compound.finance/",
            "https://docs.compound.finance/interest-rates/",
            "https://docs.compound.finance/collateral-and-borrowing/",
            "https://docs.compound.finance/liquidation/",
            "https://docs.compound.finance/governance/",
            "https://docs.compound.finance/v2/ctokens/",
            "https://docs.compound.finance/v2/comptroller/",
        ]}),

    Source("aave_risk_docs", "protocols", "direct_urls",
        "https://aave.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=2.0,
        tags=["aave","risk","health-factor","liquidation","ltv","borrow","collateral","defi","evm"],
        extra={"urls": [
            "https://aave.com/docs/resources/risks",
            "https://aave.com/docs/resources/parameters",
            "https://aave.com/docs/aave-101",
            "https://aave.com/docs/aave-v4/positions/liquidations",
        ]}),

    Source("pendle_docs", "protocols", "direct_urls",
        "https://docs.pendle.finance", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=60, crawl_delay=1.5,
        tags=["pendle","yield-tokenization","pt","yt","sy","fixed-yield","defi","evm"],
        extra={"urls": [
            "https://docs.pendle.finance/pendle-v2/Introduction",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/PT",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/YT",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/LiquidityEngines/AMM",
        ]}),

    Source("convex_docs", "protocols", "direct_urls",
        "https://docs.convexfinance.com", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5,
        tags=["convex","curve-wars","vlcvx","crv","boost","gauge","staking","defi","evm"],
        extra={"urls": [
            "https://docs.convexfinance.com/convexfinance/",
            "https://docs.convexfinance.com/convexfinance/general-information/understanding-convex-finance",
            "https://docs.convexfinance.com/convexfinance/general-information/voting-and-gauge-weights",
            "https://docs.convexfinance.com/convexfinance/faq",
        ]}),

    # ── DeFi Analyst — Risk Methodology & Strategy Deep Dives ────────────────
    Source("gauntlet_research", "education", "direct_urls",
        "https://www.gauntlet.xyz", None, "guide", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["risk","methodology","simulation","defi","protocol","liquidation","collateral","gauntlet"],
        extra={"urls": [
            "https://www.gauntlet.xyz/resources",
            "https://www.gauntlet.xyz/blog/how-gauntlet-risk-models-work",
            "https://www.gauntlet.xyz/blog/defi-protocol-risk-management",
        ]}),

    # ── DeFi Analyst — Delta-Neutral & Looping Strategies ────────────────────
    Source("delta_neutral_education", "education", "github_raw",
        "https://raw.githubusercontent.com/OffcierCia/ultimate-defi-research-base/main/README.md",
        None, "guide", "mit",
        max_pages=5, crawl_delay=0.5,
        tags=["delta-neutral","hedge","strategy","defi","yield","short","long","market-neutral","leverage"],
        extra={"single_file": True}),

    Source("looping_strategy_education", "education", "direct_urls",
        "https://docs.kamino.finance", "kamino", "guide", "proprietary-fair-use",
        max_pages=10, crawl_delay=2.0,
        tags=["looping","leverage","borrow","supply","strategy","kamino","multiply","defi","yield"],
        extra={"urls": [
            "https://kamino.finance/docs/multiply",
            "https://kamino.finance/docs/borrow-lend",
        ]}),

    # ── DeFi Analyst — LST Comparison & Liquid Staking Strategy ─────────────
    Source("lst_comparison_guide", "education", "direct_urls",
        "https://www.jito.network", None, "guide", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["lst","liquid-staking","jitosol","msol","bsol","sanctum","comparison","yield","solana"],
        extra={"urls": [
            "https://www.jito.network/blog/",
            "https://marinade.finance/blog/",
            "https://docs.jito.network/",
        ]}),

    Source("staking_guide_solana", "education", "direct_urls",
        "https://solana.com", None, "guide", "apache-2.0",
        max_pages=20, crawl_delay=1.5,
        tags=["solana","staking","validator","delegation","epoch","stake","lst","liquid-staking","yield"],
        extra={"urls": [
            "https://solana.com/staking",
            "https://solana.com/learn/staking-and-inflation",
            "https://docs.solana.com/staking",
        ]}),

    # ── DeFi Analyst — Curve Wars & veToken Mechanics ────────────────────────
    Source("crv_wars_guide", "education", "direct_urls",
        "https://tokenbrice.xyz", None, "guide", "cc-by-4.0",
        max_pages=15, crawl_delay=2.0,
        tags=["curve-wars","crv","vecrv","convex","bribe","gauge","defi","vetoken"],
        extra={"urls": [
            "https://tokenbrice.xyz/crv-wars/",
            "https://tokenbrice.xyz/protocol-owned-liquidity/",
            "https://tokenbrice.xyz/liquidity-wars/",
        ]}),

    # ── DeFi Analyst — Portfolio Allocation & Risk Management ────────────────
    Source("il_math_deep_dive", "education", "direct_urls",
        "https://www.rareskills.io", None, "guide", "proprietary-fair-use",
        max_pages=10, crawl_delay=2.0,
        tags=["impermanent-loss","amm","formula","uniswap","math","liquidity","defi","concentrated"],
        extra={"urls": [
            "https://www.rareskills.io/post/impermanent-loss",
            "https://www.rareskills.io/post/uniswap-v3-concentrated-liquidity",
        ]}),

    # ── DeFi Analyst — Solana-specific Yield Strategy ─────────────────────────
    Source("meteora_strategy_guide", "protocols", "direct_urls",
        "https://docs.meteora.ag", "meteora", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5,
        tags=["meteora","dlmm","bin","liquidity-shape","fee-apr","strategy","range","solana"],
        extra={"urls": [
            "https://docs.meteora.ag/overview/products/dlmm/what-is-dlmm",
            "https://docs.meteora.ag/overview/products/dlmm/strategies-and-use-cases",
            "https://docs.meteora.ag/overview/products/dlmm/dlmm-fee-calculation",
            "https://docs.meteora.ag/developer-guide/guides/dlmm/overview",
        ]}),

    Source("jlp_yield_guide", "protocols", "direct_urls",
        "https://docs.jup.ag", "jupiter", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["jlp","jupiter","perps","yield","lp","borrow-fee","funding","pool","strategy"],
        extra={"urls": [
            "https://docs.jup.ag/user-docs/trade/perps-and-jlp/",
            "https://docs.jup.ag/user-docs/trade/perps-and-jlp/earn",
            "https://docs.jup.ag/user-docs/trade/perps-and-jlp/liquidation",
            "https://docs.jup.ag/user-docs/trade/perps-and-jlp/fees",
        ]}),

    Source("kamino_strategy_deep", "protocols", "direct_urls",
        "https://docs.kamino.finance", "kamino", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.5,
        tags=["kamino","automated-liquidity","range","rebalance","concentrated","strategy","yield","vaults"],
        extra={"urls": [
            "https://docs.kamino.finance/products/liquidity-vaults",
            "https://docs.kamino.finance/products/liquidity-vaults/how-it-works",
            "https://docs.kamino.finance/products/liquidity-vaults/strategies",
            "https://docs.kamino.finance/products/multiply",
            "https://docs.kamino.finance/products/multiply/how-it-works",
            "https://docs.kamino.finance/products/multiply/risks",
        ]}),

    # ── THEORETICAL / RESEARCH SOURCES ────────────────────────────────────────

    # Flashbots — MEV theory: sandwich, backrun, JIT, PBS, auction design (sitemap works, 573 chunks)
    Source("flashbots_writings", "education", "sitemap",
        "https://writings.flashbots.net", None, "research", "proprietary-fair-use",
        max_pages=120, crawl_delay=2.0,
        tags=["mev","flashbots","sandwich","backrun","frontrun","jit","pbs","auction","block-building","research"]),

    # Rekt.news — hack post-mortems: oracle manipulation, flash loan attacks, bridge exploits
    Source("rekt_news", "education", "direct_urls",
        "https://rekt.news", None, "security_research", "proprietary-fair-use",
        max_pages=200, crawl_delay=2.5,
        tags=["hack","exploit","security","postmortem","oracle-manipulation","flash-loan","reentrancy","bridge-hack","rug-pull"],
        extra={"urls": [
            "https://rekt.news/leaderboard/",
            # Recent hacks
            "https://rekt.news/wasabi-protocol-rekt",
            "https://rekt.news/volo-rekt",
            "https://rekt.news/kelpdao-rekt",
            "https://rekt.news/drift-protocol-rekt",
            "https://rekt.news/resolv-labs-rekt",
            "https://rekt.news/venus-protocol-rekt4",
            "https://rekt.news/price-impact-kills",
            "https://rekt.news/aave-rekt",
            "https://rekt.news/solv-rekt",
            "https://rekt.news/stack-nobody-checked",
            "https://rekt.news/rhea-finance-rekt",
            "https://rekt.news/hyperbridge-rekt",
            # Major historical post-mortems
            "https://rekt.news/euler-rekt",
            "https://rekt.news/wormhole-rekt",
            "https://rekt.news/ronin-rekt",
            "https://rekt.news/mango-markets-rekt",
            "https://rekt.news/compound-rekt",
            "https://rekt.news/cream-rekt2",
            "https://rekt.news/beanstalk-rekt",
            "https://rekt.news/nomad-rekt",
            "https://rekt.news/transit-swap-rekt",
            "https://rekt.news/harvest-finance-rekt",
            "https://rekt.news/bzx-rekt",
            "https://rekt.news/cover-rekt",
            "https://rekt.news/badger-rekt",
            "https://rekt.news/olympusdao-rekt",
            "https://rekt.news/qbridge-rekt",
            "https://rekt.news/qubit-rekt",
            "https://rekt.news/wintermute-rekt",
            "https://rekt.news/ankr-rekt",
            "https://rekt.news/deus-rekt",
            "https://rekt.news/inverse-rekt2",
            "https://rekt.news/platypus-finance-rekt",
            "https://rekt.news/kyberswap-rekt",
            "https://rekt.news/gamma-strategies-rekt",
            "https://rekt.news/curve-vyper-rekt",
            "https://rekt.news/exactly-rekt",
            "https://rekt.news/heco-htx-rekt",
        ]}),

    # Paradigm — top-tier DeFi mechanism design research (manually verified URLs)
    Source("paradigm_research", "education", "direct_urls",
        "https://www.paradigm.xyz", None, "research", "proprietary-fair-use",
        max_pages=60, crawl_delay=2.0,
        tags=["research","cfmm","amm","twamm","mev","defi","mechanism-design","liquidity","perpetuals","auction"],
        extra={"urls": [
            # AMM theory
            "https://www.paradigm.xyz/2021/07/twamm",
            "https://www.paradigm.xyz/2021/04/understanding-automated-market-makers-part-1-price-impact",
            "https://www.paradigm.xyz/2021/06/uniswap-v3-the-universal-amm",
            "https://www.paradigm.xyz/2021/05/liquidity-mining-on-uniswap-v3",
            "https://www.paradigm.xyz/2022/05/the-dominance-of-uniswap-v3-liquidity",
            "https://www.paradigm.xyz/2024/11/pm-amm",
            "https://www.paradigm.xyz/2024/12/distribution-markets",
            "https://research.paradigm.xyz/uniswaps-alchemy",
            "https://research.paradigm.xyz/amm-price-impact",
            # MEV
            "https://www.paradigm.xyz/2021/02/mev-and-me",
            "https://www.paradigm.xyz/2020/08/ethereum-is-a-dark-forest",
            "https://www.paradigm.xyz/2021/03/ethereum-blockspace-who-gets-what-and-why",
            "https://www.paradigm.xyz/2024/06/priority-is-all-you-need",
            "https://www.paradigm.xyz/2023/04/mev-boost-ethereum-consensus",
            # Derivatives
            "https://www.paradigm.xyz/2021/08/power-perpetuals",
            "https://www.paradigm.xyz/2024/03/everything-is-a-perp",
            "https://www.paradigm.xyz/2025/05/defi-perps-deserve-a-path-forward",
            # Auctions & intents
            "https://www.paradigm.xyz/2022/04/gda",
            "https://www.paradigm.xyz/2022/08/vrgda",
            "https://www.paradigm.xyz/2024/02/leaderless-auctions",
            "https://www.paradigm.xyz/2023/06/intents",
            # Lending
            "https://www.paradigm.xyz/2023/05/blend",
            # Stablecoins & market structure
            "https://www.paradigm.xyz/2026/01/stablecoin-interchange-and-why-it-doesnt-work",
            "https://www.paradigm.xyz/2025/03/tradfi-tomorrow-defi-and-the-rise-of-extensible-finance",
            "https://www.paradigm.xyz/2025/04/the-key-neutrality-of-baselayer-markets",
            "https://www.paradigm.xyz/2020/10/crypto-market-structure-3-0",
        ]}),

    # a16z crypto — stablecoins, tokenomics, on-chain markets (manually verified)
    Source("a16z_crypto_research", "education", "direct_urls",
        "https://a16zcrypto.com", None, "research", "proprietary-fair-use",
        max_pages=20, crawl_delay=2.0,
        tags=["research","tokenomics","stablecoin","defi","web3","on-chain","market-structure"],
        extra={"urls": [
            "https://a16zcrypto.com/posts/article/why-stablecoins-wont-age-well",
            "https://a16zcrypto.com/posts/article/global-finance-stablecoins-new-stack",
            "https://a16zcrypto.com/posts/article/stablecoin-data-charts",
            "https://a16zcrypto.com/posts/article/what-are-perpetual-futures",
            "https://a16zcrypto.com/posts/article/why-wall-street-is-moving-onchain",
            "https://a16zcrypto.com/posts/article/tokenized-securities-broker-dealers-sec-response",
            "https://a16zcrypto.com/posts/article/product-market-fit-3-patterns-working-in-crypto-today",
            "https://a16zcrypto.com/posts/article/arcade-tokens",
        ]}),

    # Multicoin Capital — Solana thesis, DePIN, tokenomics, stablecoins (manually verified)
    Source("multicoin_research", "education", "direct_urls",
        "https://multicoin.capital", None, "research", "proprietary-fair-use",
        max_pages=40, crawl_delay=2.0,
        tags=["research","depin","tokenomics","solana","stablecoin","web3","thesis","value-capture"],
        extra={"urls": [
            # Solana / ecosystem
            "https://multicoin.capital/2025/01/22/the-solana-thesis-internet-capital-markets/",
            "https://multicoin.capital/2025/03/04/jito-asset-report/",
            "https://multicoin.capital/2024/09/10/drift-analysis-and-valuation/",
            # Stablecoins
            "https://multicoin.capital/2026/03/19/rwas-are-just-built-different/",
            "https://multicoin.capital/2025/12/10/specialized-stablecoin-fintechs/",
            "https://multicoin.capital/2025/11/13/ethena-synthetic-dollars-challenge-stablecoin-duopoly/",
            # Tokenomics / value capture
            "https://multicoin.capital/2026/02/10/ace-is-the-place-with-the-helpful-value-capture/",
            "https://multicoin.capital/2026/02/17/adverse-selection-rules-everything-around-me/",
            "https://multicoin.capital/2026/02/06/multicoin-capitals-investment-thesis/",
            "https://multicoin.capital/2024/01/16/what-multicoin-is-excited-about-for-2024/",
            "https://multicoin.capital/2019/04/24/multicoin-investment-thesis/",
            # DePIN
            "https://multicoin.capital/2022/04/05/proof-of-physical-work/",
            "https://multicoin.capital/2021/03/17/the-helium-flywheel/",
            # MEV / order flow
            "https://multicoin.capital/2023/12/14/oracles-and-the-new-frontier-for-application-owned-orderflow-auctions/",
            # Modular vs monolithic
            "https://multicoin.capital/2023/08/15/the-hidden-costs-of-modular-systems/",
            # Cross-chain
            "https://multicoin.capital/2021/02/23/thorchain-analysis/",
            # Web3 stack
            "https://multicoin.capital/2019/12/13/the-web3-stack-2019-edition/",
            # Misc macro
            "https://multicoin.capital/2025/12/12/recapping-cryptos-most-consequential-year/",
        ]}),

    # EigenLayer — restaking and LRT concepts (direct URLs, conceptual pages only)
    Source("eigenlayer_docs", "protocols", "direct_urls",
        "https://docs.eigenlayer.xyz", None, "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["eigenlayer","restaking","avs","lrt","slashing","operator","ethereum","shared-security","economics"],
        extra={"urls": [
            "https://docs.eigenlayer.xyz/overview/",
            "https://docs.eigenlayer.xyz/eigenlayer/overview/key-terms",
            "https://docs.eigenlayer.xyz/risk/risk-faq",
            "https://docs.eigenlayer.xyz/operators/concepts/operator-introduction",
            "https://docs.eigenlayer.xyz/eigenlayer/concepts/operator-sets/strategies-and-magnitudes",
            "https://docs.eigenlayer.xyz/developers/Concepts/avs-security-models",
            "https://docs.eigenlayer.xyz/developers/Concepts/slashing/slashing-overview",
            "https://docs.eigenlayer.xyz/eigenlayer/concepts/slashing/safety-delays-concept",
            "https://docs.eigenlayer.xyz/security/guardrails",
            "https://docs.eigenlayer.xyz/developers/rewards",
        ]}),

    # Ondo Finance — tokenized treasuries, RWA yield (direct URLs, no dev guides)
    Source("ondo_docs", "protocols", "direct_urls",
        "https://docs.ondo.finance", "ondo", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["ondo","rwa","tokenized-treasury","usdy","ousg","real-world-assets","yield","stablecoin"],
        extra={"urls": [
            "https://docs.ondo.finance",
            "https://docs.ondo.finance/general-access-products/usdy/basics",
            "https://docs.ondo.finance/general-access-products/usdy/rebasing",
            "https://docs.ondo.finance/general-access-products/usdy/eligibility",
            "https://docs.ondo.finance/general-access-products/ousg/basics",
            "https://docs.ondo.finance/ondo-global-markets/overview",
            "https://docs.ondo.finance/ondo-global-markets/corporate-actions",
            "https://docs.ondo.finance/audits",
        ]}),

    # Maple Finance — institutional lending, credit markets (sitemap works, 112 chunks OK)
    Source("maple_docs", "protocols", "sitemap",
        "https://docs.maple.finance", "maple", "protocol_documentation", "proprietary-fair-use",
        max_pages=80, crawl_delay=1.5,
        tags=["maple","lending","institutional","credit","rwa","fixed-income","undercollateralized","pool"]),

    # Frax Finance — algorithmic + collateral hybrid stablecoin mechanics (sitemap works, 88 chunks OK)
    Source("frax_docs", "protocols", "sitemap",
        "https://docs.frax.finance", "frax", "protocol_documentation", "proprietary-fair-use",
        max_pages=100, crawl_delay=1.5,
        tags=["frax","algorithmic-stablecoin","frxeth","sfrxeth","amo","collateral","peg-mechanism","fraxlend","seigniorage"]),

    # Helium — DePIN economics, PoC, HNT tokenomics (direct URLs)
    Source("helium_depin", "education", "direct_urls",
        "https://docs.helium.com", "helium", "education", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["helium","depin","iot","wireless","hnt","proof-of-coverage","token-economics","solana","5g"],
        extra={"urls": [
            "https://docs.helium.com/tokens/hnt-token",
            "https://docs.helium.com/tokens/hnt-token/hnt-max-supply",
            "https://docs.helium.com/tokens/mobile-token",
            "https://docs.helium.com/tokens/iot-token",
            "https://docs.helium.com/network-iot/proof-of-coverage",
            "https://docs.helium.com/network-iot/proof-of-coverage/poc-rewards",
            "https://docs.helium.com/solana/",
            "https://docs.helium.com/governance/",
            "https://docs.helium.com/governance/staking-with-helium-vote",
        ]}),

    # Token Terminal — protocol revenue, P/F ratio, TVL, real yield education
    Source("tokenterminal_resources", "education", "direct_urls",
        "https://tokenterminal.com", None, "education", "proprietary-fair-use",
        max_pages=15, crawl_delay=2.0,
        tags=["protocol-revenue","fees","tvl","pe-ratio","real-yield","fdv","token-turnover","analytics"],
        extra={"urls": [
            "https://tokenterminal.com/resources/articles/what-is-protocol-revenue",
            "https://tokenterminal.com/resources/articles/what-is-tvl",
            "https://tokenterminal.com/resources/articles/what-is-token-turnover",
            "https://tokenterminal.com/resources/articles/what-are-fully-diluted-valuations",
            "https://tokenterminal.com/resources/articles/p-f-ratio",
            "https://tokenterminal.com/resources/articles/real-yield",
            "https://tokenterminal.com/resources/articles/defi-metrics",
            "https://tokenterminal.com/resources/articles/blockchain-metrics",
            "https://tokenterminal.com/resources/articles/supply-side-fees",
        ]}),

    # Ethena — delta-neutral synthetic dollar, sUSDe yield, funding risk mechanics
    Source("ethena_docs", "protocols", "direct_urls",
        "https://docs.ethena.fi", "ethena", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["ethena","usde","susde","delta-neutral","synthetic-dollar","funding-rate","stablecoin","yield","backing"],
        extra={"urls": [
            "https://docs.ethena.fi/ethena-overview",
            "https://docs.ethena.fi/solution-overview/usde-overview",
            "https://docs.ethena.fi/solution-overview/usde-overview/delta-neutral-stability",
            "https://docs.ethena.fi/solution-overview/usde-overview/delta-neutral-examples",
            "https://docs.ethena.fi/solution-overview/protocol-revenue-explanation",
            "https://docs.ethena.fi/solution-overview/protocol-revenue-explanation/susde-rewards-mechanism",
            "https://docs.ethena.fi/solution-overview/risks",
            "https://docs.ethena.fi/solution-overview/risks/funding-risk",
            "https://docs.ethena.fi/solution-overview/risks/liquidation-risk",
            "https://docs.ethena.fi/solution-overview/risks/custodial-risk",
            "https://docs.ethena.fi/solution-overview/risks/backing-assets-risk",
            "https://docs.ethena.fi/solution-overview/underlying-derivatives",
            "https://docs.ethena.fi/backing-custody-and-security/overview",
        ]}),

    # Morpho — optimized lending, vault mechanics, liquidation, IRM (Interest Rate Model)
    Source("morpho_docs", "protocols", "direct_urls",
        "https://docs.morpho.org", "morpho", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["morpho","lending","optimization","vault","liquidation","irm","interest-rate","curator","market"],
        extra={"urls": [
            "https://docs.morpho.org/get-started/products/",
            "https://docs.morpho.org/get-started/use-cases/",
            "https://docs.morpho.org/learn/concepts/market/",
            "https://docs.morpho.org/learn/concepts/vault/",
            "https://docs.morpho.org/learn/concepts/liquidation/",
            "https://docs.morpho.org/learn/concepts/oracle/",
            "https://docs.morpho.org/learn/concepts/irm/",
            "https://docs.morpho.org/learn/concepts/curator/",
            "https://docs.morpho.org/learn/concepts/flashloans/",
            "https://docs.morpho.org/learn/concepts/public-allocator/",
            "https://docs.morpho.org/build/borrow/concepts/market-mechanics/",
            "https://docs.morpho.org/build/earn/concepts/vault-mechanics/",
        ]}),

    # Liquity v2 — BOLD CDP stablecoin, stability pool, redemptions, collateral mechanics
    Source("liquity_docs", "protocols", "direct_urls",
        "https://docs.liquity.org", "liquity", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["liquity","bold","lusd","cdp","stability-pool","redemption","collateral","stablecoin","liquidation"],
        extra={"urls": [
            "https://docs.liquity.org",
            "https://docs.liquity.org/v2-faq/general",
            "https://docs.liquity.org/v2-faq/bold-and-earn",
            "https://docs.liquity.org/v2-faq/borrowing-and-liquidations",
            "https://docs.liquity.org/v2-faq/redemptions-and-delegation",
            "https://docs.liquity.org/v2-faq/lqty-staking",
            "https://docs.liquity.org/v2-documentation/risk-disclosure",
        ]}),

    # dYdX v4 — perpetuals: funding rates, liquidations, margin, isolated markets
    Source("dydx_docs", "protocols", "direct_urls",
        "https://docs.dydx.exchange", "dydx", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["dydx","perpetuals","funding-rate","liquidation","margin","open-interest","perps","derivatives"],
        extra={"urls": [
            "https://docs.dydx.xyz/concepts/trading/funding",
            "https://docs.dydx.xyz/concepts/trading/liquidations",
            "https://docs.dydx.xyz/concepts/trading/margin",
            "https://docs.dydx.xyz/concepts/trading/accounts",
            "https://docs.dydx.xyz/concepts/trading/contract-loss-mechanism",
            "https://docs.dydx.xyz/concepts/trading/isolated-markets",
            "https://docs.dydx.xyz/concepts/trading/isolated-positions",
            "https://docs.dydx.xyz/concepts/trading/oracle",
            "https://docs.dydx.xyz/concepts/onboarding-faqs",
        ]}),

    # Pendle — deeper yield tokenization: SY, PT, YT minting, AMM, vePENDLE, Boros IR trading
    Source("pendle_deeper", "protocols", "direct_urls",
        "https://docs.pendle.finance", "pendle", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["pendle","yield-tokenization","pt","yt","sy","fixed-yield","amm","vependle","interest-rate","boros"],
        extra={"urls": [
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Glossary",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/SY",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/Minting",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/LiquidityEngines/AMM",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Mechanisms/Fees",
            "https://docs.pendle.finance/boros-docs/Introduction",
            "https://docs.pendle.finance/boros-docs/interest-rate-trading/interest-rate-trading-yu-trading",
            "https://docs.pendle.finance/boros-docs/interest-rate-trading/vaults",
            "https://docs.pendle.finance/boros-docs/risk-parameters/margin-and-liquidations/README",
        ]}),

    # Balancer — weighted pools, stable pools, boosted pools, veBAL gauge voting, protocol fees
    Source("balancer_docs", "protocols", "direct_urls",
        "https://docs.balancer.fi", "balancer", "protocol_documentation", "proprietary-fair-use",
        max_pages=25, crawl_delay=1.5,
        tags=["balancer","weighted-pool","stable-pool","boosted-pool","vebal","gauge","amm","liquidity","fees","governance"],
        extra={"urls": [
            "https://docs.balancer.fi/concepts/explore-available-balancer-pools/weighted-pool/weighted-pool.html",
            "https://docs.balancer.fi/concepts/explore-available-balancer-pools/weighted-pool/80-20-pool.html",
            "https://docs.balancer.fi/concepts/explore-available-balancer-pools/stable-pool/stable-pool.html",
            "https://docs.balancer.fi/concepts/explore-available-balancer-pools/boosted-pool.html",
            "https://docs.balancer.fi/concepts/governance/veBAL/",
            "https://docs.balancer.fi/concepts/governance/veBAL/FAQ.html",
            "https://docs.balancer.fi/concepts/vebal-and-gauges/vebal.html",
            "https://docs.balancer.fi/concepts/protocol-fee-model/protocol-fee-model.html",
            "https://docs.balancer.fi/concepts/core-concepts/balancer-pool-tokens.html",
            "https://docs.balancer.fi/concepts/governance/protocol-fees.html",
            "https://docs.balancer.fi/concepts/vault/swap-fee.html",
        ]}),

    # Velodrome — ve(3,3) model, bribes, gauges, VELO emissions, LP voting (GitHub docs)
    Source("velodrome_docs", "education", "github_raw",
        "https://raw.githubusercontent.com/velodrome-finance/docs/main/README.md",
        "velodrome", "protocol_documentation", "proprietary-fair-use",
        max_pages=10, crawl_delay=1.5,
        tags=["velodrome","ve33","ve-tokenomics","bribes","gauges","velo","emissions","lp","voting","optimism"],
        extra={"single_file": True}),

    # Convex Finance — vlCVX, cvxCRV, gauge vote aggregation, Curve Wars mechanics
    Source("convex_finance_docs", "protocols", "direct_urls",
        "https://docs.convexfinance.com", "convex", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["convex","vlcvx","cvxcrv","curve-wars","gauge","vote-aggregation","bribe","crv","lp-staking"],
        extra={"urls": [
            "https://docs.convexfinance.com/convexfinance/general-information/understanding-cvx",
            "https://docs.convexfinance.com/convexfinance/general-information/understanding-cvxcrv",
            "https://docs.convexfinance.com/convexfinance/general-information/voting-and-gauge-weights",
            "https://docs.convexfinance.com/convexfinance/general-information/voting-and-gauge-weights/vote-locking",
            "https://docs.convexfinance.com/convexfinance/general-information/voting-and-gauge-weights/cvx-vote-delegation",
            "https://docs.convexfinance.com/convexfinanceintegration/cvx-locking-vlcvx",
        ]}),

    # Synthetix — synthetic assets, SNX debt pool, perps V3, collateralization
    Source("synthetix_docs", "protocols", "direct_urls",
        "https://docs.synthetix.io", "synthetix", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["synthetix","snx","synthetic-assets","debt-pool","perps","collateral","stablecoin","susd","v3"],
        extra={"urls": [
            "https://docs.synthetix.io/user-docs/v2-user-docs/synthetix-protocol/the-synthetix-protocol/synthetix-litepaper",
            "https://docs.synthetix.io/user-docs/v2-user-docs/synthetix-protocol/the-synthetix-protocol/the-role-of-stakers",
            "https://docs.synthetix.io/user-docs/v2-user-docs/synthetix-protocol/the-synthetix-protocol/synthetix-token-snx",
            "https://docs.synthetix.io/user-docs/v2-user-docs/staking/staking-guide/collateralization-ratio",
            "https://docs.synthetix.io/staking",
        ]}),


    # Rocket Pool — decentralized ETH liquid staking, rETH, minipool operators, smoothing pool
    Source("rocketpool_docs", "protocols", "direct_urls",
        "https://docs.rocketpool.net", "rocketpool", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["rocketpool","reth","minipool","eth-staking","liquid-staking","decentralized","smoothing-pool","commission"],
        extra={"urls": [
            "https://docs.rocketpool.net/overview/faq.html",
            "https://docs.rocketpool.net/overview/glossary.html",
            "https://docs.rocketpool.net/guides/staking/overview.html",
            "https://docs.rocketpool.net/liquid-staking/via-rp",
            "https://docs.rocketpool.net/guides/node/fee-distrib-sp",
        ]}),

    # Spark Protocol — SparkLend (MakerDAO lending), sDAI yield, DSR mechanics
    Source("spark_docs", "protocols", "direct_urls",
        "https://docs.spark.fi", "spark", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["spark","sparklend","sdai","dai","makerdao","dsr","lending","savings","yield","rwa"],
        extra={"urls": [
            "https://docs.spark.fi/defi-infrastructure/sparklend",
            "https://docs.spark.fi/user-guides/earning-savings/sdai",
            "https://docs.spark.fi/user-guides/earning-savings",
            "https://docs.spark.fi/user-guides/using-sparklend/borrowing-assets",
        ]}),

    # Aave V3 deeper — eMode (efficiency mode), GHO stablecoin, isolation mode
    Source("aave_v3_deeper", "protocols", "direct_urls",
        "https://aave.com/docs", "aave", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.5,
        tags=["aave","emode","efficiency-mode","isolation-mode","gho","stablecoin","v3","cross-chain","portal"],
        extra={"urls": [
            "https://aave.com/docs/aave-v3/overview",
            "https://aave.com/docs/developers/aave-v3/markets/advanced",
            "https://aave.com/docs/ecosystem/gho",
            "https://aave.com/docs/aave-v3/guides/sgho",
        ]}),

    # GMX V2 — GM isolated pools, synthetic perps, GLV vaults, V2 LP mechanics
    Source("gmx_v2_docs", "protocols", "direct_urls",
        "https://docs.gmx.io", "gmx", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.5,
        tags=["gmx","v2","gm-pools","synthetic-perps","glv","isolated-markets","liquidity","perps","fees"],
        extra={"urls": [
            "https://docs.gmx.io/docs/intro/",
            "https://docs.gmx.io/docs/trading/v2/",
            "https://docs.gmx.io/docs/providing-liquidity/v2/",
            "https://docs.gmx.io/docs/tokenomics/rewards/",
            "https://docs.gmx.io/docs/providing-liquidity/v1/",
        ]}),

    # Solana Token Extensions (Token-2022) — confidential transfers, transfer fees, interest-bearing,
    # pausable tokens, transfer hooks, permanent delegate, non-transferable — new Solana token standard
    Source("solana_token_extensions", "solana", "direct_urls",
        "https://www.solana-program.com", "solana", "protocol_documentation", "mit",
        max_pages=10, crawl_delay=1.0,
        tags=["token-2022","token-extensions","spl","confidential-transfer","transfer-fee",
              "interest-bearing","pausable","transfer-hook","non-transferable","solana"],
        extra={"urls": [
            "https://www.solana-program.com/docs/token-2022",
            "https://www.solana-program.com/docs/token-2022/extensions",
            "https://www.solana-program.com/docs/token-2022/onchain",
        ]}),

    # Uniswap V4 — hooks architecture, singleton contract, flash accounting, ERC-6909,
    # dynamic fees, custom curves — paradigm shift from V3 position-NFTs
    Source("uniswap_v4_docs", "protocols", "sitemap",
        "https://developers.uniswap.org", "uniswap", "protocol_documentation", "proprietary-fair-use",
        max_pages=60, crawl_delay=1.0,
        tags=["uniswap","v4","hooks","singleton","flash-accounting","erc-6909",
              "dynamic-fees","custom-curves","amm","liquidity","ethereum"],
        extra={
            "include_patterns": [r"/docs/protocols/v4/", r"/docs/contracts/v4/"],
            "exclude_patterns": [r"/sdk/", r"/api/", r"/subgraph/"],
        }),

    # Celestia — modular blockchain, data availability layer, DAS (data availability sampling),
    # namespaced Merkle trees, Reed-Solomon encoding, sovereign rollups
    Source("celestia_learn", "education", "sitemap",
        "https://celestia.org", "celestia", "blockchain_concepts", "cc-by",
        max_pages=30, crawl_delay=1.0,
        tags=["celestia","modular-blockchain","data-availability","das","rollups",
              "namespaced-merkle","reed-solomon","sovereign-rollups","blockchain-theory"]),

    # Across Protocol — intent-based cross-chain bridging, UMA optimistic oracle,
    # relayers, canonical bridges, CCTP integration, fastest bridge architecture
    Source("across_docs", "bridges", "sitemap",
        "https://docs.across.to", "across", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["across","bridging","cross-chain","intents","uma","optimistic-oracle",
              "relayer","cctp","canonical-bridge","fast-bridge"]),

    # ── ZK & L2 Scaling ────────────────────────────────────────────────────────

    # zkSync Era — zkEVM, zero-knowledge proofs, account abstraction, native AA,
    # Boojum STARK prover, paymasters, EIP-4337, zkPorter data availability
    Source("zksync_era_docs", "blockchain", "sitemap",
        "https://docs.zksync.io", "zksync", "blockchain_concepts", "mit",
        max_pages=60, crawl_delay=1.0,
        tags=["zksync","zkevm","zk-rollup","account-abstraction","boojum","stark",
              "paymaster","eip-4337","l2","scaling","zero-knowledge"]),

    # Optimism — OP Stack, optimistic rollups, fault proofs, superchain, Bedrock,
    # sequencer, cannon, dispute game, cross-domain messaging
    Source("optimism_docs", "blockchain", "sitemap",
        "https://docs.optimism.io", "optimism", "blockchain_concepts", "mit",
        max_pages=70, crawl_delay=1.0,
        tags=["optimism","op-stack","optimistic-rollup","fault-proof","superchain",
              "bedrock","sequencer","cannon","l2","scaling","cross-domain"]),

    # StarkNet — STARKs, Cairo VM, validity rollups, ZK scalability, SNARK vs STARK,
    # Pedersen hash, STARK proofs, felt252, Sierra, account abstraction on StarkNet
    Source("starknet_docs", "blockchain", "direct_urls",
        "https://docs.starknet.io", "starknet", "blockchain_concepts", "mit",
        max_pages=30, crawl_delay=1.0,
        tags=["starknet","cairo","stark","zk-rollup","validity-rollup","sierra",
              "felt252","account-abstraction","pedersen","l2","scaling","sequencer","prover"],
        extra={"urls": [
            "https://docs.starknet.io/learn/intro.md",
            "https://docs.starknet.io/learn/protocol/intro.md",
            "https://docs.starknet.io/learn/protocol/accounts.md",
            "https://docs.starknet.io/learn/protocol/transactions.md",
            "https://docs.starknet.io/learn/protocol/fees.md",
            "https://docs.starknet.io/learn/protocol/messaging.md",
            "https://docs.starknet.io/learn/protocol/cryptography.md",
            "https://docs.starknet.io/learn/protocol/data-availability.md",
            "https://docs.starknet.io/learn/protocol/blocks.md",
            "https://docs.starknet.io/learn/protocol/state.md",
            "https://docs.starknet.io/learn/protocol/staking.md",
            "https://docs.starknet.io/learn/cheatsheets/transactions-reference.md",
            "https://docs.starknet.io/learn/cheatsheets/messaging-reference.md",
            "https://docs.starknet.io/learn/cheatsheets/chain-info.md",
            "https://docs.starknet.io/learn/S-two-book/introduction.md",
        ]}),

    # StarkWare research blog — STARK vs SNARK, FRI protocol, algebraic intermediate
    # representation (AIR), recursive STARKs, Cairo language design, Vitalik collab papers
    Source("starkware_blog", "education", "sitemap",
        "https://starkware.co", "starkware", "research", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.5,
        tags=["starkware","stark","snark","fri","air","recursive-proofs","cairo",
              "zero-knowledge","cryptography","research","scalability"]),

    # Aztec Protocol — ZK privacy on Ethereum, PLONK proof system, private state,
    # note commitment trees, nullifiers, private smart contracts, Noir language
    Source("aztec_docs", "blockchain", "sitemap",
        "https://docs.aztec.network", "aztec", "blockchain_concepts", "apache-2.0",
        max_pages=60, crawl_delay=1.0,
        tags=["aztec","zk-privacy","plonk","noir","private-transactions","nullifier",
              "note-commitment","private-state","zero-knowledge","l2","ethereum"]),

    # Polygon zkEVM — type-2 zkEVM equivalence, zkProver, polynomial identity language,
    # PIL, eSTARK, Hermez, zkNode, bridge and messaging, CDK rollup framework
    Source("polygon_zkevm_docs", "blockchain", "sitemap",
        "https://docs.polygon.technology", "polygon", "blockchain_concepts", "mit",
        max_pages=60, crawl_delay=1.0,
        tags=["polygon","zkevm","zk-rollup","zkprover","pil","estark","hermez",
              "cdk","validium","l2","scaling","evm-equivalence"],
        extra={
            "include_patterns": [r"/zkEVM/", r"/cdk/", r"/pos/"],
            "exclude_patterns": [r"/miden/", r"/api/", r"/nightfall/"],
        }),

    # Base — Coinbase L2, OP Stack fork, EIP-4337 account abstraction, OnchainKit,
    # smart wallet, paymaster, gas sponsorship, Solana bridge
    Source("base_docs", "blockchain", "sitemap",
        "https://docs.base.org", "base", "blockchain_concepts", "mit",
        max_pages=60, crawl_delay=1.0,
        tags=["base","coinbase","op-stack","l2","account-abstraction","onchainkit",
              "smart-wallet","paymaster","eip-4337","solana-bridge","scaling"]),

    # Trader Joe / LFJ — Liquidity Book AMM, bin liquidity model, active bin,
    # composition fee, bin step, uniform/exponential/curve distributions,
    # auto-pools, limit orders, DCA orders on Avalanche/Arbitrum/BNB
    Source("traderjoe_docs", "protocols", "sitemap",
        "https://docs.lfj.gg", "traderjoe", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["traderjoe","lfj","liquidity-book","bin-amm","active-bin","composition-fee",
              "bin-step","limit-orders","dca","avalanche","arbitrum","amm","liquidity"]),

    # Stargate Finance — omnichain fungible token (OFT) standard, unified liquidity,
    # delta algorithm, cross-chain composability, LayerZero V2 messaging, veSTG
    Source("stargate_docs", "bridges", "direct_urls",
        "https://docs.stargate.finance", "stargate", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.0,
        tags=["stargate","oft","omnichain","layerzero","delta-algorithm","cross-chain",
              "unified-liquidity","composability","vestg","bridge"],
        extra={"urls": [
            "https://docs.stargate.finance",
            "https://docs.stargate.finance/introduction/architecture",
            "https://docs.stargate.finance/introduction/why-stargate",
            "https://docs.stargate.finance/stargate-products/oft",
            "https://docs.stargate.finance/stargate-products/pool",
            "https://docs.stargate.finance/stargate-products/bus",
            "https://docs.stargate.finance/stargate-products/taxi",
            "https://docs.stargate.finance/stargate-products/tokenomics",
        ]}),

    # ── Vitalik's Blog ─────────────────────────────────────────────────────────
    # Vitalik Buterin's essays — ZK proofs (STARKs/SNARKs/PLONK/FRI/GKR/Binius),
    # Ethereum roadmap, rollups, sharding, PoS, L2 types, endgame, stablecoins,
    # plasma, verkle trees, account abstraction, MEV, DeFi, prediction markets
    Source("vitalik_blog", "education", "direct_urls",
        "https://vitalik.eth.limo", "ethereum", "research", "cc-by",
        max_pages=80, crawl_delay=1.0,
        tags=["vitalik","ethereum","zk-proofs","stark","snark","plonk","rollups",
              "sharding","pos","l2","endgame","stablecoins","plasma","verkle",
              "account-abstraction","defi","research","cryptography"],
        extra={"urls": [
            # ZK Cryptography deep dives
            "https://vitalik.eth.limo/general/2025/10/19/gkr.html",
            "https://vitalik.eth.limo/general/2024/07/23/circlestarks.html",
            "https://vitalik.eth.limo/general/2024/04/29/binius.html",
            "https://vitalik.eth.limo/general/2023/06/20/deeperdive.html",
            "https://vitalik.eth.limo/general/2022/08/04/zkevm.html",
            "https://vitalik.eth.limo/general/2022/06/15/using_snarks.html",
            "https://vitalik.eth.limo/general/2022/03/14/trustedsetup.html",
            "https://vitalik.eth.limo/general/2021/11/05/halo.html",
            "https://vitalik.eth.limo/general/2021/01/26/snarks.html",
            "https://vitalik.eth.limo/general/2020/07/20/homomorphic.html",
            "https://vitalik.eth.limo/general/2020/03/21/garbled.html",
            "https://vitalik.eth.limo/general/2019/09/22/plonk.html",
            "https://vitalik.eth.limo/general/2019/05/12/fft.html",
            "https://vitalik.eth.limo/general/2018/07/21/starks_part_3.html",
            "https://vitalik.eth.limo/general/2017/11/22/starks_part_2.html",
            "https://vitalik.eth.limo/general/2017/11/09/starks_part_1.html",
            "https://vitalik.eth.limo/general/2017/02/01/zk_snarks.html",
            "https://vitalik.eth.limo/general/2017/01/14/exploring_ecp.html",
            "https://vitalik.eth.limo/general/2016/12/10/qap.html",
            # L2 / Rollups / Scaling
            "https://vitalik.eth.limo/general/2025/05/06/stages.html",
            "https://vitalik.eth.limo/general/2025/05/03/simplel1.html",
            "https://vitalik.eth.limo/general/2025/02/14/l1scaling.html",
            "https://vitalik.eth.limo/general/2025/01/23/l1l2future.html",
            "https://vitalik.eth.limo/general/2024/09/02/gluecp.html",
            "https://vitalik.eth.limo/general/2024/06/30/epochslot.html",
            "https://vitalik.eth.limo/general/2024/05/31/blocksize.html",
            "https://vitalik.eth.limo/general/2024/05/29/l2culture.html",
            "https://vitalik.eth.limo/general/2024/05/23/l2exec.html",
            "https://vitalik.eth.limo/general/2024/05/09/multidim.html",
            "https://vitalik.eth.limo/general/2024/03/28/blobs.html",
            "https://vitalik.eth.limo/general/2023/11/14/neoplasma.html",
            "https://vitalik.eth.limo/general/2023/10/31/l2types.html",
            "https://vitalik.eth.limo/general/2023/09/30/enshrinement.html",
            "https://vitalik.eth.limo/general/2023/03/31/zkmulticlient.html",
            "https://vitalik.eth.limo/general/2022/09/17/layer_3.html",
            "https://vitalik.eth.limo/general/2021/12/06/endgame.html",
            "https://vitalik.eth.limo/general/2021/05/23/scaling.html",
            "https://vitalik.eth.limo/general/2021/04/07/sharding.html",
            "https://vitalik.eth.limo/general/2021/01/05/rollup.html",
            "https://vitalik.eth.limo/general/2019/08/28/hybrid_layer_2.html",
            "https://vitalik.eth.limo/general/2019/06/12/plasma_vs_sharding.html",
            "https://vitalik.eth.limo/general/2018/08/26/layer_1.html",
            "https://vitalik.eth.limo/general/2017/12/31/sharding_faq.html",
            # Ethereum Futures series (6 parts)
            "https://vitalik.eth.limo/general/2024/10/14/futures1.html",
            "https://vitalik.eth.limo/general/2024/10/17/futures2.html",
            "https://vitalik.eth.limo/general/2024/10/20/futures3.html",
            "https://vitalik.eth.limo/general/2024/10/23/futures4.html",
            "https://vitalik.eth.limo/general/2024/10/26/futures5.html",
            "https://vitalik.eth.limo/general/2024/10/29/futures6.html",
            # Ethereum PoS / Consensus
            "https://vitalik.eth.limo/general/2025/06/28/zkid.html",
            "https://vitalik.eth.limo/general/2025/04/14/privacy.html",
            "https://vitalik.eth.limo/general/2022/03/29/road.html",
            "https://vitalik.eth.limo/general/2021/06/18/verkle.html",
            "https://vitalik.eth.limo/general/2021/09/26/limits.html",
            "https://vitalik.eth.limo/general/2020/11/06/pos2020.html",
            "https://vitalik.eth.limo/general/2018/11/25/central_planning.html",
            "https://vitalik.eth.limo/general/2018/08/07/99_fault_tolerant.html",
            "https://vitalik.eth.limo/general/2018/12/05/cbc_casper.html",
            "https://vitalik.eth.limo/general/2017/12/31/pos_faq.html",
            "https://vitalik.eth.limo/general/2016/12/29/pos_design.html",
            # DeFi / Stablecoins / MEV
            "https://vitalik.eth.limo/general/2025/09/21/low_risk_defi.html",
            "https://vitalik.eth.limo/general/2024/11/09/infofinance.html",
            "https://vitalik.eth.limo/general/2023/06/09/three_transitions.html",
            "https://vitalik.eth.limo/general/2023/05/21/dont_overload.html",
            "https://vitalik.eth.limo/general/2023/01/20/stealth.html",
            "https://vitalik.eth.limo/general/2022/11/19/proof_of_solvency.html",
            "https://vitalik.eth.limo/general/2022/05/25/stable.html",
            "https://vitalik.eth.limo/general/2021/03/23/legitimacy.html",
            "https://vitalik.eth.limo/general/2020/09/11/coordination.html",
            "https://vitalik.eth.limo/general/2017/06/22/marketmakers.html",
            "https://vitalik.eth.limo/general/2017/10/17/moe.html",
            # Wallets / Account Abstraction
            "https://vitalik.eth.limo/general/2024/12/03/wallets.html",
            "https://vitalik.eth.limo/general/2022/01/26/soulbound.html",
            "https://vitalik.eth.limo/general/2021/01/11/recovery.html",
            # Governance / DAOs
            "https://vitalik.eth.limo/general/2022/09/20/daos.html",
            "https://vitalik.eth.limo/general/2019/04/03/collusion.html",
            "https://vitalik.eth.limo/general/2019/12/07/quadratic.html",
        ]}),

    # ── ZK Proving Systems ─────────────────────────────────────────────────────

    # RISC Zero — ZKVM (zero-knowledge virtual machine), STARK-based general computation,
    # Bonsai proving network, R0VM, zkEVM, continuations, recursion, receipt verification
    Source("risczero_docs", "education", "sitemap",
        "https://dev.risczero.com", "risczero", "research", "apache-2.0",
        max_pages=50, crawl_delay=1.0,
        tags=["risczero","zkvm","stark","bonsai","r0vm","general-computation",
              "zero-knowledge","recursion","receipt","proof-system"]),

    # SP1 / Succinct — fastest zkVM, PLONK+FRI backend, precompiles, SP1 prover network,
    # proof aggregation, Groth16 wrapping, recursive proofs, EVM verification
    Source("sp1_docs", "education", "sitemap",
        "https://docs.succinct.xyz", "succinct", "research", "mit",
        max_pages=50, crawl_delay=1.0,
        tags=["sp1","succinct","zkvm","plonk","fri","groth16","recursive-proofs",
              "proof-aggregation","evm-verification","zero-knowledge","precompiles"]),

    # ── New L1 Blockchains ──────────────────────────────────────────────────────

    # Cosmos / IBC — Tendermint BFT consensus, IBC protocol, app-chains, Cosmos SDK,
    # interchain accounts, ICS standards, light clients, packet lifecycle
    Source("cosmos_docs", "blockchain", "sitemap",
        "https://docs.cosmos.network", "cosmos", "blockchain_concepts", "apache-2.0",
        max_pages=60, crawl_delay=1.0,
        tags=["cosmos","ibc","tendermint","app-chain","sdk","interchain","ics",
              "light-client","packet","consensus","staking","governance"],
        extra={
            "exclude_patterns": [r"/api/", r"/swagger/", r"/proto/"],
        }),

    # Avalanche — Snowman consensus, subnets, C-Chain/P-Chain/X-Chain, Avalanche VMs,
    # HyperSDK, Warp messaging, cross-subnet communication, Avalanche L1s
    Source("avalanche_docs", "blockchain", "direct_urls",
        "https://build.avax.network", "avalanche", "blockchain_concepts", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.0,
        tags=["avalanche","snowman","consensus","subnet","c-chain","p-chain","x-chain",
              "hypervm","warp","cross-subnet","l1","avax"],
        extra={"urls": [
            "https://build.avax.network/docs/avalanche-l1s",
            "https://build.avax.network/docs/primary-network",
            "https://build.avax.network/docs/nodes",
            "https://build.avax.network/docs/dapps",
            "https://build.avax.network/docs/tooling",
            "https://build.avax.network/docs/virtual-machines",
        ]}),

    # Aptos — Move VM, parallel execution (Block-STM), AptosBFT consensus,
    # resources vs objects, Move safety guarantees, randomness, keyless accounts
    Source("aptos_docs", "blockchain", "direct_urls",
        "https://aptos.dev", "aptos", "blockchain_concepts", "apache-2.0",
        max_pages=20, crawl_delay=1.0,
        tags=["aptos","move-vm","block-stm","parallel-execution","aptsbft",
              "resources","objects","keyless","randomness","consensus"],
        extra={"urls": [
            "https://aptos.dev/en/network/blockchain/blockchain-deep-dive",
            "https://aptos.dev/en/network/blockchain/move",
            "https://aptos.dev/en/network/blockchain/accounts",
            "https://aptos.dev/en/network/blockchain/resources",
            "https://aptos.dev/en/network/blockchain/txns-states",
            "https://aptos.dev/en/network/blockchain/gas-txn-fee",
            "https://aptos.dev/en/network/blockchain/staking",
            "https://aptos.dev/en/build/smart-contracts",
        ]}),

    # Sui — object-centric model, Move on Sui, Narwhal/Bullshark DAG consensus,
    # parallel transaction execution, shared vs owned objects, zkLogin, sponsored tx
    Source("sui_docs", "blockchain", "sitemap",
        "https://docs.sui.io", "sui", "blockchain_concepts", "apache-2.0",
        max_pages=60, crawl_delay=1.0,
        tags=["sui","move","narwhal","bullshark","dag","parallel-execution",
              "object-model","zklogin","sponsored-tx","owned-objects","shared-objects"],
        extra={
            "exclude_patterns": [r"/references/", r"/sdk/", r"/api/"],
        }),

    # TON Blockchain — TON Virtual Machine (TVM), actor model, sharding, masterchain,
    # workchain, Jetton standard, TON DNS, TON storage, TON Connect, Fift/FunC/Tolk
    Source("ton_docs", "blockchain", "sitemap",
        "https://docs.ton.org", "ton", "blockchain_concepts", "cc-by",
        max_pages=60, crawl_delay=1.0,
        tags=["ton","tvm","actor-model","sharding","masterchain","jetton",
              "ton-dns","fift","func","tolk","ton-connect","smart-contracts"],
        extra={
            "exclude_patterns": [r"/api/", r"/tma/"],
        }),

    # ── Missing DeFi Protocols ──────────────────────────────────────────────────

    # Notional Finance — fixed-rate lending/borrowing on Ethereum, fCash instruments,
    # yield curve, variable rate vaults, nTokens, leveraged yield farming with Pendle
    Source("notional_docs", "protocols", "direct_urls",
        "https://docs.notional.finance", "notional", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.0,
        tags=["notional","fixed-rate","lending","borrowing","fcash","yield-curve",
              "ntoken","variable-rate","leveraged-yield","pendle","ethereum"],
        extra={"urls": [
            "https://docs.notional.finance/notional-v3/product-guides/lending",
            "https://docs.notional.finance/notional-v3/product-guides/fixed-rate-lending",
            "https://docs.notional.finance/notional-v3/product-guides/borrowing",
            "https://docs.notional.finance/notional-v3/product-guides/fixed-rate-borrowing",
            "https://docs.notional.finance/notional-v3/product-guides/providing-liquidity",
            "https://docs.notional.finance/notional-v3/product-guides/leveraged-liquidity",
            "https://docs.notional.finance/notional-v3/product-guides/leveraged-yield-farming",
            "https://docs.notional.finance/notional-v3/product-guides/leveraged-pendle-pts",
        ]}),

    # Silo Finance — isolated lending markets, non-custodial, any asset as collateral,
    # bad debt isolation, SiloDAO, Silo V2 bridge assets, protected collateral
    Source("silo_docs", "protocols", "sitemap",
        "https://docs.silo.finance", "silo", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["silo","isolated-lending","bad-debt-isolation","collateral","siloDAO",
              "protected-collateral","bridge-asset","lending","ethereum"]),

    # Euler Finance — modular vault architecture (EVC), sub-accounts, liability management,
    # nested vaults, interest rate models, liquidation, oracle risk, governance
    Source("euler_docs", "protocols", "direct_urls",
        "https://docs.euler.finance", "euler", "protocol_documentation", "proprietary-fair-use",
        max_pages=10, crawl_delay=1.0,
        tags=["euler","evc","modular-vault","sub-accounts","liability","liquidation",
              "interest-rate","oracle","governance","ethereum","lending"],
        extra={"urls": [
            "https://docs.euler.finance/llms.txt",
            "https://docs.euler.finance",
        ]}),

    # ── Cross-chain & Oracles ───────────────────────────────────────────────────

    # Axelar — cross-chain messaging, general message passing (GMP), amplifier,
    # verifier network, relayer economics, Axelar Virtual Machine (AVM)
    Source("axelar_docs", "bridges", "direct_urls",
        "https://docs.axelar.dev", "axelar", "protocol_documentation", "proprietary-fair-use",
        max_pages=20, crawl_delay=1.0,
        tags=["axelar","gmp","cross-chain","amplifier","verifier","relayer",
              "avm","interoperability","messaging","bridge"],
        extra={"urls": [
            "https://docs.axelar.dev/learn/network/flow/",
            "https://docs.axelar.dev/learn/security/",
            "https://docs.axelar.dev/learn/cli/",
            "https://docs.axelar.dev/dev/intro/",
        ]}),

    # Hyperlane — permissionless interoperability, modular security (ISM), warp routes,
    # validators, relayers, hooks, Mailbox contract, sovereign consensus
    Source("hyperlane_docs", "bridges", "sitemap",
        "https://docs.hyperlane.xyz", "hyperlane", "protocol_documentation", "apache-2.0",
        max_pages=60, crawl_delay=1.0,
        tags=["hyperlane","ism","warp-route","validator","relayer","hook",
              "mailbox","sovereign-consensus","interoperability","permissionless"]),

    # UMA Protocol — optimistic oracle, dispute resolution, Data Verification Mechanism (DVM),
    # OVAL oracle extraction value, KPI options, Across uses UMA for disputes
    Source("uma_docs", "data", "sitemap",
        "https://docs.uma.xyz", "uma", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["uma","optimistic-oracle","dvm","dispute-resolution","oval","kpi-options",
              "oracle","verification","across","ethereum"]),

    # Babylon — Bitcoin staking on other PoS chains, BTC timestamping,
    # finality provider, EOTS (extractable one-time signatures), staking contract
    Source("babylon_docs", "protocols", "sitemap",
        "https://docs.babylonlabs.io", "babylon", "blockchain_concepts", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["babylon","bitcoin-staking","pos","timestamping","finality-provider",
              "eots","staking","btc","security","slashing"],
        extra={
            "exclude_patterns": [r"/api/", r"/grpc/", r"/cli/"],
        }),

    # ── Ethereum EIPs & Consensus ───────────────────────────────────────────────

    # Key Ethereum EIPs — EIP-1559 (fee burn), EIP-4844 (blobs/proto-danksharding),
    # EIP-4337 (account abstraction), EIP-7702 (Pectra EOA code), EIP-3675 (PoS merge),
    # EIP-4895 (staking withdrawals), EIP-2718 (typed txs), EIP-2930 (access lists)
    Source("ethereum_eips", "blockchain", "direct_urls",
        "https://eips.ethereum.org", "ethereum", "blockchain_concepts", "cc0",
        max_pages=15, crawl_delay=1.0,
        tags=["ethereum","eip","eip-1559","eip-4844","eip-4337","eip-7702",
              "fee-burn","blobs","account-abstraction","pectra","typed-transactions"],
        extra={"urls": [
            "https://eips.ethereum.org/EIPS/eip-1559",
            "https://eips.ethereum.org/EIPS/eip-4844",
            "https://eips.ethereum.org/EIPS/eip-4337",
            "https://eips.ethereum.org/EIPS/eip-7702",
            "https://eips.ethereum.org/EIPS/eip-3675",
            "https://eips.ethereum.org/EIPS/eip-4895",
            "https://eips.ethereum.org/EIPS/eip-2718",
            "https://eips.ethereum.org/EIPS/eip-2930",
        ]}),

    # Ethereum PoS consensus layer — validators, attestations, committees, epochs,
    # slots, finality, Casper FFG, LMD-GHOST, slashing, staking withdrawals
    Source("ethereum_consensus_docs", "blockchain", "direct_urls",
        "https://ethereum.org", "ethereum", "blockchain_concepts", "cc-by",
        max_pages=15, crawl_delay=1.0,
        tags=["ethereum","pos","validator","attestation","committee","epoch","slot",
              "finality","casper","lmd-ghost","slashing","staking","beacon-chain"],
        extra={"urls": [
            "https://ethereum.org/en/developers/docs/consensus-mechanisms/",
            "https://ethereum.org/en/developers/docs/consensus-mechanisms/pos",
            "https://ethereum.org/en/developers/docs/consensus-mechanisms/pos/keys",
            "https://ethereum.org/en/developers/docs/consensus-mechanisms/pos/rewards-and-penalties",
            "https://ethereum.org/en/developers/docs/consensus-mechanisms/pos/attack-and-defense",
            "https://ethereum.org/en/staking/",
            "https://ethereum.org/en/developers/docs/blocks/",
            "https://ethereum.org/en/developers/docs/gas/",
        ]}),

    # ── Solana DeFiLlama Top Protocols (missing) ────────────────────────────────

    # Lifinity — proactive market maker using oracle-based pricing, concentrated
    # liquidity, rebalancing mechanism, LFNTY flywheel, impermanent loss reduction
    Source("lifinity_docs", "protocols", "sitemap",
        "https://docs.lifinity.io", "lifinity", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["lifinity","proactive-amm","oracle-pricing","concentrated-liquidity",
              "rebalancing","lfnty","impermanent-loss","solana","amm"]),

    # Solayer — native restaking on Solana, sSOL (Solayer LST), endogenous AVS,
    # Solana-native security, network bandwidth allocation, restaking rewards
    Source("solayer_docs", "protocols", "sitemap",
        "https://docs.solayer.org", "solayer", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["solayer","restaking","ssol","avs","native-restaking","solana",
              "liquid-restaking","bandwidth","network-security","lrt"]),

    # Fragmetric — liquid restaking on Solana, fragSOL, AVS portfolio,
    # NCN (node consensus network), restaking yield, auto-compound
    Source("fragmetric_docs", "protocols", "direct_urls",
        "https://docs.fragmetric.xyz", "fragmetric", "protocol_documentation", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.0,
        tags=["fragmetric","fragSOL","liquid-restaking","ncn","avs","solana",
              "restaking","auto-compound","yield","lrt"],
        extra={"urls": ["https://docs.fragmetric.xyz/llms-full.txt"]}),

    # Loopscale — structured lending on Solana, order book lending model,
    # fixed-rate loans, yield vaults, collateral isolation, credit scoring
    Source("loopscale_docs", "protocols", "sitemap",
        "https://docs.loopscale.com", "loopscale", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["loopscale","structured-lending","fixed-rate","order-book-lending",
              "yield-vault","collateral","solana","lending","credit"]),

    # Adrena — perpetuals DEX on Solana, ALP liquidity pool, market-making,
    # leverage trading, funding rates, ADX token, ALP/ADX tokenomics
    Source("adrena_docs", "protocols", "sitemap",
        "https://docs.adrena.trade", "adrena", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["adrena","perpetuals","alp","adx","leverage","funding-rate",
              "solana","perps","liquidity-pool","market-making"]),

    # Exponent Finance — yield trading on Solana, principal tokens (PT), yield tokens (YT),
    # fixed yield, yield speculating, LP strategies, similar to Pendle but on Solana
    Source("exponent_docs", "protocols", "sitemap",
        "https://docs.exponent.finance", "exponent", "protocol_documentation", "proprietary-fair-use",
        max_pages=25, crawl_delay=1.0,
        tags=["exponent","yield-trading","principal-token","yield-token","fixed-yield",
              "solana","pendle-like","pt","yt","lp-strategy"]),

    # DefiTuna — liquidity manager on Solana (similar to Kamino), concentrated liquidity
    # auto-rebalancing, range orders, whirlpool integration, strategy vaults
    Source("defituna_docs", "protocols", "sitemap",
        "https://docs.defituna.com", "defituna", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["defituna","liquidity-manager","concentrated-liquidity","auto-rebalancing",
              "whirlpool","range-order","solana","clmm","strategy"]),

    # ── Options Protocols ───────────────────────────────────────────────────────

    # Aevo — options and perpetuals exchange (formerly Ribbon Finance), RFQ system,
    # options vaults (DOV), structured products, pre-launch futures, off-chain matching
    Source("aevo_docs", "protocols", "sitemap",
        "https://docs.aevo.xyz", "aevo", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["aevo","ribbon","options","perpetuals","dov","rfq","structured-products",
              "pre-launch","options-vault","ethereum","derivatives"]),

    # Lyra / Derive — options AMM with Black-Scholes pricing, dynamic delta hedging,
    # PMRM risk model, SNX collateral, options LP, settlement, volatility surface
    Source("lyra_docs", "protocols", "sitemap",
        "https://docs.lyra.finance", "lyra", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["lyra","derive","options","amm","black-scholes","delta-hedging",
              "pmrm","volatility-surface","lp","settlement","ethereum","derivatives"]),

    # Premia Finance — options AMM on Ethereum/Arbitrum, C-Level pricing model,
    # liquidity pools, underwriters, option buying/writing, PREMIA staking, vxPREMIA
    Source("premia_docs", "protocols", "sitemap",
        "https://docs.premia.blue", "premia", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["premia","options","amm","c-level","underwriter","liquidity-pool",
              "vxpremia","option-writing","arbitrum","ethereum","derivatives"]),

    # ── Prediction Markets & Insurance ─────────────────────────────────────────

    # Polymarket — largest prediction market, CLOB (central limit order book),
    # USDC collateral, conditional tokens (ERC-1155), UMA oracle resolution, market creation
    Source("polymarket_docs", "protocols", "sitemap",
        "https://docs.polymarket.com", "polymarket", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["polymarket","prediction-market","clob","conditional-token","uma",
              "usdc","market-resolution","information-market","polygon"]),

    # Nexus Mutual — decentralized insurance protocol, discretionary mutual,
    # cover products (protocol, custody, yield), NXM bonding curve, claims assessment,
    # staking on risk, capacity limits, wNXM
    Source("nexusmutual_docs", "protocols", "sitemap",
        "https://docs.nexusmutual.io", "nexusmutual", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["nexus-mutual","defi-insurance","cover","nxm","bonding-curve",
              "claims","staking","risk-pool","wnxm","mutual","ethereum"]),

    # Beefy Finance — multi-chain yield optimizer/autocompounder, vaults, strategies,
    # BIFI token, fee structure, partner vaults, safety score, cross-chain yield
    Source("beefy_docs", "protocols", "sitemap",
        "https://docs.beefy.finance", "beefy", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.0,
        tags=["beefy","yield-optimizer","autocompounder","vault","strategy",
              "bifi","multi-chain","cross-chain-yield","safety-score","solana"]),

    # ── Solana DeFiLlama Top Protocol Additions ─────────────────────────────────

    # OnRe — on-chain reinsurance protocol on Solana, Bermuda-licensed,
    # ONyc token representing reinsurance risk, institutional reinsurance exposure,
    # parametric risk pools, premium yield for liquidity providers
    Source("onre_docs", "protocols", "sitemap",
        "https://docs.onre.finance", "onre", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.0,
        tags=["onre","reinsurance","rwa","parametric","risk-pool","onyc",
              "institutional","solana","insurance","on-chain-insurance"]),

    # Unitas — basis trading stablecoin on Solana, delta-neutral yield via Jupiter LP,
    # USD-denominated APY 8-15%, collateralized by SOL/LSTs, UST2.0 design
    Source("unitas_docs", "protocols", "sitemap",
        "https://docs.unitas.so", "unitas", "protocol_documentation", "proprietary-fair-use",
        max_pages=30, crawl_delay=1.0,
        tags=["unitas","basis-trading","delta-neutral","stablecoin","jupiter-lp",
              "sol","lst","yield","solana","funding-rate-arbitrage"]),

    # Lulo — yield aggregator on Solana, auto-routing stablecoin deposits to best APY
    # across Kamino/Drift/Solend, loss coverage protection, single-click
    Source("lulo_docs", "protocols", "direct_urls",
        "https://lulo.fi", "lulo", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.0,
        tags=["lulo","yield-aggregator","stablecoin","auto-routing","kamino","drift",
              "loss-coverage","solana","single-click-yield"],
        extra={"urls": [
            "https://lulo.fi/docs",
            "https://lulo.fi/docs/overview",
            "https://lulo.fi/docs/how-it-works",
            "https://lulo.fi/docs/supported-protocols",
        ]}),

    # The Vault — community liquid staking on Solana, vSOL token, validator
    # decentralization focus, non-custodial, stake pool architecture
    Source("thevault_docs", "protocols", "direct_urls",
        "https://docs.thevault.finance", "thevault", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.0,
        tags=["vault","vsol","liquid-staking","validator-decentralization","stake-pool",
              "non-custodial","solana","lst","community-staking"],
        extra={"urls": [
            "https://docs.thevault.finance",
            "https://docs.thevault.finance/overview",
            "https://docs.thevault.finance/vsol",
        ]}),

    # Edgevana — validator-grade liquid staking, edgeSOL, institutional validator,
    # MEV rewards, decentralized stake distribution, non-custodial
    Source("edgevana_docs", "protocols", "direct_urls",
        "https://stake.edgevana.com", "edgevana", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.0,
        tags=["edgevana","edgesol","liquid-staking","validator","mev","institutional",
              "stake-distribution","solana","lst","non-custodial"],
        extra={"urls": [
            "https://stake.edgevana.com/docs",
            "https://stake.edgevana.com/docs/overview",
            "https://stake.edgevana.com/docs/edgesol",
        ]}),

    # FlashTrade — asset-backed perpetuals DEX on Solana, up to 500x leverage,
    # 4bps fees, pool-to-peer liquidity, multi-asset collateral, flash pool
    Source("flashtrade_docs", "protocols", "sitemap",
        "https://docs.flash.trade", "flashtrade", "protocol_documentation", "proprietary-fair-use",
        max_pages=40, crawl_delay=1.0,
        tags=["flashtrade","perpetuals","leverage","pool-to-peer","flash-pool",
              "multi-asset-collateral","low-fee","solana","perps","derivatives"]),

    # Byreal — AI-native hybrid DEX on Solana (Bybit-incubated), CLMM + off-chain
    # RFQ for optimal execution, AI routing, institutional liquidity access
    Source("byreal_docs", "protocols", "sitemap",
        "https://docs.byreal.io", "byreal", "protocol_documentation", "proprietary-fair-use",
        max_pages=35, crawl_delay=1.0,
        tags=["byreal","hybrid-dex","clmm","rfq","ai-routing","bybit",
              "optimal-execution","institutional","solana","amm"]),

    # Neutral Trade — on-chain capital allocator on Solana, strategy vaults
    # (indexes, private credit, earn), automated execution across trading venues
    Source("neutral_trade_docs", "protocols", "sitemap",
        "https://docs.neutral.trade", "neutral-trade", "protocol_documentation", "proprietary-fair-use",
        max_pages=35, crawl_delay=1.0,
        tags=["neutral-trade","capital-allocator","strategy-vault","index","private-credit",
              "automated","solana","portfolio","allocation"]),

    # Perena — stablecoin AMM hub-and-spoke on Solana, USD* tokens with embedded
    # treasury yield, concentrated liquidity around peg, numéraire design
    Source("perena_docs", "protocols", "direct_urls",
        "https://perena.gitbook.io", "perena", "protocol_documentation", "proprietary-fair-use",
        max_pages=25, crawl_delay=1.5,
        tags=["perena","stablecoin-amm","hub-spoke","usd-star","treasury-yield",
              "numeraire","concentrated-liquidity","peg","solana","stablecoin"]),

    # dune_docs — removed: SQL query/analytics tooling for developers, not conceptual DeFi knowledge

    # ── Solana Missing Protocols ───────────────────────────────────────────────

    # Parcl — real estate price index perpetuals on Solana (unique RWA derivative)
    Source("parcl_docs", "protocols", "sitemap",
        "https://docs.parcl.co", "parcl", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.5,
        tags=["parcl","real-estate","perpetuals","price-index","solana","rwa",
              "derivatives","leverage","synthetic","property-market"]),

    # Credix — RWA private credit marketplace on Solana; institutional undercollateralized lending
    Source("credix_docs", "protocols", "sitemap",
        "https://docs.credix.finance", "credix", "protocol_documentation", "proprietary-fair-use",
        max_pages=50, crawl_delay=1.5,
        tags=["credix","rwa","private-credit","undercollateralized","institutional",
              "solana","credit-market","yield","emerging-markets","structured-credit"]),

    # tulip_docs — removed: tulip.garden is SPA redirect, no crawlable docs
    # nansen_research — removed: Framer JS site, content not in static HTML
    # openzeppelin_blog — removed: moved to openzeppelin.com/news (audit reports only)
    # gauntlet_research_full — removed: gauntlet.xyz sitemap only has homepage

    # ── DeFi Education & Research ──────────────────────────────────────────────

    # Curve Finance Docs — veToken model, gauge mechanics, StableSwap invariant,
    # crvUSD, emissions, governance, fee architecture
    Source("curve_finance_docs", "education", "sitemap",
        "https://docs.curve.finance", None, "education", "proprietary-fair-use",
        max_pages=120, crawl_delay=1.5,
        tags=["curve","vecrv","gauge","voting","stableswap","amm","vetoken",
              "bribe","crvusd","emissions","governance","fee","liquidity-pool","crv-wars"],
        extra={"exclude_patterns": ["/developer/"]}),

    # Trail of Bits Blog — smart contract security research, DeFi vulnerability
    # analysis, formal verification, fuzzing, audit findings
    Source("trailofbits_blog", "education", "sitemap",
        "https://blog.trailofbits.com", None, "education", "proprietary-fair-use",
        max_pages=80, crawl_delay=2.0,
        tags=["trailofbits","security","audit","vulnerability","defi-security",
              "formal-verification","fuzzing","smart-contract","exploit","reentrancy"]),

    # Wintermute Insights — market maker perspective; liquidity provision,
    # OTC trading, DeFi market structure, institutional DeFi
    Source("wintermute_insights", "education", "sitemap",
        "https://wintermute.com", None, "education", "proprietary-fair-use",
        max_pages=50, crawl_delay=2.0,
        tags=["wintermute","market-maker","liquidity","otc","institutional",
              "defi","market-structure","trading","rfq","spread"]),

    # Jump Crypto Research — technical DeFi research, blockchain infrastructure,
    # MEV, consensus mechanisms, cross-chain security
    Source("jumpcrypto_research", "education", "sitemap",
        "https://jumpcrypto.com", None, "education", "proprietary-fair-use",
        max_pages=60, crawl_delay=2.0,
        tags=["jump-crypto","research","mev","consensus","cross-chain","security",
              "infrastructure","defi","validator","trading"]),

    # ── Bitcoin knowledge ──────────────────────────────────────────────────────

    # Learn Me a Bitcoin — beginner-to-intermediate Bitcoin education
    # UTXO model, scripting, mining, P2P network, wallets — very clean HTML
    Source("learnmeabitcoin", "education", "sitemap",
        "https://learnmeabitcoin.com", None, "education", "proprietary-fair-use",
        max_pages=120, crawl_delay=1.0,
        tags=["bitcoin","utxo","script","mining","p2p","wallet","transaction",
              "block","pow","segwit","lightning","taproot","bip"]),

    # Bitcoin Optech — Topics library: technical BIP/protocol explanations
    # (PSBT, Taproot, LN, Miniscript, silent payments…) used by developers worldwide
    # Bitcoin Optech — individual topic pages (152 topics; no sitemap so using direct_urls)
    Source("bitcoinops_topics", "education", "direct_urls",
        "https://bitcoinops.org", None, "education", "mit",
        max_pages=160, crawl_delay=0.5,
        tags=["bitcoin","bip","taproot","psbt","lightning","miniscript",
              "silent-payments","rbf","cpfp","schnorr","musig","dlc",
              "covenants","coinjoin","splicing","ptlc","htlc","tapscript"],
        extra={"urls": [
            "https://bitcoinops.org/en/topics/multisignature/",
            "https://bitcoinops.org/en/topics/adaptor-signatures/",
            "https://bitcoinops.org/en/topics/cpfp/",
            "https://bitcoinops.org/en/topics/anchor-outputs/",
            "https://bitcoinops.org/en/topics/replace-by-fee/",
            "https://bitcoinops.org/en/topics/compact-block-relay/",
            "https://bitcoinops.org/en/topics/compact-block-filters/",
            "https://bitcoinops.org/en/topics/psbt/",
            "https://bitcoinops.org/en/topics/package-relay/",
            "https://bitcoinops.org/en/topics/offers/",
            "https://bitcoinops.org/en/topics/channel-factories/",
            "https://bitcoinops.org/en/topics/channel-jamming-attacks/",
            "https://bitcoinops.org/en/topics/cluster-mempool/",
            "https://bitcoinops.org/en/topics/coin-selection/",
            "https://bitcoinops.org/en/topics/coinjoin/",
            "https://bitcoinops.org/en/topics/coinswap/",
            "https://bitcoinops.org/en/topics/covenants/",
            "https://bitcoinops.org/en/topics/cross-input-signature-aggregation/",
            "https://bitcoinops.org/en/topics/output-script-descriptors/",
            "https://bitcoinops.org/en/topics/discreet-log-contracts/",
            "https://bitcoinops.org/en/topics/dual-funding/",
            "https://bitcoinops.org/en/topics/ecash/",
            "https://bitcoinops.org/en/topics/eltoo/",
            "https://bitcoinops.org/en/topics/ephemeral-anchors/",
            "https://bitcoinops.org/en/topics/fee-estimation/",
            "https://bitcoinops.org/en/topics/htlc/",
            "https://bitcoinops.org/en/topics/hold-invoices/",
            "https://bitcoinops.org/en/topics/htlc-endorsement/",
            "https://bitcoinops.org/en/topics/jit-channels/",
            "https://bitcoinops.org/en/topics/liquidity-advertisements/",
            "https://bitcoinops.org/en/topics/ln-penalty/",
            "https://bitcoinops.org/en/topics/mast/",
            "https://bitcoinops.org/en/topics/miniscript/",
            "https://bitcoinops.org/en/topics/musig/",
            "https://bitcoinops.org/en/topics/onion-messages/",
            "https://bitcoinops.org/en/topics/payjoin/",
            "https://bitcoinops.org/en/topics/ptlc/",
            "https://bitcoinops.org/en/topics/quantum-resistance/",
            "https://bitcoinops.org/en/topics/schnorr-signatures/",
            "https://bitcoinops.org/en/topics/segregated-witness/",
            "https://bitcoinops.org/en/topics/sidechains/",
            "https://bitcoinops.org/en/topics/silent-payments/",
            "https://bitcoinops.org/en/topics/simple-taproot-channels/",
            "https://bitcoinops.org/en/topics/simplicity/",
            "https://bitcoinops.org/en/topics/splicing/",
            "https://bitcoinops.org/en/topics/statechains/",
            "https://bitcoinops.org/en/topics/submarine-swaps/",
            "https://bitcoinops.org/en/topics/taproot/",
            "https://bitcoinops.org/en/topics/tapscript/",
            "https://bitcoinops.org/en/topics/timelocks/",
            "https://bitcoinops.org/en/topics/timeout-trees/",
            "https://bitcoinops.org/en/topics/version-3-transaction-relay/",
            "https://bitcoinops.org/en/topics/trampoline-payments/",
            "https://bitcoinops.org/en/topics/transaction-pinning/",
            "https://bitcoinops.org/en/topics/utreexo/",
            "https://bitcoinops.org/en/topics/vaults/",
            "https://bitcoinops.org/en/topics/watchtowers/",
            "https://bitcoinops.org/en/topics/zero-conf-channels/",
            "https://bitcoinops.org/en/topics/soft-fork-activation/",
            "https://bitcoinops.org/en/topics/hd-key-generation/",
            "https://bitcoinops.org/en/topics/consensus-cleanup-soft-fork/",
            "https://bitcoinops.org/en/topics/v2-p2p-transport/",
            "https://bitcoinops.org/en/topics/bech32/",
            "https://bitcoinops.org/en/topics/ark/",
            "https://bitcoinops.org/en/topics/assumeutxo/",
            "https://bitcoinops.org/en/topics/payment-batching/",
            "https://bitcoinops.org/en/topics/joinpools/",
            "https://bitcoinops.org/en/topics/stateless-invoices/",
            "https://bitcoinops.org/en/topics/static-channel-backups/",
            "https://bitcoinops.org/en/topics/swap-in-potentiam/",
        ]}),

    # River Learn — Bitcoin financial literacy; Bitcoin vs gold, inflation hedge,
    # self-custody, node operation, institutional adoption
    Source("river_learn", "education", "sitemap",
        "https://river.com", None, "education", "proprietary-fair-use",
        max_pages=80, crawl_delay=1.0,
        tags=["bitcoin","self-custody","inflation","store-of-value","node",
              "lightning","financial-literacy","institutional","etf"]),

    # Bitcoin.org Developer Guide — llms-full.txt (MIT license, very authoritative)
    Source("bitcoin_org_guide", "education", "direct_urls",
        "https://bitcoin.org", None, "education", "mit",
        max_pages=5, crawl_delay=1.0,
        tags=["bitcoin","developer-guide","p2p","transactions","wallet","block",
              "mining","script","rpc","utxo","sigscript","pubkey"],
        extra={"urls": ["https://bitcoin.org/llms-full.txt"]}),

    # Bitcoin Wiki — community-maintained technical reference (P2SH, SegWit,
    # BIPs, scripting, protocol details)
    Source("bitcoin_wiki", "education", "direct_urls",
        "https://en.bitcoin.it", None, "education", "mit",
        max_pages=5, crawl_delay=1.5,
        tags=["bitcoin","bip","segwit","p2sh","script","mining","difficulty",
              "utxo","transaction","multisig","lightning"],
        extra={"urls": [
            "https://en.bitcoin.it/wiki/Bitcoin",
            "https://en.bitcoin.it/wiki/Transaction",
            "https://en.bitcoin.it/wiki/Script",
            "https://en.bitcoin.it/wiki/Proof_of_work",
            "https://en.bitcoin.it/wiki/Mining",
            "https://en.bitcoin.it/wiki/Segregated_Witness",
            "https://en.bitcoin.it/wiki/BIP_0032",
            "https://en.bitcoin.it/wiki/UTXO",
        ]}),

    # ── On-chain analysis ──────────────────────────────────────────────────────

    # Glassnode Insights — leading on-chain analytics; SOPR, MVRV, realized cap,
    # exchange flows, miner behavior, market cycles (sitemap has 3984 posts)
    Source("glassnode_insights", "education", "sitemap",
        "https://insights.glassnode.com", None, "education", "proprietary-fair-use",
        max_pages=80, crawl_delay=1.5,
        tags=["glassnode","on-chain","sopr","mvrv","realized-cap","exchange-flow",
              "miner","stablecoin","bitcoin","ethereum","market-cycle","hodl"]),

    # Glassnode Academy — educational series on reading on-chain metrics;
    # NVT, SOPR, active addresses, supply distribution, exchange balances
    Source("glassnode_academy", "education", "sitemap",
        "https://academy.glassnode.com", None, "education", "proprietary-fair-use",
        max_pages=60, crawl_delay=1.5,
        tags=["glassnode","on-chain-education","metrics","nvt","sopr","mvrv",
              "active-addresses","supply","market-cycles","analytics"]),

    # Flipside Crypto — on-chain data methodology, dashboards, SQL for blockchain
    Source("flipside_crypto", "education", "direct_urls",
        "https://flipsidecrypto.xyz", None, "education", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.5,
        tags=["flipside","on-chain","sql","blockchain-data","analytics",
              "dashboards","solana","ethereum","defi","nft"],
        extra={"urls": ["https://flipsidecrypto.xyz/llms-full.txt"]}),

    # ── DeFi security ──────────────────────────────────────────────────────────

    # Immunefi Learn + Blog — bug bounty platform, vulnerability reports,
    # DeFi exploit deep-dives, responsible disclosure education
    Source("immunefi_learn", "education", "direct_urls",
        "https://immunefi.com", None, "security_research", "proprietary-fair-use",
        max_pages=80, crawl_delay=1.5,
        tags=["immunefi","bug-bounty","exploit","vulnerability","defi-security",
              "responsible-disclosure","hack","postmortem","smart-contract","audit"],
        extra={"urls": [
            "https://immunefi.com/learn/",
            "https://immunefi.com/blog/",
        ]}),

    # SWC Registry — Smart Contract Weakness Classification
    # Canonical taxonomy of Solidity/EVM smart contract bugs (MIT license)
    Source("swcregistry", "education", "sitemap",
        "https://swcregistry.io", None, "security_research", "mit",
        max_pages=60, crawl_delay=1.0,
        tags=["swc","smart-contract","vulnerability","reentrancy","overflow",
              "access-control","oracle","evm","solidity","audit","weakness"]),

    # crytic / not-so-smart-contracts — curated collection of known vulnerable
    # Solidity patterns with explanations (MIT, GitHub)
    Source("not_so_smart_contracts", "education", "github_raw",
        "https://raw.githubusercontent.com/crytic/not-so-smart-contracts/master",
        None, "security_research", "mit",
        max_pages=40, crawl_delay=0.3,
        tags=["crytic","smart-contract","vulnerability","reentrancy","overflow",
              "access-control","dos","bad-randomness","forced-ether","unchecked-call"],
        extra={"repo": "crytic/not-so-smart-contracts", "branch": "master", "path": ""}),

    # Consensys Diligence Blog — leading smart contract auditors, in-depth
    # vulnerability analysis, audit methodology, DeFi attack vectors
    Source("consensys_diligence", "education", "direct_urls",
        "https://consensys.io", None, "security_research", "proprietary-fair-use",
        max_pages=5, crawl_delay=1.5,
        tags=["consensys","diligence","audit","smart-contract","vulnerability",
              "defi-security","ethereum","solidity","reentrancy","flash-loan"],
        extra={"urls": ["https://consensys.io/diligence/blog"]}),

    # ── Macro & market analysis ────────────────────────────────────────────────

    # Galaxy Digital Research — institutional-grade crypto research; macro,
    # Bitcoin, Ethereum, DeFi sector reports, market structure analysis
    Source("galaxy_research", "education", "sitemap",
        "https://www.galaxy.com", None, "education", "proprietary-fair-use",
        max_pages=100, crawl_delay=1.5,
        tags=["galaxy","macro","bitcoin","ethereum","defi","institutional",
              "market-structure","research","mining","staking","venture"]),

    # ── Quantitative / AMM math ────────────────────────────────────────────────

    # 0xperp/defi-derivatives — curated research on DeFi derivatives:
    # AMM math, options, perps, funding rate, Greeks, CLMM, LVR theory
    Source("defi_derivatives_research", "education", "github_raw",
        "https://raw.githubusercontent.com/0xperp/defi-derivatives/main",
        None, "education", "mit",
        max_pages=10, crawl_delay=0.3,
        tags=["amm","derivatives","options","perpetuals","funding-rate","greeks",
              "clmm","lvr","impermanent-loss","uniswap-v3","concentrated-liquidity",
              "dlmm","bonding-curve","quantitative","defi-math"],
        extra={"repo": "0xperp/defi-derivatives", "branch": "main", "path": ""}),

    # ── MEV education ──────────────────────────────────────────────────────────

    # Flashbots Writings — the canonical MEV research blog; MEV taxonomy,
    # sandwich attacks, backrunning, PBS (proposer-builder separation),
    # SUAVE, MEV-Share, MEV-Boost, mev-geth, searcher economics
    Source("flashbots_writings", "education", "sitemap",
        "https://writings.flashbots.net", None, "education", "mit",
        max_pages=120, crawl_delay=1.0,
        tags=["mev","flashbots","sandwich","backrun","frontrun","pbs",
              "proposer-builder","mev-boost","searcher","mempool","bundle",
              "suave","mev-share","dark-forest","ethereum","block-building"]),

    # ── Yield tokenization ─────────────────────────────────────────────────────

    # Pendle Finance — Protocol Mechanics (conceptual, non-dev):
    # SY (standardized yield), PT (fixed yield / zero-coupon bond),
    # YT (leveraged variable yield), custom AMM for yield curves
    Source("pendle_concepts", "protocols", "direct_urls",
        "https://docs.pendle.finance", "pendle", "protocol_documentation", "proprietary-fair-use",
        max_pages=15, crawl_delay=1.0,
        tags=["pendle","yield-tokenization","pt","yt","sy","fixed-yield","variable-yield",
              "implied-apy","zero-coupon","yield-curve","amm","defi","expiry","maturity"],
        extra={"urls": [
            "https://docs.pendle.finance/pendle-v2/Introduction",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Glossary",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/SY",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/PT",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/YT",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/Minting",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/LiquidityEngines/AMM",
            "https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Mechanisms/Fees",
        ]}),

    # ── Crypto economics & monetary theory ────────────────────────────────────

    # Noah Smith (Noahpinion) — economist's view of crypto; monetary networks,
    # inflation hedge thesis, stablecoin economics, DeFi market structure,
    # crypto vs traditional finance macro
    Source("noahpinion_crypto", "education", "sitemap",
        "https://www.noahpinion.blog", None, "education", "proprietary-fair-use",
        max_pages=80, crawl_delay=1.5,
        tags=["economics","monetary-policy","inflation","crypto-economics",
              "stablecoin","macro","bitcoin-thesis","defi-macro","network-effects",
              "monetary-network","store-of-value","fiat"]),

    # ── News (hourly poll, full-content only) ───────────────────────────────
    # CRITERIA for inclusion here: RSS feed must deliver FULL article body,
    # not paywall'd excerpt. The Block / CoinDesk / Blockworks / Cointelegraph
    # are excluded on purpose — their RSS returns only a teaser, which leaves
    # the RAG half-blind. If you want one of those, switch them off again the
    # day you have a partnership / API key that returns full body.
    Source("helius_blog", "news", "rss",
        "https://www.helius.dev/blog/rss.xml", "helius", "news", "permissive",
        max_pages=30, crawl_delay=2.0, crawl_freq="hourly",
        tags=["solana","news","developer","rpc","helius","ecosystem"]),

    Source("anza_blog", "news", "rss",
        "https://www.anza.xyz/blog/rss.xml", "anza", "news", "permissive",
        max_pages=30, crawl_delay=2.0, crawl_freq="hourly",
        tags=["solana","news","core","validator","anza","ecosystem"]),

    Source("solana_foundation_news", "news", "rss",
        "https://solana.com/news/rss.xml", "solana", "news", "permissive",
        max_pages=30, crawl_delay=2.0, crawl_freq="hourly",
        tags=["solana","news","foundation","ecosystem","announcements"]),

    Source("jito_blog", "news", "rss",
        "https://www.jito.network/blog/rss.xml", "jito", "news", "permissive",
        max_pages=30, crawl_delay=2.0, crawl_freq="hourly",
        tags=["solana","news","jito","mev","staking","bundles"]),
]

# ── Stats ──────────────────────────────────────────────────────────────────────
@dataclass
class RunStats:
    sources_done: int = 0
    sources_failed: int = 0
    pages_crawled: int = 0
    pages_skipped: int = 0
    pages_unchanged: int = 0  # short-circuited by page_hash dedup → no Haiku/embed call
    start_time: float = field(default_factory=time.time)
    chunks_total: int = 0
    chunks_upserted: int = 0
    embed_tokens: int = 0

    def summary(self) -> str:
        elapsed = int(time.time() - self.start_time)
        cost = self.embed_tokens / 1_000_000 * 0.13
        return (
            f"Sources: {self.sources_done} done / {self.sources_failed} failed | "
            f"Pages: {self.pages_crawled} crawled / {self.pages_skipped} skipped / {self.pages_unchanged} unchanged | "
            f"Chunks: {self.chunks_upserted}/{self.chunks_total} upserted | "
            f"Tokens: {self.embed_tokens:,} (${cost:.2f}) | Elapsed: {elapsed}s"
        )

STATS = RunStats()

# ── Utilities ─────────────────────────────────────────────────────────────────
def make_point_id(doc_id: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(UUID_NS, f"{doc_id}:{chunk_idx}"))

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def slug(url: str, source_id: str) -> str:
    p = urlparse(url)
    path = p.path.strip("/").replace("/", "_").replace("-", "_").replace(".", "_")
    return f"{source_id}.{path}"[:180] if path else source_id

def to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None

_token_re = re.compile(r"[a-z0-9]+")
def sparse_vector(text: str) -> tuple[list[int], list[float]]:
    from collections import Counter
    tokens = _token_re.findall(text.lower())
    h = lambda t: (sum(ord(c) << (i * 5) for i, c in enumerate(t[:8]))) & 0x7FFFFFFF
    tf: Counter[int] = Counter(h(t) for t in tokens)
    return list(tf.keys()), [float(v) for v in tf.values()]

# ── HTML → Text ───────────────────────────────────────────────────────────────
def page_to_text(resp, url: str, excerpt_only: bool = False) -> str:
    """Convert a fetched page to clean text. Markdown/plain responses (e.g. a docs
    platform's `.md` variant, or an `llms.txt`) are ALREADY clean text — running
    them through readability's html_to_text zeroes them out, so pass them through
    (stripping any YAML frontmatter). Everything else goes through html_to_text."""
    ct = (resp.headers.get("content-type") or "").lower()
    if url.endswith(".md") or "markdown" in ct or "text/plain" in ct:
        t = resp.text
        if t.startswith("---"):
            end = t.find("\n---", 3)
            if end != -1:
                t = t[end + 4:]
        return t.strip()
    return html_to_text(resp.text, base_url=url, excerpt_only=excerpt_only)


def html_to_text(html: str, base_url: str = "", excerpt_only: bool = False) -> str:
    try:
        from readability import Document
        doc = Document(html, url=base_url)
        html = doc.summary(html_partial=True)
    except Exception:
        pass

    try:
        import markdownify
        md = markdownify.markdownify(
            html, heading_style="ATX",
            strip=["script", "style", "nav", "footer", "aside", "noscript", "iframe"],
        )
    except Exception:
        from html.parser import HTMLParser
        class P(HTMLParser):
            def __init__(self): super().__init__(); self.t: list[str] = []
            def handle_data(self, d): self.t.append(d)
        p = P(); p.feed(html)
        md = " ".join(p.t)

    md = re.sub(r"\n{3,}", "\n\n", md).strip()

    if excerpt_only:
        sentences = re.split(r"(?<=[.!?])\s+", md[:2000])
        excerpt = ""
        for s in sentences:
            if len(excerpt) + len(s) + 1 > MAX_EXCERPT:
                break
            excerpt = (excerpt + " " + s).strip()
        return excerpt or md[:MAX_EXCERPT]

    return md

# ── Claude Haiku page classifier ──────────────────────────────────────────────
@dataclass
class ClassifyResult:
    keep: bool
    tags: list[str]
    summary: str
    section_anchors: list[dict]  # [{"label": "Section Name", "anchor": "verbatim text"}]

_CLASSIFY_PROMPT = """\
You are a content classifier and section boundary detector for a DeFi/blockchain knowledge base.

Given a page title, URL, and content, return JSON with four fields:

"keep": true for conceptual/educational pages — protocol overviews, how-it-works explanations, risk \
descriptions, mechanism guides, glossary entries, tokenomics, governance, staking concepts, DeFi \
fundamentals. false for API reference, SDK method signatures, code-only tutorials, changelogs, \
migration guides, CLI flag lists.

"tags": 3-7 lowercase hyphenated topic tags (e.g. "liquid-staking", "impermanent-loss", "fee-tiers").

"summary": one concise sentence describing what this page covers.

"section_anchors": array of section boundary objects in document order. Each object:
  "label" — short plain-English section name (e.g. "How mSOL Works", "Risk Factors", "Fee Structure")
  "anchor" — EXACT verbatim copy of the first 7-12 words where this section begins in the text. \
Copy character-for-character including punctuation. This is used with str.find() to locate the \
boundary. If the page has a single topic or is very short, use one entry with anchor "".

Include 2-5 sections. Never use code identifiers, method names, or URLs as labels or anchors.

Return only raw JSON, no markdown fences."""

_classify_sem = asyncio.Semaphore(30)

def _find_anchor(text: str, anchor: str) -> int:
    """Find anchor in text: exact match first, then first-5-words fallback."""
    if not anchor:
        return -1
    pos = text.find(anchor)
    if pos != -1:
        return pos
    words = anchor.split()[:5]
    if len(words) >= 3:
        pos = text.find(" ".join(words))
        if pos != -1:
            return pos
    return -1

async def classify_page(
    anthropic: AsyncAnthropic,
    title: str,
    text: str,
    url: str,
) -> ClassifyResult:
    # Send up to 4000 chars so Haiku sees enough to find real section boundaries
    excerpt = text[:4000].strip()
    user_msg = f"Title: {title}\nURL: {url}\n\nContent:\n{excerpt}"

    async with _classify_sem:
        for attempt in range(3):
            try:
                resp = await anthropic.messages.create(
                    model=CLASSIFY_MODEL,
                    max_tokens=500,
                    system=_CLASSIFY_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = resp.content[0].text.strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
                data = json.loads(raw)
                anchors = [
                    {"label": str(a.get("label", "Overview")),
                     "anchor": str(a.get("anchor", ""))}
                    for a in data.get("section_anchors", [])
                ]
                if not anchors:
                    anchors = [{"label": "Overview", "anchor": ""}]
                return ClassifyResult(
                    keep=bool(data.get("keep", True)),
                    tags=[str(t) for t in data.get("tags", [])],
                    summary=str(data.get("summary", "")),
                    section_anchors=anchors,
                )
            except Exception as e:
                if attempt == 2:
                    log.debug("classify_page failed for %s: %s", url, e)
                    return ClassifyResult(keep=True, tags=[], summary="",
                                          section_anchors=[{"label": "Overview", "anchor": ""}])
                await asyncio.sleep(2 ** attempt)
    return ClassifyResult(keep=True, tags=[], summary="",
                          section_anchors=[{"label": "Overview", "anchor": ""}])

# ── Chunker ───────────────────────────────────────────────────────────────────
def _para_chunks(
    content: str, doc_id: str, start_idx: int, section_label: str,
) -> tuple[list[dict], int]:
    """Paragraph-fill content into MAX_CHARS chunks with OVERLAP carry."""
    paras = [p.strip() for p in re.split(r"\n\n+", content) if p.strip()]
    chunks: list[dict] = []
    buf = ""
    overlap_carry = ""
    idx = start_idx

    for para in paras:
        if len(buf) + len(para) + 2 <= MAX_CHARS:
            buf = (buf + "\n\n" + para).strip()
        else:
            if buf:
                chunks.append({"doc_id": doc_id, "chunk_id": idx,
                                "content": buf, "section_path": section_label})
                idx += 1
                last_paras = [p.strip() for p in re.split(r"\n\n+", buf) if p.strip()]
                overlap_carry = last_paras[-1] if last_paras else ""
                if len(overlap_carry) > OVERLAP:
                    overlap_carry = overlap_carry[-OVERLAP:]
            buf = (overlap_carry + "\n\n" + para).strip() if overlap_carry else para
            if len(buf) > MAX_CHARS:
                buf = buf[:MAX_CHARS]
            overlap_carry = ""

    if buf.strip():
        chunks.append({"doc_id": doc_id, "chunk_id": idx,
                        "content": buf, "section_path": section_label})
        idx += 1

    return chunks, idx


def chunk_text(text: str, doc_id: str, section_anchors: list[dict]) -> list[dict]:
    """
    Unified chunker for all page types.

    Markdown docs (## headers present):
        Split on headers → breadcrumb section_path. Haiku anchors ignored.

    Plain / article text:
        Locate each Haiku anchor as an exact character position → split there →
        paragraph-fill each section with OVERLAP carry. Falls back gracefully
        if anchors cannot be found.
    """
    heading_re = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)

    # ── Markdown path ─────────────────────────────────────────────────────────
    if heading_re.search(text):
        positions = [(m.start(), m.group(1)) for m in heading_re.finditer(text)]
        chunks: list[dict] = []
        breadcrumb: list[str] = []
        idx = 0

        for i, (pos, heading) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            content = text[pos:end].strip()
            if not content:
                continue
            m = heading_re.match(text[pos:])
            level = len(m.group(0).split()[0]) if m else 1
            breadcrumb = breadcrumb[:level - 1] + [heading]
            path = " > ".join(breadcrumb)
            if len(content) <= MAX_CHARS:
                chunks.append({"doc_id": doc_id, "chunk_id": idx,
                                "content": content, "section_path": path})
                idx += 1
            else:
                new_chunks, idx = _para_chunks(content, doc_id, idx, path)
                chunks.extend(new_chunks)

        if not chunks:
            chunks.append({"doc_id": doc_id, "chunk_id": 0,
                            "content": text.strip(), "section_path": ""})
        return chunks

    # ── Article path: anchor-based boundary detection ─────────────────────────
    boundaries: list[tuple[int, str]] = []
    for sa in section_anchors:
        label = sa.get("label", "Overview").strip()
        anchor = sa.get("anchor", "").strip()
        pos = _find_anchor(text, anchor)
        if pos != -1:
            boundaries.append((pos, label))

    boundaries.sort(key=lambda x: x[0])
    # Remove entries that are too close together (< 80 chars apart)
    deduped: list[tuple[int, str]] = []
    for pos, label in boundaries:
        if not deduped or pos - deduped[-1][0] > 80:
            deduped.append((pos, label))

    fallback_label = section_anchors[0].get("label", "Overview") if section_anchors else "Overview"

    if not deduped:
        # No anchors found — treat whole text as one section
        result, _ = _para_chunks(text, doc_id, 0, fallback_label)
        return result or [{"doc_id": doc_id, "chunk_id": 0,
                           "content": text.strip(), "section_path": fallback_label}]

    # Build (label, content) pairs from located boundaries
    sections_content: list[tuple[str, str]] = []
    if deduped[0][0] > 80:
        preamble = text[:deduped[0][0]].strip()
        if preamble:
            sections_content.append((fallback_label, preamble))

    for i, (pos, label) in enumerate(deduped):
        end = deduped[i + 1][0] if i + 1 < len(deduped) else len(text)
        content = text[pos:end].strip()
        if content:
            sections_content.append((label, content))

    chunks = []
    chunk_idx = 0
    for label, content in sections_content:
        new_chunks, chunk_idx = _para_chunks(content, doc_id, chunk_idx, label)
        chunks.extend(new_chunks)

    if not chunks:
        chunks.append({"doc_id": doc_id, "chunk_id": 0,
                        "content": text.strip(), "section_path": fallback_label})
    return chunks

# ── Qdrant setup ──────────────────────────────────────────────────────────────
async def ensure_collection(qdrant: AsyncQdrantClient) -> None:
    try:
        await qdrant.get_collection(COLLECTION)
        log.info("Qdrant collection '%s' already exists", COLLECTION)
        return
    except Exception:
        pass

    log.info("Creating collection '%s'", COLLECTION)
    await qdrant.create_collection(
        collection_name=COLLECTION,
        vectors_config={"dense": VectorParams(
            size=EMBED_DIM, distance=Distance.COSINE, on_disk=True,
            hnsw_config=HnswConfigDiff(m=16, ef_construct=200),
            quantization_config=ScalarQuantization(
                scalar=ScalarQuantizationConfig(type=ScalarType.INT8, always_ram=True)
            ),
        )},
        sparse_vectors_config={"sparse": SparseVectorParams(
            index=SparseIndexParams(on_disk=False),
            modifier=models.Modifier.IDF,
        )},
    )

    keyword_fields = ["doc_id", "source_id", "source_type", "protocol",
                      "category", "language", "content_hash", "page_hash", "tags"]
    int_fields = ["published_at", "fetched_at"]
    for f in keyword_fields:
        try:
            await qdrant.create_payload_index(COLLECTION, f, PayloadSchemaType.KEYWORD)
        except Exception:
            pass
    for f in int_fields:
        try:
            await qdrant.create_payload_index(COLLECTION, f, PayloadSchemaType.INTEGER)
        except Exception:
            pass
    log.info("Collection created and indexed")

# ── Embedding ─────────────────────────────────────────────────────────────────
async def embed_batch(oai: AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    for attempt in range(5):
        try:
            resp = await oai.embeddings.create(
                model=EMBED_MODEL, input=texts, dimensions=EMBED_DIM)
            STATS.embed_tokens += resp.usage.total_tokens
            return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
        except Exception as e:
            wait = 2 ** attempt
            log.warning("Embed error (attempt %d): %s — retry in %ds", attempt + 1, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("Embedding failed after 5 attempts")

# ── Upsert ────────────────────────────────────────────────────────────────────
_qdrant_write_sem = asyncio.Semaphore(8)


_FREQ_SECONDS: dict[str, int] = {
    "hourly":  60 * 60,
    "daily":   24 * 60 * 60,
    "weekly":   7 * 24 * 60 * 60,
    "monthly": 30 * 24 * 60 * 60,
}


async def should_skip_source(
    qdrant: AsyncQdrantClient, source_id: str, freq: str,
) -> bool:
    """Return True if this source has been crawled within its `freq` window
    and should be skipped this run. Looks up the freshest `fetched_at` of
    any point with `source_id == source_id` (one indexed scroll, ordered
    desc). When the source is brand-new (no points yet), returns False so
    the first-ever crawl always runs.

    Returns False on Qdrant error so an outage doesn't silently freeze the
    crawler — better to do unnecessary work than miss updates.
    """
    interval = _FREQ_SECONDS.get(freq, _FREQ_SECONDS["weekly"])
    try:
        scroll, _ = await qdrant.scroll(
            collection_name=COLLECTION,
            scroll_filter=models.Filter(must=[
                models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
            ]),
            limit=1,
            with_payload=["fetched_at"],
            with_vectors=False,
            order_by=models.OrderBy(key="fetched_at", direction=models.Direction.DESC),
        )
        if not scroll:
            return False  # never crawled — run it
        freshest_ms = (scroll[0].payload or {}).get("fetched_at")
        if not isinstance(freshest_ms, (int, float)):
            return False
        age_s = (time.time() * 1000 - freshest_ms) / 1000
        return age_s < interval
    except Exception:
        return False


async def is_page_unchanged(
    qdrant: AsyncQdrantClient, doc_id: str, page_hash: str,
) -> bool:
    """Return True if Qdrant already has at least one point for `doc_id` with
    a matching `page_hash`. Used to short-circuit the crawler before the
    expensive Haiku classify + OpenAI embed steps. The cost of this lookup
    is one indexed scroll (page_hash + doc_id are both KEYWORD-indexed),
    versus ~$0.0035 + ~$0.0002 for classify + embed per page that hasn't
    changed.

    Returns False on any Qdrant error so a transient outage doesn't cause
    silent data staleness — we'd rather pay for an unnecessary classify
    than miss a real update.
    """
    if not page_hash:
        return False
    try:
        scroll, _ = await qdrant.scroll(
            collection_name=COLLECTION,
            scroll_filter=models.Filter(must=[
                models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
                models.FieldCondition(key="page_hash", match=models.MatchValue(value=page_hash)),
            ]),
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        return len(scroll) > 0
    except Exception:
        return False


async def upsert_chunks(
    qdrant: AsyncQdrantClient, oai: AsyncOpenAI,
    raw_chunks: list[dict], source: Source,
    title: str = "", published_at: Optional[datetime] = None,
    extra_tags: Optional[list[str]] = None,
    page_hash: Optional[str] = None,  # SHA-256 of full page text (for dedup)
) -> int:
    if not raw_chunks:
        return 0

    texts = [c["content"] for c in raw_chunks]
    now_ms = int(time.time() * 1000)
    pub_ms = to_epoch_ms(published_at)
    tags = extra_tags if extra_tags is not None else source.tags

    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vecs = await embed_batch(oai, texts[i:i + EMBED_BATCH])
        all_vecs.extend(vecs)

    points: list[models.PointStruct] = []
    for chunk, vec in zip(raw_chunks, all_vecs):
        sv_idx, sv_val = sparse_vector(chunk["content"])
        named: dict = {"dense": vec}
        if sv_idx:
            named["sparse"] = models.SparseVector(indices=sv_idx, values=sv_val)

        points.append(models.PointStruct(
            id=make_point_id(chunk["doc_id"], chunk["chunk_id"]),
            vector=named,
            payload={
                "doc_id": chunk["doc_id"],
                "chunk_id": chunk["chunk_id"],
                "source_id": source.id,
                "content": chunk["content"],
                "title": title,
                "section_path": chunk.get("section_path", ""),
                "source_url": chunk.get("url", ""),
                "source_type": source.adapter if source.adapter != "direct_urls" else "docs",
                "protocol": source.protocol,
                "category": source.category,
                "language": source.language,
                "published_at": pub_ms,
                "fetched_at": now_ms,
                "content_hash": content_hash(chunk["content"]),
                "page_hash": page_hash or "",
                "tags": tags,
                "license": source.license,
                "token_count": len(chunk["content"].split()),
                "embedding_model": EMBED_MODEL,
            },
        ))

    async with _qdrant_write_sem:
        for i in range(0, len(points), 100):
            for attempt in range(3):
                try:
                    await qdrant.upsert(COLLECTION, points=points[i:i + 100], wait=True)
                    break
                except Exception as e:
                    if attempt == 2:
                        log.error("Qdrant upsert failed after 3 attempts: %s", e)
                        raise
                    await asyncio.sleep(2 ** attempt)

    STATS.chunks_upserted += len(points)
    STATS.chunks_total += len(raw_chunks)
    return len(points)

# ── Per-host semaphores ───────────────────────────────────────────────────────
_host_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(3))

async def safe_get(
    client: httpx.AsyncClient, url: str, delay: float = 1.0
) -> Optional[httpx.Response]:
    host = urlparse(url).netloc
    async with _host_sems[host]:
        for attempt in range(4):
            try:
                resp = await client.get(url, timeout=60, follow_redirects=True)
                await asyncio.sleep(delay)
                if resp.status_code == 429:
                    wait = 2 ** attempt * 5
                    log.warning("429 on %s — sleeping %ds", url, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 400:
                    return None
                return resp
            except Exception as e:
                wait = 2 ** attempt
                log.debug("Fetch error %s (attempt %d): %s", url, attempt + 1, e)
                await asyncio.sleep(wait)
    return None

# ── Sitemap ───────────────────────────────────────────────────────────────────
def parse_sitemap_xml(xml_text: str) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            if loc.text:
                urls.append(loc.text.strip())
    except ET.ParseError:
        urls = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", xml_text)
    return urls

async def get_sitemap_urls(client: httpx.AsyncClient, source: Source) -> list[str]:
    sitemap_url = source.extra.get("sitemap_url") or urljoin(source.url, "/sitemap.xml")
    resp = await safe_get(client, sitemap_url, delay=0.5)
    if not resp:
        for path in ["/sitemap-index.xml", "/sitemap_index.xml", "/docs-sitemap.xml"]:
            resp = await safe_get(client, urljoin(source.url, path), delay=0.5)
            if resp:
                break
    if not resp:
        return []

    urls = parse_sitemap_xml(resp.text)
    child_urls: list[str] = []
    for u in urls:
        if u.endswith(".xml") and "sitemap" in u:
            child_resp = await safe_get(client, u, delay=0.5)
            if child_resp:
                child_urls.extend(parse_sitemap_xml(child_resp.text))
    if child_urls:
        urls = child_urls

    base_host = urlparse(source.url).netloc
    urls = [u for u in urls
            if urlparse(u).netloc == base_host or urlparse(u).netloc.endswith("." + base_host)]
    urls = [u for u in urls
            if not any(u.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".svg", ".pdf", ".zip", ".xml"])]

    # Filter out non-English locale paths (CJK language segments in URL)
    _LOCALE_RE = re.compile(r'/(?:zh|zh-cn|zh-tw|ja|ko|de|fr|es|pt|ru|tr|ar|hi|vi|th|id)(?:/|$)', re.I)
    urls = [u for u in urls if not _LOCALE_RE.search(urlparse(u).path)]

    include_pat = source.extra.get("include_pattern")
    if include_pat:
        pat = re.compile(include_pat)
        urls = [u for u in urls if pat.search(urlparse(u).path)]

    return urls[:source.max_pages]

# ── Adapters ──────────────────────────────────────────────────────────────────
def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else ""

async def crawl_links(source: Source, client: httpx.AsyncClient, max_pages: int = 100) -> list[str]:
    from html.parser import HTMLParser
    class LinkParser(HTMLParser):
        def __init__(self): super().__init__(); self.links: list[str] = []
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                for k, v in attrs:
                    if k == "href" and v: self.links.append(v)

    visited: set[str] = set()
    queue: list[str] = [source.url]
    base_host = urlparse(source.url).netloc

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        resp = await safe_get(client, url, delay=source.crawl_delay)
        if not resp:
            continue
        p = LinkParser(); p.feed(resp.text)
        for link in p.links:
            abs_link = urljoin(url, link).split("#")[0]
            if urlparse(abs_link).netloc == base_host and abs_link not in visited:
                queue.append(abs_link)

    return list(visited)


async def process_sitemap(
    source: Source, client: httpx.AsyncClient,
    qdrant: AsyncQdrantClient, oai: AsyncOpenAI, anthropic: AsyncAnthropic,
) -> None:
    log.info("[%s] Discovering URLs via sitemap...", source.id)
    urls = await get_sitemap_urls(client, source)
    if not urls:
        log.warning("[%s] No URLs from sitemap, trying link crawl...", source.id)
        urls = await crawl_links(source, client, max_pages=min(source.max_pages, 100))

    urls = urls[:source.max_pages]
    log.info("[%s] Found %d URLs", source.id, len(urls))

    source_host = urlparse(source.url).netloc
    skipped = [0]
    page_sem = asyncio.Semaphore(10)

    async def process_one(url: str) -> None:
        async with page_sem:
            resp = await safe_get(client, url, delay=source.crawl_delay)
            if not resp:
                return

            final_host = urlparse(str(resp.url)).netloc
            if final_host and final_host != source_host and not final_host.endswith("." + source_host):
                log.debug("[%s] Cross-domain redirect %s → %s, skipping", source.id, url, final_host)
                return

            STATS.pages_crawled += 1
            text = page_to_text(resp, url, excerpt_only=source.excerpt_only)
            if len(text) < 50:
                return

            # Short-circuit: if this URL's text is byte-identical to what's
            # already in Qdrant, skip the Haiku classify + OpenAI embed.
            doc_id = slug(url, source.id)
            page_hash = content_hash(text)
            if await is_page_unchanged(qdrant, doc_id, page_hash):
                STATS.pages_unchanged += 1
                log.debug("[%s] UNCHANGED: %s", source.id, url)
                return

            title = _extract_title(resp.text)
            clf = await classify_page(anthropic, title, text, url)

            if not clf.keep:
                skipped[0] += 1
                STATS.pages_skipped += 1
                log.debug("[%s] SKIP: %s", source.id, url)
                return

            merged_tags = list(dict.fromkeys(source.tags + clf.tags))
            chunks = chunk_text(text, doc_id, clf.section_anchors)
            for c in chunks:
                c["url"] = url
            await upsert_chunks(qdrant, oai, chunks, source, title=title,
                                extra_tags=merged_tags, page_hash=page_hash)

    await asyncio.gather(*[process_one(u) for u in urls])
    log.info("[%s] done | crawled=%d unchanged=%d skipped=%d",
             source.id, STATS.pages_crawled, STATS.pages_unchanged, skipped[0])


async def process_rss(
    source: Source, client: httpx.AsyncClient, qdrant: AsyncQdrantClient, oai: AsyncOpenAI,
) -> None:
    log.info("[%s] Fetching RSS feed...", source.id)
    import feedparser
    resp = await safe_get(client, source.url, delay=0.5)
    if not resp:
        log.warning("[%s] RSS fetch failed", source.id); return

    feed = feedparser.parse(resp.text)
    entries = feed.entries[:source.max_pages]
    log.info("[%s] %d RSS entries", source.id, len(entries))

    for entry in entries:
        title = getattr(entry, "title", "")
        link = getattr(entry, "link", "")
        summary = getattr(entry, "summary", "") or ""
        if len(summary) > MAX_EXCERPT:
            summary = summary[:MAX_EXCERPT].rsplit(" ", 1)[0] + "…"
        body = f"Source: {link}\n\n{summary}".strip()
        if len(body) < 30:
            continue
        published = None
        try:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
        doc_id = slug(link or title, source.id)
        page_hash = content_hash(body)
        if await is_page_unchanged(qdrant, doc_id, page_hash):
            STATS.pages_unchanged += 1
            continue
        chunks = [{"doc_id": doc_id, "chunk_id": 0, "content": body,
                   "section_path": "", "url": link}]
        STATS.pages_crawled += 1
        await upsert_chunks(qdrant, oai, chunks, source, title=title,
                            published_at=published, page_hash=page_hash)


async def process_github_raw(
    source: Source, client: httpx.AsyncClient, qdrant: AsyncQdrantClient, oai: AsyncOpenAI,
) -> None:
    single = source.extra.get("single_file", False)

    if single:
        log.info("[%s] Fetching single file: %s", source.id, source.url)
        resp = await safe_get(client, source.url, delay=source.crawl_delay)
        if not resp:
            return
        doc_id = source.id
        page_hash = content_hash(resp.text)
        if await is_page_unchanged(qdrant, doc_id, page_hash):
            STATS.pages_unchanged += 1
            log.info("[%s] UNCHANGED — skipping ingest", source.id)
            return
        chunks = chunk_text(resp.text, doc_id, [{"label": "Overview", "anchor": ""}])
        for c in chunks:
            c["url"] = source.url
        STATS.pages_crawled += 1
        await upsert_chunks(qdrant, oai, chunks, source, title=source.id, page_hash=page_hash)
        log.info("[%s] Ingested %d chunks", source.id, len(chunks))
        return

    repo = source.extra.get("repo", "")
    branch = source.extra.get("branch", "main")
    path = source.extra.get("path", "docs")
    if not repo:
        return

    api_url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    headers = {"User-Agent": UA}
    gh_token = ENV.get("GITHUB_TOKEN", "")
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"

    try:
        resp = await client.get(api_url, headers=headers, timeout=30)
        tree = resp.json().get("tree", [])
    except Exception as e:
        log.warning("[%s] GitHub API failed: %s", source.id, e); return

    md_files = [f for f in tree if f.get("type") == "blob"
                and f.get("path", "").endswith(".md")
                and f.get("path", "").startswith(path)][:source.max_pages]

    log.info("[%s] Found %d markdown files in %s/%s", source.id, len(md_files), repo, path)

    for f in md_files:
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{f['path']}"
        resp = await safe_get(client, raw_url, delay=source.crawl_delay)
        if not resp:
            continue
        doc_id = slug(f["path"], source.id)
        page_hash = content_hash(resp.text)
        if await is_page_unchanged(qdrant, doc_id, page_hash):
            STATS.pages_unchanged += 1
            continue
        chunks = chunk_text(resp.text, doc_id, [{"label": "Overview", "anchor": ""}])
        for c in chunks:
            c["url"] = raw_url
        STATS.pages_crawled += 1
        await upsert_chunks(qdrant, oai, chunks, source, title=f["path"], page_hash=page_hash)


async def process_defillama(
    source: Source, client: httpx.AsyncClient, qdrant: AsyncQdrantClient, oai: AsyncOpenAI,
) -> None:
    log.info("[%s] Fetching DeFiLlama protocol list...", source.id)
    resp = await safe_get(client, source.url, delay=0.1)
    if not resp:
        return
    try:
        protocols = resp.json()[:source.max_pages]
    except Exception:
        return

    log.info("[%s] %d protocols to ingest", source.id, len(protocols))
    chunks: list[dict] = []
    doc_count = 0

    for proto in protocols:
        name = proto.get("name", "")
        description = proto.get("description", "") or ""
        category = proto.get("category", "") or ""
        chain = ",".join(proto.get("chains", [])[:5]) if proto.get("chains") else ""
        tvl = proto.get("tvl", 0)
        slug_id = proto.get("slug", name.lower().replace(" ", "_"))
        url_proto = proto.get("url", "")
        if not name or not description:
            continue
        # NOTE: TVL is excluded from the dedup hash because it changes on every
        # crawl and would defeat the cache. The body still includes it so the
        # stored chunk reflects current TVL when we DO upsert.
        body = f"# {name}\n\n{description}\n\n**Category**: {category}\n**Chains**: {chain}\n**TVL**: ${tvl:,.0f}\n"
        dedup_body = f"{name}|{description}|{category}|{chain}"
        doc_id = f"defillama.{slug_id}"
        page_hash = content_hash(dedup_body)
        if await is_page_unchanged(qdrant, doc_id, page_hash):
            STATS.pages_unchanged += 1
            continue
        # Tag the chunk with its page_hash so the dedup check works on the
        # next run. DefiLlama uses one chunk per protocol so it's safe to
        # carry the hash on the chunk dict directly.
        chunks.append({"doc_id": doc_id, "chunk_id": 0, "content": body,
                        "section_path": category, "url": url_proto,
                        "page_hash": page_hash})
        doc_count += 1
        # Don't batch upsert when chunks have heterogeneous page_hash values —
        # the upsert_chunks signature only accepts one. Flush per-chunk so
        # each protocol gets its own hash recorded.
        if len(chunks) >= 50:
            for c in chunks:
                await upsert_chunks(qdrant, oai, [c], source, page_hash=c.get("page_hash"))
            chunks = []
            STATS.pages_crawled += 50

    if chunks:
        for c in chunks:
            await upsert_chunks(qdrant, oai, [c], source, page_hash=c.get("page_hash"))
        STATS.pages_crawled += len(chunks)
    log.info("[%s] Ingested %d protocol entries", source.id, doc_count)


async def process_direct_urls(
    source: Source, client: httpx.AsyncClient,
    qdrant: AsyncQdrantClient, oai: AsyncOpenAI, anthropic: AsyncAnthropic,
) -> None:
    urls = source.extra.get("urls", [])
    log.info("[%s] Processing %d direct URLs", source.id, len(urls))

    async def process_one(url: str) -> None:
        resp = await safe_get(client, url, delay=source.crawl_delay)
        if not resp:
            return
        text = page_to_text(resp, url, excerpt_only=source.excerpt_only)
        if len(text) < 50:
            return

        # Short-circuit on identical content — saves Haiku + embed.
        doc_id = slug(url, source.id)
        page_hash = content_hash(text)
        if await is_page_unchanged(qdrant, doc_id, page_hash):
            STATS.pages_unchanged += 1
            log.debug("[%s] UNCHANGED: %s", source.id, url)
            return

        title = _extract_title(resp.text)
        if source.extra.get("skip_classify"):
            # force-keep: for a source whose useful pages are how-to/recipe guides
            # the classifier wrongly drops as "code tutorials" (e.g. Magic Eden).
            clf = ClassifyResult(keep=True, tags=[], summary="",
                                 section_anchors=[{"label": "Overview", "anchor": ""}])
        else:
            clf = await classify_page(anthropic, title, text, url)
            if not clf.keep:
                STATS.pages_skipped += 1
                log.debug("[%s] SKIP: %s", source.id, url)
                return

        merged_tags = list(dict.fromkeys(source.tags + clf.tags))
        chunks = chunk_text(text, doc_id, clf.section_anchors)
        for c in chunks:
            c["url"] = url
        STATS.pages_crawled += 1
        await upsert_chunks(qdrant, oai, chunks, source, title=title,
                            extra_tags=merged_tags, page_hash=page_hash)

    await asyncio.gather(*[process_one(u) for u in urls])

# ── Orchestration ─────────────────────────────────────────────────────────────
async def process_source(
    source: Source, client: httpx.AsyncClient,
    qdrant: AsyncQdrantClient, oai: AsyncOpenAI, anthropic: AsyncAnthropic,
) -> None:
    try:
        # Skip entirely if the source has been crawled within its configured
        # `crawl_freq` window. This is the cheap front-door check — protects
        # against running an hourly cron over slow-moving docs sites whose
        # actual update cadence is weekly or monthly.
        if await should_skip_source(qdrant, source.id, source.crawl_freq):
            log.info("SKIP  [%s] within %s freq window", source.id, source.crawl_freq)
            return

        log.info("=" * 60)
        log.info("START [%s] (%s, freq=%s) — %s",
                 source.id, source.adapter, source.crawl_freq, source.url)
        t0 = time.time()

        if source.adapter == "sitemap":
            await process_sitemap(source, client, qdrant, oai, anthropic)
        elif source.adapter == "rss":
            await process_rss(source, client, qdrant, oai)
        elif source.adapter == "github_raw":
            await process_github_raw(source, client, qdrant, oai)
        elif source.adapter == "defillama":
            await process_defillama(source, client, qdrant, oai)
        elif source.adapter == "direct_urls":
            await process_direct_urls(source, client, qdrant, oai, anthropic)

        elapsed = int(time.time() - t0)
        log.info("DONE  [%s] in %ds | %s", source.id, elapsed, STATS.summary())
        STATS.sources_done += 1

    except Exception as e:
        log.error("FAIL  [%s]: %s", source.id, e, exc_info=True)
        STATS.sources_failed += 1


async def main(groups: set[str], dry_run: bool) -> None:
    sources = [s for s in SOURCES if not groups or s.group in groups]
    log.info("Crawling %d sources | Groups: %s | Dry-run: %s",
             len(sources), groups or "all", dry_run)

    if dry_run:
        for s in sources:
            print(f"  [{s.group}] {s.id} — {s.url}")
        return

    oai = AsyncOpenAI(api_key=OPENAI_KEY)
    anthropic = AsyncAnthropic(api_key=ANTHROPIC_KEY)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False)
    await ensure_collection(qdrant)

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(60.0), follow_redirects=True
    ) as client:
        group_order = ["solana", "protocols", "data", "bridges", "nft",
                       "education", "news", "github", "blockchain"]
        ordered = sorted(sources,
                         key=lambda s: (group_order.index(s.group)
                                        if s.group in group_order else 99, s.id))

        SOURCE_CONCURRENCY = 12
        sem = asyncio.Semaphore(SOURCE_CONCURRENCY)

        async def bounded(source: Source) -> None:
            async with sem:
                await process_source(source, client, qdrant, oai, anthropic)

        await asyncio.gather(*[bounded(s) for s in ordered])

    log.info("=" * 60)
    log.info("ALL DONE: %s", STATS.summary())
    await qdrant.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OPRAI bulk knowledge crawler")
    parser.add_argument("--source", nargs="*",
                        help="Source groups to crawl")
    parser.add_argument("--dry-run", action="store_true",
                        help="List sources without crawling")
    parser.add_argument("--only", nargs="*",
                        help="Specific source IDs to crawl")
    args = parser.parse_args()

    groups: set[str] = set(args.source or [])
    if args.only:
        for src in SOURCES:
            if src.id in args.only:
                groups.add(src.group)
        SOURCES[:] = [s for s in SOURCES if s.id in args.only]

    asyncio.run(main(groups, args.dry_run))
