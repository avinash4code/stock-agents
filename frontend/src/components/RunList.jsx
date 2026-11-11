import { Link } from "react-router-dom";
import { AGENT_LABELS, ago, formatWhen } from "../api.js";

const ORDER = ["outlook", "fundamentals", "news", "macro"];

function duration(run) {
  if (!run.finished_at) return null;
  const s = Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

function ReportLinks({ run }) {
  const items = [
    ...run.reports.map((r) => ({ ...r, reused: false })),
    ...run.reused.map((r) => ({ ...r, reused: true })),
  ].sort((a, b) => ORDER.indexOf(a.agent) - ORDER.indexOf(b.agent));
  if (!items.length) return <span className="muted">no reports</span>;
  return items.map((r) => (
    <Link key={r.id} to={`/report/${r.id}`} className={`chip ${r.reused ? "reused" : ""}`}
          title={r.reused ? `Reused from ${formatWhen(r.created_at)}` : `Created by this run`}>
      {AGENT_LABELS[r.agent]}{r.reused ? " ↺" : ""}{r.truncated ? " ⚠" : ""}
    </Link>
  ));
}

export default function RunList({ runs, empty = "Nothing here yet." }) {
  if (!runs.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table className="runs">
        <thead><tr><th>When</th><th>Stock</th><th>Command</th><th>Status</th><th>Reports</th><th>Model</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td title={formatWhen(run.started_at)}>{ago(run.started_at)}<div className="muted small">{duration(run)}</div></td>
              <td>{run.stock ? <Link to={`/stock/${encodeURIComponent(run.stock)}`}>{run.stock}</Link> : <span className="muted">market</span>}</td>
              <td>{run.command}</td>
              <td>
                <span className={`badge ${run.status}`}>{run.status}</span>
                {run.error && <div className="small error" title={run.error}>{run.error.slice(0, 80)}</div>}
              </td>
              <td className="chips"><ReportLinks run={run} /></td>
              <td className="muted small">{run.model || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
