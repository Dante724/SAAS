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
    coverage: {
      provider: "v8",
      // Measure ALL source files, including ones no test imports — an honest denominator.
      all: true,
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**", "src/main.tsx", "src/**/*.d.ts"],
      reporter: ["text-summary", "lcov"],
      // RATCHET: set at the measured level; raise as tests are added, never lower.
      // Measured 2.8% stmts/lines, 25% funcs, 63% branches; floors leave headroom for new untested files.
      thresholds: { statements: 2.5, lines: 2.5, functions: 24, branches: 60 },
    },
  },
});
