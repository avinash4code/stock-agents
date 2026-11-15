import { useState } from "react";
import { useStartRun } from "./RunPanel.jsx";
import { AGENT_LABELS } from "../api.js";

const inr = (n, digits = 0) => n.toLocaleString("en-IN", { maximumFractionDigits: digits });
const STANCES = { positive: "Positive", neutral: "Neutral", negative: "Negative" };
const HORIZONS = [["short", "Short"], ["medium", "Medium"], ["long", "Long"]];

/** "14 Nov" from an ISO timestamp, in the viewer's time zone. */
function shortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function StanceBadge({ stance, confidence }) {
  if (!STANCES[stance]) return <span className="muted">n/a</span>;
  return (
    <span className={`stance ${stance}`} title={confidence ? `Confidence: ${confidence}` : undefined}>
      <i aria-hidden="true" />{STANCES[stance]}
    </span>
  );
}

export function Dots({ rating }) {
  if (rating == null) return <span className="dots none" aria-label="not rated">–</span>;
  return (
    <span className="dots" role="img" aria-label={`${rating} out of 5`}>
      {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= rating ? "on" : ""} />)}
    </span>
  );
}

function Header({ snapshot, symbol }) {
  const s = snapshot;
  const industry = s.sector?.length ? s.sector[s.sector.length - 1] : null;
  const change = s.change_pct;
  const stats = [
    s.market_cap_cr != null && `Mkt cap ₹${inr(s.market_cap_cr)} Cr`,
    s.pe != null && `P/E ${s.pe}${s.peer_median_pe != null ? ` (peer median ${inr(s.peer_median_pe, 1)})` : ""}`,
    s.low_52w != null && s.high_52w != null && `52w ₹${inr(s.low_52w)}–${inr(s.high_52w)}`,
  ].filter(Boolean);
  return (
    <div className="ss-head">
      <div>
        <div className="ss-name">
          <strong>{s.name}</strong>
          <span className="muted"> · NSE: {symbol}{industry ? ` · ${industry}` : ""}</span>
        </div>
        <div className="muted small">{stats.join(" · ")}</div>
      </div>
      {s.price != null && (
        <div className="ss-price">
          <span>₹{inr(s.price, s.price < 100 ? 2 : 0)}</span>
          {change != null && (
            <span className={`chg ${change < 0 ? "down" : "up"}`}>{change < 0 ? "▼" : "▲"}{Math.abs(change).toFixed(2).replace(/\.?0+$/, "")}%</span>
          )}
          {s.price_as_of && <div className="muted small">{s.price_as_of}</div>}
        </div>
      )}
    </div>
  );
}

function Overall({ company, headline }) {
  return (
    <div className="ss-overall">
      <div className="ss-horizons">
        <span className="ss-label">Overall</span>
        {HORIZONS.map(([key, label]) => (
          <span key={key}>{label} <StanceBadge {...company[key]} /></span>
        ))}
      </div>
      {headline && <p className="ss-quote">“{headline}”</p>}
    </div>
  );
}

function Pillars({ pillars }) {
  return (
    <ul className="ss-pillars">
      {pillars.map((p) => (
        <li key={p.key}>
          <span className="ss-pname">{p.label || p.key}</span>
          <Dots rating={p.rating} />
          <span className="ss-evidence">{p.evidence}</span>
        </li>
      ))}
    </ul>
  );
}

function Lists({ red, strengths, watch }) {
  const groups = [
    ["⚠", "Top red flags", red, "flags"],
    ["✓", "Top strengths", strengths, "strengths"],
    ["◉", "Watch next quarter", watch, "watch"],
  ].filter(([, , items]) => items?.length);
  if (!groups.length) return null;
  return (
    <div className="ss-lists">
      {groups.map(([icon, title, items, kind]) => (
        <details key={kind} className={kind}>
          <summary><span aria-hidden="true">{icon}</span> {title}{kind !== "watch" ? ` (${items.length})` : ""}</summary>
          <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </details>
      ))}
    </div>
  );
}

/**
 * The card at the top of a stock's page, drawn from the agents' structured blocks (no prose parsing).
 * Every part is optional: reports written before the blocks existed simply show less.
 */
export default function StockSummary({ overview, symbol, tabs, onOpenTab }) {
  const { start, busy, error } = useStartRun(symbol);
  const [confirm, setConfirm] = useState(false);
  const snapshot = overview.snapshot;
  const outlook = overview.outlook?.structured;
  const fund = overview.fundamentals?.structured;
  if (!snapshot && !outlook?.company && !fund?.pillars) return null;

  const headline = outlook?.headline || fund?.headline;
  return (
    <section className="summary" aria-label="Summary">
      {snapshot && <Header snapshot={snapshot} symbol={symbol} />}
      {outlook?.company && <Overall company={outlook.company} headline={headline} />}
      {!outlook?.company && headline && <p className="ss-quote">“{headline}”</p>}
      {fund?.pillars?.length > 0 && <Pillars pillars={fund.pillars} />}
      <Lists red={fund?.red_flags} strengths={fund?.strengths} watch={fund?.watch_next_quarter} />
      {tabs.length > 0 && (
        <div className="ss-links">
          <span className="ss-label">Detailed analysis ▸</span>
          {tabs.map((a, i) => (
            <span key={a}>{i > 0 && " · "}<button className="linklike" onClick={() => onOpenTab(a)}>{AGENT_LABELS[a]}</button></span>
          ))}
        </div>
      )}
      <div className="ss-foot muted small">
        <span>{overview.as_of ? `Report as of ${shortDate(overview.as_of)}` : ""}</span>
        <button className="linklike" disabled={busy} onClick={() => (confirm ? (setConfirm(false), start({ max_news_age: 0, max_macro_age: 0, max_fundamentals_age: 0 })) : setConfirm(true))}
                onBlur={() => setConfirm(false)} title="Re-runs every agent and fetches fresh figures">
          {busy ? "Starting…" : confirm ? "Re-run all agents?" : "Refresh ↻"}
        </button>
        <span className="spacer" />
        <span>Not investment advice</span>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
