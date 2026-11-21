# FaktBot – specyfikacja techniczna (stan faktyczny kodu, wersja 1.0)

Dokument opisuje, co crawler **faktycznie robi**. Gdy README, RAPORT albo komentarze w `sources.yaml` mówią
co innego, obowiązuje ten dokument (i kod). Format plików wynikowych: [DATA_FORMAT.md](DATA_FORMAT.md).
Pełna lista kluczy konfiguracji: [CONFIG.md](CONFIG.md).

Słowa **MUSI / NIE WOLNO / POWINIEN** mają znaczenie normatywne: opisują reguły, które kod realizuje dziś
i których każda zmiana kodu musi dalej przestrzegać.

---

## 1. Cel i zakres

FaktBot to etap pozyskiwania danych (ingestion) projektu „Prawdowid”, systemu wykrywania dezinformacji.
Okresowo pobiera oficjalne komunikaty instytucji państwowych (gov.pl, policja, IMGW, RCB, urzędy morskie…)
i zapisuje je jako zwykły tekst z metadanymi i pełną historią wersji do plików w katalogu `data/`.
Z tych plików korzysta później RAG (indeksowanie nie należy do crawlera).

### 1.1. Co crawler robi

* Pobiera źródła pięciu typów: `rss`, `html_list`, `sitemap`, `json_api`, `static_page`. Źródła są opisane
  deklaratywnie w `sources.yaml`, więc nowe źródło nie wymaga zmian w kodzie.
* Renderuje strony HTML w Chromium (Playwright), żeby wykonał się JavaScript strony. Dokument główny pobiera
  sam i podaje go przeglądarce.
* Wyciąga treść artykułu (selektor CSS z konfiguracji, potem trafilatura, potem `<article>`/`<main>`), tytuł i datę publikacji.
* Wersjonuje dokumenty po haszu treści. `documents.jsonl` jest tylko dopisywany.
* Przestrzega robots.txt (RFC 9309), `Crawl-delay`, limitu 1 zapytania na 3 s na host, wycofuje się po
  429/503/403 i po wykryciu wyzwania antybotowego. Wysyła zapytania warunkowe.
* Opcjonalnie maskuje dane osobowe (spaCy NER + PESEL) przed zapisem. **Obecnie wyłączone**, patrz §8.
* Prowadzi log przebiegów (`crawl_log.jsonl`), stan (`state.json`) i raport `status` z alarmami.

### 1.2. Czego crawler NIE robi

* Nie indeksuje, nie liczy embeddingów, nie ma bazy danych ani API. Wynikiem są wyłącznie pliki.
* Nie pobiera i nie parsuje załączników (PDF, DOC…). Gdy treść strony jest krótka, zapisuje tylko listę linków
  do załączników (§7.5).
* Nie zachowuje linków wychodzących z treści artykułu. Tekst jest „płaski”, adresy z `<a href>` giną, a plan
  `PLAN_OBSLUGI_ZRODEL.md` przewiduje `outbound_links`.
* Nie wykrywa usunięcia dokumentu u źródła. Wyjątkiem jest `json_api`: lista `active_ids` w stanie (§6.4).
* Nie deduplikuje treści między różnymi adresami URL.
* Nie obchodzi zabezpieczeń: brak proxy, rotacji UA, wtyczek stealth, rozwiązywania CAPTCHA i logowania.
* Nie stronicuje `json_api`: pobiera jeden endpoint, jedną odpowiedź.
* Nie działa równolegle. Jeden proces i jeden wątek obsługują źródła po kolei.
* Nie czyta JSON-LD. Daty bierze z metatagów, z heurystyki trafilatury, z kanału albo z listy.

---

## 2. Architektura

### 2.1. Moduły

| moduł | odpowiedzialność |
|---|---|
| `crawler.py` | CLI (argparse), logowanie, blokada procesu, obsługa sygnałów, kody wyjścia |
| `faktbot/config.py` | wczytanie `sources.yaml`, wartości domyślne (`settings`, `defaults`), walidacja źródeł, budowa User-Agenta, lista hostów lokalnych i twarde minimum 3 s |
| `faktbot/storage.py` | `Store`: zapis `documents.jsonl` (append + fsync), `current.json` i `state.json` (zapis atomowy), `crawl_log.jsonl`, walidatory HTTP, odbudowa indeksu |
| `faktbot/robots.py` | parser robots.txt (`RobotsRules`): grupy, `*`, `$`, najdłuższe dopasowanie, `Crawl-delay` |
| `faktbot/fetcher.py` | `Fetcher`: bramka każdego zapytania (wycofanie → robots → odstęp → GET), ręczne przekierowania, zapytania warunkowe, wykrywanie wyzwań antybotowych, renderowanie w Playwright; wyjątki `RobotsBlocked`, `RobotsUnavailable`, `Throttled`, `Blocked`, `HttpError` |
| `faktbot/extract.py` | daty (formaty polskie, RFC 822, ISO 8601), ekstrakcja artykułu, usuwanie banerów cookies, załączniki, heurystyka linków z datą, parsery RSS/Atom i sitemap |
| `faktbot/pii.py` | `PiiMasker`: maskowanie osób (`[OSOBA]`) i numerów PESEL (`[PESEL]`) |
| `faktbot/adapters.py` | adaptery pięciu typów, wybór kandydatów, kolejka zaległych, izolacja błędów, zapis dokumentu (z maskowaniem); wyjątki `SourceBlocked`, `SkipItem` |
| `faktbot/runner.py` | uruchomienie źródła i wyliczenie statusu, harmonogram (`run`/`once`), raport `status`, budowa kontekstu (UA, maskowanie) |
| `tests/local_server.py`, `tests/run_local_test.py` | test end-to-end bez internetu (§13) |
| `tools/inspect_xhr.py` | narzędzie diagnostyczne spoza crawlera: wypisuje żądania XHR/fetch strony (§14) |

### 2.2. Przebieg uruchomienia (`run` / `once`)

1. **Konfiguracja.** Wczytanie i walidacja `sources.yaml` (§11.1). Błąd daje kod wyjścia 2.
2. **Logowanie.** Utworzenie katalogu danych i dopisywanie do `data/crawler.log` oraz na stderr.
3. **Wybór źródeł.** Bez `--source` brane są źródła z `enabled: true` (domyślnie true), w kolejności z pliku.
   Z `--source NAZWA` (tylko `once`) brane są wskazane źródła **niezależnie od `enabled`**. Nieznana nazwa daje kod 2.
4. **Blokada procesu.** `flock(LOCK_EX | LOCK_NB)` na pliku `<data_dir>/.lock`. Jeśli inny proces trzyma blokadę,
   kod wyjścia 3. Blokada trwa do końca procesu, a plik `.lock` zostaje na dysku.
5. **Wczytanie stanu** (`Store`): `current.json`, `state.json`, ewentualna odbudowa indeksu (DATA_FORMAT §6).
6. **Kontrole startowe:**
   * **Mail kontaktowy.** Zmienna `CRAWLER_CONTACT` ma pierwszeństwo przed `settings.contact`. Brak maila albo
     wartość niepasująca do `^[^@\s]+@[^@\s]+\.[^@\s]+$` daje kod 2 i komunikat z nazwą `CRAWLER_CONTACT`.
   * **Model spaCy**, tylko jeśli **którekolwiek z wybranych źródeł** ma `mask_pii: true`. Ładowane są kolejno modele
     z `settings.spacy_models` (pierwszy, który się wczyta, i musi mieć komponent `ner`). Niepowodzenie,
     także brak samego pakietu spaCy, daje kod 2. Dziś żadne źródło nie ma `mask_pii: true`, więc spaCy nie jest ładowany.
   * Kolejność jest dokładnie taka: blokada, potem mail, potem spaCy. Odmowa startu po zajęciu blokady jej nie zostawia,
     bo blokada znika razem z procesem.
7. Zapis w logu linii `User-Agent: FaktBot/1.0 (+mail)` i instalacja obsługi SIGINT/SIGTERM (§10.2).
8. **Harmonogram:** `once` albo `run` (§10.1). Dla każdego źródła:
   1. **adapter** typu źródła wyznacza listę adresów (kanał / lista / sitemapa / API / stałe URL-e),
   2. **wybór kandydatów**: nowe, zaległe i do ponownego sprawdzenia, z limitem (§6.6),
   3. dla każdego kandydata **bramka pobierania** (§3): wycofanie, robots.txt, odstęp, GET z walidatorami,
      ręczne przekierowania, obsługa 304/403/429/503, detekcja wyzwania,
   4. **renderowanie** HTML w Playwright z `route.fulfill` (§3.9),
   5. **ekstrakcja** treści, tytułu i daty (§7),
   6. **maskowanie PII**, jeśli `mask_pii: true` (§8),
   7. **zapis**: porównanie hasza z `current.json`, dopisanie wersji do `documents.jsonl`, zapis `current.json` (§9),
   8. **zatwierdzenie walidatorów** (ETag / Last-Modified) dopiero po udanym przetworzeniu,
   9. po źródle: wyliczenie **statusu** (§5), linia w `crawl_log.jsonl`, aktualizacja i zapis `state.json`.
9. Na koniec (także po przerwaniu) zapis `state.json` i zamknięcie klienta HTTP oraz przeglądarki. Kod wyjścia 0.

---

## 3. Zasady „grzeczności” i legalności (wymagania normatywne)

Zasady są wbudowane i nie mają wyłącznika w konfiguracji.

### 3.1. User-Agent

