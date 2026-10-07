import os
import sqlite3
import logging
from datetime import datetime, timedelta

import config

log = logging.getLogger(__name__)

_CANDIDATES = [
    config.DB_PATH,
    "/data/bot.db",
    "/app/data/bot.db",
    "./data/bot.db",
    "./bot.db",
    "/tmp/bot.db",
]


def _try_open(path: str) -> bool:
    if not path:
        return False
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)

        conn = sqlite3.connect(path, timeout=5)
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (id INTEGER)")
        conn.execute("DROP TABLE IF EXISTS _probe")
        conn.close()
        return True
    except Exception as e:
        log.warning(f"⚠️ Путь '{path}' недоступен: {e}")
        return False


def _resolve_db_path() -> str:
    for path in _CANDIDATES:
        if _try_open(path):
            log.info(f"✅ База данных будет сохранена: {path}")
            return path
    raise RuntimeError(
        "❌ Не удалось найти папку для БД. Включите постоянный диск /data "
        "в панели хостинга или укажите DB_PATH в .env"
    )


DB_NAME = _resolve_db_path()


def _conn():
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            subscription_until TEXT,
            total_paid REAL DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            tx_hash TEXT UNIQUE,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            user_id INTEGER PRIMARY KEY,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username, subscription_until, total_paid FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def add_pending(user_id: int):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pending (user_id, created_at) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET created_at = excluded.created_at
    """, (user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_pending_users():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM pending")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def remove_pending(user_id: int):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def activate_subscription(user_id: int, username: str, days: int, amount: float):
    conn = _conn()
    cur = conn.cursor()

    user = get_user(user_id)
    until = datetime.now() + timedelta(days=days)

    if user and user[2]:
        try:
            current_until = datetime.fromisoformat(user[2])
            if current_until > datetime.now():
                until = current_until + timedelta(days=days)
        except Exception:
            pass

    cur.execute("""
        INSERT INTO users (user_id, username, subscription_until, total_paid)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            subscription_until = excluded.subscription_until,
            total_paid = total_paid + excluded.total_paid,
            username = excluded.username
    """, (user_id, username, until.isoformat(), amount))
    conn.commit()
    conn.close()
    return until


def is_subscribed(user_id: int) -> bool:
    user = get_user(user_id)
    if not user or not user[2]:
        return False
    try:
        return datetime.fromisoformat(user[2]) > datetime.now()
    except Exception:
        return False


def payment_exists(tx_hash: str) -> bool:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM payments WHERE tx_hash = ?", (tx_hash,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def save_payment(user_id: int, amount: float, tx_hash: str):
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO payments (user_id, amount, tx_hash, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, amount, tx_hash, datetime.now().isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()
