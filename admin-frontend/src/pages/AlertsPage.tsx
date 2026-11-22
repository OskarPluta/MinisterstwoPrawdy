import { useState } from "react";
import { Button, Notice } from "../../../frontend/src/design/components";
import { api, type Alert } from "../api";
import type { PageProps } from "../App";
import { formatDateTime, formatInt } from "../format";
import { Badge, Checkbox, DataTable, Loaded, useAsync } from "../ui";

const KIND_PL: Record<string, string> = { check: "sprawdzenie", rule: "reguła", source: "źródło" };

export function AlertsPage({ refreshAlerts }: PageProps) {
  const [openOnly, setOpenOnly] = useState(true);
  const [st, reload] = useAsync(() => api.alerts(openOnly), [openOnly]);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    reload();
    refreshAlerts();
  }

  return (
    <>
      <h1 className="app-h1">Alerty</h1>
      <p className="app-lead">
        Błędy przetwarzania wiadomości i problemy ze źródłami. Powtórzenia tego samego problemu zwiększają licznik zamiast
        tworzyć nowe alerty. Jeśli ustawiono ADMIN_ALERT_DISCORD_WEBHOOK, nowe alerty trafiają też na Discord (bez treści
        wiadomości użytkowników).
      </p>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className="adm-actions">
        <Checkbox label="Tylko otwarte" checked={openOnly} onChange={setOpenOnly} />
        <Button variant="secondary" onClick={() => void act(() => api.ackAll())}>
          Potwierdź wszystkie
        </Button>
      </div>
      <Loaded value={st} retry={reload}>
        {({ items }) => (
          <DataTable<Alert>
            label="Alerty"
            rows={items}
            rowKey={(a) => a.alert_id}
            empty={openOnly ? "Brak otwartych alertów." : "Brak alertów."}
            columns={[
              {
                key: "m", label: "Alert", header: true,
                render: (a) => <Badge tone={a.severity === "error" ? "danger" : "warning"}>{a.message}</Badge>,
              },
              { key: "k", label: "Rodzaj", render: (a) => KIND_PL[a.kind] ?? a.kind },
              { key: "n", label: "Wystąpienia", num: true, render: (a) => formatInt(a.count) },
              { key: "f", label: "Pierwsze", render: (a) => formatDateTime(a.created_at) },
              { key: "l", label: "Ostatnie", render: (a) => formatDateTime(a.last_seen_at) },
              {
                key: "c", label: "Sprawdzenie",
                render: (a) => (a.check_id ? <a className="mp-link" href={`/historia?id=${a.check_id}`}>szczegóły</a> : "—"),
              },
              { key: "s", label: "Wysłano", render: (a) => (a.notified_at ? formatDateTime(a.notified_at) : "—") },
              {
                key: "a", label: "Stan",
                render: (a) =>
                  a.acknowledged_at ? (
                    <span className="app-muted">potwierdzony {formatDateTime(a.acknowledged_at)}</span>
                  ) : (
                    <Button variant="secondary" onClick={() => void act(() => api.ack(a.alert_id))}>
                      Potwierdź
                    </Button>
                  ),
              },
            ]}
          />
        )}
      </Loaded>
    </>
  );
}
