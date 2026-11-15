import { useEffect, useState } from "react";

const SHOW_AFTER = 600; // px scrolled before the button appears

/** A floating "back to top" button, shown once the page has been scrolled a good way down. */
export default function ScrollToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > SHOW_AFTER);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  function toTop() {
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: calm ? "auto" : "smooth" });
  }

  if (!visible) return null;
  return (
    <button className="to-top" onClick={toTop} aria-label="Scroll to top" title="Back to top">↑</button>
  );
}
