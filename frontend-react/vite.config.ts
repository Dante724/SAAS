/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Guarded root cutover: FastAPI serves the redesign at "/" (the whole app).
// Same origin as the live app → the existing session token is reused, so there
// is no CORS and no auth redesign. The dev server proxies /api to the running
// FastAPI (localhost:8000) so `npm run dev` talks to real data.
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    // FastAPI serves this folder at /app2 (see README).
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    // Frontend unit/component tests (Vitest + React Testing Library).
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
