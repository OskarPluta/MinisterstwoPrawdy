# admin-frontend

Panel administracyjny **Copernicus**: React + TypeScript serwowane przez [Bun](https://bun.sh), w tym samym systemie projektowym Ministerstwo Prawdy co publiczny [`frontend/`](../frontend/). Backend: [`admin-backend`](../admin-backend/README.md).

```bash
cd admin-frontend
bun install
bun run dev        # http://127.0.0.1:3001 (wymaga działającego `uv run admin`)
bun run typecheck
bun run build      # statyczny zrzut do dist/
bun run start      # serwer produkcyjny
```

Zmienne: `ADMIN_API_URL` (domyślnie `http://127.0.0.1:8001`), `ADMIN_FRONTEND_PORT` (3001), `ADMIN_FRONTEND_HOST` (127.0.0.1). Bun czyta `.env` z tego katalogu, a [`scripts/dev.sh`](../scripts/dev.sh) eksportuje je z głównego `.env`.

## Strony

| Ścieżka | Plik | Co robi |
| --- | --- | --- |
| `/` | [`OverviewPage.tsx`](src/pages/OverviewPage.tsx) | Ostatnie 24 h i 7 dni: sprawdzenia, odsetek błędów, koszt, tokeny, czas; wykres dzienny; otwarte alerty i źródła z błędem |
| `/historia` | [`HistoryPage.tsx`](src/pages/HistoryPage.tsx) | Historia wiadomości z filtrami (stan w adresie). `?id=` pokazuje szczegóły: zamaskowaną treść, odpowiedź, błędy, wywołania LLM i formularz kategorii |
| `/bledy` | [`FailuresPage.tsx`](src/pages/FailuresPage.tsx) | Nieudane sprawdzenia według kategorii i typów wyjątków, liczba bez kategorii |
| `/koszty` | [`CostsPage.tsx`](src/pages/CostsPage.tsx) | Tokeny i koszt według dnia, modelu, kanału i rodzaju wywołania |
| `/zrodla` | [`SourcesPage.tsx`](src/pages/SourcesPage.tsx) | Włączanie, wyłączanie i edycja źródeł, źródło ręczne. `?id=` pokazuje dokumenty źródła: dodanie linku, wycofanie i przywrócenie |
| `/domeny` | [`DomainsPage.tsx`](src/pages/DomainsPage.tsx) | Domeny weryfikatorów dla wyszukiwania na żywo |
| `/alerty` | [`AlertsPage.tsx`](src/pages/AlertsPage.tsx) | Alerty z potwierdzaniem. Licznik otwartych jest w menu i odświeża się co 30 s |
| `/dziennik` | [`AuditPage.tsx`](src/pages/AuditPage.tsx) | Dziennik zmian wprowadzonych w panelu |

## Jak to działa

- **Wspólny kod z publicznym frontem.** Panel importuje przez ścieżki względne komponenty i style z [`../frontend/src/design/`](../frontend/src/design/), [`router.tsx`](../frontend/src/router.tsx) i [`theme.ts`](../frontend/src/theme.ts), a style przez `@import "../../frontend/src/app.css"`. Kopii nie ma. Własne klasy panelu (`adm-*`) są w [`src/app.css`](src/app.css) i używają tylko tokenów systemu.
- **Jedna kopia Reacta.** Pliki z `../frontend` szukałyby `react` w `frontend/node_modules`, więc powstałyby dwie kopie i hooki by się zepsuły. Wtyczka [`dedupe-react.ts`](dedupe-react.ts) kieruje każdy import `react`/`react-dom` do kopii panelu. Serwer deweloperski bierze ją z [`bunfig.toml`](bunfig.toml), a `bun run build` z [`build.ts`](build.ts).
- **Komponenty panelu** ([`src/ui.tsx`](src/ui.tsx)): `DataTable` (na `.mp-table`, na telefonie karty), `Select`, `TextArea`, `Checkbox`, `Badge`, `StatTiles`, `ConfirmDialog` (natywny `<dialog>`), `StackedBars` (SVG bez biblioteki) i `useAsync`.
- **Zasady z publicznego frontu.** Tekst użytkownika i modelu jest renderowany jako zwykły tekst, nigdy jako HTML ani Markdown, a linki pochodzą tylko z `citation.url` i adresów dokumentów w bazie. „Brak dowodów” nigdy nie jest czerwony: na czerwono są tylko błędy techniczne. Czas jest pokazywany w strefie Europe/Warsaw.
- **API.** Klient jest w [`src/api.ts`](src/api.ts). Każde żądanie wysyła ciasteczko sesji i nagłówek `X-Requested-With: admin-panel`, a odpowiedź 401 przełącza na ekran logowania. Typy odwzorowują JSON z `admin-backend/src/admin/app.py`, więc zmieniaj je razem.
