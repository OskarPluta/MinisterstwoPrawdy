import copy

from rag.db import SCHEMA_VERSION, connect
from rag.ingest import ingest_corpus, load_corpus
from rag.reprints import MIN_SHINGLES, is_reprint_pair, minhash, shingles
from rag.search import Retriever
from rag.verify import origin

WIRE = (
    "Wojewoda mazowiecki poinformował w poniedziałek, że od przyszłego tygodnia na drogach "
    "powiatu otwockiego zostaną wprowadzone czasowe ograniczenia ruchu ciężarówek. Powodem są "
    "prace przy przebudowie mostu na rzece Świder, które potrwają do końca marca. Kierowcy "
    "pojazdów o masie powyżej dwunastu ton będą kierowani objazdem przez Karczew i Celestynów. "
    "Urząd zapowiedział, że oznakowanie objazdu pojawi się w piątek, a policja będzie kontrolować "
    "przestrzeganie zakazu. Mieszkańcy mogą zgłaszać uwagi do starostwa powiatowego przez cały "
    "okres prowadzenia robót."
)
OTHER = (
    "Sanepid w Lublinie przypomina, że w okresie jesiennym rośnie liczba zatruć grzybami. Lekarze "
    "apelują, aby nie zbierać okazów, których nie potrafimy z całą pewnością rozpoznać, i nie dawać "
    "potraw z grzybów małym dzieciom. W razie objawów takich jak nudności czy bóle brzucha należy "
    "natychmiast zgłosić się do szpitala i zabrać ze sobą resztki potrawy do badania."
)

SOURCES = [
    {"source_id": sid, "publisher": sid, "publishing_domain": f"{sid}.test", "source_type": st,
     "citation_url": f"https://{sid}.test/"}
    for sid, st in [("agencja", "secondary"), ("portal_a", "secondary"), ("portal_b", "secondary"),
                    ("urzad", "official"), ("imgw", "structured")]
]


def rec(source, uid, text, published, kind="article"):
    return {
        "source_id": source, "upstream_id": uid, "kind": kind, "title": f"Tytuł {uid}",
        "citation_url": f"https://{source}.test/{uid}", "published_at": published,
        "sections": [{"heading": None, "text": text}],
    }


def links(conn):
    rows = conn.execute(
        "SELECT e.source_id || ':' || e.upstream_id AS k, r.reprint_of FROM reprint_signatures r "
        "JOIN evidence e USING (evidence_id) WHERE e.is_current"
    ).fetchall()
    return {r["k"]: r["reprint_of"] for r in rows}


def ingest(conn, embedder, *records):
    return ingest_corpus(conn, {"sources": SOURCES, "evidence": list(records)}, embedder)


