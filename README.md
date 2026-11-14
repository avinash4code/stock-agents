# stock-analyst

A LangGraph multi-agent system for analysing Indian stocks. Today it has four agents (company news, macro environment, fundamentals, and a short/medium/long-term outlook); more analysis agents can be added to the workflow. News comes from the Bing News RSS feed (no news API key); company financials are scraped from Screener.in (no key); the LLM is configurable (Anthropic or DeepSeek).

    START -> news_agent         -+
    START -> macro_agent        -+-> outlook_agent -> END   (news, macro and fundamentals run in parallel)
    START -> fundamentals_agent -+

- `news_agent`: headlines for the stock (`get_stock_news`), reads the top articles (`read_article`), summarises.
- `macro_agent`: scans macro themes (wars, crude, repo rate, budget/fiscal policy, elections, bond yields, ...)
  with `get_macro_news`, reads the 3-4 most relevant articles, and gives a detailed analysis plus overall tone.

- `fundamentals_agent`: no tools. One Screener.in page per company is fetched (`screener.py`; the consolidated view, else
  standalone) and parsed into: 13 quarters of profit & loss, the annual profit & loss with a TTM column, annual balance
  sheet, cash flow and efficiency ratios (ROCE, debtor days, ...), the quarterly shareholding pattern (promoters / FIIs /
  DIIs / public), Screener's peer comparison (the company and its industry peers on P/E, ROCE, dividend yield and
  latest-quarter growth, plus the wider-group median, with the company's rank computed in code), key ratios and
  Screener's own pros/cons. All growth, margin, leverage, cash-conversion and
  shareholding-change maths is done in code (`metrics.py`); the model only interprets the tables (growth, profitability,
  balance sheet and cash flow, shareholding, red flags, what to watch). The stored report is the data tables followed by
  the analysis, so you can check every number. If Screener cannot be reached the workflow carries on without it.
  Names are resolved to symbols locally from NSE's official list of listed equities (`symbols.py`, cached as
  `nse_equities.csv` next to the database, refreshed monthly), because Screener's search endpoint is disallowed for
  automated clients by its robots.txt. Banks and NBFCs have different rows and are flagged; balance sheet, cash flow and
  ratios are annual only (Screener does not publish them quarterly). Scraping is for personal use: two requests per
  company (the page, then the peer table that the page itself loads with JavaScript from `/api/company/<id>/peers/`; if
  that one fails the report simply has no peer section and it is not retried), an identifying User-Agent, no login. Check Screener's terms before relying on it.

- `outlook_agent`: no tools; reads the three reports and gives a sector view and a company view for the short (<=3 months),
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
    stock-analyst fundamentals Infosys   # fundamentals agent only (quarterly results analysis)
    stock-analyst outlook Infosys  # outlook agent only; uses the latest stored news + macro (+ fundamentals) reports
    stock-analyst outlook Infosys --news-id 12 --macro-id 11 --fundamentals-id 13   # or pick the reports explicitly
    stock-analyst all Infosys --max-news-age 1 --max-macro-age 24 --max-fundamentals-age 0   # per-run override of the reuse limits (hours)
    stock-analyst all Infosys --max-news-age 0 --max-macro-age 0 --max-fundamentals-age 0    # 0 = always re-run that agent

`all` reuses stored reports instead of re-running an agent when they are recent enough: the stock's news report and
the macro report if each is younger than 6 hours (complete reports only; the outlook agent always runs, on the
reused reports, and records which ones it used). Set the defaults with `STOCK_ANALYST_NEWS_MAX_AGE_HOURS` and
`STOCK_ANALYST_MACRO_MAX_AGE_HOURS` in `.env`; the command-line flags win. The stock's fundamentals report is reused
for a week by default (quarterly data changes rarely; `STOCK_ANALYST_FUNDAMENTALS_MAX_AGE_HOURS`). The standalone `news`, `macro` and `fundamentals`
commands always run their agent.

## Web UI
    pip install -e '.[web]'
    (cd frontend && npm install && npm run build)   # builds the React app into src/stock_agents/web/static
    stock-analyst serve                              # http://127.0.0.1:8000

