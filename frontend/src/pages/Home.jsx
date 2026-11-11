import { Link, useNavigate } from "react-router-dom";
import SymbolSearch from "../components/SymbolSearch.jsx";
import RunList from "../components/RunList.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";

export default function Home() {
  const navigate = useNavigate();
  const { data: runs } = useAsync(() => api.runs({ limit: 5 }), []);
  return (
    <section>
      <div className="hero">
        <h1>Analyse an Indian stock</h1>
        <p className="muted">Search by company name, NSE symbol or BSE code to see its stored news, fundamentals, macro and outlook reports.</p>
        <SymbolSearch autoFocus onSelect={(item) => navigate(`/stock/${encodeURIComponent(item.symbol)}`)} />
      </div>
      <div className="section-head"><h2>Recent runs</h2><Link to="/history">All history</Link></div>
      {runs && <RunList runs={runs} empty="No runs yet. Run `stock-analyst all TCS` to create one." />}
    </section>
  );
}
