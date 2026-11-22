#!/usr/bin/env bash
# Crawler w trybie ciągłym (scheduler: każde źródło co interval_minutes, źródła background tylko w wolnym czasie).
# Wczytuje klucze API z .env (crawler sam go nie czyta) i zapisuje do data/crawl/ (log: data/crawl/crawler.log).
#   scripts/crawler_run.sh                 # na pierwszym planie (Ctrl+C kończy po bieżącym źródle)
#   setsid nohup scripts/crawler_run.sh >/dev/null 2>&1 &   # w tle, przetrwa zamknięcie terminala
#   pkill -TERM -f "crawler run"           # zatrzymanie po bieżącym źródle
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec uv run crawler run "$@"
