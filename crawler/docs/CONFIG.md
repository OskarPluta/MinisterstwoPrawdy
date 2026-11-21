# FaktBot – konfiguracja (`sources.yaml`)

Pełna lista kluczy konfiguracji z wartościami domyślnymi z kodu. Zachowanie adapterów opisuje [SPEC.md](SPEC.md) §6.

## 1. Budowa pliku i zasady łączenia

```yaml
settings:   { ... }     # ustawienia procesu (§2)
defaults:   { ... }     # wartości domyślne dla każdego źródła (§3)
x-govpl: &govpl { ... } # kotwica YAML – profil wstawiany do źródeł (§5); sam klucz jest ignorowany
sources:    [ ... ]     # lista źródeł (§4)
```

* Plik jest czytany przez `yaml.safe_load`. Obsługiwane są kotwice (`&nazwa`), aliasy (`*nazwa`) i klucz scalania
  `<<:`. Klucze najwyższego poziomu inne niż `settings`, `defaults`, `sources` są ignorowane.
* Kolejność pierwszeństwa dla źródła, od najsłabszej: **domyślne z kodu → `defaults` → profil wstawiony przez `<<:` →
  klucze wpisane w źródle**. Łączenie jest płytkie: słownik (np. `pagination`) podany wyżej zastępuje cały słownik
  niżej.
* `settings` są łączone płytko z domyślnymi ustawieniami z kodu.
* **Walidacja przy starcie** (błąd daje kod 2): plik musi istnieć; każde źródło musi mieć `name` (wpisane w samym
  źródle), nazwy muszą być unikalne, `type` musi należeć do `rss | html_list | sitemap | json_api | static_page`,
  musi być `url` lub `urls` (dla `json_api` wystarczy `endpoint`) i niepuste `institution`.
* **Nic poza tym nie jest walidowane.** Nieznane klucze, w tym literówki, są po cichu ignorowane. Błędny typ wartości
  albo błędne wyrażenie regularne wychodzi dopiero w trakcie pracy jako status `error` źródła. Po każdej zmianie
  uruchom `once --source <nazwa>`.

---

## 2. `settings`

| klucz | typ | domyślnie (kod) | w `sources.yaml` | znaczenie |
|---|---|---|---|---|
| `contact` | string (e-mail) | `null` | `testtest@gmail.com` (tymczasowy) | mail w User-Agencie `FaktBot/1.0 (+mail)`. **Zmienna `CRAWLER_CONTACT` ma pierwszeństwo.** Wymagany: bez niego `run`/`once` się nie uruchomią (kod 2). Musi pasować do `^[^@\s]+@[^@\s]+\.[^@\s]+$` |
| `data_dir` | string (ścieżka) | `"data"` | `data` | katalog danych. Ścieżka względna liczy się **od katalogu pliku konfiguracji**. Opcja CLI `--data-dir` ją nadpisuje |
| `min_delay_seconds` | liczba | `3.0` | `3` | minimalny odstęp między zapytaniami do jednego hosta. Dla hostów innych niż lokalne obowiązuje i tak co najmniej 3,0 s. Mniejsza wartość działa tylko dla `localhost`, `::1`, `127.0.0.1`–`127.0.0.9`. `Crawl-delay` z robots.txt może odstęp wydłużyć |
| `render_timeout_seconds` | liczba | `20` | `20` | ile czekać na `networkidle` po załadowaniu strony w Playwright. Po przekroczeniu brany jest bieżący DOM. Limit `domcontentloaded` jest stały i wynosi 30 s |
| `http_timeout_seconds` | liczba | `30` | – | timeout klienta HTTP (httpx) dla każdego zapytania |
| `spacy_models` | lista stringów | `["pl_core_news_md", "pl_core_news_sm"]` | – | modele spaCy do maskowania PII, próbowane po kolei. Ładowane tylko wtedy, gdy któreś z wybranych źródeł ma `mask_pii: true` |
| `default_backoff_minutes` | liczba | `15` | – | wycofanie po 429/503 (także 429 na robots.txt), gdy nie ma `Retry-After` |
| `forbidden_backoff_minutes` | liczba | `60` | – | wycofanie po 403 bez `Retry-After` oraz po wykryciu wyzwania antybotowego |
| `robots_ttl_hours` | liczba | `24` | – | ważność robots.txt (2xx i 4xx) w pamięci procesu |
| `robots_unavailable_retry_minutes` | liczba | `30` | – | po ilu minutach ponowić pobranie robots.txt po 5xx lub błędzie sieci (do tego czasu host jest wstrzymany) |

