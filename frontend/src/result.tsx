// CheckResult → UI. Model-written text (explanation, claim, message) is rendered as plain text only;
// links come only from citation.url, which the backend takes from the database.
import { useState, type ReactNode } from "react";
import {
  ApiError,
  deleteCheck,
  formatDate,
  MSG_OUTAGE,
  shareCheck,
  unshareCheck,
  type CheckResult,
  type CheckStatus,
  type ClaimResult,
  type ShareLink,
  type Strength,
  type Verdict,
} from "./api";
import { Button, Notice, StatusPanel, TextField, type Tone } from "./design/components";
import { CitationList } from "./design/extra";
import { downloadText, exportFilename, resultAsCsv, resultAsJson } from "./export";

/** Non-ok statuses: one Notice; the form stays filled in so the person can fix the input. */
export const STATUS_NOTICE: Record<Exclude<CheckStatus, "ok">, { tone: Tone; title: string }> = {
  needs_clarification: { tone: "warning", title: "Doprecyzuj pytanie" },
  too_long: { tone: "warning", title: "Uwaga" },
  out_of_scope: { tone: "info", title: "Informacja" },
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

function ClaimPanel({ c, index, total }: { c: ClaimResult; index: number; total: number }) {
  const view =
    c.known_disinformation && c.verdict === "unverifiable"
      ? KNOWN_NARRATIVE_OPINION
      : (VERDICT_VIEW[c.verdict] ?? VERDICT_VIEW.error);
  const details: { label: string; value: ReactNode }[] = [{ label: "Twierdzenie", value: c.claim }];
  if (total > 1) details.unshift({ label: "Numer", value: `${index + 1} z ${total}` });
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
  for (const c of res.claims) {
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
        if (s.attribution) out.push(`   ${s.attribution}`);
      }
    }
    out.push("");
  }
  return out.join("\n").trim();
}

type Async = { kind: "idle" | "busy" | "done" } | { kind: "fail"; message: string };

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : MSG_OUTAGE;
}

/** Publish / withdraw the read-only page /wynik/<share_id> (a server-side copy, never editable). */
function ShareBox({ checkId, erased }: { checkId: string; erased: boolean }) {
  const [link, setLink] = useState<ShareLink | null>(null);
  const [state, setState] = useState<Async>({ kind: "idle" });
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");
  const url = link ? window.location.origin + link.path : "";

  async function share() {
    if (state.kind === "busy") return;
    setState({ kind: "busy" });
    try {
      setLink(await shareCheck(checkId));
      setState({ kind: "done" });
    } catch (err) {
      setState({ kind: "fail", message: errorText(err) });
    }
  }

  async function unshare() {
    if (state.kind === "busy") return;
    setState({ kind: "busy" });
    try {
      await unshareCheck(checkId);
      setLink(null);
      setCopied("idle");
      setState({ kind: "idle" });
    } catch (err) {
      setState({ kind: "fail", message: errorText(err) });
    }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
  }

  if (erased) return null;
  return (
    <div className="app-share">
      <h3 className="app-share-title">Udostępnij wynik</h3>
      <p>
        Każdy, kto dostanie link, zobaczy ten wynik w wersji tylko do odczytu. Link pokazuje wynik zapisany na serwerze w chwili sprawdzenia, więc
        nikt nie może go zmienić. Dane osobowe w treści są ukryte.
      </p>
      {link ? (
        <>
          <TextField label="Link do wyniku" value={url} readOnly onFocus={(e) => e.currentTarget.select()} width="full" />
          <div className="app-result-actions">
            <Button variant="secondary" icon="file-text" iconPosition="start" onClick={copyLink}>
              Kopiuj link
            </Button>
            <a className="mp-link" href={link.path} target="_blank" rel="noopener noreferrer">
              Otwórz udostępnioną stronę
              <span className="mp-sr"> (otwiera się w nowej karcie)</span>
            </a>
            <Button variant="link" onClick={unshare} aria-disabled={state.kind === "busy" || undefined}>
              Wyłącz udostępnianie
            </Button>
          </div>
          {link.expires_at && <p className="app-share-note">Link działa do {formatDate(link.expires_at)}.</p>}
        </>
      ) : (
        <Button variant="secondary" onClick={share} aria-disabled={state.kind === "busy" || undefined}>
          Utwórz link do wyniku
        </Button>
      )}
      <p className="app-copy-state" role="status">
        {state.kind === "fail"
          ? state.message
          : state.kind === "busy"
            ? "Chwileczkę…"
            : copied === "ok"
              ? "Skopiowano link do schowka."
              : copied === "fail"
                ? "Nie udało się skopiować. Zaznacz link ręcznie."
                : ""}
      </p>
    </div>
  );
}

