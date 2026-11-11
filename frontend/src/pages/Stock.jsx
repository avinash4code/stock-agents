import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AGENT_LABELS, api } from "../api.js";
import { useAsync } from "../hooks.js";
import ReportView from "../components/ReportView.jsx";
import RunList from "../components/RunList.jsx";

const ORDER = ["outlook", "fundamentals", "news", "macro"];

function LatestReport({ id }) {
  const { data, error, loading } = useAsync(() => api.report(id), [id]);
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;
  return <ReportView report={data} />;
}

export default function Stock() {
  const { symbol } = useParams();
  const [tab, setTab] = useState(null);
  const { data, error, loading } = useAsync(async () => {
    const [stockReports, macro, runs] = await Promise.all([
      api.reports({ stock: symbol, limit: 100 }),
      api.reports({ agent: "macro", limit: 1 }),
      api.runs({ stock: symbol, limit: 10 }),
    ]);
    const latest = {};
    for (const r of [...stockReports, ...macro]) {
      if (!latest[r.agent] || r.created_at > latest[r.agent].created_at) latest[r.agent] = r;
    }
    return { latest, runs };
  }, [symbol]);

  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;
  const tabs = ORDER.filter((a) => data.latest[a]);
  const current = tabs.includes(tab) ? tab : tabs[0];

  return (
    <section>
      <div className="section-head">
        <h1>{symbol}</h1>
        <Link to="/">New search</Link>
      </div>
      {tabs.length === 0 ? (
        <p className="muted">No stored reports for {symbol} yet. Run <code>stock-analyst all {symbol}</code>, then reload.</p>
      ) : (
        <>
          <div className="tabs" role="tablist">
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
