"""Jev as the judge (rag/jev.py): request shape, parsing, and the verifier on Jev's answers.
No network: httpx.MockTransport plays the Jev endpoint."""

from __future__ import annotations

import json
from typing import Any

import httpx
from test_verify import DELAY, MI_NEW, NEW_RULES, NOW, UTK, FakeLLM, _pid_for, add_records, claim, verdict

from rag import jev as jev_mod
from rag.jev import Jev, build_request, make_jev
from rag.verify import Verifier, render_markdown


def answers(verdict_code: str, probs: dict[str, float], roles: dict[str, str], **noul: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "verdict": {"type": "choice", "choice": verdict_code, "confidence": probs[verdict_code], "probabilities": probs},
    }
    for p, role in roles.items():
        out[f"role_{p}"] = {"type": "choice", "choice": role, "confidence": 0.9,
                            "probabilities": {role: 0.9, **({} if role == "unrelated" else {"unrelated": 0.1})}}
    for k, v in noul.items():
        out[k] = {"type": "noul", "noul": v}
    return {"model": "jev-1.14.0", "answers": out, "usage": {"input_tokens": 1000, "output_tokens": 20}}


def jev_with(reply: Any, seen: list[dict] | None = None) -> Jev:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        assert request.headers["authorization"] == "Bearer k"
        r = reply(body) if callable(reply) else reply
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)
    return Jev("k", "https://jev.test/v1/systemone", "jev-latest", transport=httpx.MockTransport(handle))


def llm_with(judge: Jev, claims: list, policy=None) -> FakeLLM:
    llm = FakeLLM(claims, policy)
    llm.judge = judge  # type: ignore[attr-defined]
    return llm


def test_request_asks_a_role_for_every_passage(retriever):
    c = claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne", ["stan wody Wisła Sandomierz"])
    hits = Verifier(retriever, FakeLLM(), record=False).retrieve(c, NOW)
    req = build_request(c, hits, {}, NOW, "jev-latest")
    assert req["model"] == "jev-latest"
    q = req["questions"]
    assert set(q["verdict"]["criteria"]) == {"true", "false", "misleading", "insufficient"}
    assert {f"role_P{h.passage_id}" for h in hits} <= set(q)
    p0 = f"P{hits[0].passage_id}"
    assert p0 in q[f"role_{p0}"]["instructions"], "the question key is not sent to Jev"
    assert [e["id"] for e in req["state"]["evidence"]] == [f"P{h.passage_id}" for h in hits]
    opinion = build_request(c.model_copy(update={"checkable": False}), hits, {}, NOW, "m")
    assert "unverifiable" in opinion["questions"]["verdict"]["criteria"]


def test_jev_decides_and_keeps_only_used_passages(retriever):
    c = claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne", ["stan wody Wisła Sandomierz"])
    hits = Verifier(retriever, FakeLLM(), record=False).retrieve(c, NOW)
    used, ctx, *rest = [f"P{h.passage_id}" for h in hits]
    roles = {used: "supports", ctx: "context", **{p: "unrelated" for p in rest}}
    reply = answers("true", {"true": 0.92, "false": 0.03, "misleading": 0.03, "insufficient": 0.02}, roles)
    judge = jev_with(reply)
    res = Verifier(retriever, llm_with(judge, [c]), record=False, live=False).verify("x", NOW)
    cr = res.claims[0]
    assert cr.verdict == "true"
    assert [(x.passage_id, x.role) for x in cr.citations] == [(used, "supports"), (ctx, "context")]
    assert cr.explanation.startswith("Źródła potwierdzają twierdzenie: ") and "(źródło 1)" in cr.explanation
    assert "Kontekst: " in cr.explanation
    assert cr.strength and cr.strength.confidence == 0.92 and cr.strength.level_pl == "92%"
    assert cr.strength.level == "strong" and cr.strength.reasons[0] == "Pewność oceny (Jev): 92%."
    assert "potwierdzone 92%" in cr.strength.reasons[1]
    assert "(ocena modelu)" not in " ".join(cr.strength.reasons)
    assert judge.usage.calls == 1 and judge.usage.input_tokens == 1000
    assert "**Siła dowodów:** 92%" in render_markdown(res)


def test_newer_side_wins_over_jev_verdict(retriever, embedder):
    add_records(retriever, embedder, NEW_RULES)
    v = Verifier(retriever, FakeLLM(), record=False, live=False)
    old, new = _pid_for(v, DELAY, UTK), _pid_for(v, DELAY, MI_NEW)
    probs = {"true": 0.55, "false": 0.35, "misleading": 0.05, "insufficient": 0.05}
    v.llm = llm_with(jev_with(lambda body: answers(
        "true", probs, {p[5:]: "unrelated" for p in body["questions"] if p.startswith("role_")}
        | {old: "supports", new: "contradicts"})), [DELAY])
    cr = v.verify("x", NOW).claims[0]
    assert cr.verdict == "false", "the newer source decides: one verdict, not both"
    assert cr.explanation.startswith("Źródła się różnią, a rozstrzygają nowsze dane. Źródła przeczą twierdzeniu: ")
    assert cr.strength and cr.strength.confidence == 0.35
    assert any("werdykt zmienił kod" in r for r in cr.strength.reasons)
    assert "Brak źródeł sprzecznych z werdyktem." not in cr.strength.reasons


def test_jev_outage_falls_back_to_the_llm(retriever):
    c = claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne", ["stan wody Wisła Sandomierz"])
    judge = jev_with(httpx.Response(503, text="down"))
    llm = llm_with(judge, [c], lambda cl, xml: verdict("insufficient"))
    cr = Verifier(retriever, llm, record=False, live=False).verify("x", NOW).claims[0]
    assert cr.verdict == "insufficient" and llm.seen_xml, "the LLM decided"
    assert cr.strength and cr.strength.confidence is None


def test_make_jev_picks_the_provider_from_the_key(monkeypatch):
    for var, *_ in jev_mod.PROVIDERS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.setenv("PRAWDOWID_JEV", "1")
    assert make_jev() is None
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "v")
    j = make_jev()
    assert j and j.url.startswith("https://ai-gateway.vercel.sh/") and j.model == "typesafe-ai/jev"
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    j = make_jev()
    assert j and j.url == "https://openrouter.ai/api/v1/systemone"
    monkeypatch.setenv("PRAWDOWID_JEV", "0")
    assert make_jev() is None