* Każde zapytanie HTTP crawlera MUSI mieć nagłówek `User-Agent: FaktBot/1.0 (+<mail kontaktowy>)`.
  Wersja pochodzi z `faktbot.__version__`.
* Przeglądarka Playwright MUSI używać tego samego UA (kontekst z `user_agent=UA`, `locale="pl-PL"`).
* Klient HTTP wysyła też `Accept-Language: pl,en;q=0.5`, a zależnie od typu zasobu nagłówek `Accept` (§6).
* Crawler NIE WOLNO uruchomić bez poprawnego maila kontaktowego (§2.2 pkt 6).
* Token produktu do dopasowania grup robots.txt to część UA przed pierwszym `/`, czyli `FaktBot`.

### 3.2. Bramka pobierania: kolejność kroków

Każde zapytanie wysłane przez crawler (poza podzasobami ładowanymi przez przeglądarkę, §3.9) przechodzi przez
bramkę. Na **każdym skoku przekierowania** wykonuje ona:

1. **wycofanie**: aktywny wpis `backoff` dla hosta skoku daje natychmiast `Blocked` (`kind == "blocked"`)
   albo `Throttled` (pozostałe przypadki, także wpis bez `kind`), bez żadnego zapytania sieciowego,
2. **robots.txt** dla originu skoku (`scheme://host[:port]`). Adres niedozwolony daje `RobotsBlocked`,
3. **odstęp** od poprzedniego zapytania do tego hosta (§3.4), z uwzględnieniem `Crawl-delay`,
4. **GET** bez automatycznego śledzenia przekierowań.

Kod 301, 302, 303, 307 lub 308 z nagłówkiem `Location` oznacza przejście do adresu docelowego (względny adres jest
rozwiązywany, fragment usuwany) i powtórzenie kroków 1–4. Dozwolonych jest **5 przekierowań** (łącznie 6 zapytań).
Szóste przekierowanie daje `HttpError("Za dużo przekierowań")`. Nagłówki warunkowe są wysyłane na każdym skoku.

### 3.3. robots.txt (RFC 9309)

* Crawler MUSI sprawdzić robots.txt originu przed każdym zapytaniem o zasób z tego originu, także po przekierowaniu.
  Wyjątkiem jest samo zapytanie o `/robots.txt`.
* Pobranie `/robots.txt` przechodzi przez wycofanie i odstęp, ale nie przez sprawdzenie robots. Przekierowania
  są śledzone (do 5, także na inny host).
* Interpretacja odpowiedzi:

| odpowiedź na `/robots.txt` | skutek | ważność w pamięci podręcznej |
|---|---|---|
| 2xx | reguły z treści | `robots_ttl_hours` = **24 h** |
| 429 | wycofanie hosta (`Retry-After` albo `default_backoff_minutes` = **15 min**, `kind: throttled`, powód „429 na robots.txt”) i `Throttled` | nie jest zapamiętywana |
| inne 4xx (401, 403, 404, 410…) | brak ograniczeń (allow all) | **24 h** |
| 5xx i każdy inny kod (np. 3xx bez `Location`) | **wszystko zabronione**, `RobotsUnavailable` | `robots_unavailable_retry_minutes` = **30 min** |
| błąd sieci (wyjątek httpx) | wszystko zabronione, `RobotsUnavailable` | **30 min** |
| ponad 5 przekierowań | wszystko zabronione, `RobotsUnavailable` (jak błąd sieci) | **30 min** |

* Pamięć podręczna robots.txt istnieje **tylko w pamięci procesu**, w kluczu `scheme://netloc`. Po restarcie
  robots.txt jest pobierany ponownie.
* Przez 30 minut po 5xx, błędzie sieci albo pętli przekierowań każde kolejne zapytanie do tego originu kończy się
  `RobotsUnavailable` (bez zapytania sieciowego). Źródło dostaje `throttled`, a nieprzetworzone nowe pozycje wracają
  do `pending` (nie są oznaczane jako `robots_blocked`).
* Parser:
  * linie dzielone na `klucz: wartość`; komentarz od `#`; klucz bez rozróżniania wielkości liter; linie bez `:` pomijane;
  * kolejne linie `User-agent` tworzą jedną grupę; reguły przed pierwszą linią `User-agent` są ignorowane;
  * grupa pasuje do crawlera, gdy jej agent (małymi literami) jest równy `faktbot` albo ma postać `faktbot/<wersja>`
    (dokładne dopasowanie tokenu, RFC 9309). Pusty `User-agent:` ani prefiks (np. `fakt`) nie pasują.
    Reguły **wszystkich** pasujących grup są łączone. Jeśli żadna grupa nie pasuje, brane są grupy `*`.
    Bez grup: brak ograniczeń;
  * pusty `Disallow:` jest ignorowany (brak ograniczenia). Wartość, która nie zaczyna się od `/` ani `*`,
    dostaje wiodący `/`;
  * `*` oznacza dowolny ciąg, a końcowe `$` kotwiczy koniec. Dopasowanie liczy się od początku ścieżki
    **z query** (`/a/b?x=1`). Pusta ścieżka to `/`. Nie ma normalizacji kodowania procentowego;
  * wygrywa reguła o najdłuższym wzorcu (długość surowego tekstu wzorca). Przy remisie wygrywa `Allow`.
    Brak pasującej reguły oznacza zgodę;
  * `/robots.txt` jest zawsze dozwolony;
  * `Crawl-delay` (liczba zmiennoprzecinkowa) to maksimum z pasujących grup. Linie `Sitemap` są ignorowane.

### 3.4. Odstęp między zapytaniami do hosta

* Między dwoma zapytaniami crawlera do tego samego **hosta** (nazwa hosta bez portu i schematu, wspólnie dla wszystkich
  źródeł w procesie) MUSI minąć co najmniej:
  `max(settings.min_delay_seconds, 3.0, Crawl-delay)` sekund.
* **Wyjątek localhost:** dla hostów `localhost`, `::1`, `127.0.0.1` … `127.0.0.9` minimum 3,0 s nie obowiązuje
  i wystarcza `min_delay_seconds` (test używa 0,05 s). Dla wszystkich innych hostów NIE WOLNO zejść poniżej 3 s,
  nawet gdy `min_delay_seconds` jest mniejsze.
* `Crawl-delay` jest brany z robots.txt originu bieżącego skoku. Przy pobieraniu samego robots.txt się go nie stosuje.
* Moment zapytania jest liczony zegarem monotonicznym tuż przed wysłaniem i trzymany tylko w pamięci.
  Po restarcie pierwsze zapytanie do hosta idzie bez czekania.

### 3.5. Wycofanie (backoff)

| zdarzenie | czas wycofania | `kind` | `reason` | wyjątek |
|---|---|---|---|---|
| HTTP 429 lub 503 | `Retry-After`, a bez niego `default_backoff_minutes` = **15 min** | `throttled` | `HTTP 429` / `HTTP 503` | `Throttled` |
| HTTP 429 na robots.txt | j.w. | `throttled` | `429 na robots.txt` | `Throttled` |
| HTTP 403 | `Retry-After`, a bez niego `forbidden_backoff_minutes` = **60 min** | `blocked` | `HTTP 403` | `Blocked` |
| wyzwanie antybotowe (HTTP lub po renderowaniu) | `forbidden_backoff_minutes` = **60 min** (bez `Retry-After`) | `blocked` | `wyzwanie antybotowe (<znacznik>)` | `Blocked` |

* `Retry-After` w postaci liczby sekund daje `max(wartość, 1)` s. W postaci daty HTTP daje `max(data − teraz, 1)` s.
  Wartość nieczytelna daje wartość domyślną. **Nie ma górnego limitu.**
* Wycofanie dotyczy **hosta** i jest zapisywane od razu w `state.json` (`backoff.<host>` = `{until, reason, kind}`),
  więc przetrwa restart. Póki trwa, NIE WOLNO wysłać do hosta żadnego zapytania, także o robots.txt.
* Wpis wygasły jest usuwany przy najbliższym sprawdzeniu hosta.
* Crawler NIE WOLNO ponawiać zapytania w pętli. Po 429/503/403 bieżąca pozycja i pozostałe pozycje tego hosta
  w źródle są odkładane do następnego przebiegu (§6.7).
* Inne kody ≥ 400 (np. 404, 500, 502) nie wywołują wycofania. Dają `HttpError`, który jest liczony jako błąd pozycji
  albo źródła.

### 3.6. Wykrywanie wyzwań antybotowych / CAPTCHA

Detekcja działa na każdej odpowiedzi HTML (§3.8) pobranej przez bramkę (z wyjątkiem samego robots.txt)
i drugi raz na HTML-u po renderowaniu.
Zasada: wyzwania nie obchodzimy, tylko wycofujemy się z hosta (§3.5) i kończymy pozycję lub źródło statusem `blocked`.

* **Znaczniki mocne** (typowe dla stron-wyzwań; wyszukiwane bez rozróżniania wielkości liter):
  `cf-browser-verification`, `cf_chl_opt`, `cf-challenge`, `/cdn-cgi/challenge-platform/h/`,
  `<title>just a moment...</title>`, `attention required! | cloudflare`, `checking your browser before`,
  `request unsuccessful. incapsula incident id`, `incapsula incident id`, `_incapsula_resource?swjiylwa`,
  `captcha-delivery.com`, `px-captcha`, `ddos protection by`.