ORIGINAL = rec("agencja", "A1", WIRE, "2026-11-20T08:00:00Z")
COPY_A = rec("portal_a", "PA1", "Jak podała agencja: " + WIRE + " Fot. archiwum.", "2026-11-20T10:00:00Z")
SHORT_B = rec("portal_b", "PB1", WIRE[: len(WIRE) // 2], "2026-11-20T12:00:00Z")


def test_pair_rules():
    a, b = shingles(WIRE), shingles(OTHER)
    assert len(a) >= MIN_SHINGLES and not a & b
    assert is_reprint_pair(a, shingles("Ze źródeł: " + WIRE), a_first=True)
    half = shingles(WIRE[: len(WIRE) // 2])
    assert is_reprint_pair(a, half, a_first=True)  # a later shortened copy
    assert not is_reprint_pair(half, a, a_first=True)  # an earlier short text quoted in a longer one
    assert not is_reprint_pair(a, b, a_first=True)
    tiny = shingles("Uwaga, silny wiatr w powiecie otwockim do wieczora.")
    assert not is_reprint_pair(tiny, tiny, a_first=True)  # too short to tell a reprint from a template
    assert (minhash(a) == minhash(shingles(WIRE))).all()  # deterministic


def test_reprints_point_to_earliest_regardless_of_order(new_db, embedder):
    c1, c2 = new_db(), new_db()
    ingest(c1, embedder, ORIGINAL, COPY_A, SHORT_B, rec("urzad", "U1", OTHER, "2026-11-19T08:00:00Z"))
    # Copies first, the original last and in separate runs (incremental detection).
    ingest(c2, embedder, SHORT_B)
    ingest(c2, embedder, COPY_A, rec("urzad", "U1", OTHER, "2026-11-19T08:00:00Z"))
    stats = ingest(c2, embedder, ORIGINAL)
    assert stats["reprint_links_changed"] == 2  # the short copy re-pointed, the longer one linked
    expected = {"agencja:A1": None, "portal_a:PA1": "agencja:A1", "portal_b:PB1": "agencja:A1",
                "urzad:U1": None}
    assert links(c1) == links(c2) == expected

    again = ingest(c2, embedder, ORIGINAL, COPY_A, SHORT_B)  # idempotent
    assert "reprint_links_changed" not in again and links(c2) == expected


def test_detected_reprint_counts_as_original_source(new_db, embedder):
    c = new_db()
    ingest(c, embedder, ORIGINAL, COPY_A, SHORT_B)
    r = Retriever(c, embedder)
    hits = r.hybrid("ograniczenia ruchu ciężarówek most Świder", shortlist=8, per_group=3)
    assert {h.meta["source_id"] for h in hits} == {"agencja", "portal_a", "portal_b"}
    assert {origin(h) for h in hits} == {"agencja"}
    # By default the cluster shares the per-group limit, one passage per article.
    default = r.hybrid("ograniczenia ruchu ciężarówek most Świder", shortlist=8)
    assert len([h for h in default if origin(h) == "agencja"]) == 2
    assert len({h.evidence_id for h in default}) == len(default)


def test_same_source_explicit_and_structured_are_not_linked(new_db, embedder):
    c = new_db()
    explicit = {**rec("portal_b", "PB2", WIRE, "2026-11-20T12:00:00Z"), "syndicated_from": "urzad:X"}
    ingest(c, embedder, ORIGINAL, rec("agencja", "A2", WIRE + " Aktualizacja.", "2026-11-21T08:00:00Z"),
           explicit, rec("imgw", "W1", WIRE, "2026-11-21T09:00:00Z", kind="alert"))
    assert links(c) == {"agencja:A1": None, "agencja:A2": None}  # explicit/structured not signed
    hits = Retriever(c, embedder).hybrid("most Świder objazd Karczew", shortlist=8, per_group=3)
    assert {h.meta["syndicated_from"] for h in hits if h.meta["source_id"] == "portal_b"} == {"urzad:X"}


def test_new_version_that_diverges_drops_the_link(new_db, embedder):
    c = new_db()
    ingest(c, embedder, ORIGINAL, COPY_A)
    assert links(c)["portal_a:PA1"] == "agencja:A1"
    ingest(c, embedder, {**COPY_A, "sections": [{"heading": None, "text": OTHER}]})
    assert links(c) == {"agencja:A1": None, "portal_a:PA1": None}
    # The original's own amendment re-forms the cluster around the new version.
    ingest(c, embedder, {**COPY_A, "sections": [{"heading": None, "text": WIRE}]},
           {**ORIGINAL, "title": "Zmieniony tytuł"})
    assert links(c) == {"agencja:A1": None, "portal_a:PA1": "agencja:A1"}


def test_fixture_reprint_is_detected_without_explicit_field(new_db, corpus, embedder):
    plain = copy.deepcopy(corpus)
    for e in plain["evidence"]:
        e.pop("syndicated_from", None)
    c = new_db()
    ingest_corpus(c, plain, embedder)
    found = {k: v for k, v in links(c).items() if v}
    assert found == {"portal:PORTAL-2026-11-12-ROZKLAD": "mi_gov:MI-2026-11-12-ROZKLAD-JAZDY"}


def test_upgrade_from_v3_backfills(new_db, database_url, embedder):
    c = new_db()
    ingest(c, embedder, ORIGINAL, COPY_A)
    c.execute("DELETE FROM reprint_signatures")
    c.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
    name = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    upgraded = connect(database_url, db_schema=name)
    try:
        assert links(upgraded) == {"agencja:A1": None, "portal_a:PA1": "agencja:A1"}
        row = upgraded.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert row["value"] == str(SCHEMA_VERSION)
    finally:
        upgraded.close()


def test_fixture_file_has_no_unintended_reprints(conn):
    # The default fixture keeps its explicit link and gains none.
    assert not any(links(conn).values())


def test_purged_original_unlinks_its_reprint(new_db, embedder):
    from rag.db import purge

    c = new_db()
    ingest(c, embedder, ORIGINAL, COPY_A)
    purge(c, sources=["agencja"])
    assert links(c) == {"portal_a:PA1": None}
