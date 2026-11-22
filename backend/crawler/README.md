# FaktBot (crawler)

Okresowo pobiera komunikaty i ostrzeżenia instytucji państwowych i zapisuje je jako zwykły tekst z metadanymi i pełną historią wersji.
Źródła są w `sources.yaml` i w `sources.d/*.yaml` (wczytywane alfabetycznie, nazwy unikalne we wszystkich plikach). Wynikiem są wyłącznie pliki w `data/crawl/`: crawler nie zna bazy ani RAG, a dane do bazy ładuje `rag ingest`.
Źródła opisuje deklaratywnie [`sources.yaml`](sources.yaml), więc nowe źródło nie wymaga zmian w kodzie.

## Uruchamianie

```bash
uv run playwright install chromium         # raz; przeglądarka do renderowania stron
export CRAWLER_CONTACT=zespol@example.pl   # obowiązkowy mail w User-Agencie; crawler nie czyta .env
set -a; . ./.env; set +a                   # klucze API źródeł (GOOGLE_FACTCHECK_API_KEY, EUVSDISINFO_API_KEY)
uv run crawler once --max-items 5          # jeden przebieg, maks. 5 nowych pozycji na źródło
uv run crawler once --source rcb           # tylko wybrane źródła (opcja powtarzalna, działa też dla wyłączonych)
uv run crawler run                         # pętla: każde źródło co interval_minutes
uv run crawler status                      # tabela statusów i alarmów (bez sieci, nie zmienia danych)
scripts/crawler_run.sh                     # (z katalogu głównego) `run` z kluczami z .env; w tle: setsid nohup … &
```

- Opcje globalne (`--config`, `--data-dir`, `-v`) podaje się **przed** podkomendą. Ścieżka względna `--data-dir` liczy się od katalogu `sources.yaml`, np. `uv run crawler --data-dir ../../data/snapshot status`.
- Pierwszy Ctrl+C/SIGTERM kończy pracę po bieżącym źródle, a drugi przerywa od razu. Stan przetrwa restart.
- Scheduler (`run`) uruchamia najbardziej zaległe źródło. Źródła z `background: true` (całe jednostki gov.pl z mapy strony, `sources.d/govpl.yaml`) idą tylko wtedy, gdy nie czeka żadne zwykłe źródło, więc zaległe archiwum nie opóźnia alertów bardziej niż o jeden przebieg (≤ `max_items_per_run` stron).
- Jeden katalog danych obsługuje jeden proces (`flock` na `.lock`, kod 3). Plik `.lock` zostaje na dysku i to normalne.
- Kody wyjścia: 0 to normalne zakończenie (także przy źródłach z błędami), 2 to błąd konfiguracji lub brak maila, 3 to blokada zajęta.

## Moduły (`src/crawler/`)

| Plik | Rola |
| --- | --- |
| `cli.py` | CLI, blokada procesu, sygnały |
| `config.py` | Wczytanie `sources.yaml`, wartości domyślne, User-Agent, twarde minimum 3 s |
| `runner.py` | Przebieg źródła, wyliczenie statusu, harmonogram `run`, raport `status` |
| `adapters.py` | Adaptery typów, wybór kandydatów (nowe przed ponownym sprawdzeniem), kolejka zaległych |
| `fetcher.py` | **Jedyna droga do sieci**: wycofanie, robots.txt, odstęp, zapytania warunkowe, detekcja wyzwań, Playwright |
| `robots.py` | Parser robots.txt (RFC 9309) |
| `extract.py` | Treść, tytuł, data (formaty polskie, strefa Europe/Warsaw), RSS/Atom, sitemapy, załączniki |
| `storage.py` | Zapis plików (dopisywanie z fsync, zapisy atomowe) |
| `mcp.py` | Adapter `mcp_tool`: JSON-RPC przez `Fetcher.http_send`, parser rekordów EUvsDisinfo z Markdown |

## Gov.pl w całości

