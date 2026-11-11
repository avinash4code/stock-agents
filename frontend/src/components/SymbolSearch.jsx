import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";

/** Company typeahead. Calls onSelect({symbol, name}) when a suggestion is chosen.
 *  A bare BSE scrip code (5-6 digits) is accepted as typed, since the NSE list has no BSE names. */
export default function SymbolSearch({ onSelect, autoFocus = false }) {
  const [text, setText] = useState("");
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [error, setError] = useState(null);
  const listId = useId();
  const box = useRef(null);

  useEffect(() => {
    const q = text.trim();
    if (!q) { setItems([]); return; }
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      api.symbols(q, ctrl.signal).then(
        (found) => { setItems(found); setActive(found.length ? 0 : -1); setError(null); },
        (e) => { if (e.name !== "AbortError") setError("Could not load suggestions"); },
      );
    }, 150);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [text]);

  useEffect(() => {
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const bse = /^\d{5,6}$/.test(text.trim()) ? { symbol: text.trim(), name: "BSE scrip code" } : null;
  const shown = bse && !items.some((i) => i.symbol === bse.symbol) ? [bse, ...items] : items;

  function choose(item) {
    setText(`${item.name} (${item.symbol})`);
    setOpen(false);
    onSelect(item);
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && open && shown[active]) { e.preventDefault(); choose(shown[active]); }
    else if (e.key === "Escape") setOpen(false);
  }

  return (
    <div className="search" ref={box}>
      <input
        type="search" role="combobox" aria-expanded={open && shown.length > 0} aria-controls={listId}
        aria-autocomplete="list" aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        placeholder="Company name, NSE symbol or BSE code, e.g. Infosys, TCS, 500325"
        value={text} autoFocus={autoFocus} autoComplete="off" spellCheck={false}
        onChange={(e) => { setText(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onKeyDown={onKeyDown}
      />
      {error && <div className="search-note">{error}</div>}
      {open && text.trim() && shown.length === 0 && !error && <div className="search-note">No matching company</div>}
      {open && shown.length > 0 && (
        <ul className="suggestions" role="listbox" id={listId}>
          {shown.map((item, i) => (
            <li key={item.symbol} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                className={i === active ? "active" : ""}
                onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); choose(item); }}>
              <strong>{item.symbol}</strong><span>{item.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
