#!/usr/bin/env bash
# Starts the whole stack for local development, each part in the background, and stops it on Ctrl+C:
#
#   PostgreSQL (docker compose)   127.0.0.1:${POSTGRES_PORT:-5432}
#   public API      uv run api    127.0.0.1:${API_PORT:-8000}
#   public web      frontend/     http://localhost:${FRONTEND_PORT:-3000}
#   admin API       uv run admin  127.0.0.1:${ADMIN_PORT:-8001}
#   admin panel     admin-frontend/  http://127.0.0.1:${ADMIN_FRONTEND_PORT:-3001}
#
# Every part can still be started on its own (README.md). Options:
#   --no-admin    only the public part        --admin-only   only the admin part
#   --no-db       do not start the docker compose database (own PostgreSQL)
# Run from anywhere; output of each process is prefixed with its name.
set -euo pipefail
cd "$(dirname "$0")/.."

public=1 admin=1 db=1
for arg in "$@"; do
  case "$arg" in
    --no-admin) admin=0 ;;
    --admin-only) public=0 ;;
    --no-db) db=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# The Python services read .env themselves; Bun reads .env only from its own directory, so the
# ports and URLs the two Bun servers need are exported from the root .env here.
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
export API_URL="${API_URL:-http://127.0.0.1:${API_PORT:-8000}}"
export ADMIN_API_URL="${ADMIN_API_URL:-http://127.0.0.1:${ADMIN_PORT:-8001}}"

if (( admin )) && [[ -z "${ADMIN_PASSWORD:-}" || -z "${ADMIN_SECRET:-}" ]]; then
  echo "ADMIN_PASSWORD / ADMIN_SECRET are not set in .env: the admin part is skipped (see .env.example)." >&2
  admin=0
fi

trap 'trap - INT TERM EXIT; kill 0 2>/dev/null; wait 2>/dev/null' INT TERM EXIT

run() {  # run <name> <command...>: output prefixed with [name]
  local name="$1"; shift
  ( "$@" 2>&1 | while IFS= read -r line; do printf '[%s] %s\n' "$name" "$line"; done ) &
}

if (( db )); then docker compose up -d db; fi
if (( public )); then
  run api uv run api
  [[ -d frontend/node_modules ]] || (cd frontend && bun install)
  run web bash -c "cd frontend && exec bun run dev"
fi
if (( admin )); then
  run admin uv run admin
  [[ -d admin-frontend/node_modules ]] || (cd admin-frontend && bun install)
  run panel bash -c "cd admin-frontend && exec bun run dev"
fi
wait
