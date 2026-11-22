# AGENTS.md

Wskazówki dla agentów i nowych osób w zespole. Ogólny obraz: [`README.md`](README.md), powiązania modułów: [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md).

## Mapa repozytorium

| Ścieżka | Co posiada |
| --- | --- |
| `backend/crawler/` | Pakiet `crawler` (FaktBot): konfiguracja źródeł `sources.yaml`, pobieranie, ekstrakcja, zapis `data/crawl/`. Nie zna bazy ani RAG. |
| `backend/rag/` | Pakiet `rag`: konwersja danych crawlera, ingest do PostgreSQL, wyszukiwanie, LLM, weryfikacja. Cały SQL jest w `src/rag/database/`. |
| `backend/rag/tests/fixtures/` | Sztuczny korpus (domena `.test`) i przypadki ewaluacyjne. |
| `backend/intake/` | Pakiet `intake`: materiał od użytkownika (linki, YouTube, obrazy, PDF) → `rag.attachments.Attachment`. Bezpieczne pobieranie (SSRF, robots.txt), OCR, metadane. Nie wydaje werdyktów. |
| `backend/api/` | Pakiet `api`: FastAPI pod `/api` (`health`, `verify`, `verify/upload`, `checks/{id}` (usunięcie, udostępnienie), `shares/{id}`, `sources`, `stats`, `export` dla analityka z tokenem). Tylko adaptuje HTTP do `intake` i `rag.verify.Verifier`, bez własnej logiki. |
| `backend/discord_bot/` | Pakiet `discord-bot`: kanał Discord (`/sprawdz`, `/zrodla`, wzmianki, DM). Adaptuje Discord do `rag.verify.Verifier` (lub `DISCORD_API_URL`), bez własnej logiki. |
| `frontend/` | Interfejs WWW: Bun + React + TS. `server.ts` serwuje `src/index.html` i przekazuje `/api/*` do `API_URL`. Własne zależności (`bun install`), poza workspace uv. |
| `scripts/` | `eval_retrieval.py` (bezpłatna), `eval_verify.py` i `eval_scenarios.py` (płatne, wołają LLM; zestawy `--suite scenarios` i `extended` z miernikami MVP, wyniki w `eval_results/`). |
| `data/snapshot/` | Zamrożony zrzut crawla w git. Nie nadpisuj go przypadkiem przebiegiem crawlera. |
| `docs/DANE_I_LICENCJE.md` | Podstawa prawna i licencje źródeł, retencja, kontrola dostępu, procedura usunięcia. |
| `docs/task/` | Treść konkursu. **Nie edytować.** |
| `docs/notes/` | Notatki badawcze (plany, źródła). Nie opisują kodu. |

## Polecenia

```bash
uv sync                          # instaluje oba moduły (workspace uv)
uv run pytest                    # wszystkie testy; DB z TEST_DATABASE_URL lub DATABASE_URL
uv run rag --help                # ingest | reembed | stats | search | verify | purge | prune-checks | export | delete-check
uv run crawler --help            # once | run | status | forget
uv run api                       # API na 127.0.0.1:8000 (to samo `.env` co rag)
uv run discord-bot --help        # kanał Discord (wymaga DISCORD_BOT_TOKEN w `.env`)
uv run intake read <plik|url>    # co intake odczytuje z materiału (obraz = płatne wywołanie LLM, chyba że --ocr none)

cd frontend && bun install       # zależności frontu
bun run dev | typecheck | build | test  # w frontend/
```

- Polecenia Pythona uruchamiaj z katalogu głównego, polecenia Bun z `frontend/`.
- W testach i pracy offline używaj `--embedder hash`. Domyślny jest `e5`.
- Nie uruchamiaj bez potrzeby `rag verify` ani `scripts/eval_verify.py`, bo kosztują. `rag verify` i API przy wyniku `insufficient` pytają na żywo Google Fact Check, EUvsDisinfo i gov.pl (`PRAWDOWID_LIVE=0` wyłącza). Nie uruchamiaj też crawla na żywo, bo odpytuje prawdziwe serwisy.
- Linter nie jest skonfigurowany ani wymagany.

## Niezmienniki (nie łamać)

