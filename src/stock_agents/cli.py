from __future__ import annotations

import argparse
import sys

from stock_agents.graph import run_workflow
from stock_agents.reports import DEFAULT_DIR, save_macro_report, save_outlook_report, save_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock news and macro briefing for an Indian stock")
    parser.add_argument("stock", help='Company name or symbol, e.g. "Infosys" or "TCS"')
    parser.add_argument("--output-dir", default=DEFAULT_DIR, help=f"report folder (default: {DEFAULT_DIR})")
    args = parser.parse_args(argv)

    result = run_workflow(args.stock)
    paths = [
        save_report(args.stock, result["news_report"], args.output_dir),
        save_macro_report(result["macro_report"], args.output_dir),
        save_outlook_report(args.stock, result["outlook_report"], args.output_dir),
    ]
    print(f"=== Stock news: {args.stock} ===\n{result['news_report']}\n")
    print(f"=== Macro environment ===\n{result['macro_report']}\n")
    print(f"=== Outlook ===\n{result['outlook_report']}\n")
    for path in paths:
        print(f"Saved to {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
