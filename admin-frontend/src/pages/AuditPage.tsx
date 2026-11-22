import { api } from "../api";
import type { PageProps } from "../App";
import { formatDateTime } from "../format";
import { DataTable, Loaded, useAsync } from "../ui";

const ACTION_PL: Record<string, string> = {
  login: "logowanie",
  "login.failed": "nieudane logowanie",
  "check.review": "kategoria sprawdzenia",
  "check.review_removed": "usunięcie kategorii",
  "source.update": "zmiana źródła",
  "source.create": "nowe źródło",
  "document.add": "dodanie dokumentu",
  "document.withdraw": "wycofanie dokumentu",
  "document.restore": "przywrócenie dokumentu",
  "domain.upsert": "domena weryfikatora",
  "domain.delete": "usunięcie domeny",
  "alert.ack": "potwierdzenie alertu",
  "alert.ack_all": "potwierdzenie wszystkich alertów",
};

export function AuditPage(_: PageProps) {
  const [st, reload] = useAsync(() => api.audit(), []);
  return (
    <>
      <h1 className="app-h1">Dziennik zmian</h1>
      <p className="app-lead">Każda zmiana wprowadzona w panelu, od najnowszej.</p>
      <Loaded value={st} retry={reload}>
        {({ items }) => (
          <DataTable
            label="Dziennik zmian"
            rows={items}
            rowKey={(a) => a.audit_id}
            empty="Brak wpisów."
            columns={[
              { key: "t", label: "Czas", header: true, render: (a) => formatDateTime(a.at) },
              { key: "a", label: "Zmiana", render: (a) => ACTION_PL[a.action] ?? a.action },
              { key: "o", label: "Obiekt", render: (a) => <span className="adm-mono">{a.target ?? "—"}</span> },
              {
                key: "d", label: "Szczegóły",
                render: (a) => (Object.keys(a.details).length ? <span className="adm-mono">{JSON.stringify(a.details)}</span> : "—"),
              },
            ]}
          />
        )}
      </Loaded>
    </>
  );
}
