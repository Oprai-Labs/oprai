"""/nft — NFTs on Robinhood Chain, through OpenSea.

    /nft                  what is trading right now
    /nft rh machines      a collection, its floor, and what can be bought
    /mynfts               what this wallet holds
    /sell <#id> <price>   list one for sale

Most listings on this chain use Seaport order types the in-app buyer cannot
encode yet, so buyability is checked BEFORE a button is offered: a Buy button
that fails on tap is worse than no button, and the ones that can only be bought
on OpenSea are shown as such rather than hidden.
"""

from __future__ import annotations

import asyncio
import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import evm, opensea
from app.services import portfolio as pf
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

router = Router(name="nft")

_pending: dict[str, dict] = {}

EXPLORER = "https://robinscan.io/tx/"
OPENSEA_URL = "https://opensea.io/collection/"
# How many listings to price-check so a few buyable ones can be offered.
SCAN_LISTINGS = 12
SHOW_LISTINGS = 4


def _fmt(x: float) -> str:
    return f"{x:,.6f}".rstrip("0").rstrip(".") or "0"


def _price(row: dict) -> str:
    amount, currency = opensea.price_of(row)
    return f"{_fmt(amount)} {currency}"


@router.message(Command("nft", "nfts"))
async def nft_cmd(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    query = (command.args or "").strip()

    try:
        jwt = await auth_svc.get_jwt(user.id)
        if not query:
            await _trending(message, jwt)
            return
        await _collection(message, jwt, user.id, query)
    except (opensea.OpenSeaError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ Couldn't reach OpenSea: {e}")


async def _trending(message: Message, jwt: str) -> None:
    rows = await opensea.trending(jwt, limit=8)
    if not rows:
        await message.answer("No collections are trading on Robinhood Chain right now.")
        return
    lines = ["<b>NFTs</b> · trading now on Robinhood Chain", ""]
    for c in rows:
        floor = c.get("floorPrice")
        floor_text = f"{_fmt(float(floor))} {c.get('floorSymbol') or 'ETH'}" if floor else "—"
        lines.append(
            f"• <b>{c.get('name')}</b> — floor {floor_text} · "
            f"{int(float(c.get('volume') or 0)):,} volume"
        )
    lines += ["", "<code>/nft &lt;name&gt;</code> to look at one · <code>/mynfts</code> for yours"]
    await message.answer("\n".join(lines))


async def _collection(message: Message, jwt: str, telegram_id: int, query: str) -> None:
    found = await opensea.resolve_collection(jwt, query)
    if not found:
        await message.answer(
            f"I couldn't find a collection called <b>{query}</b> on Robinhood Chain.\n\n"
            "<code>/nft</code> on its own lists what's trading."
        )
        return

    slug = found["slug"]
    note = await message.answer(f"Looking at <b>{found.get('name')}</b>…")
    addr = await wallet_svc.wallet_address(telegram_id)
    rows = await opensea.listings(jwt, slug, limit=SCAN_LISTINGS)

    # Check buyability before offering a button. Most listings here use order
    # types the in-app buyer can't encode, and a button that fails on tap
    # teaches people the bot is broken.
    checked = await asyncio.gather(
        *[opensea.build_buy(jwt, addr, r["orderHash"], r.get("protocolAddress"))
          for r in rows],
        return_exceptions=True,
    )
    buyable = [
        (row, built) for row, built in zip(rows, checked)
        if not isinstance(built, Exception)
    ]

    floor = found.get("floorPrice")
    lines = [
        f"<b>{found.get('name')}</b>",
        f"Floor: {_fmt(float(floor))} {found.get('floorSymbol') or 'ETH'}" if floor else "",
        f"Owners: {int(found.get('numOwners') or 0):,}" if found.get("numOwners") else "",
        "",
    ]

    keyboard: list[list[InlineKeyboardButton]] = []
    if buyable:
        lines.append("<b>Available to buy here</b>")
        for i, (row, built) in enumerate(buyable[:SHOW_LISTINGS]):
            lines.append(f"• #{row.get('tokenId')} — {_price(row)}")
            pid = secrets.token_urlsafe(8)
            _pending[pid] = {"telegram_id": telegram_id, "kind": "buy",
                             "row": row, "built": built, "name": found.get("name")}
            keyboard.append([InlineKeyboardButton(
                text=f"Buy #{row.get('tokenId')} — {_price(row)}",
                callback_data=f"nft:ok:{pid}")])
    else:
        lines.append("<i>Nothing in this collection can be bought in-app yet.</i>")

    rest = len(rows) - len(buyable)
    if rest > 0:
        lines.append(
            f"\n<i>{rest} more listed from {_price(rows[0])} — those order types "
            "have to be bought on OpenSea.</i>"
        )
    keyboard.append([InlineKeyboardButton(
        text="Open on OpenSea", url=f"{OPENSEA_URL}{slug}")])

    await note.edit_text(
        "\n".join(x for x in lines if x != "" or True).strip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        disable_web_page_preview=True,
    )


# ── holdings ────────────────────────────────────────────────────────────────
@router.message(Command("mynfts"))
async def my_nfts(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    try:
        addr = await wallet_svc.wallet_address(user.id)
        jwt = await auth_svc.get_jwt(user.id)
        rows = await opensea.wallet_nfts(jwt, addr, limit=20)
    except (opensea.OpenSeaError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ Couldn't read your NFTs: {e}")
        return

    if not rows:
        await message.answer(
            "You don't hold any NFTs on Robinhood Chain yet.\n\n"
            "<code>/nft</code> shows what's trading."
        )
        return

    lines = [f"<b>Your NFTs</b> — {len(rows)} on Robinhood Chain", ""]
    for n in rows[:15]:
        lines.append(
            f"• <b>{n.get('name') or n.get('collection')}</b> #{n.get('identifier')}"
        )
    lines += ["", "Sell one with <code>/sell &lt;collection&gt; &lt;id&gt; &lt;price&gt;</code>"]
    await message.answer("\n".join(lines))


# ── sell ────────────────────────────────────────────────────────────────────
@router.message(Command("sell"))
async def sell_cmd(message: Message, command: CommandObject) -> None:
    """List an NFT for sale.

    The price is in the collection's own currency — Robinhood collections are
    often quoted in USDG, not ETH — so the currency is read from the collection
    and named back, rather than assumed.
    """
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    args = (command.args or "").split()
    if len(args) < 3:
        await message.answer(
            "Usage: <code>/sell &lt;collection&gt; &lt;token id&gt; &lt;price&gt;</code>\n"
            "Example: <code>/sell rhmachines 8468 0.07</code>\n\n"
            "<i><code>/mynfts</code> lists what you hold.</i>"
        )
        return

    *name_parts, token_id, price_text = args
    try:
        price = float(Decimal(price_text.lstrip("$")))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, ArithmeticError):
        await message.answer("That price doesn't look right.")
        return

    try:
        addr = await wallet_svc.wallet_address(user.id)
        jwt = await auth_svc.get_jwt(user.id)
        found = await opensea.resolve_collection(jwt, " ".join(name_parts))
        if not found:
            await message.answer("I couldn't find that collection on Robinhood Chain.")
            return
        built = await opensea.build_listing(
            jwt, addr, token=found["contract"], token_id=token_id,
            price=price, slug=found["slug"],
        )
    except (opensea.OpenSeaError, auth_svc.AuthError) as e:
        await message.answer(f"⚠️ Couldn't prepare that listing: {e}")
        return

    currency = found.get("floorSymbol") or "ETH"
    pid = secrets.token_urlsafe(8)
    _pending[pid] = {"telegram_id": user.id, "kind": "list", "built": built,
                     "name": found.get("name"), "token_id": token_id,
                     "price": price, "currency": currency}
    needs_approval = bool(built.get("nftApprove"))
    await message.answer(
        f"<b>List {found.get('name')} #{token_id}</b>\n\n"
        f"Price: <b>{_fmt(price)} {currency}</b>\n"
        f"Listing lasts 30 days\n\n"
        + ("<i>One approval transaction first, then a signature — the listing "
           "itself costs no gas.</i>" if needs_approval
           else "<i>Just a signature — listing costs no gas.</i>"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ List it", callback_data=f"nft:ok:{pid}"),
            InlineKeyboardButton(text="Cancel", callback_data=f"nft:no:{pid}"),
        ]]),
    )


# ── confirm ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("nft:"))
async def nft_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _pending.get(pid)
    if not p:
        await cb.answer("This expired. Run the command again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't yours.", show_alert=True)
        return
    if action == "no":
        _pending.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was signed.")
        return

    _pending.pop(pid, None)
    await cb.answer()

    async def progress(i: int, total: int) -> None:
        await cb.message.edit_text(f"Step {i} of {total}…")

    try:
        addr = await wallet_svc.wallet_address(p["telegram_id"])
        jwt = await auth_svc.get_jwt(p["telegram_id"])
        w = await wallet_svc.get_or_create_wallet(p["telegram_id"])

        if p["kind"] == "buy":
            row = p["row"]
            # Check the wallet can pay before signing anything — the built
            # purchase already names the exact cost.
            await cb.message.edit_text(f"Buying #{row.get('tokenId')}…")
            hashes = await opensea.execute(
                w["enc_key_ref"], addr, p["built"], on_step=progress
            )
            link = f'<a href="{EXPLORER}{hashes[-1]}">{hashes[-1][:10]}…</a>'
            await audit(p["telegram_id"], "nft_bought",
                        {"token_id": row.get("tokenId"), "hash": hashes[-1]})
            await cb.message.edit_text(
                f"✅ Bought <b>{p['name']} #{row.get('tokenId')}</b> for "
                f"{_price(row)}\n{link}\n\nSee it with /mynfts."
            )
            return

        await cb.message.edit_text("Listing…")
        await opensea.place_order(
            jwt, w["enc_key_ref"], addr, p["built"], kind="listing",
            on_step=progress,
        )
    except (opensea.OpenSeaError, auth_svc.AuthError, evm.EvmError,
            pf.PortfolioError, SignerError) as e:
        log.warning("nft_failed", telegram_id=p["telegram_id"], kind=p["kind"],
                    error=str(e))
        await audit(p["telegram_id"], "nft_failed",
                    {"kind": p["kind"], "error": str(e)[:200]})
        await cb.message.edit_text(f"❌ Couldn't complete that: {e}")
        return

    await audit(p["telegram_id"], "nft_listed",
                {"token_id": p["token_id"], "price": p["price"]})
    await cb.message.edit_text(
        f"✅ <b>{p['name']} #{p['token_id']}</b> is listed for "
        f"{_fmt(p['price'])} {p['currency']}.\n\n"
        "<i>It sells straight from your wallet — no gas was spent listing it.</i>"
    )
