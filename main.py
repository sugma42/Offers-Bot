import asyncio
import logging
import time
from datetime import datetime
from urllib.parse import urlencode

import aiohttp
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    TelegramObject
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Callable, Dict, Any, Awaitable

import config
import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

PRICE_NANO = int(config.SUBSCRIPTION_PRICE * 1_000_000_000)
TONCENTER_URL = "https://toncenter.com/api/v2/getTransactions"


# ────────────────────────────────────────────────
# MIDDLEWARE ДЛЯ ЛОГИРОВАНИЯ НАЖАТИЙ И СООБЩЕНИЙ
# ────────────────────────────────────────────────
class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, CallbackQuery):
            user = event.from_user
            username = f"@{user.username}" if user.username else user.full_name
            logging.info(
                f"🖱️ НАЖАТИЕ | user_id={user.id} | {username} | "
                f"кнопка='{event.data}' | сообщение_id={event.message.message_id}"
            )
        elif isinstance(event, Message):
            user = event.from_user
            username = f"@{user.username}" if user.username else user.full_name
            text = (event.text or "").replace("\n", " ")[:100]
            logging.info(
                f"💬 СООБЩЕНИЕ | user_id={user.id} | {username} | текст='{text}'"
            )
        return await handler(event, data)


dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())


# ────────────────────────────────────────────────
# ЗАЩИТА ОТ RATE LIMIT
# ────────────────────────────────────────────────
_last_request_time = 0.0
_rate_lock = asyncio.Lock()


async def _rate_limited_get(url, params, headers, timeout=15):
    global _last_request_time
    async with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < 1.2:
            await asyncio.sleep(1.2 - elapsed)
        _last_request_time = time.monotonic()

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers, timeout=timeout) as resp:
            return resp.status, await resp.json()


# ────────────────────────────────────────────────
# Генерация ссылки на Tonkeeper
# ────────────────────────────────────────────────
def tonkeeper_link(user_id: int) -> str:
    query = urlencode({
        "amount": PRICE_NANO,
        "text": str(user_id),
    })
    return f"https://app.tonkeeper.com/transfer/{config.TONKEEPER_ADDRESS}?{query}"


# ────────────────────────────────────────────────
# Получение транзакций
# ────────────────────────────────────────────────
async def fetch_transactions(limit: int = 30):
    params = {
        "address": config.TONKEEPER_ADDRESS,
        "limit": limit,
        "archival": "false",
    }
    headers = {}
    if config.TONCENTER_API_KEY:
        headers["X-API-Key"] = config.TONCENTER_API_KEY

    for attempt in range(3):
        try:
            status, data = await _rate_limited_get(TONCENTER_URL, params, headers)

            if status == 429:
                wait = 2 ** attempt
                logging.warning(
                    f"⚠️ TonCenter rate limit (429). "
                    f"Повтор через {wait} сек... (попытка {attempt + 1}/3)"
                )
                await asyncio.sleep(wait)
                continue

            if not data.get("ok"):
                raise RuntimeError(f"TonCenter error: {data}")

            return data.get("result", [])

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.warning(f"⚠️ Сетевая ошибка: {e}. Повтор...")
            await asyncio.sleep(2 ** attempt)
            continue

    logging.error("❌ TonCenter недоступен после 3 попыток")
    return []


# ────────────────────────────────────────────────
# Поиск платежа пользователя
# ────────────────────────────────────────────────
async def find_user_payment(user_id: int, max_age_seconds: int = 3600):
    try:
        txs = await fetch_transactions()
    except Exception as e:
        logging.error(f"Ошибка получения транзакций: {e}")
        return None

    now = int(time.time())
    for tx in txs:
        in_msg = tx.get("in_msg") or {}
        value = int(in_msg.get("value", "0") or 0)
        comment = (in_msg.get("message") or "").strip()
        dest = in_msg.get("destination", "")

        if not dest or not value:
            continue
        if value < PRICE_NANO:
            continue
        if comment != str(user_id):
            continue

        utime = int(tx.get("utime", 0))
        if now - utime > max_age_seconds:
            continue

        tx_hash = tx.get("transaction_id", {}).get("hash")
        if not tx_hash:
            continue
        if db.payment_exists(tx_hash):
            continue

        return {
            "hash": tx_hash,
            "amount_ton": value / 1e9,
            "utime": utime,
        }
    return None