Stałe, których **nie da się** konfigurować: limit 5 przekierowań, 80 znaków minimalnej treści, kolejka zaległych
do 300, 3 błędy do `failed`, historia 10 statusów, kafelek ≤ 1500 znaków i 8 poziomów w heurystyce linków, próg 400
znaków dla załączników (maks. 10), minimalny opis RSS 40 znaków, lista hostów lokalnych, lista trackerów i blokowanych
typów zasobów, znaczniki wyzwań antybotowych.

---

## 3. `defaults` – wartości domyślne źródeł

| klucz | domyślnie (kod) | w `sources.yaml → defaults` |
|---|---|---|
| `enabled` | `true` | – |
| `interval_minutes` | `60` | `60` |
| `max_items_per_run` | `30` | `30` |
| `recheck_hours` | `24` | `24` |
| `fetch_full` | `true` | – |
| `mask_pii` | `false` | – |
| `silence_alert_hours` | `null` | **`168`** (tydzień) |
| `doc_type` | `"komunikat"` | – |
| `region` | `null` | – |

W `defaults` można też umieścić dowolny inny klucz źródła (np. `content_selector`). Trafi wtedy do wszystkich źródeł.

---

## 4. Klucze źródła

### 4.1. Wspólne

| klucz | typ | wymagany / domyślnie | dotyczy typów | znaczenie |
|---|---|---|---|---|
| `name` | string | **wymagany**, unikalny | wszystkie | identyfikator źródła: pole `source` w danych, klucz w `state.json`, argument `--source` |
| `type` | enum | **wymagany** | – | `rss`, `html_list`, `sitemap`, `json_api`, `static_page` |
| `institution` | string | **wymagany** | wszystkie | pole `institution` w dokumentach |
| `url` | string | `url` lub `urls` wymagane | wszystkie | jeden adres |
| `urls` | lista stringów | j.w. | wszystkie | kilka adresów. Niepuste `urls` ma pierwszeństwo przed `url`. `sitemap` i `json_api` używają tylko pierwszego |
| `enabled` | bool | `true` | wszystkie | `false`: źródło pomijane w `run` i `once` bez `--source`. `once --source <nazwa>` uruchamia je mimo to (z ostrzeżeniem w logu). `status` pokazuje alarm „wyłączone” |
| `interval_minutes` | liczba | `60` | wszystkie | odstęp między uruchomieniami w trybie `run`, liczony od początku poprzedniego uruchomienia |
| `max_items_per_run` | int | `30` | `rss`, `html_list`, `sitemap` | maksymalna liczba artykułów pobieranych w jednym uruchomieniu (nowe mają pierwszeństwo przed ponownymi sprawdzeniami; nadwyżka trafia do kolejki). `--max-items` nadpisuje. Ignorowany przez `json_api` i `static_page` |
| `recheck_hours` | liczba | `24` | `rss`, `html_list`, `sitemap` (adresy bez `lastmod`) | po ilu godzinach ponownie sprawdzić znany adres, który wciąż jest na liście. `0` oznacza każdy przebieg |
| `doc_type` | string \| null | `"komunikat"` | wszystkie | pole `doc_type` w dokumentach |
| `region` | string \| null | `null` | wszystkie | pole `region` w dokumentach |
| `mask_pii` | bool | `false` | wszystkie | maskowanie osób i PESEL przed zapisem (SPEC §8). `true` wymaga modelu spaCy przy starcie. **Obecnie nigdzie nie jest włączone** |
| `silence_alert_hours` | liczba \| null | `null` (w tym pliku `168`) | wszystkie | alarm `cisza >Nh` w `status`, gdy od ostatniej nowej treści minęło więcej niż N godzin. `null` wyłącza alarm |
| `attribution` | string | brak | wszystkie | zapisywany w polu `attribution` każdego dokumentu. W `json_api` dodatkowo dopisywany na końcu treści |

