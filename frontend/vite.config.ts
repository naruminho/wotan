import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Everything is bundled locally: no CDN, no external fonts, workers inlined as
// separate local chunks (public/ is copied as-is).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    chunkSizeWarningLimit: 4000,
    rollupOptions: {
      output: {
        manualChunks: {
          monaco: ["monaco-editor"],
          codemirror: [
            "@codemirror/state",
            "@codemirror/view",
            "@codemirror/commands",
            "@codemirror/language",
          ],
          xterm: ["@xterm/xterm", "@xterm/addon-fit"],
        },
      },
    },
  },
  worker: {
    format: "es",
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
