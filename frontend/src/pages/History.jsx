import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import SymbolSearch from "../components/SymbolSearch.jsx";
import RunList from "../components/RunList.jsx";

const PAGE = 25;

export default function History() {
  const [params, setParams] = useSearchParams();
  const stock = params.get("stock") || "";
  const [page, setPage] = useState(0);
  const { data, error, loading } = useAsync(
    () => api.runs({ stock, limit: PAGE + 1, offset: page * PAGE }), [stock, page]);

  function pick(item) {
    setPage(0);
    setParams({ stock: item.symbol });
  }

  return (
    <section>
      <div className="section-head"><h1>Historical runs</h1></div>
      <div className="filter">
        <SymbolSearch onSelect={pick} />
        {stock && (
          <span className="chip">{stock} <button className="x" aria-label="Clear filter" onClick={() => { setPage(0); setParams({}); }}>×</button></span>
        )}
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error.message}</p>}
      {data && <RunList runs={data.slice(0, PAGE)} empty="No runs match." />}
      {data && (
        <div className="pager">
          <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
          <button className="btn" disabled={data.length <= PAGE} onClick={() => setPage(page + 1)}>Older</button>
        </div>
      )}
    </section>
  );
}
