import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes into the Python package so `stock-analyst serve` can serve it.
// `npm run dev` proxies /api to a running `stock-analyst serve`.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/stock_agents/web/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
