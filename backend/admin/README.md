# admin

API panelu administracyjnego Prawdowida. To **osobny proces** niż publiczne API (`backend/api`), z tą samą bazą PostgreSQL. Interfejs jest w [`admin-frontend/`](../../admin-frontend/).

```bash
uv run admin            # 127.0.0.1:8001, dokumentacja: /admin/api/docs
uv run pytest backend/admin
```

Wymagane w `.env`: `ADMIN_PASSWORD` (co najmniej 12 znaków) i `ADMIN_SECRET` (co najmniej 32 znaki, podpisuje ciasteczko sesji). Bez nich proces nie startuje. [`.env.example`](../../.env.example) ma wartości deweloperskie, więc po `cp .env.example .env` panel działa od razu; zmień obie, zanim panel będzie dostępny dla kogokolwiek poza Tobą. Pozostałe zmienne (`ADMIN_ALERT_*`, `ADMIN_PUBLIC_URL`, `ADMIN_COOKIE_SECURE`) opisuje [`.env.example`](../../.env.example). Do dodawania linków potrzebny jest `INTAKE_CONTACT` (albo `CRAWLER_CONTACT`), a embedder musi być ten sam co w imporcie (`PRAWDOWID_EMBEDDER`).

## Co robi

| Obszar | Trasy (`/admin/api/...`) | Logika |
| --- | --- | --- |
| sesja | `POST login`, `POST logout`, `GET me` | [`auth.py`](src/admin/auth.py): jedno hasło, ciasteczko HttpOnly + SameSite=Strict podpisane HMAC (12 h), nagłówek `X-Requested-With: admin-panel` przy każdej zmianie, limit nieudanych logowań |
| pulpit | `GET overview`, `GET meta` | [`reports.py`](src/admin/reports.py): ostatnie 24 h i 7 dni, seria dzienna, alerty, źródła |
| historia | `GET checks`, `GET checks/{id}`, `PUT/DELETE checks/{id}/review` | `rag.admin_db`: stronicowanie po `(created_at, check_id)`, filtry (grupa, wynik, kanał, tekst, daty, bez kategorii). Tekst jest tylko zamaskowany. |
| błędy | `GET failures` | sprawdzenia bez werdyktu według kategorii z `rag.outcomes`, typy wyjątków |
| koszty | `GET usage?group=day\|model\|channel\|call` | tokeny i koszt w walucie cennika (PCSS w PLN, OpenAI/Anthropic w USD), bez przeliczania |
| źródła | `GET/POST sources`, `PATCH sources/{id}`, `GET/POST sources/{id}/documents`, `POST documents/{id}/withdraw\|restore` | wyłączanie, nadpisania odporne na import, ręczne źródła `admin-*`, dodawanie linku ([`links.py`](src/admin/links.py)), wycofanie dokumentu |
| domeny | `GET/POST trusted-domains`, `DELETE trusted-domains/{domain}` | domeny weryfikatorów dla wyszukiwania na żywo (`rag/live.py`) |
| alerty | `GET alerts`, `GET alerts/count`, `POST alerts/{id}/ack`, `POST alerts/ack-all` | `rag.alerts` |
| dziennik | `GET audit` | każda zmiana z panelu (`admin_audit`) |

## Zasady

- **Panel widzi tylko zamaskowany tekst użytkownika** (`checks.input_masked`, `result_json`). Oryginału nie ma w bazie.
- **Nic nie jest usuwane.** Wyłączone źródło wypada z wyszukiwania (FTS i indeks gęsty), a wycofany dokument dostaje `withdrawn_at` i `fts = NULL`. Rekordy zostają, więc dawne cytaty dalej da się prześledzić. Każda zmiana podbija `corpus_version`, więc działające API przeładowuje indeks bez restartu.
- **Import nie cofa decyzji admina.** `sources.upsert` nie dotyka `enabled` ani `admin_*`, a `upsert_evidence` nie przywraca wycofanej tożsamości `(source_id, upstream_id)`.
- **Dodanie linku ma reguły crawlera i ochronę SSRF**, bo idzie przez `intake.fetch.SafeFetcher`: robots.txt przy każdym adresie i przekierowaniu, co najmniej 3 s na host, uczciwy User-Agent z mailem, tylko publiczne adresy i porty 80/443, bez obchodzenia blokad i CAPTCHA. Adres musi być https w domenie źródła, także po przekierowaniach. Rekord przechodzi przez `ingest_corpus`, więc link w odpowiedziach pochodzi z `citation_url`.
- **Nowe źródła crawlera dodaje się w `backend/crawler/sources.yaml`**, nie w panelu: crawler nie zna bazy. W panelu można dodać źródło ręczne (`admin-<nazwa>`) z pojedynczymi dokumentami.

## Alerty

- **Kto je zapisuje.** Każdy proces, który loguje sprawdzenie (API, bot Discord, CLI), zapisuje alert do tabeli `alerts`, gdy wynik jest błędem technicznym lub częściowym błędem (`rag.outcomes.ALERT_OUTCOMES`). Alerty mają też sprawdzenia, które nie doszły do weryfikatora: brak klucza LLM (503) i nieoczekiwany wyjątek w API (500).
- **Deduplikacja.** Odcisk to `check:<kategoria>:<wyjątek>`, a powtórzenie podbija licznik otwartego alertu zamiast tworzyć nowy.
- **Wątek panelu** ([`notifier.py`](src/admin/notifier.py)) co `ADMIN_ALERT_POLL_S` sekund:
  - sprawdza regułę odsetka błędów: `ADMIN_ALERT_ERROR_RATE` w ostatnich `ADMIN_ALERT_WINDOW_MIN` minutach, przy co najmniej `ADMIN_ALERT_MIN_CHECKS` sprawdzeniach, bez sprawdzeń z kanału `eval`;
  - sprawdza źródła, których ostatnie pobranie się nie udało. Przy ustawionym `ADMIN_ALERT_STALE_HOURS` dochodzą źródła milczące dłużej niż ten próg;
  - wysyła niewysłane alerty na `ADMIN_ALERT_DISCORD_WEBHOOK`. Ten sam alert wraca najwyżej co 30 minut, z licznikiem. `allowed_mentions` jest puste, a przy 429 wątek czeka `retry_after`.
- **Treść.** Wiadomości na Discord budowane są z wiersza alertu: kategoria, nazwa wyjątku, liczba, nazwa źródła, link do panelu. **Nigdy nie zawierają tekstu użytkownika ani odpowiedzi modelu.**
- **Gdy panel nie działa,** alerty czekają w bazie i zostaną wysłane po jego starcie. Drugi proces panelu uruchamiaj z `ADMIN_NOTIFIER=0`.

## Wdrożenie

API panelu słucha domyślnie tylko na 127.0.0.1. Publiczny frontend przekazuje wyłącznie `/api/*`, a trasy `/admin/api/*` istnieją tylko w tym procesie. Jeśli panel ma być dostępny z sieci, wystaw go za reverse proxy z HTTPS, ustaw `ADMIN_COOKIE_SECURE=1` i najlepiej ogranicz dostęp do adresów zespołu (VPN, allowlista).
