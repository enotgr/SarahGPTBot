"""Durable, atomic spending caps shared by all requests on this bot server."""

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import config


class BudgetExceeded(Exception):
    pass


class RateLimited(Exception):
    pass


def request_key(user_id, request_id):
    return hashlib.sha256(f"{user_id}:{request_id}".encode()).hexdigest()


class Finance:
    def __init__(self, path=None, clock=time.time):
        self.path = path or config.FINANCE_DB_PATH
        self.clock = clock
        self._init_lock = threading.Lock()
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self._initialize()
            self._initialized = True

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=20)
        os.chmod(self.path, 0o600)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
                day TEXT NOT NULL, month TEXT NOT NULL, created REAL NOT NULL,
                kind TEXT NOT NULL, model TEXT NOT NULL, category TEXT NOT NULL,
                credits INTEGER NOT NULL, reserved INTEGER NOT NULL, actual INTEGER,
                started INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'pending',
                usage TEXT, wallet_charge INTEGER)""")
            connection.execute("CREATE INDEX IF NOT EXISTS requests_month ON requests(month)")
            connection.execute("CREATE INDEX IF NOT EXISTS requests_user_day ON requests(user_id,day)")
            connection.execute("""CREATE TABLE IF NOT EXISTS grants (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, month TEXT NOT NULL,
                amount INTEGER NOT NULL, user_id INTEGER NOT NULL)""")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value REAL NOT NULL)"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def connection(self):
        self.initialize()
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def period(self):
        now = self.clock()
        date = datetime.fromtimestamp(now, ZoneInfo("Asia/Tbilisi"))
        return now, date.strftime("%Y-%m-%d"), date.strftime("%Y-%m")

    async def reserve(self, user_id, request_id, kind, model, category, credits, amount):
        return await asyncio.to_thread(
            self._reserve, user_id, request_id, kind, model, category, credits, amount
        )

    def _reserve(self, user_id, request_id, kind, model, category, credits, amount):
        now, day, month = self.period()
        key = request_key(user_id, request_id)
        if amount <= 0:
            raise ValueError("Invalid cost reservation")
        with self.connection() as db:
            checked = db.execute("SELECT value FROM metadata WHERE name='payments_synced_at'").fetchone()
            if not checked or now - checked[0] > 3600:
                raise BudgetExceeded(
                    "Выполняется проверка оплат. Попробуйте немного позже. Токены не списаны."
                )
            if db.execute("SELECT 1 FROM requests WHERE id=?", (key,)).fetchone():
                raise RateLimited("Этот запрос уже учтён.")
            latest = db.execute("SELECT MAX(created) FROM requests WHERE user_id=?", (user_id,)).fetchone()[0]
            if latest is not None and now - latest < config.REQUEST_COOLDOWN_SECONDS:
                raise RateLimited("Подождите пару секунд перед следующим запросом.")
            count = db.execute(
                "SELECT COUNT(*) FROM requests WHERE user_id=? AND day=?", (user_id, day)
            ).fetchone()[0]
            if count >= config.USER_DAILY_REQUESTS:
                raise RateLimited("Достигнут дневной лимит запросов. Попробуйте завтра.")
            if category == "promo":
                count = db.execute(
                    "SELECT COUNT(*) FROM requests WHERE user_id=? AND day=? AND category='promo'",
                    (user_id, day),
                ).fetchone()[0]
                if count >= config.PROMO_DAILY_REQUESTS:
                    raise RateLimited("Сегодня бесплатные запросы закончились. /buy — пополнить баланс.")
            if db.execute(
                "SELECT 1 FROM requests WHERE month=? AND actual>reserved LIMIT 1", (month,)
            ).fetchone():
                raise BudgetExceeded("API временно приостановлен: требуется проверка стоимости.")
            for condition, args, limit in [
                ("month=?", (month,), config.MONTHLY_API_BUDGET_MICRO),
                ("day=?", (day,), config.DAILY_API_BUDGET_MICRO),
                (
                    "month=? AND category='promo'",
                    (month,),
                    config.PROMO_MONTHLY_BUDGET_MICRO if category == "promo" else None,
                ),
            ]:
                if limit is None:
                    continue
                spent = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM requests WHERE " + condition, args
                ).fetchone()[0]
                if spent + amount > limit:
                    raise BudgetExceeded(
                        "Достигнут лимит расходов сервиса. Попробуйте позже. Токены не списаны."
                    )
            db.execute(
                """INSERT INTO requests
                (id,user_id,request_id,day,month,created,kind,model,category,credits,reserved)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (key, user_id, request_id, day, month, now, kind, model, category, credits, amount),
            )
        return key

    async def start(self, key):
        await asyncio.to_thread(self._start, key)

    def _start(self, key):
        with self.connection() as db:
            row = db.execute("SELECT outcome FROM requests WHERE id=?", (key,)).fetchone()
            if not row or row["outcome"] != "pending":
                raise RuntimeError("Missing active cost reservation")
            db.execute("UPDATE requests SET started=1 WHERE id=?", (key,))

    async def settle(self, key, actual, outcome, usage=None, charge=False):
        await asyncio.to_thread(self._settle, key, actual, outcome, usage, charge)

    def _settle(self, key, actual, outcome, usage, charge):
        with self.connection() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (key,)).fetchone()
            if not row or row["outcome"] != "pending":
                return
            # Unknown/disconnected usage is charged at the full reserved ceiling.
            amount = actual if actual is not None else row["reserved"] if row["started"] else 0
            db.execute(
                "UPDATE requests SET actual=?,outcome=?,usage=?,wallet_charge=? WHERE id=?",
                (max(0, amount), outcome, json.dumps(usage) if usage else None, int(charge), key),
            )

    async def wallet_charge(self, user_id, request_id):
        return await asyncio.to_thread(self._wallet_charge, user_id, request_id)

    def _wallet_charge(self, user_id, request_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT wallet_charge FROM requests WHERE id=?", (request_key(user_id, request_id),)
            ).fetchone()
            return None if not row or row[0] is None else bool(row[0])

    async def claim_grant(self, kind, user_id, amount):
        return await asyncio.to_thread(self._claim_grant, kind, user_id, amount)

    def _claim_grant(self, kind, user_id, amount):
        _, day, month = self.period()
        key = f"{kind}_{user_id}"
        with self.connection() as db:
            if db.execute("SELECT 1 FROM grants WHERE id=?", (key,)).fetchone():
                return True
            daily = db.execute("SELECT COALESCE(SUM(amount),0) FROM grants WHERE day=?", (day,)).fetchone()[0]
            monthly = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM grants WHERE month=?", (month,)
            ).fetchone()[0]
            if daily + amount > config.PROMO_DAILY_CREDITS or monthly + amount > config.PROMO_MONTHLY_CREDITS:
                return False
            db.execute("INSERT INTO grants VALUES(?,?,?,?,?)", (key, day, month, amount, user_id))
            return True

    async def recover(self):
        await asyncio.to_thread(self._recover)

    def _recover(self):
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET actual=CASE WHEN started=1 THEN reserved ELSE 0 END,"
                "wallet_charge=started,outcome='interrupted' WHERE outcome='pending'"
            )

    async def report(self):
        return await asyncio.to_thread(self._report)

    async def payments_ready(self, ready):
        await asyncio.to_thread(self._payments_ready, ready)

    def _payments_ready(self, ready):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES('payments_synced_at',?)",
                (self.clock() if ready else 0,),
            )

    def _report(self):
        _, day, month = self.period()
        with self.connection() as db:
            total = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(actual,reserved)),0),"
                "SUM(CASE WHEN actual IS NULL THEN 1 ELSE 0 END) FROM requests WHERE month=?",
                (month,),
            ).fetchone()
            categories = {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT category,SUM(COALESCE(actual,reserved)) FROM requests WHERE month=? GROUP BY category",
                    (month,),
                )
            }
            return {
                "month": month,
                "requests": total[0],
                "cost_micro": total[1],
                "pending": total[2] or 0,
                "categories": categories,
                "monthly_limit_micro": config.MONTHLY_API_BUDGET_MICRO,
                "daily_limit_micro": config.DAILY_API_BUDGET_MICRO,
            }


finance = Finance()
