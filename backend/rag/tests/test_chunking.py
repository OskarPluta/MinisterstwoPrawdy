from rag.chunking import MAX_WORDS, chunk_record, render_structured, split_section


def test_split_respects_max_words_and_paragraphs():
    par = " ".join(["słowo"] * 120)
    text = "\n\n".join([par] * 5)  # 600 words in 5 paragraphs
    chunks = split_section(text)
    assert len(chunks) >= 2
    assert all(len(c.split()) <= MAX_WORDS for c in chunks)
    # paragraphs are never cut in the middle
    assert all(len(c.split()) % 120 == 0 for c in chunks)


def test_long_paragraph_split_at_sentences():
    sent = " ".join(["wyraz"] * 49) + "."
    text = " ".join([sent] * 10)  # one 500-word paragraph
    chunks = split_section(text)
    assert len(chunks) >= 2
    assert all(c.endswith(".") for c in chunks)


def test_short_tail_is_merged():
    text = " ".join(["a"] * 280) + "\n\n" + " ".join(["b"] * 20)
    chunks = split_section(text)
    assert len(chunks) == 1


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
