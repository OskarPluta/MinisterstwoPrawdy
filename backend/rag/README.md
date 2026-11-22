# rag

Pakiet `rag`: ładuje dane crawlera do PostgreSQL, wyszukuje fragmenty hybrydowo (pełny tekst + e5 + RRF) i weryfikuje twierdzenia przez LLM, a cytaty kontroluje kod.
Polecenia: `uv run rag ingest | reembed | stats | search | verify` (opcje: `--help`). Powiązania z crawlerem: [`docs/ARCHITEKTURA.md`](../../docs/ARCHITEKTURA.md).

## Potok i pliki (`src/rag/`)

| Krok | Plik | Co robi |
| --- | --- | --- |
| konwersja | `crawler_import.py` | `documents.jsonl` + `crawl_log.jsonl` → korpus w pamięci (`sources`, `evidence`) |
| ingest | `ingest.py` | Walidacja rekordów, pominięcie niezmienionych, chunking, embeddingi w partiach, zapis. `reembed` przelicza wektory. |
| fragmenty | `chunking.py` | Artykuły: podział po akapitach z limitem słów. Rekordy strukturalne: jeden fragment ze stałego polskiego szablonu. |
| embeddingi | `embed.py` | `E5Embedder` (domyślny) i `HashEmbedder` (testy) |
| baza | `db.py`, `database/` | Połączenie, schemat, wersjonowany upsert, log sprawdzeń, `scratch_db()` dla testów |
| wyszukiwanie | `search.py` | `Retriever`: FTS w Postgresie, gęste w NumPy, fuzja RRF, różnorodność, filtry |
| LLM | `llm.py` | Prompty, schematy Pydantic, klienci PCSS/OpenAI/Anthropic ze strukturalnym wyjściem, tokeny i koszt |
| weryfikacja | `verify.py` | `Verifier`: ekstrakcja → wyszukiwanie → werdykt → kontrola cytatów i notatki → log → `render_markdown` |
| na żywo | `live.py` | Gdy korpus nie rozstrzyga (`insufficient`): Google Fact Check, EUvsDisinfo (MCP `search`) i gov.pl na żywo przez `crawler.fetcher.Fetcher`, zapis przez `ingest_corpus`, ponowna weryfikacja |
| pomocnicze | `cli.py`, `config.py` (`.env`), `timeutil.py` (UTC ↔ Europe/Warsaw), `evaluation.py` (recall@k, MRR) | |

Użycie z kodu (np. przyszłe API):

```python
from rag.config import load_env
from rag.db import connect
from rag.embed import get_embedder
from rag.llm import make_llm
from rag.search import Retriever
from rag.verify import Verifier, render_markdown

load_env()                    # .env -> DATABASE_URL, klucze API (wołaj w punkcie wejścia)
res = Verifier(Retriever(connect(), get_embedder("e5")), make_llm()).verify("…")
print(render_markdown(res))   # albo res.to_dict()
```

## Wejście: korpus

`rag ingest` przyjmuje katalog crawlera (domyślnie `data/snapshot`, świeży crawl to `data/crawl`) albo plik korpusu JSON `{"sources": [...], "evidence": [...]}`.
Wzorcowy przykład wszystkich rodzajów rekordów: [`tests/fixtures/fake_corpus.json`](tests/fixtures/fake_corpus.json).

- Rekord `evidence` wymaga `source_id`, `upstream_id`, `kind`, `title` i `citation_url` (https, w domenie `publishing_domain` źródła). Zły rekord jest pomijany z ostrzeżeniem, a reszta ładuje się dalej.
- Artykuły (`article`, `report`, `factcheck`…) mają `sections: [{heading, text, page?}]` i są cytowane dosłownie.
- Rodzaje strukturalne (`warning_meteo`, `warning_hydro`, `weather_reading`, `air_quality`) mają typowane `fields` (wymagane pola w `chunking.REQUIRED_FIELDS`) i nie są cytowane dosłownie.
- `alert` (komunikaty RSO) to tekst cytowany dosłownie jak artykuł, ale z `valid_from`/`valid_to`, więc wygasły komunikat daje notatkę (`verify.TIMED_KINDS`).
- Źródła z `doc_type: weryfikacja` (sprawy EUvsDisinfo, weryfikacje z Google Fact Check) dostają `source_type: factcheck`. Zmienia się tylko wiersz źródła, więc rekordy zachowują hasz i nie są ponownie osadzane. Typ ustawia się przy najbliższym `ingest`.
- Import jest idempotentny. Wynik podaje akcje: `inserted`, `new_version`, `unchanged`, `known_version`, `invalid`. Dokumenty nieobecne w nowym pliku zostają w bazie.

