import { useState } from "react";
import RefreshControl from "./RefreshControl.jsx";
import { api } from "../api.js";
import { fixNegativeRupees as fix } from "../format.js";

const inr = (n, digits = 0) => n.toLocaleString("en-IN", { maximumFractionDigits: digits });
const STANCES = { positive: "Positive", neutral: "Neutral", negative: "Negative" };
const HORIZONS = [["short", "Short term", "up to 3 months"], ["medium", "Medium term", "3 to 12 months"], ["long", "Long term", "over 1 year"]];

/** The stance as a known key (positive / neutral / negative), whatever its case; null if it is none of those. */
const stanceKey = (stance) => {
  const key = String(stance ?? "").trim().toLowerCase();
  return STANCES[key] ? key : null;
};

/** One horizon's verdict: a solid segment of the strip, coloured by stance (unknown values look neutral). */
export function Horizon({ id, label, span, stance, confidence }) {
  const key = stanceKey(stance);
  const conf = String(confidence ?? "").trim().toLowerCase();
  return (
    // Focusable so the timeframe tooltip is also reachable from the keyboard.
    <div className={`horizon horizon--${key || "neutral"}`} tabIndex={0} aria-describedby={`${id}-tip`}>
      <span className="horizon__label">{label}</span>
      <span className="horizon__stance">{key ? STANCES[key] : "n/a"}</span>
      {key && conf && <span className="horizon__conf">{conf[0].toUpperCase() + conf.slice(1)} confidence</span>}
      <span id={`${id}-tip`} role="tooltip" className="horizon__tip">{label}: {span}</span>
    </div>
  );
}

/** 4-5 strong (green), 3 OK (amber), 1-2 weak (red); the word means the colour is never the only signal. */
export function band(rating) {
  return rating >= 4 ? ["strong", "Strong"] : rating === 3 ? ["ok", "OK"] : ["weak", "Weak"];
}

export function Dots({ rating }) {
  if (rating == null) return <span className="rating none" aria-label="not rated">–</span>;
  const [tone, word] = band(rating);
  return (
    <span className={`rating ${tone}`} role="img" aria-label={`${rating} out of 5, ${word}`}>
      <span className="dots">{[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= rating ? "on" : ""} />)}</span>
      <span className="rating-word">{word}</span>
    </span>
  );
}

/** The ↻ next to the price: fetches Screener's latest close (one request, no LLM) and shows it in place. */
function usePriceRefresh(symbol) {
  const [live, setLive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function refresh() {
    setBusy(true);
    setError(null);
    try { setLive(await api.refreshQuote(symbol)); } catch (e) { setError(e.message); }
    setBusy(false);
  }
  return { live, busy, error, refresh };
}

function Header({ snapshot, symbol }) {
  const price = usePriceRefresh(symbol);
  const s = price.live
    ? { ...snapshot, price: price.live.price, change_pct: price.live.change_pct, price_as_of: price.live.as_of }
    : snapshot;
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
          <button className="linklike ss-refresh" onClick={price.refresh} disabled={price.busy} aria-label="Refresh price"
                  title="Fetch the latest close from Screener (no analysis is re-run)">{price.busy ? "…" : "↻"}</button>
          {s.price_as_of && <div className="muted small">{s.price_as_of}</div>}
          {price.error && <div className="error small">{price.error}</div>}
        </div>
      )}
    </div>
  );
}

/** The three horizon verdicts as one joined strip. */
function VerdictStrip({ company }) {
  return (
    <div className="verdict" role="group" aria-label="Overall outlook by time horizon">
      {HORIZONS.map(([key, label, span]) => <Horizon key={key} id={`horizon-${key}`} label={label} span={span} {...company[key]} />)}
    </div>
  );
}

/**
 * Where each pillar's row leads in the Fundamentals report: the heading (by its text, matched case-insensitively)
 * that discusses it. Cash-flow pillars go to the cash flow table, which holds the figures the evidence quotes.
 */
export const PILLAR_TARGETS = {
  growth: /^growth$/i,
  profitability: /^profitability/i,
  balance_sheet: /^balance sheet (&|and) capital/i,
  leverage: /^balance sheet (&|and) capital/i,
  capital: /^balance sheet (&|and) capital/i,
  asset_quality: /^balance sheet (&|and) capital/i,
  cash_flow: /^cash flow/i,
  capex_funding: /^cash flow/i,
  valuation: /^versus peers/i,
  ownership: /^shareholding (&|and) governance/i,
};

function Pillars({ pillars, onOpen }) {
  return (
    <ul className="ss-pillars">
      {pillars.map((p) => (
        <li key={p.key}>
          <button className="ss-pillar" onClick={() => onOpen("fundamentals", PILLAR_TARGETS[p.key])}
                  aria-describedby={`tip-${p.key}`}>
            <span className="ss-pname">{p.label || p.key}</span>
            <Dots rating={p.rating} />
            <span className={`ss-evidence${p.detail ? " has-detail" : ""}`}>{fix(p.evidence)}</span>
          </button>
          <span id={`tip-${p.key}`} role="tooltip" className="ss-tip">
            {p.detail && <span className="ss-tip__detail">{fix(p.detail)}</span>}
            <span className="ss-tip__hint">Click to open {p.label || p.key} in the Fundamentals report</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const COLUMNS = [
  ["flags", "RED FLAGS"],
  ["strengths", "STRENGTHS"],
  ["watch", "WATCH NEXT QUARTER"],
];

/** Red flags, strengths and what to watch: three quiet, always-open columns with up to three bullets each. */
function Lists({ red, strengths, watch }) {
  const items = { flags: red, strengths, watch };
  if (!COLUMNS.some(([kind]) => items[kind]?.length)) return null;
  return (
    <div className="evidence">
      {COLUMNS.map(([kind, title]) => (
        <section key={kind} className={`evidence__col evidence__col--${kind}`} aria-label={title}>
          <h3 className="evidence__title">{title}</h3>
          {items[kind]?.length
            ? <ul>{items[kind].slice(0, 3).map((t, i) => <li key={i}>{fix(t)}</li>)}</ul>
            : <p className="muted small">None noted</p>}
        </section>
      ))}
    </div>
  );
}

/** Whether the card has anything to draw (reports written before the structured blocks have none). */
export function hasSummary(overview) {
  return !!(overview?.snapshot || overview?.outlook?.structured?.company || overview?.fundamentals?.structured?.pillars);
}

/**
 * The card at the top of a stock's page, drawn from the agents' structured blocks (no prose parsing).
 * Every part is optional: reports written before the blocks existed simply show less.
 */
export default function StockSummary({ overview, symbol, onOpenTab }) {
  if (!hasSummary(overview)) return null;
  const snapshot = overview.snapshot;
  const outlook = overview.outlook?.structured;
  const fund = overview.fundamentals?.structured;
  const headline = outlook?.headline || fund?.headline;
  return (
    <section className="summary" aria-label="Summary">
      {snapshot && <Header snapshot={snapshot} symbol={symbol} />}
      {headline && <p className="ss-quote">“{fix(headline)}”</p>}
      {outlook?.company && <VerdictStrip company={outlook.company} />}
      {fund?.pillars?.length > 0 && <Pillars pillars={fund.pillars} onOpen={onOpenTab} />}
      <Lists red={fund?.red_flags} strengths={fund?.strengths} watch={fund?.watch_next_quarter} />
      <div className="ss-foot muted small">
        <RefreshControl stock={symbol} asOf={overview.as_of}>
          <span>Not investment advice</span>
        </RefreshControl>
      </div>
    </section>
  );
}
