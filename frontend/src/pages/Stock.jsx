import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AGENT_LABELS, api } from "../api.js";
import { useAsync } from "../hooks.js";
import ReportView from "../components/ReportView.jsx";
import RunPanel from "../components/RunPanel.jsx";
import RunList from "../components/RunList.jsx";
import StockSummary from "../components/StockSummary.jsx";

// The most recent stock-specific report (the macro report is market-wide, not part of this company's history).
function newest(latest) {
  return ["outlook", "fundamentals", "news"].map((a) => latest[a]).filter(Boolean)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
}

const ORDER = ["outlook", "fundamentals", "news", "macro"];

function LatestReport({ id }) {
  const { data, error, loading } = useAsync(() => api.report(id), [id]);
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;
  return <ReportView report={data} />;
}

/** Scrolls to the first report heading whose text matches `pattern`, waiting for the report to finish loading. */
function useJumpTo(jump, ready, fallbackRef, clear) {
  useEffect(() => {
    if (!jump || !ready) return undefined;
    let tries = 0;
    let timer;
    const attempt = () => {
      if (!jump.pattern) {  // a plain tab link: just bring the tabs into view
        fallbackRef.current?.scrollIntoView({ behavior: "smooth" });
        clear();
        return;
      }
      const heading = [...document.querySelectorAll(".md h2, .md h3")]
        .find((h) => jump.pattern.test(h.textContent.trim()));
      if (heading) {
        heading.scrollIntoView({ behavior: "smooth", block: "start" });
        heading.classList.add("jump-flash");
        setTimeout(() => heading.classList.remove("jump-flash"), 1800);
        clear();
      } else if (++tries < 40) {
        timer = setTimeout(attempt, 100);  // the report is fetched when its tab opens
      } else {
        fallbackRef.current?.scrollIntoView({ behavior: "smooth" });  // an older report with other headings
        clear();
      }
    };
    attempt();
    return () => clearTimeout(timer);
  }, [jump, ready]);  // eslint-disable-line react-hooks/exhaustive-deps
}

export default function Stock() {
  const { symbol } = useParams();
  const [tab, setTab] = useState(null);
  const tabsRef = useRef(null);
  const [jump, setJump] = useState(null);
  const { data, error, loading } = useAsync(async () => {
    const [stockReports, macro, runs, overview] = await Promise.all([
      api.reports({ stock: symbol, limit: 100 }),
      api.reports({ agent: "macro", limit: 1 }),
      api.runs({ stock: symbol, limit: 10 }),
      api.overview(symbol).catch(() => null),  // the card is optional; the tabs still work without it
    ]);
    const latest = {};
    for (const r of [...stockReports, ...macro]) {
      if (!latest[r.agent] || r.created_at > latest[r.agent].created_at) latest[r.agent] = r;
    }
    return { latest, runs, overview };
  }, [symbol]);

  const tabs = data ? ORDER.filter((a) => data.latest[a]) : [];
  const current = tabs.includes(tab) ? tab : tabs[0];
  useJumpTo(jump, !!jump && !!data && jump.agent === current, tabsRef, () => setJump(null));  // hooks stay above the early returns

  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;

  return (
    <section>
      <div className="section-head">
        <h1>{symbol}</h1>
        <Link to="/">New search</Link>
      </div>
      {data.overview && (
        <StockSummary overview={data.overview} symbol={symbol} tabs={tabs}
                      onOpenTab={(agent, pattern) => { setTab(agent); setJump({ agent, pattern }); }} />
      )}
      <RunPanel stock={symbol} latest={newest(data.latest)} />
      {tabs.length === 0 ? (
        <p className="muted">No stored reports for {symbol} yet.</p>
      ) : (
        <>
          <div className="tabs" role="tablist" ref={tabsRef}>
            {tabs.map((a) => (
              <button key={a} role="tab" aria-selected={a === current} className={a === current ? "on" : ""} onClick={() => setTab(a)}>
                {AGENT_LABELS[a]}
              </button>
            ))}
          </div>
          <LatestReport id={data.latest[current].id} />
        </>
      )}
      {data.runs.length > 0 && (
        <>
          <div className="section-head"><h2>Past runs</h2><Link to={`/history?stock=${encodeURIComponent(symbol)}`}>See all</Link></div>
          <RunList runs={data.runs} />
        </>
      )}
    </section>
  );
}
