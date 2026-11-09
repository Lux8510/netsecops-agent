import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";

export default defineConfig({
  plugins: [react(), {
    name: "local-v1-baseline",
    configureServer(server) {
      server.middlewares.use("/baseline.json", async (req, res) => {
        res.setHeader("Cache-Control", "no-store");
        if (req.method !== "GET") { res.statusCode = 405; res.end(); return; }
        try {
          const data = await readFile(fileURLToPath(new URL("../v1/results/latest.json", import.meta.url)), "utf8");
          res.setHeader("Content-Type", "application/json");
          res.end(data);
        } catch { res.statusCode = 404; res.end("No local baseline collected"); }
      });
    },
  }],
  // One reviewed copy of each JSON example serves both the site and offline tests.
  publicDir: fileURLToPath(new URL("../examples", import.meta.url)),
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: { environment: "node" },
});
