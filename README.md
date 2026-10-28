# stock-analyst

A LangGraph multi-agent system for analysing Indian stocks. Today it has three agents (company news, macro environment, and a short/medium/long-term outlook); more analysis agents can be added to the workflow. News comes from the Bing News RSS feed (no news API key); the LLM is configurable (Anthropic or DeepSeek).

    START -> news_agent  -+
    START -> macro_agent -+-> outlook_agent -> END   (news and macro run in parallel)

- `news_agent`: headlines for the stock (`get_stock_news`), reads the top articles (`read_article`), summarises.
- `macro_agent`: scans macro themes (wars, crude, repo rate, budget/fiscal policy, elections, bond yields, ...)
  with `get_macro_news`, reads the 3-4 most relevant articles, and gives a detailed analysis plus overall tone.

- `outlook_agent`: no tools; reads the two reports and gives a sector view and a company view for the short (<=3 months),
  medium (3-12 months) and long (>1 year) term, with stance, confidence, drivers, risks and stated assumptions.

Each agent runs its own LLM/tool loop with a private conversation and writes its report to the shared
workflow state (`news_report`, `macro_report`, `outlook_report`); the CLI saves them as separate markdown files in `news_reports/` (`<stock>_<ts>.md`, `macro_<ts>.md`, `<stock>_outlook_<ts>.md`).

Layout: `storage/` holds the SQLite layer; `graph.py` wires agents into the workflow; each agent lives in `agents/<name>.py`
(exposing `build_<name>_agent(llm)`); `state.py` holds the shared state; `llm.py` the model factory.
To add an agent: create `agents/<name>.py`, then add it as a node and edges in `graph.py`.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    cp .env.example .env   # set LLM_PROVIDER (anthropic|deepseek) and the matching API key

## Running
    stock-analyst all Infosys      # whole workflow (`stock-analyst Infosys` does the same)
    stock-analyst news Infosys     # news agent only
    stock-analyst macro            # macro agent only (market-wide, no stock needed)
    stock-analyst outlook Infosys  # outlook agent only; uses the latest stored news + macro reports
    stock-analyst outlook Infosys --news-id 12 --macro-id 11   # or pick the reports explicitly
    stock-analyst all Infosys --max-news-age 1 --max-macro-age 24   # per-run override of the reuse limits (hours)
    stock-analyst all Infosys --max-news-age 0 --max-macro-age 0    # 0 = always re-run that agent

`all` reuses stored reports instead of re-running an agent when they are recent enough: the stock's news report and
the macro report if each is younger than 6 hours (complete reports only; the outlook agent always runs, on the
reused reports, and records which ones it used). Set the defaults with `STOCK_ANALYST_NEWS_MAX_AGE_HOURS` and
`STOCK_ANALYST_MACRO_MAX_AGE_HOURS` in `.env`; the command-line flags win. The standalone `news` and `macro`
commands always run their agent.

Add `-v` to log the settings used and each agent's token usage / finish reason.
Use `news` and `macro` first, then `outlook`, to iterate on the outlook prompt without re-running the other agents.

## Stored reports
Every run and report is stored in a local SQLite database (default `data/stock_analyst.db`, next to your `.env`;
change it with `STOCK_ANALYST_DB` in `.env` or `--db PATH`). Each report keeps its stock, agent, timestamp, token
usage, whether it was cut off by the token limit, and (for outlooks) which news and macro reports it used.
Reports cut off by the token limit are skipped when the outlook agent picks its inputs.

    stock-analyst reports list [--agent news|macro|outlook] [--stock TCS] [--limit 20]
    stock-analyst reports show 22
    stock-analyst reports export 22 15 11 --dir exports     # write markdown files
    stock-analyst import news_reports                       # one-time import of reports saved as files by older versions

The schema is versioned (`PRAGMA user_version`) and upgraded automatically; add a new numbered entry to `MIGRATIONS`
in `storage/db.py` for any change, never edit an existing one.

Settings come from `.env`; a real environment variable (e.g. `export LLM_MAX_TOKENS=...`) overrides it, and the
defaults in `config.py` apply only when neither sets a value. Reasoning models spend `LLM_MAX_TOKENS` on hidden
thinking too, so keep it generous (16000 works for deepseek-flash).

## Tests
    .venv/bin/pytest                # unit + integration (offline, LLM and network faked)
    .venv/bin/pytest -m live        # real network + Anthropic API
