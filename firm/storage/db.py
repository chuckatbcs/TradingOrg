"""SQLite persistence for firm audit logs, positions, and fused signals."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from firm.config import FIRM_CONFIG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fused_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ticker TEXT NOT NULL,
    trade_date TEXT,
    quant_pass INTEGER NOT NULL,
    quant_score REAL,
    llm_pass INTEGER NOT NULL,
    llm_rating TEXT,
    llm_score REAL,
    regime TEXT,
    regime_multiplier REAL,
    fused_score REAL,
    fused_pass INTEGER NOT NULL,
    blockers TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL,
    notional REAL,
    order_id TEXT,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    avg_entry REAL,
    market_value REAL,
    unrealized_pl REAL,
    sector TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    equity REAL,
    loss_pct REAL,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL
);
"""


class FirmDB:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (FIRM_CONFIG.data_dir / "firm.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_fused_signal(self, record: dict[str, Any]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO fused_signals (
                    run_id, ticker, trade_date, quant_pass, quant_score,
                    llm_pass, llm_rating, llm_score, regime, regime_multiplier,
                    fused_score, fused_pass, blockers, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.get("run_id"),
                    record["ticker"],
                    record.get("trade_date"),
                    1 if record.get("quant_pass") else 0,
                    record.get("quant_score"),
                    1 if record.get("llm_pass") else 0,
                    record.get("llm_rating"),
                    record.get("llm_score"),
                    record.get("regime"),
                    record.get("regime_multiplier"),
                    record.get("fused_score"),
                    1 if record.get("fused_pass") else 0,
                    json.dumps(record.get("blockers") or []),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_fused_signals(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fused_signals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def save_execution(self, record: dict[str, Any]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO executions (
                    run_id, ticker, side, qty, notional, order_id, status, message, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.get("run_id"),
                    record["ticker"],
                    record["side"],
                    record.get("qty"),
                    record.get("notional"),
                    record.get("order_id"),
                    record["status"],
                    record.get("message"),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_executions(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def upsert_positions(self, positions: list[dict]):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("DELETE FROM positions")
            for p in positions:
                conn.execute(
                    """
                    INSERT INTO positions (
                        ticker, qty, avg_entry, market_value, unrealized_pl, sector, synced_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        p["ticker"],
                        p["qty"],
                        p.get("avg_entry"),
                        p.get("market_value"),
                        p.get("unrealized_pl"),
                        p.get("sector"),
                        now,
                    ),
                )

    def list_positions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY ticker").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def save_lesson(self, content: str, source: str = "reviewer") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO lessons (content, source, created_at) VALUES (?,?,?)",
                (content, source, now),
            )
            return int(cur.lastrowid)

    def save_kill_event(self, event_type: str, equity: float, loss_pct: float, message: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO kill_switch_events (event_type, equity, loss_pct, message, created_at)
                VALUES (?,?,?,?,?)
                """,
                (event_type, equity, loss_pct, message, now),
            )
            return int(cur.lastrowid)

    def recent_lessons(self, limit: int = 10) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT content FROM lessons ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["content"] for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        if "blockers" in d and isinstance(d["blockers"], str):
            with suppress(json.JSONDecodeError):
                d["blockers"] = json.loads(d["blockers"])
        if "quant_pass" in d:
            d["quant_pass"] = bool(d["quant_pass"])
        if "llm_pass" in d:
            d["llm_pass"] = bool(d["llm_pass"])
        if "fused_pass" in d:
            d["fused_pass"] = bool(d["fused_pass"])
        return d
