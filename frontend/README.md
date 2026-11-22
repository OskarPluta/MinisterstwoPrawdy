# frontend

Interfejs WWW **Copernicus**: React + TypeScript, budowany i serwowany przez [Bun](https://bun.sh) (bez Vite i bez osobnego bundlera). Wygląd pochodzi z systemu projektowego **Ministerstwo Prawdy** (fikcyjne ministerstwo, układ stron usług publicznych).

```bash
cd frontend
bun install
bun run dev        # http://localhost:3000, hot reload
bun run typecheck
bun run build      # statyczny zrzut do dist/
bun run start      # serwer produkcyjny
```

## Strony

| Ścieżka | Plik | Co robi |
| --- | --- | --- |
| `/` | [`pages/CheckPage.tsx`](src/pages/CheckPage.tsx) | Pole wiadomości i wynik. Pole rośnie z tekstem, Enter wysyła, Shift+Enter dodaje nową linię. Wynik zastępuje formularz, a fokus przechodzi na jego tytuł. |
| `/zrodla` | [`pages/SourcesPage.tsx`](src/pages/SourcesPage.tsx) | Lista źródeł do audytu (`GET /api/sources`). |
| `/jak-to-dziala` | [`pages/HowPage.tsx`](src/pages/HowPage.tsx) | Metoda, znaczenie ocen, granice systemu, prywatność, deklaracja dostępności. |

`?now=<ISO 8601>` w adresie strony głównej przekazuje czas zapytania do API (demo na starym zrzucie).

## Jak to działa

- [`server.ts`](server.ts) to `Bun.serve`: importuje [`src/index.html`](src/index.html), a Bun sam bundluje TSX i CSS, do których strona się odwołuje. Style mają jedno wejście, [`app.css`](src/app.css), które przez `@import` wczytuje `tokens.css`, `components.css` i `extra.css`. Serwer deweloperski Buna nie zachowuje kolejności osobnych `<link>`, a `@import` ją ustala. Każda ścieżka poza `/api/*` dostaje `index.html`, a routing robi [`router.tsx`](src/router.tsx) (History API, bez zależności).
- `/api/*` jest przekazywane do backendu Pythona pod `API_URL` (domyślnie `http://127.0.0.1:8000`), więc kod frontu woła po prostu `fetch("/api/...")`, bez CORS. Gdy backend nie działa, proxy zwraca 502, a strona pokazuje komunikat o niedostępności.
- Zmienne: `API_URL`, `FRONTEND_PORT`. Bun czyta `.env` z `frontend/`, nie z katalogu głównego.

## System projektowy ([`src/design/`](src/design/))

| Plik | Pochodzenie |
| --- | --- |
| `tokens.css`, `components.css`, `fonts/` | Kopie 1:1 z paczki Ministerstwo Prawdy (`tokens.css`, `components/bundle.css`, Source Sans 3, licencja OFL). **Nie edytuj.** Poprawki wpisuj do `extra.css`. |
| `components.tsx`, `icons.ts` | Port `components/bundle.js` (React 18 UMD, `window.MP`) do TSX. Ta sama struktura HTML i klasy `mp-*`, a propsy jak w `components/index.d.ts`. |
| `extra.tsx`, `extra.css` | Elementy, których system nie ma: `Composer` (pole wiadomości z plikami: przycisk, Ctrl+V, przeciągnięcie), `CitationList`, `SourcesTable`, `MaterialList`, przełącznik kontrastu, poprawka obramowania `StatusPanel`. Do tego nadpisania systemu: łagodniejsze rogi i miękki niebieski fokus pola wiadomości. |

Zasady z systemu, których pilnuje kod:

- jeden niebieski kolor (`primary`) dla wszystkiego, co klikalne; czerwony `accent` tylko jako pasek identyfikacyjny,
- status zawsze jako ikona i słowo, nigdy sam kolor; `insufficient` („brak wystarczających dowodów”) jest informacją, nie błędem,
- lekko zaokrąglone rogi (6–12 px, nadpisane w `extra.css`), ale nagłówek, stopka i pasy strony zostają kwadratowe,
- tylko krótkie przejścia koloru i łagodne pojawianie się treści, wyłączane przy `prefers-reduced-motion`; brak cieni (poza nagłówkiem i fokusem pola) i kolorowych lewych obramowań,
- „Wersja kontrastowa” w górnym pasku ustawia `data-theme="contrast"` na `<html>` (żółte na czarnym, kontrast ≥ 7:1),
- stopka zawsze z dopiskiem „projekt fikcyjny”.

Mapowanie odpowiedzi API na widok (ton, tytuł, komunikaty) jest w [`result.tsx`](src/result.tsx). Tekst modelu jest wyświetlany jako zwykły tekst. Linki pochodzą tylko z `citation.url` (z bazy).

## Backend

[`backend/api/`](../backend/api/) (FastAPI) woła `rag.verify.Verifier`. Uruchom z katalogu głównego: `uv run api`. Typy odpowiedzi są w [`src/api.ts`](src/api.ts) i odwzorowują dataclassy z `rag/verify.py`.

| Trasa | Odpowiedź |
| --- | --- |
| `GET /api/health` | `{status, corpus_version}` |
| `POST /api/verify` `{text, now?}` | `CheckResult` bez pól wewnętrznych (`error`, `shortlist_ids`, `invented_ids`). 503, gdy nie ma klucza LLM. |
| `GET /api/sources` | `{totals, sources}`: katalog źródeł ze stanem pobrania i liczbą rekordów |

## Znane braki

- Bun wkleja fonty do CSS jako base64 (CSS ma ok. 250 KB). Działa, ale fonty mogłyby być osobnymi plikami.
- Nie ma eskalacji do moderatora ani drugiego kanału.
