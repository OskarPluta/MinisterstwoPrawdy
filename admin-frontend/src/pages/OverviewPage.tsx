import { Notice } from "../../../frontend/src/design/components";
import { api, type Group, type Window } from "../api";
import type { PageProps } from "../App";
import { GROUP_PL, GROUP_TONE, formatDay, formatInt, formatMoney, formatMs, formatPct } from "../format";
import { Badge, DataTable, Loaded, Section, StackedBars, StatTiles, useAsync } from "../ui";

const SERIES = (["failure", "degraded", "no_result", "success"] as Group[]).map((g) => ({ key: g, label: GROUP_PL[g], tone: GROUP_TONE[g] }));

function WindowTiles({ w }: { w: Window }) {
  return (
    <StatTiles
      items={[
        { label: "Sprawdzenia", value: formatInt(w.total) },
        { label: "Błędy techniczne", value: formatPct(w.failure_rate), hint: `${formatInt(w.by_group.failure)} sprawdzeń` },
        { label: "Z werdyktem", value: formatInt(w.by_group.success) },
        { label: "Koszt LLM", value: formatMoney(w.cost) },
        { label: "Tokeny (wej. / wyj.)", value: `${formatInt(w.tokens.input)} / ${formatInt(w.tokens.output)}` },
        { label: "Czas (mediana / p95)", value: `${formatMs(w.duration_ms.median)} / ${formatMs(w.duration_ms.p95)}` },
      ]}
    />
  );
}

export function OverviewPage(_: PageProps) {
  const [ov, reload] = useAsync(() => Promise.all([api.overview(), api.meta()]), []);
  return (
    <>
      <h1 className="app-h1">Pulpit</h1>
      <Loaded value={ov} retry={reload}>
        {([o, meta]) => {
          const labels = Object.fromEntries(meta.outcomes.map((x) => [x.key, x]));
          const outcomes = Object.entries(o.windows["7d"].by_outcome).map(([key, n]) => ({ key, n }));
          return (
            <>
              {o.alerts.open > 0 && (
                <Notice tone={o.alerts.errors ? "danger" : "warning"} title={`Otwarte alerty: ${o.alerts.open}`}>
                  <p>
                    {o.alerts.errors ? `${o.alerts.errors} z nich to błędy. ` : ""}
                    <a className="mp-link" href="/alerty">
                      Przejdź do alertów
                    </a>
                  </p>
                </Notice>
              )}
              {o.sources.with_errors > 0 && (
                <Notice tone="warning" title={`Źródła z błędem pobierania: ${o.sources.with_errors}`}>
                  <p>
                    <a className="mp-link" href="/zrodla">
                      Sprawdź źródła
                    </a>
                    {o.sources.disabled ? ` · wyłączone źródła: ${o.sources.disabled}` : ""}
                  </p>
                </Notice>
              )}
              <Section title="Ostatnie 24 godziny">
                <WindowTiles w={o.windows["24h"]} />
              </Section>
              <Section title="Ostatnie 14 dni">
                <StackedBars
                  label="Sprawdzenia dziennie według wyniku"
                  series={SERIES}
                  data={o.daily.map((d) => ({
                    label: formatDay(d.day),
                    values: { failure: d.failure, degraded: d.degraded, no_result: d.no_result, success: d.success },
                  }))}
                />
              </Section>
              <Section title="Ostatnie 7 dni">
                <WindowTiles w={o.windows["7d"]} />
                <DataTable
                  label="Wyniki sprawdzeń z 7 dni"
                  rowKey={(r) => r.key}
                  rows={outcomes}
                  empty="Brak sprawdzeń w tym okresie."
                  columns={[
                    {
                      key: "o", label: "Wynik", header: true,
                      render: (r) => {
                        const x = labels[r.key];
                        return x ? <Badge tone={GROUP_TONE[x.group]}>{x.label}</Badge> : r.key;
                      },
                    },
                    { key: "g", label: "Grupa", render: (r) => (labels[r.key] ? GROUP_PL[labels[r.key]!.group] : "—") },
                    { key: "n", label: "Liczba", num: true, render: (r) => formatInt(r.n) },
                    {
                      key: "l", label: "Lista",
                      render: (r) => (
                        <a className="mp-link" href={`/historia?outcome=${encodeURIComponent(r.key)}`}>
                          Pokaż wiadomości
                        </a>
                      ),
                    },
                  ]}
                />
              </Section>
              <Section title="Baza dowodów">
                <StatTiles
                  items={[
                    { label: "Aktualne rekordy", value: formatInt(o.corpus.current_records) },
                    { label: "Fragmenty w wyszukiwaniu", value: formatInt(o.corpus.passages_searchable) },
                    { label: "Źródła", value: formatInt(o.sources.total), hint: o.sources.disabled ? `wyłączone: ${o.sources.disabled}` : undefined },
                  ]}
                />
              </Section>
            </>
          );
        }}
      </Loaded>
    </>
  );
}
