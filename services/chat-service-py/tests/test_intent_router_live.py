"""Live accuracy checks for the intent classifier — needs an OpenAI key.

Skipped without `OPRAI_OPENAI_API_KEY`, so a normal `pytest` run ignores it.
No database and no chat-service: the router talks to the model directly.

Why this file exists
--------------------
Seven fields on `IntentResult` — token_category, wants_venues, is_chitchat,
wants_price, wants_balance, compare_tokens, wants_analysis — replaced keyword
tables that each covered English plus one or two other languages. The bet in
making that swap is that a multilingual model reads a SHORT question in a
language nobody enumerated at least as well as a word list read the two it knew.
The old code doubted exactly this: its comment said the classifier "sometimes
returns false on short Turkish / Spanish phrasings the keyword matcher catches
reliably", and kept the matcher as a backstop.

These cases are that doubt, written down. They are deliberately short, because
short is where a classifier has least to work with, and deliberately spread
across scripts, because the point is the fields work where no list ever reached.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.services.intent_router import IntentRouter

pytestmark = pytest.mark.skipif(
    not os.getenv("OPRAI_OPENAI_API_KEY"),
    reason="live classifier test — needs OPRAI_OPENAI_API_KEY",
)

# (message, language tag, field, expected)
CASES: list[tuple[str, str, str, object]] = [
    # Token category — the documented weak spot.
    ("hangi stablecoinler var",          "tr", "token_category", "stable"),
    ("stablecoin'ler neler",             "tr", "token_category", "stable"),
    ("likit stake tokenları listele",    "tr", "token_category", "lst"),
    ("memecoinleri göster",              "tr", "token_category", "memecoin"),
    ("qué stablecoins hay",              "es", "token_category", "stable"),
    ("welche stablecoins gibt es",       "de", "token_category", "stable"),
    ("какие есть стейблкоины",           "ru", "token_category", "stable"),
    ("有哪些稳定币",                      "zh", "token_category", "stable"),
    ("which stablecoins exist",          "en", "token_category", "stable"),
    # A class of POOLS is not a class of TOKENS — these keep their tools.
    ("stablecoin havuzlarını listele",   "tr", "wants_venues", True),
    ("listar los pools de stablecoins",  "es", "wants_venues", True),
    ("list the stablecoin pools",        "en", "wants_venues", True),
    # Chitchat drives prompt size; a false positive strips a real question's context.
    ("merhaba",                          "tr", "is_chitchat", True),
    ("teşekkürler",                      "tr", "is_chitchat", True),
    ("hola",                             "es", "is_chitchat", True),
    ("спасибо",                          "ru", "is_chitchat", True),
    ("こんにちは",                        "ja", "is_chitchat", True),
    ("SOL fiyatı ne",                    "tr", "is_chitchat", False),
    # "What does it cost" vs "how much do I hold" — different fields.
    ("SOL fiyatı ne kadar",              "tr", "wants_price", True),
    ("cuánto vale SOL",                  "es", "wants_price", True),
    ("ne kadar SOL'üm var",              "tr", "wants_balance", True),
    ("cuánto SOL tengo",                 "es", "wants_balance", True),
    ("wie viel SOL habe ich",            "de", "wants_balance", True),
    # Comparison returns the symbols, verbatim as written.
    ("USDS ile USDC'yi karşılaştır",     "tr", "compare_tokens", ("USDS", "USDC")),
    ("compara mSOL y jitoSOL",           "es", "compare_tokens", ("MSOL", "JITOSOL")),
    # Analysis routes to a stronger responder, so a lookup must not trip it.
    ("portföyümü detaylı analiz et",     "tr", "wants_analysis", True),
    ("analiza mi cartera en detalle",    "es", "wants_analysis", True),
    ("SOL fiyatı",                       "tr", "wants_analysis", False),
]


@pytest.fixture(scope="module")
def verdicts() -> dict[str, object]:
    """Classify every distinct message once, concurrently."""
    messages = list(dict.fromkeys(c[0] for c in CASES))
    router = IntentRouter()

    async def _run():
        results = await asyncio.gather(
            *[router.classify(m, "", wallet="pytest-live") for m in messages]
        )
        return dict(zip(messages, results))

    return asyncio.run(_run())


@pytest.mark.parametrize(
    "message,lang,field,expected",
    CASES,
    ids=[f"{c[1]}-{c[2]}-{c[0][:22]}" for c in CASES],
)
def test_field(verdicts, message, lang, field, expected):
    got = getattr(verdicts[message], field)
    if field == "compare_tokens":
        assert set(expected).issubset(set(got)), f"{message!r} → {got!r}"
    else:
        assert got == expected, f"{message!r} → {field}={got!r}"
