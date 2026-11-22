// Message history: the masked check log. User text is shown exactly as logged (masked), as plain text.
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Breadcrumbs, Button, Notice, TextField } from "../../../frontend/src/design/components";
import { navigate, useSearch } from "../../../frontend/src/router";
import { api, type CheckDetail, type CheckRow, type LoggedClaim, type Meta } from "../api";
import type { PageProps } from "../App";
import { CALL_PL, CHANNEL_PL, GROUP_PL, GROUP_TONE, formatCost, formatDateTime, formatInt, formatMs } from "../format";
import { Badge, Checkbox, DataTable, Loaded, Section, Select, TextArea, TextBlock, useAsync, type Column } from "../ui";

export const REVIEW_LABELS = [
  "awaria dostawcy LLM",
  "błąd konfiguracji",
  "brak źródła w bazie",
  "błędnie wyodrębnione twierdzenie",
  "błąd w danych źródła",
  "spam lub poza zakresem",
  "do poprawy w promptach",
  "fałszywy alarm",
];

interface Filters {
  group: string;
  outcome: string;
  channel: string;
  q: string;
  from: string;
  to: string;
  unreviewed: boolean;
}

function filtersFrom(search: string): Filters {
  const p = new URLSearchParams(search);
  return {
    group: p.get("group") ?? "",
    outcome: p.get("outcome") ?? "",
    channel: p.get("channel") ?? "",
    q: p.get("q") ?? "",
    from: p.get("from") ?? "",
    to: p.get("to") ?? "",
    unreviewed: p.get("unreviewed") === "1",
  };
}

function toSearch(f: Filters): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v === true ? "1" : String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function HistoryPage(props: PageProps) {
  const search = useSearch();
  const id = new URLSearchParams(search).get("id");
  return id ? <CheckDetailView id={id} {...props} /> : <HistoryList search={search} />;
}

function OutcomeBadge({ row }: { row: Pick<CheckRow, "group" | "outcome_pl"> }) {
  return <Badge tone={GROUP_TONE[row.group]}>{row.outcome_pl}</Badge>;
}

