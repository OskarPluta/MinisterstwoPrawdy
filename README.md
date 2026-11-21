# Prawdowid

System weryfikacji twierdzeń (dezinformacji) na podstawie **oficjalnych źródeł państwowych**, zadanie konkursowe MSZ.
Użytkownik podaje twierdzenie, a system wyszukuje komunikaty, ostrzeżenia i raporty instytucji publicznych.
Na ich podstawie zwraca po polsku krótki werdykt z klikalnymi cytatami.

```
 oficjalne źródła ──► crawler/ (FaktBot) ──► crawler/data/*.jsonl
                                                 │  prawdowid convert-crawler
                                                 ▼
                                            corpus.json ──► prawdowid ingest ──► PostgreSQL
                                                                                     │
           twierdzenie ──► prawdowid verify ──► wyszukiwanie hybrydowe (BM25 + e5 + RRF)
                                    │
                                    ▼
                    LLM: ekstrakcja twierdzeń → werdykt → kontrola cytatów → odpowiedź PL
```

## Składniki

| Katalog | Co to jest | Dokumentacja |
|---|---|---|
| [`crawler/`](crawler/) | **FaktBot**: „grzeczny” crawler ~25 źródeł (gov.pl, Policja, RCB, IMGW API, PSP, GIS, MON, urzędy morskie…). Adaptery `rss`, `html_list`, `sitemap`, `json_api`, `static_page`. Zapis do `crawler/data/` z pełną historią wersji. | [`crawler/README.md`](crawler/README.md), [`crawler/docs/`](crawler/docs/) |
| [`backend/`](backend/) | **RAG** (pakiet `prawdowid`, CLI `uv run prawdowid`): import danych z crawlera, PostgreSQL z wyszukiwaniem pełnotekstowym, embeddingi multilingual-e5, fuzja RRF, weryfikator LLM (OpenAI/Anthropic) z kontrolą cytatów po stronie kodu. | [`backend/README.md`](backend/README.md) (EN) |
| [`data/`](data/) | Zamrożony zrzut danych z crawlera (98 dokumentów, 2026-11-21) i wygenerowany z niego `corpus.json`. Domyślne wejście `convert-crawler`. | |
| [`fixtures/`](fixtures/), [`tests/`](tests/), [`scripts/`](scripts/) | Sztuczny korpus testowy (domena `.test`), testy pytest, ewaluacja wyszukiwania i werdyktów. | |
| [`docs/`](docs/) | Treść zadania, plan, architektura, instrukcja uruchomienia. | patrz niżej |

## Szybki start

Pełna instrukcja krok po kroku, z rozwiązywaniem problemów: **[`docs/URUCHOMIENIE.md`](docs/URUCHOMIENIE.md)**.

```bash
# 1. Baza danych i zależności RAG (z katalogu głównego repo)
cp .env.example .env                 # ustaw POSTGRES_PASSWORD, DATABASE_URL, OPENAI_API_KEY lub ANTHROPIC_API_KEY
docker compose up -d db
uv sync                              # + `uv sync --extra e5` dla modelu multilingual-e5-small

# 2. Korpus: zrzut z repo (data/) albo świeże dane z crawlera (krok 4)
uv run prawdowid convert-crawler     # data/documents.jsonl → data/corpus.json
uv run prawdowid ingest data/corpus.json --embedder e5
uv run prawdowid stats

# 3. Wyszukiwanie i weryfikacja
uv run prawdowid search "ostrzeżenie hydrologiczne susza" --embedder e5
uv run prawdowid verify "Czy IMGW wydał ostrzeżenie przed suszą hydrologiczną?" --embedder e5

# 4. (opcjonalnie) świeże dane z crawlera
cd crawler && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
export CRAWLER_CONTACT=zespol@example.pl   # obowiązkowy mail kontaktowy w User-Agencie
python crawler.py once --max-items 5 && python crawler.py status
cd .. && uv run prawdowid convert-crawler \
    --documents crawler/data/documents.jsonl --crawl-log crawler/data/crawl_log.jsonl \
    --out data/corpus.json
uv run prawdowid ingest data/corpus.json --embedder e5   # idempotentne: zmienione treści stają się nowymi wersjami
```

Do `ingest`, `search` i `verify` używaj tego samego `--embedder`. Model zmienisz poleceniem `prawdowid reembed`.

## Testy

```bash
uv run pytest                                   # RAG; potrzebuje Postgresa (TEST_DATABASE_URL lub DATABASE_URL), bez bazy testy DB są pomijane
cd crawler && python tests/run_local_test.py    # crawler; test end-to-end offline na lokalnym serwerze
```

Stan po scaleniu gałęzi `crawler` i `rag-initial` do `main` (2026-11-21): 52/52 testów pytest przechodzi, test offline crawlera przechodzi.
Ścieżka crawler → `convert-crawler` → `ingest` → `search` działa. Zrzut z `data/` daje 98 rekordów i 132 fragmenty, a świeży przebieg crawlera z tego dnia 221 rekordów i 330 fragmentów. Ponowny import daje 0 duplikatów.

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) | Architektura całego systemu, przepływ danych, kontrakt crawler ↔ RAG, schemat bazy, potok wyszukiwania i weryfikacji, ograniczenia |
| [`docs/URUCHOMIENIE.md`](docs/URUCHOMIENIE.md) | Instalacja od zera, cykl pracy, testy, ewaluacja, zmienne środowiskowe, rozwiązywanie problemów |
| [`crawler/docs/SPEC.md`](crawler/docs/SPEC.md) | Specyfikacja zachowania crawlera (zasady, adaptery, statusy, CLI) |
| [`crawler/docs/CONFIG.md`](crawler/docs/CONFIG.md) | Wszystkie klucze `sources.yaml`, dodawanie źródła |
| [`crawler/docs/DATA_FORMAT.md`](crawler/docs/DATA_FORMAT.md) | Format plików w `crawler/data/`, JSON Schema |
| [`crawler/RAPORT.md`](crawler/RAPORT.md) | Raport z przebiegu na prawdziwych stronach (statusy źródeł, blokady) |
| [`backend/README.md`](backend/README.md) | RAG: moduły, konwencje SQL, format korpusu (EN) |
| [`docs/plan.md`](docs/plan.md) | Plan implementacji (EN) |
| [`docs/PLAN_OBSLUGI_ZRODEL.md`](docs/PLAN_OBSLUGI_ZRODEL.md) | Plan obsługi źródeł: RAG vs zapytania na żywo, scenariusze testowe |
| [`docs/DODATKOWE_ZRODLA_I_API.md`](docs/DODATKOWE_ZRODLA_I_API.md) | Dodatkowe źródła i API |
| [`docs/task/`](docs/task/) | Pełna treść i regulamin zadania konkursowego |
| [`docs/example-analysis-strajk-wlochy.md`](docs/example-analysis-strajk-wlochy.md) | Przykładowa analiza twierdzenia |

## Bezpieczeństwo i dane

* Sekrety trzymaj tylko w `.env`: plik jest w `.gitignore`, a szablon to `.env.example`. Kluczy API nie commitujemy.
* Crawler nie obchodzi zabezpieczeń. Przestrzega robots.txt i limitu 1 zapytania na 3 s na host, a CAPTCHA i wyzwania antybotowe kończą się statusem `blocked`.
* **Maskowanie danych osobowych w crawlerze jest tymczasowo wyłączone** (`mask_pii: false`), szczegóły w [`crawler/README.md`](crawler/README.md).
