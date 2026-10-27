import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");

// How old an export may be before a dev-server start refreshes it. Each
// exporter exits without a single request when its file is younger than this
// and still covers every holding, so restarting the dev server stays instant.
const EXPORT_MAX_AGE_HOURS = 12;

/**
 * Refresh one of the keyless exports (market caps, price history) when the dev
 * server starts.
 *
 * These are the files with no broker behind them, and having to remember a
 * separate `python export_*.py` before opening the page defeats the point — so
 * the dev server runs them, in the background (the server is up and serving
 * meanwhile) and reloads the page if the file changed.
 */
function backgroundExport(name: string, script: string, outName: string): Plugin {
  return {
    name: `plout:${name.replace(/ /g, "-")}`,
    apply: "serve",
    configureServer(server: ViteDevServer) {
      const venvPython = path.join(PROJECT_ROOT, ".venv/bin/python");
      const python = fs.existsSync(venvPython) ? venvPython : "python3";
      const outFile = path.join(PROJECT_ROOT, "data", outName);
      const before = fs.statSync(outFile, { throwIfNoEntry: false })?.mtimeMs;

      const child = spawn(
        python,
        [script, "--max-age-hours", String(EXPORT_MAX_AGE_HOURS)],
        { cwd: PROJECT_ROOT, stdio: ["ignore", "pipe", "pipe"] },
      );

      const log = (data: Buffer) =>
        String(data)
          .trimEnd()
          .split("\n")
          .forEach((line) => server.config.logger.info(`  ${name}: ${line.trim()}`));
      child.stdout.on("data", log);
      child.stderr.on("data", log);

      // A missing interpreter (or missing `requests`) must never take the dev
      // server down with it: the page just keeps the data it has.
      child.on("error", (err) =>
        server.config.logger.warn(`  ${name}: could not run the exporter (${err.message})`),
      );
      child.on("close", () => {
        const after = fs.statSync(outFile, { throwIfNoEntry: false })?.mtimeMs;
        if (after !== before) server.ws.send({ type: "full-reload" });
      });
    },
  };
}

// Serves the sibling `data/` folder (gitignored, written by the
// exporters) as static files at the site root, so the app can fetch
// /positions.json and the rest without a backend or data duplication.
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    backgroundExport("market caps", "export_marketcaps.py", "marketcaps.json"),
    backgroundExport("prices", "export_prices.py", "prices.json"),
  ],
  publicDir: path.resolve(__dirname, "../data"),
});
