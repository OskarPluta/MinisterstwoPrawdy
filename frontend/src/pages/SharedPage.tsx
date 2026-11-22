// Read-only page of a shared check (/wynik/<share_id>). The result comes from the server copy made
// at check time (GET /api/shares/<id>); nothing on this page can change it.
import { useEffect, useState } from "react";
import { ApiError, formatDate, formatDateTime, getShare, MSG_OUTAGE, type SharedResult } from "../api";
import { Notice } from "../design/components";
import { ResultView } from "../result";

type State = { kind: "loading" } | { kind: "ok"; data: SharedResult } | { kind: "failed"; message: string };

/** Shared pages are for the people given the link, not for search engines. */
function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
}

export function SharedPage({ shareId }: { shareId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  useNoIndex();

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    getShare(shareId)
      .then((data) => live && setState({ kind: "ok", data }))
      .catch((err) => live && setState({ kind: "failed", message: err instanceof ApiError ? err.message : MSG_OUTAGE }));
    return () => {
      live = false;
    };
  }, [shareId]);

  return (
    <>
      <h1 className="app-h1">Udostępniony wynik sprawdzenia</h1>
      {state.kind === "loading" && <p className="app-lead">Wczytuję wynik…</p>}
      {state.kind === "failed" && (
        <Notice tone="warning" title="Nie można wyświetlić wyniku">
          <p>{state.message}</p>
          <p>
            <a className="mp-link" href="/">
              Sprawdź własną wiadomość
            </a>
          </p>
        </Notice>
      )}
      {state.kind === "ok" && (
        <>
          <Notice tone="info" title="Wersja tylko do odczytu">
            <p>
              Sprawdzenie z {formatDateTime(state.data.checked_at)}. Ten wynik zapisaliśmy na serwerze w chwili sprawdzenia. Nikt nie może
              go zmienić, także osoba, która go udostępniła. Dane osobowe w treści są ukryte.
            </p>
            {state.data.expires_at && <p>Link działa do {formatDate(state.data.expires_at)}.</p>}
            <p>Ocena dotyczy stanu źródeł z dnia sprawdzenia. Informacje mogły się od tego czasu zmienić.</p>
          </Notice>
          <ResultView res={state.data.result} readOnly checkedAt={state.data.checked_at} />
        </>
      )}
    </>
  );
}
