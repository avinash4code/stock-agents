import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AGENT_LABELS, api } from "../api.js";

const STAGES = ["news", "macro", "fundamentals", "outlook"];
const STATE_TEXT = { waiting: "Waiting", running: "Running…", reused: "Reused stored report", done: "Done", failed: "Failed" };
const ICON = { waiting: "○", running: "◐", reused: "↺", done: "✓", failed: "✕" };

function elapsed(from, to = Date.now()) {
  const s = Math.max(0, Math.round((to - new Date(from).getTime()) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

/** Live progress of one run: polls until it finishes, then offers (and follows) the link to its reports. */
export default function RunPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let live = true;
    let timer;
    async function poll() {
      try {
        const data = await api.run(id);
        if (!live) return;
        setRun(data);
        setError(null);
        if (data.status === "running") timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!live) return;
        setError(e.message);
        timer = setTimeout(poll, 4000);  // the server may be restarting: keep trying
      }
    }
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [id]);

  useEffect(() => {
    if (run?.status !== "running") return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [run?.status]);

  useEffect(() => {
    if (run?.status !== "ok" || !run.stock) return;
    const timer = setTimeout(() => navigate(`/stock/${encodeURIComponent(run.symbol || run.stock)}`), 1200);
    return () => clearTimeout(timer);
  }, [run?.status, run?.stock, run?.symbol, navigate]);

  if (error && !run) return <p className="error">{error}</p>;
  if (!run) return <p className="muted">Loading…</p>;

  const stages = run.stages || stagesFromReports(run);
  return (
    <section>
      <div className="section-head">
        <h1>{run.stock || "Market"} <span className={`badge ${run.status}`}>{run.status}</span></h1>
        <span className="muted">
          {run.status === "running" ? `Elapsed ${elapsed(run.started_at, now)}` : run.finished_at && `Took ${elapsed(run.started_at, new Date(run.finished_at).getTime())}`}
        </span>
      </div>
      <ol className="stages">
        {STAGES.map((s) => (
          <li key={s} className={`stage ${stages[s]}`}>
            <span className="icon" aria-hidden>{ICON[stages[s]]}</span>
            <strong>{AGENT_LABELS[s]}</strong>
            <span className="muted">{STATE_TEXT[stages[s]]}</span>
          </li>
        ))}
      </ol>
      {error && <p className="error">Lost contact with the server ({error}); retrying…</p>}
      {run.status === "failed" && <p className="error">{run.error}</p>}
      {run.status === "ok" && <p><Link className="btn primary" to={`/stock/${encodeURIComponent(run.symbol || run.stock)}`}>View reports</Link></p>}
      {run.status === "failed" && run.stock && <p><Link to={`/stock/${encodeURIComponent(run.symbol || run.stock)}`}>Back to {run.stock}</Link></p>}
      {run.status === "running" && <p className="muted small">You can leave this page: the analysis keeps running and appears in History.</p>}
    </section>
  );
}

// A finished run no longer has live stages; derive them from what it stored.
function stagesFromReports(run) {
  const own = new Set(run.reports.map((r) => r.agent));
  const reused = new Set(run.reused.map((r) => r.agent));
  const fail = run.status === "failed";
  return Object.fromEntries(STAGES.map((s) => [
    s, own.has(s) ? "done" : reused.has(s) ? "reused" : fail ? "failed" : s === "fundamentals" && run.status === "ok" ? "failed" : "waiting",
  ]));
}
