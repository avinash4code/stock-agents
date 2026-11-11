"""Reads and writes of runs and reports. Methods do not commit: wrap related writes in
`with conn:` so they land atomically."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

AGENTS = ("news", "macro", "outlook", "fundamentals")


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
class Run:
    id: int
    command: str
    stock: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    error: str | None
    provider: str | None
    model: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Run":
        finished = row["finished_at"]
        return cls(
            id=row["id"], command=row["command"], stock=row["stock"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(finished) if finished else None,
            status=row["status"], error=row["error"], provider=row["provider"], model=row["model"],
        )


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
    fundamentals_report_id: int | None = None

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
            fundamentals_report_id=row["fundamentals_report_id"],
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

    def get_run(self, run_id: int) -> Run | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run.from_row(row) if row else None

    def list_runs(self, *, stocks: list[str] | None = None, limit: int = 50, offset: int = 0) -> list[Run]:
        """Newest first; `stocks` keeps runs for any of those names. Runs of the one-off `import`
        command are not analyses, so they are left out."""
        sql, params = "SELECT * FROM runs WHERE command != 'import'", []
        if stocks:
            keys = sorted({k for k in map(stock_key, stocks) if k})
            sql += f" AND lower(replace(stock, ' ', '-')) IN ({','.join('?' * len(keys))})"
            params += keys
        rows = self.conn.execute(
            sql + " ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return [Run.from_row(r) for r in rows]

    def reports_for_run(self, run_id: int) -> list[Report]:
        rows = self.conn.execute("SELECT * FROM reports WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [Report.from_row(r) for r in rows]

    # -- reports ------------------------------------------------------------------------
    def save_report(
        self, run_id: int, agent: str, content_md: str, *, stock: str | None = None,
        truncated: bool = False, input_tokens: int | None = None, output_tokens: int | None = None,
        news_report_id: int | None = None, macro_report_id: int | None = None,
        fundamentals_report_id: int | None = None, created_at: datetime | None = None,
    ) -> int:
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}; expected one of {AGENTS}")
        if agent != "macro" and not stock:
            raise ValueError(f"{agent} reports need a stock")
        cur = self.conn.execute(
            """INSERT INTO reports (run_id, agent, stock, stock_key, content_md, truncated, input_tokens,
                                    output_tokens, created_at, news_report_id, macro_report_id,
                                    fundamentals_report_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, agent, stock, stock_key(stock), content_md, int(truncated), input_tokens,
             output_tokens, _iso(created_at or utcnow()), news_report_id, macro_report_id,
             fundamentals_report_id),
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

    def list(self, *, agent: str | None = None, stock: str | None = None, stocks: list[str] | None = None,
             limit: int = 20) -> list[Report]:
        """Newest first. `stock` is one name; `stocks` any of several names for the same company."""
        sql, params = "SELECT * FROM reports WHERE 1=1", []
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        names = [*(stocks or []), *([stock] if stock else [])]
        if names:
            keys = sorted({k for k in map(stock_key, names) if k})
            sql += f" AND stock_key IN ({','.join('?' * len(keys))})"
            params += keys
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", [*params, limit]).fetchall()
        return [Report.from_row(r) for r in rows]

    def exists(self, agent: str, stock: str | None, created_at: datetime) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM reports WHERE agent = ? AND stock_key IS ? AND created_at = ?",
            (agent, stock_key(stock), _iso(created_at)),
        ).fetchone()
        return row is not None
