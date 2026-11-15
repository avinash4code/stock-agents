import { Children, cloneElement, createContext, isValidElement, useContext, useLayoutEffect, useRef } from "react";

/**
 * Table behaviour for report markdown: sticky row labels (CSS), time-series tables open scrolled to their
 * latest period, negative numbers in red, and a trend sparkline on the Sales, OPM and free cash flow rows.
 */

const SeriesContext = createContext(false);

const PERIOD = /^(?:[A-Z][a-z]{2} \d{4}|TTM)$/;  // "Mar 2026", "TTM"
const NEGATIVE = /^[-−]\s?\d[\d,]*(?:\.\d+)?\s*(?:%|x|pp)?$/;
// Rows where a negative figure is normal (cash going out to invest or repay), so red would mislead.
const NEGATIVE_IS_NORMAL = /^cash from (investing|financing)/i;
const SPARK_ROWS = /^(sales|revenue|opm %|free cash flow)$/i;

function textOf(node) {
  if (!node) return "";
  if (typeof node.value === "string") return node.value;
  return (node.children || []).map(textOf).join("");
}

const cellsOf = (row, tag) => (row.children || []).filter((c) => c.tagName === tag);

/** A table is a time series when its header row is mostly period labels (Mar 2026, TTM, ...). */
export function isSeriesTable(node) {
  const header = (node.children || []).find((c) => c.tagName === "thead");
  const row = header && (header.children || []).find((c) => c.tagName === "tr");
  if (!row) return false;
  const periods = cellsOf(row, "th").slice(1).filter((th) => PERIOD.test(textOf(th).trim()));
  return periods.length >= 3;
}

export function parseCell(text) {
  const n = Number(text.trim().replace(/[,%+]/g, "").replace(/\s*(?:x|pp)$/, "").replace("−", "-"));
  return text.trim() && Number.isFinite(n) ? n : null;
}

export function Table({ node, children, ...props }) {
  const series = isSeriesTable(node);
  const ref = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (series && el) el.scrollLeft = el.scrollWidth;  // latest period is the last column
  }, [series]);
  return (
    <SeriesContext.Provider value={series}>
      <div className="table-wrap" ref={ref}><table {...props}>{children}</table></div>
    </SeriesContext.Provider>
  );
}

export function Sparkline({ values, label }) {
  const points = values.map((v, i) => [i, v]).filter(([, v]) => v !== null);
  if (points.length < 3) return null;
  const W = 60, H = 18, PAD = 2;
  const ys = points.map(([, v]) => v);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const x = (i) => PAD + (i / (values.length - 1)) * (W - 2 * PAD);
  const y = (v) => (hi === lo ? H / 2 : H - PAD - ((v - lo) / (hi - lo)) * (H - 2 * PAD));
  const first = ys[0], last = ys[ys.length - 1];
  const tone = last > first ? "up" : last < first ? "down" : "flat";
  const [lx, ly] = [x(points[points.length - 1][0]), y(last)];
  return (
    <svg className={`spark ${tone}`} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label={`${label} trend: ${first.toLocaleString("en-IN")} to ${last.toLocaleString("en-IN")}`}>
      <title>{`${label}: ${first.toLocaleString("en-IN")} → ${last.toLocaleString("en-IN")}`}</title>
      <polyline points={points.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} fill="none" />
      <circle cx={lx} cy={ly} r="2" />
    </svg>
  );
}

/**
 * A body row. It knows its label and its cells' text, so it flags negative numbers (`neg`) and, in a time-series
 * table, gives the label cell of a Sales / OPM / free cash flow row a sparkline (`spark`).
 */
export function Tr({ node, children, ...props }) {
  const series = useContext(SeriesContext);
  const cells = cellsOf(node, "td");
  if (!cells.length) return <tr {...props}>{children}</tr>;  // a header row
  const label = textOf(cells[0]).trim();
  const values = cells.slice(1).map((c) => parseCell(textOf(c)));
  const showSpark = series && SPARK_ROWS.test(label) && values.filter((v) => v !== null).length >= 3;
  const redAllowed = !NEGATIVE_IS_NORMAL.test(label);

  let i = 0;
  const content = Children.map(children, (child) => {
    if (!isValidElement(child)) return child;
    const index = i++;
    if (index === 0) return showSpark ? cloneElement(child, { spark: <Sparkline label={label} values={values} /> }) : child;
    return redAllowed && NEGATIVE.test(textOf(cells[index]).trim()) ? cloneElement(child, { neg: true }) : child;
  });
  return <tr {...props}>{content}</tr>;
}

export function Td({ node, children, spark, neg, ...props }) {
  if (spark) return <td {...props}><div className="rowhead"><span>{children}</span>{spark}</div></td>;
  return <td {...props} className={neg ? "neg" : undefined}>{children}</td>;
}
