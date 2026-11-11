import { useMemo } from "react";
import { Link } from "react-router-dom";
import { AGENT_LABELS, ago, formatWhen } from "../api.js";
import Markdown, { headingsOf } from "./Markdown.jsx";

/** One report: metadata bar, table of contents and rendered markdown. */
export default function ReportView({ report }) {
  const toc = useMemo(() => headingsOf(report.content_md), [report.content_md]);
  const inputs = Object.entries(report.inputs || {});
  return (
    <div className="report">
      <div className="report-meta">
        <span title={formatWhen(report.created_at)}>{AGENT_LABELS[report.agent]} · report #{report.id} · {ago(report.created_at)}</span>
        {report.truncated && <span className="badge warn">Incomplete: hit the token limit</span>}
        {report.input_tokens != null && <span className="muted">tokens in/out {report.input_tokens}/{report.output_tokens ?? "-"}</span>}
        <span className="spacer" />
        <a className="btn" href={`/api/reports/${report.id}/export`}>Download .md</a>
        <button className="btn" onClick={() => window.print()}>Print</button>
      </div>
      {inputs.length > 0 && (
        <div className="report-inputs">
          Based on:{" "}
          {inputs.map(([agent, r]) => (
            <Link key={agent} to={`/report/${r.id}`} className="chip">{AGENT_LABELS[agent]} #{r.id}</Link>
          ))}
        </div>
      )}
      <div className="report-body">
        {toc.length > 2 && (
          <nav className="toc" aria-label="Contents">
            <div className="toc-title">Contents</div>
            {toc.map((h) => <a key={h.id} href={`#${h.id}`} className={`l${h.level}`}>{h.text}</a>)}
          </nav>
        )}
        <Markdown>{report.content_md}</Markdown>
      </div>
    </div>
  );
}
