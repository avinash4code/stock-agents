# stock-agents

A single LangGraph agent that gathers recent news about an Indian stock (Google News RSS, no news API key) and summarises it with Claude.

    START -> news_agent -> macro_agent -> outlook_agent -> END

- `news_agent`: headlines for the stock (`get_stock_news`), reads the top articles (`read_article`), summarises.
- `macro_agent`: scans macro themes (wars, crude, repo rate, budget/fiscal policy, elections, bond yields, ...)
  with `get_macro_news`, reads the 3-4 most relevant articles, and gives a detailed analysis plus overall tone.

- `outlook_agent`: no tools; reads the two reports and gives a sector view and a company view for the short (<=3 months),
  medium (3-12 months) and long (>1 year) term, with stance, confidence, drivers, risks and stated assumptions.

Each agent runs its own LLM/tool loop with a private conversation and writes its report to the shared
workflow state (`news_report`, `macro_report`, `outlook_report`); the CLI saves them as separate markdown files in `news_reports/` (`<stock>_<ts>.md`, `macro_<ts>.md`, `<stock>_outlook_<ts>.md`).

Layout: `graph.py` wires agents into the workflow; each agent lives in `agents/<name>.py`
(exposing `build_<name>_agent(llm)`); `state.py` holds the shared state; `llm.py` the model factory.
To add an agent: create `agents/<name>.py`, then add it as a node and edges in `graph.py`.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    cp .env.example .env   # set LLM_PROVIDER (anthropic|deepseek) and the matching API key
    .venv/bin/stock-news "Infosys"   # prints both reports; saves news_reports/<stock>_<ts>.md and macro_<ts>.md

## Tests
    .venv/bin/pytest                # unit + integration (offline, LLM and network faked)
    .venv/bin/pytest -m live        # real network + Anthropic API
