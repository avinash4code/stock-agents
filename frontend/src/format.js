/**
 * "₹-789 Cr", "₹ −789 Cr" or "₹–789 Cr" -> "–₹789 Cr": a negative amount carries its sign before the rupee sign.
 * Report and card text comes from the model, which often writes the sign after the ₹, so it is fixed at render time.
 */
export function fixNegativeRupees(text) {
  return typeof text === "string" ? text.replace(/₹\s?[-−–‑]\s?(?=\d)/g, "–₹") : text;
}
