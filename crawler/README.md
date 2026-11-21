# FaktBot – crawler oficjalnych komunikatów

Zbiera komunikaty instytucji państwowych (gov.pl, policja, RCB, IMGW, CERT…) do plików JSON
z pełną historią wersji. To etap 1 systemu weryfikacji dezinformacji: bez RAG, bez bazy danych.

## Instalacja

```bash
python3.12 -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt                           # zawiera model spaCy pl_core_news_md
playwright install chromium
```

## Konfiguracja

* `sources.yaml`: lista źródeł. Nowe źródło to nowy wpis (typy: `rss`, `html_list`, `sitemap`,
  `json_api`, `static_page`). Opis pól jest w nagłówku pliku.
* **Mail kontaktowy** (obowiązkowy, trafia do User-Agenta `FaktBot/1.0 (+mail)`):
  `export CRAWLER_CONTACT=zespol@example.pl` albo `settings.contact` w `sources.yaml`.
  Bez niego crawler nie wystartuje.

## Uruchamianie

```bash
python crawler.py once                         # jeden przebieg wszystkich źródeł
python crawler.py once --source rcb --source imgw-meteo
python crawler.py once --max-items 5           # ostrożny przebieg (tymczasowy limit na źródło)
python crawler.py run                          # pętla ciągła wg interval_minutes
python crawler.py status                       # tabela: status, ostatnie nowe treści, alarmy
```

Ctrl+C / SIGTERM kończy pracę po bieżącym źródle (drugi Ctrl+C przerywa od razu).
Stan przetrwa restart. Dwa procesy na tym samym katalogu `data/` się nie uruchomią (blokada `data/.lock`).

## Dane (`data/`)

| plik | zawartość |
|---|---|
| `documents.jsonl` | **tylko dopisywany**: jedna wersja dokumentu na linię. Zmieniona treść pod tym samym URL to nowa linia z `version+1`, stare linie zostają |
| `current.json` | indeks `url → {version, content_hash, id}` (zapis atomowy) |
| `state.json` | ETagi/Last-Modified, znane adresy, kolejka zaległych, wycofania hostów, status źródeł (zapis atomowy) |
| `crawl_log.jsonl` | jedna linia na uruchomienie źródła (`status`: ok, not_modified, warn, blocked, throttled, error) |
| `crawler.log` | log tekstowy |

## Zasady „grzecznego” crawlowania (wbudowane, bez wyłącznika)

* robots.txt przed każdym adresem, także przy przekierowaniach (RFC 9309: 4xx oznacza brak ograniczeń,
  5xx, błąd sieci lub pętla przekierowań wstrzymuje host na 30 min, w pamięci procesu). Respektuje `Crawl-delay`.
  Skrypty/XHR ładowane przez stronę w Playwright też są sprawdzane z zapamiętanym robots.txt.
* Uczciwy User-Agent, bez wtyczek stealth, bez CAPTCHA, proxy i logowania. Wyzwanie antybotowe daje status `blocked`.
* Maks. 1 zapytanie na 3 s do jednego hosta, wspólnie dla wszystkich źródeł.
* 429/503: wycofanie zgodnie z `Retry-After` (domyślnie 15 min). 403: `Retry-After`, domyślnie godzina.
  Wyzwanie antybotowe: godzina. Bez ponawiania w pętli.
* Zapytania warunkowe (`If-None-Match` / `If-Modified-Since`). Dokument główny strony pobieramy sami
  (z walidatorami) i podajemy go przeglądarce przez `route.fulfill`, więc renderowanie nie dubluje zapytania.
  Obrazy, fonty, CSS, media i trackery są blokowane.
* `mask_pii: true` zamienia osoby na `[OSOBA]` (spaCy `persName`) i PESEL na `[PESEL]` **przed zapisem**.
  Bez modelu spaCy crawler nie wystartuje, jeśli któreś uruchamiane źródło ma `mask_pii: true`.

> **⚠️ Maskowanie jest obecnie wyłączone.** Na polecenie właściciela projektu (2026-11-21), na czas prototypu,
> źródła policji mają `mask_pii: false`, więc w `data/` zapisywane są pełne imiona i nazwiska z komunikatów.
> Dane nie trafiają do repozytorium (`data/` jest w `.gitignore`). **Przed publikacją danych, udostępnieniem
> zbioru albo wystawieniem RAG na zewnątrz trzeba przywrócić `mask_pii: true`** (albo maskować w kroku eksportu).

## Test lokalny (bez internetu)

```bash
python tests/run_local_test.py
```

Uruchamia lokalny serwer z robots.txt, RSS, listą w stylu gov.pl, sitemapą, JSON-em, stroną statyczną,
stroną renderowaną przez JS i endpointem 429. Sprawdza: brak ponownego pobierania, nowe wersje po zmianie treści,
odfiltrowanie menu, robots.txt, 429 → `throttled`, maskowanie PII i odmowę startu bez maila lub modelu.

## Specyfikacja

Szczegółowy opis zachowania: [`docs/SPEC.md`](docs/SPEC.md) (architektura, zasady, adaptery, statusy, CLI),
[`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md) (format plików w `data/`, JSON Schema, wskazówki dla RAG),
[`docs/CONFIG.md`](docs/CONFIG.md) (wszystkie klucze `sources.yaml`, dodawanie źródła).

## Heurystyka `html_list`

Domyślnie bierzemy link, przy którym stoi data (`21.11.2026`, `21.11.2026 14:05`, `2026-11-21`, `21 listopada 2026`, `21-11-2026`).
Datę szukamy tylko w przodkach, w których ten link jest **jedynym** linkiem (kafelek artykułu), i tylko w krótkich
blokach tekstu. Linki w `nav/header/footer` pomijamy. Pomijamy też PDF-y, obrazy, załączniki i obce domeny.
0 znalezionych linków daje status `warn` (prawdopodobnie zmieniła się struktura strony).
Gdy heurystyka nie wystarcza, ustaw `link_selector` (CSS).