- **Crawler nigdy nie obchodzi zabezpieczeń.** robots.txt jest sprawdzany przy każdym adresie i każdym przekierowaniu. Odstęp to co najmniej 3 s na host. Po 429/503/403 crawler się wycofuje. CAPTCHA lub wyzwanie antybotowe daje status `blocked`. Żadnych proxy, stealth, zmiany User-Agenta ani logowania. Tych reguł celowo nie da się wyłączyć w konfiguracji.
- **Crawler wymaga maila kontaktowego** w User-Agencie (`FaktBot/<wersja> (+mail)`) i bez niego nie startuje.
- **`documents.jsonl` jest tylko dopisywany.** Linii się nie zmienia. Nowa wersja powstaje, gdy zmieni się hasz `content`. Jedyny wyjątek to procedura usunięcia (`crawler forget` według `removed.yaml`).
- **W bazie rekordy są wersjonowane, a nie nadpisywane.** Tożsamość to `(source_id, upstream_id)`. Stara wersja zostaje, ale wypada z wyszukiwania. `ingest` musi pozostać idempotentny. Kasować wolno tylko procedurą usunięcia (`rag purge`), która usuwa całą tożsamość ze wszystkimi wersjami.
- **Bez licencji nie archiwizujemy.** Każde źródło w `sources.yaml` musi mieć `license` (pole albo reguła `licenses` po hoście), inaczej konfiguracja jest odrzucana. Licencja trafia do tabeli `sources` i na stronę „Źródła”. Opis: `docs/DANE_I_LICENCJE.md`.
- **`backend/crawler/removed.yaml` to rejestr usuniętych treści** (każdy wpis z `date` i `reason`). Crawler ich nie zapisuje, a `ingest` i wyszukiwanie na żywo je pomijają.
- **Oznaczenie źródła (`attribution`, `processed_notice`) jest pokazywane pod cytatem** we wszystkich kanałach (WWW, Discord, Markdown). To stały tekst z katalogu źródeł, nie z modelu. Wymaga go regulamin IMGW.
- **Cytaty egzekwuje kod.** Model widzi tylko listę fragmentów. Identyfikatory spoza listy są odrzucane, a werdykt bez cytatu o właściwej roli spada do `insufficient`. Linki pochodzą wyłącznie z `citation_url` w bazie, nigdy z tekstu modelu.
- **Brak dowodów oznacza `insufficient`, a nie `false`.**
- **Siłę dowodów liczy kod** (`assess_strength` w `rag/verify.py`) z zachowanych cytatów: liczba niezależnych źródeł (przedruk = źródło oryginału), rodzaj źródła, aktualność, zgodność. Ocena modelu (`evidence_strength`) może ją tylko obniżyć i jest pokazywana jako ocena modelu. Każda przesłanka trafia do `strength.reasons`.
- **Flagę `known_disinformation` (znana narracja dezinformacyjna) ustawia kod** tylko przy zachowanym cytacie `contradicts` ze źródła `source_type: factcheck`. Cytat samej narracji nigdy nie potwierdza twierdzenia.
- **Wyszukiwanie na żywo (`rag/live.py`) idzie tylko przez `crawler.fetcher.Fetcher`** (te same reguły co crawler: robots.txt, ≥3 s na host, wycofanie) i tylko do zaufanych źródeł. Wyniki trafiają do bazy przez `ingest_corpus`, więc linki dalej pochodzą z `citation_url`.
- **Materiał od użytkownika (strona, zrzut ekranu, PDF, film) to przedmiot sprawdzenia, a nie dowód.** Nie trafia do bazy dowodów, nie jest cytowany ani linkowany, a jego domena nie wpływa na werdykt. Model widzi go tylko jako escapowany blok `<attachment>`. Nieczytelny materiał daje status `unreadable` albo ograniczenie, nigdy werdykt.
- **Pobieranie linków od użytkownika ma reguły crawlera** (robots.txt, uczciwy User-Agent z mailem, bez obchodzenia zabezpieczeń) **i ochronę SSRF**: tylko publiczne adresy IP, porty 80/443, sprawdzenie po każdym przekierowaniu. `allow_private` tylko w testach.
- **SQL tylko jako nazwane zapytania** w `database/queries/<grupa>.sql` (`-- name: <nazwa>`), wywoływane przez `query("<grupa>.<nazwa>")`. Wartości przekazuje się tylko jako `%(nazwa)s`. Nie składaj SQL f-stringiem.
- **Zmiana schematu** (`database/schema.sql`) wymaga podbicia `SCHEMA_VERSION` i dodania kroku migracji w `db.connect`.
- **Frontend nie renderuje tekstu modelu jako HTML ani Markdown** i nie tworzy linków z tekstu. Linki pochodzą tylko z `citation.url`. Ocena `insufficient` nigdy nie jest czerwona.
- **Dane strukturalne (ostrzeżenia IMGW, pomiary synop i GIOŚ, rekordy ClaimReview z Google Fact Check) nie są cytowane dosłownie.** Renderuje je stały szablon (ClaimReview: `verify.claimreview_text`) i są pokazywane jako „Dane ze źródła”.
- **Kanały mają limity żądań** (`rag/ratelimit.py`: na klienta na minutę i godzinę oraz dzienny limit procesu, `PRAWDOWID_RATE_*`). API ufa `X-Forwarded-For` tylko od lokalnego proxy (`PRAWDOWID_TRUSTED_PROXIES`), a proxy Bun ustawia ten nagłówek samo, nigdy nie przekazuje wartości z przeglądarki.
- **Udostępniony wynik (`/wynik/<share_id>`) jest tylko do odczytu.** Pochodzi z `checks.result_json`, którego nic nie aktualizuje. `share_id` to osobny losowy klucz, a `check_id` (klucz do usunięcia) nigdy nie wychodzi przez udostępnienie. Link żyje tyle co zapis sprawdzenia.
- **Tekst użytkownika jest maskowany w logu `checks`.** Treści źródeł nie są maskowane. Log ma retencję (`PRAWDOWID_CHECKS_RETENTION_DAYS`, domyślnie 30 dni) i można go usunąć po `check_id` (`DELETE /api/checks/{check_id}`).
- **Eksport (CSV/JSON) wypuszcza tylko pola z listy dozwolonych** (`rag/export.py`, `frontend/src/export.ts`). Nigdy `check_id` (klucz usunięcia; w eksporcie analityka zastępuje go skrót `check_ref`), treści wiadomości użytkownika (`input_masked`) ani pól wewnętrznych. Każda komórka CSV zaczynająca się od `= + - @`, tabulatora lub CR dostaje apostrof. `GET /api/export` działa tylko z `PRAWDOWID_ADMIN_TOKEN`.
- Czas w bazie jest w UTC (ISO 8601, porównywany w Pythonie), a użytkownikowi pokazywany w strefie Europe/Warsaw.
- Testy nie korzystają z sieci ani z prawdziwego LLM. Korpus testowy używa domeny `.test`.

