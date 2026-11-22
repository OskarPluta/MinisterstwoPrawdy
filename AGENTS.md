# AGENTS.md

Wskazówki dla agentów i nowych osób w zespole. Ogólny obraz: [`README.md`](README.md), powiązania modułów: [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md).

## Mapa repozytorium

| Ścieżka | Co posiada |
| --- | --- |
| `backend/crawler/` | Pakiet `crawler` (FaktBot): konfiguracja źródeł `sources.yaml`, pobieranie, ekstrakcja, zapis `data/crawl/`. Nie zna bazy ani RAG. |
| `backend/rag/` | Pakiet `rag`: konwersja danych crawlera, ingest do PostgreSQL, wyszukiwanie, LLM, weryfikacja. Połączenie i migracje w `rag/db.py`, sam SQL w `database/`. |
| `database/` | Pakiet `database`: cały SQL (`src/database/schema.sql`, `src/database/queries/<grupa>.sql`) i `query()`/`schema()`, które go czytają. Bez połączenia z bazą i bez zależności. |
| `backend/rag/tests/fixtures/` | Sztuczny korpus (domena `.test`) i przypadki ewaluacyjne. |
| `backend/intake/` | Pakiet `intake`: materiał od użytkownika (linki, YouTube, obrazy, PDF) → `rag.attachments.Attachment`. Bezpieczne pobieranie (SSRF, robots.txt), OCR, metadane. Nie wydaje werdyktów. |
| `backend/api/` | Pakiet `api`: FastAPI pod `/api` (`health`, `verify`, `verify/upload`, `checks/{id}` (usunięcie, udostępnienie), `shares/{id}`, `sources`, `stats`, `export` dla analityka z tokenem). Tylko adaptuje HTTP do `intake` i `rag.verify.Verifier`, bez własnej logiki. |
| `backend/discord_bot/` | Pakiet `discord-bot`: kanał Discord (`/sprawdz`, `/zrodla`, wzmianki, DM). Adaptuje Discord do `rag.verify.Verifier` (lub `DISCORD_API_URL`), bez własnej logiki. |
| `frontend/` | Interfejs WWW: Bun + React + TS. `server.ts` serwuje `src/index.html` i przekazuje `/api/*` do `API_URL`. Własne zależności (`bun install`), poza workspace uv. |
| `admin-backend/` | Pakiet `admin`: API panelu administracyjnego pod `/admin/api`, **osobny proces** (127.0.0.1:8001). Logowanie jednym hasłem, historia (zamaskowana), koszty, kategorie błędów, alerty + webhook Discord, źródła, domeny weryfikatorów, linki. Logika danych w `rag.admin_db`, `rag.outcomes`, `rag.alerts`. |
| `admin-frontend/` | Panel administracyjny: Bun + React + TS (port 3001), przekazuje `/admin/api/*` do `ADMIN_API_URL`. Importuje system projektowy z `../frontend/src/design/`; wtyczka `dedupe-react.ts` pilnuje jednej kopii Reacta. |
| `scripts/` | `dev.sh` (cały stos naraz: baza, API, strona, panel), `eval_retrieval.py` (bezpłatna), `eval_verify.py` i `eval_scenarios.py` (płatne, wołają LLM; zestawy `--suite scenarios` i `extended` z miernikami MVP, wyniki w `eval_results/`). |
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
uv run admin                     # API panelu na 127.0.0.1:8001 (wymaga ADMIN_PASSWORD i ADMIN_SECRET)
scripts/dev.sh                   # wszystko naraz; --no-admin | --admin-only | --no-db
uv run discord-bot --help        # kanał Discord (wymaga DISCORD_BOT_TOKEN w `.env`)
uv run intake read <plik|url>    # co intake odczytuje z materiału (obraz = płatne wywołanie LLM, chyba że --ocr none)

