# Uruchomienie Prawdowida od zera

Instrukcja krok po kroku dla nowej osoby w zespole: od czystego komputera do działającego systemu.
System sprawdza twierdzenia na podstawie oficjalnych źródeł państwowych.

System ma dwa niezależne składniki. Każdy ma własne środowisko Pythona:

| Składnik | Katalog | Środowisko | Uruchamianie |
|---|---|---|---|
| **FaktBot** (crawler) | `crawler/` | własny venv `crawler/.venv` + `pip install -r requirements.txt` | `python crawler.py …` **z katalogu `crawler/`** |
| **RAG** (pakiet `prawdowid`) | katalog główny repo | `uv` (`.venv` w katalogu głównym) + PostgreSQL w Dockerze | `uv run prawdowid …` **z katalogu głównego** |

Łączy je plik JSONL: crawler zapisuje `crawler/data/documents.jsonl`, a `prawdowid convert-crawler` zamienia go
na korpus `corpus.json`, który ładuje `prawdowid ingest`.

```
crawler/ ──► crawler/data/documents.jsonl ──► prawdowid convert-crawler ──► data/corpus.json
                                                                                │ prawdowid ingest
                                                                                ▼
                                         prawdowid search / verify ◄──── PostgreSQL
```

Spis treści:

1. [Wymagania](#1-wymagania)
2. [Część A: crawler](#2-część-a-crawler-faktbot)
3. [Część B: RAG](#3-część-b-rag-prawdowid)
4. [Część C: połączenie crawlera z RAG](#4-część-c-połączenie-crawlera-z-rag)
5. [Część D: testy i ewaluacja](#5-część-d-testy-i-ewaluacja)
6. [Typowy cykl pracy](#6-typowy-cykl-pracy)
7. [Zmienne środowiskowe](#7-zmienne-środowiskowe)
8. [Rozwiązywanie problemów (FAQ)](#8-rozwiązywanie-problemów-faq)

> O ścieżkach: bloki zaczynają się od `cd` do właściwego katalogu. `REPO` oznacza katalog główny klonu
> (tam, gdzie leży `pyproject.toml`). Ustaw go raz na sesję, np. `export REPO=~/prawdowid`.

---

## 1. Wymagania

| Co | Wersja | Do czego | Sprawdzenie |
|---|---|---|---|
| Linux lub macOS (WSL też działa) | – | crawler używa `fcntl` (blokada pliku), więc nie działa w natywnym Windows | – |
| Python | **3.11+** dla crawlera, **3.10+** dla RAG (testowane na 3.12) | oba składniki | `python3 --version` |
| [uv](https://docs.astral.sh/uv/) | aktualna | środowisko i zależności RAG | `uv --version` |
| Docker z wtyczką Compose v2 | aktualna | PostgreSQL 16 (`docker compose`) | `docker compose version` |
| Chromium dla Playwright | instalowany przez `playwright install chromium` | crawler renderuje strony list i artykułów | patrz krok A.1 |
| Klucz API OpenAI **lub** Anthropic | – | tylko `prawdowid verify` i `scripts/eval_verify.py` | – |
| Dostęp do internetu | – | crawl, pierwsze pobranie modelu e5 (~0,5 GB), instalacja zależności | – |

Instalacja uv, jeśli go nie masz:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Zamiast Dockera możesz użyć dowolnego PostgreSQL w wersji 14 lub nowszej. Wtedy pomiń `docker compose`
i ustaw `DATABASE_URL` na swoją bazę. Rozszerzenia (np. pgvector) nie są potrzebne.

Klucze API są potrzebne tylko do weryfikacji przez LLM. Crawl, import, wyszukiwanie, testy pytest i
`eval_retrieval.py` działają bez kluczy.

---

## 2. Część A: crawler (FaktBot)

Szczegóły zachowania crawlera: [`crawler/README.md`](../crawler/README.md),
[`crawler/docs/SPEC.md`](../crawler/docs/SPEC.md), [`crawler/docs/CONFIG.md`](../crawler/docs/CONFIG.md),
[`crawler/docs/DATA_FORMAT.md`](../crawler/docs/DATA_FORMAT.md).

### A.1. Instalacja

```bash
cd "$REPO/crawler"
python3 -m venv .venv                 # Python 3.11+; np. python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt       # playwright, httpx, trafilatura, spaCy + model pl_core_news_md
playwright install chromium           # przeglądarka dla Playwright (~150 MB, do ~/.cache/ms-playwright)
```

Na świeżym Linuksie Chromium może nie mieć bibliotek systemowych. Wtedy doinstaluj je (wymaga sudo):

```bash
playwright install --with-deps chromium
```

Model spaCy `pl_core_news_md` instaluje się z `requirements.txt` (bezpośredni link do pliku wheel).
Jest potrzebny tylko źródłom z `mask_pii: true`. Obecnie maskowanie jest **wyłączone** we wszystkich źródłach
(patrz ostrzeżenie w `crawler/README.md`), więc crawler startuje także bez modelu.

Alternatywnie, z uv zamiast pip:

```bash
cd "$REPO/crawler"
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/playwright install chromium
```

### A.2. Mail kontaktowy (obowiązkowy)

Crawler przedstawia się nagłówkiem `User-Agent: FaktBot/1.0 (+mail)`. Bez poprawnego adresu e-mail nie wystartuje
(kod wyjścia 2). Zmienna środowiskowa ma pierwszeństwo przed `settings.contact` w `sources.yaml`:

```bash
export CRAWLER_CONTACT=zespol@example.pl     # wpisz prawdziwy adres zespołu
```

> W `crawler/sources.yaml` jest teraz **tymczasowy** adres (`settings.contact: testtest@gmail.com`).
> Crawler wystartuje więc nawet bez `CRAWLER_CONTACT`, ale przedstawi się tym zastępczym adresem.
> Przed prawdziwym crawlem zawsze ustaw `CRAWLER_CONTACT`.

Crawler **nie czyta pliku `.env`**. Zmienną trzeba wyeksportować w powłoce (np. w `~/.bashrc`) albo wpisać do
`sources.yaml`.

### A.3. Pierwszy, ostrożny crawl

Najpierw sprawdź, jakie źródła są skonfigurowane. `status` nie wymaga maila, nie łączy się z siecią i niczego nie zapisuje:

```bash
cd "$REPO/crawler"
source .venv/bin/activate
python crawler.py status
```

Potem uruchom jeden przebieg na małej próbce. `--max-items N` tymczasowo ogranicza liczbę nowych pozycji
**na źródło**. Nie dotyczy źródeł `json_api` ani `static_page`, które zawsze pobierają całość.
`--source NAZWA` wybiera konkretne źródła (opcję można powtórzyć). Nazwy są w pierwszej kolumnie `status`.

```bash
# jedno-dwa źródła, maks. 3 nowe artykuły na źródło
python crawler.py once --source rcb --source imgw-meteo --max-items 3

# wszystkie włączone źródła, maks. 5 nowych artykułów na źródło (kilka minut: limit 1 zapytanie / 3 s na host)
python crawler.py once --max-items 5

# pełny przebieg (limity z sources.yaml: max_items_per_run, domyślnie 30)
python crawler.py once
```

Uwagi do składni:

* `--source` i `--max-items` podaje się **po** podkomendzie (`once …`). Opcje globalne `--config`,
  `--data-dir` i `-v` podaje się **przed** podkomendą: `python crawler.py -v once --max-items 3`.
* `--source` uruchamia także źródła wyłączone (`enabled: false`). W logu pojawi się wtedy ostrzeżenie.
* `run` przyjmuje `--max-items`, ale nie przyjmuje `--source`.
* Ctrl+C kończy pracę po bieżącym źródle. Drugie Ctrl+C przerywa od razu, a stan i tak zostaje zapisany.

### A.4. Sprawdzenie wyniku

```bash
python crawler.py status
```

Tabela ma kolumny `źródło`, `typ`, `ostatnio`, `status`, `ostatnio nowe`, `dok/wer` (unikalne URL-e / wersje)
i `alarmy`. Pod tabelą są aktywne wycofania hostów i podsumowanie liczby dokumentów.

| `status` | znaczenie | co robić |
|---|---|---|
| `ok` | pobrano, coś nowego lub zmienionego | nic |
| `not_modified` | serwer odpowiedział „bez zmian” | nic |
| `warn` | część pozycji z błędami albo 0 linków na liście (zmieniła się struktura strony?) | sprawdź `crawler.log`, ewentualnie `link_selector` w `sources.yaml` |
| `throttled` | 429/503 albo robots.txt niedostępny; host wycofany (domyślnie 15 min) | czekać, crawler sam ponowi w terminie |
| `blocked` | 403, wyzwanie antybotowe/CAPTCHA albo robots.txt zabrania | **nie obchodzić**; patrz FAQ |
| `error` | wyjątek źródła (np. zły JSON z API) | ślad stosu w `crawler.log` |

Alarmy: `BLOCKED`/`THROTTLED`, `3× błąd` (trzy ostatnie przebiegi z `error`), `cisza >Nh` (brak nowych treści
dłużej niż `silence_alert_hours`), `wyłączone`.

Stan zrzutu w repo możesz obejrzeć bez crawlowania. Ścieżka `--data-dir` liczy się względem katalogu `sources.yaml`:

```bash
python crawler.py --data-dir ../data status
```

### A.5. Gdzie trafiają dane

Domyślnie do `crawler/data/` (`settings.data_dir: data`, względem `sources.yaml`, więc niezależnie od bieżącego
katalogu). Katalog jest w `crawler/.gitignore` i **nie trafia do repozytorium**.

| plik | zawartość |
|---|---|
| `documents.jsonl` | tylko dopisywany, jedna wersja dokumentu na linię (zmieniona treść → nowa linia z `version+1`) |
| `current.json` | indeks `url → {version, content_hash, id}` |
| `state.json` | ETagi, znane adresy, kolejka zaległych, wycofania hostów, statusy źródeł |
| `crawl_log.jsonl` | jedna linia na uruchomienie źródła (`status`, liczniki) |
| `crawler.log` | log tekstowy (dopisywany, bez rotacji) |
| `.lock` | blokada procesu (zostaje na dysku, to normalne) |

Katalog `data/` w **katalogu głównym** repo to co innego. To zamrożony zrzut jednego crawla (98 dokumentów,
2026-11-21) razem z wygenerowanym `data/corpus.json`. Jest w repozytorium, żeby RAG dało się uruchomić bez crawlowania.

### A.6. Praca ciągła

`run` to pętla: każde źródło uruchamia się co `interval_minutes` z `sources.yaml` (np. policja co 10 min,
domyślnie 60 min). Stan przetrwa restart. Na jednym katalogu danych może działać tylko jeden proces `run`/`once`.

```bash
cd "$REPO/crawler"
source .venv/bin/activate
export CRAWLER_CONTACT=zespol@example.pl

# w tmux/screen:
python crawler.py run

# albo w tle, z logiem do pliku:
nohup python crawler.py run > run.out 2>&1 &
echo $! > run.pid
```

Zatrzymanie z dokończeniem bieżącego źródła (SIGTERM działa jak pierwsze Ctrl+C):

```bash
kill "$(cat "$REPO/crawler/run.pid")"
```

Postęp śledzisz przez `python crawler.py status` (działa obok `run`, bo nie bierze blokady) albo
`tail -f data/crawler.log`.

---

## 3. Część B: RAG (`prawdowid`)

Wszystkie polecenia w tej części uruchamiaj **z katalogu głównego repo**. Szczegóły modułów (po angielsku):
[`backend/README.md`](../backend/README.md).

### B.1. Plik `.env`

```bash
cd "$REPO"
cp .env.example .env
```

Uzupełnij w `.env` co najmniej:

```dotenv
POSTGRES_PASSWORD=jakies-haslo
DATABASE_URL=postgresql://prawdowid:jakies-haslo@localhost:5432/prawdowid
OPENAI_API_KEY=sk-...            # albo ANTHROPIC_API_KEY=... i PRAWDOWID_PROVIDER=anthropic
```

Hasło w `DATABASE_URL` musi być takie samo jak `POSTGRES_PASSWORD`. Port musi się zgadzać z `POSTGRES_PORT`.
Zmienne ustawione w powłoce mają pierwszeństwo przed `.env`. `.env` jest w `.gitignore`, więc nigdy go nie commituj.
Pełna lista zmiennych jest w [rozdziale 7](#7-zmienne-środowiskowe).

### B.2. Baza danych

```bash
cd "$REPO"
docker compose up -d db          # PostgreSQL 16 na 127.0.0.1:${POSTGRES_PORT:-5432}, dane w wolumenie pgdata
docker compose ps                # kolumna STATUS powinna pokazać "healthy"
```

`docker compose` czyta `.env` z katalogu głównego. Bez `POSTGRES_PASSWORD` odmówi startu
(`set POSTGRES_PASSWORD in .env`). Starsza instalacja ze skryptem `docker-compose` (v1) przyjmuje te same argumenty.

Zatrzymanie: `docker compose stop db`. Usunięcie bazy razem z danymi: `docker compose down -v`.

### B.3. Zależności Pythona

```bash
cd "$REPO"
uv sync                  # rdzeń + pytest; wystarcza dla embeddera "hash" (bez pobierania modeli)
uv sync --extra e5       # dodatkowo sentence-transformers + torch CPU dla multilingual-e5-small
```

> `uv sync` instaluje **dokładnie** wskazany zestaw. Zwykłe `uv sync` po `uv sync --extra e5` **odinstaluje** e5.
> Jeśli używasz e5, za każdym razem wpisuj `uv sync --extra e5`.

Model `intfloat/multilingual-e5-small` pobiera się automatycznie przy pierwszym użyciu `--embedder e5`
do `~/.cache/huggingface`. Potem możesz pracować offline z `HF_HUB_OFFLINE=1`.

Embedder wybierasz opcją `--embedder`:

| wartość | co to jest | kiedy |
|---|---|---|
| `hash` (domyślny w CLI) | deterministyczny hashing n-gramów znaków, bez pobierania | testy, szybkie próby, brak `--extra e5` |
| `e5` | `intfloat/multilingual-e5-small` | normalna praca (prawdziwe podobieństwo znaczeniowe) |
| inna nazwa | dowolny model sentence-transformers | eksperymenty |

### B.4. Schemat bazy

Osobnego kroku migracji nie ma. Schemat (`backend/database/schema.sql`) tworzy się **automatycznie przy pierwszym
połączeniu** dowolnego polecenia `prawdowid`. Jest idempotentny. Najprościej sprawdzić bazę tak:

```bash
cd "$REPO"
uv run prawdowid stats
```

Na pustej bazie wynik to same zera:

```json
{
  "distinct_records": 0,
  "current_records": 0,
  "evidence_versions": 0,
  "publishing_domains": 0,
  "source_types": 0,
  "passages_searchable": 0
}
```

Każde polecenie przyjmuje `--database-url`, które nadpisuje `DATABASE_URL`.

---

## 4. Część C: połączenie crawlera z RAG

### C.1. Konwersja wyniku crawla na korpus

Uruchom z katalogu głównego, wskazując pliki z `crawler/data/`:

```bash
cd "$REPO"
uv run prawdowid convert-crawler \
    --documents crawler/data/documents.jsonl \
    --crawl-log crawler/data/crawl_log.jsonl \
    --out data/corpus.json
```

Przykładowy wynik:

```
wrote data/corpus.json: 18 sources, 98 records {'article': 61, 'warning_meteo': 1, 'warning_hydro': 35, 'report': 1}
crawled but no documents: ['cert-polska', 'sar']
```

`crawled but no documents` wymienia źródła, które były crawlowane, ale nic nie dały (zwykle `blocked`).

> **Domyślne ścieżki wskazują zrzut w repo, nie `crawler/data/`.** Bez opcji `convert-crawler` czyta
> `data/documents.jsonl` i `data/crawl_log.jsonl` i pisze `data/corpus.json`, czyli działa na zamrożonym zrzucie
> z katalogu głównego. Żeby użyć świeżego crawla, zawsze podawaj `--documents` i `--crawl-log` jak wyżej.

> `--out data/corpus.json` **nadpisuje plik śledzony w git**. Nawet bez zmian w danych zmieni się linia
> `_comment` z datą konwersji. Jeśli nie chcesz commitować nowego korpusu, cofnij plik poleceniem
> `git checkout -- data/corpus.json` albo zapisuj gdzie indziej, np. `--out /tmp/corpus.json`.

Bez crawlera (zrzut z repo) wystarczy:

```bash
uv run prawdowid convert-crawler        # data/documents.jsonl → data/corpus.json
```

### C.2. Import do bazy

```bash
cd "$REPO"
uv run prawdowid ingest data/corpus.json --embedder e5      # albo --embedder hash bez modelu
```

```
records: {'inserted': 98}
totals:  {'distinct_records': 98, 'current_records': 98, 'evidence_versions': 98, 'publishing_domains': 7, 'source_types': 2, 'passages_searchable': 132}
```

`ingest` jest **idempotentny**. Ponowny import tego samego pliku daje `{'unchanged': 98}` i nie tworzy duplikatów.
Po każdym nowym crawlu powtórz C.1 i C.2. Liczniki w `records` znaczą:

| licznik | znaczenie |
|---|---|
| `inserted` | nowy dokument (nowa para `source_id` + `upstream_id`) |
| `new_version` | ta sama tożsamość, inna treść; stara wersja wypada z indeksu wyszukiwania |
| `unchanged` | treść identyczna z bieżącą wersją |
| `known_version` | treść identyczna ze starszą wersją |
| `invalid` | rekord odrzucony (brak pól, `citation_url` spoza domeny źródła lub bez https); szczegóły w ostrzeżeniu |

Dokumenty, których nie ma w nowym pliku, **zostają** w bazie. Import tylko dodaje i wersjonuje.

### C.3. Podgląd i wyszukiwanie

```bash
uv run prawdowid stats
uv run prawdowid search "ostrzeżenie hydrologiczne susza" --embedder e5
uv run prawdowid search "komunikat policji" --mode bm25 --k 3        # tylko pełnotekstowe, bez embeddera
uv run prawdowid search "ostrzeżenie" --area mazowieckie --k 5        # filtr obszaru
```

Opcje `search`: `--embedder` (domyślnie `hash`), `--mode hybrid|bm25|dense` (domyślnie `hybrid`),
`--k` (długość listy, domyślnie 8), `--area`, `--database-url`. Każdy wynik pokazuje id fragmentu,
`[upstream_id vN]`, wynik RRF i pozycje w rankingach składowych (`bm25#0=1 dense#0=7`).

### C.4. Weryfikacja twierdzenia (LLM, płatne)

Potrzebny jest `OPENAI_API_KEY` (domyślny dostawca) albo `ANTHROPIC_API_KEY` z `--provider anthropic`.
Każde wywołanie kosztuje: jedno zapytanie do LLM na ekstrakcję twierdzeń i po jednym na każde twierdzenie
(maks. 5 twierdzeń, wejście do 10 000 znaków).

```bash
cd "$REPO"
uv run prawdowid verify "Czy IMGW wydał ostrzeżenie przed suszą hydrologiczną na Mazowszu?" --embedder e5

# Claude zamiast OpenAI
uv run prawdowid verify "Czy IMGW wydał ostrzeżenie przed suszą hydrologiczną?" \
    --embedder e5 --provider anthropic

# ustalony "czas zapytania" (dla dat względnych typu "dziś"); bez --now: teraz, Europe/Warsaw
uv run prawdowid verify "Czy IMGW wydał dziś ostrzeżenie stopnia 2 dla całego Mazowsza?" \
    --embedder e5 --now 2026-11-21T12:00

# wynik jako JSON zamiast Markdown
uv run prawdowid verify "…" --embedder e5 --json
```

Opcje `verify`: `--embedder` (domyślnie `hash`), `--now` (ISO 8601), `--json`, `--provider openai|anthropic`
(domyślnie `PRAWDOWID_PROVIDER` albo `openai`), `--model` (domyślnie `OPENAI_MODEL`/`gpt-5.5` dla OpenAI,
`PRAWDOWID_MODEL`/`claude-opus-5-5` dla Anthropic), `--database-url`.
Na stderr trafia podsumowanie: status, model, czas, liczba wywołań, tokeny i koszt.
Koszt pokazuje się tylko dla modeli ze znaną ceną, dla pozostałych ustaw `PRAWDOWID_PRICE_IN`/`_OUT`.

`--now` jest ważne przy zrzucie z 2026-11-21 i przy danych testowych. Ostrzeżenia mają okna ważności, więc „dziś”
w innym dniu może dać werdykt `insufficient`.

### C.5. Embedder musi się zgadzać

Wektory fragmentów zapisują się z nazwą modelu. `search` i `verify` muszą używać **tego samego `--embedder`**,
którego użyto przy `ingest`. Inaczej tryby `hybrid`/`dense` kończą się błędem:

```
RuntimeError: passages embedded with ['intfloat/multilingual-e5-small'], query embedder is 'hash-512-35'; run `prawdowid reembed`
```

Rozwiązanie: użyj tego samego embeddera albo przelicz wszystkie wektory pod nowy model.

```bash
uv run prawdowid reembed --embedder e5      # domyślnie e5 (wymaga uv sync --extra e5)
uv run prawdowid reembed --embedder hash    # powrót do hash
```

Nie mieszaj embedderów między kolejnymi `ingest` do tej samej bazy. Dokładaj nowe dane tym samym `--embedder`,
co poprzednio. `--mode bm25` nie używa wektorów, więc działa zawsze.

---

## 5. Część D: testy i ewaluacja

### D.1. Test offline crawlera

Działa bez internetu. Stawia lokalny serwer (robots.txt, RSS, lista w stylu gov.pl, sitemapa, JSON, strona
statyczna, strona renderowana przez JS, endpoint 429) i uruchamia crawler przez CLI na tymczasowym katalogu
`/tmp/faktbot-test-*`. Wymaga zainstalowanego Chromium i modelu spaCy, bo testuje też maskowanie PII.

```bash
cd "$REPO/crawler"
source .venv/bin/activate
python tests/run_local_test.py
```

Na końcu powinno się pojawić `WSZYSTKO OK  (dane testowe: /tmp/faktbot-test-…/data)`, a kod wyjścia to 0.
Katalogi `/tmp/faktbot-test-*` zostają po teście i można je usunąć ręcznie.

### D.2. Testy RAG (pytest)

```bash
cd "$REPO"
uv run pytest                                  # albo: uv run pytest -q
```

Testy bazodanowe łączą się z `TEST_DATABASE_URL`, a bez niej z `DATABASE_URL`. Każdy test pracuje we **własnym,
tymczasowym schemacie** `scratch_<id>`, usuwanym po teście, więc istniejące tabele nie są ruszane. Bez osiągalnej
bazy testy DB są **pomijane** (nie oblane):

```
17 passed, 35 skipped
```

Wynik z bazą: `52 passed`. Przyczynę pominięć pokaże `uv run pytest -rs`. Inna baza tylko dla testów:

```bash
TEST_DATABASE_URL=postgresql://prawdowid:jakies-haslo@localhost:5432/prawdowid uv run pytest
```

Testy klienta OpenAI używają atrapy HTTP. Nie wymagają klucza i nic nie kosztują.

### D.3. Ewaluacja wyszukiwania (bezpłatna)

Porównuje BM25, dense i hybrydę (recall@1/3/8, MRR) na oznaczonym zbiorze pytań. Skrypt sam ładuje korpus do
tymczasowego schematu w bazie z **`DATABASE_URL`** (nie czyta `TEST_DATABASE_URL` i nie ma `--database-url`)
i usuwa ten schemat na koniec.

```bash
cd "$REPO"
uv run python scripts/eval_retrieval.py --embedder e5 --show-misses     # domyślnie --embedder e5!
uv run python scripts/eval_retrieval.py --embedder hash                 # bez modelu

# prawdziwe dane z crawla zamiast korpusu testowego
uv run python scripts/eval_retrieval.py --embedder e5 \
    --corpus data/corpus.json --cases fixtures/real_retrieval_cases.yaml
```

Opcje: `--embedder` (domyślnie `e5`), `--corpus` (domyślnie `fixtures/fake_corpus.json`), `--cases`
(domyślnie `fixtures/retrieval_cases.yaml`), `--k` (domyślnie 8), `--no-prefix-stem`, `--show-misses`,
`--json-out PLIK`.

### D.4. Ewaluacja werdyktów (płatna)

> **Uwaga na koszt.** Skrypt woła prawdziwe LLM: 2 wywołania na przypadek, 15 przypadków z
> `fixtures/verify_cases.yaml`, czyli ok. 30 płatnych zapytań na przebieg. Do sprawdzenia, czy działa,
> zacznij od `--only` z jednym-dwoma przypadkami.

```bash
cd "$REPO"
uv run python scripts/eval_verify.py --embedder e5 --only v01,v07     # najpierw tanio
uv run python scripts/eval_verify.py --embedder e5                    # pełny przebieg
uv run python scripts/eval_verify.py --embedder e5 --provider anthropic --verbose
```

Opcje: `--embedder` (domyślnie `e5`), `--only id1,id2`, `--provider openai|anthropic`, `--model`, `--verbose`,
`--json-out PLIK`. Skrypt ładuje sztuczny korpus `fixtures/fake_corpus.json` do tymczasowego schematu
(baza z `DATABASE_URL`) i używa czasu zapytania z pliku przypadków (`2026-11-21T12:00:00+01:00`).
Wypisuje wynik każdego przypadku, trafność, kontrole cytatów, opóźnienia i koszt (z przeliczeniem na 1000 sprawdzeń).
Jeśli pierwszy przypadek kończy się błędem (np. zły klucz), skrypt przerywa od razu, żeby nie palić pieniędzy.

---

## 6. Typowy cykl pracy

Jednorazowo: kroki A.1–A.2 i B.1–B.3. Potem na co dzień:

```bash
# 1. Baza (jeśli nie działa)
cd "$REPO" && docker compose up -d db

# 2. Świeże dane z crawlera (albo crawler działa w tle przez `run`, patrz A.6)
cd "$REPO/crawler" && source .venv/bin/activate
export CRAWLER_CONTACT=zespol@example.pl
python crawler.py once
python crawler.py status                   # szukaj alarmów BLOCKED / THROTTLED / 3× błąd / cisza
deactivate

# 3. Konwersja i import (idempotentne: przy braku zmian wszystko "unchanged")
cd "$REPO"
uv run prawdowid convert-crawler \
    --documents crawler/data/documents.jsonl \
    --crawl-log crawler/data/crawl_log.jsonl \
    --out data/corpus.json
uv run prawdowid ingest data/corpus.json --embedder e5
uv run prawdowid stats

# 4. Praca: wyszukiwanie i weryfikacja (ten sam --embedder co w ingest!)
uv run prawdowid search "…" --embedder e5
uv run prawdowid verify "…" --embedder e5

# 5. Przed commitem
uv run pytest
(cd crawler && .venv/bin/python tests/run_local_test.py)   # gdy zmieniasz crawler
git status        # data/corpus.json zmieniony? commituj tylko świadomie, inaczej: git checkout -- data/corpus.json
```

---

## 7. Zmienne środowiskowe

RAG czyta zmienne z powłoki i uzupełnia je z `.env` (szukanego w bieżącym katalogu i wyżej). Zmienne z powłoki
wygrywają. Crawler **nie** czyta `.env`.

| Zmienna | Kto czyta | Domyślnie | Znaczenie |
|---|---|---|---|
| `CRAWLER_CONTACT` | crawler (`faktbot/config.py`) | `settings.contact` z `sources.yaml` | mail kontaktowy w User-Agencie; obowiązkowy (z env albo z pliku), musi wyglądać jak e-mail |
| `POSTGRES_USER` | `docker-compose.yml` | `prawdowid` | użytkownik tworzony w kontenerze |
| `POSTGRES_PASSWORD` | `docker-compose.yml` | – (**wymagane**) | hasło tego użytkownika |
| `POSTGRES_DB` | `docker-compose.yml` | `prawdowid` | nazwa bazy |
| `POSTGRES_PORT` | `docker-compose.yml` | `5432` | port na `127.0.0.1` hosta |
| `DATABASE_URL` | `prawdowid` (`backend/config.py`), `scripts/eval_*.py`, pytest (zapas) | – (**wymagane**) | URL PostgreSQL, np. `postgresql://prawdowid:haslo@localhost:5432/prawdowid`; CLI pozwala nadpisać przez `--database-url` |
| `TEST_DATABASE_URL` | pytest (`tests/conftest.py`) | `DATABASE_URL` | baza dla testów (schematy tymczasowe); skrypty eval jej **nie** czytają |
| `PRAWDOWID_PROVIDER` | `backend/llm.py` | `openai` | dostawca LLM: `openai` albo `anthropic` (nadpisuje go `--provider`) |
| `OPENAI_API_KEY` | SDK OpenAI | – | klucz OpenAI; bez niego `verify` z dostawcą openai kończy się błędem |
| `OPENAI_MODEL` | `backend/llm.py` | `gpt-5.5` | model OpenAI (nadpisuje go `--model`) |
| `OPENAI_REASONING_EFFORT` | `backend/llm.py` | nie wysyłane | np. `low`/`medium`/`high`; wysyłane tylko, gdy ustawione |
| `ANTHROPIC_API_KEY` | SDK Anthropic | – | klucz Anthropic dla `--provider anthropic` |
| `PRAWDOWID_MODEL` | `backend/llm.py` | `claude-opus-5-5` | model Anthropic (nadpisuje go `--model`); dla OpenAI nieużywane |
| `PRAWDOWID_LLM_TIMEOUT` | `backend/llm.py` | `60` | limit sekund na zapytanie LLM (plus jedno ponowienie) |
| `PRAWDOWID_PRICE_IN`, `PRAWDOWID_PRICE_OUT` | `backend/llm.py` | – | cena USD za 1 mln tokenów wej./wyj., do raportu kosztu; potrzebne obie, nadpisują wbudowany cennik |
| `HF_HUB_OFFLINE` | biblioteka Hugging Face | – | `1`: używaj tylko modelu e5 z cache, bez pobierania |

---

## 8. Rozwiązywanie problemów (FAQ)

**Crawler: `Brak maila kontaktowego: ustaw zmienną CRAWLER_CONTACT…` (kod 2)**
Ustaw `export CRAWLER_CONTACT=zespol@example.pl` w tej samej powłoce albo `settings.contact` w `sources.yaml`.
Komunikat `CRAWLER_CONTACT nie wygląda na adres e-mail` oznacza literówkę w adresie. Pamiętaj, że crawler nie czyta `.env`.

**Crawler: `Executable doesn't exist at …/ms-playwright/chromium…` albo błąd uruchamiania przeglądarki**
Nie zainstalowano Chromium w tym venv/użytkowniku. Uruchom `playwright install chromium` z aktywnym
`crawler/.venv`. Jeśli brakuje bibliotek systemowych (`libnss3`, `libatk…`), uruchom
`playwright install --with-deps chromium`.

**Crawler: `Odmowa startu – maskowanie danych osobowych niedostępne…` (kod 2)**
Któreś z uruchamianych źródeł ma `mask_pii: true`, a model spaCy się nie wczytał. Zainstaluj go:
`pip install -r requirements.txt` albo `python -m spacy download pl_core_news_md`.
Ten sam model jest potrzebny w teście offline (D.1).

**Crawler: `Inny proces crawlera już działa na …/data` (kod 3)**
Na tym katalogu danych działa już `run` lub `once`. Znajdź proces (`pgrep -af crawler.py`) i poczekaj albo zatrzymaj
go przez `kill <pid>`. Sam plik `data/.lock` **zostaje na dysku po zakończeniu i to normalne**: blokadę trzyma
proces (`flock`), nie istnienie pliku. Nie trzeba go usuwać, a usunięcie nie zwalnia blokady działającego procesu.
`status` działa zawsze, bo nie bierze blokady.

**Crawler: źródło ma status `throttled` lub `blocked`**
* `throttled`: serwer zwrócił 429/503 albo robots.txt był niedostępny. Host jest wycofany zgodnie z `Retry-After`
  (domyślnie 15 min dla 429/503, 30 min dla niedostępnego robots.txt). Crawler sam spróbuje w następnym terminie.
  Aktywne wycofania widać pod tabelą `status`.
* `blocked`: 403, CAPTCHA/wyzwanie antybotowe (wycofanie na 60 min) albo robots.txt zabrania adresu.
  Tego **nie obchodzimy** (żadnych proxy, stealth ani zmiany User-Agenta). Poszukaj innego, dozwolonego adresu
  tych samych treści (np. RSS, API, lista dopuszczona w robots.txt) i dopisz go do `sources.yaml`.
  Na dziś stale `blocked` są `cert-polska` (robots.txt) i `sar`, patrz `crawler/RAPORT.md`.
* `warn` z 0 linków: zmieniła się struktura strony. Ustaw `link_selector` (patrz `crawler/docs/CONFIG.md`).

**RAG: `DATABASE_URL is not set. Copy .env.example to .env…`**
Brak `.env` albo uruchamiasz polecenie poza drzewem repo. Utwórz `.env` (B.1) albo podaj `--database-url`.

**RAG: `cannot connect to PostgreSQL: connection failed: … Connection refused`**
Baza nie działa albo URL wskazuje zły port lub hasło. Sprawdź `docker compose ps` (czy `healthy`)
i `docker compose logs db`. Porównaj `DATABASE_URL` z `POSTGRES_PORT`/`POSTGRES_PASSWORD` w `.env`.
Zmiana `POSTGRES_PASSWORD` po pierwszym starcie **nie działa** na istniejący wolumen: hasło ustawia się tylko przy
tworzeniu bazy. Wróć do starego hasła albo usuń dane przez `docker compose down -v` i uruchom ponownie.

**pytest: dużo `skipped`**
Testy DB pomijają się, gdy nie ma `TEST_DATABASE_URL`/`DATABASE_URL` (`no TEST_DATABASE_URL / DATABASE_URL set`)
albo baza nie odpowiada w 3 s (`PostgreSQL not reachable: …`). Uruchom `uv run pytest -rs`, żeby zobaczyć powód,
potem napraw połączenie jak wyżej. Bez bazy oczekiwany wynik to `17 passed, 35 skipped`, z bazą `52 passed`.

**Docker: `port is already allocated` / `address already in use` na 5432**
Port zajmuje inny PostgreSQL (np. systemowy). Ustaw w `.env` inny port i popraw URL:

```dotenv
POSTGRES_PORT=5433
DATABASE_URL=postgresql://prawdowid:jakies-haslo@localhost:5433/prawdowid
```

Potem `docker compose up -d db`. Możesz też użyć tamtej istniejącej bazy (PostgreSQL ≥ 14) i nie stawiać kontenera.

**RAG: `RuntimeError: passages embedded with [...], query embedder is '...'; run prawdowid reembed`**
`search`/`verify` używa innego embeddera niż `ingest`. Podaj ten sam `--embedder` albo przelicz wektory:
`uv run prawdowid reembed --embedder e5` (lub `hash`). Uwaga: samo `prawdowid reembed` bez opcji przelicza
na **e5**. Patrz C.5.

**RAG: `ModuleNotFoundError: No module named 'sentence_transformers'`**
Użyto `--embedder e5` (albo skryptu eval z domyślnym e5) bez dodatku e5. Uruchom `uv sync --extra e5` albo podaj
`--embedder hash`. Pamiętaj, że zwykłe `uv sync` usuwa dodatek e5.

**RAG: `OPENAI_API_KEY is not set (export OPENAI_API_KEY=sk-...)`**
Ustaw klucz w `.env` lub w powłoce, albo przełącz się na Anthropic: `--provider anthropic` z `ANTHROPIC_API_KEY`.
Pod WSL zmienne z Windows nie są widoczne. Komunikat podpowiada `setx WSLENV "OPENAI_API_KEY/u"`.

**RAG: `convert-crawler` kończy się `FileNotFoundError: … crawler/data/documents.jsonl`**
Crawler jeszcze nic nie zapisał (A.3) albo polecenie nie jest uruchomione z katalogu głównego repo
(ścieżki są względne).

**RAG: wyszukiwanie zwraca stare wyniki po nowym crawlu**
Nie wykonano ponownie `convert-crawler` z `--documents crawler/data/documents.jsonl` (domyślnie konwertuje zrzut
`data/`) albo `ingest`. Powtórz C.1 i C.2 i sprawdź `stats`.