* **Znaczniki słabe** (występują też na zwykłych stronach, np. reCAPTCHA w formularzu WordPressa):
  `g-recaptcha`, `h-captcha`, `hcaptcha.com`, `recaptcha/api`, `captcha`, `are you a robot`,
  `nie jesteś robotem`, `access denied`, `dostęp zabroniony`.
* Algorytm:
  1. szukaj pierwszego znacznika mocnego. Słabego szukaj **tylko wtedy, gdy nie ma mocnego**;
  2. policz **widoczny tekst**: usuń bloki `<script>`/`<style>` i wszystkie tagi, zwiń białe znaki;
  3. mocny znacznik oznacza wyzwanie, gdy status to 403/429/503 **albo** widoczny tekst ma < **3000** znaków;
  4. słaby znacznik oznacza wyzwanie, gdy status to 403/429/503 **albo** widoczny tekst ma < **400** znaków;
  5. w pozostałych przypadkach nie ma wyzwania.
* Kody 403/429/503 są obsłużone wcześniej (§3.5), a po renderowaniu sprawdzanie zakłada status 200. Dlatego
  w obecnym przepływie zawsze działają same progi długości (3000 / 400 znaków).

### 3.7. Zapytania warunkowe i walidatory

* Jeśli dla **żądanego** adresu (bez fragmentu) w `state.json → http_cache` są zapisane walidatory, crawler MUSI
  wysłać `If-None-Match` (ETag) i/lub `If-Modified-Since` (Last-Modified).
* Kiedy zapytanie jest warunkowe:

| zasób | warunkowe? |
|---|---|
| kanał RSS/Atom, sitemapa (każda podmapa), endpoint `json_api` | zawsze (jeśli są walidatory) |
| pierwsza strona listy `html_list` | tak |
| kolejne strony paginacji `html_list` | **nie** |
| artykuł lub strona statyczna | tylko gdy URL ma już wersję w `current.json` |

* **Zatwierdzanie walidatorów.** ETag i Last-Modified z odpowiedzi 200 są zapisywane **dopiero po udanym przetworzeniu**:
  * artykuł lub strona statyczna: po zapisie dokumentu (także z wynikiem `unchanged`);
  * kanał, lista, sitemapa: po przetworzeniu wszystkich kandydatów źródła. Jeśli źródło zakończy się wyjątkiem
    (np. wszystkie hosty w wycofaniu), walidatory listy **nie** są zapisywane i następny przebieg pobierze ją w całości;
  * `json_api`: po przetworzeniu wszystkich rekordów.
  * Odpowiedź 304 niczego nie zmienia. Odpowiedź bez obu walidatorów usuwa wpis z `http_cache`.

### 3.8. Kody odpowiedzi i treść

| status | zachowanie |
|---|---|
| 304 | odpowiedź „bez zmian”; adapter używa danych zapamiętanych (§6) |
| 403 | wycofanie (`blocked`), `Blocked` |
| 429, 503 | wycofanie (`throttled`), `Throttled` |
| 3xx bez `Location` (301/302/303/307/308 z `Location` śledzi bramka) i każde ≥ 400 poza powyższymi | `HttpError("HTTP <kod>")` |
| błąd sieci / timeout (`http_timeout_seconds` = **30 s**) | `HttpError` |
| 2xx | odpowiedź przyjęta |

* Treść zaczynająca się od sygnatury gzip (`1f 8b`) jest dodatkowo rozpakowywana (np. sitemapa `.gz` bez
  `Content-Encoding`). Tekst jest dekodowany w kodowaniu z odpowiedzi albo w UTF-8, z zamianą błędnych bajtów.
* Odpowiedź jest **HTML-em**, gdy `Content-Type` to `text/html` albo `application/xhtml+xml`, albo gdy nagłówka brak,
  a pierwsze 512 bajtów zaczyna się od `<!doctype html` lub `<html`.

### 3.9. Renderowanie w Playwright

1. Dokument główny pobiera bramka (§3.2) z `Accept: text/html,application/xhtml+xml`. Robots, odstęp, walidatory
   i przekierowania działają jak przy każdym zapytaniu.
2. Przy 304 albo odpowiedzi nie-HTML renderowania nie ma (`fetch_method = "http"`).
3. W przeciwnym razie przeglądarka (jedna instancja Chromium na proces, headless, uruchamiana przy pierwszym renderowaniu,
   bez własnej obsługi SIGINT/SIGTERM/SIGHUP) dostaje nowy kontekst: UA FaktBota, `locale pl-PL`,
   service workers zablokowane, JavaScript włączony. Kontekst jest zamykany po każdej stronie.
4. Przechwytywanie żądań, reguły w kolejności:
   1. nawigacja ramki głównej na adres końcowy dokumentu jest obsługiwana przez **`route.fulfill`**: status 200,
      oryginalny `Content-Type`, już pobrane bajty. Dokument nie jest pobierany drugi raz;
   2. zasoby typu `image`, `media`, `font`, `stylesheet`, `imageset`, `texttrack`, `manifest` oraz wszystko z domen
      trackerów (także subdomen): `google-analytics.com`, `googletagmanager.com`, `doubleclick.net`, `facebook.net`,
      `facebook.com`, `hotjar.com`, `gemius.pl`, `youtube.com`, `ytimg.com`, `twitter.com`, `x.com`, `linkedin.com`,
      `clarity.ms` są przerywane (`abort`);
   3. każda inna nawigacja ramki głównej (JS próbuje przekierować) jest przerywana;
   4. żądania do **innych hostów** niż host dokumentu są przerywane, chyba że to `script`, `xhr` albo `fetch`;
   5. żądanie do originu, którego robots.txt jest już w pamięci podręcznej (w praktyce: host dokumentu), jest przerywane,
      gdy reguły go zabraniają (sprawdzenie bez zapytań sieciowych);
   6. reszta (np. skrypty, XHR i fetch z tego samego lub obcego hosta) przechodzi do sieci.
5. `goto` czeka na `domcontentloaded` z limitem **30 s** (wartość stała, przekroczenie to błąd pozycji). Potem czeka
   na `networkidle` z limitem `render_timeout_seconds` = **20 s**. Przekroczenie tego drugiego limitu nie jest błędem:
   bierzemy bieżący DOM.
6. Wynik (`page.content()`) przechodzi detekcję wyzwania (§3.6). `fetch_method = "playwright"`.

**Ograniczenie:** podzasoby przepuszczone w pkt 4.6 (skrypty, XHR, fetch) wysyła przeglądarka bezpośrednio, jak przy
zwykłym otwarciu strony. Sprawdzany jest dla nich robots.txt z pamięci podręcznej (pkt 4.5), ale **nie** odstęp na host
ani wycofanie. Dotyczy to tylko zasobów, które strona sama ładuje; ich liczbę ograniczają reguły 4.2–4.5.

---

## 4. Wyjątki pobierania i ich znaczenie

| wyjątek | kiedy | jak traktowany na poziomie pozycji (§6.7) | na poziomie źródła (§5) |
|---|---|---|---|
| `RobotsBlocked` | robots.txt zabrania adresu | `skipped`, `known[url].robots_blocked = true` | `blocked` |
| `RobotsUnavailable` | robots.txt 5xx / błąd sieci | host „martwy” w tym przebiegu | `throttled` |
| `Throttled` | 429/503 albo aktywne wycofanie `throttled` | host „martwy” | `throttled` |
| `Blocked` | 403, wyzwanie antybotowe albo aktywne wycofanie `blocked` | host „martwy” | `blocked` |
| `SourceBlocked` | robots.txt blokuje **wszystkie** kanały RSS źródła | – | `blocked` |
| `SkipItem` | pozycja celowo pominięta (nie-HTML bez treści, RSS bez opisu przy `fetch_full: false`) | `skipped`, `known[url].skipped = komunikat` | – |
| `HttpError` i każdy inny wyjątek | 404/500, timeout, za dużo przekierowań, za krótka treść, błąd Playwright… | `errors += 1`, licznik `fails` | `error` |

---

## 5. Statusy źródła

Status jest wyliczany po każdym uruchomieniu źródła, zapisywany w `crawl_log.jsonl` (`status`) i w stanie
(`last_status`, `history`).

### 5.1. Status z wyjątku, który wyszedł z adaptera

| status | wyjątek |
|---|---|
| `throttled` | `Throttled`; `RobotsUnavailable` (do komunikatu dopisane „– zgodnie z RFC 9309 wstrzymano pobieranie”) |
| `blocked` | `Blocked`; `SourceBlocked`; `RobotsBlocked` (dopisane „– nic nie pobieram”) |
| `error` | każdy inny wyjątek (`errors += 1`, komunikat `Typ: treść` obcięty do 300 znaków, ślad w `crawler.log`) |

Wyjątek wychodzi z adaptera, gdy zawiedzie zapytanie „źródłowe” (kanał, strona listy, sitemapa, endpoint API;
dla RSS z wyjątkiem `RobotsBlocked` na pojedynczym kanale) albo gdy w pętli pozycji **wszystkie** hosty kandydatów
okażą się „martwe” (§6.7). W tym drugim przypadku ponownie zgłaszany jest pierwszy zapamiętany wyjątek, więc status
to `throttled` albo `blocked`. Dokumenty zapisane przed zdarzeniem zostają w danych.

### 5.2. Status ustawiony przez adapter na `warn`

* `html_list`: pobrano ≥ 1 stronę listy i znaleziono 0 linków („prawdopodobnie zmieniła się struktura strony”);
* `sitemap`: pobrano ≥ 1 sitemapę (200) i po filtrach zostało 0 adresów;
* pętla pozycji: część hostów była „martwa”, ale nie wszystkie („część hostów niedostępna: …”).