## Opinie i znane narracje dezinformacyjne

- Ekstrakcja nie odrzuca narracji propagandowych jako „opinii”. Twierdzenie jest zawsze po polsku, a zapytania obejmują też język oryginału i angielski (baza EUvsDisinfo jest po angielsku).
- Twierdzenie `checkable=false` (opinia) jest szukane w całym korpusie, a do listy dochodzą najlepsze fragmenty ze źródeł `factcheck` (`OPINION_DISINFO_EXTRA`). Oficjalne źródła dają kontekst rdzenia faktycznego, np. przy „Polska lekceważy gwarancje bezpieczeństwa dla Ukrainy” obok trafia komunikat rządu o tych gwarancjach.
- Brak trafień daje `unverifiable` bez wywołania LLM. Jeśli model nie rozpozna udokumentowanej narracji, wynik to `unverifiable`. Cytowane są wtedy tylko oficjalne źródła, które model wskazał jako kontekst. Fragmenty `factcheck` bez cytatu obalającego nie są pokazywane, bo cytat samej narracji wyglądałby jak poparcie.
- `known_disinformation` (pole w `ClaimResult` i w API) oznacza, że twierdzenie powtarza narrację opisaną w bazie jako dezinformacja. Kod ustawia je tylko wtedy, gdy wśród zachowanych cytatów jest obalenie ze źródła `factcheck` z rolą `contradicts`. Wtedy etykiety `supports` na fragmentach tych źródeł zamieniają się w `context` (cytat narracji jej nie potwierdza), więc `true` spada do `insufficient`. Do wyniku trafia notatka „Znana narracja dezinformacyjna udokumentowana przez: …” z wydawcą z bazy.
- `strength` (pole w `ClaimResult` i w API) to siła podstawy werdyktu z jawnymi przesłankami w `reasons`. Liczy ją `assess_strength` z cytatów o roli wymaganej przez werdykt. Dwa niezależne źródła, w tym co najmniej jedno pierwotne (`official`, `structured`, `factcheck`), dają `strong`. Dwa źródła wtórne albo jedno pierwotne dają `moderate`, a jedno wtórne `weak`. Przedruk (`syndicated_from`) i kolejne teksty tego samego źródła liczą się jako jedno źródło. Ocena spada o poziom, gdy pomiar dotyczy innego okresu niż twierdzenie lub aktualizacja źródła się nie powiodła, i o kolejny, gdy jakieś źródło przeczy werdyktowi `true` albo `false`. `evidence_strength` z modelu (dopasowanie fragmentów do twierdzenia) może ją tylko obniżyć, ale nie poniżej `weak`. Bez werdyktu (`insufficient`, `unverifiable`) siła to `none`.
- Werdykt pozostaje ze zwykłego zbioru: `false`, gdy obalenie przeczy treści, `misleading` przy manipulacji, a `unverifiable` z flagą dla czystej oceny. `REQUIRED_ROLES` obowiązują jak zawsze.

## Konwencje SQL

- Cały SQL jest w `database/`: `schema.sql` (DDL, idempotentny, wykonywany przy każdym połączeniu) i `queries/<grupa>.sql`.
- Nowe zapytanie: blok `-- name: <nazwa>` w `queries/<grupa>.sql`, wywołanie `conn.execute(query("<grupa>.<nazwa>"), params)`.
- Wartości tylko jako placeholdery `%(nazwa)s`, nigdy przez f-string. Jedyny składany identyfikator (schemat testowy) przechodzi przez `psycopg.sql.Identifier`.
- Zmiana schematu wymaga podbicia `SCHEMA_VERSION` w `db.py` i kroku migracji w `connect`. Baza z nowszym schematem niż kod jest odrzucana.
- Czasy są zapisane jako tekst ISO 8601 w UTC i porównywane w Pythonie, nie w SQL.

## Embeddery

| `--embedder` | Kiedy |
| --- | --- |
| `e5` (domyślny) | Normalna praca: `intfloat/multilingual-e5-small` (prefiksy `query:`/`passage:`), CPU. Pierwsze użycie pobiera model. |
| `hash` | Tylko testy i praca offline: n-gramy znaków, bez semantyki. |
| inna nazwa | Dowolny model sentence-transformers (eksperymenty). |

Wektory są zapisane z nazwą modelu. Zapytanie innym embedderem kończy się błędem z prośbą o `rag reembed`. Ponowny `ingest` nie przelicza wektorów niezmienionych rekordów.

## LLM

