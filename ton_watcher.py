# ton_watcher.py
"""
Фоновый мониторинг входящих TON-транзакций на кошелёк бота.
Автоматически продлевает подписку при поступлении GRAM с комментарием = user_id.
Присылает уведомление админу о каждой покупке.
"""
import asyncio
import logging
import aiohttp
from datetime import datetime

from admin import (
    get_wallet, get_price_gram, grant_days, sub_until, DATA, _save, ADMIN_ID,
)

log = logging.getLogger(__name__)

TONCENTER_API = "https://toncenter.com/api/v2/getTransactions"
API_KEY = ""                     # ← ключ с toncenter.com (можно оставить пустым)
POLL_INTERVAL = 30               # секунд между проверками
NANO = 1_000_000_000             # 1 TON = 10^9 nanotons

PROCESSED_TX: set[str] = set()


async def fetch_transactions(wallet: str, limit: int = 20) -> list:
    params = {
        "address": wallet,
        "limit": limit,
        "archival": "false",
    }
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TONCENTER_API, params=params, headers=headers, timeout=15
            ) as resp:
                if resp.status != 200:
                    log.warning(f"TON API HTTP {resp.status}")
                    return []
                data = await resp.json()
                if not data.get("ok"):
                    log.warning(f"TON API error: {data}")
                    return []
                return data.get("result", [])
    except Exception as e:
        log.warning(f"TON API request failed: {e}")
        return []


def extract_comment(tx: dict) -> str | None:
    in_msg = tx.get("in_msg") or {}
    msg = in_msg.get("message") or ""
    if msg and not msg.startswith("\x00"):
        return msg.strip()
    return None


def extract_amount(tx: dict) -> int:
    in_msg = tx.get("in_msg") or {}
    try:
        return int(in_msg.get("value", 0))
    except (TypeError, ValueError):
        return 0


def extract_sender(tx: dict) -> str:
    """Адрес отправителя (кошелёк, с которого пришли GRAM)."""
    in_msg = tx.get("in_msg") or {}
    return in_msg.get("source", "") or "—"


async def notify_admin(bot, user_id: int, amount_ton: float,
                       until_ts: int, tx_hash: str,
                       sender: str, price: float) -> None:
    """Уведомить админа о новой автопокупке подписки."""
    if not ADMIN_ID:
        return

    dt = datetime.fromtimestamp(until_ts).strftime("%Y-%m-%d %H:%M")

    try:
        user = await bot.get_chat(user_id)
        uname = f"@{user.username}" if user.username else "—"
        full_name = user.full_name or "—"
    except Exception:
        uname = "—"
        full_name = "—"

    text = (
        f"💎 <b>Новая покупка подписки</b>\n\n"
        f"👤 Пользователь: <b>{full_name}</b>\n"
        f"🔗 Юзернейм: {uname}\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        f"💰 Оплачено: <b>{amount_ton} GRAM</b>\n"
        f"💵 Цена: {price} GRAM\n"
        f"📅 Доступ до: <b>{dt}</b>\n\n"
        f"📥 Кошелёк отправителя:\n"
        f"<code>{sender}</code>\n\n"
        f"🔗 Хеш: <code>{tx_hash[:16]}…</code>"
    )

    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        log.warning(f"notify admin: {e}")


async def process_transaction(tx: dict, bot) -> None:
    tx_hash = tx.get("transaction_id", {}).get("hash", "")
    if not tx_hash or tx_hash in PROCESSED_TX:
        return

    comment = extract_comment(tx)
    if not comment:
        return

    try:
        user_id = int(comment)
    except ValueError:
        return

    amount_nano = extract_amount(tx)
    amount_ton = amount_nano / NANO
    price = get_price_gram()

    if amount_ton + 1e-9 < price:
        log.info(f"Недостаточно GRAM от {user_id}: {amount_ton} < {price}")
        PROCESSED_TX.add(tx_hash)
        return

    paid_hashes = DATA.setdefault("paid_hashes", [])
    if tx_hash in paid_hashes:
        PROCESSED_TX.add(tx_hash)
        return

    # Начисляем подписку
    until = grant_days(user_id, 30)
    DATA["pending"].pop(str(user_id), None)
    paid_hashes.append(tx_hash)
    if len(paid_hashes) > 500:
        DATA["paid_hashes"] = paid_hashes[-500:]
    _save(DATA)

    PROCESSED_TX.add(tx_hash)

    dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
    sender = extract_sender(tx)
    log.info(f"✅ Автопродление для {user_id} до {dt} (+{amount_ton} GRAM)")

    # 1) Уведомление пользователю
    try:
        await bot.send_message(
            user_id,
            f"✅ Оплата получена автоматически.\n"
            f"Сумма: <b>{amount_ton} GRAM</b>\n"
            f"Доступ активен до <b>{dt}</b>.\n\n"
            f"Приятной работы! 🎁",
        )
    except Exception as e:
        log.warning(f"notify user {user_id}: {e}")

    # 2) Уведомление админу
    await notify_admin(bot, user_id, amount_ton, until, tx_hash, sender, price)


async def watch_loop(bot) -> None:
    log.info("TON watcher запущен")
    await asyncio.sleep(5)

    while True:
        try:
            wallet = get_wallet()
            txs = await fetch_transactions(wallet, limit=20)
            for tx in txs:
                await process_transaction(tx, bot)
        except Exception as e:
            log.exception(f"watcher error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


def start_watcher(bot) -> asyncio.Task:
    return asyncio.create_task(watch_loop(bot))
