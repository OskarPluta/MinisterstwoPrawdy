import { useEffect, useState, type FormEvent } from "react";
import {
  ACCEPTED_FILES,
  ApiError,
  MAX_FILE_BYTES,
  MAX_FILES,
  MAX_IMAGE_BYTES,
  MAX_INPUT_CHARS,
  MSG_OUTAGE,
  verify,
  verifyUpload,
  type CheckResult,
} from "../api";
import { Icon, InfoSection, Notice } from "../design/components";
import { Composer, MaterialList } from "../design/extra";
import { RESULT_HEADING_ID, ResultView, STATUS_NOTICE } from "../result";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; res: CheckResult }
  | { kind: "failed"; message: string };

/** Optional ?now=<ISO 8601> for demos on an old snapshot (warnings have validity windows). */
function demoNow(): string | undefined {
  return new URLSearchParams(window.location.search).get("now") ?? undefined;
}

const ACCEPTED = new Set(ACCEPTED_FILES.split(","));
const isPdf = (f: File) => f.type === "application/pdf" || (!f.type && f.name.toLowerCase().endsWith(".pdf"));

/** Why a file cannot be added, in plain words, or null. The backend checks the content again. */
function fileProblem(f: File): string | null {
  if (!ACCEPTED.has(f.type) && !isPdf(f)) return `Pliku „${f.name}” nie możemy sprawdzić. Dodaj zdjęcie, zrzut ekranu albo plik PDF.`;
  if (f.size === 0) return `Plik „${f.name}” jest pusty.`;
  const limit = isPdf(f) ? MAX_FILE_BYTES : MAX_IMAGE_BYTES;
  if (f.size > limit) return `Plik „${f.name}” jest za duży. Limit to ${limit / 1_000_000} MB.`;
  return null;
}

function CheckForm() {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fieldError, setFieldError] = useState<string>();
  const [fileError, setFileError] = useState<string>();
  const [state, setState] = useState<State>({ kind: "idle" });
  const fieldId = "tresc";

  function addFiles(added: File[]) {
    if (added.length === 0) return;
    const problems: string[] = [];
    const next = [...files];
    for (const f of added) {
      const p = fileProblem(f);
      if (p) problems.push(p);
      else if (next.length >= MAX_FILES) problems.push(`Możesz dodać najwyżej ${MAX_FILES} pliki.`);
      else next.push(f);
    }
    setFiles(next);
    setFileError(problems.length ? [...new Set(problems)].join(" ") : undefined);
    if (next.length) setFieldError(undefined);
  }

  function removeFile(i: number) {
    setFiles(files.filter((_, j) => j !== i));
    setFileError(undefined);
    document.getElementById(fieldId)?.focus(); // the removed chip's button is gone; keep focus in the box
  }

  const shown = state.kind === "done" && state.res.status === "ok" && state.res.claims.length > 0 ? state.res : null;

  useEffect(() => {
    if (shown) document.getElementById(RESULT_HEADING_ID)?.focus();
  }, [shown]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (state.kind === "loading") return; // one check at a time; the button stays focusable
    const value = text.trim();
    if (!value && files.length === 0) {
      setFieldError("Wpisz albo wklej treść wiadomości, link albo dodaj zdjęcie lub plik PDF.");
      document.getElementById(fieldId)?.focus();
      return;
    }
    if (value.length > MAX_INPUT_CHARS) {
      setFieldError(`Skróć wiadomość do ${MAX_INPUT_CHARS.toLocaleString("pl-PL")} znaków. Zostaw fragment z twierdzeniem, które chcesz sprawdzić.`);
      document.getElementById(fieldId)?.focus();
      return;
    }
    setFieldError(undefined);
    setState({ kind: "loading" });
    try {
      const res = files.length ? await verifyUpload(value, files, demoNow()) : await verify(value, demoNow());
      setState({ kind: "done", res });
    } catch (err) {
      setState({ kind: "failed", message: err instanceof ApiError ? err.message : MSG_OUTAGE });
    }
  }

  function reset() {
    setText("");
    setFiles([]);
    setFieldError(undefined);
    setFileError(undefined);
    setState({ kind: "idle" });
    // Wait for the form to render again, then put the cursor back in the field.
    requestAnimationFrame(() => document.getElementById(fieldId)?.focus());
  }

  if (shown) return <ResultView res={shown} onReset={reset} />;

  // Not-ok results and transport failures keep the form, with one notice above it.
  let notice = null;
  if (state.kind === "failed") {
    notice = <Notice tone="danger">{state.message}</Notice>;
  } else if (state.kind === "done") {
    const { status, message } = state.res;
    const view = status === "ok" ? STATUS_NOTICE.out_of_scope : STATUS_NOTICE[status];
    notice = (
      <>
        <Notice tone={view.tone} title={view.title}>
          <p>{message || "Nie znalazłem w tej wiadomości twierdzenia, które można sprawdzić."}</p>
        </Notice>
        <MaterialList inputs={state.res.inputs ?? []} />
      </>
    );
  }

  const loading = state.kind === "loading";
  const slow = files.length > 0 || /https?:\/\/|www\./i.test(text);
  return (
    <form className="app-check" onSubmit={submit} noValidate aria-busy={loading} aria-label="Sprawdź wiadomość">
      {notice}
      <Composer
        id={fieldId}
        label="Treść wiadomości do sprawdzenia"
        placeholder="Wklej wiadomość, link albo zrzut ekranu…"
        hint={`Enter wysyła, Shift+Enter to nowa linia. Możesz dodać do ${MAX_FILES} zdjęć lub plików PDF.`}
        value={text}
        onChange={(v) => {
          setText(v);
          if (fieldError) setFieldError(undefined);
        }}
        error={fieldError ?? fileError}
        maxChars={MAX_INPUT_CHARS}
        files={files}
        accept={ACCEPTED_FILES}
        canAdd={files.length < MAX_FILES}
        onAddFiles={addFiles}
        onRemoveFile={removeFile}
        busy={loading}
        submitLabel="Sprawdź"
      />
      <div aria-live="polite" className="app-progress">
        {loading && (
          <p>
            <Icon name="loader" size={18} className="mp-spin" />
            {slow ? "Odczytujemy materiał. To może potrwać do minuty." : "To może potrwać do 30 sekund."}
          </p>
        )}
      </div>
    </form>
  );
}

export function CheckPage() {
  return (
    <>
      <h1 className="app-h1">Sprawdź informację</h1>
      <p className="app-lead">
        Wklej wiadomość, link albo zrzut ekranu. Porównamy go z komunikatami instytucji publicznych i pokażemy źródła.
      </p>

      <CheckForm />

      <InfoSection title="Co możesz sprawdzić" id="co-sprawdzisz">
        <p>Wiadomości o faktach, które ogłaszają instytucje publiczne, na przykład:</p>
        <ul>
          <li>ostrzeżenia pogodowe i hydrologiczne,</li>
          <li>komunikaty służb i urzędów,</li>
          <li>informacje o zdarzeniach w Twojej okolicy.</li>
        </ul>
        <p>Nie oceniamy opinii. Jeśli brakuje dowodów, nie wydajemy oceny.</p>
      </InfoSection>
    </>
  );
}
