# Architektura Prawdowida

Opis powiązań modułów i decyzji, których nie widać od razu w kodzie. Szczegóły modułów: [crawler](../backend/crawler/README.md), [rag](../backend/rag/README.md).

## Przepływ

```text
sources.yaml ─► crawler ─► data/crawl/documents.jsonl + crawl_log.jsonl      (pliki, tylko dopisywane)
                                   │
                                   ▼
               rag ingest: crawler_import → walidacja → chunking → embed → db
                                   │
                                   ▼
               PostgreSQL: sources, evidence (wersje), passages (FTS + wektor), checks (log)
                                   │
tekst ─► Verifier: ekstrakcja (LLM) → search → werdykt (LLM) → _enforce/_notes → Markdown/JSON
```

- Crawler i RAG łączy wyłącznie katalog z plikami. Crawler nie zna bazy, a RAG nie importuje kodu crawlera.
- Kroki są uruchamiane ręcznie. Nie ma harmonogramu, który po crawlu sam odświeża bazę.
- Jedynym interfejsem jest dziś CLI. Przyszłe API/WWW powinno wołać `rag.verify.Verifier`.

## Kontrakt crawler → rag

Kontrakt realizuje [`crawler_import.py`](../backend/rag/src/rag/crawler_import.py). Czyta **wszystkie** linie `documents.jsonl` (wszystkie wersje, w kolejności pliku) i ostatni wpis `crawl_log.jsonl` dla każdego źródła. Niepełna ostatnia linia jest pomijana, bo crawler może właśnie dopisywać.

Pola `documents.jsonl`, które mają znaczenie:

| Pole crawlera | Trafia do | Uwagi |
| --- | --- | --- |
| `source` | `source_id` | |
| `url` | `upstream_id`, `citation_url` | Dla `json_api` URL ma postać `endpoint#id`, czyli wskazuje API, a nie stronę dla człowieka. |
| `title` | `title` | Gdy jest `null`, w jego miejsce trafia URL. |
| `content` | `sections` (artykuł) albo typowane `fields` (ostrzeżenia) | |
| `doc_type` | `kind` | Patrz niżej. |
| `published_at`, `fetched_at` | to samo, w UTC | Czas bez strefy: `published_at` jest traktowany jako czas warszawski, `fetched_at` jako UTC. |
| `region` | `areas` | |
| `institution` | `sources.publisher` | Z pierwszego dokumentu źródła. |
| `attribution` | `fields.attribution` | Wymóg licencji IMGW. |
| `id`, `version`, `content_hash` | – | RAG liczy własny hasz i własne wersje. |

Rodzaje rekordów:

- **Artykuł** (`komunikat`, `ostrzezenie`, `informacja`, każdy nieznany typ → `article`; `raport` → `report`). Każda niepusta linia staje się akapitem, a pierwsza linia powtarzająca tytuł jest usuwana. Fragmenty są dosłowne i cytowalne.
- **Ostrzeżenie strukturalne** (`ostrzezenie_meteo` → `warning_meteo`, `ostrzezenie_hydro` → `warning_hydro`). Crawler zapisuje rekord API jako linie `klucz: wartość`, a konwerter parsuje je z powrotem do `fields` (`phenomenon`, `level`, `valid_from/to`, `areas`…). Data `9999-…` oznacza „do odwołania”. Bez `phenomenon` i `level` rekord jest odrzucany.
- **Pomiar strukturalny** (`pomiar_synop` → `weather_reading`, `pomiar_powietrza` → `air_quality`). Tak samo jak ostrzeżenia: pola API trafiają do `fields` (temperatura, wiatr, opad, ciśnienie; indeks ogólny i indeksy zanieczyszczeń), czas pomiaru do `observed_at`, a fragment renderuje szablon. Bez `station` (i `index` dla `air_quality`) rekord jest odrzucany.
- **Komunikat RSO** (`ostrzezenie_rso` → `alert`). Tekst komunikatu (pole `content`, wieloliniowe) jest dosłowny i cytowalny jak artykuł, a do tego rekord ma `valid_from/to` i obszar z województw. `alert` jest w `TIMED_KINDS`, więc wygasły komunikat daje notatkę.
- **Źródło** powstaje z pierwszego dokumentu: `publishing_domain` = host bez `www.`, `source_type` = `structured` dla danych z API (ostrzeżenia, RSO, pomiary), w innych przypadkach `official`. Z `crawl_log`: `ok`/`not_modified` to sukces, inny status ustawia `last_error`. `last_success_at` to ostatni udany przebieg, więc późniejsza awaria go nie kasuje.
- Źródła z przebiegami, ale bez żadnego dokumentu, nie trafiają do bazy. `ingest` je tylko wypisuje.

