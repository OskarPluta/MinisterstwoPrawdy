# Dodatkowe źródła i informacje techniczne (materiały od mentora)

Materiały spoza oficjalnych dokumentów zadania w repozytorium: linki udostępnione przez mentora (**Piotr Sosnowski / `ps_msz`**) na Discordzie (kanał `Prawdowid`) oraz zweryfikowane szczegóły techniczne dotyczące API.

---

## 1. Przykładowe źródła aktualności i ostrzeżeń (domeny rządowe)

> *Uwaga od mentora:* są to przykłady — nie trzeba implementować wszystkich jako źródeł.

### Podróże, dyplomacja i sytuacja międzynarodowa (MSZ)
- Odyseusz (MSZ): https://odyseusz.gov.pl/
- MSZ — Informacje dla podróżujących: https://www.gov.pl/web/dyplomacja/informacje-dla-podrozujacych

### Zarządzanie kryzysowe, ostrzeżenia i administracja (RCB, RSO, MSWiA, KPRM)
- Gov.pl — System ostrzeżeń: https://www.gov.pl/web/gov/ostrzezenia
- Gov.pl — Ostrzeżenia (przykład: mazowieckie): https://www.gov.pl/web/gov/ostrzezenia#/_/mazowieckie/wszystkie
- MSWiA — Regionalny System Ostrzegania (RSO): https://www.gov.pl/web/mswia/regionalny-system-ostrzegania
- MSWiA — Strona główna: https://www.gov.pl/web/mswia
- RCB — Strona główna: https://www.gov.pl/web/rcb/strona-glowna-rzadowego-centrum-bezpieczenstwa
- RCB — Raport dobowy: https://www.gov.pl/web/rcb/raport-dobowy3
- KPRM (Premier): https://www.gov.pl/web/premier

### Służby, bezpieczeństwo, obronność i cyberbezpieczeństwo
- Komenda Główna PSP — Aktualności: https://www.gov.pl/web/kgpsp/aktualnosci
- Policja — Aktualności: https://policja.pl/pol/aktualnosci
- Policja Mazowiecka — Aktualności: https://mazowiecka.policja.gov.pl/ra/aktualnosci/aktualnosci
- MON — Aktualności: https://www.gov.pl/web/obrona-narodowa/aktualnosci5
- CERT Polska — Komunikaty: https://moje.cert.pl/komunikaty/

### Zdrowie, środowisko, promieniowanie, pogoda i hydrologia
- GIS — Ostrzeżenia: https://www.gov.pl/web/gis/ostrzezenia
- Państwowa Agencja Atomistyki (PAA): https://www.gov.pl/web/paa/panstwowa-agencja-atomistyki
- PAA — Monitoring radiacyjny: https://monitoring.paa.gov.pl/
- GIOŚ — Jakość powietrza: https://powietrze.gios.gov.pl/pjp/
- IMGW — Meteo: https://meteo.imgw.pl/
- IMGW — Hydro: https://hydro.imgw.pl/
- IMGW — Bałtyk: https://baltyk.imgw.pl/

### Infrastruktura, drogi, kolej, lotnictwo i żegluga
- Ministerstwo Infrastruktury: https://www.gov.pl/web/infrastruktura
- MI — Aktualności: https://www.gov.pl/web/infrastruktura/aktualnosci
- MI — Prawa pasażerów: https://www.gov.pl/web/infrastruktura/prawa-pasazerow
- MI — Infolinia dla obywatela: https://www.gov.pl/web/infrastruktura/infolinia-dla-obywatela
- MI — Dane kontaktowe: https://www.gov.pl/web/infrastruktura/dane-kontaktowe
- GDDKiA — Informacje drogowe: https://drogi.gddkia.gov.pl/informacje-drogowe
- GDDKiA — Mapa informacji drogowej: https://drogi.gddkia.gov.pl/mapy/mapa-informacji-drogowej
- GDDKiA — Lista utrudnień: https://drogi.gddkia.gov.pl/informacje-drogowe/lista-utrudnien
- GDDKiA — Informacja drogowa 19 111: https://www.gov.pl/web/gddkia/informacja-drogowa---19-111
- Urząd Lotnictwa Cywilnego (ULC): https://ulc.gov.pl/
- ULC — Prawa pasażera: https://ulc.gov.pl/prawa-pasazera
- ULC — Poradnik dla pasażerów: https://ulc.gov.pl/prawa-pasazera/poradnik-dla-pasazerow
- Portal Pasażera (kolej): https://portalpasazera.pl/
- UTK — Pasażerowie: https://utk.gov.pl/pl/pasazerowie
- Straż Ochrony Kolei (KGSOK): https://kgsok.pl/
- Morska Służba Poszukiwania i Ratownictwa (SAR): https://www.sar.gov.pl/
- Urząd Morski w Gdyni: https://www.umgdy.gov.pl/
- Urząd Morski w Szczecinie — Warunki żeglugi: https://www.ums.gov.pl/bezpieczenstwo-morskie/warunki-zeglugi
- Wody Polskie — Komunikaty nawigacyjne: https://www.gov.pl/web/wody-polskie/komunikaty-nawigacyjne

---

## 2. Baza weryfikatorów faktów — Google Fact Check Tools API (w tym Demagog)

- **Explorer (PL):** https://toolbox.google.com/factcheck/explorer/search/list:recent;hl=pl
- **Dokumentacja REST API:** https://developers.google.com/fact-check/tools/api/reference/rest?hl=pl

