"""Asking OPRAI questions, and the credits that meter it.

The unit tests cover the two things that silently corrupt a chat bot: text the
model marks as reasoning leaking into the answer, and Markdown reaching
Telegram's HTML parser. The credit tests cover the accounting, where a race
means giving away model calls and a carry-over means giving them away for ever.
"""

from __future__ import annotations

import asyncio
import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import chat as chat_svc
from app.services import credits


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.GATEWAY_URL.rstrip('/')}/health") and _reachable(
    "http://127.0.0.1:3020/health"
)


# ── unit: reasoning must never reach the user ───────────────────────────────
def test_reasoning_is_removed_from_the_answer():
    a = chat_svc.Answer(raw="<think>weighing options</think>NVDA looks strong.")
    assert a.text == "NVDA looks strong."


def test_a_half_streamed_reasoning_block_is_not_shown():
    """The closing tag arrives later. Until it does, the opening fragment must
    not be rendered — a progress edit would otherwise flash the model's
    working in front of the user."""
    a = chat_svc.Answer()
    for chunk in ("Here: ", "<thi", "nk>weigh", "ing</think>", "buy."):
        a.raw += chunk
        assert "think" not in a.text and "weigh" not in a.text
    assert a.text == "Here: buy."


def test_progress_only_fires_when_visible_text_grows():
    """A delta made entirely of reasoning must not trigger a redraw that shows
    nothing new."""
    a = chat_svc.Answer()
    assert chat_svc._consume({"delta": "<think>hmm"}, a) is False
    assert chat_svc._consume({"delta": "</think>Yes"}, a) is True


# ── unit: Markdown -> Telegram HTML ─────────────────────────────────────────
def test_markdown_becomes_html_and_stray_angle_brackets_are_escaped():
    out = chat_svc.to_telegram_html("**NVDA** <risky> at 5x")
    assert "<b>NVDA</b>" in out and "&lt;risky&gt;" in out


def test_code_contents_are_not_re_parsed_as_markdown():
    """Escaping code after converting emphasis would turn an address's
    underscores into italics and corrupt it."""
    out = chat_svc.to_telegram_html("`a_b_c <x>` and **bold**")
    assert "<code>a_b_c &lt;x&gt;</code>" in out and "<b>bold</b>" in out


def test_long_answers_split_on_a_boundary_not_mid_tag():
    text = "\n\n".join(["paragraph " + "x" * 300 for _ in range(30)])
    parts = chat_svc.split_for_telegram(chat_svc.to_telegram_html(text))
    assert len(parts) > 1
    assert all(len(p) <= chat_svc.TELEGRAM_LIMIT for p in parts)
    # A split inside a tag would leave an unbalanced '<' in a part.
    assert all(p.count("<") == p.count(">") for p in parts)


def test_an_action_is_answered_with_the_command_that_runs_it():
    """The assistant explains; the commands execute. Two execution paths for
    one intent is two ways for them to disagree."""
    from app.handlers.chat import _action_hint

    assert "/swap" in _action_hint({"type": "swap"})
    assert "/long" in _action_hint({"type": "perp_open"})
    # An action with no command still gets a useful sentence, not a blank.
    assert _action_hint({"type": "something_new"}).strip()


# ── credits ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_credits_cannot_be_overspent_by_simultaneous_messages():
    """Check-then-charge as two statements would let a burst of messages all
    pass against the same last credit."""
    await init_pool()
    scope = -random.randint(10**9, 10**10)
    try:
        allowance = credits.free_allowance(True)
        results = await asyncio.gather(
            *[credits.spend(scope, True, 1) for _ in range(allowance + 15)]
        )
        assert sum(1 for r in results if r is not None) == allowance
        assert (await credits.balance(scope, True)).exhausted
    finally:
        await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = $1", scope)
        await pool().execute("DELETE FROM tg_credits WHERE scope_id = $1", scope)
        await close_pool()


@pytest.mark.asyncio
async def test_free_credits_reset_each_window_while_paid_ones_survive():
    """Free credits are a ration, not property: carrying them over would let an
    idle group accumulate free model calls indefinitely."""
    await init_pool()
    scope = -random.randint(10**9, 10**10)
    try:
        allowance = credits.free_allowance(True)
        await credits.grant(scope, True, 10)
        for _ in range(allowance):
            await credits.spend(scope, True, 1)
        assert (await credits.balance(scope, True)).free_left == 0

        await pool().execute(
            "UPDATE tg_credits SET window_start = now() - interval '30 days' "
            "WHERE scope_id = $1", scope,
        )
        rolled = await credits.balance(scope, True)
        assert rolled.free_left == allowance
        assert rolled.paid == 10

        # A second idle window must not stack another allowance on top.
        await pool().execute(
            "UPDATE tg_credits SET window_start = now() - interval '30 days' "
            "WHERE scope_id = $1", scope,
        )
        assert (await credits.balance(scope, True)).remaining == rolled.remaining
    finally:
        await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = $1", scope)
        await pool().execute("DELETE FROM tg_credits WHERE scope_id = $1", scope)
        await close_pool()


