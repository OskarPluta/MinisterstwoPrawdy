# database

Pakiet `database`: cały SQL Prawdowida. Schemat PostgreSQL i nazwane zapytania, z których korzystają `rag` (połączenie i migracje w `rag/db.py`), panel administracyjny ([`admin-backend/`](../admin-backend/)) i skrypty ewaluacyjne. Sam nie łączy się z bazą.

| Plik | Co zawiera |
| --- | --- |
| [`src/database/schema.sql`](src/database/schema.sql) | DDL, idempotentny, wykonywany przy każdym połączeniu (`rag.db.connect`) |
| [`src/database/queries/<grupa>.sql`](src/database/queries/) | Nazwane zapytania, każde po linii `-- name: <nazwa>` |
| [`src/database/__init__.py`](src/database/__init__.py) | `schema()` i `query("<grupa>.<nazwa>")`: tekst SQL z plików |

```python
from database import query

conn.execute(query("evidence.insert"), params)
```

- Wartości tylko jako placeholdery `%(nazwa)s`, nigdy przez f-string.
- Zmiana `schema.sql` wymaga podbicia `SCHEMA_VERSION` w [`rag/db.py`](../backend/rag/src/rag/db.py) i kroku migracji w `connect`. Więcej: [konwencje SQL w README `rag`](../backend/rag/README.md#konwencje-sql).
- Testy schematu i zapytań są w `backend/rag/tests/test_db.py`.
