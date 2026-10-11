# admin.py
import json
import os
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

router = Router()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DATA_FILE = Path("data.json")

DEFAULT_RECIPIENT = "Manager_offer"
DEFAULT_WALLET = "UQDzaMh2ITk2XELKZ9cykD7cORxvasI-mDN8wkk2LEwclLmN"
DEFAULT_PRICE_GRAM = 5
DEFAULT_DAYS = 30


def _load() -> dict:
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    else:
        d = {}
    d.setdefault("allowed_ids", [])
    d.setdefault("allowed_usernames", [])
    d.setdefault("recipient", DEFAULT_RECIPIENT)
    d.setdefault("wallet", DEFAULT_WALLET)
    d.setdefault("price_gram", DEFAULT_PRICE_GRAM)
    d.setdefault("enabled", True)
    d.setdefault("stats", {"created": 0, "accepted": 0, "declined": 0})
    d.setdefault("subs", {})
    d.setdefault("pending", {})
    d.setdefault("paid_hashes", [])
    return d


def _save(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


DATA = _load()


# ---------- Подписки ----------
def sub_until(user_id: int) -> int:
    return int(DATA["subs"].get(str(user_id), 0))


def sub_active(user_id: int) -> bool:
    return sub_until(user_id) > int(time.time())


def grant_days(user_id: int, days: int = DEFAULT_DAYS) -> int:
    now = int(time.time())
    current = sub_until(user_id)
    base = current if current > now else now
    new_until = int((datetime.fromtimestamp(base) + timedelta(days=days)).timestamp())
    DATA["subs"][str(user_id)] = new_until
    _save(DATA)
    return new_until


def revoke(user_id: int) -> None:
    DATA["subs"].pop(str(user_id), None)
    _save(DATA)


# ---------- Публичные помощники для main.py ----------
def has_access(user_id: int, username: str | None) -> bool:
    if user_id == ADMIN_ID:
        return True
    if user_id in DATA["allowed_ids"]:
        return True
    if username and username.lower() in [u.lower() for u in DATA["allowed_usernames"]]:
        return True
    if sub_active(user_id):
        return True
    return False


def get_recipient() -> str:
    return DATA["recipient"]


def get_wallet() -> str:
    return DATA["wallet"]


def get_price_gram() -> float:
    return float(DATA["price_gram"])


def is_enabled() -> bool:
    return DATA.get("enabled", True)


def bump_stat(key: str) -> None:
    DATA["stats"][key] = DATA["stats"].get(key, 0) + 1
    _save(DATA)


def all_allowed_ids() -> list:
    ids = set(DATA["allowed_ids"])
    ids.update(int(uid) for uid in DATA["subs"].keys())
    if ADMIN_ID:
        ids.add(ADMIN_ID)
    return list(ids)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# ---------- Клавиатуры ----------
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm|stats"),
         InlineKeyboardButton(text="🎁 Офферы",    callback_data="adm|offers")],
        [InlineKeyboardButton(text="👥 Юзеры",     callback_data="adm|users"),
         InlineKeyboardButton(text="💳 Оплаты",    callback_data="adm|pending")],
        [InlineKeyboardButton(text="⚙️ Вкл/Выкл",  callback_data="adm|toggle"),
         InlineKeyboardButton(text="🗑 Очистить",  callback_data="adm|clear")],
    ])


# ---------- Команды админа ----------
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer(
        f"🛠 <b>Админ-панель</b>\n\n"
        f"Получатель подарков: <b>@{DATA['recipient']}</b>\n"
        f"Кошелёк GRAM: <code>{DATA['wallet']}</code>\n"
        f"Цена 30 дней: <b>{DATA['price_gram']} GRAM</b>\n"
        f"Приём офферов: <b>{'включён' if DATA['enabled'] else 'выключен'}</b>\n"
        f"Автопродление: <b>включено</b>\n\n"
        f"Команды:\n"
        f"/confirm &lt;id&gt; [дней] — выдать доступ вручную\n"
        f"/revoke &lt;id&gt; — забрать доступ\n"
        f"/pending — ожидающие оплаты\n"
        f"/price &lt;граммы&gt;\n"
        f"/wallet\n"
        f"/setwallet &lt;адрес&gt;\n"
        f"/setrecipient &lt;username&gt;\n"
        f"/stats, /users, /broadcast, /toggle, /adduser, /deluser",
        reply_markup=main_menu(),
    )


