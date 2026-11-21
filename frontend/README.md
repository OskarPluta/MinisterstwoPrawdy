# frontend

Interfejs WWW Prawdowida: React + TypeScript, budowany i serwowany przez [Bun](https://bun.sh) (bez Vite i bez osobnego bundlera).

```bash
cd frontend
bun install
bun run dev        # http://localhost:3000, hot reload
bun run typecheck
bun run build      # statyczny zrzut do dist/
bun run start      # serwer produkcyjny
```

## Jak to działa

- [`server.ts`](server.ts) to `Bun.serve`: importuje [`src/index.html`](src/index.html), a Bun sam bundluje TSX i CSS, do których strona się odwołuje.
- `/api/*` jest przekazywane do backendu Pythona pod `API_URL` (domyślnie `http://127.0.0.1:8000`), więc kod frontu woła po prostu `fetch("/api/...")`, bez CORS. Gdy backend nie działa, proxy zwraca 502.
- Zmienne: `API_URL`, `FRONTEND_PORT`. Bun czyta `.env` z `frontend/`, nie z katalogu głównego.

## Backend

API jeszcze nie istnieje. Powinno powstać jako nowy pakiet `backend/api/` (zob. [AGENTS.md](../AGENTS.md#gdzie-zmienić-x)), który woła `rag.verify.Verifier` i wystawia trasy pod prefiksem `/api`.
