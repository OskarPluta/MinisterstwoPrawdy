// Admin panel dev/prod server: Bun bundles src/index.html on the fly; /admin/api/* is proxied to the
// admin API (backend/admin, a separate process from the public API). Listens on 127.0.0.1 only unless
// ADMIN_FRONTEND_HOST says otherwise; put it behind HTTPS (ADMIN_COOKIE_SECURE=1) if exposed.
import index from "./src/index.html";

const ADMIN_API_URL = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8001";
const PORT = Number(process.env.ADMIN_FRONTEND_PORT ?? 3001);
const HOST = process.env.ADMIN_FRONTEND_HOST ?? "127.0.0.1";

const server = Bun.serve({
  port: PORT,
  hostname: HOST,
  routes: {
    "/admin/api/*": (req) => {
      const url = new URL(req.url);
      return fetch(new URL(url.pathname + url.search, ADMIN_API_URL), req).catch(
        () => new Response(JSON.stringify({ detail: `API panelu niedostępne: ${ADMIN_API_URL}` }), {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }),
      );
    },
    "/*": index,
  },
  development: process.env.NODE_ENV !== "production" && { hmr: true, console: true },
});

console.log(`admin-frontend: ${server.url} (API → ${ADMIN_API_URL})`);