Status ustawiony przez adapter jest ostateczny, chyba że później adapter rzuci wyjątek (wtedy obowiązuje §5.1).

### 5.3. Status wyliczany (gdy adapter nie ustawił statusu i nie rzucił wyjątku)

Niech `produced = new + updated + unchanged`. Reguły w kolejności:

1. `errors > 0` i `produced == 0` → **`error`**
2. `errors > 0` → **`warn`**
3. adapter zgłosił wskazówkę „bez zmian” i `new + updated == 0` → **`not_modified`**
4. w pozostałych przypadkach → **`ok`** (także przy 0 znalezionych pozycjach, np. pusty kanał)

Wskazówka „bez zmian” pojawia się, gdy:

| typ | warunek |
|---|---|
| `rss` | każdy kanał zwrócił 304 albo jest zablokowany w robots.txt (przy ≥ 1 kanale z 304) |
| `html_list` | żadna strona listy nie została pobrana z kodem 200, a co najmniej jedna zwróciła 304 |
| `sitemap` | odwiedzono ≥ 1 sitemapę i żadna nie zwróciła 200 |
| `json_api` | endpoint zwrócił 304 |
| `static_page` | wszystkie adresy dały `unchanged` (304 albo ten sam hasz) i `errors == 0` |

`errors` liczy błędy pozycji (§6.7), błędy parsowania kanału RSS i błędy rekordów `json_api`.
Pozycje pominięte (`skipped`) nie wliczają się do `produced`.

### 5.4. Komunikat

`message` to unikalne komunikaty z przebiegu połączone `"; "` i obcięte do 2000 znaków. Gdy były adresy zablokowane
w robots.txt, na końcu dochodzi „pominięto N adres(ów) zablokowanych w robots.txt”, a w logu pole `blocked_urls`
(pierwsze 50).

---

## 6. Adaptery

### 6.1. `rss`

1. Każdy URL z `urls` jest pobierany przez bramkę z
   `Accept: application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5`.
   * `RobotsBlocked` na kanale: kanał pominięty, adres trafia do `blocked_urls`.
   * Każdy inny wyjątek przy kanale (429, 403, 404, błąd sieci…) przerywa **całe źródło** (§5.1).
   * 304: pozycje kanału są brane z `list_cache[<url kanału>]`. Dzięki temu `recheck_hours` działa także dla kanału bez zmian.
   * 200: parsowanie RSS 2.0 / Atom (§7.6). Błąd parsowania daje `errors += 1`, kanał jest pomijany, a jego walidatory
     nie są zapisywane. Pusty kanał dostaje komunikat „kanał … jest pusty”.
2. **Filtr domeny:** zostają tylko wpisy, których przybliżona domena rejestrowalna jest równa domenie adresu końcowego
   kanału (ostatnie 2 etykiety; dla `*.pl` z drugą etykietą `gov`, `com`, `org`, `net`, `edu`, `waw`, `info`, `biz`
   ostatnie 3). Odrzucone dają komunikat „pominięto link spoza domeny kanału: host”.
3. Zapisanie przefiltrowanych pozycji w `list_cache[<url kanału>]` (przy 200).
4. Wszystkie kanały zablokowane przez robots.txt: `SourceBlocked` (`blocked`).
5. `found` = liczba unikalnych URL-i ze wszystkich kanałów (także z `list_cache`).
6. Wybór kandydatów (§6.6) z regułą ponownego sprawdzania co `recheck_hours`.
7. Obsługa pozycji:
   * `fetch_full: true` (domyślnie): pobranie artykułu (§6.8), **tytuł z kanału ma pierwszeństwo**, data z kanału ma
     pierwszeństwo przed datą ze strony;
   * `fetch_full: false`: tylko sprawdzenie robots.txt dla adresu (zablokowany adres nie jest zapisywany).
     Wpis bez opisu daje `SkipItem`. W przeciwnym razie zapis opisu z kanału jako treści
     (tytuł i data z kanału, `fetch_method = "http"`). Bez minimum długości.
8. Zatwierdzenie walidatorów kanałów, które zwróciły 200 i dały się sparsować.

### 6.2. `html_list`

1. Dla każdego URL z `urls` lista stron to: pierwsza strona = URL z konfiguracji, a przy `pagination.template` dodatkowo
   strony `n = start … start + max_pages − 2` (domyślnie `start = 2`, `max_pages = 1`). `max_pages` liczy więc
   **wszystkie** strony łącznie z pierwszą, a bez `max_pages` jest tylko pierwsza. Adres strony n powstaje przez
   podstawienie `{n}` w szablonie (np. `page={n}&size=10`) i dopisanie lub podmianę tych parametrów w query
   (powtórzone klucze i puste wartości w query giną; fragment jest usuwany).
2. Każda strona listy jest **renderowana** (§3.9). Warunkowo tylko pierwsza.
   * 304: pozycje z `list_cache[<url>]` i koniec stron dla tego URL.
   * Inne wyjątki (429, 403, 404, robots…) przerywają **całe źródło**.
3. Z HTML-u wyciągane są linki heurystyką linków z datą (§7.7) z parametrami `path_prefix`, `link_selector`,
   `exclude_regex`, `require_date`, `skip_chrome`.
4. `list_cache` dostaje wynik **tylko pierwszej strony**.
5. **Reguła paginacji:** następną stronę pobieramy tylko wtedy, gdy bieżąca dała ≥ 1 link i **żaden** z jej linków
   nie jest jeszcze w `known`. Strona z choćby jednym znanym linkiem albo bez linków kończy paginację.
6. `found` = liczba unikalnych URL-i. Pobrana ≥ 1 strona i 0 linków daje `warn` (§5.2). Przetwarzanie kandydatów
   (np. z kolejki zaległych) i tak trwa dalej.
7. Wybór kandydatów (§6.6, `recheck_hours`), pobranie artykułów (§6.8): **tytuł ze strony artykułu ma pierwszeństwo**
   przed tekstem linku, a data z listy ma pierwszeństwo przed datą ze strony.
8. Zatwierdzenie walidatorów wszystkich pobranych stron listy. Walidatory stron 2+ są zapisywane, ale nieużywane.

### 6.3. `sitemap`

1. Używany jest **wyłącznie pierwszy** URL z `urls`. Jego host (dokładna nazwa) wyznacza domenę źródła.
2. Przechodzenie wszerz: kolejka zaczyna się od mapy głównej, odwiedza się najwyżej `max_sitemaps` = **20** map
   (licząc także te z 304). Każda mapa jest pobierana warunkowo z `Accept: application/xml, text/xml;q=0.9, */*;q=0.5`.
   * 304: mapa pominięta. **Jej adresy i podmapy nie są odtwarzane**, bo sitemap nie ma `list_cache`.
   * 200: elementy `<url>` dają adresy, a elementy `<sitemap>` (sitemapindex) podmapy. Podmapy z innego hosta są
     odrzucane. Pozostałe są sortowane od najnowszego `lastmod` (bez `lastmod` na końcu) i dopisywane na koniec kolejki.
   * Inne wyjątki przerywają całe źródło.
3. Filtry adresów: host różny od hosta mapy (liczone jako „zignorowano N adres(ów) z obcej domeny”), rozszerzenie
   plikowe w ścieżce (§7.7, lista `SKIP_EXT`), `path_prefix` (początek ścieżki), `exclude_regex` (wyszukiwanie w całym URL).
   Domyślne wykluczenia `html_list` (`/attachment/`, `/download/`…) **nie** są tu stosowane.
4. `found` = liczba adresów po filtrach (bez deduplikacji). Pobrana ≥ 1 mapa i 0 adresów daje `warn`.
5. **Logika `lastmod` (kiedy znany adres sprawdzić ponownie):**
   * `failed` → nigdy;
   * adres ma `lastmod` w mapie: sprawdzamy, gdy w `known` nie ma zapamiętanego `lastmod` albo nowy `lastmod`
     jest późniejszy;
   * adres bez `lastmod`: co `recheck_hours`.
6. Kolejność nowych: od najnowszego `lastmod`. Przed pobraniem artykułu `lastmod` **nie** jest traktowany jako data
   publikacji: datę bierzemy wyłącznie ze strony artykułu (także zgadniętą przez trafilaturę).
7. Pobranie artykułów (§6.8, tytuł ze strony), potem zatwierdzenie walidatorów map, które zwróciły 200.

### 6.4. `json_api`

1. Endpoint (`endpoint`, a bez niego pierwszy z `urls`) jest pobierany warunkowo z `Accept: application/json`.
   304 daje wskazówkę „bez zmian” i koniec (`found = 0`, `active_ids` bez zmian).
2. Odpowiedź jest parsowana jako JSON. Błąd daje status `error` źródła.
3. **`records_path`**: ścieżka z kropkami do listy rekordów (`a.b.0.c`, liczba = indeks listy). Brak klucza daje `error`.
   Jeśli wynik jest obiektem: bierzemy pierwszą wartość-listę, a gdy jej nie ma, obiekt traktujemy jako jeden rekord.
   Wynik niebędący listą daje `ValueError` (`error`).
4. `found` = liczba rekordów. **Nie ma** limitu `max_items_per_run`, kolejki ani `known`: co przebieg przetwarzane są
   wszystkie rekordy, a o zapisie decyduje hasz (§9).
