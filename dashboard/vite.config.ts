import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to a running `sdlc dashboard --no-open`;
// `npm run build` writes into the Python package so `sdlc dashboard` serves it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/agentic_sdlc/dashboard/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