@router.message(Command("confirm"))
async def cmd_confirm(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /confirm <user_id> [дней=30]")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    days = DEFAULT_DAYS
    if len(parts) >= 3:
        try:
            days = int(parts[2])
        except ValueError:
            pass
    until = grant_days(uid, days)
    DATA["pending"].pop(str(uid), None)
    _save(DATA)
    dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
    await message.answer(f"✅ Доступ для <code>{uid}</code> продлён до <b>{dt}</b>")
    try:
        await message.bot.send_message(
            uid,
            f"✅ Оплата подтверждена. Доступ активен до <b>{dt}</b>.",
        )
    except Exception as e:
        logging.warning(f"notify {uid}: {e}")


@router.message(Command("revoke"))
async def cmd_revoke(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /revoke <user_id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    revoke(uid)
    await message.answer(f"🗑 Доступ у <code>{uid}</code> снят.")


@router.message(Command("pending"))
async def cmd_pending(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    if not DATA["pending"]:
        await message.answer("Нет ожидающих оплат.")
        return
    lines = []
    for uid, info in DATA["pending"].items():
        dt = datetime.fromtimestamp(info.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
        uname = info.get("username") or "—"
        lines.append(f"<code>{uid}</code> | @{uname} | {dt}\n/confirm {uid}")
    await message.answer("💳 <b>Ожидают подтверждения</b>\n\n" + "\n\n".join(lines))


@router.message(Command("price"))
async def cmd_price(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(f"Цена: {DATA['price_gram']} GRAM за 30 дней")
        return
    try:
        DATA["price_gram"] = float(parts[1].replace(",", "."))
    except ValueError:
        await message.answer("Число, пожалуйста.")
        return
    _save(DATA)
    await message.answer(f"✅ Новая цена: <b>{DATA['price_gram']} GRAM</b> / 30 дней")


@router.message(Command("wallet"))
async def cmd_wallet(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    await message.answer(f"Кошелёк: <code>{DATA['wallet']}</code>")


@router.message(Command("setwallet"))
async def cmd_setwallet(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /setwallet <адрес>")
        return
    DATA["wallet"] = parts[1].strip()
    _save(DATA)
    await message.answer(f"✅ Кошелёк: <code>{DATA['wallet']}</code>")


@router.message(Command("setrecipient"))
async def cmd_setrecipient(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /setrecipient <username>")
        return
    name = parts[1].lstrip("@").strip()
    DATA["recipient"] = name
    _save(DATA)
    await message.answer(f"✅ Получатель подарков: <b>@{name}</b>")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    s = DATA["stats"]
    active_subs = sum(1 for v in DATA["subs"].values() if v > time.time())
    await message.answer(
        f"📊 Создано: <b>{s.get('created', 0)}</b>\n"
        f"Принято: <b>{s.get('accepted', 0)}</b>\n"
        f"Отклонено: <b>{s.get('declined', 0)}</b>\n"
        f"Подписок активно: <b>{active_subs}</b>"
    )


@router.message(Command("users"))
async def cmd_users(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    lines = []
    for uid, until in DATA["subs"].items():
        dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
        active = "✅" if until > time.time() else "❌"
        lines.append(f"{active} <code>{uid}</code> — до {dt}")
    text = "\n".join(lines) or "Пусто."
    await message.answer("👥 <b>Подписки</b>\n\n" + text)


@router.message(Command("adduser"))
async def cmd_adduser(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /adduser <id> [username]")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    if uid not in DATA["allowed_ids"]:
        DATA["allowed_ids"].append(uid)
    if len(parts) >= 3:
        uname = parts[2].lstrip("@")
        if uname.lower() not in [u.lower() for u in DATA["allowed_usernames"]]:
            DATA["allowed_usernames"].append(uname)
    _save(DATA)
    await message.answer(f"✅ Постоянный доступ выдан <code>{uid}</code>")


@router.message(Command("deluser"))
async def cmd_deluser(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /deluser <id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    DATA["allowed_ids"] = [i for i in DATA["allowed_ids"] if i != uid]
    _save(DATA)
    await message.answer(f"🗑 У <code>{uid}</code> снят постоянный доступ.")


@router.message(Command("toggle"))
async def cmd_toggle(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    DATA["enabled"] = not DATA.get("enabled", True)
    _save(DATA)
    await message.answer(f"Приём офферов: {'включён' if DATA['enabled'] else 'выключен'}")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    text = (message.text or "").replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer("Формат: /broadcast <текст>")
        return
    sent = 0
    for uid in all_allowed_ids():
        try:
            await message.bot.send_message(uid, text)
            sent += 1
        except Exception as e:
            logging.warning(f"broadcast {uid}: {e}")
    await message.answer(f"📨 Отправлено: {sent}")


@router.callback_query(F.data.startswith("adm|"))
async def adm_cb(cb: CallbackQuery):
    if not cb.from_user or not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    action = cb.data.split("|")[1]

    if action == "stats":
        s = DATA["stats"]
        active_subs = sum(1 for v in DATA["subs"].values() if v > time.time())
        await cb.message.answer(
            f"📊 Создано: <b>{s.get('created', 0)}</b>\n"
            f"Принято: <b>{s.get('accepted', 0)}</b>\n"
            f"Отклонено: <b>{s.get('declined', 0)}</b>\n"
            f"Подписок активно: <b>{active_subs}</b>"
        )
    elif action == "users":
        lines = []
        for uid, until in DATA["subs"].items():
            dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
            active = "✅" if until > time.time() else "❌"
            lines.append(f"{active} <code>{uid}</code> — до {dt}")
        await cb.message.answer("👥 <b>Подписки</b>\n\n" + ("\n".join(lines) or "Пусто."))
    elif action == "pending":
        if not DATA["pending"]:
            await cb.message.answer("Нет ожидающих оплат.")
        else:
            lines = []
            for uid, info in DATA["pending"].items():
                dt = datetime.fromtimestamp(info.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
                uname = info.get("username") or "—"
                lines.append(f"<code>{uid}</code> | @{uname} | {dt}\n/confirm {uid}")
            await cb.message.answer("💳 <b>Ожидают</b>\n\n" + "\n\n".join(lines))
    elif action == "toggle":
        DATA["enabled"] = not DATA.get("enabled", True)
        _save(DATA)
        await cb.answer(f"Теперь {'вкл' if DATA['enabled'] else 'выкл'}")
    elif action == "clear":
        try:
            from main import OFFERS
            OFFERS.clear()
        except Exception:
            pass
        await cb.answer("Офферы очищены", show_alert=True)
    elif action == "offers":
        try:
            from main import OFFERS
            if not OFFERS:
                await cb.message.answer("Активных офферов нет.")
            else:
                lines = []
                for oid_, (amount, cur, gname, gnum, url) in list(OFFERS.items())[-20:]:
                    lines.append(f"<code>{oid_}</code> — {amount} {cur} {gname} #{gnum}")
                await cb.message.answer("🎁 <b>Активные офферы</b>\n\n" + "\n".join(lines))
        except Exception as e:
            await cb.message.answer(f"Ошибка: {e}")
    await cb.answer()


# ---------- Пользователь: оплата доступа ----------
@router.message(Command("subscribe", "buy_access", "доступ"))
async def cmd_subscribe(message: Message):
    if not message.from_user:
        return
    wallet = DATA["wallet"]
    price = DATA["price_gram"]
    uid = message.from_user.id
    until = sub_until(uid)
    active_note = ""
    if until > time.time():
        dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
        active_note = f"\n\nТекущий доступ активен до <b>{dt}</b>. Оплата продлит его."

    await message.answer(
        f"💎 <b>Доступ на 30 дней</b>\n\n"
        f"Стоимость: <b>{price} GRAM</b>\n\n"
        f"Переведи GRAM (TON) на кошелёк:\n"
        f"<code>{wallet}</code>\n\n"
        f"⚠️ <b>Обязательно</b> укажи комментарий к переводу:\n"
        f"<code>{uid}</code>\n\n"
        f"Доступ активируется автоматически в течение ~30 секунд после подтверждения сети."
        f"{active_note}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Мой доступ", callback_data="myaccess")],
        ]),
    )


@router.message(Command("myaccess"))
async def cmd_myaccess(message: Message):
    if not message.from_user:
        return
    until = sub_until(message.from_user.id)
    if until > time.time():
        dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
        await message.answer(f"✅ Доступ активен до <b>{dt}</b>")
    else:
        await message.answer("❌ Доступ не активен. Напиши /subscribe")


@router.callback_query(F.data == "myaccess")
async def cb_myaccess(cb: CallbackQuery):
    if not cb.from_user:
        return
    until = sub_until(cb.from_user.id)
    if until > time.time():
        dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
        await cb.answer(f"Доступ до {dt}", show_alert=True)
    else:
        await cb.answer("Доступ не активен", show_alert=True)
