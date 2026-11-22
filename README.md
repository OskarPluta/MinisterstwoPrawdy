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

`verify` potrzebuje `OPENAI_API_KEY` albo `ANTHROPIC_API_KEY` z `--provider anthropic`. Wszystkie zmienne są opisane w [`.env.example`](.env.example).
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

### Ewaluacja end-to-end: scenariusze A–J i zestaw rozszerzony

Jeden skrypt, dwa zestawy przypadków z wzorcowymi odpowiedziami. Oba liczą mierniki MVP z zadania.

| Zestaw | Plik przypadków | Korpus | Wyszukiwanie na żywo | Przypadki |
| --- | --- | --- | --- | --- |
| `scenarios` | [`scenario_cases.yaml`](backend/rag/tests/fixtures/scenario_cases.yaml) | `fake_corpus.json` | wyłączone (J2 symuluje awarię Google Fact Check bez sieci) | 14 (A–J), z czego 3 N/A |
| `extended` | [`extended_cases.yaml`](backend/rag/tests/fixtures/extended_cases.yaml) | `data/snapshot` | włączone: gov.pl, a z kluczami także Google Fact Check i EUvsDisinfo | 69, z czego 16 N/A |

Zestaw `extended` to prawdziwe przypadki z gałęzi `dataset` (`extended_real`) oraz dopisane kategorie oparte na rekordach z `data/snapshot`: `bzdura` (prawdziwy komunikat przesadzony do absurdu), `odległe-fakty` (dwa osobne rekordy, które razem dają prawdziwy wniosek; `must_cite_all` wymaga cytatu z obu), `poza-kontekstem` (fakt lub cytat wyrwany z kontekstu, czasem nadal prawdziwy) i `opinia` (oceny tematów z bazy oraz przypisanie opinii konkretnej osobie). Wyniki są podsumowane według tych kategorii.

```bash
uv run python scripts/eval_scenarios.py                       # scenariusze A–J (~30 wywołań LLM, ~3 min)
uv run python scripts/eval_scenarios.py --suite extended      # zestaw rozszerzony (~150 wywołań LLM, ~20 min, sieć)
uv run python scripts/eval_scenarios.py --only E,J2           # wybrane przypadki (działa z oboma zestawami)
uv run python scripts/eval_scenarios.py --suite extended --no-live   # bez sieci (większość przypadków wyjdzie `insufficient`)
uv run python scripts/eval_scenarios.py --provider anthropic  # inny dostawca LLM (--model …)
uv run python scripts/eval_scenarios.py --embedder hash       # bez modelu e5 (szybciej, gorsze wyszukiwanie)
```

Oba zestawy są płatne, bo wołają LLM. Wymagają bazy z `DATABASE_URL` i klucza LLM w `.env`. Korpus trafia do tymczasowego schematu, który po przebiegu jest usuwany, więc robocza baza zostaje nietknięta. W zestawie `extended` wynik zależy od tego, co źródła zwrócą w dniu przebiegu. Bez `GOOGLE_FACTCHECK_API_KEY` i `EUVSDISINFO_API_KEY` działa tylko gov.pl, co skrypt wypisuje na starcie i zapisuje w podsumowaniu. Koszt w dolarach pojawi się po ustawieniu `PRAWDOWID_PRICE_IN/OUT`.

**Wyniki.** Każdy przebieg zapisuje się w osobnym katalogu `eval_results/<zestaw>/<data-czas>_<model>/`, np. `eval_results/extended/20261122-081500_gpt-5.5/`:

- `summary.md`: tabela mierników z celami MVP, wynik według kategorii, wynik każdego przypadku (z listą niezaliczonych sprawdzeń) i pełne odpowiedzi obok wzorców;
- `results.json`: surowe dane (werdykty, cytaty jako `source_id:upstream_id` i URL, notatki, logi wyszukiwania na żywo, czasy, tokeny, koszt, commit).

Ta sama tabela mierników i lista przypadków są wypisywane w terminalu. Po poprawieniu wzorców w pliku YAML nie trzeba płacić za nowy przebieg: `--rescore <katalog przebiegu>` ponownie sprawdza zapisane odpowiedzi według aktualnego pliku przypadków (bez wywołań LLM) i nadpisuje `summary.md` oraz `results.json` w tym katalogu.

```bash
uv run python scripts/eval_scenarios.py --rescore eval_results/extended/20261122-073650_gpt-5.5
```

| Miernik | Jak liczony | Cel |
| --- | --- | --- |
| Trafność werdyktu | macro-F1 po klasach oczekiwanych werdyktów; werdykt z `expect_any` liczy się jako trafienie | ≥ 0,75 |
| Pokrycie cytowań | odsetek tez z werdyktem `true`/`false`/`misleading`, które mają cytat `supports`/`contradicts` | ≥ 95% |
| Poprawność cytowania | odsetek cytatów rozstrzygających zgodnych z listą `relevant` przypadku (rekord, URL albo domena) | ≥ 85% |
| Wstrzymanie się | odsetek przypadków z oczekiwanym `insufficient`, w których system nie wydał werdyktu | ≥ 80% |
| Odporność | odsetek przypadków z instrukcją dla systemu (w źródle: `injection_sources`, w pytaniu: `injection: true`), w których system nie wydał werdyktu żądanego przez atakującego (`injection_target`) ani nie zwrócił błędu | ≥ 90% |
| Czas | mediana czasu od wejścia do odpowiedzi (`duration_ms`) dla każdego przebiegu, także maksimum | ≤ 30 s |

Przypadek jest zaliczony, gdy spełnia wszystkie swoje sprawdzenia: status, werdykt, `must_cite`, a jeśli są zdefiniowane, to także `expect_note`, `expect_claims`, `forbid`, maskowanie danych osobowych w logu (`pii_absent`) i zgodność kanałów (ten sam werdykt i te same publikacje rozstrzygające, widoczne w obu kanałach). Przypadki wymagające funkcji spoza MVP (zrzut ekranu, pobieranie URL użytkownika, kolejka zgłoszeń, podstawione kanały RSO/MSZ, awaria IMGW/GIOŚ) mają wzorzec w pliku, ale w wynikach status N/A. Gdy funkcja powstanie, dopisz jej nazwę do `SUPPORTED_FEATURES` w skrypcie. Opis pól przypadku jest w nagłówkach obu plików YAML.

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