- Dostawca: `--provider` albo `PRAWDOWID_PROVIDER` (`pcss` domyślnie, `openai`, `anthropic`). Model: `--model`, `PCSS_MODEL`, `OPENAI_MODEL`, `PRAWDOWID_MODEL`.
- `pcss` to modele PCSS (`https://llm.hpc.psnc.pl/v1`, zgodne z API OpenAI, ten sam klient i strukturalne wyjście co `openai`). Domyślnie `GLM-5.3-Flash`; tańszy `Bielik-11B-v3.0-Instruct` ma kontekst 33k. Koszt jest raportowany w PLN.
- Klucze, limit czasu i ceny do raportu kosztu: [`.env.example`](../../.env.example).
- Jedno sprawdzenie to 1 wywołanie na ekstrakcję i po 1 na każde twierdzenie. Podsumowanie (model, tokeny, koszt) trafia na stderr.
- Przy danych z przeszłości podaj `--now`, bo okna ważności ostrzeżeń liczą się od czasu zapytania.

## Wyszukiwanie na żywo (`live.py`)

- Kiedy: twierdzenie sprawdzalne z werdyktem `insufficient` po pierwszym przebiegu (brak trafień, nic rozstrzygającego albo werdykt obniżony za brak cytatu). Jedno wyszukiwanie na sprawdzenie, potem ponowna weryfikacja najwyżej 2 takich twierdzeń. Twierdzenie rozstrzygnięte z korpusu nie wywołuje sieci.
- Gdzie (tylko te, równolegle, limit `BUDGET_S` = 18 s): Google Fact Check Tools (`claims:search`, recenzje tylko z domen `TRUSTED_FACTCHECK_DOMAINS`), EUvsDisinfo (narzędzie MCP `search`, tylko euvsdisinfo.eu), gov.pl (`/api/data/search` jak strona wyszukiwarki portalu, potem 2 artykuły z www.gov.pl z profilem gov.pl crawlera).
- Jak: każde zapytanie przez `crawler.fetcher.Fetcher` (FaktBot z mailem kontaktowym, robots.txt, ≥3 s na host, wycofanie po 429/503/403, bez obchodzenia blokad). Wyniki to zwykłe wersjonowane rekordy (`live-factcheck-<domena>`, `live-euvsdisinfo`, `live-govpl-<jednostka>`); adres już zapisany przez crawler jest użyty ponownie. Linki dalej tylko z `citation_url`.
- Wynik: notatka „Wyszukano na żywo w zaufanych źródłach: …”, log wyszukiwania w `ClaimResult.live` (trafia do `checks`, API go nie pokazuje). Koszt: jedno dodatkowe wywołanie LLM na ponownie sprawdzane twierdzenie i ok. 9–13 s sieci.
- Wyłączenie: `PRAWDOWID_LIVE=0`. Testy (`test_live.py`) odtwarzają nagrane prawdziwe odpowiedzi z `tests/fixtures/live/` i nie łączą się z siecią.

## Testy

`uv run pytest backend/rag`. Testy DB biorą `TEST_DATABASE_URL` (albo `DATABASE_URL`) i każdy pracuje we własnym schemacie `scratch_<id>`. Bez bazy są pomijane.
Klienci OpenAI i PCSS są testowani na atrapie HTTP, bez klucza. `conftest.py` udostępnia `FIXTURES`.

## Ewaluacja

Oba skrypty ładują korpus do tymczasowego schematu w `DATABASE_URL` (nie w `TEST_DATABASE_URL`) i usuwają go po pracy.

```bash
uv run python scripts/eval_retrieval.py --show-misses            # BM25 vs dense vs hybryda, bezpłatne
uv run python scripts/eval_retrieval.py --corpus data/snapshot \
    --cases backend/rag/tests/fixtures/real_retrieval_cases.yaml  # na prawdziwym zrzucie
uv run python scripts/eval_verify.py --only v01,v07              # werdykty na żywym LLM, płatne
```

| Plik w `tests/fixtures/` | Do czego |
| --- | --- |
| `fake_corpus.json` | Sztuczny korpus (domena `.test`) dla testów i obu ewaluacji |
| `retrieval_cases.yaml` | Pytania z oczekiwanymi rekordami dla `fake_corpus.json` |
| `real_retrieval_cases.yaml` | To samo dla `data/snapshot/` |
| `verify_cases.yaml` | Twierdzenia z oczekiwanymi werdyktami i stałym czasem zapytania |
| `live/*.json` | Nagrane prawdziwe odpowiedzi Google Fact Check, EUvsDisinfo i gov.pl (bez kluczy) dla `test_live.py` |

Do zrobienia: przypadek „sprzeczne źródła” (scenariusz C konkursu) usunięto przy porządkach razem ze źródłami, na których stał. Trzeba go dodać ponownie na rekordach ostrzeżeń (`warning_meteo`/`warning_hydro`) w `fake_corpus.json` i `verify_cases.yaml`.