## Model danych (PostgreSQL)

Źródło prawdy: [`schema.sql`](../backend/rag/src/rag/database/schema.sql). DDL jest idempotentny i wykonuje się przy każdym połączeniu.

- **`sources`**: katalog źródeł i stan ostatniego pobrania (`last_success_at`, `last_error`).
- **`evidence`**: wersjonowane rekordy. Tożsamość to `(source_id, upstream_id)`. Nowa treść (hasz kanonicznego JSON pól treści, bez `fetched_at`) daje wersję `n+1`, a poprzednia dostaje `is_current = false`. Ta sama treść co bieżąca daje `unchanged`, a treść równa starszej wersji daje `known_version`.
- **`passages`**: fragmenty z `tsvector` (`fts`) i wektorem (`BYTEA` float32, bez pgvector). Fragmenty starych wersji mają `fts = NULL`, więc znikają z wyszukiwania, ale dawne cytaty da się prześledzić.
- **`checks`**: log każdej weryfikacji z zamaskowanym wejściem.
- **`meta`**: `schema_version` i `corpus_version`. Każdy zapis podbija `corpus_version`, co unieważnia macierz wektorów trzymaną w pamięci.

`ingest` dodatkowo odrzuca rekord, gdy `citation_url` nie jest https albo nie należy do `publishing_domain` źródła (ani jej subdomeny).

## Wyszukiwanie ([`search.py`](../backend/rag/src/rag/search.py))

1. Zapytania: tekst twierdzenia oraz 1–3 zapytania wygenerowane przez LLM, w tym jedno nastawione na sprostowanie.
2. Pełny tekst: tokeny po złożeniu (małe litery, bez diakrytyków, bez stopwords), OR. Długie słowa są skracane do prefiksu (`mazow:*`) zamiast stemmera. Ranking `ts_rank_cd` z wagami tytuł > nagłówek > treść.
3. Gęste: multilingual-e5, pełny skan macierzy NumPy w pamięci, tylko bieżące wersje.
4. Fuzja RRF po wszystkich listach (metody × zapytania), potem limit fragmentów na jeden rekord (różnorodność) i krótka lista dla LLM.
5. Filtry (`area`, okres, rodzaj) istnieją, ale używa ich tylko `rag search` i ewaluacja. **Weryfikator ich nie przekazuje.**

## Weryfikacja ([`verify.py`](../backend/rag/src/rag/verify.py), [`llm.py`](../backend/rag/src/rag/llm.py))

1. Limit długości wejścia. Ekstrakcja twierdzeń (1 wywołanie LLM) zwraca też status `needs_clarification` / `out_of_scope` i flagę próby injection.
2. Dla każdego twierdzenia (z limitem liczby): opinia daje `unverifiable`, a pusta lista wyników daje `insufficient`. W obu przypadkach bez wywołania LLM.
3. Werdykt (1 wywołanie na twierdzenie, równolegle). Model dostaje fragmenty jako escapowane bloki `<evidence id=…>`, bez narzędzi i bez przeglądarki.
4. **Gwarancje po stronie kodu** (`_enforce`, `render_markdown`):
   - identyfikatory spoza listy wyników są usuwane i zapisywane w `invented_ids`,
   - `true` wymaga cytatu `supports`, `false` wymaga `contradicts`, a `misleading` jednego z nich. W przeciwnym razie werdykt spada do `insufficient`,
   - `conflict`, gdy cytaty jednocześnie potwierdzają i przeczą,
   - URL-e z tekstu modelu są usuwane, a Markdown escapowany. Linki pochodzą tylko z bazy (`_safe_url`).