function HistoryList({ search }: { search: string }) {
  const applied = useMemo(() => filtersFrom(search), [search]);
  const [draft, setDraft] = useState<Filters>(applied);
  const [rows, setRows] = useState<CheckRow[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [meta] = useAsync(() => api.meta(), []);
  const [page, reload] = useAsync(() => api.checks({ ...applied, unreviewed: applied.unreviewed }), [search]);

  useEffect(() => {
    setDraft(applied);
  }, [applied]);
  useEffect(() => {
    if (page.state === "ok") {
      setRows(page.data.items);
      setNext(page.data.next);
    }
  }, [page]);

  async function loadMore() {
    if (!next) return;
    setMore(true);
    try {
      const p = await api.checks({ ...applied, cursor: next });
      setRows((r) => [...r, ...p.items]);
      setNext(p.next);
    } finally {
      setMore(false);
    }
  }

  function apply(e: FormEvent) {
    e.preventDefault();
    navigate(`/historia${toSearch(draft)}`);
  }

  const m: Meta | null = meta.state === "ok" ? meta.data : null;
  const columns: Column<CheckRow>[] = [
    { key: "t", label: "Czas", header: true, render: (r) => <a className="mp-link" href={`/historia?id=${r.check_id}`}>{formatDateTime(r.created_at)}</a> },
    { key: "o", label: "Wynik", render: (r) => <OutcomeBadge row={r} /> },
    { key: "c", label: "Kanał", render: (r) => CHANNEL_PL[r.channel ?? ""] ?? r.channel ?? "—" },
    { key: "i", label: "Wiadomość (zamaskowana)", render: (r) => <span className="adm-preview">{r.input_masked}</span> },
    { key: "e", label: "Błąd", render: (r) => (r.error_type ? <span className="adm-mono">{r.error_type}</span> : "—") },
    { key: "k", label: "Koszt", num: true, render: (r) => formatCost(r.cost, r.cost_currency) },
    { key: "d", label: "Czas trwania", num: true, render: (r) => formatMs(r.duration_ms) },
    { key: "r", label: "Kategoria", render: (r) => r.review_label ?? "—" },
  ];

  return (
    <>
      <h1 className="app-h1">Historia wiadomości</h1>
      <p className="app-lead">
        Wszystkie sprawdzenia z WWW, Discorda i wiersza poleceń. Dane osobowe w treści są zamaskowane już przy zapisie
        (e-mail, telefon, PESEL, numer konta i dowodu), panel nie ma dostępu do oryginału.
      </p>
      <form className="adm-filters" onSubmit={apply} aria-label="Filtry">
        <Select
          label="Grupa"
          value={draft.group}
          onChange={(e) => setDraft({ ...draft, group: e.target.value })}
          options={[{ value: "", label: "wszystkie" }, ...(["failure", "degraded", "no_result", "success"] as const).map((g) => ({ value: g, label: GROUP_PL[g] }))]}
        />
        <Select
          label="Wynik"
          value={draft.outcome}
          onChange={(e) => setDraft({ ...draft, outcome: e.target.value })}
          options={[{ value: "", label: "wszystkie" }, ...(m?.outcomes ?? []).map((o) => ({ value: o.key, label: o.label }))]}
        />
        <Select
          label="Kanał"
          value={draft.channel}
          onChange={(e) => setDraft({ ...draft, channel: e.target.value })}
          options={[{ value: "", label: "wszystkie" }, ...(m?.channels ?? []).map((c) => ({ value: c, label: CHANNEL_PL[c] ?? c }))]}
        />
        <TextField label="Szukaj w treści" className="adm-grow" value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
        <TextField label="Od" type="date" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        <TextField label="Do" type="date" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        <div className="mp-field">
          <Checkbox label="Tylko bez kategorii" checked={draft.unreviewed} onChange={(v) => setDraft({ ...draft, unreviewed: v })} />
        </div>
        <div className="mp-field">
          <Button type="submit" icon="search">
            Filtruj
          </Button>
        </div>
      </form>
      <Loaded value={page} retry={reload}>
        {() => (
          <>
            <DataTable label="Historia wiadomości" rows={rows} rowKey={(r) => r.check_id} columns={columns} empty="Brak sprawdzeń dla tych filtrów." />
            {next && (
              <Button variant="secondary" className="adm-more" onClick={() => void loadMore()} disabled={more}>
                {more ? "Wczytywanie…" : "Pokaż starsze"}
              </Button>
            )}
          </>
        )}
      </Loaded>
    </>
  );
}

function liveErrors(c: LoggedClaim): string[] {
  const live = c.live as { error?: string; sources?: { source: string; error?: string | null }[] } | null | undefined;
  if (!live) return [];
  return [...(live.error ? [live.error] : []), ...(live.sources ?? []).filter((s) => s.error).map((s) => `${s.source}: ${s.error}`)];
}

function ClaimCard({ c, i }: { c: LoggedClaim; i: number }) {
  const errors = liveErrors(c);
  return (
    <article className="adm-card">
      <h3>
        {i + 1}. {c.verdict_pl}
        {c.downgraded_from ? ` (model: ${c.downgraded_from})` : ""}
      </h3>
      <TextBlock>{c.claim}</TextBlock>
      <TextBlock>{c.explanation}</TextBlock>
      {c.citations.length > 0 && (
        <ul>
          {c.citations.map((ct) => (
            <li key={ct.n}>
              [{ct.n}] {ct.role}: {ct.url ? (
                <a className="mp-link" href={ct.url} target="_blank" rel="noopener noreferrer">
                  {ct.title}
                  <span className="mp-sr"> (otwiera się w nowej karcie)</span>
                </a>
              ) : ct.title}{" "}
              <span className="app-muted">· {ct.publisher} · {ct.date_label}</span>
            </li>
          ))}
        </ul>
      )}
      <dl className="adm-dl">
        {c.strength && (
          <>
            <dt>Siła dowodów</dt>
            <dd>{c.strength.level}</dd>
          </>
        )}
        {c.known_disinformation && (
          <>
            <dt>Znana dezinformacja</dt>
            <dd>tak</dd>
          </>
        )}
        {c.error && (
          <>
            <dt>Błąd</dt>
            <dd className="adm-mono">{c.error}</dd>
          </>
        )}
        {!!c.invented_ids?.length && (
          <>
            <dt>Odrzucone identyfikatory</dt>
            <dd className="adm-mono">{c.invented_ids.join(", ")}</dd>
          </>
        )}
        {errors.length > 0 && (
          <>
            <dt>Wyszukiwanie na żywo</dt>
            <dd className="adm-mono">{errors.join("; ")}</dd>
          </>
        )}
        {!!c.notes.length && (
          <>
            <dt>Notatki</dt>
            <dd>{c.notes.join(" · ")}</dd>
          </>
        )}
      </dl>
    </article>
  );
}

function ReviewForm({ d, onSaved }: { d: CheckDetail; onSaved: () => void }) {
  const [label, setLabel] = useState(d.review?.label ?? "");
  const [note, setNote] = useState(d.review?.note ?? "");
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api.review(d.check_id, label.trim(), note);
      setMsg({ tone: "success", text: "Zapisano kategorię." });
      onSaved();
    } catch (err) {
      setMsg({ tone: "danger", text: err instanceof Error ? err.message : String(err) });
    }
  }

  async function remove() {
    await api.unreview(d.check_id);
    setLabel("");
    setNote("");
    setMsg({ tone: "success", text: "Usunięto kategorię." });
    onSaved();
  }

  return (
    <form className="adm-form" onSubmit={save}>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <TextField
        label="Kategoria"
        hint="Wybierz z listy albo wpisz własną."
        list="adm-review-labels"
        value={label}
        maxLength={80}
        onChange={(e) => setLabel(e.target.value)}
      />
      <datalist id="adm-review-labels">
        {REVIEW_LABELS.map((l) => (
          <option key={l} value={l} />
        ))}
      </datalist>
      <TextArea label="Notatka" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      <div className="adm-actions">
        <Button type="submit" disabled={!label.trim()}>
          Zapisz kategorię
        </Button>
        {d.review && (
          <Button variant="link" onClick={() => void remove()}>
            Usuń kategorię
          </Button>
        )}
      </div>
    </form>
  );
}