# ────────────────────────────────────────────────
# Клавиатуры
# ────────────────────────────────────────────────
def main_menu(is_active: bool = False):
    kb = InlineKeyboardBuilder()
    if is_active:
        kb.button(text="🔄 Продлить подписку", callback_data="buy_sub")
    else:
        kb.button(
            text=f"💎 Купить подписку ({config.SUBSCRIPTION_PRICE:g} TON)",
            callback_data="buy_sub"
        )
    kb.button(text="👤 Мой профиль", callback_data="profile")
    kb.adjust(1)
    return kb.as_markup()


def pay_menu(user_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="💎 Оплатить в Tonkeeper",
            url=tonkeeper_link(user_id)
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="✅ Я оплатил — подтвердить",
            callback_data="check_payment"
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back"
        )
    )
    return kb.as_markup()


# ────────────────────────────────────────────────
# Хендлеры
# ────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    db.init_db()
    active = db.is_subscribed(message.from_user.id)

    text = (
        f"👋 Привет, {message.from_user.full_name}!\n\n"
        f"Это бот продажи подписки за **TON**.\n"
        f"Средства поступают напрямую на кошелёк **Tonkeeper**.\n\n"
    )

    if active:
        user = db.get_user(message.from_user.id)
        until = datetime.fromisoformat(user[2]).strftime("%d.%m.%Y %H:%M")
        text += f"✅ Ваша подписка активна до **{until}**\n\n"

    text += "Выберите действие 👇"

    await message.answer(text, reply_markup=main_menu(active), parse_mode="Markdown")


