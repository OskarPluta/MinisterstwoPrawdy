# Raport: crawler FaktBot, etap 1 (2026-11-21)

## Co zostało zrobione

* Działający crawler: `crawler.py` + pakiet `faktbot/`, adaptery `rss`, `html_list`, `sitemap`, `json_api`, `static_page`.
* Zapis do `data/` (documents.jsonl tylko dopisywany, current.json, state.json, crawl_log.jsonl).
* Test offline `tests/run_local_test.py`: lokalny serwer z wszystkimi typami źródeł. **Wszystkie asercje przechodzą**:
  drugi przebieg nie pobiera niczego ponownie (304 / brak zmian), zmiana treści i pola JSON daje v2 przy nietkniętej v1,
  menu/stopka/PDF/obca domena odfiltrowane, robots.txt (w tym 5xx → nic nie pobrano), 429 → `throttled` po jednym
  żądaniu, Playwright renderuje treść z JS, maskowanie PII, odmowa startu bez maila i bez modelu spaCy,
  Ctrl+C/SIGTERM kończy po bieżącym źródle (sprawdzone też z sygnałem do całej grupy procesów, jak w terminalu).
* Ostrożny przebieg na prawdziwych stronach: `once --max-items 5`, UA `FaktBot/1.0 (+testtest@gmail.com)`
  (mail tymczasowy, do zmiany w `sources.yaml` → `settings.contact` albo `CRAWLER_CONTACT`).

## Wynik na prawdziwych stronach

Przebieg `python crawler.py once --max-items 5` (2026-11-21 18:20–18:25), potem `status`:
**98 dokumentów / 98 wersji**, 17 źródeł `ok`, 2 `blocked`, 6 wyłączonych (czekają na decyzję albo są zablokowane).

| źródło | status | dok. | uwagi |
|---|---|---|---|
| policja-kgp | ok | 5 | 2 kanały (21 wpisów). Treść czysta, PII zamaskowane (sprawdzone ręcznie, łącznie z nazwiskami WERSALIKAMI) |
| policja-mazowiecka | ok | 5 | 20 wpisów. Podpisy autorów („nadkom. [OSOBA]/KMP…”) zamaskowane |
| cert-polska | **blocked** | 0 | robots.txt moje.cert.pl blokuje `/advisories/feed/`. Nic nie pobrano. Alternatywa poniżej |
| cert-polska-komunikaty | wyłączone | – | propozycja: `/komunikaty/` jest jawnie dozwolone w robots.txt (daty `19-11-2026` obsłużone) |
| utk | wyłączone | – | robots.txt: `User-agent: * / Disallow: /` (wpuszcza tylko Googlebota). Nie da się nawet obejrzeć kanałów → **do wniosku o udostępnienie danych** |
| kgsok | ok | 5 | pierwszy przebieg dał fałszywe `blocked` (reCAPTCHA w formularzu WP), poprawione |
| um-gdynia (Urząd **Morski**) | ok | 5 | `/rss` był pusty, kanał `/aktualnosci/feed/`. Drobiazg: treść zaczyna się od „Aktualności” (okruszek) |
| kgpsp-aktualnosci | ok | 5 | 60 linków z 3 stron (`?page=N&size=10`). Tylko artykuły, zero linków z menu |
| mon-aktualnosci | ok | 5 | 14 linków |
| mi-aktualnosci | ok | 5 | 10 linków |
| gis-ostrzezenia | ok | 5 | 10 ostrzeżeń, pełna treść (zagrożenie, partie, zalecenia) |
| wody-polskie-nawigacyjne | wyłączone | – | 0 linków (`warn`): strona to tylko węzeł do 10 serwisów RZGW, potem 3 kategorie, potem aktualne/archiwalne. Wymaga decyzji |
| rcb | ok | 4 | strona główna: łapie tylko kafelki Alertów RCB, menu odpada |
| mswia | ok | 4 | strona główna: tylko aktualności (4 kafelki z datą). Brak linków spoza aktualności |
| premier | ok | 5 | strona główna: 13 linków, wszystkie to aktualności |
| paa | ok | 4 | strona główna: 4 aktualności, menu odpada |
| ulc | wyłączone | – | Imperva/Incapsula: zamiast strony wyzwanie JS (212 B). Nie obchodzimy → **do wniosku o udostępnienie danych** |
| sar | **blocked** | 0 | sitemapa pobrana (115 adresów), ale strony artykułów dostają wyzwanie Incapsula. Wycofanie 1 h, bez obchodzenia → **do wniosku** (albo prośba do SAR o wpuszczenie UA FaktBot) |
| imgw-meteo | ok | 1 | `id_field: id`, tytuł z szablonu, attribution w treści i polu |
| imgw-hydro | ok | 35 | brak pola id → `id_template` numer+rok+biuro. `data_do: 9999-12-31` oznacza „do odwołania” |
| rso-ogolne | wyłączone | – | propozycja (patrz niżej) |
| ums-warunki-zeglugi | ok | 1 | strona to spis linków do komunikatów (same komunikaty w plikach). Wykrywa pojawienie się nowych |
| rcb-raport-dobowy | ok | 1 | pełny raport (ostrzeżenia, stopnie alarmowe) |
| msz-informacje-dla-podrozujacych | ok | 1 | tylko najnowszy komunikat (1 kraj), patrz Odyseusz |
| prawa-pasazera | ok | 2 | 2 strony gov.pl. Strony ULC (Incapsula) i UTK (robots) usunięte z listy |

