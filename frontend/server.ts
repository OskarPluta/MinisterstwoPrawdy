// Dev/prod server: Bun bundles src/index.html (TSX, CSS) on the fly; /api/* is proxied to the Python backend.
import index from "./src/index.html";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const PORT = Number(process.env.FRONTEND_PORT ?? 3000);

const server = Bun.serve({
  port: PORT,
  routes: {
    "/api/*": (req, srv) => {
      const url = new URL(req.url);
      const fwd = new Request(new URL(url.pathname + url.search, API_URL), req);
      // The API limits requests per client (rag/ratelimit.py) and trusts X-Forwarded-For only from
      // this local proxy, so set it here: the visitor's address, or Cloudflare's header when the
      // request comes through the local cloudflared tunnel (scripts/tunnel.sh). Never pass on what
      // the browser sent.
      const peer = srv.requestIP(req)?.address ?? "";
      const local = peer === "127.0.0.1" || peer === "::1" || peer === "::ffff:127.0.0.1";
      fwd.headers.set("x-forwarded-for", (local && req.headers.get("cf-connecting-ip")) || peer || "unknown");
      return fetch(fwd).catch(
        () => new Response(`backend niedostępny: ${API_URL}`, { status: 502 }),
      );
    },
    "/*": index,
  },
  development: process.env.NODE_ENV !== "production" && { hmr: true, console: true },
});

console.log(`frontend: ${server.url} (API → ${API_URL})`);
