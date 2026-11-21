# FaktBot – format danych w `data/`

Opis plików, które crawler zapisuje w katalogu danych (`settings.data_dir`, domyślnie `data/` obok `sources.yaml`).
Zachowanie crawlera opisuje [SPEC.md](SPEC.md).

> **Dane osobowe.** Maskowanie PII jest obecnie wyłączone (SPEC §8.1), więc `documents.jsonl` zawiera pełne imiona
> i nazwiska z komunikatów policji. Katalogu `data/` NIE WOLNO commitować ani publikować bez wcześniejszego maskowania.

| plik | rodzaj | zapis | przeznaczenie |
|---|---|---|---|
| `documents.jsonl` | JSON Lines | **tylko dopisywanie**, fsync po każdej linii | **kontrakt dla konsumentów (RAG)**: wszystkie wersje dokumentów |
| `current.json` | JSON | atomowy (plik tymczasowy, fsync, `rename`) | indeks: najnowsza wersja każdego URL |
| `state.json` | JSON | atomowy | stan wewnętrzny crawlera (adresy znane, kolejka, walidatory, wycofania, statusy) |
| `crawl_log.jsonl` | JSON Lines | tylko dopisywanie, fsync | jedna linia na uruchomienie źródła |
| `crawler.log` | tekst | dopisywanie | log diagnostyczny |
| `.lock` | pusty plik | – | blokada `flock` procesu crawlera |
| `*.corrupt`, `*.tmp` | – | – | artefakty awarii (§6) |

Wspólne konwencje:
* kodowanie UTF-8, JSON z `ensure_ascii=False` (polskie znaki dosłownie);
* znaczniki czasu crawlera (`fetched_at`, `started_at`, `checked_at`, `until`…) są w ISO 8601, w UTC, z dokładnością
  do sekundy i z przesunięciem `+00:00`, np. `2026-11-21T18:22:39+00:00`;
* daty pochodzące ze źródeł (`published_at`, `published`, `lastmod`) są w ISO 8601 ze strefą źródła. Daty bez strefy
  dostają strefę Europe/Warsaw (`+01:00` / `+02:00`), np. `2026-11-21T08:00:00+01:00`;
* `current.json` i `state.json` są zapisywane atomowo z uprawnieniami **0644**. `documents.jsonl` i `crawl_log.jsonl`
  dostają uprawnienia zgodne z umask.

---

## 1. `documents.jsonl`

Jedna linia to jeden obiekt JSON zakończony `\n` i odpowiada jednej **wersji** dokumentu. Nowa wersja powstaje, gdy
zmieni się hasz treści pod tym samym URL. Wcześniejsze linie nigdy nie są zmieniane ani usuwane.

### 1.1. Pola

Kolejność pól w linii jest stała (jak w tabeli). Konsumenci MUSZĄ jednak czytać pola po nazwie i ignorować nieznane.