@dp.callback_query(F.data == "buy_sub")
async def buy_sub(callback: CallbackQuery):
    user_id = callback.from_user.id

    text = (
        f"💎 **Подписка на {config.SUBSCRIPTION_DAYS} дней**\n\n"
        f"💰 Стоимость: **{config.SUBSCRIPTION_PRICE:g} TON**\n\n"
        f"👇 **Нажмите кнопку «Оплатить в Tonkeeper»** — приложение откроется "
        f"с уже заполненными адресом, суммой и комментарием.\n\n"
        f"Если у вас не установлен Tonkeeper, откройте ссылку вручную:\n"
        f"`{config.TONKEEPER_ADDRESS}`\n\n"
        f"**Обязательно оставьте комментарий:** `{user_id}`\n"
        f"_(он подставится автоматически)_\n\n"
        f"После оплаты вернитесь в бота и нажмите **«Я оплатил — подтвердить»**.\n\n"
        f"⚠️ Без комментария `{user_id}` бот не сможет найти ваш платёж!"
    )

    db.add_pending(user_id)

    await callback.message.edit_text(
        text,
        reply_markup=pay_menu(user_id),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def process_payment(user_id: int, username: str):
    payment = await find_user_payment(user_id)

    if not payment:
        return False, (
            "❌ **Платёж не найден.**\n\n"
            "Проверьте:\n"
            f"• Сумма перевода ровно **{config.SUBSCRIPTION_PRICE:g} TON**\n"
            f"• Комментарий к переводу — `{user_id}`\n"
            f"• Прошло ли хотя бы 30 секунд с момента отправки\n"
            f"• Адрес получателя: `{config.TONKEEPER_ADDRESS}`\n\n"
            "Если всё верно — подождите минуту и нажмите «Подтвердить» ещё раз."
        )

    tx_hash = payment["hash"]
    amount = payment["amount_ton"]

    db.save_payment(user_id, amount, tx_hash)
    until = db.activate_subscription(
        user_id, username, config.SUBSCRIPTION_DAYS, amount
    )
    db.remove_pending(user_id)

    text = (
        f"✅ **Оплата получена!**\n\n"
        f"💰 Сумма: `{amount} TON`\n"
        f"📅 Подписка активна до: `{until.strftime('%d.%m.%Y %H:%M')}`\n"
        f"🔗 TX: `{tx_hash[:20]}...`\n\n"
    )

    if config.SUBSCRIBER_CHANNEL_LINK:
        text += f"👉 Ссылка на закрытый канал:\n{config.SUBSCRIBER_CHANNEL_LINK}"
    else:
        text += "Спасибо за покупку! 🎉"

    return True, text


@dp.callback_query(F.data == "check_payment")
async def check_payment(callback: CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.full_name

    await callback.message.edit_text("🔍 Проверяю поступление на Tonkeeper...")

    ok, text = await process_payment(user_id, username)

    if ok:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu(True),
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text(
            text,
            reply_markup=pay_menu(user_id),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    user = db.get_user(callback.from_user.id)

    if not user:
        await callback.message.edit_text(
            "❌ Вы ещё не покупали подписку.\n\nНажмите «Купить подписку».",
            reply_markup=main_menu(False)
        )
        return

    active = db.is_subscribed(callback.from_user.id)
    until = datetime.fromisoformat(user[2]).strftime("%d.%m.%Y %H:%M")

    text = (
        f"👤 **Профиль**\n\n"
        f"🆔 ID: `{callback.from_user.id}`\n"
        f"📅 Подписка до: `{until}`\n"
        f"Статус: {'✅ активна' if active else '❌ истекла'}\n"
        f"💰 Всего оплачено: `{user[3]} TON`"
    )

    await callback.message.edit_text(
        text, reply_markup=main_menu(active), parse_mode="Markdown"
    )


@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):
    active = db.is_subscribed(callback.from_user.id)
    text = f"👋 Привет, {callback.from_user.full_name}!\n\nВыберите действие 👇"
    await callback.message.edit_text(
        text, reply_markup=main_menu(active), parse_mode="Markdown"
    )


# ────────────────────────────────────────────────
# Автопроверка платежей в фоне
# ────────────────────────────────────────────────
async def auto_check_loop():
    await asyncio.sleep(30)

    while True:
        try:
            await asyncio.sleep(max(config.AUTO_CHECK_INTERVAL, 90))

            pending = db.get_pending_users()
            for user_id in pending:
                payment = await find_user_payment(user_id)
                if not payment:
                    continue

                tx_hash = payment["hash"]
                amount = payment["amount_ton"]
                db.save_payment(user_id, amount, tx_hash)
                until = db.activate_subscription(
                    user_id, "auto", config.SUBSCRIPTION_DAYS, amount
                )
                db.remove_pending(user_id)

                try:
                    text = (
                        f"✅ **Оплата получена!**\n\n"
                        f"💰 Сумма: `{amount} TON`\n"
                        f"📅 Подписка активна до: `{until.strftime('%d.%m.%Y %H:%M')}`\n"
                        f"🔗 TX: `{tx_hash[:20]}...`\n\n"
                    )
                    if config.SUBSCRIBER_CHANNEL_LINK:
                        text += f"👉 Ссылка на закрытый канал:\n{config.SUBSCRIBER_CHANNEL_LINK}"
                    else:
                        text += "Спасибо за покупку! 🎉"

                    await bot.send_message(
                        user_id, text,
                        reply_markup=main_menu(True),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.warning(f"Не удалось уведомить {user_id}: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Ошибка в auto_check_loop: {e}")
            await asyncio.sleep(15)


# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────
async def main():
    db.init_db()
    logging.info("🤖 Бот запущен")
    logging.info(f"📍 Приём платежей: {config.TONKEEPER_ADDRESS}")
    logging.info(f"💰 Цена подписки: {config.SUBSCRIPTION_PRICE:g} TON")
    logging.info(f"⏱️  Автопроверка: каждые {max(config.AUTO_CHECK_INTERVAL, 90)} сек")
    logging.info(f"🗄️  База данных: {db.DB_NAME}")

    if config.AUTO_CHECK_INTERVAL > 0:
        asyncio.create_task(auto_check_loop())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