### 4.2. Ekstrakcja treści (`rss` z `fetch_full: true`, `html_list`, `sitemap`, `static_page`)

| klucz | typ | domyślnie | znaczenie |
|---|---|---|---|
| `content_selector` | string (CSS) | brak | treść = tekst **wszystkich** pasujących elementów, połączony pustą linią. Jeśli wynik ma < 80 znaków, używana jest trafilatura, a potem `<article>`/`<main>` |
| `title_selector` | string (CSS) | brak | tytuł z pierwszego pasującego elementu (pierwszeństwo przed `<h1>`, `og:title`, `<title>`) |
| `strip_title_suffix` | string (regex) | brak | usuwany z końca tytułu (dopasowanie `regex + \s*$`) |
| `drop_selectors` | lista CSS | brak | elementy usuwane przed ekstrakcją tytułu i treści (metryczki, galerie, przyciski) |

### 4.3. `rss`

| klucz | typ | domyślnie | znaczenie |
|---|---|---|---|
| `url` / `urls` | adresy kanałów RSS 2.0 / Atom | – | każdy kanał jest pobierany osobno. Zablokowany w robots.txt kanał jest pomijany, ale każdy inny błąd kanału przerywa całe źródło |
| `fetch_full` | bool | `true` | `true`: pobiera i renderuje stronę każdego wpisu (tytuł z kanału ma pierwszeństwo). `false`: zapisuje opis z kanału (wpis bez opisu jest pomijany), sprawdza tylko robots.txt adresu |

Wpisy wskazujące na domenę inną niż domena kanału są odrzucane (porównanie przybliżonej domeny rejestrowalnej,
np. `mazowiecka.policja.gov.pl` → `policja.gov.pl`).

### 4.4. `html_list`

| klucz | typ | domyślnie | znaczenie |
|---|---|---|---|
| `url` / `urls` | adresy list aktualności | – | każda lista (i jej strony) jest renderowana |
| `path_prefix` | string | brak | przyjmowane są tylko linki, których ścieżka zaczyna się od tego prefiksu (np. `/web/kgpsp/`) |
| `link_selector` | string (CSS) | brak (heurystyka) | zamiast wszystkich `a[href]` brane są elementy pasujące do selektora (dla elementu innego niż `<a>` jego pierwszy `a[href]`). Ustawienie zmienia domyślne `require_date` i `skip_chrome` na `false` |
| `exclude_regex` | string (regex) | brak | linki pasujące (wyszukiwanie w całym URL) są odrzucane |
| `require_date` | bool | `true` bez `link_selector`, `false` z nim | odrzuca linki, przy których heurystyka nie znalazła daty |
| `skip_chrome` | bool | `true` bez `link_selector`, `false` z nim | odrzuca linki wewnątrz `nav`, `header`, `footer` |
| `pagination.template` | string | brak | parametry query kolejnych stron, `{n}` to numer strony, np. `"page={n}&size=10"` |
| `pagination.start` | int | `2` | numer pierwszej strony **po** stronie z konfiguracji |
| `pagination.max_pages` | int | `1` | łączna liczba stron **razem z pierwszą**. Kolejna strona jest pobierana tylko wtedy, gdy wszystkie linki bieżącej są nowe |

Zawsze (bez względu na konfigurację) odrzucane są: linki do innego hosta, adres samej listy, pliki
(`pdf`, `doc(x)`, `xls(x)`, `ppt(x)`, `odt/ods/odp`, `rtf`, `zip/rar/7z/gz`, obrazy, `mp3/mp4/avi/mov/wmv`, `xml`, `csv`)
oraz adresy z `/attachment/`, `/download/`, `/pobierz/`, `print=`, `format=`.

### 4.5. `sitemap`