| pole | typ | null? | znaczenie |
|---|---|---|---|
| `id` | string | nie | `<16 hex SHA-256(url)>-v<version>`. Unikalny dla wersji. Prefiks przed `-v` jest stały dla URL |
| `url` | string | nie | klucz dokumentu. Dla adapterów stron to URL z kanału/listy/konfiguracji (bez fragmentu, **przed** przekierowaniami). Dla `json_api`: `<endpoint z konfiguracji>#<id rekordu>` |
| `source` | string | nie | nazwa źródła (`sources[].name`) |
| `institution` | string | nie | instytucja (`sources[].institution`) |
| `doc_type` | string | tak* | typ dokumentu ze źródła, domyślnie `"komunikat"`. Dziś także `ostrzezenie`, `ostrzezenie_meteo`, `ostrzezenie_hydro`, `raport`, `informacja` (oraz w wyłączonych: `komunikat_nawigacyjny`, `ostrzezenie_rso`). *`null` tylko przy jawnym `doc_type: null` w konfiguracji |
| `region` | string | tak | region ze źródła (np. `"mazowieckie"`), domyślnie `null` |
| `title` | string | tak | tytuł (SPEC §6.8, §7.2). `null`, gdy nie udało się go ustalić albo gdy `json_api` nie ma `title_template` |
| `content` | string | nie | zwykły tekst. Akapity rozdzielone `\n`, maksymalnie jedna pusta linia między blokami, komórki tabel rozdzielone ` \| `. Dla stron co najmniej 80 znaków. Może kończyć się blokiem `Komunikat opublikowany w załączniku:` z liniami `- nazwa: url`. Dla `json_api`: linie `klucz: wartość` (klucze spłaszczone, np. `obszary[0].wojewodztwo`), a przy `attribution` dodatkowo `\n\n<attribution>`. Przy maskowaniu zawiera `[OSOBA]` / `[PESEL]` |
| `content_hash` | string | nie | SHA-256 (64 znaki hex, małe litery) z `content` w UTF-8. Tytuł i metadane nie wchodzą do hasza |
| `version` | integer | nie | 1, 2, 3… Rośnie o 1 dla danego `url` |
| `published_at` | string | tak | data publikacji, ISO 8601 ze strefą. Kolejność źródeł: kanał/lista, potem metadane strony, potem (poza `static_page`) data zgadnięta przez trafilaturę; dla `json_api` pierwsze pole z `date_fields`; dla `sitemap` tylko ze strony. `null`, gdy nieznana (typowe dla `static_page`) |
| `fetched_at` | string | nie | moment zapisu tej wersji (UTC, `+00:00`) |
| `fetch_method` | string | nie | `"playwright"`: treść z wyrenderowanego DOM. `"http"`: odpowiedź bez renderowania (nie-HTML, `json_api`, opis z kanału RSS) |
| `attribution` | string | pole opcjonalne | obecne tylko wtedy, gdy źródło ma `attribution` (dziś IMGW). Wymagany dopisek licencyjny |

Inne dodatkowe pola dziś nie występują. Mechanizm dopuszcza tylko klucze niekolidujące z polami powyżej.

### 1.2. Przykłady (treść skrócona)

```json
{"id": "654784d6af5ff3aa-v1", "url": "https://policja.pl/pol/aktualnosci/284115,Operacja-TOR-Rok-wspolnej-sluzby-na-rzecz-bezpieczenstwa-kolei.html", "source": "policja-kgp", "institution": "Komenda Główna Policji", "doc_type": "komunikat", "region": null, "title": "Operacja TOR. Rok wspólnej służby na rzecz bezpieczeństwa kolei", "content": "Operacja TOR. Rok wspólnej służby na rzecz bezpieczeństwa kolei\nBlisko pół miliona policjantów, …", "content_hash": "7ae466845cd4de4161670ac292775938a1fe5ab3ea14d1ccbfd8b8d73d621b1e", "version": 1, "published_at": "2026-11-21T08:00:00+01:00", "fetched_at": "2026-11-21T18:22:39+00:00", "fetch_method": "playwright"}
```

```json
{"id": "b20e524177790c7f-v1", "url": "https://danepubliczne.imgw.pl/api/data/warningsmeteo#Sk20261121110027236", "source": "imgw-meteo", "institution": "Instytut Meteorologii i Gospodarki Wodnej – PIB", "doc_type": "ostrzezenie_meteo", "region": null, "title": "Ostrzeżenie meteorologiczne: Oblodzenie – stopień 1", "content": "id: Sk20261121110027236\nnazwa_zdarzenia: Oblodzenie\nstopien: 1\nprawdopodobienstwo: 70\nobowiazuje_do: 2026-11-22 08:00:00\nobowiazuje_od: 2026-11-22 01:00:00\nopublikowano: 2026-11-21 12:00:00\ntresc: …\n\nŹródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy.", "content_hash": "8b13ce225c22f46f723c94442a1dfe60f408d42ca3445de3e9fc0de4a47276f6", "version": 1, "published_at": "2026-11-21T12:00:00+01:00", "fetched_at": "2026-11-21T18:31:36+00:00", "fetch_method": "http", "attribution": "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy."}
```

