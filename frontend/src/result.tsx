// CheckResult → UI. Model-written text (explanation, claim, message) is rendered as plain text only;
// links come only from citation.url, which the backend takes from the database.
import { useState, type ReactNode } from "react";
import type { CheckResult, CheckStatus, ClaimResult, InputSummary, Strength, Verdict } from "./api";
import { Button, Notice, StatusPanel, type Tone } from "./design/components";
import { CitationList, MaterialList } from "./design/extra";

/** Non-ok statuses: one Notice; the form stays filled in so the person can fix the input. */
export const STATUS_NOTICE: Record<Exclude<CheckStatus, "ok">, { tone: Tone; title: string }> = {
  needs_clarification: { tone: "warning", title: "Doprecyzuj pytanie" },
  too_long: { tone: "warning", title: "Uwaga" },
  out_of_scope: { tone: "info", title: "Informacja" },
  // A technical limitation (blocked page, unreadable file), not a verdict: never red.
  unreadable: { tone: "warning", title: "Nie udało się odczytać materiału" },
  error: { tone: "danger", title: "Błąd" },
};

/** `insufficient` is never red: no evidence does not mean false. */
export const VERDICT_VIEW: Record<Verdict, { tone: Tone; title: string }> = {
  true: { tone: "success", title: "Źródła potwierdzają to twierdzenie" },
  false: { tone: "danger", title: "Źródła przeczą temu twierdzeniu" },
  misleading: { tone: "warning", title: "To twierdzenie wprowadza w błąd" },
  insufficient: { tone: "info", title: "Nie znaleźliśmy wystarczających dowodów" },
  unverifiable: { tone: "info", title: "To opinia, a nie fakt do sprawdzenia" },
  error: { tone: "warning", title: "Nie udało się sprawdzić tego twierdzenia" },
};

/** An opinion that repeats a narrative a fact-check database documents as disinformation. */
const KNOWN_NARRATIVE_OPINION = { tone: "warning", title: "Ta opinia powtarza znaną narrację dezinformacyjną" } as const;
const KNOWN_NARRATIVE = "Znana narracja dezinformacyjna";

const CONFLICT = "Źródła podają różne informacje. Szczegóły znajdziesz w cytatach poniżej.";
const INJECTION =
  "Twoja wiadomość zawierała polecenia skierowane do systemu. Zignorowaliśmy je i sprawdziliśmy tylko twierdzenia.";

export const RESULT_HEADING_ID = "wynik";

const STRENGTH_LABEL = "Siła dowodów";

