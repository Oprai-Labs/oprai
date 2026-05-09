#!/usr/bin/env python3
"""
Delete existing chunks for SEMANTIC_SOURCES from Qdrant,
then re-crawl them with the new semantic chunker.
"""
import asyncio
import subprocess
import sys
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

COLLECTION = "oprai_blockchain_knowledge"
QDRANT_URL = "http://localhost:6333"

SEMANTIC_SOURCES = [
    "binance_academy", "coinbase_learn", "coinbase_learn2",
    "kraken_learn", "kraken_learn2", "coingecko_learn", "coingecko_learn2",
    "cmc_alexandria", "cmc_alexandria2", "bybit_learn",
    "investopedia_crypto2", "coindesk_learn", "finematics_blog",
]

async def delete_source_chunks(source_id: str, qdrant: AsyncQdrantClient) -> int:
    result = await qdrant.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(
                    key="source_id",
                    match=models.MatchValue(value=source_id)
                )]
            )
        ),
    )
    print(f"  Deleted chunks for [{source_id}]: {result.status}")
    return 0

async def main():
    qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False)
    before = await qdrant.count(COLLECTION)
    print(f"Points before: {before.count}")

    for src in SEMANTIC_SOURCES:
        await delete_source_chunks(src, qdrant)

    after = await qdrant.count(COLLECTION)
    print(f"Points after deletion: {after.count} (removed {before.count - after.count})")
    await qdrant.close()

if __name__ == "__main__":
    asyncio.run(main())