### 1.3. JSON Schema rekordu `documents.jsonl`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://prawdowid.example/faktbot/document.schema.json",
  "title": "FaktBot – wersja dokumentu (linia documents.jsonl)",
  "type": "object",
  "required": ["id", "url", "source", "institution", "doc_type", "region", "title", "content",
               "content_hash", "version", "published_at", "fetched_at", "fetch_method"],
  "properties": {
    "id":           {"type": "string", "pattern": "^[0-9a-f]{16}-v[1-9][0-9]*$"},
    "url":          {"type": "string", "pattern": "^https?://"},
    "source":       {"type": "string", "minLength": 1},
    "institution":  {"type": "string", "minLength": 1},
    "doc_type":     {"type": ["string", "null"]},
    "region":       {"type": ["string", "null"]},
    "title":        {"type": ["string", "null"]},
    "content":      {"type": "string", "minLength": 1},
    "content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "version":      {"type": "integer", "minimum": 1},
    "published_at": {"type": ["string", "null"], "format": "date-time"},
    "fetched_at":   {"type": "string", "format": "date-time"},
    "fetch_method": {"enum": ["http", "playwright"]},
    "attribution":  {"type": "string"}
  },
  "additionalProperties": true
}
```

Niezmienniki spoza schematu:
* `id == sha256(url)[:16] + "-v" + str(version)`;
* `content_hash == sha256(content.encode("utf-8")).hexdigest()`;
* dla danego `url` kolejne linie w pliku mają `version` 1, 2, 3… bez luk (przy jednym procesie piszącym, co gwarantuje
  blokada), a dwie kolejne wersje mają różne `content_hash`.

---

## 2. `current.json`

Indeks najnowszych wersji: obiekt `url → {version, content_hash, id}`. Zapisywany atomowo po każdej dopisanej
linii (wcięcie 1 spacja).

```json
{
 "https://policja.pl/pol/aktualnosci/284115,Operacja-TOR-Rok-wspolnej-sluzby-na-rzecz-bezpieczenstwa-kolei.html": {
  "version": 1,
  "content_hash": "7ae466845cd4de4161670ac292775938a1fe5ab3ea14d1ccbfd8b8d73d621b1e",
  "id": "654784d6af5ff3aa-v1"
 }
}
```

| pole | typ | znaczenie |
|---|---|---|
| `version` | integer | numer najnowszej wersji URL |
| `content_hash` | string | hasz najnowszej wersji. Z nim porównywana jest nowo pobrana treść |
| `id` | string | `id` najnowszej linii w `documents.jsonl` |

`current.json` to pochodna `documents.jsonl` i może zostać z niego odbudowany (§6). Liczba kluczy to liczba
unikalnych dokumentów.

---

## 3. `state.json`

Stan wewnętrzny crawlera. **To nie jest kontrakt dla konsumentów** i może się zmieniać. Wyjątkiem jest pole
`active_ids` (§3.2), które RAG może czytać. Zapis atomowy po każdym źródle, przy ustawieniu wycofania i na koniec procesu.

```text
{
  "sources":    { "<nazwa źródła>": SourceState, ... },
  "http_cache": { "<url>": {"etag": string|null, "last_modified": string|null}, ... },
  "backoff":    { "<host>": {"until": iso, "reason": string, "kind": "throttled"|"blocked"}, ... }
}
```

### 3.1. `sources.<nazwa>` (SourceState)

| klucz | typ | kiedy obecny | znaczenie |
|---|---|---|---|
| `known` | object `url → Known` | zawsze (może być `{}`) | adresy już obsłużone (`rss`, `html_list`, `sitemap`, `static_page`). Dla `json_api` zawsze puste |
| `pending` | array of ListItem | zawsze | kolejka zaległych nowych adresów (ponad limit albo z hostów w wycofaniu), max 300 |
| `history` | array of string | zawsze | ostatnie statusy, **najnowszy pierwszy**, max 10 |
| `list_cache` | object `url_listy → [ListItem]` | `rss` (każdy kanał), `html_list` (pierwsza strona każdej listy) | ostatnio odczytana lista pozycji, używana przy 304 |
| `active_ids` | array of string | `json_api` po pierwszej odpowiedzi 200 | identyfikatory rekordów w ostatniej odpowiedzi 200 (przy 304 bez zmian) |
| `first_run_at` | iso | po pierwszym uruchomieniu | początek pierwszego uruchomienia źródła |
| `last_run` | iso | j.w. | początek ostatniego uruchomienia (od niego liczy się harmonogram) |
| `last_finished` | iso | j.w. | koniec ostatniego uruchomienia |
| `last_status` | string | j.w. | `ok` \| `not_modified` \| `warn` \| `blocked` \| `throttled` \| `error` |
| `last_message` | string | j.w. | komunikat ostatniego uruchomienia (jak w `crawl_log`) |
| `last_new_at` | iso | po pierwszym uruchomieniu z `new` lub `updated` > 0 | koniec ostatniego uruchomienia z nową lub zmienioną treścią (do alarmu ciszy) |

**Known** (`known.<url>`):

| klucz | typ | znaczenie |
|---|---|---|
| `first_seen` | iso | pierwsze obsłużenie (albo pierwszy błąd) |
| `checked_at` | iso | ostatnie udane obsłużenie albo moment ustawienia `failed`. Brak przy wpisie, który ma tylko `fails` |
| `published` | iso | data z listy lub kanału (jeśli była) |
| `lastmod` | iso | `lastmod` z sitemapy (jeśli był). Porównywany przy kolejnych przebiegach |
| `fails` | integer | liczba kolejnych błędów (usuwana po sukcesie) |
| `failed` | `true` | po 3 błędach: adres nie będzie już pobierany (poza `static_page`) |
| `robots_blocked` | `true` | adres zablokowany w robots.txt (informacyjnie; nie jest kasowane) |
| `skipped` | string | powód pominięcia, max 200 znaków (informacyjnie; nie jest kasowane) |

**ListItem** (w `pending` i `list_cache`):

```json
{"url": "https://…", "title": "string|null", "published": "iso|null", "lastmod": "iso|null", "summary": "string|null"}
```

Przykład wpisu źródła (skrócony):

```json
"policja-kgp": {
 "known": {"https://policja.pl/pol/aktualnosci/284115,….html": {"first_seen": "2026-11-21T18:22:39+00:00", "checked_at": "2026-11-21T18:22:39+00:00", "published": "2026-11-21T08:00:00+01:00"}},
 "pending": [],
 "history": ["ok", "ok"],
 "list_cache": {"https://policja.pl/dokumenty/rss/1-rss-1.rss": [{"url": "…", "title": "Operacja TOR. …", "published": "2026-11-21T08:00:00+01:00", "lastmod": null, "summary": "Blisko pół miliona …"}]},
 "first_run_at": "2026-11-21T18:22:29+00:00",
 "last_run": "2026-11-21T18:35:27+00:00",
 "last_finished": "2026-11-21T18:35:33+00:00",
 "last_status": "ok",
 "last_message": "",
 "last_new_at": "2026-11-21T18:23:58+00:00"
}
```

### 3.2. `active_ids` (`json_api`)

Lista identyfikatorów rekordów z ostatniej odpowiedzi 200 endpointu, w kolejności z odpowiedzi. URL dokumentu
odpowiadającego identyfikatorowi to `<endpoint>#<id>`, gdzie endpoint pochodzi z konfiguracji źródła (CONFIG.md).
Powtórzony identyfikator w jednej odpowiedzi dostaje sufiks `~2`, `~3`… (np. `A~2`).
Przy `id_template` identyfikator jest slugiem, np.
`21-2026-biuro-prognoz-hydrologicznych-we-wroclawiu-wydzial-prognoz-i-opracowan-hydrologicznych-w-poznaniu`.

