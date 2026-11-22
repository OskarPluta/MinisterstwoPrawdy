import { useState } from "react";
import { api } from "../api";
import type { PageProps } from "../App";
import { GROUP_PL, GROUP_TONE, formatDay, formatInt, warsawDay } from "../format";
import { Badge, DataTable, DateRange, Loaded, Section, StackedBars, StatTiles, useAsync } from "../ui";

export function FailuresPage(_: PageProps) {
  const [range, setRange] = useState({ from: warsawDay(-29), to: warsawDay() });
  const [st, reload] = useAsync(() => api.failures(range.from, range.to), [range.from, range.to]);
  return (
    <>
      <h1 className="app-h1">Nieudane sprawdzenia</h1>
      <p className="app-lead">
        Sprawdzenia bez werdyktu, w trzech grupach: błędy techniczne (alarmują), częściowe błędy oraz wiadomości obsłużone
        poprawnie, które nie dały werdyktu (np. brak dowodów w bazie). Kategorie przypisuje kod, nie model.
      </p>
      <DateRange from={range.from} to={range.to} onChange={(from, to) => setRange({ from, to })} />
      <Loaded value={st} retry={reload}>
        {(f) => {
          const sum = (g: string) => f.by_outcome.filter((o) => o.group === g).reduce((s, o) => s + o.count, 0);
          const q = `from=${f.from}&to=${f.to}`;
          const labelOf = Object.fromEntries(f.by_outcome.map((o) => [o.outcome, o.label]));
          return (
            <>
              <StatTiles
                items={[
                  { label: "Błędy techniczne", value: formatInt(sum("failure")) },
                  { label: "Częściowe błędy", value: formatInt(sum("degraded")) },
                  { label: "Bez werdyktu", value: formatInt(sum("no_result")) },
                  { label: "Błędy bez kategorii", value: formatInt(f.unreviewed), hint: "do przejrzenia" },
                ]}
              />
              {f.unreviewed > 0 && (
                <p>
                  <a className="mp-link" href={`/historia?group=failure&unreviewed=1&${q}`}>
                    Przejrzyj błędy bez kategorii
                  </a>
                </p>
              )}
              <Section title="Dziennie">
                <StackedBars
                  label="Nieudane sprawdzenia dziennie"
                  series={(["failure", "degraded", "no_result"] as const).map((g) => ({ key: g, label: GROUP_PL[g], tone: GROUP_TONE[g] }))}
                  data={f.daily.map((d) => ({ label: formatDay(d.day), values: { failure: d.failure, degraded: d.degraded, no_result: d.no_result } }))}
                />
              </Section>
              <Section title="Według kategorii">
                <DataTable
                  label="Nieudane sprawdzenia według kategorii"
                  rows={f.by_outcome}
                  rowKey={(r) => r.outcome}
                  empty="Brak nieudanych sprawdzeń w tym okresie."
                  columns={[
                    { key: "o", label: "Kategoria", header: true, render: (r) => <Badge tone={GROUP_TONE[r.group]}>{r.label}</Badge> },
                    { key: "g", label: "Grupa", render: (r) => GROUP_PL[r.group] },
                    { key: "n", label: "Liczba", num: true, render: (r) => formatInt(r.count) },
                    {
                      key: "l", label: "Lista",
                      render: (r) => (
                        <a className="mp-link" href={`/historia?outcome=${encodeURIComponent(r.outcome)}&${q}`}>
                          Pokaż wiadomości
                        </a>
                      ),
                    },
                  ]}
                />
              </Section>
              <Section title="Typy błędów">
                <DataTable
                  label="Typy błędów"
                  rows={f.error_types}
                  rowKey={(r) => `${r.error_type}:${r.outcome}`}
                  empty="Brak błędów z typem wyjątku."
                  columns={[
                    { key: "t", label: "Wyjątek", header: true, render: (r) => <span className="adm-mono">{r.error_type}</span> },
                    { key: "o", label: "Kategoria", render: (r) => labelOf[r.outcome] ?? r.outcome },
                    { key: "n", label: "Liczba", num: true, render: (r) => formatInt(r.count) },
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
