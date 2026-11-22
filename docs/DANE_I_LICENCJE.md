# Dane, licencje i usuwanie

Odpowiedź na wymóg konkursu (`docs/task/`, sekcja ograniczeń): „Nie wolno archiwizować pełnych treści i obrazów bez określenia podstawy prawnej, licencji, retencji, kontroli dostępu oraz procedury usunięcia”.
Dokument opisuje stan na 2026-11-22. Warunki źródeł odczytano tego dnia ze stron wydawców. To nie jest opinia prawna.

## Co przechowujemy

| Miejsce | Zawartość | Pełna treść? | Retencja | Dostęp |
| --- | --- | --- | --- | --- |
| `data/snapshot/documents.jsonl` (w git) | Zamrożony zrzut ok. 100 dokumentów, wyłącznie instytucje publiczne i IMGW. | Tak | Do zmiany zrzutu. Historia git zachowuje stare wersje. | Każdy z dostępem do repozytorium. Warunki ponownego użycia: [`data/snapshot/README.md`](../data/snapshot/README.md). |
| `data/crawl/` (poza git) | Bieżące przebiegi crawlera: `documents.jsonl` z wersjami, `state.json`, `crawl_log.jsonl`. | Tak, oprócz fact-checków Google (same metadane). | Dopóki źródło jest w `sources.yaml` i nie trafiło do `removed.yaml`. | Konto systemowe, na którym działa crawler. Brak usługi sieciowej. |
| PostgreSQL `evidence`, `passages` | Rekordy z wersjami, fragmenty do cytowania, wektory. | Tak, w podziale na fragmenty. | Jak `data/crawl/`. Starsze wersje zostają do śledzenia cytatów. | Baza słucha tylko na `127.0.0.1` (`docker-compose.yml`), hasło w `.env` (poza git). |
| PostgreSQL `sources` | Katalog źródeł z licencją (`license`, `terms_url`, `attribution`). | Nie | Jak wyżej. | Publiczny przez `GET /api/sources` i stronę „Źródła”. |
| PostgreSQL `checks` | Log sprawdzeń: zamaskowany tekst użytkownika i wynik z cytatami. | Nie (cytaty mają ≤ 400 znaków) | **30 dni** (`PRAWDOWID_CHECKS_RETENTION_DAYS`, `0` wyłącza log). Usuwanie automatyczne, najwyżej raz na godzinę. | Autor sprawdzenia: usunięcie po `check_id`. Osoby z linkiem: odczyt wyniku, jeśli autor go udostępnił (`/wynik/<share_id>`). Administrator (token `PRAWDOWID_ADMIN_TOKEN`, konto systemowe): eksport CSV/JSON bez treści wiadomości i bez `check_id`. |
| Wyszukiwanie na żywo (`rag/live.py`) | Artykuły gov.pl, sprawy i artykuły EUvsDisinfo oraz ClaimReview, pobrane w trakcie sprawdzenia. | Tak (gov.pl, EUvsDisinfo) | Jak `evidence`. Źródła `live-*` też podlegają `removed.yaml` i `rag purge`. | Jak `evidence`. |

API nie ma endpointu zwracającego cały dokument. Użytkownik widzi tylko cytowane fragmenty (≤ 400 znaków) z linkiem do oryginału.

## Źródła: podstawa prawna i licencja

Reguły są w [`backend/crawler/sources.yaml`](../backend/crawler/sources.yaml) (klucz `licenses`, dopasowanie po hoście). **Źródło bez licencji nie przejdzie walidacji konfiguracji.** Crawler nie wystartuje, a `rag ingest` nie wczyta katalogu. Licencja trafia do tabeli `sources` i na stronę „Źródła” (kolumna „Licencja” z linkiem „warunki”).

