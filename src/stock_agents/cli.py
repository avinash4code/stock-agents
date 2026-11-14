"""Command line: run the whole workflow or any single agent, and browse stored reports.

    stock-analyst all TCS                 # whole workflow (same as `stock-analyst TCS`)
    stock-analyst news TCS                # news agent only
    stock-analyst macro                   # macro agent only (market-wide, no stock needed)
    stock-analyst fundamentals TCS        # fundamentals agent only (quarterly results analysis)
    stock-analyst outlook TCS             # outlook agent only, on the latest stored news + macro (+ fundamentals) reports
    stock-analyst reports list            # browse stored reports; also `reports show ID`, `reports export ID`
    stock-analyst compare TCS HDFCBANK    # run stocks through different per-agent model setups, side by side
    stock-analyst serve                   # web UI at http://127.0.0.1:8000
    stock-analyst import news_reports/    # one-time import of reports saved as files by older versions
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from stock_agents.agents import fundamentals_agent, macro_agent, news_agent, outlook_agent
from stock_agents.analysis import run_all, save_report, tracked_run
from stock_agents.compare import (
    DEFAULT_SETUPS, describe_setup, parse_setup, run_comparison, write_comparison)
from stock_agents.config import LLMConfig, db_path
from stock_agents.graph import run_workflow
from stock_agents.llm import build_llm, model_for
from stock_agents.reports import DEFAULT_EXPORT_DIR, export_report, import_reports, render_markdown
from stock_agents.storage import Report, Repository, connect

COMMANDS = {"all", "news", "macro", "fundamentals", "outlook", "reports", "import", "serve", "compare"}
OPTIONS_WITH_VALUE = {"--db"}


def _llm():
    cfg = LLMConfig.from_env()
    return cfg, build_llm(cfg)


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _save(repo: Repository, run_id: int, agent: str, stock: str | None, text: str, meta: dict | None, **links) -> int:
    return save_report(repo, run_id, agent, stock, text, meta, _err, **links)


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


def cmd_all(args, conn, repo) -> None:
    cfg, llm = _llm()
    for name in ("max_news_age", "max_macro_age", "max_fundamentals_age"):
        if (getattr(args, name) or 0) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 0")
    result = run_all(
        args.stock, conn, repo, cfg, llm, max_news_age=args.max_news_age, max_macro_age=args.max_macro_age,
        max_fundamentals_age=args.max_fundamentals_age, notify=_err, workflow=run_workflow)
    _show(f"Stock news: {args.stock}", result["news_report"])
    _show("Macro environment", result["macro_report"])
    if result.get("fundamentals_report"):
        _show(f"Fundamentals: {args.stock}", result["fundamentals_report"])
    _show("Outlook", result["outlook_report"])


def cmd_news(args, conn, repo) -> None:
    cfg, llm = _llm()
    with tracked_run(conn, "news", args.stock, cfg) as run_id:
        state = news_agent.news_agent_node(model_for(llm, "news"))({"stock": args.stock})
        with conn:
            _save(repo, run_id, "news", args.stock, state["news_report"], state.get("news_meta"))
    _show(f"Stock news: {args.stock}", state["news_report"])


def cmd_macro(args, conn, repo) -> None:
    cfg, llm = _llm()
    with tracked_run(conn, "macro", None, cfg) as run_id:
        state = macro_agent.macro_agent_node(model_for(llm, "macro"))({})
        with conn:
            _save(repo, run_id, "macro", None, state["macro_report"], state.get("macro_meta"))
    _show("Macro environment", state["macro_report"])


def cmd_fundamentals(args, conn, repo) -> None:
    cfg, llm = _llm()
    with tracked_run(conn, "fundamentals", args.stock, cfg) as run_id:
        state = fundamentals_agent.fundamentals_agent_node(model_for(llm, "fundamentals"))({"stock": args.stock})
        if "fundamentals_report" not in state:
            raise SystemExit(f"Cannot run fundamentals: {state['fundamentals_error']}.")
        with conn:
            _save(repo, run_id, "fundamentals", args.stock, state["fundamentals_report"], state.get("fundamentals_meta"))
    _show(f"Fundamentals: {args.stock}", state["fundamentals_report"])


def _pick_fundamentals(repo: Repository, report_id: int | None, stock: str) -> Report | None:
    """Fundamentals are optional for the outlook: an explicit --fundamentals-id must exist, the default may be absent."""
    if report_id:
        return _pick(repo, report_id, "fundamentals", stock, "fundamentals")
    report = repo.latest("fundamentals", stock)
    if report is None:
        print(f"No stored fundamentals report for {stock}; the outlook will run without it "
              f"(run `stock-analyst fundamentals {stock}` first to include it).", file=sys.stderr)
        return None
    print(f"Using fundamentals report #{report.id} from {report.created_at.astimezone():%Y-%m-%d %H:%M}", file=sys.stderr)
    return report


def cmd_outlook(args, conn, repo) -> None:
    news = _pick(repo, args.news_id, "news", args.stock, "news")
    macro = _pick(repo, args.macro_id, "macro", None, "macro")
    fund = _pick_fundamentals(repo, args.fundamentals_id, args.stock)
    cfg, llm = _llm()
    with tracked_run(conn, "outlook", args.stock, cfg) as run_id:
        state = outlook_agent.outlook_agent_node(model_for(llm, "outlook"))(
            {"stock": args.stock, "news_report": news.content_md, "macro_report": macro.content_md,
             "fundamentals_report": fund.content_md if fund else ""}
        )
        with conn:
            _save(repo, run_id, "outlook", args.stock, state["outlook_report"], state.get("outlook_meta"),
                  news_report_id=news.id, macro_report_id=macro.id,
                  fundamentals_report_id=fund.id if fund else None)
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


def cmd_compare(args, conn, repo) -> None:
    base = LLMConfig.from_env()
    if args.setup and len({label for label, _ in args.setup}) != len(args.setup):
        raise SystemExit("Cannot compare: each --setup needs a different label.")
    specs = dict(args.setup) if args.setup else DEFAULT_SETUPS
    try:
        setups = {label: parse_setup(spec, base) for label, spec in specs.items()}
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Cannot compare: {exc}") from None
    _err(f"Comparing {len(setups)} setup(s) on {len(args.stocks)} stock(s): this runs every agent for each, "
         "and spends LLM tokens.")
    comparison = run_comparison(args.stocks, setups, notify=_err)
    out = write_comparison(comparison, {k: describe_setup(c) for k, c in setups.items()}, args.dir)
    print(f"Wrote the comparison to {out}  (open the .html files; summary.md has token totals)")


def cmd_serve(args, conn, repo) -> None:
    try:
        import uvicorn

        from stock_agents.web.app import create_app
    except ImportError:
        raise SystemExit("The web UI needs extra packages: pip install -e '.[web]'") from None
    path = Path(args.db) if getattr(args, "db", None) else db_path()
    conn.close()
    print(f"Serving on http://{args.host}:{args.port}  (database: {path})", file=sys.stderr)
    uvicorn.run(create_app(path), host=args.host, port=args.port, log_level="info")


HANDLERS = {"compare": cmd_compare, "all": cmd_all, "news": cmd_news, "macro": cmd_macro, "fundamentals": cmd_fundamentals, "outlook": cmd_outlook,
            "reports": cmd_reports, "import": cmd_import, "serve": cmd_serve}


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
    p.add_argument("--max-fundamentals-age", type=float, metavar="HOURS",
                   help="same for the fundamentals report "
                        "(default: STOCK_ANALYST_FUNDAMENTALS_MAX_AGE_HOURS or 168, one week; 0 = always re-run)")
    sub.add_parser("news", help="run only the news agent", parents=[common]).add_argument("stock", help=stock_help)
    sub.add_parser("macro", help="run only the macro agent", parents=[common])
    sub.add_parser("fundamentals", help="run only the fundamentals agent (quarterly results)",
                   parents=[common]).add_argument("stock", help=stock_help)
    p = sub.add_parser("outlook", help="run only the outlook agent on stored news + macro (+ fundamentals) reports",
                       parents=[common])
    p.add_argument("stock", help=stock_help)
    p.add_argument("--news-id", type=int, help="news report to use (default: latest for this stock)")
    p.add_argument("--macro-id", type=int, help="macro report to use (default: latest)")
    p.add_argument("--fundamentals-id", type=int,
                   help="fundamentals report to use (default: latest for this stock, if any)")

    reports = sub.add_parser("reports", help="browse and export stored reports", parents=[common])
    rsub = reports.add_subparsers(dest="reports_command", required=True)
    p = rsub.add_parser("list", help="list stored reports, newest first", parents=[common])
    p.add_argument("--agent", choices=["news", "macro", "fundamentals", "outlook"])
    p.add_argument("--stock")
    p.add_argument("--limit", type=int, default=20)
    rsub.add_parser("show", help="print one report", parents=[common]).add_argument("id", type=int)
    p = rsub.add_parser("export", help="write reports to markdown files", parents=[common])
    p.add_argument("ids", type=int, nargs="+", metavar="id")
    p.add_argument("--dir", default=DEFAULT_EXPORT_DIR, help=f"target folder (default: {DEFAULT_EXPORT_DIR})")

    p = sub.add_parser("import", help="import markdown reports saved by older versions", parents=[common])
    p.add_argument("directory", help="folder with the old *.md reports, e.g. news_reports")

    p = sub.add_parser("compare", help="run stocks through different per-agent model setups and write the reports "
                       "side by side (nothing is stored in the database)", parents=[common])
    p.add_argument("stocks", nargs="+", metavar="stock", help=stock_help)
    p.add_argument("--setup", nargs=2, action="append", metavar=("LABEL", "SPEC"),
                   help='a setup to try, e.g. --setup mix "news=deepseek:deepseek-flash@0,outlook=anthropic:claude-sonnet-5-5" '
                        "(agents: news, macro, fundamentals, outlook, or all; repeat for more setups; "
                        "default: flash+sonnet and flash+pro)")
    p.add_argument("--dir", default="comparisons", help="folder for the output (default: comparisons)")

    p = sub.add_parser("serve", help="start the web UI", parents=[common])
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default: 127.0.0.1; the UI has no login, keep it local)")
    p.add_argument("--port", type=int, default=8000)
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
