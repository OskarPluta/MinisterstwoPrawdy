#!/usr/bin/env bash
# Co INGEST_EVERY_MIN minut (domyślnie 15) ładuje nowe wersje z data/crawl do bazy (rag ingest jest idempotentny:
# niezmienione rekordy są pomijane przed liczeniem embeddingów). Para dla scripts/crawler_run.sh.
#   setsid nohup scripts/ingest_loop.sh > /tmp/ingest_loop.log 2>&1 &
#   pkill -f ingest_loop.sh
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
while true; do
  echo "$(date -Is) ingest start"
  uv run rag ingest data/crawl 2>&1 | grep -v "Loading weights" | tail -3
  echo "$(date -Is) ingest koniec"
  sleep $(( ${INGEST_EVERY_MIN:-15} * 60 ))
done
