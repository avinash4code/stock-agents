import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, ago, api } from "../api.js";
import { useAsync } from "../hooks.js";

const FIELDS = [
  ["max_news_age", "news", "News"],
  ["max_macro_age", "macro", "Macro"],
  ["max_fundamentals_age", "fundamentals", "Fundamentals"],
];

/** "Run analysis" button with the per-agent report-reuse limits under an Advanced toggle. */
export default function RunPanel({ stock, latest }) {
  const navigate = useNavigate();
  const { data: config } = useAsync(() => api.config(), []);
  const [ages, setAges] = useState({});
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function start() {
    setBusy(true);
    setError(null);
    const body = { stock };
    for (const [key] of FIELDS) {
      if (ages[key] !== undefined && ages[key] !== "") body[key] = Number(ages[key]);
    }
    try {
      const { run_id } = await api.startRun(body);
      navigate(`/run/${run_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) navigate(`/run/${e.detail.run_id}`);  // already running: follow it
      else { setError(e.message); setBusy(false); }
    }
  }

  const canRun = config?.can_run;
  return (
    <div className="runpanel">
      <div className="runpanel-main">
        <button className="btn primary" onClick={start} disabled={busy || !canRun}>
          {busy ? "Starting…" : "Run analysis"}
        </button>
        <span className="muted small">
          {config && !canRun ? config.reason
            : latest ? `Last analysed ${ago(latest.created_at)}. Recent news, macro and fundamentals reports are reused.`
            : "Runs news, macro and fundamentals analyses, then the outlook. Takes a minute or two."}
        </span>
        <span className="spacer" />
        <button className="linklike" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
          {advanced ? "Hide options" : "Options"}
        </button>
      </div>
      {advanced && (
        <div className="runpanel-options">
          <p className="muted small">
            Reuse a stored report if it is younger than this many hours; <b>0</b> always re-runs that agent.
            {config && model(config)}
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

function model(config) {
  return config.can_run ? ` Model: ${config.provider} / ${config.model}.` : "";
}