5. **Spłaszczanie rekordu** (kolejność kluczy jak w JSON):
   * obiekt: klucze łączone kropką (`a.b`);
   * lista samych skalarów: jedna para `klucz: "w1, w2"`. Przy **`sort_lists: true`** wartości są sortowane leksykalnie
     jako napisy, żeby kolejność nie tworzyła nowych wersji. Pusta lista znika;
   * lista zawierająca obiekty lub listy: indeksy `klucz[0].pole`, kolejność bez sortowania;
   * `null` i puste napisy znikają, a pozostałe wartości to `str(v).strip()` (bool daje `True`/`False`).
6. **Identyfikator rekordu** (pierwsza pasująca reguła):
   1. `id_template`: szablon (pkt 8) przepuszczony przez slug (`ł→l`, usunięcie znaków diakrytycznych, małe litery,
      ciągi spoza `[a-z0-9]` zamienione na `-`, bez `-` na brzegach). Pusty wynik to błąd rekordu;
   2. `id_field`: wartość pod ścieżką z kropkami w **oryginalnym** rekordzie, jako `str`. Brak klucza, `null`
      albo `""` to błąd rekordu;
   3. bez obu: pierwsze 16 znaków hex SHA-256 z `json.dumps(rekord, sort_keys=True, ensure_ascii=False)`. Komunikat
      ostrzega, że wtedy każda zmiana treści daje nowy dokument zamiast nowej wersji.
7. **URL dokumentu** = `<endpoint dokładnie jak w konfiguracji>#<id>`.
8. **Szablony** (`id_template`, `title_template`): `{klucz}` albo `{klucz:spec}`, gdzie klucz to dosłowna nazwa po
   spłaszczeniu (np. `{obszary[0].wojewodztwo}`), a `spec` to specyfikacja formatu Pythona zastosowana do **napisu**
   (np. `{opublikowano:.4}` daje pierwsze 4 znaki). Brakujący klucz daje pusty napis. Spec liczbowa (np. `:d`) to błąd rekordu.
9. **Treść** = linie `klucz: wartość` w kolejności spłaszczenia, a przy `attribution` dodatkowo `\n\n<attribution>`.
10. **Tytuł** (`title_template`): wynik szablonu ze zwiniętymi białymi znakami, obcięty z obu stron ze znaków ` `, `–`, `-`.
    Następnie usuwane są puste nawiasy `( )`, wynik jest przycinany, a pusty daje `null`. Bez szablonu: `null`.
11. **Data** (`date_fields`): pierwsze pole z listy (klucz po spłaszczeniu), którego wartość daje się sparsować (§7.3).
    Bez takiego pola: `null`.
12. Zapis (§9) z `fetch_method = "http"`. Błąd pojedynczego rekordu daje `errors += 1` i przejście do kolejnego.
13. `state.sources.<name>.active_ids` = identyfikatory wszystkich rekordów z bieżącej odpowiedzi, w kolejności,
    także tych, których zapis się nie udał. To jedyny sygnał, które rekordy (np. ostrzeżenia) są nadal aktualne.
14. Zatwierdzenie walidatorów endpointu.

Powtórzony identyfikator w jednej odpowiedzi: kolejne wystąpienia dostają sufiks `~2`, `~3`… (np. `#A~2`), z komunikatem
w logu przebiegu. Dzięki temu rekordy nie nadpisują się nawzajem i nie powstają naprzemienne wersje (przy stałej
kolejności rekordów w odpowiedzi).

### 6.5. `static_page`

1. Każdy URL z `urls` to jedna pozycja. `found` = liczba URL-i.
2. **Wszystkie** adresy są pobierane w każdym przebiegu: bez `max_items_per_run`, `recheck_hours`, kolejki
   i pomijania `failed` (licznik `fails` jest prowadzony, ale nie wstrzymuje pobierania).
3. Pobranie artykułu (§6.8): tytuł ze strony, **zgadnięta data jest odrzucana** (§7.3). Zapytanie jest warunkowe,
   gdy URL ma już wersję.
4. Wskazówka „bez zmian”: wszystkie adresy `unchanged` i brak błędów.

### 6.6. Wybór kandydatów (`rss`, `html_list`, `sitemap`)

Wejście: pozycje z bieżącej listy, `known`, `pending`.

1. Deduplikacja po URL (pierwsze wystąpienie wygrywa).
2. Pozycja jest **nowa**, gdy URL nie jest w `known` **albo** ma `fails > 0` i nie ma `failed`
   (nieudane próby są ponawiane w każdym przebiegu, z priorytetem nowych).
3. Pozycja znana jest **do ponownego sprawdzenia** według reguły adaptera:
   * `rss`, `html_list`: brak `failed` i (brak `checked_at` albo `teraz − checked_at ≥ recheck_hours`);
   * `sitemap`: §6.3 pkt 5.
4. Do nowych dochodzą pozycje z **`pending`** (zaległe z poprzednich przebiegów, np. takie, które spadły z listy),
   o ile ich URL nie jest w `known` i nie pojawił się w bieżącej liście.
5. Nowe są sortowane od najnowszych (`published`, a bez niego `lastmod`; bez dat na końcu). Do ponownego sprawdzenia
   są sortowane od najdawniej sprawdzonych.
6. Limit `L` = `--max-items`, jeśli podano wartość różną od 0, w przeciwnym razie `max_items_per_run` (domyślnie **30**).
   Kandydaci = `nowe[:L]` + `do_sprawdzenia[:max(0, L − liczba_nowych)]`. Ponowne sprawdzenia dostają tylko miejsce
   niewykorzystane przez **wszystkie** nowe.
7. **`pending`** := nowe ponad limit (`nowe[L:]`), maksymalnie **300** pozycji (`MAX_PENDING`). Kolejka jest
   nadpisywana w każdym przebiegu.

### 6.7. Przetwarzanie pozycji i izolacja błędów

Izolacja działa na trzech poziomach:

* **pozycja:** wyjątek jednego artykułu nie przerywa pozostałych;
* **host:** `Throttled`, `Blocked` albo `RobotsUnavailable` przy pozycji oznacza host jako „martwy” do końca przebiegu
  źródła. Pozostałe pozycje tego hosta są pomijane bez zapytań, a pozycje innych hostów przetwarzane dalej;
* **źródło:** wyjątek źródła jest łapany przez runner (§5.1), a kolejne źródła działają normalnie. Wycofanie hosta jest
  globalne (w `state.json`), więc dotyczy też innych źródeł z tego hosta.

Skutki dla pozycji:

| wynik | liczniki | `known[url]` | inne |
|---|---|---|---|
| zapis `new` / `updated` / `unchanged` | odpowiedni licznik | utworzony lub zaktualizowany: `checked_at` = teraz, `published`/`lastmod` z listy, `fails` i `failed` usunięte | – |
| `RobotsBlocked` | `skipped += 1` | j.w. + `robots_blocked: true` | URL w `blocked_urls` |
| `SkipItem` | `skipped += 1` | j.w. + `skipped: "<powód, max 200 zn.>"` | – |
| host martwy (wyjątek albo wcześniejsza blokada w przebiegu) | – | bez zmian | jeśli URL nie jest w `known`, trafia na **początek** `pending` (limit 300) |
| inny wyjątek | `errors += 1` | `fails += 1` (nowy wpis: `{first_seen, fails}`). Przy `fails ≥ 3` (`MAX_FAILS`) `failed: true` i `checked_at` = teraz | komunikat w logu |

* Pozycja z `failed: true` **nie jest już nigdy pobierana** (`rss`, `html_list`, `sitemap`), także jeśli wcześniej
  miała zapisaną wersję. Przywrócenie wymaga ręcznej edycji `state.json`.
* Flagi `robots_blocked` i `skipped` nie są kasowane po późniejszym sukcesie. Mają charakter informacyjny i nie wpływają
  na wybór kandydatów.
* Po pętli: jeśli zbiór martwych hostów pokrywa **wszystkie** hosty kandydatów, zgłaszany jest ponownie pierwszy zapamiętany
  wyjątek (status `throttled`/`blocked`, walidatory listy niezatwierdzone). Jeśli tylko część, status to `warn`.

### 6.8. Pobranie artykułu (wspólne dla `rss` z `fetch_full`, `html_list`, `sitemap`, `static_page`)

1. Renderowanie (§3.9). Zapytanie warunkowe tylko wtedy, gdy URL jest w `current.json`. Odpowiedź 304 daje `unchanged`
   (bez zapisu i bez zatwierdzania walidatorów).
2. Dla HTML: ekstrakcja (§7) z `content_selector`, `title_selector`, `strip_title_suffix`, `drop_selectors`
   ze źródła. Przy `static_page` zgadnięta data jest odrzucana.
3. **Załączniki:** gdy odpowiedź jest HTML-em i tekst ma < **400** znaków, wyszukiwane są linki do załączników (§7.5).
4. **Opis z kanału:** gdy tekst ma < **80** znaków, a pozycja ma opis (tylko RSS) o długości ≥ **40** znaków,
   treścią zostaje opis, a `fetch_method` zmienia się na `"http"`.
5. Jeśli są załączniki, do tekstu (po `\n\n`, jeśli tekst nie jest pusty) dochodzi blok
   `Komunikat opublikowany w załączniku:` i linie `- <nazwa>: <url>` (maksymalnie 10).
6. Tekst krótszy niż **80** znaków (`MIN_CONTENT_CHARS`): przy odpowiedzi nie-HTML `SkipItem`
   („nie-HTML (<content-type>)”), przy HTML `ValueError` („nie udało się wyciągnąć treści”), czyli błąd pozycji.