Zapytania warunkowe: ETag/Last-Modified daje www.gov.pl (artykuły i listy), kanały policji, kgsok i umgdy.
IMGW nie daje (1 zapytanie na 5 min, więc bez znaczenia). Artykuły policji bez walidatorów: ponowne sprawdzenie
po `recheck_hours` (24 h) porównuje hash treści.

Do wniosku o udostępnienie danych (wymagałyby obejścia zabezpieczeń lub złamania robots.txt):
**ULC** (Incapsula), **SAR** (Incapsula na artykułach), **UTK** (robots.txt), **GDDKiA drogi** (Incapsula), **portalpasazera.pl**.

## Co poprawiłem po przeglądzie jakościowym

| problem | poprawka |
|---|---|
| gov.pl: tytuł = „gov.pl Serwis Rzeczypospolitej Polskiej” (to `<h1>` logo) | profil `x-govpl` w sources.yaml: `title_selector: article h2`, `content_selector: article .editor-content` |
| gov.pl: trafilatura gubiła numery telefonów (zapisane jako `<h4>`) i dorzucała metryczkę BIP („historia zmian”) | j.w. + `drop_selectors: [.metrics, .metric-history]` |
| strony statyczne: data publikacji „zgadnięta” z treści (np. 2011-01-01 z daty rozporządzenia) | dla `static_page` tylko data z metadanych strony, nigdy heurystyka |
| PII: nazwiska WERSALIKAMI nie były maskowane („MACIEJ WALASZCZYK”), nazwisko sklejone z „/” uciekało („Sakowska/ KPP”), fałszywy alarm na „DYŻURNY” | normalizacja wejścia NER bez zmiany długości (separatory na spacje, linie wersalikami na Title Case) + propagacja nazwisk znalezionych w dokumencie na pozostałe wystąpienia (także w tytule) |
| fałszywe „wyzwanie antybotowe” na kgsok.pl i sar.gov.pl (reCAPTCHA w zwykłym formularzu WordPressa) | rozdzielone znaczniki: typowe dla stron-wyzwań (Cloudflare/Incapsula) vs ogólne słowa („captcha”), które liczą się tylko na niemal pustej stronie lub przy 403/429/503 |
| linki z nagłówka/stopki z jedną datą w pobliżu (np. „© 2026-11-21”) przechodziły heurystykę | pomijanie linków w `nav/header/footer` + kafelek musi być krótki (≤1500 znaków) |
| kanał 304 ⇒ `recheck_hours` nie działał (pusta lista) | zapamiętana ostatnia lista wpisów kanału/strony, używana przy 304 |
| policja.pl/pol/rss to strona HTML ze spisem kanałów | usunięty; zostały dwa kanały, które ta strona wskazuje |
| umgdy.gov.pl: `/rss` pusty; to **Urząd Morski** w Gdyni, nie Urząd Miasta | kanał `/aktualnosci/feed/`, poprawiona nazwa instytucji |
| moje.cert.pl: daty w formacie `19-11-2026` | dodany format DD-MM-YYYY |
| IMGW hydro nie ma pola id | `id_template` (numer + rok + biuro), `sort_lists` (kolejność TERYT/zlewni nie tworzy wersji) |
| blokada jednego hosta w źródle wielohostowym przerywała całe źródło | izolacja per host: pozostałe hosty są przetwarzane, status `warn` |

## Wymaga Twojej decyzji

> **Aktualizacja:** maskowanie danych osobowych jest tymczasowo wyłączone na polecenie właściciela projektu
> (`mask_pii: false`, opis w README). Punkty 7–8 poniżej wracają, gdy maskowanie zostanie włączone ponownie.