`sources.d/govpl.yaml` ma dwie warstwy: listy aktualności ministerstw (z datami, co 30 min) i ~90 całych jednostek (ministerstwa, urzędy centralne, UW, WSSE, GDDKiA, KW PSP). Całą jednostkę opisuje strona `/web/<jednostka>/mapa-strony?show-bip=true`: jedno zapytanie zwraca wszystkie podstrony (np. KGPSP ~3100, GIF ~1000). Źródło typu `html_list` z `require_date: false` pobiera co przebieg do `max_items_per_run` nowych stron, a znane sprawdza ponownie po `recheck_hours: 720`. Listy aktualności gov.pl pokazują najwyżej 20 pozycji na stronę (większe `size` jest przycinane).
Skala to rzędu 100 tys. stron, czyli kilka dni pracy ciągłej przy 1 zapytaniu na 3 s. Nową jednostkę dodaje się czterema liniami pod kotwicą `*mapa` (slug z https://www.gov.pl/web/gov/katalog-jednostek).

## Typy adapterów

Wybieraj w tej kolejności, od najlepszego. **Jeśli źródło ma API** (udokumentowane albo endpoint JSON, z którego korzysta sama strona), używaj `json_api` zamiast scrapowania HTML:

| Typ | Kiedy | Uwagi |
| --- | --- | --- |
| `rss` | Źródło ma kanał RSS/Atom | Domyślnie pobiera pełny artykuł (`fetch_full`). Odrzuca wpisy spoza domeny kanału. |
| `json_api` | Publiczne API (IMGW, RSO, GIOŚ, Odyseusz, Google Fact Check) | Każdy rekord to dokument `endpoint#id` z treścią w liniach `klucz: wartość`. Co przebieg przetwarza wszystkie rekordy, a o zapisie decyduje hasz. Obsługuje stronicowanie i dociąganie szczegółów rekordu (niżej). |
| `sitemap` | Brak kanału, jest sitemap.xml | Ponownie sprawdza adres, gdy wzrośnie jego `lastmod`. |
| `html_list` | Lista aktualności z datami | Heurystyka bierze linki, przy których stoi data, w kafelku z jednym linkiem, i pomija `nav`/`header`/`footer`. Gdy nie wystarcza, ustaw `link_selector`. |
| `static_page` | Pojedyncza strona, której zmiany śledzimy | Pobierana co przebieg. Data tylko z metadanych strony. |
| `mcp_tool` | Narzędzie serwera MCP (EUvsDisinfo) | Plik `mcp.py`, sekcja niżej. |

## Dodanie źródła

1. Sprawdź ręcznie `https://<host>/robots.txt` i ochronę antybotową. Źródło zablokowane **nie trafia** do konfiguracji, tylko na listę wykluczonych z powodem (komentarz na początku `sources:`).
2. Dodaj wpis. Na start ustaw `enabled: false`: `once --source` i tak go uruchomi.

   ```yaml
     - name: mz-aktualnosci          # unikalny; pole `source` w danych i klucz w stanie
       <<: *govpl                    # profil CMS gov.pl (selektory tytułu i treści)
       type: html_list
       institution: Ministerstwo Zdrowia
       url: https://www.gov.pl/web/zdrowie/wiadomosci
       path_prefix: /web/zdrowie/
       enabled: false
   ```

3. Przebieg próbny na osobnym katalogu (`documents.jsonl` jest tylko dopisywany):
   `uv run crawler --data-dir /tmp/faktbot-proba once --source mz-aktualnosci --max-items 3`
4. Obejrzyj wynik w `/tmp/faktbot-proba/`. `found = 0` oznacza zły `path_prefix` albo heurystykę. Logo zamiast tytułu wymaga `title_selector`, a śmieci w treści `content_selector` lub `drop_selectors`.
5. Ustaw `enabled: true`. Jeśli wprowadzasz nowy `doc_type`, zmapuj go w `rag/crawler_import.py`.

Klucze, które zwykle mają znaczenie: `name`, `type`, `institution`, `url`/`urls`, `doc_type`, `region`, `interval_minutes`, `enabled`, selektory ekstrakcji, a dla `json_api` `records_path`, `id_field`/`id_template`, `title_template`, `date_fields`, `sort_lists` i `attribution`.
Dodatkowe klucze `json_api` (przykłady: źródła `rso-*` i `gios-indeks-powietrza`):

| Klucz | Do czego |
| --- | --- |
| `accept` | Nagłówek `Accept` (domyślnie `application/json`; GIOŚ odpowiada na niego 406, stąd `"*/*"`) |
| `page_template`, `page_start`, `max_pages` | Stronicowanie: `endpoint` to strona `page_start`, kolejne to `page_template` z `{page}`. Koniec, gdy strona jest krótsza od pierwszej, nie wnosi nowych rekordów albo po `max_pages` (domyślnie 10) |
| `record_filter` | `{pole: [wartości]}` po spłaszczeniu, bez wielkości liter, np. tylko stacje z wybranych miast |
| `detail_url`, `detail_path`, `detail_as` | Szablon adresu szczegółów pobieranych dla każdego rekordu (np. indeks stacji GIOŚ). Wynik trafia pod klucz `detail_as`, a URL dokumentu to `detail_url#id` |
| `drop_fields` | Wzorce (`fnmatch`) pól usuwanych z treści, np. `"provinces.*.id"` |
| `require_fields` | Rekord bez któregoś z tych pól jest pomijany (`skipped`) |
| `headers` | Dodatkowe nagłówki, np. `{X-Goog-Api-Key: "${GOOGLE_FACTCHECK_API_KEY}"}`. `${ZMIENNA}` pochodzi ze środowiska (brak zmiennej daje `error` źródła). Idą tylko do hosta źródła, nie po przekierowaniu na inny host. `User-Agent` jest zabroniony. Klucza nie wpisuj do `sources.yaml` ani do URL, bo URL trafia do danych |
| `url_template` | Adres dokumentu z pól rekordu zamiast `endpoint#id`, np. `{claimReview[0].url}` (artykuł weryfikacji) |
| `next_token_path`, `token_param`, `max_pages` | Stronicowanie tokenem: wartość spod `next_token_path` (np. `nextPageToken`) trafia do parametru `token_param` (np. `pageToken`) kolejnego zapytania. Koniec, gdy nie ma tokenu albo po `max_pages` (domyślnie 10) |

Przy `page_template`, `next_token_path` lub `detail_url` zapytania są bezwarunkowe, bo 304 dla pierwszej strony nic nie mówi o reszcie.
Endpointy aplikacji JS najłatwiej znaleźć w narzędziach deweloperskich przeglądarki (zakładka Sieć, filtr Fetch/XHR).

### `mcp_tool` (EUvsDisinfo)

Źródła `euvsdisinfo-cases` (~19 900 spraw dezinformacji) i `euvsdisinfo-articles` (~12 000 artykułów) są w [`sources.d/euvsdisinfo.yaml`](sources.d/euvsdisinfo.yaml). Wymagają `EUVSDISINFO_API_KEY` w środowisku (klucz z formularza na euvsdisinfo.eu). Klucz idzie nagłówkiem `Authorization`, więc nie trafia do danych.

- Serwer zwraca Markdown (`**Case N: tytuł**`, pola `**Claim:**`, `**Fact Check:**`, `**Source URL:**`…). Adresem dokumentu jest `Source URL`, a treścią są pola w liniach `Etykieta: wartość`.
- Klucze: `endpoint`, `tool`, `headers`, `page_size` (maks. 100), `max_pages` (zwykły przebieg czyta od najnowszych do strony bez zmian), `backfill_pages_per_run`.
- Archiwum jest dociągane kursorem `backfill_offset` w `state.json`, aż do `Has next page: No` (`backfill_done`). Przy 20 stronach na przebieg całe archiwum schodzi w ok. 10 przebiegach (`once --source euvsdisinfo-cases`).
- Test `tests/test_mcp.py` używa zapisanych prawdziwych odpowiedzi serwera (`tests/fixtures/euvsdisinfo_*.sse`).

Lista kluczy jest w nagłówku `sources.yaml`, a wartości domyślne w `config.py`. Profil `x-govpl` to kotwica YAML; klucze wpisane w źródle nadpisują profil (łączenie płytkie).
Uwaga: nieznane klucze (literówki) są po cichu ignorowane. Bez `id_field`/`id_template` każda zmiana rekordu `json_api` daje nowy dokument zamiast wersji.

## Zasady grzeczności (nie do negocjacji)

- robots.txt jest sprawdzany przed każdym adresem i przy każdym przekierowaniu, także dla skryptów/XHR strony renderowanej w Playwright. 5xx lub błąd robots.txt oznacza „wszystko zabronione” na pewien czas.
- Odstęp co najmniej 3 s między zapytaniami do jednego hosta, wspólny dla wszystkich źródeł. `Crawl-delay` może go wydłużyć. Krócej tylko dla localhost (testy).
- 429/503 i 403 powodują wycofanie hosta (`Retry-After` albo wartość domyślna) zapisane w stanie, bez ponawiania w pętli.
- CAPTCHA lub wyzwanie antybotowe daje `blocked`. Żadnych proxy, stealth, zmiany UA ani logowania.
- Uczciwy User-Agent `FaktBot/<wersja> (+mail)`. Bez maila crawler nie startuje.
- Zapytania warunkowe (ETag/Last-Modified). Dokument pobrany raz jest podawany przeglądarce (`route.fulfill`), a obrazy, style, fonty i trackery są blokowane.

Tych reguł celowo nie da się wyłączyć w konfiguracji.

## Statusy przebiegu źródła

| Status | Znaczenie | Co robić |
| --- | --- | --- |
| `ok` | Pobrano (może być nic nowego) | nic |
| `not_modified` | Serwer odpowiedział „bez zmian” | nic |
| `warn` | Część pozycji z błędami albo 0 linków na liście | Sprawdź `crawler.log` i selektory |
| `error` | Wyjątek źródła | Ślad stosu w `crawler.log` |
| `throttled` | 429/503 albo robots.txt niedostępny | Czekać, crawler sam ponowi |
| `blocked` | 403, wyzwanie antybotowe albo zakaz w robots.txt | **Nie obchodzić**. Poszukaj dozwolonego kanału (RSS, API). |

`status` pokazuje też alarmy: `BLOCKED`/`THROTTLED`, trzy błędy z rzędu, `cisza >Nh` (`silence_alert_hours`).
Pozycja, która zawiodła 3 razy z rzędu, dostaje `failed` i nie jest już pobierana. Przywrócenie wymaga edycji `state.json`.

## Pliki wynikowe (`data/crawl/`)

| Plik | Zawartość | Dla RAG? |
| --- | --- | --- |
| `documents.jsonl` | Jedna linia = jedna **wersja** dokumentu. Tylko dopisywany. | **tak, źródło prawdy** |
| `crawl_log.jsonl` | Jedna linia na przebieg źródła: `status`, liczniki, `message` | tak (stan źródeł) |
| `current.json` | Indeks `url → bieżąca wersja` (odbudowywany z JSONL, gdy starszy) | nie |
| `state.json` | Stan wewnętrzny: znane adresy, kolejka, walidatory, wycofania, `active_ids` | nie (wyjątek: `active_ids`) |
| `crawler.log` | Log tekstowy | nie |

Rekord `documents.jsonl`: `id` (`<hash url>-v<n>`), `url` (klucz dokumentu), `source`, `institution`, `doc_type`, `region`, `title` (może być `null`), `content` (zwykły tekst), `content_hash`, `version`, `published_at` (ISO ze strefą albo `null`), `fetched_at` (UTC), `fetch_method`, opcjonalnie `attribution`.

- Nowa wersja powstaje tylko przy zmianie `content`. Zmiana samego tytułu lub daty nie tworzy nowej linii.
- Ostatnia linia bez `\n` może być w trakcie zapisu, więc czytelnik ją pomija. Konsument nie zapisuje nic w katalogu danych i ignoruje nieznane pola.
- Crawler nie wykrywa usunięć u źródła. Wyjątkiem są rekordy `json_api`: aktualne są tylko te z `state.json → sources.<nazwa>.active_ids`.
- Załączniki (PDF) nie są pobierane. Treść kończy się wtedy listą „Komunikat opublikowany w załączniku:”.
- Treść jest zapisywana bez maskowania danych osobowych.

## Testy

`uv run pytest backend/crawler` uruchamia test end-to-end bez internetu: lokalne serwery (RSS, lista gov.pl, sitemapa, JSON, strona JS, 429, robots 5xx), crawler wołany przez CLI na katalogu tymczasowym.
Bez Chromium testy wymagające przeglądarki są pomijane.