cd frontend && bun install       # zależności frontu
bun run dev | typecheck | build | test  # w frontend/ (w admin-frontend/ bez test)
```

- Polecenia Pythona uruchamiaj z katalogu głównego, polecenia Bun z `frontend/` albo `admin-frontend/`.
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
- **Gdy ustawiony jest klucz Jev** (`JEV_API_KEY`, `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` albo `AI_GATEWAY_API_KEY`), werdykt i rolę każdego fragmentu (`supports`/`contradicts`/`context`/`unrelated`) wybiera Jev (`rag/jev.py`), a LLM tylko wyodrębnia twierdzenia i czyta obrazy. Fragmenty `unrelated` nie są cytowane. Jev nie pisze tekstu, więc wyjaśnienie składa kod (`verify.explain`) ze stałych zdań i pól katalogu. Awaria Jev oznacza, że to twierdzenie rozstrzyga LLM. Jev ocenia też frazy wypowiedzi użytkownika (`Claim.fragments` → `ClaimResult.quote`/`fragments`: `correct`/`wrong`/`unchecked`); cytat pokazujemy tylko, gdy dosłownie występuje w wiadomości lub czytelnym materiale, a kolory tylko przy werdykcie `true`/`false`/`misleading` (przy `true` nic nie jest `wrong`). Fraza użytkownika idzie do Jev w `state`, nigdy w `instructions`.
- **Jeden werdykt, nowsze dane wygrywają.** Gdy cytowane źródła są za i przeciw, a jedna strona jest nowsza (`verify.newer_side`), werdykt to `true`/`false` tej strony, nawet wbrew ocenie Jev lub modelu. Ekstrakcja nie rozbija jednego zdania na założenie i twierdzenie.
- **Siłę dowodów liczy kod** (`assess_strength` w `rag/verify.py`) z zachowanych cytatów: liczba niezależnych źródeł (przedruk = źródło oryginału), rodzaj źródła, aktualność, zgodność. Ocena modelu (`evidence_strength`) może ją tylko obniżyć i jest pokazywana jako ocena modelu. Każda przesłanka trafia do `strength.reasons`. Gdy decyduje Jev, pokazywana siła to pewność Jev dla końcowego werdyktu (`strength.confidence`, `level_pl` np. „87%”), a przesłanki kodu zostają w `reasons`.
- **Flagę `known_disinformation` (znana narracja dezinformacyjna) ustawia kod** tylko przy zachowanym cytacie `contradicts` ze źródła `source_type: factcheck`. Cytat samej narracji nigdy nie potwierdza twierdzenia.
- **Wyszukiwanie na żywo (`rag/live.py`) idzie tylko przez `crawler.fetcher.Fetcher`** (te same reguły co crawler: robots.txt, ≥3 s na host, wycofanie) i tylko do zaufanych źródeł. Wyniki trafiają do bazy przez `ingest_corpus`, więc linki dalej pochodzą z `citation_url`.
- **Materiał od użytkownika (strona, zrzut ekranu, PDF, film) to przedmiot sprawdzenia, a nie dowód.** Nie trafia do bazy dowodów, nie jest cytowany ani linkowany, a jego domena nie wpływa na werdykt. Model widzi go tylko jako escapowany blok `<attachment>`. Nieczytelny materiał daje status `unreadable` albo ograniczenie, nigdy werdykt.
- **Pobieranie linków od użytkownika ma reguły crawlera** (robots.txt, uczciwy User-Agent z mailem, bez obchodzenia zabezpieczeń) **i ochronę SSRF**: tylko publiczne adresy IP, porty 80/443, sprawdzenie po każdym przekierowaniu. `allow_private` tylko w testach.
- **SQL tylko jako nazwane zapytania** w `database/src/database/queries/<grupa>.sql` (`-- name: <nazwa>`), wywoływane przez `from database import query; query("<grupa>.<nazwa>")`. Wartości przekazuje się tylko jako `%(nazwa)s`. Nie składaj SQL f-stringiem.
- **Zmiana schematu** (`database/src/database/schema.sql`) wymaga podbicia `SCHEMA_VERSION` i dodania kroku migracji w `rag/db.py:connect`.
- **Frontend nie renderuje tekstu modelu jako HTML ani Markdown** i nie tworzy linków z tekstu. Linki pochodzą tylko z `citation.url`. Ocena `insufficient` nigdy nie jest czerwona.
- **Dane strukturalne (ostrzeżenia IMGW, pomiary synop i GIOŚ, rekordy ClaimReview z Google Fact Check) nie są cytowane dosłownie.** Renderuje je stały szablon (ClaimReview: `verify.claimreview_text`) i są pokazywane jako „Dane ze źródła”.
- **Kanały mają limity żądań** (`rag/ratelimit.py`: na klienta na minutę i godzinę oraz dzienny limit procesu, `PRAWDOWID_RATE_*`). API ufa `X-Forwarded-For` tylko od lokalnego proxy (`PRAWDOWID_TRUSTED_PROXIES`), a proxy Bun ustawia ten nagłówek samo, nigdy nie przekazuje wartości z przeglądarki.
- **Udostępniony wynik (`/wynik/<share_id>`) jest tylko do odczytu.** Pochodzi z `checks.result_json`, którego nic nie aktualizuje. `share_id` to osobny losowy klucz, a `check_id` (klucz do usunięcia) nigdy nie wychodzi przez udostępnienie. Link żyje tyle co zapis sprawdzenia.
- **Tekst użytkownika jest maskowany w logu `checks`.** Treści źródeł nie są maskowane. Log ma retencję (`PRAWDOWID_CHECKS_RETENTION_DAYS`, domyślnie 30 dni) i można go usunąć po `check_id` (`DELETE /api/checks/{check_id}`).
- **Eksport (CSV/JSON) wypuszcza tylko pola z listy dozwolonych** (`rag/export.py`, `frontend/src/export.ts`). Nigdy `check_id` (klucz usunięcia; w eksporcie analityka zastępuje go skrót `check_ref`), treści wiadomości użytkownika (`input_masked`) ani pól wewnętrznych. Każda komórka CSV zaczynająca się od `= + - @`, tabulatora lub CR dostaje apostrof. `GET /api/export` działa tylko z `PRAWDOWID_ADMIN_TOKEN`.
- **Panel administracyjny widzi tylko zamaskowany tekst** z `checks`, a **alerty i wiadomości webhooka nigdy nie zawierają tekstu użytkownika ani modelu**. Alert składa kod z kategorii, nazwy wyjątku i liczników.
- **Kategorię wyniku sprawdzenia liczy kod** (`rag/outcomes.py:classify`), nie model. Każde sprawdzenie trafia do `checks`, także to, które nie doszło do `Verifier` (`record_failure`). Wyjątek: `PRAWDOWID_CHECKS_RETENTION_DAYS=0` wyłącza log całkiem.
- **Panel niczego nie usuwa z bazy dowodów.** Wyłączenie źródła (`sources.enabled`) i wycofanie dokumentu (`evidence.withdrawn_at`) tylko zdejmują je z wyszukiwania i podbijają `corpus_version`. Import nie cofa decyzji admina: nie nadpisuje `enabled`/`admin_*` i nie przywraca wycofanej tożsamości.
- **Trasy panelu istnieją tylko w procesie `admin-backend`** (domyślnie 127.0.0.1), a każda zmiana wymaga sesji i nagłówka `X-Requested-With: admin-panel`. Nie dodawaj tras administracyjnych do `backend/api`.
- Czas w bazie jest w UTC (ISO 8601, porównywany w Pythonie), a użytkownikowi pokazywany w strefie Europe/Warsaw. Wyjątek: filtry zakresu logu w panelu (`checks.page`, `checks.in_range`) porównują tekst `created_at` w SQL, co działa, bo `utcnow_iso` ma stały format.
- Testy nie korzystają z sieci ani z prawdziwego LLM. Korpus testowy używa domeny `.test`.

## Gdzie zmienić X

| Chcę… | Zmień | Uwagi |
| --- | --- | --- |
| dodać źródło | `backend/crawler/sources.yaml` | Źródło musi mieć licencję (reguła `licenses` albo pole `license`), a wpis trzeba dopisać do `docs/DANE_I_LICENCJE.md`. Bez zmian w kodzie. Jeśli źródło ma API, użyj `json_api` (stronicowanie, szczegóły rekordu: crawler README). Instrukcja: [crawler README](backend/crawler/README.md#dodanie-źródła). Nowy `doc_type` zmapuj w `ARTICLE_KINDS` w `rag/crawler_import.py`. |
| dodać rodzaj rekordu lub szablon strukturalny | `rag/crawler_import.py` (konwerter pól, jak `_meteo`/`_hydro`), `rag/chunking.py` (`render_structured`, `REQUIRED_FIELDS`), `rag/search.py` (`TIMED_KINDS`, jeśli rekord ma okres ważności) | Dodaj przykład do `fake_corpus.json` i test. |
| zmienić pytania do Jev (werdykt, role fragmentów) | `rag/jev.py` (`VERDICT_CRITERIA`, `ROLE_CRITERIA`, `build_request`, `parse_answers`, `PROVIDERS`) | Testy bez sieci: `test_jev.py` (`httpx.MockTransport`). |
| zmienić prompty | `rag/llm.py` (`EXTRACT_SYSTEM`, `VERIFY_SYSTEM`, schematy Pydantic) | Wspólne dla PCSS, OpenAI i Anthropic. Sprawdź przez `eval_verify.py --only ...`. |
| zmienić reguły werdyktu, siłę dowodów, notatki, render | `rag/verify.py` (`REQUIRED_ROLES`, `_enforce`, `assess_strength`, `PRIMARY_SOURCE_TYPES`, `_notes`, `render_markdown`, `VERDICT_PL`) | Testy: `test_verify.py`. |
| zmienić starsze vs nowsze dowody (pierwszeństwo nowszych, konflikt, etykiety wieku cytatów) | `rag/search.py` (`same_matter`, `supersessions`), `rag/verify.py` (`_prefer_newer`, `outdated_basis`, `_outdated`, `recency`, `_age_notes`, `RECENCY_PL`) | Etykieta wieku idzie przez API (`Citation.recency`, `recency_pl`) do `frontend/src/api.ts` i `design/extra.tsx`. Testy: `test_search.py`, `test_verify.py`. |
| zmienić wyszukiwanie lub fuzję | `rag/search.py` (`Retriever`, `fts_query`, `STOPWORDS`, `RRF_K`, `Prefer`, `SOFT_WEIGHT`, `in_period`), `rag/areas.py` (województwa, kraj) | Porównaj wyniki przed i po: `scripts/eval_retrieval.py` (`--no-prefer` wyłącza miękkie listy obszaru i okresu; przypadki z tagiem `context`). |
| zmienić wykrywanie przedruków | `rag/reprints.py` (`JACCARD_MIN`, `CONTAINMENT_MIN`, `MIN_SHINGLES`, `SHINGLE_WORDS`) | Wynik w `reprint_signatures.reprint_of`, czytany jako `syndicated_from`, gdy rekord go nie podaje. Testy: `test_reprints.py`. |
| zmienić wyszukiwanie na żywo | `rag/live.py` (`SOURCES`, `BUDGET_S`, `needs_live`); domeny weryfikatorów: tabela `trusted_domains` (panel `/domeny`), lista startowa w `rag/trusted.py` | Testy: `test_live.py` na nagranych odpowiedziach w `tests/fixtures/live/`. Nowe nagranie: prawdziwa odpowiedź, bez kluczy w pliku. |
| zmienić podział na fragmenty | `rag/chunking.py` (`MAX_TOKENS`, `split_section`, `estimate_tokens`) | Potem ponowny `ingest` na świeżej bazie. |
| usunąć treść (wniosek wydawcy, brak licencji) | `backend/crawler/removed.yaml`, potem `crawler forget` i `rag purge` | Procedura: `docs/DANE_I_LICENCJE.md#procedura-usunięcia`. Testy: `test_db.py`, `test_crawler_offline.py`. |
| zmienić schemat bazy | `database/src/database/schema.sql`, `rag/db.py` (`SCHEMA_VERSION`, migracja) | Zapytania w `database/src/database/queries/`. |
| dodać polecenie CLI | `rag/cli.py` (Typer) albo `crawler/cli.py` (argparse) | |
| dodać dostawcę LLM | `rag/llm.py`: podklasa `_StructuredLLM` z `_parse` oraz `make_llm` | |
| dodać moduł backendu (API, kanał np. komunikator) | nowy `backend/<nazwa>/` z `pyproject.toml` i `src/<nazwa>/`, dopisany do `dependencies` i `[tool.uv.sources]` w głównym `pyproject.toml` (`backend/*` jest już w `members`; katalog poza `backend/` dopisz też tam, jak `admin-backend` i `database`) | Wołaj `rag.verify.Verifier(Retriever(conn, get_embedder(...)), make_llm()).verify(text)`, tak jak `rag/cli.py:verify`. Nie duplikuj logiki weryfikacji. API wystawiaj pod prefiksem `/api` (tak proxuje frontend). |
| zmienić interfejs WWW | `frontend/src/` (strony w `pages/`, wynik w `result.tsx`, typy API w `api.ts`) | Komponenty i tokeny systemu Ministerstwo Prawdy są w `src/design/`. `tokens.css` i `components.css` to kopie 1:1, więc poprawki wpisuj do `extra.css`. Backend wołaj przez `fetch("/api/...")`. Sprawdź przez `bun run typecheck`. |
| zmienić eksport CSV/JSON | `rag/export.py` (`CSV_COLUMNS`, `EXPORT_VERSION`, pola rekordu), `database/queries/checks.sql` (`export`), `frontend/src/export.ts` (pobieranie pojedynczego wyniku) | Kolumny i pola są lustrzane w obu plikach. Zmiana kształtu JSON wymaga podbicia `EXPORT_VERSION`. Testy: `test_export.py`, `test_api.py`, `cd frontend && bun test`. |
| zmienić odpowiedź API | `backend/api/src/api/app.py` (`public_result`) i `frontend/src/api.ts` | Typy w `api.ts` odwzorowują dataclassy z `rag/verify.py` i `rag/attachments.py`. Zmieniaj je razem. Testy: `backend/api/tests/test_api.py`. |
| dodać rodzaj materiału od użytkownika (np. audio) | `backend/intake/src/intake/` (nowy czytnik zwracający `Attachment`, wpięty w `core.Intake`), `rag/attachments.py` (`KIND_PL`) | Każda awaria to ograniczenie na `Attachment`, nie wyjątek. Testy bez sieci: `backend/intake/tests/`. |
| zmienić odczyt obrazów (OCR) | `intake/ocr.py`, prompt `IMAGE_SYSTEM` i `ImageTranscript` w `rag/llm.py` | `PRAWDOWID_OCR=llm|tesseract|none`. |
| zmienić kategorie wyników sprawdzeń | `rag/outcomes.py` (`OUTCOMES`, `classify`, `ALERT_OUTCOMES`) | Kategorie stoją w kolumnie `checks.outcome`; nowa kategoria dla starych wierszy wymaga kroku migracji. Testy: `test_admin_db.py`. |
| zmienić reguły alertów lub powiadomienia | `rag/alerts.py` (zapis, deduplikacja), `admin-backend/src/admin/notifier.py` (reguły okresowe, webhook Discord) | Bez tekstu użytkownika w treści. Testy: `admin-backend/tests/test_admin.py` (webhook przez `httpx.MockTransport`). |
| zmienić panel administracyjny | API: `admin-backend/src/admin/app.py` (+ `reports.py`, `links.py`), SQL i operacje: `rag/admin_db.py` i `database/src/database/queries/`; UI: `admin-frontend/src/` (strony w `pages/`, typy w `api.ts`, komponenty w `ui.tsx`) | Typy w `admin-frontend/src/api.ts` odwzorowują JSON z `app.py`. Zmieniaj je razem. Sprawdź `uv run pytest admin-backend` i `bun run typecheck` w `admin-frontend/`. |
