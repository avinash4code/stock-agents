import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import rehypeSlug from "rehype-slug";
import GithubSlugger from "github-slugger";

// Sanitise first (report text includes scraped article content), then add heading ids.
const REHYPE = [rehypeSanitize, rehypeSlug];
const REMARK = [remarkGfm];

const components = {
  // Wide financial tables scroll sideways instead of breaking the page layout.
  table: ({ node, ...props }) => <div className="table-wrap"><table {...props} /></div>,
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

/** Headings (levels 2-3) for the table of contents; ids match rehype-slug's. */
export function headingsOf(md) {
  const slugger = new GithubSlugger();
  const out = [];
  let fenced = false;
  for (const line of md.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (fenced) continue;
    const m = /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;
    const text = m[2].replace(/[*_`]/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
    const id = slugger.slug(text);
    if (m[1].length === 2 || m[1].length === 3) out.push({ level: m[1].length, text, id });
  }
  return out;
}

export default function Markdown({ children }) {
  const body = useMemo(() => (
    <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={components}>{children}</ReactMarkdown>
  ), [children]);
  return <article className="md">{body}</article>;
}
