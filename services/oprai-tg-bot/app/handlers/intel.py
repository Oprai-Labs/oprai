"""/token — the on-chain X-ray, without waiting on a model.

Asking the assistant to analyse an address works, but it costs two LLM round
trips on top of the data itself: about thirty seconds against six for the read
alone. Worse, it varies — the same question sometimes came back "I couldn't
fetch any data for this address" while the index was answering perfectly.

So a plain address gets the real thing directly: our own index, rendered here.
The assistant is still there for the open-ended questions that need it; this is
for the one people ask most, where the answer is a known shape.
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, upsert_tg_user
from app.handlers.privacy import private_answer
from app.logging_config import log
from app.services import tokens as tok
from app.services.signals_client import SignalsClient, SignalsError

router = Router(name="intel")

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
# The same thing anywhere in a sentence: people write "0x… analiz et", not a
# bare address on its own line.
ADDRESS_IN_TEXT = re.compile(r"0x[0-9a-fA-F]{40}")

# Words that mean "do something with it". Everything else that carries an
# address is a question about that address — nobody pastes a contract to make
# conversation, and listing the ways people ask ("launchpad", "hacim", "who is
# the dev") is a list that is always one phrasing short. It was: "hangi
# launchpad'de basıldı, hacmi ne?" went to the model, took twenty-four seconds
# and came back "no data", while the index held both answers.
# Words that make an address a wallet question, not a token look-up.
WALLET_WORDS = (
    "wallet", "cüzdan", "cuzdan", "p&l", "pnl", "win rate", "portfolio", "portföy", "portfoy",
    "balance", "bakiye", "holds", "holding", "hold ", "where is", "parked", "positions", "pozisyon",
    "smart money?", "is it a bot", "bot mu", "kazan", "how much money", "ne kadar para", "net worth",
)
ACTION_WORDS = (
    "send", "gönder", "gonder", "transfer", "yolla",
    "swap", "buy", "sell", "satın", "satin", "sat ", "al ",
    "borrow", "lend", "ödünç", "odunc",
    "long", "short", "approve", "onayla", "bridge", "köprü", "kopru",
)

def wants_a_look(text: str) -> str | None:
    """The address someone is asking about — which is most of the time.

    An address plus anything that isn't an instruction is a question about it.
    Deciding the other way round, by listing the words that count as asking,
    means every phrasing nobody thought of takes the slow path and often comes
    back with nothing.

    "send 5 USDG to 0x…" is the exception that matters: hijacking it would
    answer a question nobody asked instead of moving the money.
    """
    found = ADDRESS_IN_TEXT.search(text or "")
    if not found:
        return None
    rest = (text or "").replace(found.group(0), " ").strip().lower()
    if any(w in rest for w in ACTION_WORDS):
        return None
    if any(w in rest for w in WALLET_WORDS):
        return None          # a question about a WALLET — the assistant's wallet tools answer it
    return found.group(0)
EXPLORER = "https://robinscan.io/token/"

# Which risk band gets which mark. The number alone doesn't say whether 23 is
# good; the word does.
BANDS = ((30, "🟢", "low"), (60, "🟡", "medium"), (101, "🔴", "high"))


def _band(score: float) -> tuple[str, str]:
    for ceiling, mark, word in BANDS:
        if score < ceiling:
            return mark, word
    return "🔴", "high"


def _kpi(report: dict, *labels: str) -> str | None:
    """KPIs arrive as a list of {label, value, fmt}; pull one out by name."""
    for row in report.get("kpis") or []:
        if str(row.get("label", "")).lower() in [x.lower() for x in labels]:
            value = row.get("value")
            return f"{value}%" if row.get("fmt") == "%" else str(value)
    return None


def render(report: dict, symbol: str | None, address: str,
           market: dict | None = None) -> str:
    facts = report.get("facts") or {}
    score = facts.get("risk_score")
    lines = []

    # A score built without price history, volume or flow is not a low risk;
    # it is a blind spot. This token had fallen from tens of millions and the
    # card still said "6/100 — low", because the index had seen none of its
    # trades: no venue, no volume, no ATH, no smart-money flow. Say what the
    # score is made of, and stop calling it low when it is made of nothing.
    blind = _index_saw_no_trading(facts)
    if score is not None and not blind:
        mark, word = _band(float(score))
        lines.append(f"{mark} <b>Risk {score}/100</b> — {word}")
    elif score is not None:
        swaps = facts.get("swaps_24h") or 0
        why = ("has no trade history for this token"
               if not swaps else f"has only seen {swaps} trades in a day")
        lines.append(
            f"⚠️ <b>Risk unscored</b> — our index {why}, so holders and supply "
            f"are all it could weigh."
        )
    name = symbol or facts.get("symbol") or "Token"
    lines.insert(0, f"🔬 <b>{name}</b> · Robinhood Chain")
    lines.append("")

    market = market or {}
    price = _usd(market.get("price")) or _kpi(report, "Price")
    if price:
        try:
            age = float(facts.get("age_days")) if facts.get("age_days") is not None else None
        except (TypeError, ValueError):
            age = None
        lines.append(f"• Price: <b>{price}</b>{_move(market, age)}")
    for label, key in (("Market cap", "mcap"), ("Liquidity", "liquidity"),
                       ("24h volume", "volume")):
        value = _usd(market.get(key)) or _kpi(report, label)
        if value:
            lines.append(f"• {label}: <b>{value}</b>")

    # How far it is off its high, when the index knows. A token 90% down from
    # its peak is the single most important thing on this card.
    fall = facts.get("drawdown_from_ath")
    ath = facts.get("ath_mcap_usd")
    # An impossible high is not a high. A negative market cap means the price
    # behind it is wrong, and every figure derived from it with it.
    try:
        if ath is not None and float(ath) <= 0:
            fall = ath = None
    except (TypeError, ValueError):
        fall = ath = None
    if fall is not None:
        peak = f" (peak {_usd(ath)})" if ath else ""
        lines.append(f"• <b>Down {float(fall):.0f}% from its high</b>{peak}")

    buys, sells = market.get("buys_24h"), market.get("sells_24h")
    if buys is not None and sells is not None and (buys or sells):
        lines.append(f"• Trades 24h: {buys:,} buys · {sells:,} sells")

    holders = _kpi(report, "Holders")
    age = _kpi(report, "Age (days)")
    if holders:
        lines.append(f"• Holders: <b>{holders}</b>" + (f" · {age} days old" if age else ""))

    top10 = _kpi(report, "Top-10 wallet concentration")
    lp = _kpi(report, "In LP pools")
    burned = _kpi(report, "Burned")
    if top10 or lp:
        bits = []
        if top10:
            bits.append(f"top-10 {top10}")
        if lp:
            bits.append(f"LP {lp}")
        if burned:
            bits.append(f"burned {burned}")
        lines.append("• Supply: " + " · ".join(bits))

    whales = _kpi(report, "Whales (>1%)")
    if whales:
        lines.append(f"• Whales over 1%: <b>{whales}</b>")

    launchpad = facts.get("launchpad")
    if launchpad:
        lines.append(f"• Launched via <b>{launchpad}</b>")

    # Who is holding is half the question; the other half is what they have
    # been doing. Reporting only the holder count let a token where smart
    # money was selling read exactly like one where it was buying.
    smart = facts.get("smart_money_holders")
    if smart:
        line = f"• Smart money: <b>{smart}</b> wallets"
        if facts.get("smart_money_holding_pct"):
            line += f", {facts['smart_money_holding_pct']}% of supply"
        lines.append(line)
        net = facts.get("smart_money_net_usd")
        sellers = facts.get("smart_money_sellers")
        if net:
            way = "net buying" if float(net) > 0 else "net SELLING"
            lines.append(f"  └ {way} {_usd(abs(float(net)))}"
                         + (f" · {sellers} selling" if sellers else ""))
        elif sellers:
            lines.append(f"  └ {sellers} of them selling")

    lines += ["", f"<code>{address}</code>"]
    return "\n".join(lines)


# Below this, a day's trading is too small a sample for anything derived from
# price to mean much. CASHCAT at 46,000 swaps prices correctly; OPRAI at 31
# comes back negative.
MIN_SWAPS_TO_SCORE = 250


def _index_saw_no_trading(facts: dict) -> bool:
    """Did our index see this token trade at all?

    When it has no venue and no swaps, every price-derived signal it produces
    is absent rather than reassuring — and the risk score is then built from
    holders and supply alone.
    """
    keys = ("venues", "swaps_24h", "volume_24h_usd")
    if not any(k in facts for k in keys):
        # An older report shape that never carried these. Absence of the
        # fields is not evidence of absence of trading, so leave the score be.
        return False
    if (not (facts.get("venues") or [])
            and not facts.get("swaps_24h")
            and not float(facts.get("volume_24h_usd") or 0)):
        return True
    # A handful of swaps is not a market. The index derives price — and from
    # it market cap, high and drawdown — off these, and on thin samples it
    # returns figures that are plainly impossible: our own token came back
    # with a market cap of minus eighteen thousand dollars. A score resting on
    # that is not worth presenting as one.
    try:
        thin = int(facts.get("swaps_24h") or 0) < MIN_SWAPS_TO_SCORE
    except (TypeError, ValueError):
        thin = False
    return thin


def _move(market: dict, age_days: float | None = None) -> str:
    """What the price has been doing, over windows that mean something.

    A day-old token's 24-hour change is measured from its first hours, so it
    reads as a spectacular gain no matter what has happened since: SLINK
    showed "+1656% (24h)" while everyone who bought that morning was down by
    half. The shorter windows are what tell you whether you are catching a
    falling knife, so they are shown first and the long one is labelled for
    what it actually spans.
    """
    def part(value, label):
        if value is None:
            return None
        return f"{'▲' if value >= 0 else '▼'}{abs(value):.1f}% {label}"

    bits = [b for b in (part(market.get("change_1h"), "1h"),
                        part(market.get("change_6h"), "6h"),
                        part(market.get("change_24h"), "24h")) if b]
    if not bits:
        return ""
    line = "  " + " · ".join(bits)
    # Younger than the window it is being measured over.
    if age_days is not None and age_days <= 1 and market.get("change_24h") is not None:
        line += "\n  <i>(24h reaches back to launch)</i>"
    return line


def _keyboard(address: str, symbol: str | None) -> InlineKeyboardMarkup:
    buy = f"/swap 0.01 ETH {symbol}" if symbol else None
    rows = [[InlineKeyboardButton(text="🔎 On the explorer",
                                  url=f"{EXPLORER}{address}")]]
    if buy:
        rows.insert(0, [InlineKeyboardButton(
            text=f"💱 Buy {symbol}", callback_data=f"swap:from:ETH")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _market(address: str) -> dict:
    """Price, market cap, liquidity and 24h volume.

    Our index carries none of these — it counts holders and traces wallets.
    An analysis without a price is half an answer, so the pair is fetched
    together and a failure costs only those four lines.
    """
    import httpx

    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url)
            pairs = (r.json() or {}).get("pairs") or []
    except Exception as e:  # noqa: BLE001 — the X-ray stands without it
        log.info("market_data_unavailable", token=address, error=str(e)[:120])
        return {}

    # The deepest pool is the honest price: a thin pair can quote anything.
    on_chain = [p for p in pairs if str(p.get("chainId", "")).lower() in
                ("robinhood", "4663")] or pairs
    if not on_chain:
        return {}
    best = max(on_chain, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    change = best.get("priceChange") or {}
    return {
        "price": best.get("priceUsd"),
        "mcap": best.get("marketCap") or best.get("fdv"),
        "liquidity": (best.get("liquidity") or {}).get("usd"),
        "volume": (best.get("volume") or {}).get("h24"),
        # What the token has actually been doing. A report that lists a market
        # cap and calls the token low-risk, while the price is down two thirds
        # on the day, is describing a different token than the one being asked
        # about.
        "change_1h": _num(change.get("h1")),
        "change_6h": _num(change.get("h6")),
        "change_24h": _num(change.get("h24")),
        "buys_24h": ((best.get("txns") or {}).get("h24") or {}).get("buys"),
        "sells_24h": ((best.get("txns") or {}).get("h24") or {}).get("sells"),
    }


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usd(value) -> str | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n >= 1_000_000:
        return f"${n / 1_000_000:,.2f}M"
    if n >= 1_000:
        return f"${n / 1_000:,.1f}K"
    return f"${n:,.4f}".rstrip("0").rstrip(".")


async def analyse(message: Message, query: str) -> bool:
    """Look a token up and show the X-ray. Returns whether we could."""
    query = query.strip()
    address, symbol = None, None

    if ADDRESS.match(query):
        address = query
        found = await tok.resolve(query)
        symbol = found[0]["symbol"] if found else None
    else:
        found = await tok.resolve(query)
        if found:
            address, symbol = found[0]["address"], found[0]["symbol"]

    if not address:
        return False

    note = await private_answer(message, f"🔬 Reading the chain for <b>{symbol or query}</b>…")
    import asyncio

    try:
        report, market = await asyncio.gather(
            SignalsClient().token_report(address), _market(address)
        )
    except SignalsError as e:
        log.warning("token_report_failed", token=address, error=str(e)[:160])
        if note:
            await note.edit_text(
                "⚠️ The on-chain index isn't answering right now — try again shortly."
            )
        return True

    await audit(message.from_user.id, "token_analysis", {"token": address})
    if report.get("status") and report["status"] != "ok":
        # Not a token we know. It may well be a WALLET — hand the message to the
        # assistant (its wallet tools answer P&L / holdings) instead of declaring
        # the address dead.
        if note:
            try:
                await note.delete()
            except Exception:  # noqa: BLE001
                pass
        return False

    text = render(report, symbol, address, market)
    if note:
        await note.edit_text(text, reply_markup=_keyboard(address, symbol),
                             disable_web_page_preview=True)
    return True


@router.message(Command("token", "analyze", "analyse"))
async def token_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "🔬 <b>Analyse a token</b>\n\n"
            "<code>/token NVDA</code> · <code>/token 0xe8ff…</code>\n\n"
            "<i>Holders, concentration, liquidity, whales, launchpad and a risk "
            "score — straight from our index.</i>"
        )
        return
    if not await analyse(message, query):
        await message.answer(
            f"I don't know a token called <b>{query}</b> on Robinhood Chain."
        )


@router.message(F.text)
async def address_in_message(message: Message) -> None:
    """An address someone is asking about, wherever it sits in the sentence.

    Routing this through the model cost thirty seconds to reach data we read
    in five, and it sometimes gave up and said there was none. Anything that
    isn't a request to look is handed straight back to the assistant.
    """
    from aiogram.dispatcher.event.bases import SkipHandler

    address = wants_a_look(message.text or "")
    if not address:
        raise SkipHandler
    await upsert_tg_user(message.from_user.id, message.from_user.username)
    if not await analyse(message, address):
        raise SkipHandler