Search a company by name, NSE symbol or BSE code (typeahead from NSE's list; a BSE code is accepted as typed), read its
stored reports rendered from markdown (contents sidebar, scrolling tables, download, print), and browse historical runs
with the reports each one created or reused. **Run analysis** on a stock's page starts the whole workflow in the
background (the same code as `stock-analyst all`, with the same report-reuse limits, overridable under Options) and
shows live progress per agent; runs keep going if you leave the page. It needs the LLM API key in `.env` like the CLI,
and one analysis per stock runs at a time. There is no login, so it listens on 127.0.0.1 by default and only accepts
same-origin JSON requests to start runs. For frontend development run `stock-analyst serve` and `npm run dev` in
`frontend/` (Vite proxies `/api`). API docs are at `/api/docs`.

Add `-v` to log the settings used and each agent's token usage / finish reason.
Use `news` and `macro` first, then `outlook`, to iterate on the outlook prompt without re-running the other agents.

## Stored reports
Every run and report is stored in a local SQLite database (default `data/stock_analyst.db`, next to your `.env`;
change it with `STOCK_ANALYST_DB` in `.env` or `--db PATH`). Each report keeps its stock, agent, timestamp, token
usage, whether it was cut off by the token limit, and (for outlooks) which news and macro reports it used.
Reports cut off by the token limit are skipped when the outlook agent picks its inputs.

    stock-analyst reports list [--agent news|macro|fundamentals|outlook] [--stock TCS] [--limit 20]
    stock-analyst reports show 22
    stock-analyst reports export 22 15 11 --dir exports     # write markdown files
    stock-analyst import news_reports                       # one-time import of reports saved as files by older versions

The schema is versioned (`PRAGMA user_version`) and upgraded automatically; add a new numbered entry to `MIGRATIONS`
in `storage/db.py` for any change, never edit an existing one.

Settings come from `.env`; a real environment variable (e.g. `export LLM_MAX_TOKENS=...`) overrides it, and the
defaults in `config.py` apply only when neither sets a value. Reasoning models spend `LLM_MAX_TOKENS` on hidden
thinking too, so keep it generous (16000 works for deepseek-flash).

### A different model per agent
By default every agent uses the `LLM_*` model. To give one agent its own, set `LLM_<AGENT>_PROVIDER`,
`LLM_<AGENT>_MODEL`, `LLM_<AGENT>_TEMPERATURE`, `LLM_<AGENT>_MAX_TOKENS` and/or `LLM_<AGENT>_BASE_URL` (`<AGENT>` = `NEWS`, `MACRO`,
`FUNDAMENTALS` or `OUTLOOK`); whatever you leave out is inherited. Switching an agent to another provider uses
that provider's API key and default model and does not inherit `LLM_BASE_URL`. For example, cheap tool-using
agents and a stronger writer:

    LLM_PROVIDER=deepseek
    LLM_MODEL=deepseek-flash
    LLM_FUNDAMENTALS_MODEL=deepseek-v4-pro
    LLM_OUTLOOK_PROVIDER=anthropic       # needs ANTHROPIC_API_KEY
    LLM_OUTLOOK_MODEL=claude-sonnet-5-5
    LLM_TEMPERATURE=0.2                  # default for every agent (DeepSeek only)
    LLM_OUTLOOK_TEMPERATURE=0.3          # per agent

A run with mixed models is recorded with provider `mixed` and the model of each agent. Anthropic requests do not
send a temperature (Sonnet 5.5 and newer reject non-default values), so the temperature settings (global and per agent) only affect DeepSeek.

### Comparing model setups
    stock-analyst compare TCS HDFCBANK Infosys     # flash+sonnet vs flash+pro on the same stocks
    stock-analyst compare TCS --setup cheap "all=deepseek:deepseek-flash" \
                              --setup mix "news=deepseek:deepseek-flash,macro=deepseek:deepseek-flash,outlook=anthropic:claude-sonnet-5-5"

Each stock is run fresh through each setup (a setup lists `agent=provider:model@temperature`, or `all=`; either part may be left out, e.g. `outlook=@0.3`; agents it does not
name use the `.env` defaults). Output goes to `comparisons/<timestamp>/`: `summary.md` (token totals, failures,
truncated replies) and, per stock, an `.html` page with the setups in columns for each agent plus a `.md` copy.
Nothing is stored in the database. This spends real tokens (every agent, every stock, every setup), so start
with a few stocks, including a bank and a standalone-only company.

## Tests
    .venv/bin/pytest                # unit + integration (offline, LLM and network faked)
    .venv/bin/pytest -m live        # real network + Anthropic API