### Szczegóły techniczne
- **Demagog.org.pl** nie ma własnego publicznego API (a `demagog.org.pl/wp-json/...` zwraca `HTTP 403` za Cloudflare). Jego weryfikacje (oraz innych polskich organizacji fact-checkingowych, np. Konkret24, AFP Sprawdzam, Pravda) są publikowane w standardzie `schema.org/ClaimReview` i indeksowane właśnie w **Google Fact Check Tools API**.
- **Endpoint wyszukiwania (`v1alpha1`):**
  ```http
  GET https://factchecktools.googleapis.com/v1alpha1/claims:search
  ```
- **Autoryzacja:** darmowy klucz API z Google Cloud (`key=YOUR_API_KEY`).
- **Najważniejsze parametry query:**
  - `query` — tekst twierdzenia / krótkie słowa kluczowe (wymagane, jeśli nie podano `reviewPublisherSiteFilter`),
  - `languageCode` — np. `pl`,
  - `reviewPublisherSiteFilter` — np. `demagog.org.pl` (ogranicza wyniki do wybranego wydawcy; brak parametru przeszukuje wszystkich weryfikatorów w danym języku),
  - `maxAgeDays` — maks. wiek twierdzenia/weryfikacji w dniach,
  - `pageSize` (domyślnie `10`), `pageToken`, `offset`.
- **Zwracane pola (JSON):**
  - `claims[].text`, `claims[].claimant`, `claims[].claimDate`,
  - `claims[].claimReview[]`: `publisher.name`, `publisher.site`, `url`, `title`, `reviewDate`, `textualRating` (np. `Fałsz`, `Prawda`, `Manipulacja`), `languageCode`.
- **Ograniczenia:**
  - API nie zwraca pełnej treści artykułu, tylko streszczenie twierdzenia, werdykt i metadane z linkiem.
  - Endpoint `claims:imageSearch` wymaga osobnej autoryzacji (dla zweryfikowanych dziennikarzy/fact-checkerów).

---

## 3. Wrogie operacje informacyjne / FIMI — serwer MCP EUvsDisinfo

- **Instrukcja techniczna (PDF):** https://euvsdisinfo.eu/public-docs/How_to_grab_all_Articles_and_Cases_from_disinfo.pdf
- **Formularz o token dostępowy MCP:** https://euvsdisinfo.eu/about/#aaad5def9c9014028bbdba28e341feb7
- **Kontakt w razie problemu z tokenem:** PW na Discordzie do **`ps_msz`** (Piotr Sosnowski).

### Szczegóły techniczne (dokumentacja Fibery „EUvsDisinfo MCP. API.”, wersja z 4.05.2026; sprawdzone 2026-11-21)
- **Adres serwera MCP:** `https://mcp.euvsd.info/mcp`. **Stary adres z PDF (`euvsdisinfo-mcp-server.vercel.app`) nie przyjmuje nowych kluczy** (401 „Invalid API key”).
- **Health check (bez klucza):** `GET https://mcp.euvsd.info/health`.
- **Klucz:** `?key=KLUCZ`, nagłówek `Authorization: Bearer KLUCZ` albo `X-API-Key: KLUCZ`. Trzymamy go tylko w `.env` (`EUVSDISINFO_API_KEY`).
- **Transport:** Streamable HTTP, JSON-RPC 2.0, wymagany `Accept: application/json, text/event-stream`. Odpowiedzi przychodzą jako zdarzenia SSE (`data: {...}`).
- **Tryby:**
  1. stanowy: `initialize` → nagłówek `mcp-session-id` → `tools/list` / `tools/call` z tym nagłówkiem → opcjonalnie `DELETE`;
  2. bezstanowy: od razu `tools/call` z dowolnym, wygenerowanym przez klienta `mcp-session-id` (tak działa Claude.ai).
- **Narzędzia:**
  1. **`cases`** (`limit` 1–100, `offset` ≥ 0): ~19 000 spraw dezinformacji, od najnowszych. Pola: `id`, `title`, `claim`, `fact`, `date`, `keywords`, `countries`, `linksToDisinformation[]`, `url`;
  2. **`articles`** (`limit` 1–100, `offset` ≥ 0): ~6 800 artykułów analitycznych. Pola: `id`, `title`, `content`, `dateGmt`, `keywords[]`, `categories[]`, `articleLanguage[]`, `thumbnailURL`, `url`;
  3. **`search`** (`query` ≥ 3 znaki, opcjonalnie `date_from` / `date_to` w formacie `YYYY-MM-DD`): wyszukiwanie pełnotekstowe w sprawach i artykułach, 10 najlepszych wyników (`type`, `title`, `content`, `link`);
  4. **`narratives`** (opcjonalnie `date_from` / `date_to`): narracje dezinformacyjne w okresie (`narrative`, `date`).
- **Uwaga:** `tools/call` zwraca **tekst w Markdown** (np. `**Case 1: …**`, `**Claim:**`, `**Fact Check:**`), a nie JSON, więc importer musi go parsować.
- **Kody błędów:** 401/-32001 brak lub zły klucz, 406 zły `Accept`, 400 zła sesja, 500 błąd po stronie serwera.
- **Uwaga architektoniczna:** od wersji z 2026 r. serwer **ma narzędzie `search`**, więc można go odpytywać na żywo w trakcie weryfikacji. `cases` / `articles` nadal pozwalają zasilić lokalny indeks partiami po 100 rekordów.
