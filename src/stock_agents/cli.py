"""Command line: run the whole workflow or any single agent, and browse stored reports.

    stock-analyst all TCS                 # whole workflow (same as `stock-analyst TCS`)
    stock-analyst news TCS                # news agent only
    stock-analyst macro                   # macro agent only (market-wide, no stock needed)
    stock-analyst outlook TCS             # outlook agent only, on the latest stored news + macro reports
    stock-analyst reports list            # browse stored reports; also `reports show ID`, `reports export ID`
    stock-analyst import news_reports/    # one-time import of reports saved as files by older versions
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from stock_agents.agents import macro_agent, news_agent, outlook_agent
from stock_agents.config import LLMConfig, db_path, reuse_hours
from stock_agents.graph import run_workflow
from stock_agents.llm import default_llm
from stock_agents.reports import DEFAULT_EXPORT_DIR, export_report, import_reports, render_markdown
from stock_agents.storage import Report, Repository, connect
from stock_agents.storage.repository import utcnow

COMMANDS = {"all", "news", "macro", "outlook", "reports", "import"}
OPTIONS_WITH_VALUE = {"--db"}


def _llm():
    cfg = LLMConfig.from_env()
    return cfg, default_llm(cfg)


@contextmanager
def tracked_run(conn: sqlite3.Connection, command: str, stock: str | None, cfg: LLMConfig):
    """Record a run; it is marked failed (with the error) if the body raises."""
    repo = Repository(conn)
    with conn:
        run_id = repo.start_run(command, stock, provider=cfg.provider, model=cfg.model, max_tokens=cfg.max_tokens)
    try:
        yield run_id
    except BaseException as exc:
        with conn:
            repo.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    with conn:
        repo.finish_run(run_id)


def _save(repo: Repository, run_id: int, agent: str, stock: str | None, text: str, meta: dict | None, **links) -> int:
    meta = meta or {}
    report_id = repo.save_report(
        run_id, agent, text, stock=stock, truncated=meta.get("truncated", False),
        input_tokens=meta.get("input_tokens"), output_tokens=meta.get("output_tokens"), **links,
    )
    note = " (INCOMPLETE: hit the token limit)" if meta.get("truncated") else ""
    print(f"Saved {agent} report #{report_id}{note}", file=sys.stderr)
    return report_id


def _show(title: str, text: str) -> None:
    print(f"=== {title} ===\n{text}\n")


def _pick(repo: Repository, report_id: int | None, agent: str, stock: str | None, what: str) -> Report:
    report = repo.get(report_id) if report_id else repo.latest(agent, stock)
    if report is None or report.agent != agent:
        hint = f"report #{report_id} is not a {what} report" if report_id and report else (
            f"no {what} report #{report_id}" if report_id else f"no stored {what} report - run `stock-analyst {agent}"
            + (f" {stock}" if agent != "macro" else "") + "` first"
        )
        raise SystemExit(f"Cannot run outlook: {hint}.")
    print(f"Using {what} report #{report.id} from {report.created_at.astimezone():%Y-%m-%d %H:%M}", file=sys.stderr)
    return report


def _reusable(repo: Repository, agent: str, stock: str | None, cli_hours: float | None) -> Report | None:
    """Latest complete stored report younger than the allowed age (CLI value, else config); 0 disables."""
    hours = cli_hours if cli_hours is not None else reuse_hours(agent)
    if hours <= 0:
        return None
    report = repo.latest(agent, stock, max_age=timedelta(hours=hours))
    if report:
        age = utcnow() - report.created_at
        print(f"Reusing {agent} report #{report.id} ({age.total_seconds() / 3600:.1f}h old, limit {hours:g}h)", file=sys.stderr)
    return report


def cmd_all(args, conn, repo) -> None:
    cfg, llm = _llm()
    for name in ("max_news_age", "max_macro_age"):
        if (getattr(args, name) or 0) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 0")
    news_old = _reusable(repo, "news", args.stock, args.max_news_age)
    macro_old = _reusable(repo, "macro", None, args.max_macro_age)
    with tracked_run(conn, "all", args.stock, cfg) as run_id:
        result = run_workflow(
            args.stock, llm,
            news_report=news_old.content_md if news_old else None,
            macro_report=macro_old.content_md if macro_old else None,
        )
        with conn:
            news_id = news_old.id if news_old else _save(
                repo, run_id, "news", args.stock, result["news_report"], result.get("news_meta"))
            macro_id = macro_old.id if macro_old else _save(
                repo, run_id, "macro", None, result["macro_report"], result.get("macro_meta"))
            _save(repo, run_id, "outlook", args.stock, result["outlook_report"], result.get("outlook_meta"),
                  news_report_id=news_id, macro_report_id=macro_id)
    _show(f"Stock news: {args.stock}", result["news_report"])
    _show("Macro environment", result["macro_report"])
    _show("Outlook", result["outlook_report"])


def cmd_news(args, conn, repo) -> None:
    cfg, llm = _llm()
    with tracked_run(conn, "news", args.stock, cfg) as run_id:
        state = news_agent.news_agent_node(llm)({"stock": args.stock})
        with conn:
            _save(repo, run_id, "news", args.stock, state["news_report"], state.get("news_meta"))
    _show(f"Stock news: {args.stock}", state["news_report"])


def cmd_macro(args, conn, repo) -> None:
    cfg, llm = _llm()
    with tracked_run(conn, "macro", None, cfg) as run_id:
        state = macro_agent.macro_agent_node(llm)({})
        with conn:
            _save(repo, run_id, "macro", None, state["macro_report"], state.get("macro_meta"))
    _show("Macro environment", state["macro_report"])


def cmd_outlook(args, conn, repo) -> None:
    news = _pick(repo, args.news_id, "news", args.stock, "news")
    macro = _pick(repo, args.macro_id, "macro", None, "macro")
    cfg, llm = _llm()
    with tracked_run(conn, "outlook", args.stock, cfg) as run_id:
        state = outlook_agent.outlook_agent_node(llm)(
            {"stock": args.stock, "news_report": news.content_md, "macro_report": macro.content_md}
        )
        with conn:
            _save(repo, run_id, "outlook", args.stock, state["outlook_report"], state.get("outlook_meta"),
                  news_report_id=news.id, macro_report_id=macro.id)
    _show("Outlook", state["outlook_report"])


def cmd_reports(args, conn, repo) -> None:
    if args.reports_command == "list":
        reports = repo.list(agent=args.agent, stock=args.stock, limit=args.limit)
        if not reports:
            print("No reports stored yet.")
        for r in reports:
            tokens = f"{r.input_tokens or '-'}/{r.output_tokens or '-'}"
            flag = "  INCOMPLETE" if r.truncated else ""
            print(f"#{r.id:<5} {r.agent:<8} {(r.stock or '(market)'):<20} "
                  f"{r.created_at.astimezone():%Y-%m-%d %H:%M}  {len(r.content_md):>6} chars  tokens in/out {tokens}{flag}")
        return
    ids = [args.id] if args.reports_command == "show" else args.ids
    for report_id in ids:
        report = repo.get(report_id)
        if report is None:
            raise SystemExit(f"No report #{report_id}.")
        if args.reports_command == "show":
            print(render_markdown(report))
        else:
            print(f"Exported report #{report_id} to {export_report(report, args.dir)}")


def cmd_import(args, conn, repo) -> None:
    if not Path(args.directory).is_dir():
        raise SystemExit(f"{args.directory} is not a folder.")
    with conn:
        imported, skipped = import_reports(repo, args.directory)
    print(f"Imported {imported} report(s); skipped {skipped} (already stored or not a report file).")


HANDLERS = {"all": cmd_all, "news": cmd_news, "macro": cmd_macro, "outlook": cmd_outlook,
            "reports": cmd_reports, "import": cmd_import}


def build_parser() -> argparse.ArgumentParser:
    # Shared options, accepted before or after the command; SUPPRESS keeps one side from overriding the other.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="log the settings used and each agent's token usage / finish reason")
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help="SQLite database file (default: STOCK_ANALYST_DB or data/stock_analyst.db)")
    parser = argparse.ArgumentParser(
        prog="stock-analyst", description="Stock news, macro and outlook agents for Indian stocks",
        parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    stock_help = 'company name or symbol, e.g. "Infosys" or "TCS"'
    p = sub.add_parser("all", help="run the whole workflow", parents=[common])
    p.add_argument("stock", help=stock_help)
    p.add_argument("--max-news-age", type=float, metavar="HOURS",
                   help="reuse this stock's stored news report if newer than this, instead of re-running the news agent "
                        "(default: STOCK_ANALYST_NEWS_MAX_AGE_HOURS or 6; 0 = always re-run)")
    p.add_argument("--max-macro-age", type=float, metavar="HOURS",
                   help="same for the macro report (default: STOCK_ANALYST_MACRO_MAX_AGE_HOURS or 6; 0 = always re-run)")
    sub.add_parser("news", help="run only the news agent", parents=[common]).add_argument("stock", help=stock_help)
    sub.add_parser("macro", help="run only the macro agent", parents=[common])
    p = sub.add_parser("outlook", help="run only the outlook agent on stored news + macro reports", parents=[common])
    p.add_argument("stock", help=stock_help)
    p.add_argument("--news-id", type=int, help="news report to use (default: latest for this stock)")
    p.add_argument("--macro-id", type=int, help="macro report to use (default: latest)")

    reports = sub.add_parser("reports", help="browse and export stored reports", parents=[common])
    rsub = reports.add_subparsers(dest="reports_command", required=True)
    p = rsub.add_parser("list", help="list stored reports, newest first", parents=[common])
    p.add_argument("--agent", choices=["news", "macro", "outlook"])
    p.add_argument("--stock")
    p.add_argument("--limit", type=int, default=20)
    rsub.add_parser("show", help="print one report", parents=[common]).add_argument("id", type=int)
    p = rsub.add_parser("export", help="write reports to markdown files", parents=[common])
    p.add_argument("ids", type=int, nargs="+", metavar="id")
    p.add_argument("--dir", default=DEFAULT_EXPORT_DIR, help=f"target folder (default: {DEFAULT_EXPORT_DIR})")

    p = sub.add_parser("import", help="import markdown reports saved by older versions", parents=[common])
    p.add_argument("directory", help="folder with the old *.md reports, e.g. news_reports")
    return parser


def _first_positional(argv: list[str]) -> int | None:
    """Index of the first token that is neither an option nor an option's value."""
    i = 0
    while i < len(argv):
        if argv[i] in OPTIONS_WITH_VALUE:
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return i
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backwards compatible: `stock-analyst TCS` means `stock-analyst all TCS`.
    first = _first_positional(argv)
    if first is not None and argv[first] not in COMMANDS:
        argv.insert(first, "all")
    args = build_parser().parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    path = Path(args.db) if getattr(args, "db", None) else db_path()
    logging.getLogger(__name__).info("Database: %s", path)
    conn = connect(path)
    try:
        HANDLERS[args.command](args, conn, Repository(conn))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