### 3.3. `http_cache`

Klucz to **żądany** URL (bez fragmentu): kanał, strona listy, sitemapa, endpoint, artykuł. Wartość to
`{"etag": string|null, "last_modified": string|null}`, czyli surowe nagłówki odpowiedzi. Wpis istnieje tylko wtedy,
gdy jest co najmniej jeden walidator. Zapisywany po udanym przetworzeniu (SPEC §3.7).

### 3.4. `backoff`

Klucz to nazwa hosta (małe litery, bez portu).

| pole | typ | znaczenie |
|---|---|---|
| `until` | iso (UTC) | do kiedy NIE WOLNO wysyłać zapytań do hosta |
| `reason` | string | `HTTP 429`, `HTTP 503`, `HTTP 403`, `429 na robots.txt`, `wyzwanie antybotowe (<znacznik>)` |
| `kind` | `"throttled"` \| `"blocked"` | `blocked` dla 403 i wyzwań (skutkuje statusem `blocked`), `throttled` dla 429/503. Wpis bez `kind` jest traktowany jak `throttled` |

Wygasłe wpisy są usuwane dopiero przy następnym sprawdzeniu danego hosta, więc mogą jeszcze chwilę zostać w pliku.

```json
"backoff": {"www.sar.gov.pl": {"until": "2026-11-21T19:31:33+00:00", "reason": "wyzwanie antybotowe (_incapsula_resource?swjiylwa)", "kind": "blocked"}}
```

