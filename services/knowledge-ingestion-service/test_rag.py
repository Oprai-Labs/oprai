#!/usr/bin/env python3
"""
RAG Knowledge Base Quality Test
Simulates exactly what chat-service-py does when a user asks a question.
"""
from __future__ import annotations
import asyncio, hashlib, os, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

# ── Config (mirrors chat-service-py settings) ─────────────────────────────────
def _load_env():
    env = {}
    p = os.path.join(os.path.dirname(__file__), "../../.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env

ENV = _load_env()
OPENAI_KEY   = ENV.get("OPRAI_OPENAI_API_KEY", "")
QDRANT_URL   = ENV.get("QDRANT_URL", "http://localhost:6333")
COLLECTION   = "oprai_blockchain_knowledge"
EMBED_MODEL  = "text-embedding-3-large"
EMBED_DIM    = 3072
UUID_NS      = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
TOP_K        = 50
RERANK_TOP_N = 5
TOKEN_BUDGET = 1500
SCORE_THRESH = 0.3

# OpenAI pricing (per 1M tokens)
EMBED_PRICE_PER_M  = 0.13   # text-embedding-3-large
GPT4O_IN_PER_M     = 2.50   # gpt-4o input
GPT4O_OUT_PER_M    = 10.00  # gpt-4o output

# ── Test queries — 20 questions across difficulty levels ─────────────────────
QUERIES = [
    # Solana Core
    ("EASY",   "What is liquid staking on Solana?"),
    ("EASY",   "How does mSOL work with Marinade Finance?"),
    ("EASY",   "What is impermanent loss in DeFi?"),
    # Protocol-specific
    ("MEDIUM", "What are the risks of Kamino Multiply?"),
    ("MEDIUM", "How does Jupiter DCA work and what fees does it charge?"),
    ("MEDIUM", "Explain JLP (Jupiter Liquidity Provider) payoff structure"),
    ("MEDIUM", "What is the difference between Marinade and Jito for staking?"),
    ("MEDIUM", "How does Meteora DLMM work compared to regular AMMs?"),
    # Advanced
    ("HARD",   "What is MEV and how do Jito bundles prevent frontrunning?"),
    ("HARD",   "Explain Kamino multiply leverage risks and liquidation mechanics"),
    ("HARD",   "How does Orca Whirlpool concentrated liquidity differ from Uniswap v3?"),
    ("HARD",   "What are the tokenomics of JUP and how does governance work?"),
    # Cross-protocol
    ("HARD",   "Compare yield strategies: Marinade native stake vs Kamino vaults vs Jito staking"),
    ("HARD",   "How does Circle CCTP enable USDC bridging without wrapped tokens?"),
    # Education
    ("EASY",   "What is a flash loan and how is it used in DeFi?"),
    ("EASY",   "What is yield farming and what are the risks?"),
    ("MEDIUM", "How do AMMs like Raydium determine token prices?"),
    # Edge cases
    ("EDGE",   "What is the Solana Firedancer validator client?"),
    ("EDGE",   "How does Realms DAO governance work on Solana?"),
    ("EDGE",   "What is pump.fun bonding curve mechanism?"),
]

# ── Retrieval ─────────────────────────────────────────────────────────────────
async def embed(oai: AsyncOpenAI, text: str, usage: list) -> list[float]:
    resp = await oai.embeddings.create(model=EMBED_MODEL, input=text, dimensions=EMBED_DIM)
    usage.append(resp.usage.total_tokens)
    return resp.data[0].embedding

async def search(qdrant: AsyncQdrantClient, vec: list[float]) -> list[dict]:
    for attempt in range(3):
        try:
            results = await qdrant.query_points(
                collection_name=COLLECTION,
                query=vec,
                using="dense",
                limit=TOP_K,
                with_payload=True,
                score_threshold=SCORE_THRESH,
                timeout=60,
            )
            return [{"payload": r.payload, "score": r.score} for r in results.points if r.payload]
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)