1. **Mail kontaktowy**: teraz `testtest@gmail.com` (tymczasowy). Zmień w `sources.yaml` → `settings.contact`.
2. **CERT**: włączyć `cert-polska-komunikaty`? (lista `/komunikaty/` jest dozwolona w robots.txt, kanał nie)
3. **RSO**: włączyć `rso-ogolne` i dodać pozostałe kategorie? (endpoint niżej)
4. **Odyseusz**: pełne ostrzeżenia dla podróżnych są tylko tam (publiczne JSON API). Dodać jako `json_api`?
5. **Wody Polskie**: które regiony RZGW i które kategorie komunikatów nawigacyjnych?
6. **UTK**: kanały 1803/1807/1894/51/64. Nie da się sprawdzić ich zawartości bez złamania robots.txt, więc nie proponuję wyboru.
   Najprościej poprosić UTK o dopisanie `User-agent: FaktBot` z `Allow`.
7. **RODO a adresy URL**: slugi adresów policji zawierają nazwiska z tytułów (np. `…Podkom-Krzysztof-Gredzinski-…html`).
   Treść i tytuł są maskowane, URL nie (to identyfikator i link do cytatu). Zostawić, czy np. przechowywać URL tylko w
   zahashowanej postaci plus link generowany na żądanie?
8. **Maskowanie jest „na wyrost”**: maskuje też funkcjonariuszy i urzędników publicznych wymienionych z nazwiska
   (np. komendantów). Tak zostawiłem (bezpieczniej). Jeśli nazwiska osób publicznych mają zostać, potrzebna lista wyjątków.
9. **SAR**: wpisać do wniosku, czy zostawić włączone (przy każdym przebiegu najpierw sprawdza sitemapę, a po wyzwaniu
   odczekuje godzinę)? Teraz jest włączone i co przebieg dostaje `blocked`.
10. Tryb `run` jest jednowątkowy (źródła po kolei). Przy obecnych limitach (3 s na host) długi przebieg gov.pl
    (30 artykułów ≈ 2 min) może opóźnić IMGW o tyle samo. Jeśli to za dużo, kolejny krok to równoległość między hostami.

## RSO i Odyseusz

**RSO / gov.pl/web/gov/ostrzezenia.** Nasłuch żądań Playwright (narzędzie `tools/inspect_xhr.py`) pokazał,
że strona pobiera dane z publicznego JSON (bez logowania, bez tokenów; robots.txt gov.pl: `Allow: /`):

```
https://www.gov.pl/api/data/proxy/rso/komunikatyxml/wszystkie/<kategoria>/<strona>?_format=json
kategorie: meteorologiczne (5 rekordów), hydrologiczne (44), informacje-drogowe (7), ogolne (41), stany-wod (307)
odpowiedź: {"pagination":{"totalitems":N,"itemsperpage":20},"newses":[{id, title, shortcut, content,
            rso_alarm, rso_icon, valid_from, valid_to, ...}]}   strona liczona od 0
```

Gotowy, **wyłączony** wpis `rso-ogolne` (json_api, `records_path: newses`, `id_field: id`) jest w sources.yaml.
Uwaga: pobiera tylko stronę 0 (20 rekordów), a „ogolne” ma 41. Dla pełnego pokrycia trzeba dodać stronicowanie
w json_api albo osobne wpisy `/0`, `/1`, `/2`. „stany-wod” to dane pomiarowe (307 rekordów) i raczej nie warto ich brać.

**Odyseusz a gov.pl.** `gov.pl/web/dyplomacja/informacje-dla-podrozujacych` („Bezpieczna podróż”) pokazuje
**tylko najnowszy komunikat** (dziś: Włochy, strajk generalny 20.11) i zachętę do rejestracji w Odyseuszu.
Pełnych ostrzeżeń dla krajów tam **nie ma**. Odyseusz (Angular) bierze je z publicznego API, które działa bez
logowania: `https://odyseusz.gov.pl/api/v1/informacje/profile-panstw` (lista profili krajów),
`/api/v1/informacje/profile-panstw/mini` i podobne. robots.txt odyseusz.gov.pl faktycznie nie istnieje (SPA zwraca
`index.html` z kodem 200, co według RFC 9309 oznacza brak reguł). Żądania `/api/v1/logowanie/*` dają 401, czyli dotyczą
tylko kont użytkowników. Do crawlera niczego nie dodałem, czeka na decyzję.