/** `readOnly`: a shared result (/wynik/<id>) – no reset, delete or share controls.
 *  `checkedAt`: UTC ISO time of the check for the downloaded file (default: when the result was shown). */
export function ResultView({
  res,
  onReset,
  readOnly = false,
  checkedAt,
}: {
  res: CheckResult;
  onReset?: () => void;
  readOnly?: boolean;
  checkedAt?: string | null;
}) {
  // One status line for copy and download, announced by screen readers.
  const [actionState, setActionState] = useState("");
  const [erase, setErase] = useState<Async>({ kind: "idle" });
  const [shownAt] = useState(() => new Date().toISOString());

  async function eraseLog() {
    if (erase.kind === "busy" || erase.kind === "done") return;
    setErase({ kind: "busy" });
    try {
      await deleteCheck(res.check_id);
      setErase({ kind: "done" });
    } catch (err) {
      setErase({ kind: "fail", message: errorText(err) });
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(resultAsText(res));
      setActionState("Skopiowano wynik do schowka.");
    } catch {
      setActionState("Nie udało się skopiować. Zaznacz tekst ręcznie.");
    }
  }

  /** Built from the displayed result in the browser; the check_id (deletion key) is never written. */
  function download(ext: "json" | "csv") {
    const ctx = { checkedAt: checkedAt ?? shownAt, shared: readOnly ? true : null };
    const name = exportFilename(ext);
    try {
      if (ext === "json") downloadText(name, resultAsJson(res, ctx), "application/json;charset=utf-8");
      else downloadText(name, resultAsCsv(res, ctx), "text/csv;charset=utf-8");
      setActionState(`Pobrano plik ${name}.`);
    } catch {
      setActionState("Nie udało się zapisać pliku. Skopiuj wynik do schowka.");
    }
  }

  const owner = !readOnly && Boolean(res.check_id);
  return (
    <div className="app-result">
      <h2 className="mp-sr">Wynik sprawdzenia</h2>
      {res.message && <Notice tone="info">{res.message}</Notice>}
      {res.injection_detected && (
        <Notice tone="info" title="Zignorowane polecenia">
          {INJECTION}
        </Notice>
      )}
      {res.claims.map((c, i) => (
        <ClaimPanel key={i} c={c} index={i} total={res.claims.length} />
      ))}
      <div className="app-result-actions">
        <Button variant="secondary" icon="file-text" iconPosition="start" onClick={copy}>
          Kopiuj wynik
        </Button>
        <Button variant="secondary" icon="download" iconPosition="start" onClick={() => download("json")}>
          Pobierz JSON
        </Button>
        <Button variant="secondary" icon="download" iconPosition="start" onClick={() => download("csv")}>
          Pobierz CSV
        </Button>
        {onReset ? (
          <Button variant="link" onClick={onReset}>
            Sprawdź inną wiadomość
          </Button>
        ) : (
          <a className="mp-link" href="/">
            Sprawdź własną wiadomość
          </a>
        )}
      </div>
      <p className="app-copy-state" role="status">
        {actionState}
      </p>
      {owner && <ShareBox checkId={res.check_id} erased={erase.kind === "done"} />}
      {owner && (
        <div className="app-erase">
          <p>
            Zapis tego sprawdzenia (z ukrytymi danymi osobowymi) przechowujemy 30 dni. Możesz go usunąć od razu. Usunięcie wyłącza też
            link do wyniku.
          </p>
          <Button variant="secondary" onClick={eraseLog} aria-disabled={erase.kind === "busy" || erase.kind === "done" || undefined}>
            Usuń zapis tego sprawdzenia
          </Button>
          <p className="app-copy-state" role="status">
            {erase.kind === "done"
              ? "Usunęliśmy zapis tego sprawdzenia z serwera. Wynik zostaje tylko na tej stronie."
              : erase.kind === "busy"
                ? "Usuwam zapis…"
                : erase.kind === "fail"
                  ? erase.message
                  : ""}
          </p>
        </div>
      )}
    </div>
  );
}