7. Tytuł: dla RSS `tytuł_z_listy or tytuł_ze_strony`, dla pozostałych `tytuł_ze_strony or tytuł_z_listy`.
8. `published_at` = data z listy lub kanału (dla `sitemap` zawsze pusta), a gdy jej brak, data ze strony. Format ISO 8601.
9. Zapis (§9) pod URL-em **z listy** (nie adresem końcowym po przekierowaniach), potem zatwierdzenie walidatorów.

---

## 7. Ekstrakcja treści

### 7.1. Przygotowanie HTML

1. Parsowanie (BeautifulSoup + lxml).
2. **Usuwanie banerów cookies/RODO**:
   * każdy element (oprócz `html`, `body`, `main`, `article`), którego `id` lub klasa pasuje do
     `cookie|consent|gdpr|rodo-|cmplz|onetrust|cookiebar` (bez rozróżniania wielkości liter) i którego tekst ma
     < 2000 znaków;
   * każdy `div`/`section`/`aside`/`p` o tekście 1–699 znaków, który pasuje do `plik(i|ów)? cookie|cookies|ciasteczk`
     **i** do `zgod|akceptuj|ustawie(nia|ń) przeglądarki|wyrażasz|zgadzasz` oraz nie zawiera `article` ani `main`.
3. Usunięcie elementów pasujących do `drop_selectors` (CSS).

### 7.2. Tytuł (pierwszy niepusty)

1. tekst pierwszego elementu `title_selector`,
2. `<h1>` w `<article>` (a bez niego w `<main>` albo w całym dokumencie). Jeśli w tym zakresie nie ma `<h1>`,
   pierwszy `<h1>` dokumentu,
3. `meta og:title`,
4. `<title>`.

Następnie, przy `strip_title_suffix`, usuwany jest sufiks pasujący do wyrażenia `strip_title_suffix + \s*$`
(niezależnie od tego, skąd pochodzi tytuł).

### 7.3. Data publikacji

1. Metatagi (atrybut `property` albo `name`) w kolejności: `article:published_time`, `datePublished`, `dc.date`,
   `DC.date.issued`.
2. Jeśli brak, a treść jest wyciągana trafilaturą (czyli `content_selector` dał < 80 znaków albo go nie ma),
   brana jest data z `trafilatura.extract_metadata`. Taka data jest oznaczana jako **zgadnięta**.
   **Dla `static_page` data zgadnięta jest odrzucana** (zostaje `null`), bo heurystyka myliła np. datę rozporządzenia
   w treści z datą publikacji.
3. Parsowanie wartości daty, w kolejności:
   * RFC 822 / RFC 2822 (RSS),
   * ISO 8601 (`Z` jako `+00:00`),
   * formaty polskie, z których wygrywa wystąpienie **najwcześniej w tekście**: `DD.MM.RRRR`, `RRRR-MM-DD[ T]HH:MM`
     (sekundy i strefa są ignorowane), `DD <miesiąc w dopełniaczu> RRRR` (bez rozróżniania wielkości liter; także
     `wrzesnia`, `pazdziernika`), `DD-MM-RRRR`. Do formatów z dniem na początku może dojść godzina `HH:MM` lub `HH.MM`,
     poprzedzona opcjonalnie `r.`, przecinkiem, `godz.` lub `o godz.`. Rok musi mieścić się w 1990–2100.
   * Daty bez strefy dostają strefę **Europe/Warsaw**.

### 7.4. Treść (kolejność metod)

1. **`content_selector`**: tekst **wszystkich** pasujących elementów w kolejności dokumentu, połączony `\n\n`
   (metoda `selector`),
2. jeśli < 80 znaków: **trafilatura** (`include_comments=False`, `include_tables=True`, `favor_precision=True`,
   `deduplicate=True`), przyjęta, gdy ma ≥ 80 znaków (metoda `trafilatura`),
3. jeśli nadal < 80 znaków: tekst pierwszego z `<article>`, `<main>`, `[role=main]`, o ile jest dłuższy od dotychczasowego
   (metoda `bs4`).

Nazwa metody trafia tylko do logu DEBUG, a nie do danych.

**Tekst z podziałem na bloki** (selektor i metoda `bs4`):
* usuwane są `script`, `style`, `noscript`, `nav`, `header`, `footer`, `aside`, `form`, `iframe`, `svg`, `button`, `template`;
* `<br>` daje nową linię;
* przed i po elementach blokowych (`p`, `div`, `li`, `ul`, `ol`, `h1`–`h6`, `tr`, `table`, `section`, `article`,
  `blockquote`, `dt`, `dd`, `dl`, `pre`, `figcaption`, `header`, `address`) wstawiana jest nowa linia, więc linki
  i pogrubienia zostają w zdaniu;
* po każdej komórce `td`/`th` dochodzi ` | `;
* czyszczenie: spacje, tabulatory i NBSP zwinięte do jednej spacji, linie przycięte, 3+ puste linie zamienione na 2,
  całość przycięta. To samo czyszczenie dostaje wynik trafilatury.

### 7.5. Załączniki (fallback)

Na **wyrenderowanym HTML-u przed czyszczeniem** (bez usuwania cookies i `drop_selectors`) brany jest zakres `<article>`,
potem `<main>`, potem cały dokument. Załącznikiem jest każdy `a[href]`, którego ścieżka kończy się na `.pdf`, `.doc`,
`.docx`, `.odt`, `.rtf`, `.xls`, `.xlsx` lub `.ods`, albo którego URL zawiera `/download/` lub `/attachment/`.
Duplikaty są pomijane. Nazwa to tekst linku, a bez niego ostatni segment URL. Mechanizm działa tylko przy tekście
< 400 znaków (§6.8).

### 7.6. RSS / Atom

* Parser XML z `recover=True`, bez encji zewnętrznych i bez sieci.
* Pozycja to każdy element o nazwie lokalnej `item` albo `entry`.
* Link: pierwszy potomny `link` z `href` i `rel` równym `alternate` (lub bez `rel`), albo z niepustym tekstem.
  Bez niego `guid`/`id`, jeśli zaczyna się od `http` i nie ma `isPermaLink="false"`. Pozycja bez linku jest pomijana.
  Link jest rozwiązywany względem adresu końcowego kanału, bez fragmentu.
* Data: pierwszy istniejący z `pubDate`, `published`, `date`, `issued`, `updated`, `modified` (§7.3).
* Opis: `content:encoded`, potem `content`, potem `description`/`summary`. HTML w opisie i tytule jest zamieniany
  na tekst.

### 7.7. Heurystyka linków z datą (`html_list`)

Dla każdego linku (`a[href]`; przy `link_selector` są to elementy z selektora, a dla elementu innego niż `<a>` jego
pierwszy potomny `a[href]`):

1. pomijane są adresy `mailto:`, `tel:`, `javascript:`, `#…`, `data:` i adresy spoza http(s). Fragment jest usuwany;
2. pomijany jest adres samej listy i URL już przyjęty;
3. host musi być **dokładnie** równy hostowi adresu końcowego listy;
4. odrzucane są ścieżki z rozszerzeniem z `SKIP_EXT` (`pdf`, `doc(x)`, `xls(x)`, `ppt(x)`, `odt`, `ods`, `odp`,
   `rtf`, `zip`, `rar`, `7z`, `gz`, `jp(e)g`, `png`, `gif`, `bmp`, `svg`, `webp`, `tif(f)`, `mp3`, `mp4`, `avi`,
   `mov`, `wmv`, `xml`, `csv`) oraz URL-e pasujące do domyślnych wykluczeń `/attachment/`, `/download/`, `/pobierz/`,
   `?print=`/`&print=`, `?format=`/`&format=`;
5. ścieżka musi zaczynać się od `path_prefix` (jeśli podano), a URL nie może pasować do `exclude_regex`;
6. przy `skip_chrome` (domyślnie włączone, gdy nie ma `link_selector`) pomijane są linki wewnątrz `nav`, `header`, `footer`;
7. **data**: najpierw z tekstu linku. Potem z przodków, idąc w górę, najwyżej **8** poziomów; wspinaczka kończy się na
   `body`/`html`, na pierwszym przodku zawierającym **więcej niż jeden różny** (znormalizowany) URL
   (to już nie kafelek tego artykułu) i na przodku o tekście > **1500** znaków;
8. **tytuł**: tekst linku, a bez niego atrybut `title`, a bez niego pierwszy `h1`–`h4` w przodkach odwiedzonych
   podczas wspinaczki. Tytuł zawierający datę i krótszy niż 30 znaków (czyli „sama data”) jest zerowany;
9. przy `require_date` (domyślnie włączone, gdy nie ma `link_selector`) link bez daty jest odrzucany.

---

## 8. Maskowanie danych osobowych (PII)

### 8.1. Obecna polityka – MASKOWANIE WYŁĄCZONE

* Decyzją właściciela projektu (2026-11-21), na czas prototypu, maskowanie jest **wyłączone**:
  domyślne `mask_pii` w kodzie to `false`, a źródła policji (`policja-kgp`, `policja-mazowiecka`) mają jawnie
  `mask_pii: false`. Żadne źródło w `sources.yaml` nie ma dziś `mask_pii: true`. W `data/` zapisywane są więc
  pełne imiona i nazwiska (oraz ewentualne numery PESEL) z komunikatów.
* `data/` jest w `.gitignore` i NIE WOLNO go commitować.
* **Przed jakąkolwiek publikacją danych, udostępnieniem zbioru albo wystawieniem RAG na zewnątrz maskowanie MUSI
  zostać przywrócone** (`mask_pii: true` przy źródłach z danymi osobowymi) albo dane MUSZĄ być maskowane w kroku eksportu.