| klucz | typ | domyślnie | znaczenie |
|---|---|---|---|
| `url` | adres sitemap.xml lub sitemapindex | – | używany jest **tylko pierwszy** adres. Podmapy i adresy spoza jego hosta są ignorowane |
| `max_sitemaps` | int | `20` | maksymalna liczba odwiedzanych map (główna + podmapy) w jednym uruchomieniu |
| `path_prefix` | string | brak | filtr początku ścieżki adresów |
| `exclude_regex` | string (regex) | brak | adresy pasujące są odrzucane |

Pliki (lista jak w `html_list`) są odrzucane zawsze. Znany adres jest sprawdzany ponownie, gdy wzrośnie jego `lastmod`,
a przy braku `lastmod` co `recheck_hours`.

### 4.6. `json_api`

| klucz | typ | domyślnie | znaczenie |
|---|---|---|---|
| `endpoint` | string | – | adres API, ma pierwszeństwo przed `url`/`urls`. Jest też prefiksem URL dokumentu (`endpoint#id`), więc jego zmiana zmienia klucze wszystkich dokumentów |
| `records_path` | string | brak (korzeń) | ścieżka z kropkami do listy rekordów, np. `newses` albo `data.items.0.list`. Gdy wynik jest obiektem, brana jest jego pierwsza wartość-lista, a bez niej obiekt jest jednym rekordem |
| `id_field` | string | brak | pole z identyfikatorem rekordu (ścieżka z kropkami w oryginalnym rekordzie) |
| `id_template` | string | brak | szablon identyfikatora, gdy API nie ma pola id, np. `"{numer}-{opublikowano:.4}-{biuro}"`. Wynik jest zamieniany na slug. Ma pierwszeństwo przed `id_field`. Bez obu identyfikatorem jest hasz rekordu (każda zmiana daje nowy dokument zamiast wersji) |
| `title_template` | string | brak (`title: null`) | szablon tytułu. Klucze to nazwy po spłaszczeniu: `{pole}`, `{a.b}`, `{lista[0].pole}`; `{pole:.4}` to pierwsze 4 znaki. Brakujące pole daje pusty napis. Puste `()` są usuwane |
| `date_fields` | lista stringów | `[]` | pola (po spłaszczeniu) próbowane po kolei jako `published_at` |
| `sort_lists` | bool | `false` | sortuje listy skalarów w treści (np. kody TERYT), żeby zmiana kolejności nie tworzyła nowej wersji |
| `attribution` | string | brak | dopisek licencyjny: na końcu treści i w polu `attribution` |

`max_items_per_run` i `recheck_hours` nie działają. Co przebieg przetwarzane są wszystkie rekordy, a zapis zależy od hasza.
Brak stronicowania.

### 4.7. `static_page`

| klucz | typ | znaczenie |
|---|---|---|
| `url` / `urls` | adresy stron | każda strona jest pobierana (warunkowo) w każdym uruchomieniu. Nowa wersja powstaje przy zmianie treści. Data publikacji tylko z metadanych strony |

Klucze ekstrakcji z §4.2 działają. `max_items_per_run` i `recheck_hours` nie działają.

---

## 5. Profil `x-govpl` (kotwica YAML)

Wspólny profil dla stron w CMS gov.pl, wstawiany do źródła przez `<<: *govpl`:

```yaml
x-govpl: &govpl
  title_selector: "article h2"                 # <h1> na gov.pl to logo serwisu
  content_selector: "article .editor-content"  # trafilatura gubi np. telefony zapisane jako <h4>
  drop_selectors: [".metrics", ".metric-history", ".gallery", ".main-photo"]   # metryczka BIP, galerie
  strip_title_suffix: " - [^-]+ - Portal Gov\\.pl"
```

* Klucz `x-govpl` jest ignorowany przez crawler i służy tylko jako nośnik kotwicy.
* Klucze wpisane w źródle nadpisują klucze profilu. Na przykład `drop_selectors` w źródle zastępuje **całą** listę z profilu.
* Profil używają: `kgpsp-aktualnosci`, `mon-aktualnosci`, `mi-aktualnosci`, `gis-ostrzezenia`,
  `wody-polskie-nawigacyjne`, `rcb`, `mswia`, `premier`, `paa`, `rcb-raport-dobowy`,
  `msz-informacje-dla-podrozujacych`, `prawa-pasazera`.

