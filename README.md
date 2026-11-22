# Prawdowid

Prototyp do weryfikacji twierdzeń (dezinformacji) na podstawie **oficjalnych źródeł państwowych**, przygotowany na konkurs MSZ ([`docs/task/`](docs/task/)).
Crawler zbiera komunikaty i ostrzeżenia instytucji publicznych, a RAG indeksuje je w PostgreSQL.
Dla podanego twierdzenia system zwraca po polsku werdykt z cytatami, których poprawność sprawdza kod, a nie model.

```text
oficjalne źródła ─► crawler ─► data/crawl/*.jsonl ─► rag ingest ─► PostgreSQL
                                (albo data/snapshot/)                 │
                                                                      ▼
twierdzenie ─► rag verify ─► LLM: ekstrakcja twierdzeń ─► wyszukiwanie (FTS + e5 + RRF)
                                                                      │
                     odpowiedź PL z cytatami ◄─ kontrola cytatów ◄─ LLM: werdykt
```

## Moduły

| Katalog | Rola | Dokumentacja |
| --- | --- | --- |
| [`backend/crawler/`](backend/crawler/) | **FaktBot**: „grzeczny” crawler źródeł z [`sources.yaml`](backend/crawler/sources.yaml), zapis do plików JSONL z historią wersji. Nie zna bazy. | [README](backend/crawler/README.md) |
| [`backend/rag/`](backend/rag/) | Pakiet `rag`: import danych crawlera, PostgreSQL, wyszukiwanie hybrydowe, weryfikator LLM. | [README](backend/rag/README.md) |
| [`backend/api/`](backend/api/) | API HTTP (FastAPI) dla interfejsu WWW: `POST /api/verify`, `GET /api/sources`, `GET /api/health`. | [frontend README](frontend/README.md#backend) |
| [`backend/discord_bot/`](backend/discord_bot/) | Kanał Discord (`discord-bot`): komendy `/sprawdz`, `/zrodla`, wzmianki i DM oparte o `rag.verify.Verifier` lub `/api`. | [README](backend/discord_bot/README.md) |
| [`frontend/`](frontend/) | Interfejs WWW **Copernicus** w systemie Ministerstwo Prawdy (Bun + React + TypeScript), `/api/*` przekazywane do backendu. | [README](frontend/README.md) |
| [`data/snapshot/`](data/snapshot/) | Zamrożony zrzut crawla w repo, domyślne wejście `rag ingest`. `data/crawl/` (świeże przebiegi) jest poza git. | |
| [`scripts/`](scripts/) | Ewaluacja wyszukiwania i werdyktów. | [rag README](backend/rag/README.md#ewaluacja) |
| [`docs/`](docs/) | [Architektura](docs/ARCHITEKTURA.md), [treść zadania](docs/task/), [notatki badawcze](docs/notes/). | |

Backend (`backend/*`) to workspace uv: jedno `.venv` w katalogu głównym, polecenia Pythona uruchamiasz **z katalogu głównego**.
Frontend ma własne zależności (Bun) i jego polecenia uruchamiasz w `frontend/`.

## Szybki start

Wymagania: [uv](https://docs.astral.sh/uv/), Docker z Compose v2 (albo własny PostgreSQL ≥ 14), Linux/macOS/WSL.

```bash
cp .env.example .env                 # domyślne wartości działają z docker compose; dopisz klucz LLM
docker compose up -d db              # PostgreSQL 16 na 127.0.0.1:${POSTGRES_PORT}
uv sync                              # oba moduły + torch CPU dla multilingual-e5
uv run playwright install chromium   # potrzebne tylko crawlerowi

uv run rag ingest                    # ładuje data/snapshot/; pierwsze uruchomienie pobiera model e5
uv run rag stats
uv run rag search "ostrzeżenie hydrologiczne"
uv run rag verify "Czy IMGW wydał ostrzeżenie przed suszą hydrologiczną?"   # płatne wywołania LLM
```

`verify` potrzebuje `PCSS_API_KEY` (domyślny dostawca: modele PCSS, np. GLM-5.3-Flash, Bielik), albo `OPENAI_API_KEY` z `--provider openai`, albo `ANTHROPIC_API_KEY` z `--provider anthropic`. Wszystkie zmienne są opisane w [`.env.example`](.env.example).
Ostrzeżenia mają okna ważności, więc przy pytaniach o „dziś” na starym zrzucie podaj `--now <ISO 8601>`.

Świeże dane z crawlera (odpytuje prawdziwe serwisy, trwa kilka minut):

```bash
export CRAWLER_CONTACT=zespol@example.pl   # mail w User-Agencie; crawler nie czyta .env
uv run crawler once --max-items 5          # ostrożny przebieg: maks. 5 nowych pozycji na źródło
uv run crawler status                      # statusy i alarmy źródeł
uv run rag ingest data/crawl               # idempotentne: zmieniona treść staje się nową wersją
```

Interfejs WWW (API + [Bun](https://bun.sh)), w dwóch terminalach:

```bash
uv run api                                  # http://127.0.0.1:8000/api/docs; ten sam embedder co ingest (PRAWDOWID_EMBEDDER)
cd frontend && bun install && bun run dev   # http://localhost:3000
```

Kanał Discord (wymaga `DISCORD_BOT_TOKEN` w `.env`, szczegóły w [backend/discord_bot/README.md](backend/discord_bot/README.md)):

```bash
uv run discord-bot                          # /sprawdz, /zrodla, wzmianki @Prawdowid i DM
```

Każde sprawdzenie w interfejsie to płatne wywołania LLM, tak jak `rag verify`. Na starym zrzucie dopisz do adresu `?now=<ISO 8601>`.

Opcje poleceń: `uv run rag --help`, `uv run rag <polecenie> --help`, `uv run crawler --help`.

## Testy

```bash
uv run pytest                  # RAG + crawler, bez sieci i bez kluczy API
uv run pytest backend/crawler  # tylko crawler (test end-to-end na lokalnych serwerach)
cd frontend && bun run typecheck
```

Testy bazodanowe używają `TEST_DATABASE_URL` (albo `DATABASE_URL`). Każdy test działa w tymczasowym schemacie, więc nie rusza danych.
Bez bazy albo bez Chromium część testów jest pomijana (`uv run pytest -rs` pokaże powód).

## Problemy

- **`cannot connect to PostgreSQL`**: sprawdź `docker compose ps` (stan `healthy`) i czy `DATABASE_URL` zgadza się z `POSTGRES_PORT`/`POSTGRES_PASSWORD`.
  Hasło ustawia się tylko przy tworzeniu wolumenu: po zmianie `POSTGRES_PASSWORD` wróć do starego albo zrób `docker compose down -v`.
- **Port 5432 zajęty**: ustaw inny `POSTGRES_PORT` w `.env` i popraw port w `DATABASE_URL`.
- **`passages embedded with [...], query embedder is ...`**: `search`/`verify` używa innego `--embedder` niż `ingest`.
  Użyj tego samego albo przelicz wektory: `uv run rag reembed [--embedder ...]`. Ponowny `ingest` innym embedderem nie przelicza niezmienionych rekordów. `--mode bm25` działa zawsze.
- **Model e5 nie chce się pobrać**: pierwsze użycie pobiera go do `~/.cache/huggingface`. Potem można pracować offline z `HF_HUB_OFFLINE=1`.
  Bez sieci i modelu użyj `--embedder hash` (tylko testy, bez semantyki).
- **Playwright: `Executable doesn't exist`**: `uv run playwright install chromium` (na świeżym Linuksie z `--with-deps`).
- **Crawler: `Brak maila kontaktowego`** (kod 2): wyeksportuj `CRAWLER_CONTACT` w powłoce. Kod 3 oznacza, że inny proces crawlera pracuje na tym katalogu danych.
- **Wyszukiwanie nie widzi nowego crawla**: `rag ingest` bez argumentu ładuje `data/snapshot/`, a nie `data/crawl/`.

## Bezpieczeństwo i dane

- Sekrety trzymaj tylko w `.env` (jest w `.gitignore`). Szablon to `.env.example`.
- Crawler nigdy nie obchodzi zabezpieczeń: przestrzega robots.txt i limitu zapytań, a CAPTCHA lub wyzwanie antybotowe kończy się statusem `blocked`.
- Treści ze źródeł są zapisywane tak, jak je opublikowano, **bez maskowania** (komunikaty policji mogą zawierać nazwiska).
  Tekst od użytkownika jest maskowany w logu sprawdzeń (`mask` w [`verify.py`](backend/rag/src/rag/verify.py)).
- Ograniczenia i ryzyka: [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md#ograniczenia-i-ryzyka).