---

## 4. `crawl_log.jsonl`

Jedna linia na każde zakończone uruchomienie źródła (w `once` i w `run`). Plik tylko dopisywany.
Uruchomienie przerwane drugim sygnałem nie ma linii.

| pole | typ | znaczenie |
|---|---|---|
| `source` | string | nazwa źródła |
| `started_at` | iso (UTC) | początek |
| `finished_at` | iso (UTC) | koniec |
| `status` | enum | `ok`, `not_modified`, `warn`, `blocked`, `throttled`, `error` (warunki: SPEC §5) |
| `found` | integer ≥ 0 | znalezione pozycje: `rss`/`html_list` – unikalne URL-e z list (także z `list_cache`); `sitemap` – adresy po filtrach; `json_api` – rekordy (0 przy 304); `static_page` – liczba URL-i |
| `new` | integer ≥ 0 | zapisane wersje 1 |
| `updated` | integer ≥ 0 | zapisane wersje ≥ 2 |
| `unchanged` | integer ≥ 0 | sprawdzone bez zmian (304 albo ten sam hasz) |
| `skipped` | integer ≥ 0 | pominięte: robots.txt + `SkipItem`. Pozycje odłożone z powodu wycofania hosta się tu **nie** liczą |
| `errors` | integer ≥ 0 | błędy pozycji, rekordów, parsowania kanału albo błąd źródła |
| `message` | string | komunikaty połączone `"; "`, max 2000 znaków. Może być `""` |
| `blocked_urls` | array of string | **opcjonalne**: adresy zablokowane w robots.txt (max 50) |

Przykład:

```json
{"source": "cert-polska", "started_at": "2026-11-21T18:25:03+00:00", "finished_at": "2026-11-21T18:25:03+00:00", "status": "blocked", "found": 0, "new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0, "message": "robots.txt blokuje wszystkie kanały – nic nie pobieram; pominięto 1 adres(ów) zablokowanych w robots.txt", "blocked_urls": ["https://moje.cert.pl/advisories/feed/"]}
```