## Gdzie zmienić X

| Chcę… | Zmień | Uwagi |
| --- | --- | --- |
| dodać źródło | `backend/crawler/sources.yaml` | Źródło musi mieć licencję (reguła `licenses` albo pole `license`), a wpis trzeba dopisać do `docs/DANE_I_LICENCJE.md`. Bez zmian w kodzie. Jeśli źródło ma API, użyj `json_api` (stronicowanie, szczegóły rekordu: crawler README). Instrukcja: [crawler README](backend/crawler/README.md#dodanie-źródła). Nowy `doc_type` zmapuj w `ARTICLE_KINDS` w `rag/crawler_import.py`. |
| dodać rodzaj rekordu lub szablon strukturalny | `rag/crawler_import.py` (konwerter pól, jak `_meteo`/`_hydro`), `rag/chunking.py` (`render_structured`, `REQUIRED_FIELDS`), `rag/verify.py` (`TIMED_KINDS`, jeśli rekord ma okres ważności) | Dodaj przykład do `fake_corpus.json` i test. |
| zmienić prompty | `rag/llm.py` (`EXTRACT_SYSTEM`, `VERIFY_SYSTEM`, schematy Pydantic) | Wspólne dla PCSS, OpenAI i Anthropic. Sprawdź przez `eval_verify.py --only ...`. |
| zmienić reguły werdyktu, siłę dowodów, notatki, render | `rag/verify.py` (`REQUIRED_ROLES`, `_enforce`, `assess_strength`, `PRIMARY_SOURCE_TYPES`, `_notes`, `render_markdown`, `VERDICT_PL`) | Testy: `test_verify.py`. |
| zmienić wyszukiwanie lub fuzję | `rag/search.py` (`Retriever`, `fts_query`, `STOPWORDS`, `RRF_K`) | Porównaj wyniki przed i po: `scripts/eval_retrieval.py`. |
| zmienić wykrywanie przedruków | `rag/reprints.py` (`JACCARD_MIN`, `CONTAINMENT_MIN`, `MIN_SHINGLES`, `SHINGLE_WORDS`) | Wynik w `reprint_signatures.reprint_of`, czytany jako `syndicated_from`, gdy rekord go nie podaje. Testy: `test_reprints.py`. |
| zmienić wyszukiwanie na żywo | `rag/live.py` (`SOURCES`, `TRUSTED_FACTCHECK_DOMAINS`, `BUDGET_S`, `needs_live`) | Testy: `test_live.py` na nagranych odpowiedziach w `tests/fixtures/live/`. Nowe nagranie: prawdziwa odpowiedź, bez kluczy w pliku. |
| zmienić podział na fragmenty | `rag/chunking.py` (`MAX_TOKENS`, `split_section`, `estimate_tokens`) | Potem ponowny `ingest` na świeżej bazie. |
| usunąć treść (wniosek wydawcy, brak licencji) | `backend/crawler/removed.yaml`, potem `crawler forget` i `rag purge` | Procedura: `docs/DANE_I_LICENCJE.md#procedura-usunięcia`. Testy: `test_db.py`, `test_crawler_offline.py`. |
| zmienić schemat bazy | `rag/database/schema.sql`, `rag/db.py` (`SCHEMA_VERSION`, migracja) | Zapytania w `database/queries/`. |
| dodać polecenie CLI | `rag/cli.py` (Typer) albo `crawler/cli.py` (argparse) | |
| dodać dostawcę LLM | `rag/llm.py`: podklasa `_StructuredLLM` z `_parse` oraz `make_llm` | |
| dodać moduł backendu (API, kanał np. komunikator) | nowy `backend/<nazwa>/` z `pyproject.toml` i `src/<nazwa>/`, dopisany do `dependencies` i `[tool.uv.sources]` w głównym `pyproject.toml` | Wołaj `rag.verify.Verifier(Retriever(conn, get_embedder(...)), make_llm()).verify(text)`, tak jak `rag/cli.py:verify`. Nie duplikuj logiki weryfikacji. API wystawiaj pod prefiksem `/api` (tak proxuje frontend). |
| zmienić interfejs WWW | `frontend/src/` (strony w `pages/`, wynik w `result.tsx`, typy API w `api.ts`) | Komponenty i tokeny systemu Ministerstwo Prawdy są w `src/design/`. `tokens.css` i `components.css` to kopie 1:1, więc poprawki wpisuj do `extra.css`. Backend wołaj przez `fetch("/api/...")`. Sprawdź przez `bun run typecheck`. |
| zmienić eksport CSV/JSON | `rag/export.py` (`CSV_COLUMNS`, `EXPORT_VERSION`, pola rekordu), `database/queries/checks.sql` (`export`), `frontend/src/export.ts` (pobieranie pojedynczego wyniku) | Kolumny i pola są lustrzane w obu plikach. Zmiana kształtu JSON wymaga podbicia `EXPORT_VERSION`. Testy: `test_export.py`, `test_api.py`, `cd frontend && bun test`. |
| zmienić odpowiedź API | `backend/api/src/api/app.py` (`public_result`) i `frontend/src/api.ts` | Typy w `api.ts` odwzorowują dataclassy z `rag/verify.py` i `rag/attachments.py`. Zmieniaj je razem. Testy: `backend/api/tests/test_api.py`. |
| dodać rodzaj materiału od użytkownika (np. audio) | `backend/intake/src/intake/` (nowy czytnik zwracający `Attachment`, wpięty w `core.Intake`), `rag/attachments.py` (`KIND_PL`) | Każda awaria to ograniczenie na `Attachment`, nie wyjątek. Testy bez sieci: `backend/intake/tests/`. |
| zmienić odczyt obrazów (OCR) | `intake/ocr.py`, prompt `IMAGE_SYSTEM` i `ImageTranscript` w `rag/llm.py` | `PRAWDOWID_OCR=llm|tesseract|none`. |