---

## 6. Jak dodać nowe źródło

1. **Sprawdź legalność ręcznie.** Otwórz `https://<host>/robots.txt` i upewnij się, że ścieżki listy i artykułów nie są
   zablokowane dla `*` ani `FaktBot`. Sprawdź też, czy strona nie stoi za ochroną antybotową. Crawler i tak to sprawdzi,
   ale wtedy źródło będzie tylko produkować statusy `blocked`. Strona zablokowana trafia na listę
   „do wniosku o udostępnienie danych”, a nie do konfiguracji.
2. **Wybierz typ** (od najlepszego): `rss` (kanał) → `json_api` (publiczne API; aplikacje JS można podejrzeć
   narzędziem `tools/inspect_xhr.py`) → `sitemap` → `html_list` (lista aktualności z datami) → `static_page`
   (pojedyncza strona, której zmiany śledzimy).
3. **Dodaj wpis** w `sources.yaml` z unikalnym `name`, `type`, `institution` i `url`. Na początek ustaw
   `enabled: false`: `once --source` i tak go uruchomi, a `run` go pominie.
4. Ustaw `interval_minutes` adekwatnie do częstotliwości publikacji. Odstęp 3 s na host i tak obowiązuje.
   Ustaw `doc_type`, `region`, `silence_alert_hours` (albo `null` dla stron rzadko zmienianych).
5. **Dane osobowe:** jeśli źródło publikuje nazwiska osób prywatnych (np. komunikaty policji), docelowo MUSI mieć
   `mask_pii: true`. Do czasu decyzji właściciela projektu maskowanie jest wyłączone (SPEC §8.1). Dopisz
   wtedy komentarz jak przy źródłach policji.
6. **Przebieg próbny:**
   ```bash
   python crawler.py once --source <nazwa> --max-items 3
   python crawler.py status
   tail -n 1 data/crawl_log.jsonl
   grep '"source": "<nazwa>"' data/documents.jsonl | tail -n 3
   ```
   Sprawdź `found` (dla `html_list` 0 oznacza złą heurystykę lub `path_prefix`), tytuły (logo zamiast tytułu oznacza,
   że potrzebny jest `title_selector`) i treść (metryczki, przyciski czy okruszki wymagają `content_selector` albo
   `drop_selectors`).
7. Dostrój selektory i powtórz krok 6. Pamiętaj, że dokumenty z przebiegu próbnego zostają w `documents.jsonl`
   (plik tylko dopisywany). Do eksperymentów używaj osobnego katalogu: `--data-dir /tmp/faktbot-proba`.
8. Ustaw `enabled: true` (albo usuń klucz).

**Przykład: lista aktualności na gov.pl** (adres ilustracyjny, do zweryfikowania):

```yaml
  - name: mz-aktualnosci
    <<: *govpl
    type: html_list
    institution: Ministerstwo Zdrowia
    url: https://www.gov.pl/web/zdrowie/wiadomosci
    path_prefix: /web/zdrowie/
    pagination: {template: "page={n}&size=10", start: 2, max_pages: 3}
    interval_minutes: 30
```

**Przykład: API bez pola id** (wzorowany na `imgw-hydro`):

```yaml
  - name: przyklad-api
    type: json_api
    institution: Instytut Przykładowy
    doc_type: ostrzezenie
    endpoint: https://api.example.gov.pl/ostrzezenia
    records_path: data.items
    id_template: "{numer}-{opublikowano:.4}-{biuro}"
    title_template: "Ostrzeżenie nr {numer}: {zdarzenie} ({obszary[0].wojewodztwo})"
    date_fields: [opublikowano, data_od]
    sort_lists: true
    silence_alert_hours: null
```

---

## 7. Skonfigurowane źródła (stan `sources.yaml` na 2026-11-21)

Wartości efektywne po scaleniu z `defaults`. Wszystkie mają `max_items_per_run = 30`, `recheck_hours = 24`
i `mask_pii = false`. `cisza` to `silence_alert_hours`.