5. Notatki: wygasłe ostrzeżenie, nieudana ostatnia aktualizacja źródła, nieaktualne dane strukturalne, instrukcja w treści źródła (zignorowana).
6. Izolacja błędów: awaria jednego twierdzenia daje dla niego `error`, a reszta jest sprawdzana dalej.
7. Log do `checks` z maskowaniem e-maili, IBAN, PESEL, numerów dowodu i telefonów.

## Kluczowe decyzje

| Decyzja | Powód |
| --- | --- |
| Crawler i RAG łączy plik tylko do dopisywania | Audytowalna historia („co instytucja napisała pierwotnie”). Bazę można w każdej chwili odbudować z plików. Moduły rozwijają się niezależnie. |
| Legalność ponad kompletność | Źródło zablokowane zostaje `blocked`, a dane pozyskuje się wnioskiem lub innym kanałem (RSS, API). |
| Model nie jest źródłem prawdy o cytatach | Ogranicza halucynacje: kod weryfikuje każdy cytat i rolę, a linki bierze tylko z bazy. |
| Dane strukturalne renderuje szablon, bez cytowania | Surowe pola API nie są tekstem do cytowania. Szablon daje stabilne, porównywalne zdanie. |
| FTS w Postgresie + e5 + RRF, bez pgvector i rerankera | Przy obecnej skali pełny skan NumPy wystarcza. Mniej infrastruktury. |
| Wspólne prompty i schematy, wymienny dostawca | Zmiana modelu lub dostawcy nie dotyka logiki. |
| SQL w plikach `.sql` z nazwanymi zapytaniami | Jedno miejsce na SQL i brak wstrzyknięć przez składanie tekstu. |
| Testy na `HashEmbedder` i schematach tymczasowych | Szybkie i deterministyczne, bez sieci i bez pobierania modelu. |

## Ograniczenia i ryzyka

- **Halucynacje**: ograniczone kontrolą cytatów, ale wyjaśnienie (`explanation_pl`) pisze model. Rola cytatu też pochodzi od modelu.
- **Stronniczość źródeł**: korpus to wyłącznie komunikaty instytucji. System sprawdza zgodność z nimi, a nie prawdę absolutną. Brak wyniku nie oznacza fałszu.
- **Prywatność**: treści źródeł (także `data/snapshot/` w git) są przechowywane bez maskowania i mogą zawierać nazwiska. Maskowany jest tylko log `checks`.
- **Prompt injection**: tekst użytkownika i fragmenty są escapowane, a podejrzane fragmenty oznaczane. To heurystyka, a nie gwarancja.
- **Dostępność i aktualność**: zależy od ostatniego crawla i `ingest`. Notatka o nieaktualności dotyczy tylko źródeł `structured`. Awaria LLM daje `error`.
- **Znane luki w kodzie**:
  - ostrzeżenia wycofane z API IMGW i RSO zostają bieżące, bo konwerter nie czyta `state.json → active_ids`,
  - pomiary (`imgw-synop`, `gios-indeks-powietrza`) to osobny dokument na stację i godzinę, czyli ok. 3200 dokumentów na dobę przy pracy ciągłej; odmiana nazw („w Łebie”) słabo trafia w prefiksy pełnego tekstu, pomaga e5,
  - szablon `warning_hydro` pokazuje „Rzeka: brak danych”, bo konwerter nie wypełnia `river`,
  - powrót treści A→B→A zostawia bieżącą wersję B (`known_version`),
  - `publishing_domain` dla `www.gov.pl` to `gov.pl`, więc walidacja przepuszcza dowolną subdomenę `*.gov.pl`,
  - atrybucja IMGW trafia do modelu, ale nie do odpowiedzi w Markdown,
  - załączniki (PDF) nie są pobierane, zapisuje się tylko lista linków.
- **Brakuje względem [zadania](task/)**: interfejsu WWW, drugiego kanału, weryfikacji adresu URL od użytkownika, eskalacji do moderatora, korpusu spełniającego minimum konkursu (≥ 100 dokumentów, ≥ 10 domen; sprawdź `rag stats`) i warstwy zapytań na żywo z [`notes/PLAN_OBSLUGI_ZRODEL.md`](notes/PLAN_OBSLUGI_ZRODEL.md).