* Włączenie `mask_pii` **nie działa wstecz**. Wersje zapisane bez maskowania zostają w `documents.jsonl`
  (plik tylko dopisywany), a dokument niezmieniony u źródła (304) nie zostanie przepisany. Przed publikacją trzeba więc
  wyczyścić lub zamaskować istniejące dane. Po włączeniu maskowania każdy ponownie pobrany dokument da nową wersję,
  bo zmieni się hasz.
* Adresy URL nie są maskowane. Slugi policji zawierają nazwiska z tytułów (RAPORT, decyzja 7).

### 8.2. Algorytm (gdy `mask_pii: true`)

1. Start: wymagany model spaCy z `settings.spacy_models` (domyślnie `pl_core_news_md`, potem `pl_core_news_sm`),
   ładowany bez komponentów `parser`, `lemmatizer`, `tagger`, `morphologizer`, `attribute_ruler`, `senter`,
   `trainable_lemmatizer`. Model MUSI mieć `ner`. `max_length` co najmniej 2 000 000 znaków. Brak modelu
   oznacza odmowę startu (kod 2). Nie ma trybu „zapisz bez maskowania”.
2. Maskowane jest **razem** `tytuł (białe znaki zwinięte do spacji) + "\n" + treść`, a potem rozdzielane na pierwszym `\n`.
   Nazwisko znalezione w treści zostaje więc zamaskowane też w tytule i odwrotnie.
3. Tekst jest dzielony na akapity po `\n`. Puste akapity nie trafiają do NER.
4. **Normalizacja wejścia NER bez zmiany długości** (offsety się zgadzają): znaki `/ | _ \ • ·` zamieniane na spacje
   („Anna Kowalska/KPP”). Akapit z ≥ 8 literami, w którym > 80% liter to wersaliki, jest zamieniany na Title Case,
   o ile długość się nie zmienia.
5. Encje z etykietą **`persName`** są zaznaczane w oryginalnym akapicie.
6. **Propagacja:** z każdej encji brane są tokeny złożone z ≥ 3 liter, zaczynające się wielką literą i nieobecne
   na liście wyjątków (`policja`, `policji`, `komendant`, `komendanta`, `polska`, `polski`, `asp`, `podkom`, `nadkom`,
   `kom`, `sierż`, `st`, `mł`, `insp`, `nadinsp`, `mjr`, `płk`, `gen`, `pan`, `pani`). Każde ich wystąpienie w całym
   dokumencie (całe słowo, dopasowanie bez rozróżniania wielkości liter), **którego pierwsza litera jest wielka**,
   też jest maskowane.
7. Każdy ciągły zaznaczony fragment zamienia się na `[OSOBA]`, a sąsiednie `[OSOBA]` rozdzielone spacjami lub
   tabulatorami są scalane w jedno.
8. **PESEL:** każde 11 cyfr niepoprzedzonych cyfrą ani `+` i nienastępujących przed cyfrą
   (`(?<![\d+])\d{11}(?!\d)`) zamienia się na `[PESEL]`. Numery `+48…` i krótsze (112, infolinie) zostają.
9. Zasada: lepiej zamaskować za dużo niż za mało. Maskowani są też funkcjonariusze i urzędnicy.

---

## 9. Wersjonowanie i identyfikatory dokumentów

* **Klucz dokumentu** to URL (dla `json_api`: `endpoint#id`).
* `content_hash` = SHA-256 (hex) z **treści po maskowaniu** (`content`). Tytuł, data i metadane **nie** wchodzą do
  hasza, więc zmiana samego tytułu nie tworzy nowej wersji.
* Zapis: gdy hasz jest równy haszowi bieżącej wersji z `current.json`, wynik to `unchanged` i nic nie jest zapisywane.
  W przeciwnym razie `version` = poprzednia + 1 (albo 1), linia jest dopisywana do `documents.jsonl` (fsync),
  potem atomowo zapisywany jest `current.json`. Wynik to `new` (v1) albo `updated`.
* `id` = pierwsze 16 znaków hex SHA-256 z URL + `-v` + `version`, np. `654784d6af5ff3aa-v1`. Prefiks przed `-v`
  jest stały dla URL i wspólny dla wszystkich jego wersji.
* Linie w `documents.jsonl` NIE są nigdy modyfikowane ani usuwane. Powrót treści do wcześniejszej postaci daje nową wersję.
* Szczegóły pól: [DATA_FORMAT.md](DATA_FORMAT.md).

---

## 10. Harmonogram, sygnały, stan po restarcie

### 10.1. Tryby

* **`once`**: jeden przebieg wybranych źródeł po kolei (kolejność z pliku albo z `--source`), bez względu na
  `interval_minutes`.
* **`run`**: pętla ciągła. Termin źródła = `last_run` (czas **rozpoczęcia** poprzedniego uruchomienia) +
  `interval_minutes`. Źródło bez `last_run` jest od razu zaległe. W każdym kroku uruchamiane jest **jedno** najbardziej
  zaległe źródło (sortowanie po terminie, potem po nazwie), a po nim zaległość jest liczona od nowa. Gdy nic nie jest
  zaległe, proces śpi do najbliższego terminu w krokach co 1 s, sprawdzając sygnał zatrzymania. Wycofanie hosta nie
  zmienia harmonogramu: źródło uruchomi się w terminie i szybko skończy statusem `throttled`/`blocked`.
* Działa jeden wątek. Długie źródło opóźnia pozostałe.

### 10.2. Sygnały

* Pierwszy SIGINT lub SIGTERM ustawia flagę zatrzymania: bieżące źródło jest **dokańczane**, kolejne nie startują,
  proces kończy się kodem 0 („Koniec pracy.” w trybie `run`).
* Drugi sygnał przerywa natychmiast (`KeyboardInterrupt`): log „Przerwano.”, zapis `state.json`, kod 0. Dla przerwanego
  źródła nie powstaje linia w `crawl_log.jsonl`. Dopisane wcześniej dokumenty i `current.json` są spójne.
* Chromium jest uruchamiany bez własnej obsługi sygnałów, więc Ctrl+C w terminalu nie zabija przeglądarki w trakcie
  źródła.

### 10.3. Stan po restarcie

Restart **przetrwają** (`state.json`): `known`, `pending`, `list_cache`, `history`, `last_*`, `active_ids`,
walidatory HTTP i wycofania hostów. **Nie przetrwają** (pamięć procesu): cache robots.txt (pobierany ponownie)
i czasy ostatnich zapytań do hostów. `state.json` jest zapisywany po każdym źródle, przy każdym ustawieniu wycofania
i na koniec procesu.

### 10.4. Blokada procesu

Na jednym katalogu danych może działać tylko jeden proces `run`/`once` (`flock` na `<data_dir>/.lock`, kod 3 przy
konflikcie). Komenda `status` **nie** bierze blokady.

---

## 11. CLI

```
python crawler.py [--config PLIK] [--data-dir KATALOG] [-v|--verbose] {run,once,status} ...
  run    [--max-items N]
  once   [--source NAZWA]... [--max-items N]
  status
```

| opcja | znaczenie |
|---|---|
| `--config` | ścieżka do konfiguracji. Domyślnie `sources.yaml` obok `crawler.py` |
| `--data-dir` | nadpisuje `settings.data_dir`. Ścieżka względna liczy się **od katalogu pliku konfiguracji**, nie od katalogu bieżącego |
| `-v`, `--verbose` | poziom DEBUG (domyślnie INFO). Loggery `httpx`, `httpcore`, `trafilatura`, `urllib3` zawsze od WARNING |
| `--source` | (`once`) nazwa źródła, opcja powtarzalna. Uruchamia także źródła z `enabled: false` (z ostrzeżeniem w logu) |
| `--max-items` | (`run`, `once`) tymczasowo nadpisuje `max_items_per_run` wszystkich źródeł (także 0: żadnych nowych artykułów). Nie dotyczy `json_api` ani `static_page` |

**`status`** wypisuje na stdout tabelę wszystkich źródeł z konfiguracji (także wyłączonych) z kolumnami:
`źródło`, `typ`, `ostatnio` (`last_run`), `status` (`last_status`), `ostatnio nowe` (`last_new_at`),
`dok/wer` (unikalne URL-e / linie w `documents.jsonl` dla źródła) i `alarmy`. Czas jest lokalny, w formacie
`RRRR-MM-DD GG:MM`. Alarmy:
* `BLOCKED` / `THROTTLED`, gdy taki jest `last_status`;
* `3× błąd`, gdy trzy ostatnie wpisy źródła w `crawl_log.jsonl` mają status `error`;
* `cisza >Nh`, gdy źródło ma `silence_alert_hours = N`, było uruchomione, a od `last_new_at` (a bez niego od
  `first_run_at`) minęło ponad N godzin;
* `wyłączone`, gdy `enabled: false`.

Pod tabelą są aktywne wycofania hostów (host, do kiedy, powód) i podsumowanie
`Dokumenty (unikalne URL): N   Wersje (linie w documents.jsonl): M`. `status` nie wymaga maila, nie konfiguruje logu
i nie bierze blokady. Otwiera dane **tylko do odczytu**: przy nieaktualnym `current.json` odbudowuje indeks w pamięci,
ale niczego nie zapisuje (DATA_FORMAT §6).

**Kody wyjścia:**

