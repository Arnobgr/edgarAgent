import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * `public/recorded` is a symlink to the repo's `recorded/`, which is where
 * `fie export-runs` writes and where the accepted baseline's runs are
 * versioned. Vite copies `public/` into the build verbatim, so the landing
 * page ships as static files the CDN answers and a reader who only reads
 * never reaches the VPS (ADR 0027). A symlink rather than a copy step keeps
 * the export the single source of truth — there is no second copy to go
 * stale, and no build plugin to explain.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
