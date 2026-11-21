// Dev/prod server: Bun bundles src/index.html (TSX, CSS) on the fly; /api/* is proxied to the Python backend.
import index from "./src/index.html";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const PORT = Number(process.env.FRONTEND_PORT ?? 3000);

const server = Bun.serve({
  port: PORT,
  routes: {
    "/api/*": (req) => {
      const url = new URL(req.url);
      return fetch(new URL(url.pathname + url.search, API_URL), req).catch(
        () => new Response(`backend niedostępny: ${API_URL}`, { status: 502 }),
      );
    },
    "/*": index,
  },
  development: process.env.NODE_ENV !== "production" && { hmr: true, console: true },
});

console.log(`frontend: ${server.url} (API → ${API_URL})`);
