import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, MAX_INPUT_CHARS, MSG_OUTAGE, verify, type CheckResult } from "../api";
import { Button, InfoSection, Notice, ServiceSteps } from "../design/components";
import { TextArea } from "../design/extra";
import { RESULT_HEADING_ID, ResultView, STATUS_NOTICE } from "../result";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; res: CheckResult }
  | { kind: "failed"; message: string; limited?: boolean };

/** Optional ?now=<ISO 8601> for demos on an old snapshot (warnings have validity windows). */
function demoNow(): string | undefined {
  return new URLSearchParams(window.location.search).get("now") ?? undefined;
}

function CheckForm() {
  const [text, setText] = useState("");
  const [fieldError, setFieldError] = useState<string>();
  const [state, setState] = useState<State>({ kind: "idle" });
  const formRef = useRef<HTMLFormElement>(null);
  const fieldId = "tresc";

  const shown = state.kind === "done" && state.res.status === "ok" && state.res.claims.length > 0 ? state.res : null;

  useEffect(() => {
    if (shown) document.getElementById(RESULT_HEADING_ID)?.focus();
  }, [shown]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (state.kind === "loading") return; // one check at a time; the button stays focusable
    const value = text.trim();
    if (!value) {
      setFieldError("Wpisz albo wklej treść wiadomości, którą chcesz sprawdzić.");
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
      setState({ kind: "done", res: await verify(value, demoNow()) });
    } catch (err) {
      setState({
        kind: "failed",
        message: err instanceof ApiError ? err.message : MSG_OUTAGE,
        limited: err instanceof ApiError && err.status === 429,
      });
    }
  }

  function reset() {
    setText("");
    setState({ kind: "idle" });
    // Wait for the form to render again, then put the cursor back in the field.
    requestAnimationFrame(() => document.getElementById(fieldId)?.focus());
  }

  if (shown) return <ResultView res={shown} onReset={reset} />;

  // Not-ok results and transport failures keep the form, with one notice above it.
  let notice = null;
  if (state.kind === "failed") {
    // A rate limit is not a failure: amber "try again later", not red.
    notice = state.limited ? (
      <Notice tone="warning" title="Spróbuj za chwilę">
        <p>{state.message}</p>
      </Notice>
    ) : (
      <Notice tone="danger">{state.message}</Notice>
    );
  } else if (state.kind === "done") {
    const { status, message } = state.res;
    const view = status === "ok" ? STATUS_NOTICE.out_of_scope : STATUS_NOTICE[status];
    notice = (
      <Notice tone={view.tone} title={view.title}>
        <p>{message || "Nie znalazłem w tej wiadomości twierdzenia, które można sprawdzić."}</p>
      </Notice>
    );
  }

  const loading = state.kind === "loading";
  return (
    <form ref={formRef} className="app-check" onSubmit={submit} noValidate aria-busy={loading} aria-labelledby="sprawdz-t">
      <h2 id="sprawdz-t" className="app-check-title">
        Sprawdź wiadomość
      </h2>
      {notice}
      <TextArea
        id={fieldId}
        label="Treść wiadomości"
        hint="Wklej tekst z komunikatora, SMS-a albo posta. Jeśli masz tylko link, wklej też tekst, który się pod nim znajduje."
        value={text}
        onChange={(e) => setText(e.target.value)}
        error={fieldError}
        maxChars={MAX_INPUT_CHARS}
        rows={6}
      />
      <div aria-live="polite">
        {loading && (
          <Notice tone="info" title="Sprawdzam">
            <p>To może potrwać do 30 sekund. Nie zamykaj tej strony.</p>
          </Notice>
        )}
      </div>
      <Button type="submit" size="lg" aria-disabled={loading || undefined}>
        Sprawdź
      </Button>
    </form>
  );
}

export function CheckPage() {
  return (
    <>
      <h1 className="app-h1">Sprawdź informację</h1>
      <p className="app-lead">
        Wklej wiadomość, którą chcesz sprawdzić. Porównamy ją z komunikatami instytucji publicznych i pokażemy źródła. Sprawdzenie trwa
        zwykle do 30 sekund.
      </p>
      <ul className="app-links">
        <li>
          <a className="mp-link" href="/jak-to-dziala">
            Jak sprawdzamy informacje
          </a>
        </li>
        <li>
          <a className="mp-link" href="/zrodla">
            Z jakich źródeł korzystamy
          </a>
        </li>
      </ul>

      <CheckForm />

      <InfoSection title="Co możesz sprawdzić" id="co-sprawdzisz">
        <p>Wiadomości o faktach, które ogłaszają instytucje publiczne, na przykład:</p>
        <ul>
          <li>ostrzeżenia pogodowe i hydrologiczne,</li>
          <li>komunikaty służb i urzędów,</li>
          <li>informacje o zdarzeniach w Twojej okolicy.</li>
        </ul>
        <p>Nie oceniamy opinii. Jeśli wiadomość zawiera tylko opinię, powiemy Ci o tym.</p>
      </InfoSection>

      <InfoSection title="Co zrobisz" id="kroki">
        <ServiceSteps
          steps={[
            "Wklej treść wiadomości w polu powyżej.",
            "Kliknij „Sprawdź”.",
            "Przeczytaj ocenę każdego twierdzenia z wiadomości.",
            "Otwórz źródła i sprawdź je sam.",
          ]}
        />
        <Notice>
          <p>Wynik nie jest dokumentem urzędowym. Jeśli brakuje dowodów, nie wydajemy oceny.</p>
        </Notice>
      </InfoSection>

      <InfoSection title="Ile zapłacisz" id="koszt">
        <p>Usługa jest bezpłatna.</p>
      </InfoSection>

      <InfoSection title="Na jakiej podstawie" id="podstawa">
        <p>
          Korzystamy tylko z oficjalnych komunikatów instytucji publicznych.{" "}
          <a className="mp-link" href="/zrodla">
            Zobacz listę źródeł
          </a>
          .
        </p>
      </InfoSection>
    </>
  );
}