### 4.1. JSON Schema rekordu `crawl_log.jsonl`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://prawdowid.example/faktbot/crawl-log.schema.json",
  "title": "FaktBot – uruchomienie źródła (linia crawl_log.jsonl)",
  "type": "object",
  "required": ["source", "started_at", "finished_at", "status", "found", "new", "updated",
               "unchanged", "skipped", "errors", "message"],
  "properties": {
    "source":      {"type": "string", "minLength": 1},
    "started_at":  {"type": "string", "format": "date-time"},
    "finished_at": {"type": "string", "format": "date-time"},
    "status":      {"enum": ["ok", "not_modified", "warn", "blocked", "throttled", "error"]},
    "found":       {"type": "integer", "minimum": 0},
    "new":         {"type": "integer", "minimum": 0},
    "updated":     {"type": "integer", "minimum": 0},
    "unchanged":   {"type": "integer", "minimum": 0},
    "skipped":     {"type": "integer", "minimum": 0},
    "errors":      {"type": "integer", "minimum": 0},
    "message":     {"type": "string", "maxLength": 2000},
    "blocked_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 50}
  },
  "additionalProperties": false
}
```

---

## 5. `crawler.log`

Log tekstowy (także na stderr), dopisywany przez `run` i `once` (nie przez `status`), bez rotacji.
Format linii: `RRRR-MM-DD GG:MM:SS POZIOM  komunikat`, czas **lokalny**, poziom wyrównany do 7 znaków:

```
2026-11-21 19:22:29 INFO    User-Agent: FaktBot/1.0 (+testtest@gmail.com)
2026-11-21 19:22:29 INFO    ▶ policja-kgp (rss)
2026-11-21 19:23:58 INFO    ■ policja-kgp: ok (znaleziono 21, nowe 21, zmienione 0, bez zmian 0, pominięte 0, błędy 0)
```

Służy ludziom do diagnostyki i nie jest formatem maszynowym. Do automatyzacji służy `crawl_log.jsonl`.

---

## 6. Gwarancje i odporność na awarie

* **Tylko dopisywanie.** Linie `documents.jsonl` i `crawl_log.jsonl` nie są nigdy modyfikowane, usuwane ani
  przestawiane. Każda linia jest zapisywana jednym wywołaniem `write` w Pythonie, po którym następują `flush` i `fsync`.
  Długa linia może mimo to trafić na dysk w kilku wywołaniach systemowych, więc czytelnik może chwilowo zobaczyć
  niepełną ostatnią linię (§7.2).
* **Kolejność zapisu dokumentu:** najpierw linia w `documents.jsonl` (fsync), potem atomowy zapis `current.json`.
* **Zapis atomowy** `current.json` i `state.json`: plik tymczasowy w tym samym katalogu
  (`<nazwa>.<losowe>.tmp`), fsync, `os.replace`. Czytelnik widzi zawsze całą starą albo całą nową wersję.
  Katalog nie jest fsyncowany. Po twardym padzie systemu możliwa jest utrata ostatniej zmiany, ale nie uszkodzenie pliku.
  Przy awarii w trakcie zapisu plik `.tmp` jest usuwany (o ile proces zdążył). W przeciwnym razie zostaje i można go usunąć.
* **Uzgadnianie `current.json`** przy każdym starcie `Store` (`run`, `once`, `status`): jeśli `documents.jsonl`
  istnieje, a `current.json` nie istnieje albo ma mtime **starszy** niż `documents.jsonl`, indeks jest odbudowywany
  z JSONL (przy `status` tylko w pamięci, bez zapisu – `status` niczego nie zapisuje). Dla każdego URL brana jest linia o największym `version`, a puste i niepoprawne linie są pomijane.
  W logu pojawia się ostrzeżenie „current.json starszy niż documents.jsonl”.
* **Uszkodzony JSON** (`current.json` lub `state.json`): plik jest przemianowywany na `<nazwa>.corrupt` (poprzedni
  `.corrupt` zostaje nadpisany), a crawler zaczyna od pustego obiektu. W przypadku `current.json` indeks zostaje zaraz
  odbudowany. W przypadku `state.json` tracone są `known`, walidatory i wycofania: adresy z list będą traktowane jako nowe,
  ale istniejące wersje nie są dublowane, bo o zapisie decyduje hasz w `current.json`.
* **Jeden piszący:** blokada `flock` na `.lock` gwarantuje jeden proces `run`/`once` na katalog.

---

## 7. Wskazówki dla konsumentów (RAG)

### 7.1. Zasady

* Źródłem prawdy jest **`documents.jsonl`**. `current.json` to wygodny indeks, a `state.json` to szczegół
  implementacji (poza `active_ids`).
* Katalog `data/` MUSI być dla konsumenta **tylko do odczytu**. NIE WOLNO zapisywać, przenosić ani obcinać plików
  podczas pracy crawlera.
* NIE WOLNO zakładać blokady na `data/.lock`. Crawler odmówiłby startu (kod 3).
* Wszystkie pliki są czytelne dla innych użytkowników systemu (0644 / umask), więc konsument może działać jako osobny
  użytkownik.
* Konsumenci MUSZĄ ignorować nieznane pola (mogą dojść nowe opcjonalne pola, jak `attribution`).

### 7.2. Bezpieczny odczyt w trakcie dopisywania

* Czytaj `documents.jsonl` w trybie binarnym linia po linii. **Ostatnią linię bez końcowego `\n` pomiń**: może być
  w trakcie zapisu. Przetworzysz ją przy następnym odczycie.
* Przy przetwarzaniu przyrostowym zapamiętuj **offset bajtowy** końca ostatniej pełnej linii i następnym razem zaczynaj od niego.
  Plik nigdy się nie skraca. Jeśli rozmiar jest mniejszy od zapamiętanego offsetu, plik został ręcznie podmieniony
  i trzeba przeczytać go od nowa.
* Linię, której nie da się sparsować jako JSON, pomiń i zaloguj.
* `current.json` czytaj w całości jednym otwarciem. Dzięki `rename` zawsze dostaniesz spójny plik.
  Może wyprzedzać to, co już przeczytałeś z JSONL, ale linia o danym `id` na pewno już jest w JSONL.

### 7.3. Najnowsza wersja każdego dokumentu

Klucz dokumentu to `url` (równoważnie prefiks `id` przed `-v`). Najnowsza wersja to linia o największym `version`
dla danego `url`. W pliku to zawsze **ostatnie** wystąpienie URL.

```python
import json

