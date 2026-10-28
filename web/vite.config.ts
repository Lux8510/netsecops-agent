import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  // One reviewed copy of each JSON example serves both the site and offline tests.
  publicDir: fileURLToPath(new URL("../examples", import.meta.url)),
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: { environment: "node" },
});
