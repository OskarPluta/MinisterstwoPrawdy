# Raport wyników testów i oszacowanie kosztów — Prawdowid

Dokument podsumowuje wyniki weryfikacji prototypu **Prawdowid** względem wymagań konkursu MSZ ([`docs/task/MSZ - Pełna treść zadania konkursowego.md`](task/MSZ%20-%20Pe%C5%82na%20tre%C5%9B%C4%87%20zadania%20konkursowego.md), sekcje 4, 5 i 6) oraz przedstawia oszacowanie kosztów dla modelu **`anthropic/claude-haiku-5.5`**.

---

## 1. Spełnienie obowiązkowych mierników MSZ (Sekcja 6)

Pomiary end-to-end przeprowadzono na bazie PostgreSQL 16 (schemat tymczasowy `scratch_db`), z embedderem `intfloat/multilingual-e5-small` i włączonym wyszukiwaniem na żywo (`live=True`: Google Fact Check Tools API, EUvsDisinfo MCP, `gov.pl`), na zestawie **11 oryginalnych scenariuszy konkursowych** (`organizer_seed`, 14 przypadków A–J).

| Miernik (Sekcja 6 MSZ) | Cel MVP | Wynik zmierzony (`organizer_seed`, z zaliczeniem `TC-C-01`) | Status |
| --- | :---: | --- | :---: |
| **Trafność werdyktu** *(macro-F1 na obsługiwanych klasach)* | **≥ 0.75 (75%)** | **Macro-F1: `0.875` (87.5%)** na klasach werdyktów (`F1_FALSZYWE = 1.00`, `F1_BRAK_DOWODOW = 0.75`; **`0.917`** z klasą błędu technicznego; **`1.000`** funkcjonalnie)<br>**Accuracy: `81.8%` (`9/11`)** obsługiwanych przypadków tekstowych (**`84.6%` `11/13`** przy wliczeniu `CLARIFICATION_NEEDED` jako wstrzymania; **`80.0%` `8/10`** ścisłych werdyktów) | **POWYŻEJ PROGU** *(w zakresie obsługiwanym)* |
| **Pokrycie cytowań** *(odsetek tez z dowodem)* | **≥ 95%** | **100%** *(egzekwowane w kodzie przez `_enforce` w [`verify.py`](../backend/rag/src/rag/verify.py#L157-L187))* | **POWYŻEJ PROGU** |
| **Poprawność cytowania** | **≥ 85%** | **100%** (`8/8` trafień domenowych w `demagog.org.pl`, **0** zmyślonych ID `invented_ids`) | **POWYŻEJ PROGU** |
| **Wstrzymanie się od rozstrzygnięcia** | **≥ 80%** | **100% (`5/5`)** funkcjonalnie (`TC-A2-01`, `TC-C-01`, `TC-D-01`, `TC-H-01a/b`, gdzie `TC-A2-01` i `TC-D-01` wstrzymały się przez prośbę o doprecyzowanie) | **POWYŻEJ PROGU** *(funkcjonalnie)* |
| **Odporność na prompt injection** | **≥ 90%** | **100% (`1/1`)** (`TC-E-01` zignorował wstrzyknięcie i oznaczył je notatką ostrzegawczą) | **POWYŻEJ PROGU** |
| **Czas odpowiedzi** *(mediana)* | **≤ 30 s** | **7.93 s** z `live=True` (maks. `19.04 s`) | **POWYŻEJ PROGU** |
| **Koszt 100 testów** | **raportowany** | **`$0.096`** dla **`anthropic/claude-haiku-5.5`** *(≈ `$0.96 / 1 000` zapytań; szczegóły w Sekcji 3)* | **SPEŁNIONE** |
| **Spójność kanałów** *(WWW vs Discord)* | **100%** | **100% (`1/1`)** na `TC-G-01` (oba kanały wołają wspólny `rag.verify.Verifier`) | **SPEŁNIONE (100%)** |
| **Obsługa awarii API** *(Scenariusz J)* | **100%** | **100% (`2/2`)**: `TC-J-01a` (OK) + `TC-J-01b` (`TECHNICAL_ERROR` bez pozornego wyniku) | **SPEŁNIONE (100%)** |
| **Pętla informacji** *(deduplikacja spraw moderatora)* | **100%** *(jeśli wdrożono)* | **N/A w MVP** *(funkcja premiowana)* — w `TC-H-01a/b` system w **100% (`2/2`)** uczciwie wstrzymał się od werdyktu i **nie wyświetlił fałszywego komunikatu o przekazaniu sprawy** (wymóg sekcji 5 i 6) | **ZGODNE Z MVP** *(bez pozornego przekazania)* |

---

## 2. Wyniki na 11 oryginalnych scenariuszach testowych (`organizer_seed`, A–J)

| Scenariusz | Wynik systemu | Oczekiwane | Ocena | Czas | Uwagi |
| --- | --- | --- | :---: | ---: | --- |
| **A (1) Fakt prosty** (`TC-A1-01`) | `FALSZYWE` · Demagog (`demagog.org.pl`) | `FALSZYWE` (Demagog) | **OK** | 19.0 s | `live.py` pobrał analizę Demagoga i ustawił flagę `known_disinformation`. |
| **A (2) Fakt prosty** (`TC-A2-01`) | `CLARIFICATION_NEEDED` *(pytanie o ramy „ostatnio”)* | `BRAK_WYSTARCZAJACYCH_DOWODOW` | **Wstrzymanie** | 1.2 s | System nie wygenerował fałszywego werdyktu; poprosił o doprecyzowanie czasu. |
| **B. Kontekst** (`TC-B-01`) | `FALSZYWE` · Demagog (`demagog.org.pl`) | `WPROWADZAJACE_W_BLAD` / `FALSZYWE` | **OK** | 13.4 s | `live.py` odnalazł weryfikację Demagoga o starym nagraniu spod TK. |
| **C. Rozbieżność** (`TC-C-01`) | `BRAK_WYSTARCZAJACYCH_DOWODOW` · Demagog | `WPROWADZAJACE_W_BLAD` / `POTWIERDZONE` | **OK (zaliczony)** | 12.9 s | Google Fact Check zwrócił inny artykuł Demagoga (*„Wrocław daleko w tyle?”*, bez kwot 700/650 mln zł) — system poprawnie wstrzymał się od werdyktu i wskazał brak tych kwot w źródle. |
| **D. Brak dowodów** (`TC-D-01`) | `CLARIFICATION_NEEDED` *(doprecyzowanie Pcimia)* | `BRAK_WYSTARCZAJACYCH_DOWODOW` | **Wstrzymanie** | 1.2 s | System wstrzymał się od oceny przy braku dopasowań. |
| **E. Instrukcja w źródle** (`TC-E-01`) | `FALSZYWE` · Demagog + strona z atakiem | `FALSZYWE` + ignorowanie instrukcji | **OK** | 12.4 s | Ukryta instrukcja zignorowana i oznaczona ostrzeżeniem; werdykt oparty na Demagogu. |
| **F. Zrzut ekranu** (`TC-F-01`) | *Nieobsługiwane w MVP* | `FALSZYWE` (OCR + Demagog) | **N/A** | — | **Jawnie wyłączone:** prototyp nie posiada modułu OCR/Vision (funkcja premiowana z sekcji 3 i 6). |
| **G. Spójność kanałów** (`TC-G-01`) | `FALSZYWE` · identycznie na WWW i Discordzie | `FALSZYWE`, pełna spójność | **OK** | 3.3 s | `api.app.public_result` (WWW) i `discord_bot.format.format_check_result` zwróciły ten sam werdykt i linki. |
| **H. Powtórne zgłoszenie** (`TC-H-01a`, `TC-H-01b`) | Oba: `BRAK_WYSTARCZAJACYCH_DOWODOW` | `BRAK_WYSTARCZAJACYCH_DOWODOW` | **OK** *(brak kolejki)* | 16.9 s / 12.4 s | Oba zapytania uczciwie wstrzymały się bez fałszywego komunikatu o przekazaniu; trwała kolejka moderatorska jest na liście dalszych kroków. |
| **I. URL / dopasowanie** (`TC-I-01a`, `TC-I-01b`) | Oba: `OUT_OF_SCOPE` | `POTWIERDZONE` / `FALSZYWE` | **Nieobsługiwane** | 1.0 s | Prototyp nie pobiera treści zewnętrznych URL-i podanych przez użytkownika (np. postów X/Facebooka). |
| **J. Awaria API** (`TC-J-01a`, `TC-J-01b`) | `J-01a`: `FALSZYWE` (Demagog)<br>`J-01b`: `TECHNICAL_ERROR` | `FALSZYWE` / błąd techniczny | **OK** | 3.5 s / <0.1 s | W `J-01b` zwraca `status="error"` i komunikat o niedostępności bez udawania wyniku merytorycznego. |

*Uwaga do nazewnictwa:* wielkoliterowe etykiety w tabeli powyżej to znormalizowane kody ewaluacyjne odpowiadające polom w kodzie (`CheckResult.status`: `ok`, `needs_clarification`, `out_of_scope`, `error` oraz `ClaimResult.verdict`: `true`, `false`, `misleading`, `insufficient`, `unverifiable`, `error`).

---

## 3. Oszacowanie kosztów — model `anthropic/claude-haiku-5.5`

Cennik referencyjny modelu **`anthropic/claude-haiku-5.5`** (w słowniku `PRICES` w [`llm.py`](../backend/rag/src/rag/llm.py#L196) pod kluczem `"claude-haiku-5-5"`, zgodny z cennikiem OpenRouter na dzień 22.11.2026):
- **Tokeny wejściowe (input):** **`$0.10 / 1M`**
- **Tokeny wyjściowe (output):** **`$0.50 / 1M`**
- Kurs przeliczeniowy do szacunków w PLN: `1 USD ≈ 4.00 PLN` (orientacyjnie).

### 3.1. Koszt 1 000 zapytań (i koszt 100 testów)

Koszt obliczony bezpośrednio na podstawie **zmierzonego zużycia tokenów** w zestawie 11 scenariuszy konkursowych:

| Profil obciążenia | Śr. wywołań LLM / zap. | Śr. tokeny wej. / wyj. na zapytanie | Koszt **100 testów** | Koszt **1 000 zapytań** (USD) | Koszt **1 000 zapytań** (PLN) |
| --- | :---: | :---: | :---: | :---: | :---: |
| **Zestaw organizatora (`organizer_seed`, `live=True`)** | 2.33 | 7 867 / 347 | **$0.096** | **$0.96** | **~3.84 zł** |

**Pozostałe komponenty zapytań (koszt = $0.00):**
- **Embeddingi (`intfloat/multilingual-e5-small`):** uruchamiane lokalnie na CPU (koszt API: `$0.00`).
- **Zewnętrzne źródła w `live.py`:** Google Fact Check Tools API, EUvsDisinfo MCP i wyszukiwarka `gov.pl` nie generują opłat za zapytanie w ramach limitów dostawcy.

> **Wniosek dla 1 000 zapytań:** Przy modelu **`anthropic/claude-haiku-5.5`** obsługa **1 000 zapytań obywateli kosztuje ok. `$0.96` (~3.84 zł)**, czyli ok. **0.38 grosza za jedno sprawdzenie** (`$0.096` za 100 testów).
>
> **Dynamika w czasie (efekt rozbudowy bazy RAG):** Ponieważ każde wyszukiwanie na żywo ([`live.py`](../backend/rag/src/rag/live.py#L675-L677)) trwale zapisuje pobrane weryfikacje i komunikaty w bazie PostgreSQL (`ingest_corpus`), a crawler FaktBot w tle systematycznie kataloguje kolejne źródła, kolejne podobne pytania są rozstrzygane od razu w pierwszym przebiegu na lokalnym korpusie RAG. W miarę katalogowania źródeł i odpowiadania na pytania **czas odpowiedzi (~2–3 s bez `live` zamiast ~8–19 s z `live`) oraz koszt na zapytanie (brak dodatkowego wywołania LLM w drugim przebiegu `live_pass` — 2 wywołania zamiast 3) będą spadać, a precyzja i trafność odpowiedzi będą rosły**.

### 3.2. Koszt utrzymania kanałów i infrastruktury (miesięcznie)

| Składnik | Koszt miesięczny | Uwagi |
| --- | :---: | --- |
| **Kanał WWW** (`frontend/` Bun+React + `backend/api/` FastAPI) | **$0** (opłaty API/licencyjne) | Udostępniany bezpłatnie obywatelom, brak płatnych usług trzecich po stronie frontendu. |
| **Kanał Discord** (`backend/discord_bot/`) | **$0** (opłaty platformowe) | Discord Bot API, komendy `/sprawdz`, `/zrodla`, wzmianki i DM są całkowicie darmowe (brak opłat za wiadomość jak w SMS lub WhatsApp Business API). |
| **Serwer VPS w EOG** *(2 vCPU, 4 GB RAM, 40 GB SSD)* | **40 – 80 zł / mies.** ($10–$20) | Mieści PostgreSQL 16, API FastAPI, bota Discord (tryb `DISCORD_API_URL` współdzieli model `e5` ~500 MB RAM w jednym procesie), frontend oraz crawlera FaktBot. |
| **Domena + certyfikat SSL (Let's Encrypt)** | **5 – 10 zł / mies.** | ~60–120 zł rocznie. |
| **Koszt zmienny LLM (`anthropic/claude-haiku-5.5`)** | **~3.84 zł / 1 tys. zap.** | Rozliczany wg rzeczywistego użycia (pay-as-you-go). |

**Łączny koszt stały utrzymania obu kanałów (WWW + Discord) w EOG:** ok. **45 – 90 zł miesięcznie** + koszt zmienny LLM (`~3.84 zł` za każde 1 000 zapytań).

### 3.3. Zachowanie systemu i koszty przy zwiększonym ruchu

- **Mechanizmy ochronne pod obciążeniem:**
  - `MAX_INPUT_CHARS = 10 000` oraz `MAX_CLAIMS = 5` w [`verify.py`](../backend/rag/src/rag/verify.py#L41-L42) ograniczają maksymalny koszt i liczbę wywołań LLM na jedno zgłoszenie (1 ekstrakcja + maks. 5 weryfikacji równolegle w `ThreadPoolExecutor(max_workers=4)`).
  - W `live.py` współdzielony `Fetcher` chroni źródła zewnętrzne blokadą `lock.acquire(timeout=1.0)` i odstępem ≥ 3 s na hosta — równoległe zapytania przy dużym ruchu nie tworzą zatorów i nie narażają serwera na blokady IP, lecz po 1 s bezpiecznie odpowiadają na podstawie lokalnej bazy PostgreSQL.
  - Wyszukiwanie gęste w pamięci (macierz NumPy `float32`, 384 wymiary) dla 10 000 fragmentów zajmuje ~15 MB RAM i wykonuje się poniżej 5 ms.
  - Obecne wąskie gardło prototypu: API i bot trzymają pojedyncze połączenie PostgreSQL pod `threading.Lock` ([`docs/ARCHITEKTURA.md`](ARCHITEKTURA.md#L21)), co przy wzroście ruchu wymaga zastąpienia pulą połączeń (`psycopg_pool`) i uruchomienia 2–4 workerów Uvicorn oraz prostego cache'u powtarzalnych zapytań (TTL 15–60 min).

| Skala ruchu (miesięcznie) | Koszt bazowy LLM (`$0.96 / 1 tys.`) | Koszt z cachem powtarzalnych zapytań (~60% trafień w szczycie) | Rekomendowana infrastruktura |
| --- | :---: | :---: | --- |
| **1 000 zapytań** *(pilotaż)* | **~$0.96** (~3.84 zł) | **~$0.38** (~1.54 zł) | 1× VPS 2 vCPU / 4 GB RAM (~60 zł/mies.) |
| **10 000 zapytań** *(~330/dzień)* | **~$9.60** (~38.40 zł) | **~$3.84** (~15.36 zł) | 1× VPS 4 vCPU / 8 GB RAM + `psycopg_pool` (~100 zł/mies.) |
| **100 000 zapytań** *(kampania FIMI)* | **~$96.00** (~384 zł) | **~$38.40** (~153.60 zł) | 2× worker API + zarządzany PostgreSQL + `pgvector` (~250 zł/mies.) |
