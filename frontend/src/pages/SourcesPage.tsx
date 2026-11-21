import { useEffect, useState } from "react";
import { ApiError, getSources, MSG_OUTAGE, type SourcesResponse } from "../api";
import { Breadcrumbs, InfoSection, Notice } from "../design/components";
import { SourcesTable } from "../design/extra";

type State = { kind: "loading" } | { kind: "ok"; data: SourcesResponse } | { kind: "failed"; message: string };

export function SourcesPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    getSources()
      .then((data) => live && setState({ kind: "ok", data }))
      .catch((e) => live && setState({ kind: "failed", message: e instanceof ApiError ? e.message : MSG_OUTAGE }));
    return () => {
      live = false;
    };
  }, []);

  return (
    <>
      <Breadcrumbs items={[{ label: "Źródła" }]} />
      <h1 className="app-h1">Sprawdź, z jakich źródeł korzystamy</h1>
      <p className="app-lead">
        Porównujemy wiadomości tylko z oficjalnymi komunikatami i danymi instytucji publicznych. Każdą ocenę możesz sprawdzić w źródle.
      </p>

      {state.kind === "loading" && (
        <p className="app-muted" role="status">
          Wczytuję listę źródeł…
        </p>
      )}
      {state.kind === "failed" && <Notice tone="danger">{state.message}</Notice>}
      {state.kind === "ok" && (
        <>
          <dl className="app-totals">
            <div>
              <dt>Rekordy w bazie</dt>
              <dd>{state.data.totals.current_records.toLocaleString("pl-PL")}</dd>
            </div>
            <div>
              <dt>Domeny</dt>
              <dd>{state.data.totals.publishing_domains.toLocaleString("pl-PL")}</dd>
            </div>
            <div>
              <dt>Źródła</dt>
              <dd>{state.data.sources.length.toLocaleString("pl-PL")}</dd>
            </div>
          </dl>
          {state.data.sources.length > 0 ? (
            <SourcesTable sources={state.data.sources} />
          ) : (
            <Notice>
              <p>Baza źródeł jest pusta.</p>
            </Notice>
          )}
        </>
      )}

      <InfoSection title="Jak aktualizujemy źródła" id="aktualizacja" className="app-after-table">
        <p>
          Nasz program pobiera komunikaty ze stron instytucji. Przestrzega zasad tych stron i nie obchodzi zabezpieczeń. Każda zmiana treści
          tworzy nową wersję, a poprzednia zostaje zapisana.
        </p>
        <p>„Ostatnia aktualizacja” to dzień, w którym ostatni raz udało się pobrać dane ze źródła.</p>
      </InfoSection>
    </>
  );
}
