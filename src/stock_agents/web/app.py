"""FastAPI app: company search, run history and stored reports.

Each request opens its own SQLite connection (connections must not be shared across threads).
The built frontend (`frontend/dist`, copied into `web/static` by `npm run build`) is served at `/`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from stock_agents.reports import export_filename, render_markdown
from stock_agents.storage import Report, Repository, Run, connect
from stock_agents.symbols import Listing, load_listings, normalize_name, search_listings

STATIC_DIR = Path(__file__).parent / "static"


def report_summary(r: Report) -> dict:
    return {
        "id": r.id, "run_id": r.run_id, "agent": r.agent, "stock": r.stock, "truncated": r.truncated,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "created_at": r.created_at.isoformat(),
        "chars": len(r.content_md),
    }


def report_detail(r: Report, repo: Repository) -> dict:
    """The report plus, for an outlook, the news/macro/fundamentals reports it was based on."""
    out = {**report_summary(r), "content_md": r.content_md, "inputs": {}}
    for agent, rid in (("news", r.news_report_id), ("macro", r.macro_report_id),
                       ("fundamentals", r.fundamentals_report_id)):
        if rid and (src := repo.get(rid)):
            out["inputs"][agent] = report_summary(src)
    return out


def run_summary(run: Run, repo: Repository) -> dict:
    """A run with the reports it produced and, for `all` runs, the older reports it reused."""
    own = repo.reports_for_run(run.id)
    reused: dict[str, Report] = {}
    for outlook in (r for r in own if r.agent == "outlook"):
        for agent, rid in (("news", outlook.news_report_id), ("macro", outlook.macro_report_id),
                           ("fundamentals", outlook.fundamentals_report_id)):
            if rid and not any(r.id == rid for r in own) and (src := repo.get(rid)):
                reused[agent] = src
    return {
        "id": run.id, "command": run.command, "stock": run.stock, "status": run.status, "error": run.error,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "provider": run.provider, "model": run.model,
        "reports": [report_summary(r) for r in own],
        "reused": [report_summary(r) for r in reused.values()],
    }


def create_app(db_file: str | Path, listings: list[Listing] | None = None) -> FastAPI:
    app = FastAPI(title="stock-analyst", docs_url="/api/docs", openapi_url="/api/openapi.json")
    connect(db_file).close()  # create / migrate once up front
    state = {"listings": listings}

    def get_listings() -> list[Listing]:
        if state["listings"] is None:  # loaded lazily: may download NSE's list on first use
            state["listings"] = load_listings()
        return state["listings"]

    def aliases(stock: str | None) -> list[str] | None:
        """Names a company may have been stored under: reports are saved with whatever was typed
        on the command line ("Infosys", "infy"), while the UI works with the NSE symbol."""
        if not stock:
            return None
        names = {stock}
        symbol = stock.strip().upper()
        for listing in get_listings():
            if listing.symbol == symbol:
                names |= {listing.symbol, listing.name, normalize_name(listing.name)}
        return sorted(names)

    def get_repo() -> Iterator[Repository]:
        # FastAPI may run this dependency and the endpoint on different threads; the connection
        # is still used by one request at a time and closed at the end of it.
        conn = connect(db_file, check_same_thread=False)
        try:
            yield Repository(conn)
        finally:
            conn.close()

    @app.get("/api/symbols")
    def symbols(q: str = Query("", max_length=100), limit: int = Query(10, ge=1, le=25)):
        return [{"symbol": l.symbol, "name": l.name} for l in search_listings(q, get_listings(), limit)]

    @app.get("/api/runs")
    def runs(stock: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             repo: Repository = Depends(get_repo)):
        return [run_summary(r, repo) for r in repo.list_runs(stocks=aliases(stock), limit=limit, offset=offset)]

    @app.get("/api/runs/{run_id}")
    def run(run_id: int, repo: Repository = Depends(get_repo)):
        found = repo.get_run(run_id)
        if found is None:
            raise HTTPException(404, f"No run #{run_id}")
        return run_summary(found, repo)

    def find_report(report_id: int, repo: Repository) -> Report:
        found = repo.get(report_id)
        if found is None:
            raise HTTPException(404, f"No report #{report_id}")
        return found

    @app.get("/api/reports")
    def reports(agent: str | None = None, stock: str | None = Query(None, max_length=100), limit: int = Query(20, ge=1, le=200),
                repo: Repository = Depends(get_repo)):
        return [report_summary(r) for r in repo.list(agent=agent, stocks=aliases(stock), limit=limit)]

    @app.get("/api/reports/{report_id}")
    def report(report_id: int, repo: Repository = Depends(get_repo)):
        return report_detail(find_report(report_id, repo), repo)

    @app.get("/api/reports/{report_id}/export")
    def export(report_id: int, repo: Repository = Depends(get_repo)):
        found = find_report(report_id, repo)
        return PlainTextResponse(
            render_markdown(found), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{export_filename(found)}"'})

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (STATIC_DIR / path).resolve()
            if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app
