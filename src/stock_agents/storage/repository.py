"""Reads and writes of runs and reports. Methods do not commit: wrap related writes in
`with conn:` so they land atomically."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

AGENTS = ("news", "macro", "outlook")


def stock_key(stock: str | None) -> str | None:
    """Normalised form used to match stock names: 'Tata Motors' == 'tata-motors'."""
    if stock is None:
        return None
    return re.sub(r"[^a-z0-9]+", "-", stock.lower()).strip("-") or None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Report:
    id: int
    run_id: int
    agent: str
    stock: str | None
    content_md: str
    truncated: bool
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime  # timezone-aware, UTC
    news_report_id: int | None
    macro_report_id: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Report":
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            agent=row["agent"],
            stock=row["stock"],
            content_md=row["content_md"],
            truncated=bool(row["truncated"]),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            created_at=datetime.fromisoformat(row["created_at"]),
            news_report_id=row["news_report_id"],
            macro_report_id=row["macro_report_id"],
        )


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- runs ---------------------------------------------------------------------------
    def start_run(
        self, command: str, stock: str | None = None, *, provider: str | None = None,
        model: str | None = None, max_tokens: int | None = None, now: datetime | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (command, stock, started_at, provider, model, max_tokens) VALUES (?,?,?,?,?,?)",
            (command, stock, _iso(now or utcnow()), provider, model, max_tokens),
        )
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str = "ok", error: str | None = None, now: datetime | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, _iso(now or utcnow()), run_id),
        )

    # -- reports ------------------------------------------------------------------------
    def save_report(
        self, run_id: int, agent: str, content_md: str, *, stock: str | None = None,
        truncated: bool = False, input_tokens: int | None = None, output_tokens: int | None = None,
        news_report_id: int | None = None, macro_report_id: int | None = None,
        created_at: datetime | None = None,
    ) -> int:
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}; expected one of {AGENTS}")
        if agent != "macro" and not stock:
            raise ValueError(f"{agent} reports need a stock")
        cur = self.conn.execute(
            """INSERT INTO reports (run_id, agent, stock, stock_key, content_md, truncated, input_tokens,
                                    output_tokens, created_at, news_report_id, macro_report_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, agent, stock, stock_key(stock), content_md, int(truncated), input_tokens,
             output_tokens, _iso(created_at or utcnow()), news_report_id, macro_report_id),
        )
        return cur.lastrowid

    def get(self, report_id: int) -> Report | None:
        row = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return Report.from_row(row) if row else None

    def latest(
        self, agent: str, stock: str | None = None, *, max_age: timedelta | None = None,
        now: datetime | None = None, include_truncated: bool = False,
    ) -> Report | None:
        """Most recent report for an agent (and stock). Truncated reports are skipped by default
        because they are incomplete and make poor inputs for other agents."""
        sql, params = "SELECT * FROM reports WHERE agent = ?", [agent]
        if agent != "macro":
            sql += " AND stock_key = ?"
            params.append(stock_key(stock))
        if not include_truncated:
            sql += " AND truncated = 0"
        if max_age is not None:
            sql += " AND created_at >= ?"
            params.append(_iso((now or utcnow()) - max_age))
        row = self.conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT 1", params).fetchone()
        return Report.from_row(row) if row else None

    def list(self, *, agent: str | None = None, stock: str | None = None, limit: int = 20) -> list[Report]:
        sql, params = "SELECT * FROM reports WHERE 1=1", []
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        if stock:
            sql += " AND stock_key = ?"
            params.append(stock_key(stock))
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", [*params, limit]).fetchall()
        return [Report.from_row(r) for r in rows]

    def exists(self, agent: str, stock: str | None, created_at: datetime) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM reports WHERE agent = ? AND stock_key IS ? AND created_at = ?",
            (agent, stock_key(stock), _iso(created_at)),
        ).fetchone()
        return row is not None
