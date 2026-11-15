import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, ago, api } from "../api.js";
import { useAsync } from "../hooks.js";

const FIELDS = [
  ["max_news_age", "news", "News"],
  ["max_macro_age", "macro", "Macro"],
  ["max_fundamentals_age", "fundamentals", "Fundamentals"],
];

/** "14 Nov" from an ISO timestamp, in the viewer's time zone. */
export function shortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Starts a run for `stock` (with optional reuse limits) and opens its progress page. */
export function useStartRun(stock) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function start(limits = {}) {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.startRun({ stock, ...limits });
      navigate(`/run/${run_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) navigate(`/run/${e.detail.run_id}`);  // already running: follow it
      else { setError(e.message); setBusy(false); }
    }
  }
  return { start, busy, error };
}

/**
 * The one place to (re-)run the analysis: the report's age, a "Refresh analysis" button and, under Options,
 * how recent a stored news / macro / fundamentals report may be to be reused. `children` go at the end of the row.
 */
export default function RefreshControl({ stock, asOf, children }) {
  const { data: config } = useAsync(() => api.config(), []);
  const [ages, setAges] = useState({});
  const [open, setOpen] = useState(false);
  const { start, busy, error } = useStartRun(stock);

  function run() {
    const limits = {};
    for (const [key] of FIELDS) {
      if (ages[key] !== undefined && ages[key] !== "") limits[key] = Number(ages[key]);
    }
    start(limits);
  }

  const canRun = config?.can_run;
  return (
    <div className="refresh">
      <div className="refresh-row">
        <span>{asOf ? `Report as of ${shortDate(asOf)} · ${ago(asOf)}` : "Not analysed yet"}</span>
        <button className="btn primary small" onClick={run} disabled={busy || !canRun}
                title="Re-runs the outlook, reusing recent news, macro and fundamentals reports (see Options)">
          {busy ? "Starting…" : asOf ? "Refresh analysis ↻" : "Run analysis"}
        </button>
        <button className="linklike" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide options" : "Options"}
        </button>
        <span className="spacer" />
        {children}
      </div>
      {config && !canRun && <p className="muted small">{config.reason}</p>}
      {open && (
        <div className="runpanel-options">
          <p className="muted small">
            Reuse a stored report if it is younger than this many hours; <b>0</b> always re-runs that agent.
            {canRun && ` Model: ${config.provider} / ${config.model}.`}
          </p>
          {FIELDS.map(([key, kind, label]) => (
            <label key={key}>
              {label} (hours)
              <input type="number" min="0" step="any" inputMode="decimal"
                     placeholder={config ? String(config.reuse_hours[kind]) : ""}
                     value={ages[key] ?? ""} onChange={(e) => setAges({ ...ages, [key]: e.target.value })} />
            </label>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
