/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Tekly Go (the phone app) is a second page of this build, served at /go. In dev, any /go/<route>
// that isn't a real file falls back to the Go page so client-side routes survive a refresh.
const goFallback: Plugin = {
  name: "tekly-go-fallback",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const u = req.url || "";
      if ((u === "/go" || u.startsWith("/go/")) && !/\.[a-z0-9]+$/i.test(u.split("?")[0])) req.url = "/go/index.html";
      next();
    });
  },
};

// Guarded root cutover: FastAPI serves the redesign at "/" (the whole app).
// Same origin as the live app → the existing session token is reused, so there
// is no CORS and no auth redesign. The dev server proxies /api to the running
// FastAPI (localhost:8000) so `npm run dev` talks to real data.
export default defineConfig({
  base: "/",
  plugins: [react(), goFallback],
  server: {
    port: 5175,
    proxy: {
      "/api": { target: process.env.API_TARGET || "http://localhost:8000", changeOrigin: true },
      "/static": { target: process.env.API_TARGET || "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    // FastAPI serves this folder at /app2 (see README).
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        go: fileURLToPath(new URL("./go/index.html", import.meta.url)),
      },
    },
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
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**", "src/main.tsx", "src/go/main.tsx", "src/**/*.d.ts"],
      reporter: ["text-summary", "lcov"],
      // RATCHET: set at the measured level; raise as tests are added, never lower.
      // Measured 2.8% stmts/lines, 25% funcs, 63% branches; floors leave headroom for new untested files.
      thresholds: { statements: 2.5, lines: 2.5, functions: 24, branches: 60 },
    },
  },
});
