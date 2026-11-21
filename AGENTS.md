# AGENTS.md

Wskazówki dla agentów i nowych osób w zespole. Ogólny obraz: [`README.md`](README.md), powiązania modułów: [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md).

## Mapa repozytorium

| Ścieżka | Co posiada |
| --- | --- |
| `backend/crawler/` | Pakiet `crawler` (FaktBot): konfiguracja źródeł `sources.yaml`, pobieranie, ekstrakcja, zapis `data/crawl/`. Nie zna bazy ani RAG. |
| `backend/rag/` | Pakiet `rag`: konwersja danych crawlera, ingest do PostgreSQL, wyszukiwanie, LLM, weryfikacja. Cały SQL jest w `src/rag/database/`. |
| `backend/rag/tests/fixtures/` | Sztuczny korpus (domena `.test`) i przypadki ewaluacyjne. |
| `frontend/` | Interfejs WWW: Bun + React + TS. `server.ts` serwuje `src/index.html` i przekazuje `/api/*` do `API_URL`. Własne zależności (`bun install`), poza workspace uv. |
| `scripts/` | `eval_retrieval.py` (bezpłatna), `eval_verify.py` (płatna, woła LLM). |
| `data/snapshot/` | Zamrożony zrzut crawla w git. Nie nadpisuj go przypadkiem przebiegiem crawlera. |
| `docs/task/` | Treść konkursu. **Nie edytować.** |
| `docs/notes/` | Notatki badawcze (plany, źródła). Nie opisują kodu. |

## Polecenia

```bash
uv sync                          # instaluje oba moduły (workspace uv)
uv run pytest                    # wszystkie testy; DB z TEST_DATABASE_URL lub DATABASE_URL
uv run rag --help                # ingest | reembed | stats | search | verify
uv run crawler --help            # once | run | status

cd frontend && bun install       # zależności frontu
bun run dev | typecheck | build  # w frontend/
```

- Polecenia Pythona uruchamiaj z katalogu głównego, polecenia Bun z `frontend/`.
- W testach i pracy offline używaj `--embedder hash`. Domyślny jest `e5`.
- Nie uruchamiaj bez potrzeby `rag verify` ani `scripts/eval_verify.py`, bo kosztują. Nie uruchamiaj też crawla na żywo, bo odpytuje prawdziwe serwisy.
- Linter nie jest skonfigurowany ani wymagany.

## Niezmienniki (nie łamać)

- **Crawler nigdy nie obchodzi zabezpieczeń.** robots.txt jest sprawdzany przy każdym adresie i każdym przekierowaniu. Odstęp to co najmniej 3 s na host. Po 429/503/403 crawler się wycofuje. CAPTCHA lub wyzwanie antybotowe daje status `blocked`. Żadnych proxy, stealth, zmiany User-Agenta ani logowania. Tych reguł celowo nie da się wyłączyć w konfiguracji.
- **Crawler wymaga maila kontaktowego** w User-Agencie (`FaktBot/<wersja> (+mail)`) i bez niego nie startuje.
- **`documents.jsonl` jest tylko dopisywany.** Linii się nie zmienia. Nowa wersja powstaje, gdy zmieni się hasz `content`.
- **W bazie rekordy są wersjonowane, a nie nadpisywane.** Tożsamość to `(source_id, upstream_id)`. Stara wersja zostaje, ale wypada z wyszukiwania. `ingest` musi pozostać idempotentny.
- **Cytaty egzekwuje kod.** Model widzi tylko listę fragmentów. Identyfikatory spoza listy są odrzucane, a werdykt bez cytatu o właściwej roli spada do `insufficient`. Linki pochodzą wyłącznie z `citation_url` w bazie, nigdy z tekstu modelu.
- **Brak dowodów oznacza `insufficient`, a nie `false`.**
- **SQL tylko jako nazwane zapytania** w `database/queries/<grupa>.sql` (`-- name: <nazwa>`), wywoływane przez `query("<grupa>.<nazwa>")`. Wartości przekazuje się tylko jako `%(nazwa)s`. Nie składaj SQL f-stringiem.
- **Zmiana schematu** (`database/schema.sql`) wymaga podbicia `SCHEMA_VERSION` i dodania kroku migracji w `db.connect`.
- **Dane strukturalne (ostrzeżenia IMGW, pomiary synop i GIOŚ) nie są cytowane dosłownie.** Renderuje je stały szablon i są pokazywane jako „Dane ze źródła”.
- **Tekst użytkownika jest maskowany w logu `checks`.** Treści źródeł nie są maskowane.
- Czas w bazie jest w UTC (ISO 8601, porównywany w Pythonie), a użytkownikowi pokazywany w strefie Europe/Warsaw.
- Testy nie korzystają z sieci ani z prawdziwego LLM. Korpus testowy używa domeny `.test`.

## Gdzie zmienić X

| Chcę… | Zmień | Uwagi |
| --- | --- | --- |
| dodać źródło | `backend/crawler/sources.yaml` | Bez zmian w kodzie. Jeśli źródło ma API, użyj `json_api` (stronicowanie, szczegóły rekordu: crawler README). Instrukcja: [crawler README](backend/crawler/README.md#dodanie-źródła). Nowy `doc_type` zmapuj w `ARTICLE_KINDS` w `rag/crawler_import.py`. |
| dodać rodzaj rekordu lub szablon strukturalny | `rag/crawler_import.py` (konwerter pól, jak `_meteo`/`_hydro`), `rag/chunking.py` (`render_structured`, `REQUIRED_FIELDS`), `rag/verify.py` (`TIMED_KINDS`, jeśli rekord ma okres ważności) | Dodaj przykład do `fake_corpus.json` i test. |
| zmienić prompty | `rag/llm.py` (`EXTRACT_SYSTEM`, `VERIFY_SYSTEM`, schematy Pydantic) | Wspólne dla OpenAI i Anthropic. Sprawdź przez `eval_verify.py --only ...`. |
| zmienić reguły werdyktu, notatki, render | `rag/verify.py` (`REQUIRED_ROLES`, `_enforce`, `_notes`, `render_markdown`, `VERDICT_PL`) | Testy: `test_verify.py`. |
| zmienić wyszukiwanie lub fuzję | `rag/search.py` (`Retriever`, `fts_query`, `STOPWORDS`, `RRF_K`) | Porównaj wyniki przed i po: `scripts/eval_retrieval.py`. |
| zmienić podział na fragmenty | `rag/chunking.py` (`MAX_WORDS`, `split_section`) | Potem ponowny `ingest` na świeżej bazie. |
| zmienić schemat bazy | `rag/database/schema.sql`, `rag/db.py` (`SCHEMA_VERSION`, migracja) | Zapytania w `database/queries/`. |
| dodać polecenie CLI | `rag/cli.py` (Typer) albo `crawler/cli.py` (argparse) | |
| dodać dostawcę LLM | `rag/llm.py`: podklasa `_StructuredLLM` z `_parse` oraz `make_llm` | |
| dodać moduł backendu (API, kanał np. komunikator) | nowy `backend/<nazwa>/` z `pyproject.toml` i `src/<nazwa>/`, dopisany do `dependencies` i `[tool.uv.sources]` w głównym `pyproject.toml` | Wołaj `rag.verify.Verifier(Retriever(conn, get_embedder(...)), make_llm()).verify(text)`, tak jak `rag/cli.py:verify`. Nie duplikuj logiki weryfikacji. API wystawiaj pod prefiksem `/api` (tak proxuje frontend). |
| zmienić interfejs WWW | `frontend/src/` | Backend wołaj przez `fetch("/api/...")`. Sprawdź przez `bun run typecheck`. |