def diversity_budget(results: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    selected = []
    tokens_used = 0
    for r in results:
        doc_id = r["payload"].get("doc_id", "")
        count = seen.get(doc_id, 0)
        if count >= 2:
            continue
        tok = r["payload"].get("token_count", len(r["payload"].get("content","").split()))
        if tokens_used + tok > TOKEN_BUDGET:
            continue
        seen[doc_id] = count + 1
        tokens_used += tok
        selected.append(r)
        if len(selected) >= RERANK_TOP_N:
            break
    return selected

def format_context(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    parts = ["[Knowledge Context]"]
    for c in chunks:
        p = c["payload"]
        doc_id = p.get("doc_id", "?")
        chunk_id = p.get("chunk_id", 0)
        protocol = p.get("protocol") or p.get("source_id", "")
        source_type = p.get("source_type", "")
        ts = ""
        if p.get("published_at"):
            try:
                ts = datetime.fromtimestamp(p["published_at"]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except: pass
        meta = " / ".join(x for x in [protocol, source_type, ts] if x)
        section = f"Section: {p.get('section_path','')}" if p.get("section_path") else ""
        title = p.get("title","") or doc_id
        parts.append(
            f"---\n[{doc_id}:{chunk_id}]" +
            (f" ({meta})" if meta else "") +
            (f" — {title}" if title != doc_id else "") + "\n" +
            (f"{section}\n" if section else "") +
            p.get("content","").strip()
        )
    return "\n".join(parts)

# ── Test runner ───────────────────────────────────────────────────────────────
@dataclass
class Result:
    difficulty: str
    query: str
    n_retrieved: int
    n_after_budget: int
    top_score: float
    min_score: float
    sources: list[str]
    section_paths: list[str]
    context_tokens: int
    embed_tokens: int
    context_block: str
    latency_ms: int
    verdict: str = ""

async def run_tests():
    oai    = AsyncOpenAI(api_key=OPENAI_KEY)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False, timeout=60)

    print("=" * 72)
    print("OPRAI RAG Knowledge Base — Quality Test")
    print(f"Collection: {COLLECTION}  |  Top-K: {TOP_K}  |  Budget: {TOKEN_BUDGET} tok")
    print("=" * 72)

    results: list[Result] = []
    total_embed_tokens = 0

    for diff, query in QUERIES:
        usage: list[int] = []
        t0 = time.perf_counter()
        vec = await embed(oai, query, usage)
        raw = await search(qdrant, vec)
        final = diversity_budget(raw)
        ctx = format_context(final)
        latency = int((time.perf_counter() - t0) * 1000)
        embed_tok = usage[0] if usage else 0
        total_embed_tokens += embed_tok

        ctx_tokens = len(ctx.split())  # rough word count as proxy
        top_score  = raw[0]["score"] if raw else 0.0
        min_score  = final[-1]["score"] if final else 0.0
        sources    = [r["payload"].get("source_id","?") for r in final]
        sections   = [r["payload"].get("section_path","")[:50] for r in final]

        # Verdict
        if not final:
            verdict = "❌ NO RESULTS"
        elif top_score >= 0.6:
            verdict = "✅ STRONG"
        elif top_score >= 0.45:
            verdict = "🟡 MODERATE"
        else:
            verdict = "🔴 WEAK"

        r = Result(diff, query, len(raw), len(final), top_score, min_score,
                   sources, sections, ctx_tokens, embed_tok, ctx, latency, verdict)
        results.append(r)

        # Live output
        print(f"\n[{diff}] {query}")
        print(f"  Verdict : {verdict}  |  Retrieved: {len(raw)}→{len(final)}  |  Scores: {top_score:.3f}–{min_score:.3f}")
        print(f"  Sources : {', '.join(set(sources))}")
        print(f"  Sections: {' | '.join(s for s in sections if s)[:120]}")
        print(f"  Ctx tok : ~{ctx_tokens}  |  Embed tok: {embed_tok}  |  Latency: {latency}ms")

        await asyncio.sleep(0.1)

    await qdrant.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)

    strong   = sum(1 for r in results if "STRONG"   in r.verdict)
    moderate = sum(1 for r in results if "MODERATE" in r.verdict)
    weak     = sum(1 for r in results if "WEAK"     in r.verdict)
    no_res   = sum(1 for r in results if "NO RESULTS" in r.verdict)
    avg_lat  = sum(r.latency_ms for r in results) // len(results)
    avg_score = sum(r.top_score for r in results) / len(results)

    print(f"  ✅ Strong   : {strong}/{len(results)}")
    print(f"  🟡 Moderate : {moderate}/{len(results)}")
    print(f"  🔴 Weak     : {weak}/{len(results)}")
    print(f"  ❌ No results: {no_res}/{len(results)}")
    print(f"  Avg top score : {avg_score:.3f}")
    print(f"  Avg latency   : {avg_lat}ms")
    print()

    # ── Cost breakdown ────────────────────────────────────────────────────────
    print("TOKEN & COST ANALYSIS")
    print("-" * 40)
    embed_cost = total_embed_tokens / 1_000_000 * EMBED_PRICE_PER_M

    # Context block is injected as system message → input tokens for GPT-4o
    avg_ctx_tokens = sum(r.context_tokens for r in results) / len(results)
    # Typical OPRAI system prompt: ~800 tokens, wallet context: ~200, action prompt: ~600
    base_system_tokens = 1600
    typical_user_msg   = 30   # user question
    typical_output     = 200  # LLM answer tokens

    total_input_per_turn  = base_system_tokens + avg_ctx_tokens + typical_user_msg
    total_output_per_turn = typical_output

    gpt4o_in_cost  = total_input_per_turn  / 1_000_000 * GPT4O_IN_PER_M
    gpt4o_out_cost = total_output_per_turn / 1_000_000 * GPT4O_OUT_PER_M
    total_per_turn = embed_cost / len(QUERIES) + gpt4o_in_cost + gpt4o_out_cost

    print(f"  Embed tokens (20 queries)  : {total_embed_tokens:,} tok  →  ${embed_cost:.4f}")
    print(f"  Avg embed / query          : {total_embed_tokens // len(QUERIES)} tok  →  ${embed_cost/len(QUERIES):.5f}")
    print(f"  Avg context block size     : ~{avg_ctx_tokens:.0f} words (~{avg_ctx_tokens*1.3:.0f} tokens)")
    print()
    print(f"  Per advice turn (GPT-4o):")
    print(f"    Base system prompt       : ~{base_system_tokens} tok")
    print(f"    RAG context block        : ~{avg_ctx_tokens*1.3:.0f} tok")
    print(f"    User message             : ~{typical_user_msg} tok")
    print(f"    Total input              : ~{total_input_per_turn:.0f} tok  →  ${gpt4o_in_cost:.4f}")
    print(f"    Output                   : ~{typical_output} tok  →  ${gpt4o_out_cost:.4f}")
    print(f"    Embed query              : ~{total_embed_tokens//len(QUERIES)} tok  →  ${embed_cost/len(QUERIES):.5f}")
    print(f"    ─────────────────────────────────────────")
    print(f"    TOTAL per advice turn    : ~${total_per_turn:.4f}")
    print()

    # Monthly projections
    for dau_advice in [100, 500, 1000, 5000]:
        monthly = dau_advice * 30 * total_per_turn
        print(f"  {dau_advice:5d} advice turns/day  →  ${monthly:.2f}/month")

    # Show one full context block example
    print("\n" + "=" * 72)
    print("SAMPLE CONTEXT BLOCK — 'What are the risks of Kamino Multiply?'")
    print("=" * 72)
    for r in results:
        if "Kamino Multiply" in r.query:
            print(r.context_block[:3000])
            break

if __name__ == "__main__":
    asyncio.run(run_tests())
