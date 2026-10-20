"""Conservative, workspace-local spending guards. No credentials or prompts stored."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import uuid


DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "data" / "llm-usage.sqlite3"
ALLOWED_MODELS = frozenset({"gpt-4.1-mini"})
MAX_INPUT_BYTES = 10_000
INPUT_TOKEN_OVERHEAD = 1_024
OUTPUT_TOKEN_LIMIT = 256


class BudgetError(RuntimeError):
    pass


class UsageLimitReached(BudgetError):
    pass


class LedgerUnavailable(BudgetError):
    pass


@dataclass(frozen=True)
class Limits:
    daily_requests: int = 5
    lifetime_requests: int = 25
    daily_tokens: int = 50_000
    lifetime_tokens: int = 250_000

    def __post_init__(self):
        ceilings = {"daily_requests": 5, "lifetime_requests": 25,
                    "daily_tokens": 50_000, "lifetime_tokens": 250_000}
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}; higher limits require an intentional code change")

    @classmethod
    def from_env(cls):
        names = {"daily_requests": "CAREFLOW_LLM_DAILY_REQUEST_LIMIT",
                 "lifetime_requests": "CAREFLOW_LLM_LIFETIME_REQUEST_LIMIT",
                 "daily_tokens": "CAREFLOW_LLM_DAILY_TOKEN_LIMIT",
                 "lifetime_tokens": "CAREFLOW_LLM_LIFETIME_TOKEN_LIMIT"}
        return cls(**{field: int(os.environ[name]) for field, name in names.items() if name in os.environ})


class UsageLedger:
    def __init__(self, path=DEFAULT_LEDGER_PATH, limits=None, clock=None):
        self.path = Path(path).resolve()
        self.limits = limits or Limits.from_env()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS attempts (
                        id TEXT PRIMARY KEY, day TEXT NOT NULL, created_at TEXT NOT NULL,
                        model TEXT NOT NULL, reserved_input INTEGER NOT NULL,
                        reserved_output INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                        reported_input INTEGER, reported_output INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """)
            self.path.chmod(0o600)
        except (sqlite3.Error, OSError) as error:
            raise LedgerUnavailable("Usage ledger unavailable; model calls are disabled") from error

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5, isolation_level=None)

    @contextmanager
    def transaction(self):
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except (sqlite3.Error, OSError) as error:
            raise LedgerUnavailable("Usage ledger unavailable; model calls are disabled") from error

    @staticmethod
    def totals(connection, day=None):
        sql = """SELECT count(*), coalesce(sum(reserved_input + reserved_output),0),
                        coalesce(sum(reported_input),0), coalesce(sum(reported_output),0)
                 FROM attempts"""
        return connection.execute(sql + (" WHERE day=?" if day else ""), (day,) if day else ()).fetchone()

    def reserve(self, model, input_tokens, output_tokens=OUTPUT_TOKEN_LIMIT):
        if model not in ALLOWED_MODELS:
            raise ValueError("Model is not on Careflow's fixed allowlist")
        if type(input_tokens) is not int or not 0 < input_tokens <= MAX_INPUT_BYTES + INPUT_TOKEN_OVERHEAD:
            raise ValueError("Input token reservation is outside the fixed per-request limit")
        if type(output_tokens) is not int or not 0 < output_tokens <= OUTPUT_TOKEN_LIMIT:
            raise ValueError("Output allowance is outside the fixed per-request limit")
        now = self.clock().astimezone(timezone.utc)
        with self.transaction() as connection:
            if connection.execute("SELECT value FROM state WHERE key='halted'").fetchone():
                raise UsageLimitReached("Model calls halted after a provider accounting discrepancy")
            daily = self.totals(connection, now.date().isoformat())
            total = self.totals(connection)
            limits = self.limits
            reserved = input_tokens + output_tokens
            if (daily[0] >= limits.daily_requests or total[0] >= limits.lifetime_requests or
                daily[1] + reserved > limits.daily_tokens or total[1] + reserved > limits.lifetime_tokens):
                raise UsageLimitReached("Local model request or token allowance reached")
            identifier = uuid.uuid4().hex
            connection.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,'pending',NULL,NULL)",
                               (identifier, now.date().isoformat(), now.isoformat(), model, input_tokens, output_tokens))
        return identifier

    def finish(self, identifier, succeeded, usage=None):
        usage = usage or {}
        with self.transaction() as connection:
            row = connection.execute("SELECT reserved_input,reserved_output FROM attempts WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise LedgerUnavailable("Usage reservation missing; model calls are disabled")
            reported = (usage.get("input_tokens"), usage.get("output_tokens"))
            if any(value is not None and (type(value) is not int or value < 0) for value in reported):
                reported = (None, None)
            connection.execute("UPDATE attempts SET status=?,reported_input=?,reported_output=? WHERE id=?",
                               ("completed" if succeeded else "failed", *reported, identifier))
            overrun = any(value is not None and value > allowance for value, allowance in zip(reported, row))
            if overrun:
                connection.execute("INSERT OR REPLACE INTO state VALUES ('halted','provider usage exceeded reservation')")
        if overrun:
            raise UsageLimitReached("Provider usage exceeded the reservation; further model calls are halted")

    def snapshot(self):
        now = self.clock().astimezone(timezone.utc)
        with self.transaction() as connection:
            daily = self.totals(connection, now.date().isoformat())
            total = self.totals(connection)
            statuses = dict(connection.execute("SELECT status,count(*) FROM attempts GROUP BY status"))
            unreported = connection.execute("SELECT count(*) FROM attempts WHERE reported_input IS NULL OR reported_output IS NULL").fetchone()[0]
            halted = bool(connection.execute("SELECT value FROM state WHERE key='halted'").fetchone())
        return {
            "scope": "this workspace ledger", "utc_day": now.date().isoformat(), "halted": halted,
            "next_daily_reset": (now.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=1)).isoformat(),
            "daily": {"attempts": daily[0], "request_limit": self.limits.daily_requests,
                      "remaining_requests": max(0, self.limits.daily_requests-daily[0]),
                      "reserved_tokens": daily[1], "token_limit": self.limits.daily_tokens,
                      "remaining_tokens": max(0, self.limits.daily_tokens-daily[1])},
            "lifetime": {"attempts": total[0], "request_limit": self.limits.lifetime_requests,
                         "remaining_requests": max(0, self.limits.lifetime_requests-total[0]),
                         "reserved_tokens": total[1], "token_limit": self.limits.lifetime_tokens,
                         "remaining_tokens": max(0, self.limits.lifetime_tokens-total[1])},
            "reported_tokens": {"input_tokens": total[2], "output_tokens": total[3]},
            "unreported_attempts": unreported,
            "attempt_statuses": statuses,
            "accounting": "Full reservations remain charged locally, including failures and interrupted requests; no automatic refunds or retries.",
        }
