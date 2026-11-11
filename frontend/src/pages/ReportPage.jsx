import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import ReportView from "../components/ReportView.jsx";

export default function ReportPage() {
  const { id } = useParams();
  const { data, error, loading } = useAsync(() => api.report(id), [id]);
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;
  return (
    <section>
      <div className="section-head">
        <h1>{data.stock || "Market"} · {data.agent}</h1>
        {data.stock && <Link to={`/stock/${encodeURIComponent(data.stock)}`}>All reports for {data.stock}</Link>}
      </div>
      <ReportView report={data} />
    </section>
  );
}
