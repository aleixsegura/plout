import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Serves ../demo-data (synthetic fixtures, see scripts/generate_demo_data.py) instead
// of the real ../data, so the app can be demoed without touching or
// exposing real broker/account data. Separate port from `npm run dev`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  publicDir: path.resolve(__dirname, "../demo-data"),
  server: { port: 5175 },
  // Its own folder, so a deploy can never pick up a `dist/` built from real data.
  build: { outDir: "dist-demo" },
});
