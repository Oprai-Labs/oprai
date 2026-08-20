#!/usr/bin/env python3
"""
OPRAI RAG Full Quality Test — 150 queries
- generates a real LLM answer (gpt-4o-mini)
- scores answer quality automatically
- reports the full Haiku + embedding + LLM cost

The questions and the eval system prompt are in Turkish on purpose: the quality
checks below look for Turkish phrases in the answer, so the two move together.
"""
from __future__ import annotations
import asyncio, json, os, re, sys, time, uuid
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

# ── Config ────────────────────────────────────────────────────────────────────
def _env():
    e = {}
    p = os.path.join(os.path.dirname(__file__), "../../.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("="); e[k.strip()] = v.strip()
    e.update(os.environ); return e

ENV          = _env()
OPENAI_KEY   = ENV.get("OPRAI_OPENAI_API_KEY", "")
QDRANT_URL   = ENV.get("QDRANT_URL", "http://localhost:6333")
COLLECTION   = "oprai_blockchain_knowledge"
EMBED_MODEL  = "text-embedding-3-large"
EMBED_DIM    = 3072
ANSWER_MODEL = "gpt-4o-mini"   # cheap, fast, good enough to grade quality
TOP_K        = 50
TOP_N        = 5
TOKEN_BUDGET = 1500
SCORE_THRESH = 0.3

# Fiyatlar ($ per 1M token)
EMBED_PRICE      = 0.13    # text-embedding-3-large
HAIKU_IN_PRICE   = 0.80    # claude-haiku-4-5 input
HAIKU_OUT_PRICE  = 4.00    # claude-haiku-4-5 output
MINI_IN_PRICE    = 0.15    # gpt-4o-mini input
MINI_OUT_PRICE   = 0.60    # gpt-4o-mini output
GPT4O_IN_PRICE   = 2.50    # gpt-4o input (prod)
GPT4O_OUT_PRICE  = 10.00   # gpt-4o output (prod)

CRAWL_PAGES_CLASSIFIED = 7363   # crawl log'dan: 4735 crawled + 2628 skipped
CRAWL_EMBED_TOKENS     = 1_841_162  # crawl log'dan

SYSTEM_PROMPT = """Sen OPRAI'ın blockchain/DeFi/Solana uzmanı asistanısın.
Sana verilen [Knowledge Context] bloğunu kullanarak soruyu yanıtla.
Her kullandığın bilgiyi [doc_id:chunk_id] formatında kaynak göster.
Eğer context'te cevap yoksa "Bu konuda bilgim yok" de, uydurma."""

# ── 150 Test Queries ──────────────────────────────────────────────────────────
QUERIES = [
    # ── Solana Core (20) ────────────────────────────────────────────────────
    ("solana_core", "Solana'da hesap modeli nasıl çalışır?"),
    ("solana_core", "Proof of History nedir ve nasıl çalışır?"),
    ("solana_core", "Solana'da transaction fee nasıl hesaplanır?"),
    ("solana_core", "Program Derived Address (PDA) nedir?"),
    ("solana_core", "Solana'da validator olmak için ne gerekir?"),
    ("solana_core", "SPL Token nedir, nasıl oluşturulur?"),
    ("solana_core", "Solana'da native stake ile liquid stake farkı nedir?"),
    ("solana_core", "Rent exemption nedir, neden önemlidir?"),
    ("solana_core", "Solana gossip protokolü nasıl çalışır?"),
    ("solana_core", "Transaction confirmation seviyeleri nelerdir (processed/confirmed/finalized)?"),
    ("solana_core", "Solana'da compute units ve compute budget nedir?"),
    ("solana_core", "Cross Program Invocation (CPI) nedir?"),
    ("solana_core", "Solana'da priority fee nasıl belirlenir?"),
    ("solana_core", "Firedancer validator client nedir?"),
    ("solana_core", "Solana'da epoch nedir, kaç slot sürer?"),
    ("solana_core", "Token 2022 (Token Extensions) programı ne ekliyor?"),
    ("solana_core", "Solana'da shred nedir?"),
    ("solana_core", "Versioned transactions ve address lookup tables nedir?"),
    ("solana_core", "Solana'da block production nasıl çalışır?"),
    ("solana_core", "Solana'da program upgrade nasıl yapılır?"),

    # ── Liquid Staking (15) ──────────────────────────────────────────────────
    ("staking", "mSOL ile ne yapabilirim?"),
    ("staking", "Marinade native stake nedir, liquid stake'ten farkı nedir?"),
    ("staking", "JitoSOL'un diğer LST'lerden farkı nedir?"),
    ("staking", "Sanctum Infinity pool nasıl çalışır?"),
    ("staking", "Liquid staking token (LST) unstake süreci kaç gün sürer?"),
    ("staking", "mSOL APY nasıl hesaplanır?"),
    ("staking", "Marinade ve Jito arasında validator seçimi nasıl farklılaşır?"),
    ("staking", "Sanctum LST router ne işe yarar?"),
    ("staking", "JitoSOL mev tiplerinden nasıl yararlanıyor?"),
    ("staking", "Stake pool'ların likit unstake mekanizması nasıl çalışır?"),
    ("staking", "Lido'nun stETH tokenı nasıl çalışır?"),
    ("staking", "Ethereum'da liquid staking riskleri nelerdir?"),
    ("staking", "Marinade delayed unstake ile instant unstake farkı nedir?"),
    ("staking", "Stake farming nedir, riskler nelerdir?"),
    ("staking", "Solana'da stake yield kaynakları nelerdir?"),

    # ── DEX / AMM (15) ──────────────────────────────────────────────────────
    ("dex", "Jupiter swap routing nasıl çalışır?"),
    ("dex", "Concentrated liquidity (CLMM) nedir?"),
    ("dex", "Orca Whirlpool'da fee tier nasıl seçilir?"),
    ("dex", "Raydium CLMM ile Orca Whirlpool farkları nedir?"),
    ("dex", "Meteora DLMM bin step nedir?"),
    ("dex", "AMM'de constant product formula (x*y=k) nasıl çalışır?"),
    ("dex", "Jupiter DCA ile nasıl otomatik alım yapabilirim?"),
    ("dex", "Impermanent loss (IL) nasıl hesaplanır?"),
    ("dex", "Uniswap v3 concentrated liquidity nasıl çalışır?"),
    ("dex", "Curve Finance stableswap invariant nedir?"),
    ("dex", "Raydium AMM ile CLMM arasındaki fark nedir?"),
    ("dex", "Meteora Dynamic Pool nedir?"),
    ("dex", "Jupiter limit order nasıl çalışır?"),
    ("dex", "LP token nedir, nasıl kazanç sağlanır?"),
    ("dex", "DEX aggregator nasıl çalışır?"),

    # ── Lending / Borrowing (15) ─────────────────────────────────────────────
    ("lending", "Kamino Lend'de collateral ratio nedir?"),
    ("lending", "DeFi lending'de likidasyonu tetikleyen nedir?"),
    ("lending", "Kamino Multiply nasıl çalışır?"),
    ("lending", "Solend'de isolated pool ile main pool farkı nedir?"),
    ("lending", "Flash loan nedir ve arbitraj için nasıl kullanılır?"),
    ("lending", "Aave'de health factor nedir?"),
    ("lending", "Lending protokollerinde utilization rate APY'yi nasıl etkiler?"),
    ("lending", "DeFi lending'de over-collateralization neden gerekli?"),
    ("lending", "Kamino klend market nedir?"),
    ("lending", "Liquidation penalty genellikle ne kadar?"),
    ("lending", "Borrow APY ile Supply APY farkı nasıl oluşur?"),
    ("lending", "Kamino vaultları lending ile nasıl entegre?"),
    ("lending", "DeFi lending'de oracle manipülasyon riski nedir?"),

    # ── Perps / Trading (12) ─────────────────────────────────────────────────
    ("perps", "Jupiter Perps nasıl çalışır?"),
    ("perps", "JLP (Jupiter Liquidity Provider) token nedir?"),
    ("perps", "JLP tutmanın riskleri nelerdir?"),
    ("perps", "Drift Protocol perps mekanizması nedir?"),
    ("perps", "Funding rate nedir, perps tradingleri nasıl etkiler?"),
    ("perps", "Drift DLOB (Decentralized Limit Order Book) nedir?"),
    ("perps", "GMX GLP ile JLP karşılaştırması"),
    ("perps", "Perps'de mark price ile index price farkı nedir?"),
    ("perps", "Jupiter Perps'de max leverage kaçtır?"),
    ("perps", "Liquidation price nasıl hesaplanır?"),
    ("perps", "Delta neutral strateji nedir?"),
    ("perps", "Drift'te cross-margin nasıl çalışır?"),

    # ── Bridges / Cross-chain (10) ────────────────────────────────────────────
    ("bridges", "Wormhole köprüsü nasıl çalışır?"),
    ("bridges", "Circle CCTP nedir, neden wrap'siz transfer yapar?"),
    ("bridges", "LayerZero omnichain messaging nedir?"),
    ("bridges", "deBridge nasıl çalışır?"),
    ("bridges", "Köprü güvenlik riskleri nelerdir?"),
    ("bridges", "Solana'ya USDC getirmenin en güvenli yolu nedir?"),
    ("bridges", "Wormhole guardian network nedir?"),
    ("bridges", "Cross-chain bridge exploit'leri nasıl gerçekleşir?"),
    ("bridges", "Native USDC ile wrapped USDC farkı nedir?"),
    ("bridges", "Allbridge ile Wormhole farkları nedir?"),

    # ── Token Launch / NFT (10) ───────────────────────────────────────────────
    ("tokens", "pump.fun bonding curve nasıl çalışır?"),
    ("tokens", "Token migration pump.fun'dan neden yapılır?"),
    ("tokens", "Metaplex NFT standardı nedir?"),
    ("tokens", "Tensor NFT marketplace özellikleri nelerdir?"),
    ("tokens", "SPL token mint authority nedir?"),
    ("tokens", "NFT royalty mekanizması Solana'da nasıl çalışır?"),
    ("tokens", "Compressed NFT (cNFT) nedir, avantajları neler?"),
    ("tokens", "Token vesting nedir, Streamflow nasıl kullanılır?"),
    ("tokens", "Solana Name Service (SNS) nedir?"),
    ("tokens", "Squads multisig ile token treasury nasıl yönetilir?"),

    # ── Oracles / Data (8) ────────────────────────────────────────────────────
    ("oracles", "Pyth Network fiyat oracle'ları nasıl çalışır?"),
    ("oracles", "Chainlink VRF nedir?"),
    ("oracles", "Oracle manipülasyon saldırısı nedir?"),
    ("oracles", "Pyth confidence interval ne anlama gelir?"),
    ("oracles", "Switchboard oracle Pyth'ten nasıl farklı?"),
    ("oracles", "DeFi protokolleri hangi oracle'ları kullanır?"),
    ("oracles", "Price feed güvenilirliği nasıl sağlanır?"),
    ("oracles", "Helius RPC API nedir, ne işe yarar?"),

    # ── DeFi Education (20) ───────────────────────────────────────────────────
    ("education", "DeFi nedir, CeFi'den farkı nedir?"),
    ("education", "Yield farming nedir?"),
    ("education", "TVL (Total Value Locked) nedir?"),
    ("education", "Smart contract nedir?"),
    ("education", "DAO nedir, nasıl çalışır?"),
    ("education", "Stablecoin tipleri nelerdir?"),
    ("education", "Algo stablecoin neden çöktü (UST örneği)?"),
    ("education", "MEV nedir?"),
    ("education", "Slippage nedir, nasıl minimize edilir?"),
    ("education", "Gas fee nedir?"),
    ("education", "Web3 cüzdanı nedir?"),
    ("education", "Seed phrase güvenliği neden önemli?"),
    ("education", "DeFi'de rug pull nedir?"),
    ("education", "Tokenomics nedir?"),
    ("education", "Governance token ne işe yarar?"),
    ("education", "Layer 2 nedir?"),
    ("education", "Ethereum ve Solana mimarisi farkı nedir?"),
    ("education", "DeFi lending ile traditional lending farkı?"),
    ("education", "Proof of Stake nedir?"),
    ("education", "Kripto portföy diversifikasyonu nasıl yapılır?"),

    # ── Governance / DAO (8) ──────────────────────────────────────────────────
    ("governance", "Realms DAO nasıl kullanılır?"),
    ("governance", "Jupiter DAO governance süreci nasıl işler?"),
    ("governance", "Snapshot voting nedir?"),
    ("governance", "On-chain vs off-chain governance farkı?"),
    ("governance", "veToken modeli nedir?"),
    ("governance", "Marinade governance nerede gerçekleşir?"),
    ("governance", "DAO treasury nasıl yönetilir?"),
    ("governance", "SPL Governance programı nedir?"),

    # ── Cross-protocol / Strategy (10) ────────────────────────────────────────
    ("strategy", "Solana'da en yüksek güvenli yield nereden gelir?"),
    ("strategy", "JLP delta neutral strateji nasıl kurulur?"),
    ("strategy", "mSOL ile lending stratejisi nasıl yapılır?"),
    ("strategy", "Leveraged yield farming riskleri nelerdir?"),
    ("strategy", "Stablecoin yield farming Solana'da nasıl yapılır?"),
    ("strategy", "LST ile collateral verip borrow yapmanın riskleri?"),
    ("strategy", "DCA stratejisi neden etkili?"),
    ("strategy", "Arbitraj botu Solana'da nasıl çalışır?"),
    ("strategy", "Yield aggregator nedir?"),
    ("strategy", "Portföyde stablecoin oranı ne olmalı?"),

    # ── Edge / Niche (7) ──────────────────────────────────────────────────────
    ("edge", "Polkadot parachain nedir?"),
    ("edge", "NEAR protokolü sharding nasıl çalışır?"),
    ("edge", "Ethereum rollup türleri nelerdir?"),
    ("edge", "MakerDAO DAI nasıl oluşturuluyor?"),
    ("edge", "Lido stETH rebase mekanizması nedir?"),
    ("edge", "Jito tip router nedir?"),
    ("edge", "Realms üzerinde proposal nasıl oluşturulur?"),
]

assert len(QUERIES) == 150, f"Expected 150, got {len(QUERIES)}"

# ── RAG ───────────────────────────────────────────────────────────────────────
async def embed(oai, text, tok_log):
    resp = await oai.embeddings.create(model=EMBED_MODEL, input=text, dimensions=EMBED_DIM)
    tok_log["embed"] += resp.usage.total_tokens
    return resp.data[0].embedding

async def qdrant_search(qdrant, vec):
    for attempt in range(3):
        try:
            r = await qdrant.query_points(
                collection_name=COLLECTION, query=vec, using="dense",
                limit=TOP_K, with_payload=True, score_threshold=SCORE_THRESH, timeout=60,
            )
            return [{"payload": p.payload, "score": p.score} for p in r.points if p.payload]
        except Exception:
            if attempt == 2: return []
            await asyncio.sleep(2 ** attempt)
    return []

def diversity_budget(results):
    seen, selected, used = {}, [], 0
    for r in results:
        doc = r["payload"].get("doc_id", "")
        cnt = seen.get(doc, 0)
        if cnt >= 2: continue
        tok = r["payload"].get("token_count", len(r["payload"].get("content","").split()))
        if used + tok > TOKEN_BUDGET: continue
        seen[doc] = cnt + 1; used += tok; selected.append(r)
        if len(selected) >= TOP_N: break
    return selected

def format_ctx(chunks):
    if not chunks: return ""
    parts = ["[Knowledge Context]\nKullandığın her bilgiyi [doc_id:chunk_id] ile kaynak göster.\n"]
    for c in chunks:
        p = c["payload"]
        did, cid = p.get("doc_id","?"), p.get("chunk_id",0)
        src = p.get("source_id",""); section = p.get("section_path","")
        parts.append(
            f"---\n[{did}:{cid}]" + (f" ({src})" if src else "") + "\n" +
            (f"Section: {section}\n" if section else "") +
            p.get("content","").strip()
        )
    return "\n".join(parts)

async def generate_answer(oai, question, ctx_block, tok_log):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if ctx_block:
        messages[0]["content"] += f"\n\n{ctx_block}"
    messages.append({"role": "user", "content": question})

    for attempt in range(3):
        try:
            resp = await oai.chat.completions.create(
                model=ANSWER_MODEL, messages=messages,
                max_tokens=350, temperature=0.1,
            )
            tok_log["llm_in"]  += resp.usage.prompt_tokens
            tok_log["llm_out"] += resp.usage.completion_tokens
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt == 2: return f"[ERROR: {e}]"
            await asyncio.sleep(2 ** attempt)
    return "[ERROR]"

# ── Quality evaluation ────────────────────────────────────────────────────────
def evaluate(question, answer, chunks, top_score):
    has_citation  = bool(re.search(r'\[\w[\w_\-\.]+:\d+\]', answer))
    no_data       = any(p in answer.lower() for p in ["bilgim yok", "bilgiye sahip değil", "context'te", "mevcut değil", "bulamıyorum"])
    answer_len    = len(answer.split())
    has_content   = not chunks

    # Kalite skoru (0-10)
    score = 0
    if top_score >= 0.65:  score += 4
    elif top_score >= 0.45: score += 2
    elif top_score >= 0.3:  score += 1

    if has_citation:   score += 2
    if answer_len > 60: score += 2
    if not no_data:    score += 1
    if chunks:         score += 1

    if no_data and not chunks:
        grade = "❌ NO_DATA"
    elif no_data:
        grade = "⚠️  GROUNDED_MISS"   # context var ama cevap yok
    elif score >= 8:
        grade = "✅ EXCELLENT"
    elif score >= 6:
        grade = "🟢 GOOD"
    elif score >= 4:
        grade = "🟡 MODERATE"
    else:
        grade = "🔴 POOR"

    return grade, score, has_citation, answer_len, no_data

# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    oai    = AsyncOpenAI(api_key=OPENAI_KEY)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False, timeout=60)

    tok = {"embed": 0, "llm_in": 0, "llm_out": 0}
    results = []

    print("=" * 76)
    print(f"OPRAI RAG — 150 Sorgu Kalite Testi  |  Cevap: {ANSWER_MODEL}")
    print("=" * 76)

    cat_stats: dict[str, list] = {}

    for i, (cat, q) in enumerate(QUERIES, 1):
        t0 = time.perf_counter()
        vec    = await embed(oai, q, tok)
        raw    = await qdrant_search(qdrant, vec)
        chunks = diversity_budget(raw)
        ctx    = format_ctx(chunks)
        ans    = await generate_answer(oai, q, ctx, tok)
        ms     = int((time.perf_counter() - t0) * 1000)

        top_score = raw[0]["score"] if raw else 0.0
        grade, score, cited, wlen, miss = evaluate(q, ans, chunks, top_score)
        sources = list(dict.fromkeys(r["payload"].get("source_id","?") for r in chunks))

        results.append({
            "i": i, "cat": cat, "q": q, "grade": grade, "score": score,
            "top_score": top_score, "cited": cited, "wlen": wlen,
            "miss": miss, "sources": sources, "ms": ms, "ans": ans,
        })

        cat_stats.setdefault(cat, []).append(score)

        # One short line per query
        src_str = ", ".join(sources[:3]) + ("…" if len(sources) > 3 else "")
        print(f"[{i:3d}] {grade}  s={score}/10  top={top_score:.3f}  {wlen:3d}w  {'📎' if cited else '  '} [{cat}]")
        print(f"       Q: {q[:75]}")
        print(f"       Src: {src_str or 'NONE'} | {ms}ms")

        await asyncio.sleep(0.05)

    await qdrant.close()

    # ── Worked examples ───────────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("SAMPLE ANSWERS — one good and one bad per category")
    print("=" * 76)
    shown_cats = set()
    for r in sorted(results, key=lambda x: -x["score"]):
        if r["cat"] not in shown_cats and r["score"] >= 7:
            shown_cats.add(r["cat"])
            print(f"\n✅ [{r['cat']}] {r['q']}")
            print(f"   {r['ans'][:400]}")
            if len(shown_cats) >= 5: break

    poor = [r for r in results if "NO_DATA" in r["grade"] or "POOR" in r["grade"]]
    if poor:
        print(f"\n❌ FAILED QUERIES ({len(poor)}):")
        for r in poor[:10]:
            print(f"  [{r['cat']}] {r['q'][:70]}")
            print(f"   → {r['ans'][:150]}")

    # ── Summary stats ─────────────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("SUMMARY — QUALITY DISTRIBUTION")
    print("=" * 76)

    grade_counts: dict[str, int] = {}
    for r in results:
        g = r["grade"].split()[1] if " " in r["grade"] else r["grade"]
        grade_counts[g] = grade_counts.get(g, 0) + 1

    total = len(results)
    for g, cnt in sorted(grade_counts.items(), key=lambda x: -x[1]):
        bar = "█" * (cnt * 30 // total)
        print(f"  {g:15s} {cnt:3d}/150  {bar}")

    avg_score = sum(r["score"] for r in results) / total
    avg_top   = sum(r["top_score"] for r in results) / total
    cited_pct = sum(1 for r in results if r["cited"]) / total * 100
    no_data_n = sum(1 for r in results if r["miss"])
    avg_ms    = sum(r["ms"] for r in results) // total

    print(f"\n  Mean quality score    : {avg_score:.1f}/10")
    print(f"  Mean retrieval        : {avg_top:.3f}")
    print(f"  Citation rate         : {cited_pct:.0f}%")
    print(f"  'No knowledge' answers: {no_data_n}/150")
    print(f"  Mean latency          : {avg_ms}ms")

    print(f"\n  BY CATEGORY:")
    for cat, scores in sorted(cat_stats.items()):
        avg = sum(scores) / len(scores)
        bar = "█" * int(avg)
        print(f"  {cat:15s} avg={avg:.1f}  {bar}")

    # ── Cost analysis ─────────────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("TOKEN & COST ANALYSIS — FULL BREAKDOWN")
    print("=" * 76)

    # 1. Crawl one-time costs
    crawl_embed_cost  = CRAWL_EMBED_TOKENS / 1e6 * EMBED_PRICE
    haiku_in_tok      = CRAWL_PAGES_CLASSIFIED * 1100   # ~1100 tok/sayfa input
    haiku_out_tok     = CRAWL_PAGES_CLASSIFIED * 85     # ~85 tok/sayfa output
    haiku_cost        = haiku_in_tok / 1e6 * HAIKU_IN_PRICE + haiku_out_tok / 1e6 * HAIKU_OUT_PRICE
    crawl_total       = crawl_embed_cost + haiku_cost

    print("\n  📦 ONE-TIME INGESTION COST (68 sources, ~32 min)")
    print(f"  Embedding  : {CRAWL_EMBED_TOKENS:>10,} tok  →  ${crawl_embed_cost:.2f}")
    print(f"  Haiku in   : {haiku_in_tok:>10,} tok  →  ${haiku_in_tok/1e6*HAIKU_IN_PRICE:.2f}")
    print(f"  Haiku out  : {haiku_out_tok:>10,} tok  →  ${haiku_out_tok/1e6*HAIKU_OUT_PRICE:.2f}")
    print(f"  {'─'*45}")
    print(f"  TOTAL      :                    ${crawl_total:.2f}  ({'Haiku: {:.0f}%'.format(haiku_cost/crawl_total*100)})")

    # 2. Per-query test costs
    test_embed_cost = tok["embed"] / 1e6 * EMBED_PRICE
    test_llm_in     = tok["llm_in"] / 1e6 * MINI_IN_PRICE
    test_llm_out    = tok["llm_out"] / 1e6 * MINI_OUT_PRICE
    test_total      = test_embed_cost + test_llm_in + test_llm_out

    print(f"\n  🧪 THIS RUN (150 queries, {ANSWER_MODEL})")
    print(f"  Embed      : {tok['embed']:>10,} tok  →  ${test_embed_cost:.4f}")
    print(f"  LLM input  : {tok['llm_in']:>10,} tok  →  ${test_llm_in:.4f}")
    print(f"  LLM output : {tok['llm_out']:>10,} tok  →  ${test_llm_out:.4f}")
    print(f"  {'─'*45}")
    print(f"  TOTAL      :                    ${test_total:.4f}")
    print(f"  Per query  :                    ${test_total/150:.5f}")

    # 3. Production per-turn costs
    avg_embed_per_q = tok["embed"] / 150
    avg_ctx_tok     = tok["llm_in"] / 150 - 400   # base system prompt ~400 tok

    for model_name, in_p, out_p in [
        ("gpt-4o-mini (current)", MINI_IN_PRICE, MINI_OUT_PRICE),
        ("gpt-4o (prod)", GPT4O_IN_PRICE, GPT4O_OUT_PRICE),
    ]:
        base_sys  = 1800   # OPRAI system prompt
        llm_in    = base_sys + avg_ctx_tok + 30   # +30 user msg
        llm_out   = 300
        cost_turn = (avg_embed_per_q / 1e6 * EMBED_PRICE +
                     llm_in / 1e6 * in_p +
                     llm_out / 1e6 * out_p)

        print(f"\n  🚀 PRODUCTION — {model_name}")
        print(f"  Embed/query    : {avg_embed_per_q:.0f} tok")
        print(f"  RAG context    : ~{avg_ctx_tok:.0f} tok")
        print(f"  LLM input/turn : ~{llm_in:.0f} tok")
        print(f"  LLM output     : ~{llm_out} tok")
        print(f"  Cost/turn      : ${cost_turn:.4f}")
        print(f"  {'─'*40}")
        for dau in [100, 500, 1000, 5000, 10000]:
            monthly = dau * 30 * cost_turn
            print(f"  {dau:6d} advice turns/day  →  ${monthly:.2f}/mo")

    # 4. Haiku versus the alternatives
    print(f"\n  ⚡ HAIKU COST ANALYSIS (during ingestion)")
    print(f"  7363 pages × 1100 input tok = {haiku_in_tok/1e6:.1f}M tok × $0.80 = ${haiku_in_tok/1e6*HAIKU_IN_PRICE:.2f}")
    print(f"  7363 pages × 85 output tok  = {haiku_out_tok/1e6:.2f}M tok × $4.00 = ${haiku_out_tok/1e6*HAIKU_OUT_PRICE:.2f}")
    print(f"  Haiku total: ${haiku_cost:.2f}  ({haiku_cost/crawl_total*100:.0f}% of ingestion)")
    print(f"")
    print(f"  ALTERNATIVE — filter on URL patterns alone (no Haiku):")
    print(f"    Saved: ${haiku_cost:.2f} one-time")
    print(f"    Lost:  36% junk pages (API refs, changelogs) enter the collection")
    print(f"    Tradeoff: ${haiku_cost:.2f} once vs. permanently worse retrieval")
    print(f"")
    print(f"  ALTERNATIVE — classify with gpt-4o-mini:")
    mini_haiku_in  = haiku_in_tok / 1e6 * MINI_IN_PRICE
    mini_haiku_out = haiku_out_tok / 1e6 * MINI_OUT_PRICE
    print(f"    gpt-4o-mini cost: ${mini_haiku_in + mini_haiku_out:.2f}  ({(mini_haiku_in+mini_haiku_out)/haiku_cost:.1f}x Haiku)")
    print(f"    → Haiku is cheaper and faster; the right call ✅")

asyncio.run(main())
