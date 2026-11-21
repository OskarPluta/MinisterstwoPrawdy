# FaktBot (crawler)

Okresowo pobiera komunikaty i ostrzeżenia instytucji państwowych i zapisuje je jako zwykły tekst z metadanymi i pełną historią wersji.
Wynikiem są wyłącznie pliki w `data/crawl/`: crawler nie zna bazy ani RAG, a dane do bazy ładuje `rag ingest`.
Źródła opisuje deklaratywnie [`sources.yaml`](sources.yaml), więc nowe źródło nie wymaga zmian w kodzie.

## Uruchamianie

```bash
uv run playwright install chromium         # raz; przeglądarka do renderowania stron
export CRAWLER_CONTACT=zespol@example.pl   # obowiązkowy mail w User-Agencie; crawler nie czyta .env
uv run crawler once --max-items 5          # jeden przebieg, maks. 5 nowych pozycji na źródło
uv run crawler once --source rcb           # tylko wybrane źródła (opcja powtarzalna, działa też dla wyłączonych)
uv run crawler run                         # pętla: każde źródło co interval_minutes
uv run crawler status                      # tabela statusów i alarmów (bez sieci, nie zmienia danych)
```

- Opcje globalne (`--config`, `--data-dir`, `-v`) podaje się **przed** podkomendą. Ścieżka względna `--data-dir` liczy się od katalogu `sources.yaml`, np. `uv run crawler --data-dir ../../data/snapshot status`.
- Pierwszy Ctrl+C/SIGTERM kończy pracę po bieżącym źródle, a drugi przerywa od razu. Stan przetrwa restart.
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

## Typy adapterów

Wybieraj w tej kolejności, od najlepszego. **Jeśli źródło ma API** (udokumentowane albo endpoint JSON, z którego korzysta sama strona), używaj `json_api` zamiast scrapowania HTML:

| Typ | Kiedy | Uwagi |
| --- | --- | --- |
| `rss` | Źródło ma kanał RSS/Atom | Domyślnie pobiera pełny artykuł (`fetch_full`). Odrzuca wpisy spoza domeny kanału. |
| `json_api` | Publiczne API (IMGW, RSO, GIOŚ) | Każdy rekord to dokument `endpoint#id` z treścią w liniach `klucz: wartość`. Co przebieg przetwarza wszystkie rekordy, a o zapisie decyduje hasz. Obsługuje stronicowanie i dociąganie szczegółów rekordu (niżej). |
| `sitemap` | Brak kanału, jest sitemap.xml | Ponownie sprawdza adres, gdy wzrośnie jego `lastmod`. |
| `html_list` | Lista aktualności z datami | Heurystyka bierze linki, przy których stoi data, w kafelku z jednym linkiem, i pomija `nav`/`header`/`footer`. Gdy nie wystarcza, ustaw `link_selector`. |
| `static_page` | Pojedyncza strona, której zmiany śledzimy | Pobierana co przebieg. Data tylko z metadanych strony. |

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

Przy `page_template` lub `detail_url` zapytania są bezwarunkowe, bo 304 dla pierwszej strony nic nie mówi o reszcie.
Endpointy aplikacji JS najłatwiej znaleźć w narzędziach deweloperskich przeglądarki (zakładka Sieć, filtr Fetch/XHR).

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
