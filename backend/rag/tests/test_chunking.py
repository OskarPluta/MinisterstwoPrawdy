from rag.chunking import (
    MAX_TOKENS,
    MIN_TAIL_TOKENS,
    chunk_record,
    embedding_text,
    estimate_tokens,
    render_structured,
    split_section,
)


def wc(s: str) -> int:  # 1 token per word: easy budgets in tests
    return len(s.split())


def test_split_respects_budget_and_paragraphs():
    par = " ".join(["słowo"] * 120)
    text = "\n\n".join([par] * 5)  # 600 "tokens" in 5 paragraphs
    chunks = split_section(text, wc, max_tokens=300)
    assert len(chunks) >= 2
    assert all(wc(c) <= 300 for c in chunks)
    # paragraphs are never cut in the middle
    assert all(wc(c) % 120 == 0 for c in chunks)


def test_long_paragraph_split_at_sentences():
    sent = " ".join(["wyraz"] * 49) + "."
    text = " ".join([sent] * 10)  # one 500-word paragraph
    chunks = split_section(text, wc, max_tokens=300)
    assert len(chunks) >= 2
    assert all(c.endswith(".") for c in chunks)
    assert all(wc(c) <= 300 for c in chunks)


def test_sentence_over_budget_is_cut_between_words():
    text = " ".join(["x"] * 1000)  # no punctuation at all
    chunks = split_section(text, wc, max_tokens=300, min_tail=0)
    assert [wc(c) for c in chunks] == [300, 300, 300, 100]
    assert " ".join(chunks) == text


def test_short_tail_is_merged():
    text = " ".join(["a"] * 280) + "\n\n" + " ".join(["b"] * 20)
    chunks = split_section(text, wc, max_tokens=290, min_tail=60)
    assert len(chunks) == 1


def test_default_budget_uses_estimate_and_stays_under_512():
    # Without a tokenizer the estimate is conservative: a long Polish section ends up in
    # passages of at most MAX_TOKENS (+ a merged tail), well below e5's 512.
    sent = "Wojewódzki inspektorat przeprowadził kontrolę przestrzegania przepisów sanitarnych."
    text = "\n\n".join([" ".join([sent] * 6)] * 12)
    chunks = split_section(text)
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= MAX_TOKENS + MIN_TAIL_TOKENS for c in chunks)
    assert estimate_tokens(sent) > len(sent.split())  # Polish: more tokens than words


def test_chunk_record_budget_includes_title_and_heading():
    title = " ".join(["tytuł"] * 50)
    rec = {
        "kind": "article", "upstream_id": "X", "title": title,
        "sections": [{"heading": "nagłówek", "text": "\n\n".join([" ".join(["w"] * 100)] * 8)}],
    }
    seen: list[str] = []

    def count(text: str) -> int:
        seen.append(text)
        return wc(text)

    ps = chunk_record(rec, count)
    assert len(ps) >= 3
    assert all(wc(embedding_text(title, p.heading, p.text)) <= MAX_TOKENS + MIN_TAIL_TOKENS for p in ps)
    assert all(wc(embedding_text(title, p.heading, p.text)) <= MAX_TOKENS for p in ps[:-1])
    assert seen and all(s.startswith(title + "\nnagłówek") for s in seen)  # counter sees the embedded text
    assert "\n\n".join(p.text for p in ps) == rec["sections"][0]["text"]  # nothing lost or altered


def test_article_keeps_headings_and_verbatim_excerpt(corpus):
    rec = next(e for e in corpus["evidence"] if e["upstream_id"] == "MI-2026-11-12-ROZKLAD-JAZDY")
    ps = chunk_record(rec)
    assert {p.heading for p in ps} == {s["heading"] for s in rec["sections"]}
    assert all(p.excerpt == p.text for p in ps)


def test_structured_template_uses_local_time_and_fields(corpus):
    rec = [e for e in corpus["evidence"] if e["upstream_id"] == "OST-2026-1121-MAZ-WIATR"][-1]
    s = render_structured(rec)
    assert s.startswith("Ostrzeżenie meteorologiczne IMGW-PIB: silny wiatr. Stopień: 2.")
    assert "powiat legionowski" in s
    assert "21.11.2026 11:00 – 21.11.2026 20:00" in s  # UTC 10:00-19:00 -> Warsaw (CET)
    ps = chunk_record(rec)
    assert len(ps) == 1 and ps[0].excerpt is None  # rendered, not verbatim


def test_structured_templates_fit_e5_budget(corpus):
    # Structured records are one passage each and never split; keep them well below 512.
    for rec in corpus["evidence"]:
        if rec["kind"] in ("warning_meteo", "warning_hydro", "weather_reading", "air_quality"):
            (p,) = chunk_record(rec)
            assert estimate_tokens(embedding_text(rec["title"], None, p.text)) <= MAX_TOKENS
