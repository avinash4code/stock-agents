async function get(path, params) {
  const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")) : "";
  const res = await fetch(`/api${path}${qs}`);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  symbols: (q, signal) => fetch(`/api/symbols?q=${encodeURIComponent(q)}`, { signal }).then((r) => r.json()),
  runs: (params) => get("/runs", params),
  report: (id) => get(`/reports/${id}`),
  reports: (params) => get("/reports", params),
};

export const AGENT_LABELS = { outlook: "Outlook", fundamentals: "Fundamentals", news: "News", macro: "Macro" };

export function formatWhen(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ago(iso) {
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