@pytest.mark.asyncio
async def test_the_ration_is_spent_before_purchased_credits():
    await init_pool()
    scope = random.randint(10**9, 10**10)
    try:
        await credits.grant(scope, False, 5)
        await credits.spend(scope, False, 1)
        bal = await credits.balance(scope, False)
        assert bal.paid == 5, "a purchased credit was spent while free ones remained"
    finally:
        await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = $1", scope)
        await pool().execute("DELETE FROM tg_credits WHERE scope_id = $1", scope)
        await close_pool()


@pytest.mark.asyncio
async def test_a_failed_answer_is_refunded():
    """Charging for an answer that never arrived is the fastest way to lose
    trust in the meter."""
    await init_pool()
    scope = random.randint(10**9, 10**10)
    try:
        before = (await credits.balance(scope, False)).remaining
        await credits.spend(scope, False, 1)
        await credits.refund(scope, 1, reason="ChatError")
        assert (await credits.balance(scope, False)).remaining == before
    finally:
        await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = $1", scope)
        await pool().execute("DELETE FROM tg_credits WHERE scope_id = $1", scope)
        await close_pool()


# ── live ────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not LIVE, reason="gateway/chat-service not running")
@pytest.mark.asyncio
async def test_a_question_gets_a_real_answer_and_a_thread():
    await init_pool()
    tg = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg, "chat_live")
    try:
        jwt = await auth_svc.get_jwt(tg)
        ticks: list[int] = []

        async def progress(partial: str) -> None:
            ticks.append(len(partial))

        answer = await chat_svc.stream(
            jwt, chat_svc.new_local_session(),
            "Which venue do tokenized stocks trade on?", on_progress=progress,
        )
        # Not a length assertion: how long an answer runs is the model's
        # choice and varies run to run. What must hold is that something
        # readable came back and none of it is the model's own reasoning.
        assert answer.text.strip()
        assert "<think>" not in answer.text and "</think>" not in answer.text
        # A local placeholder must come back as a real session, or every
        # follow-up question would start from nothing.
        assert answer.session_id and not answer.session_id.startswith("local:")
        assert ticks, "nothing streamed — the answer arrived as one block"

        # The thread is remembered, so "and what about TSLA?" has context.
        await chat_svc.remember_session(tg, tg, answer.session_id)
        assert await chat_svc.session_for(tg, tg) == answer.session_id
    finally:
        await pool().execute("DELETE FROM tg_chat_sessions WHERE scope_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


# ── instructions, not just questions ────────────────────────────────────────
def test_an_action_only_answer_is_not_a_failure():
    """"buy 0.01 NVDA" is an instruction. The model answers it by emitting an
    action and no prose — which used to be read as "no answer" and thrown
    away, with the person told to rephrase the thing that had worked."""
    answer = chat_svc.Answer(raw="")
    answer.actions.append({"type": "lighter_open", "params": {"symbol": "NVDA"}})
    assert not answer.text.strip()
    # stream() only raises when there is neither text nor an action.
    import inspect

    source = inspect.getsource(chat_svc.stream)
    assert "not answer.actions" in source, (
        "an action-only answer is still treated as a failure"
    )


@pytest.mark.parametrize(
    "action, expected",
    [
        ({"type": "lighter_open",
          "params": {"symbol": "NVDA", "side": "long", "collateralUsd": "0.01"}},
         ("long", "NVDA 0.01")),
        ({"type": "lighter_open",
          "params": {"symbol": "TSLA", "side": "short", "collateralUsd": "50",
                     "leverage": "5"}},
         ("short", "TSLA 50 5")),
        ({"type": "swap",
          "params": {"amount": "0.05", "fromToken": "ETH", "toToken": "NVDA"}},
         ("swap", "0.05 ETH NVDA")),
        # Sushi names them tokenIn/tokenOut; missing that meant a swap the
        # model had understood was silently dropped.
        ({"type": "sushi_swap",
          "params": {"tokenIn": "ETH", "tokenOut": "USDG", "amount": "0.01"}},
         ("swap", "0.01 ETH USDG")),
        ({"type": "morpho_supply", "params": {"amount": "10", "loanSymbol": "USDG"}},
         ("lend", "10")),
        ({"type": "transfer",
          "params": {"amount": "5", "token": "USDG", "to": "0xabc"}},
         ("send", "5 USDG 0xabc")),
    ],
)
def test_what_the_model_decided_becomes_a_command_we_can_run(action, expected):
    from app.handlers.chat import _args_for

    assert _args_for(action) == expected


def test_an_action_we_cannot_run_faithfully_is_declined():
    """Half a swap is worse than none: running with a guessed token spends
    someone's money on something they didn't ask for."""
    from app.handlers.chat import _args_for

    assert _args_for({"type": "swap", "params": {"amount": "1"}}) is None
    assert _args_for({"type": "transfer", "params": {"amount": "1"}}) is None
    assert _args_for({"type": "something_new", "params": {}}) is None


def test_every_command_the_mapper_emits_has_a_handler():
    from app.handlers.chat import _HANDLERS

    for command, (module_name, func_name) in _HANDLERS.items():
        module = __import__(f"app.handlers.{module_name}", fromlist=[func_name])
        assert hasattr(module, func_name), f"/{command} -> {module_name}.{func_name}"