| nazwa | typ | włączone | interwał [min] | cisza [h] | uwagi |
|---|---|---|---|---|---|
| `policja-kgp` | rss | tak | 10 | 48 | 2 kanały policja.pl (Aktualności, Ważne informacje). `mask_pii: false` tymczasowo |
| `policja-mazowiecka` | rss | tak | 10 | 48 | 2 kanały KWP Radom, `region: mazowieckie`. `mask_pii: false` tymczasowo |
| `cert-polska` | rss | tak | 15 | 168 | `doc_type: ostrzezenie`. robots.txt blokuje kanał, więc co przebieg status `blocked` |
| `cert-polska-komunikaty` | html_list | **nie** | 15 | 168 | propozycja: `/komunikaty/` dozwolone w robots.txt, `path_prefix: /komunikaty/20` |
| `utk` | rss | **nie** | 60 | 168 | 5 kanałów. robots.txt `Disallow: /` dla wszystkich poza Googlebotem, do wniosku |
| `kgsok` | rss | tak | 60 | 168 | WordPress. `content_selector` + `drop_selectors` własne |
| `um-gdynia` | rss | tak | 60 | 168 | Urząd **Morski** w Gdyni, kanał `/aktualnosci/feed/`, `region: pomorskie` |
| `kgpsp-aktualnosci` | html_list | tak | 60 | 168 | govpl. Paginacja `page={n}&size=10`, `start: 2`, `max_pages: 3` |
| `mon-aktualnosci` | html_list | tak | 60 | 168 | govpl |
| `mi-aktualnosci` | html_list | tak | 60 | 168 | govpl |
| `gis-ostrzezenia` | html_list | tak | 20 | 168 | govpl, `doc_type: ostrzezenie` |
| `wody-polskie-nawigacyjne` | html_list | **nie** | 60 | 168 | govpl, `doc_type: komunikat_nawigacyjny`. Strona-węzeł bez dat, wymaga decyzji |
| `rcb` | html_list | tak | 15 | 168 | govpl, strona główna RCB (kafelki alertów) |
| `mswia` | html_list | tak | 60 | 168 | govpl, strona główna |
| `premier` | html_list | tak | 60 | 168 | govpl, strona główna KPRM |
| `paa` | html_list | tak | 60 | 168 | govpl, strona główna PAA |
| `ulc` | html_list | **nie** | 60 | 168 | wyzwanie Incapsula, do wniosku |
| `sar` | sitemap | tak | 60 | 168 | sitemapa się pobiera, artykuły dostają wyzwanie Incapsula, więc `blocked` i wycofanie 60 min |
| `imgw-meteo` | json_api | tak | 5 | – | `id_field: id`, `title_template`, `date_fields: [opublikowano, obowiazuje_od]`, `sort_lists`, `attribution` |
| `imgw-hydro` | json_api | tak | 5 | – | `id_template: "{numer}-{opublikowano:.4}-{biuro}"`, `date_fields: [opublikowano, data_od]`, `sort_lists`, `attribution` |
| `rso-ogolne` | json_api | **nie** | 10 | 168 | propozycja. `records_path: newses`, `id_field: id`, tylko strona 0 (20 rekordów) |
| `ums-warunki-zeglugi` | static_page | tak | 60 | – | spis linków do komunikatów (same komunikaty w plikach) |
| `rcb-raport-dobowy` | static_page | tak | 60 | 48 | govpl, `doc_type: raport` |
| `msz-informacje-dla-podrozujacych` | static_page | tak | 360 | – | govpl. Tylko najnowszy komunikat, pełne dane są w Odyseuszu |
| `prawa-pasazera` | static_page | tak | 10080 | – | govpl, 2 strony MI, `doc_type: informacja`, raz w tygodniu |

Razem 25 źródeł: 20 włączonych i 5 wyłączonych. Celowo nieskonfigurowane (komentarz na końcu `sources.yaml`):
aplikacja RSO na gov.pl (zamiast niej API, wpis `rso-ogolne`), Odyseusz (API do decyzji), drogi.gddkia.gov.pl
i portalpasazera.pl (ochrona antybotowa, do wniosku) oraz serwisy z danymi pomiarowymi.