/** Premises behind the verdict's strength. Neutral styling: strength is not a verdict colour. */
function StrengthBox({ s }: { s: Strength }) {
  return (
    <section className="app-strength" aria-label={STRENGTH_LABEL}>
      <p className="app-strength-title">
        {STRENGTH_LABEL}: <strong data-level={s.level}>{s.level_pl}</strong>
      </p>
      <ul>
        {s.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </section>
  );
}

function originLabel(c: ClaimResult, inputs: InputSummary[]): string | null {
  const m = c.origin && c.origin !== "user" ? inputs.find((i) => i.id === c.origin) : undefined;
  return m ? `materiał ${m.id} (${m.kind_pl})` : null;
}

function ClaimPanel({ c, index, total, inputs }: { c: ClaimResult; index: number; total: number; inputs: InputSummary[] }) {
  const view =
    c.known_disinformation && c.verdict === "unverifiable"
      ? KNOWN_NARRATIVE_OPINION
      : (VERDICT_VIEW[c.verdict] ?? VERDICT_VIEW.error);
  const details: { label: string; value: ReactNode }[] = [{ label: "Twierdzenie", value: c.claim }];
  if (total > 1) details.unshift({ label: "Numer", value: `${index + 1} z ${total}` });
  const origin = originLabel(c, inputs);
  if (origin) details.push({ label: "Pochodzi z", value: origin });
  if (c.known_disinformation) details.push({ label: KNOWN_NARRATIVE, value: "Tak, udokumentowana w bazie weryfikacji" });
  if (c.strength) details.push({ label: STRENGTH_LABEL, value: c.strength.level_pl });
  details.push({ label: "Liczba źródeł", value: String(c.citations.length) });
  return (
    <StatusPanel
      tone={view.tone}
      kicker={c.verdict_pl}
      title={view.title}
      details={details}
      titleId={index === 0 ? RESULT_HEADING_ID : undefined}
      className="app-claim"
    >
      <p className="app-explanation">{c.explanation}</p>
      {c.conflict && <p className="app-conflict">{CONFLICT}</p>}
      {c.strength && <StrengthBox s={c.strength} />}
      {c.notes.length > 0 && (
        <Notice tone="warning" title="Zwróć uwagę">
          {c.notes.length === 1 ? (
            <p>{c.notes[0]}</p>
          ) : (
            <ul>
              {c.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </Notice>
      )}
      <CitationList citations={c.citations} />
    </StatusPanel>
  );
}

/** Plain-text copy in the layout of render_markdown() in verify.py, without Markdown syntax. */
export function resultAsText(res: CheckResult): string {
  const out: string[] = [];
  if (res.message) out.push(res.message, "");
  for (const m of res.inputs ?? []) {
    out.push(`Materiał ${m.id}: ${m.kind_pl}, ${m.label}${m.date_label ? ` · ${m.date_label}` : ""}`);
    for (const n of m.notes) out.push(`Informacja: ${n}`);
    for (const n of m.limitations) out.push(`Ograniczenie: ${n}`);
  }
  if (res.inputs?.length) out.push("");
  for (const c of res.claims) {
    const origin = originLabel(c, res.inputs ?? []);
    if (origin) out.push(`Pochodzi z: ${origin}`);
    out.push(`Twierdzenie: ${c.claim}`, `Ocena: ${c.verdict_pl}`);
    if (c.strength) {
      out.push(`${STRENGTH_LABEL}: ${c.strength.level_pl}`);
      for (const r of c.strength.reasons) out.push(`- ${r}`);
    }
    if (c.known_disinformation) out.push(KNOWN_NARRATIVE);
    out.push(c.explanation);
    if (c.conflict) out.push(CONFLICT);
    for (const n of c.notes) out.push(`Uwaga: ${n}`);
    if (c.citations.length) {
      out.push("Źródła:");
      for (const s of c.citations) {
        const role = { supports: "potwierdza", contradicts: "przeczy", context: "kontekst" }[s.role];
        out.push(`${s.n}. ${s.title} · ${s.publisher}, ${s.date_label} (${role})${s.url ? `\n   ${s.url}` : ""}`);
        out.push(s.is_verbatim ? `   „${s.text}”` : `   Dane ze źródła: ${s.text}`);
      }
    }
    out.push("");
  }
  return out.join("\n").trim();
}

export function ResultView({ res, onReset }: { res: CheckResult; onReset: () => void }) {
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(resultAsText(res));
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
  }

  return (
    <div className="app-result">
      <h2 className="mp-sr">Wynik sprawdzenia</h2>
      {res.message && <Notice tone="info">{res.message}</Notice>}
      {res.injection_detected && (
        <Notice tone="info" title="Zignorowane polecenia">
          {INJECTION}
        </Notice>
      )}
      <MaterialList inputs={res.inputs ?? []} />
      {res.claims.map((c, i) => (
        <ClaimPanel key={i} c={c} index={i} total={res.claims.length} inputs={res.inputs ?? []} />
      ))}
      <div className="app-result-actions">
        <Button variant="secondary" icon="file-text" iconPosition="start" onClick={copy}>
          Kopiuj wynik
        </Button>
        <Button variant="link" onClick={onReset}>
          Sprawdź inną wiadomość
        </Button>
      </div>
      <p className="app-copy-state" role="status">
        {copied === "ok" ? "Skopiowano wynik do schowka." : copied === "fail" ? "Nie udało się skopiować. Zaznacz tekst ręcznie." : ""}
      </p>
    </div>
  );
}