function CheckDetailView({ id }: { id: string } & PageProps) {
  const [st, reload] = useAsync(() => api.check(id), [id]);
  return (
    <>
      <Breadcrumbs items={[{ label: "Historia", href: "/historia" }, { label: "Sprawdzenie" }]} />
      <h1 className="app-h1">Sprawdzenie</h1>
      <Loaded value={st} retry={reload}>
        {(d) => {
          const u = d.result.usage;
          return (
            <>
              <dl className="adm-dl">
                <dt>Wynik</dt>
                <dd>
                  <OutcomeBadge row={d} /> <span className="app-muted">({GROUP_PL[d.group]}, status: {d.status})</span>
                </dd>
                <dt>Czas</dt>
                <dd>{formatDateTime(d.created_at)}</dd>
                <dt>Kanał</dt>
                <dd>{CHANNEL_PL[d.channel ?? ""] ?? d.channel ?? "—"}</dd>
                <dt>Model</dt>
                <dd className="adm-mono">{d.model ?? "—"}</dd>
                <dt>Embedder / wersja bazy</dt>
                <dd className="adm-mono">
                  {d.embedding_model ?? "—"} / {d.corpus_version}
                </dd>
                <dt>Czas trwania</dt>
                <dd>{formatMs(d.duration_ms)}</dd>
                <dt>Wywołania LLM / tokeny</dt>
                <dd>
                  {formatInt(d.llm_calls)} / {formatInt(d.input_tokens)} wej. · {formatInt(d.output_tokens)} wyj.
                </dd>
                <dt>Koszt</dt>
                <dd>{formatCost(d.cost, d.cost_currency)}</dd>
                {d.error_type && (
                  <>
                    <dt>Typ błędu</dt>
                    <dd className="adm-mono">{d.error_type}</dd>
                  </>
                )}
                {d.result.error && (
                  <>
                    <dt>Szczegóły błędu</dt>
                    <dd className="adm-mono">{d.result.error}</dd>
                  </>
                )}
                <dt>Identyfikator</dt>
                <dd className="adm-mono">{d.check_id}</dd>
              </dl>
              <Section title="Wiadomość (zamaskowana)">
                <TextBlock>{d.input_masked || "(pusta)"}</TextBlock>
                {d.result.injection_detected && <Notice tone="warning">Wykryto próbę wstrzyknięcia instrukcji.</Notice>}
                {d.result.inputs.length > 0 && (
                  <ul>
                    {d.result.inputs.map((a) => (
                      <li key={a.id}>
                        {a.id} · {a.kind} · {a.label} · {a.readable ? "odczytano" : "nieczytelne"}
                        {a.limitations.length ? ` · ${a.limitations.join("; ")}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
              <Section title="Odpowiedź">
                {d.result.message && <TextBlock>{d.result.message}</TextBlock>}
                {d.result.claims.map((c, i) => (
                  <ClaimCard key={i} c={c} i={i} />
                ))}
                {!d.result.claims.length && !d.result.message && <p className="app-muted">Brak twierdzeń.</p>}
              </Section>
              {u?.by_call?.length ? (
                <Section title="Wywołania LLM">
                  <DataTable
                    label="Wywołania LLM"
                    rows={u.by_call.map((c, i) => ({ ...c, i }))}
                    rowKey={(r) => r.i}
                    columns={[
                      { key: "c", label: "Wywołanie", header: true, render: (r) => CALL_PL[r.call] ?? r.call },
                      { key: "i", label: "Tokeny wejściowe", num: true, render: (r) => formatInt(r.in) },
                      { key: "o", label: "Tokeny wyjściowe", num: true, render: (r) => formatInt(r.out) },
                    ]}
                  />
                </Section>
              ) : null}
              <Section title="Kategoria administratora">
                <ReviewForm d={d} onSaved={reload} />
              </Section>
            </>
          );
        }}
      </Loaded>
    </>
  );
}