def latest_versions(path="data/documents.jsonl"):
    latest = {}
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break                      # niepełna linia na końcu – w trakcie zapisu
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            prev = latest.get(d["url"])
            if prev is None or d["version"] > prev["version"]:
                latest[d["url"]] = d
    return latest
```

Przy indeksowaniu przyrostowym: dla każdej nowej linii zrób upsert po `url`, gdy `version` jest większe od
zaindeksowanego, a starą wersję usuń z indeksu albo oznacz jako nieaktualną. Starszych wersji nie trzeba indeksować,
ale warto je zachować do audytu („co instytucja napisała pierwotnie”).

### 7.4. Interpretacja pól

* **Aktualność:** `published_at` może być `null`. Wtedy jako przybliżenia użyj `fetched_at` pierwszej wersji
  (`version == 1`) i oznacz datę jako przybliżoną. `fetched_at` kolejnych wersji to moment wykrycia zmiany.
* **Brak „ostatnio widziano”:** dokument niezmieniony nie dostaje nowej linii. Brak nowych wersji nie oznacza, że
  dokument zniknął u źródła. Crawler nie wykrywa usunięć.
* **Ostrzeżenia `json_api`** (IMGW): aktualne są tylko rekordy, których `id` (część URL po `#`) jest w
  `state.json → sources.<źródło>.active_ids`. Pozostałe to ostrzeżenia archiwalne. Okres ważności jest też w treści
  (np. `obowiazuje_od`/`obowiazuje_do`, `data_od`/`data_do`; `9999-12-31` oznacza „do odwołania”).
* **`attribution`:** przy cytowaniu dokumentu z tym polem RAG POWINIEN pokazać tekst atrybucji (wymóg licencji IMGW).
* **Linki:** `url` to adres do cytowania. Dla `json_api` adres z `#id` prowadzi do endpointu API, a nie do strony.
* **Załączniki:** treść z blokiem „Komunikat opublikowany w załączniku:” oznacza, że właściwy komunikat jest w pliku
  (PDF/DOC), którego crawler nie pobiera.
* **Dane osobowe:** dopóki maskowanie jest wyłączone, każdy eksport poza zespół MUSI przejść przez maskowanie (SPEC §8).
