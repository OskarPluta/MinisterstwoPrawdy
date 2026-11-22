import { useState } from "react";
import { Notice } from "../../../frontend/src/design/components";
import { api, type UsageRow } from "../api";
import type { PageProps } from "../App";
import { CALL_PL, CHANNEL_PL, formatDay, formatInt, formatMoney, warsawDay } from "../format";
import { DataTable, DateRange, Loaded, Section, StackedBars, StatTiles, useAsync, type Column } from "../ui";

const GROUPS = [
  { key: "day", label: "Dzień" },
  { key: "model", label: "Model" },
  { key: "channel", label: "Kanał" },
  { key: "call", label: "Rodzaj wywołania" },
];

function keyLabel(group: string, key: string): string {
  if (group === "day") return formatDay(key);
  if (group === "channel") return CHANNEL_PL[key] ?? key;
  if (group === "call") return CALL_PL[key] ?? key;
  return key;
}

export function CostsPage(_: PageProps) {
  const [range, setRange] = useState({ from: warsawDay(-29), to: warsawDay() });
  const [group, setGroup] = useState("day");
  const [st, reload] = useAsync(() => api.usage(group, range.from, range.to), [group, range.from, range.to]);

  const columns = (g: string): Column<UsageRow>[] => [
    { key: "k", label: GROUPS.find((x) => x.key === g)?.label ?? "", header: true, render: (r) => <span className={g === "model" ? "adm-mono" : undefined}>{keyLabel(g, r.key)}</span> },
    ...(g === "call" ? [] : [{ key: "c", label: "Sprawdzenia", num: true, render: (r: UsageRow) => formatInt(r.checks) }]),
    { key: "n", label: "Wywołania LLM", num: true, render: (r) => formatInt(r.calls) },
    { key: "i", label: "Tokeny wej.", num: true, render: (r) => formatInt(r.input_tokens) },
    { key: "o", label: "Tokeny wyj.", num: true, render: (r) => formatInt(r.output_tokens) },
    { key: "m", label: "Koszt", num: true, render: (r) => formatMoney(r.cost) },
  ];

  return (
    <>
      <h1 className="app-h1">Koszty i tokeny</h1>
      <p className="app-lead">
        Zużycie modelu językowego według cennika w kodzie (lub PRAWDOWID_PRICE_*). Kwoty są w walucie cennika: PCSS w PLN,
        OpenAI i Anthropic w USD; nie przeliczamy ich.
      </p>
      <DateRange from={range.from} to={range.to} onChange={(from, to) => setRange({ from, to })} />
      <ul className="adm-tabs" aria-label="Grupowanie">
        {GROUPS.map((g) => (
          <li key={g.key}>
            <button type="button" aria-pressed={group === g.key} onClick={() => setGroup(g.key)}>
              {g.label}
            </button>
          </li>
        ))}
      </ul>
      <Loaded value={st} retry={reload}>
        {(u) => (
          <>
            <StatTiles
              items={[
                { label: "Sprawdzenia", value: formatInt(u.totals.checks) },
                { label: "Wywołania LLM", value: formatInt(u.totals.calls) },
                { label: "Tokeny (wej. / wyj.)", value: `${formatInt(u.totals.input_tokens)} / ${formatInt(u.totals.output_tokens)}` },
                { label: "Koszt", value: formatMoney(u.totals.cost) },
              ]}
            />
            {u.totals.unpriced_checks > 0 && (
              <Notice tone="warning" title="Część sprawdzeń bez ceny">
                <p>
                  {formatInt(u.totals.unpriced_checks)} sprawdzeń użyło modelu, którego nie ma w cenniku. Ustaw PRAWDOWID_PRICE_IN i
                  PRAWDOWID_PRICE_OUT, aby je wycenić.
                </p>
              </Notice>
            )}
            {u.group === "day" && (
              <Section title="Tokeny dziennie">
                <StackedBars
                  label="Tokeny dziennie"
                  series={[
                    { key: "in", label: "wejściowe", tone: "info" },
                    { key: "out", label: "wyjściowe", tone: "warning" },
                  ]}
                  data={u.rows.map((r) => ({ label: formatDay(r.key), values: { in: r.input_tokens, out: r.output_tokens } }))}
                />
              </Section>
            )}
            <DataTable
              label="Zużycie"
              rows={u.group === "day" ? u.rows.filter((r) => r.checks || r.calls) : u.rows}
              rowKey={(r) => r.key}
              columns={columns(u.group)}
              empty="Brak wywołań w tym okresie."
            />
          </>
        )}
      </Loaded>
    </>
  );
}