| kod | znaczenie |
|---|---|
| 0 | zakończenie normalne, także po sygnale/przerwaniu i przy źródłach ze statusem `error`/`blocked` |
| 2 | błąd konfiguracji (brak pliku, brak `name`/`institution`/`url`, nieznany typ, duplikat nazwy); nieznana nazwa w `--source`; brak lub niepoprawny mail kontaktowy; maskowanie wymagane, ale niedostępne (brak spaCy lub modelu) |
| 3 | inny proces crawlera trzyma blokadę katalogu danych |

---

## 12. Logowanie

* `data/crawler.log` (dopisywany, bez rotacji) i stderr, format `RRRR-MM-DD GG:MM:SS POZIOM  komunikat`, czas lokalny.
* Na start źródła trafia linia `▶ <nazwa> (<typ>)`, na koniec
  `■ <nazwa>: <status> (znaleziono, nowe, zmienione, bez zmian, pominięte, błędy) <komunikat>`.
* Ostrzeżenia dotyczą wycofań, adresów zablokowanych w robots.txt i błędów pozycji. Pełny ślad stosu pojawia się
  przy błędzie źródła.

---

## 13. Testy (`tests/run_local_test.py`)

Test end-to-end bez internetu: trzy lokalne serwery (`127.0.0.1` jako zwykły, `127.0.0.2` zawsze odpowiadający 429
z `Retry-After: 120`, `127.0.0.3` z robots.txt odpowiadającym 500). Crawler jest uruchamiany przez CLI
z `min_delay_seconds: 0.05` (wyjątek localhost) i tymczasowym katalogiem danych. Test wymaga Playwright (Chromium)
i modelu `pl_core_news_md`, bo jego konfiguracja ma `mask_pii: true` dla źródła testowego. Kod wyjścia testu to 0
albo 1.

Co sprawdza:

0. **Odmowy startu:** brak maila daje kod 2 i „CRAWLER_CONTACT” na stderr. Nieistniejący model spaCy przy `mask_pii`
   daje kod 2, komunikat o maskowaniu i brak `documents.jsonl`.
1. **Pierwszy przebieg** (kod 0):
   * RSS: pełna treść artykułu, nazwiska zamaskowane (`[OSOBA]`), PESEL zamieniony na `[PESEL]`, numery 112
     i `800 120 226` zachowane, bez stopki, `fetch_method = playwright`, data z kanału;
   * robots.txt: `/private/` niepobrany, adres w `blocked_urls`, serwer nie dostał żadnego żądania o ten zasób;
   * `html_list`: dokładnie 3 kafelki (`found = 3`). Menu, stopka, PDF i obca domena odfiltrowane. Data z tekstu linku
     z godziną i data słowna;
   * `sitemap`: sitemapindex daje 2 adresy, obca domena jest ignorowana, kolejność od najnowszego;
   * `json_api`: 2 rekordy z `url = endpoint#id`, `title_template`, spłaszczone pola (`teryt: 2201, 2202`),
     `attribution` w treści i w polu, data z `date_fields`;
   * `static_page` zapisana; treść wstrzykiwana przez JavaScript wyrenderowana;
   * skrypt strony w Playwright nie pobiera adresu zablokowanego w robots.txt (zero żądań o `/private/js-data`);
   * 429 daje `throttled` po **dokładnie jednym** żądaniu; każde żądanie ma UA `FaktBot/…`;
   * robots.txt 5xx daje `throttled`, jedno żądanie o robots i zero innych żądań do hosta;
   * siedem źródeł testowych ma status `ok`.
2. **Drugi przebieg:** zero nowych linii; źródła mają `new = updated = 0` i status `ok`/`not_modified`; żaden artykuł
   nie jest pobrany ponownie z kodem 200; przy `recheck_hours: 0` artykuł RSS dostaje zapytanie warunkowe i 304;
   wycofanie trwa (zero nowych żądań do hosta z 429).
3. **Zmiana treści** (`once --source t-policja --source t-api`): stare linie nietknięte, artykuł ma wersje 1 i 2
   (`id` kończy się `-v2`, v2 zawiera poprawkę i jest zamaskowana), rekord JSON po zmianie pola ma v2, niezmieniony
   rekord nie dostaje nowej wersji, `current.json` wskazuje v2.
4. **`status`:** kod 0, alarm `THROTTLED` i podsumowanie „Wersje”.
5. **`run` + SIGTERM:** kod 0, w logu „kończę po bieżącym źródle” i „Koniec pracy”, `last_run` zapisany dla
   uruchomionych źródeł.
6. **Testy jednostkowe** (bez sieci zewnętrznej): pusty `User-agent:` nie dotyczy FaktBota, a `FaktBot/1.0` pasuje;
   robots.txt 5xx daje `RobotsUnavailable` także z pamięci podręcznej; pętla przekierowań robots.txt daje
   `RobotsUnavailable`; 3xx bez `Location` to `HttpError`; powtórzony id w `json_api` daje osobny dokument (`~2`)
   bez przeplatania wersji; `current.json` ma uprawnienia 0644; `Store` w trybie tylko do odczytu niczego nie zapisuje.

Test **nie** sprawdza: 403/503, wyzwań antybotowych, paginacji `html_list`, `fetch_full: false`, `id_template`,
`records_path`, kolejki `pending`, `MAX_FAILS`, zwykłych przekierowań ani `Crawl-delay`.

---

## 14. Narzędzie `tools/inspect_xhr.py`

Narzędzie diagnostyczne, które nie jest częścią crawlera. Wczytuje `sources.yaml` (wymaga maila), dla każdego URL
sprawdza robots.txt i czeka na swoją kolej, a potem otwiera stronę w Playwright **bez** `route.fulfill` (dokument pobiera
przeglądarka). Blokuje obrazy, fonty i media, czeka na `networkidle` z limitem 40 s plus 2 s i wypisuje żądania
`xhr`/`fetch`/`document`/`eventsource`/`websocket` wraz z próbką odpowiedzi JSON (do 300 bajtów) i początkiem
tekstu strony. Służy do znajdowania publicznych API aplikacji JS (np. RSO, Odyseusz). Podzasoby nie przechodzą
przez robots ani przez odstęp.

---

## 15. Znane ograniczenia i otwarte decyzje

Według RAPORT.md i `sources.yaml`, sprawdzone z kodem i konfiguracją (stan na 2026-11-21):

| temat | stan |
|---|---|
| Mail kontaktowy | `settings.contact: testtest@gmail.com`, tymczasowy, do zmiany |
| Maskowanie PII | wyłączone (§8.1); decyzje 7 (nazwiska w URL) i 8 (maskowanie osób publicznych) czekają na ponowne włączenie |
| `cert-polska` (RSS) | włączone; robots.txt blokuje kanał, co przebieg status `blocked` |
| `cert-polska-komunikaty` | wyłączone, propozycja (lista `/komunikaty/` dozwolona w robots.txt) |
| `rso-ogolne` (`json_api`) | wyłączone, propozycja. `json_api` nie stronicuje, więc pobierze tylko stronę 0 (20 rekordów) |
| Odyseusz | nie skonfigurowany, publiczne API do decyzji |
| `wody-polskie-nawigacyjne` | wyłączone, struktura bez list z datami, wymaga decyzji o regionach i kategoriach |
| `utk` | wyłączone, robots.txt `Disallow: /` dla wszystkich poza Googlebotem, do wniosku o dane |
| `ulc` | wyłączone, wyzwanie Incapsula, do wniosku |
| `sar` (`sitemap`) | **włączone**; sitemapa się pobiera, artykuły dostają wyzwanie Incapsula, więc status `blocked` i wycofanie 60 min. Adresy czekają w `pending` (dziś 115) |
| Równoległość | brak; tryb `run` obsługuje źródła po kolei |
| Załączniki PDF | nie są pobierane, zapisywana jest tylko lista linków |
| Usunięte dokumenty | brak wykrywania (poza `active_ids` w `json_api`) |
| Sitemapa bez cache listy | przy 304 mapy głównej adresy bez `lastmod` nie są ponownie sprawdzane |
| Podzasoby Playwright | robots.txt sprawdzany z pamięci podręcznej; odstęp i wycofanie ich nie dotyczą (§3.9) |

## 16. Zachowania do decyzji

Poprawione po przeglądzie (2026-11-21): zapamiętana niedostępność robots.txt daje `RobotsUnavailable`; pętla
przekierowań robots.txt to „niedostępny”; dokładne dopasowanie grup robots.txt; robots.txt dla podzasobów Playwright;
powtórzone identyfikatory w `json_api`; 3xx bez `Location` to błąd; `status` tylko do odczytu; uprawnienia 0644;
`--max-items 0`; ostrzeżenie przy `--source` dla źródła wyłączonego. Każdą poprawkę sprawdza test (§13).

Pozostają (świadome, do decyzji):

1. `Retry-After` nie ma górnego limitu: serwer może wstrzymać źródło na dowolnie długo (widać to w `status`).
2. Pozycja z `failed: true` (3 kolejne błędy) nie jest już nigdy sprawdzana, także jeśli wcześniej miała zapisaną wersję.
3. `--source` uruchamia także źródła wyłączone (zamierzone, z ostrzeżeniem w logu).
4. `sitemap` nie zapamiętuje listy adresów: przy 304 mapy głównej adresy bez `lastmod` nie są ponownie sprawdzane.
5. `html_list` zapamiętuje tylko pierwszą stronę listy: przy 304 kolejne strony paginacji nie są ponownie sprawdzane.
6. Wycofanie po 5xx robots.txt (30 min) jest tylko w pamięci procesu: po restarcie robots.txt jest pobierany od razu.
