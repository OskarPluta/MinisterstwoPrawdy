// Dev/prod server: Bun bundles src/index.html (TSX, CSS) on the fly; /api/* is proxied to the Python backend.
import index from "./src/index.html";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const PORT = Number(process.env.FRONTEND_PORT ?? 3000);
const DEV = process.env.NODE_ENV !== "production";

// TEMPORARY: admin panel port and password for the strip in src/DevAdminLink.tsx (remove together). Read from the
// environment at runtime, so the password is never in the source or the bundle; answered only in dev mode, only
// to loopback clients and only when ADMIN_PASSWORD is set.
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "";
const ADMIN_FRONTEND_PORT = Number(process.env.ADMIN_FRONTEND_PORT ?? 3001);
const LOOPBACK = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

const server = Bun.serve({
  port: PORT,
  routes: {
    "/dev/admin-link": (req, srv) => {
      const ip = srv.requestIP(req)?.address ?? "";
      if (!DEV || !ADMIN_PASSWORD || !LOOPBACK.has(ip)) return new Response("Not Found", { status: 404 });
      return Response.json({ port: ADMIN_FRONTEND_PORT, password: ADMIN_PASSWORD }, { headers: { "Cache-Control": "no-store" } });
    },
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
    // Shared results are for the people given the link: tell crawlers that do not run JS too.
    "/wynik/*": async (req) => {
      const page = await fetch(new URL("/", `http://127.0.0.1:${PORT}`));
      const headers = new Headers(page.headers);
      headers.set("X-Robots-Tag", "noindex, nofollow");
      headers.set("Cache-Control", "no-store");
      return new Response(page.body, { status: page.status, headers });
    },
    "/*": index,
  },
  development: DEV && { hmr: true, console: true },
});

console.log(`frontend: ${server.url} (API → ${API_URL})`);
