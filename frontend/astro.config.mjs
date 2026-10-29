// @ts-check
import { defineConfig } from "astro/config";

const apiTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:5200";

// Canonical URLs need an absolute origin. Override this when hosting elsewhere.
const site = process.env.PUBLIC_SITE_URL ?? "http://127.0.0.1:5100";

// The frontend is fully static: the browser calls /api/* and Nginx (or the Vite
// dev server) forwards those requests to the FastAPI service.
export default defineConfig({
  site,
  output: "static",
  server: { host: true, port: 4321 },
  vite: {
    server: {
      proxy: {
        "/api": { target: apiTarget, changeOrigin: true },
        "/health": { target: apiTarget, changeOrigin: true },
      },
    },
  },
});