| Źródło (hosty) | Podstawa / licencja | Co zapisujemy | Oznaczenie źródła | Stan ustaleń |
| --- | --- | --- | --- | --- |
| Portal gov.pl (`www.gov.pl`): ministerstwa, RCB, KG PSP, RSO, MSZ, urzędy wojewódzkie, sanepid… | Teksty na licencji **CC BY-SA 4.0**. „Korzystanie przez użytkowników z materiałów urzędowych” nie wymaga zgody ([prawa autorskie gov.pl](https://www.gov.pl/web/gov/prawa-autorskie)). Zdjęcia i wideo mają CC BY-NC-ND, ale ich nie pobieramy. | Pełny tekst komunikatu | Instytucja i link przy każdym cytacie. | Potwierdzone na stronie wydawcy. |
| Policja (`policja.pl`, `*.policja.gov.pl`), Urząd Morski w Gdyni i Szczecinie, MSZ Odyseusz | Materiał urzędowy organu publicznego (art. 4 pkt 2 ustawy o prawie autorskim): komunikaty organów nie są przedmiotem prawa autorskiego. | Pełny tekst | Instytucja i link. | Kwalifikacja przyjęta przez zespół. Wydawcy nie publikują osobnej licencji. |
| IMGW-PIB (`danepubliczne.imgw.pl`, `baltyk-api.imgw.pl`) | „Regulamin udostępniania danych” IMGW-PIB (ustawa o otwartych danych). Bezpłatnie do celów „prywatnych, niezarobkowych”, przy oznaczeniu źródła. | Rekordy ostrzeżeń i pomiarów. Renderujemy je stałym szablonem, więc są to dane przetworzone. | Pod każdym cytatem: „Źródłem pochodzenia danych jest IMGW-PIB.” oraz „Dane IMGW-PIB zostały przetworzone.” (§5 ust. 3 i 4 regulaminu). | Potwierdzone. **Uwaga:** wdrożenie komercyjne albo produkcyjne wymaga umowy z IMGW (§3 ust. 2). |
| GIOŚ (`api.gios.gov.pl`) | Dane publiczne Państwowego Monitoringu Środowiska. | Indeksy jakości powietrza | „Źródło danych: GIOŚ, PMŚ” w rekordzie. | **Do potwierdzenia**: strony warunków nie udało się odczytać automatycznie. |
| Google Fact Check Tools (`factchecktools.googleapis.com`): Demagog, OKO.press, AFP Sprawdzam, FakeNews.pl | [Google APIs Terms of Service](https://developers.google.com/terms). | **Tylko metadane ClaimReview**: teza, ocena, tytuł, link. Bez treści artykułów. | Wydawca fact-checku i link. | **Ryzyko otwarte**, opisane niżej. |
| EUvsDisinfo (`mcp.euvsd.info`, `euvsdisinfo.eu`) | Dostęp do serwera MCP tylko na wniosek (mamy klucz). Nota prawna ESDZ: „Reuse is authorised, provided the source is acknowledged”, z wyłączeniem materiałów osób trzecich. | Pełne sprawy i artykuły analityczne | Wydawca i link. | **Do potwierdzenia**: nota ESDZ obejmuje stronę ESDZ, a strona EUvsDisinfo nie podaje własnej licencji. |
| CERT Polska (`moje.cert.pl`), Straż Ochrony Kolei (`kgsok.pl`) | Licencja nieokreślona przez wydawcę. Prawo cytatu (art. 29) obejmuje krótkie fragmenty z podaniem źródła. | Pełny tekst | Wydawca i link. | **Ryzyko otwarte**, opisane niżej. |

## Otwarte ryzyka (decyzje zespołu)

1. **Google Fact Check API, §5(e) regulaminu.** Regulamin zabrania „build databases, or otherwise create permanent copies” treści z API, chyba że zezwala na to właściciel treści albo prawo. Zapisujemy tylko metadane ClaimReview, które fact-checkerzy sami publikują jako znaczniki schema.org. To ogranicza ryzyko, ale go nie usuwa. Są dwie drogi: zgoda fact-checkerów (Demagog, OKO.press, AFP, FakeNews.pl) albo pobieranie ClaimReview bezpośrednio z ich stron (RSS albo mapa strony, z zachowaniem robots.txt). Do tego czasu źródła `factcheck-*` i `live-factcheck-*` można jednym wpisem przenieść do `removed.yaml`.
2. **CERT Polska i SOK.** Bez licencji wolno nam trzymać krótkie cytaty, a nie pełne teksty. Do wyboru: zgoda wydawcy, przycięcie treści do leadu albo usunięcie (`source: cert-polska-komunikaty` i `source: kgsok` w `removed.yaml`, potem procedura niżej). Pięć komunikatów SOK jest w `data/snapshot/`.
3. **EUvsDisinfo.** Warto potwierdzić u East StratCom Task Force, że nota ESDZ obejmuje bazę. Cytowane artykuły prokremlowskie to materiały osób trzecich, ale pokazujemy z nich tylko krótkie fragmenty jako dowód narracji.
4. **IMGW poza konkursem.** Regulamin dopuszcza bez umowy tylko użycie niezarobkowe.
5. **Historia git.** Usunięcie dokumentu ze zrzutu nie usuwa go z historii. Pełne wymazanie wymaga przepisania historii (`git filter-repo`) i wymuszonego wypchnięcia, co jest decyzją właściciela repozytorium.

## Retencja

- **Korpus** (`data/crawl/`, `evidence`, `passages`): przechowujemy tak długo, jak źródło jest w `sources.yaml` i nie ma go w `removed.yaml`. Starsze wersje zostają do śledzenia cytatów do czasu usunięcia całego dokumentu.
- **Log sprawdzeń** (`checks`): 30 dni od sprawdzenia. Usuwa go sam weryfikator (API, CLI, Discord) najwyżej raz na godzinę. Ręcznie: `uv run rag prune-checks [--days N]`. `PRAWDOWID_CHECKS_RETENTION_DAYS=0` wyłącza zapis logu.
- **Dane użytkownika**: przed zapisem maskujemy e-maile, telefony, PESEL, numery dowodów i rachunków (`verify.mask`). Discord przechowuje swoje wiadomości na własnych zasadach. Nie kontrolujemy ich.

## Kontrola dostępu

- PostgreSQL działa tylko na `127.0.0.1` z hasłem z `.env` (poza git). Klucze API (Google, EUvsDisinfo, LLM) są tylko w środowisku i nie trafiają do danych ani do URL.
- API (`uv run api`) domyślnie słucha na `127.0.0.1`. Publicznie wystawiamy tylko frontend (`scripts/tunnel.sh`), który przekazuje `/api/*`. API daje werdykt z cytatami (≤ 400 znaków) i katalog źródeł, nigdy całych dokumentów.
- Limity żądań: API i bot przyjmują od jednego klienta 5 sprawdzeń na minutę i 30 na godzinę, a cały proces najwyżej 1000 na dobę (`PRAWDOWID_RATE_*`). Nadmiar dostaje 429 z komunikatem.
- Udostępnienie wyniku: `POST /api/checks/{check_id}/share` tworzy osobny losowy `share_id`. Strona `/wynik/<share_id>` pokazuje zamaskowany wynik z logu, tylko do odczytu i z `noindex`. Link przestaje działać po wyłączeniu udostępniania, usunięciu zapisu albo po upływie retencji.
- Usunięcie zapisu sprawdzenia: `DELETE /api/checks/{check_id}`. `check_id` to losowy UUID4 zwracany tylko autorowi sprawdzenia, więc nikt inny go nie zna. Innych operacji zapisu API nie udostępnia.
- Eksport dla analityka (`GET /api/export`, `uv run rag export`): tylko dla kogoś, kto zna `PRAWDOWID_ADMIN_TOKEN` (nagłówek `Authorization: Bearer`, porównanie w stałym czasie, co najmniej 16 znaków) albo ma dostęp do bazy na koncie systemowym. Bez tej zmiennej endpoint odpowiada 404. Żądania, także nieudane, liczy ten sam limit co zapisy (20 na minutę). Eksport zawiera tylko dane już zamaskowane: twierdzenia, werdykty, siłę dowodów, cytaty (≤ 400 znaków) z linkami, model i wersję korpusu. Nie zawiera treści wiadomości użytkownika ani `check_id`, który jest kluczem usunięcia. Zamiast niego jest `check_ref`, czyli skrót SHA-256, z którego nie da się odtworzyć klucza. Udostępnienie widać tylko jako flagę `shared`, bez `share_id`.
- Wyeksportowany plik żyje poza retencją bazy. Odbiorca odpowiada za usunięcie go po 30 dniach albo po wniosku (wiersz znajdzie po `check_ref`, który właściciel `check_id` może policzyć: `rag.export.check_ref`). Cytaty zachowują licencje źródeł (`GET /api/sources`), w tym wymagane oznaczenia (`attribution` w JSON).
- Przyciski „Pobierz JSON” i „Pobierz CSV” pod wynikiem budują plik w przeglądarce z wyświetlonego wyniku. Serwer nic nie wydaje, a plik nie zawiera `check_id`.
- `data/crawl/` i `.env` są w `.gitignore`. Do repozytorium trafia tylko `data/snapshot/`.

## Procedura usunięcia

### Wniosek wydawcy albo brak podstawy prawnej

1. Dopisz wpis do [`backend/crawler/removed.yaml`](../backend/crawler/removed.yaml): `url` (adres dokumentu) albo `source` (nazwa źródła lub identyfikator `live-*`), do tego `date` i `reason`. Plik jest zarazem rejestrem usunięć, a wpis bez daty i powodu jest odrzucany.
2. `uv run crawler forget` przepisuje `data/crawl/documents.jsonl` bez tych dokumentów i czyści po nich indeks i stan. To jedyny wyjątek od zasady „tylko dopisujemy”.
3. `uv run crawler --data-dir data/snapshot forget`, jeśli dokument jest w zrzucie, a potem commit.
4. `uv run rag purge` kasuje z bazy wszystkie wersje rekordów, ich fragmenty i wektory, wiersze źródeł (dla wpisów `source`) oraz logi sprawdzeń, które cytowały lub linkowały usunięte adresy. Podbija `corpus_version`.
5. Od tej chwili crawler nie zapisze już tych dokumentów, a `rag ingest` i wyszukiwanie na żywo je pomijają (licznik `removed`).
6. Jeśli wpis źródła ma zostać usunięty na stałe, ustaw w `sources.yaml` `enabled: false`, żeby crawler nie odpytywał go na próżno.

Doraźnie, bez rejestru: `uv run rag purge --url <adres> --source <id>`. Bez wpisu w `removed.yaml` dokument wróci przy następnym `ingest`, o czym polecenie przypomina.

### Wniosek użytkownika

- W interfejsie WWW pod wynikiem jest przycisk „Usuń zapis tego sprawdzenia” (`DELETE /api/checks/{check_id}`).
- Usunięcie zapisu nie sięga do plików wyeksportowanych wcześniej (patrz „Kontrola dostępu”).
- Ręcznie: `uv run rag delete-check <check_id>`. Bez identyfikatora zapis zniknie sam po 30 dniach. Zapisu nie wiążemy z kontem, więc bez `check_id` nie wskażemy, który należy do kogo.

## Zmiany

- Nowe źródło: dodaj regułę w `licenses` albo pole `license` (i `terms_url`) w źródle. Uzupełnij tę tabelę.
- Nowe wymaganie oznaczenia źródła: `attribution` w źródle (pokazywane pod każdym cytatem) albo `processed_notice` w regule (pod danymi renderowanymi szablonem).
