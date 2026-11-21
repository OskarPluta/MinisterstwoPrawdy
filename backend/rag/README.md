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
| LLM | `llm.py` | Prompty, schematy Pydantic, klienci OpenAI/Anthropic ze strukturalnym wyjściem, tokeny i koszt |
| weryfikacja | `verify.py` | `Verifier`: ekstrakcja → wyszukiwanie → werdykt → kontrola cytatów i notatki → log → `render_markdown` |
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
- Import jest idempotentny. Wynik podaje akcje: `inserted`, `new_version`, `unchanged`, `known_version`, `invalid`. Dokumenty nieobecne w nowym pliku zostają w bazie.

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

- Dostawca: `--provider` albo `PRAWDOWID_PROVIDER` (`openai` domyślnie, `anthropic`). Model: `--model`, `OPENAI_MODEL`, `PRAWDOWID_MODEL`.
- Klucze, limit czasu i ceny do raportu kosztu: [`.env.example`](../../.env.example).
- Jedno sprawdzenie to 1 wywołanie na ekstrakcję i po 1 na każde twierdzenie. Podsumowanie (model, tokeny, koszt) trafia na stderr.
- Przy danych z przeszłości podaj `--now`, bo okna ważności ostrzeżeń liczą się od czasu zapytania.

## Testy

`uv run pytest backend/rag`. Testy DB biorą `TEST_DATABASE_URL` (albo `DATABASE_URL`) i każdy pracuje we własnym schemacie `scratch_<id>`. Bez bazy są pomijane.
Klient OpenAI jest testowany na atrapie HTTP, bez klucza. `conftest.py` udostępnia `FIXTURES`.

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

Do zrobienia: przypadek „sprzeczne źródła” (scenariusz C konkursu) usunięto przy porządkach razem ze źródłami, na których stał. Trzeba go dodać ponownie na rekordach ostrzeżeń (`warning_meteo`/`warning_hydro`) w `fake_corpus.json` i `verify_cases.yaml`.
