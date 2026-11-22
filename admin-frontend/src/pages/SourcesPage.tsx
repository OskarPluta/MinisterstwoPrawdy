// Trusted sources: enable/disable, overrides that survive ingest, single links, withdrawn documents.
// Crawler sources themselves are defined in backend/crawler/sources.yaml; nothing here deletes evidence.
import { useState, type FormEvent } from "react";
import { Breadcrumbs, Button, Icon, Notice, TextField } from "../../../frontend/src/design/components";
import { useSearch } from "../../../frontend/src/router";
import { api, type AdminSource, type Document } from "../api";
import type { PageProps } from "../App";
import { SOURCE_TYPE_PL, formatDateTime, formatInt } from "../format";
import { Badge, Checkbox, ConfirmDialog, DataTable, Loaded, Section, Select, TextArea, useAsync } from "../ui";

const TYPE_OPTIONS = Object.entries(SOURCE_TYPE_PL).map(([value, label]) => ({ value, label }));
type Msg = { tone: "success" | "danger" | "warning"; text: string } | null;

export function SourcesPage(_: PageProps) {
  const id = new URLSearchParams(useSearch()).get("id");
  return id ? <DocumentsView id={id} /> : <SourceList />;
}

function FetchState({ s }: { s: AdminSource }) {
  if (s.last_error) return <Badge tone="warning">ostatnie pobranie nieudane</Badge>;
  if (s.last_success_at) return <Badge tone="success">{formatDateTime(s.last_success_at)}</Badge>;
  return <span className="app-muted">brak danych</span>;
}

function SourceList() {
  const [st, reload] = useAsync(() => api.sources(), []);
  const [editing, setEditing] = useState<AdminSource | null>(null);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function toggle(s: AdminSource) {
    try {
      await api.updateSource(s.source_id, {
        enabled: !s.enabled, publisher: s.admin_publisher, source_type: s.admin_source_type, note: s.admin_note,
      });
      setMsg({ tone: "success", text: `${s.enabled ? "Wyłączono" : "Włączono"}: ${s.admin_publisher ?? s.publisher}.` });
      reload();
    } catch (e) {
      setMsg({ tone: "danger", text: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <>
      <h1 className="app-h1">Źródła</h1>
      <p className="app-lead">
        Wyłączone źródło wypada z wyszukiwania od razu (bez restartu API), a jego rekordy zostają w bazie. Zmiany nazwy i
        rodzaju są nadpisaniem: kolejny import danych crawlera ich nie cofa. Nowe źródła crawlera dodaje się w
        backend/crawler/sources.yaml.
      </p>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <div className="adm-actions">
        <Button variant="secondary" icon="file-text" iconPosition="start" onClick={() => setAdding((v) => !v)}>
          {adding ? "Zamknij formularz" : "Dodaj źródło ręczne"}
        </Button>
      </div>
      {adding && <NewSourceForm onDone={(text) => (setAdding(false), setMsg({ tone: "success", text }), reload())} />}
      {editing && (
        <EditSourceForm
          s={editing}
          onCancel={() => setEditing(null)}
          onSaved={(text) => (setEditing(null), setMsg({ tone: "success", text }), reload())}
        />
      )}
      <Loaded value={st} retry={reload}>
        {({ items }) => (
          <DataTable
            label="Źródła"
            rows={items}
            rowKey={(s) => s.source_id}
            columns={[
              {
                key: "p", label: "Instytucja", header: true,
                render: (s) => (
                  <span className={s.enabled ? undefined : "adm-disabled-row"}>
                    {s.admin_publisher ?? s.publisher}
                    {s.admin_publisher && <span className="app-muted"> (zmienione; import: {s.publisher})</span>}
                    <br />
                    <span className="adm-mono app-muted">{s.source_id}</span>
                  </span>
                ),
              },
              { key: "d", label: "Domena", render: (s) => s.publishing_domain },
              {
                key: "t", label: "Rodzaj",
                render: (s) => (
                  <>
                    {SOURCE_TYPE_PL[s.admin_source_type ?? s.source_type] ?? s.admin_source_type ?? s.source_type}
                    {s.admin_source_type && <span className="app-muted"> (zmieniony)</span>}
                  </>
                ),
              },
              {
                key: "r", label: "Rekordy", num: true,
                render: (s) => (
                  <>
                    {formatInt(s.current_records)}
                    {s.withdrawn_records > 0 && <span className="app-muted"> (wycofane: {s.withdrawn_records})</span>}
                  </>
                ),
              },
              { key: "f", label: "Ostatnie pobranie", render: (s) => <FetchState s={s} /> },
              { key: "e", label: "W wyszukiwaniu", render: (s) => (s.enabled ? <Badge tone="success">włączone</Badge> : <Badge tone="warning">wyłączone</Badge>) },
              {
                key: "a", label: "Akcje",
                render: (s) => (
                  <div className="adm-actions">
                    <Button variant="secondary" onClick={() => void toggle(s)}>
                      {s.enabled ? "Wyłącz" : "Włącz"}
                    </Button>
                    <Button variant="link" onClick={() => setEditing(s)}>
                      Edytuj
                    </Button>
                    <a className="mp-link" href={`/zrodla?id=${encodeURIComponent(s.source_id)}`}>
                      Dokumenty
                    </a>
                  </div>
                ),
              },
            ]}
          />
        )}
      </Loaded>
    </>
  );
}

function EditSourceForm({ s, onSaved, onCancel }: { s: AdminSource; onSaved: (msg: string) => void; onCancel: () => void }) {
  const [publisher, setPublisher] = useState(s.admin_publisher ?? "");
  const [type, setType] = useState(s.admin_source_type ?? "");
  const [note, setNote] = useState(s.admin_note ?? "");
  const [enabled, setEnabled] = useState(s.enabled);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Only fact-check sources can mark known disinformation, so moving a source in or out of that type is confirmed.
  const factcheckChange = ((type || s.source_type) === "factcheck") !== ((s.admin_source_type ?? s.source_type) === "factcheck");

  async function save() {
    setConfirm(false);
    try {
      await api.updateSource(s.source_id, { enabled, publisher: publisher.trim() || null, source_type: type || null, note: note.trim() || null });
      onSaved(`Zapisano zmiany źródła ${publisher.trim() || s.publisher}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (factcheckChange) setConfirm(true);
    else void save();
  }

  return (
    <Section title={`Edycja: ${s.publisher}`}>
      <form className="adm-form adm-card" onSubmit={submit}>
        {error && <Notice tone="danger">{error}</Notice>}
        <TextField label="Nazwa wyświetlana" hint={`Puste = z importu („${s.publisher}”).`} value={publisher} maxLength={200} onChange={(e) => setPublisher(e.target.value)} />
        <Select
          label="Rodzaj źródła"
          hint={`Puste = z importu (${SOURCE_TYPE_PL[s.source_type] ?? s.source_type}). Rodzaj wpływa na siłę dowodów.`}
          value={type}
          onChange={(e) => setType(e.target.value)}
          options={[{ value: "", label: "z importu" }, ...TYPE_OPTIONS]}
        />
        <TextArea label="Notatka" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        <div className="mp-field">
          <Checkbox label="Źródło w wyszukiwaniu" checked={enabled} onChange={setEnabled} />
        </div>
        <div className="adm-actions">
          <Button type="submit">Zapisz</Button>
          <Button variant="link" onClick={onCancel}>
            Anuluj
          </Button>
        </div>
      </form>
      <ConfirmDialog open={confirm} title="Zmienić rodzaj na weryfikację lub z niej?" confirmLabel="Zmień rodzaj" onConfirm={() => void save()} onCancel={() => setConfirm(false)}>
        <p>
          Tylko cytat „przeczy” ze źródła typu weryfikacja może oznaczyć twierdzenie jako znaną narrację dezinformacyjną. Ta
          zmiana wpływa na przyszłe werdykty.
        </p>
      </ConfirmDialog>
    </Section>
  );
}

function NewSourceForm({ onDone }: { onDone: (msg: string) => void }) {
  const [f, setF] = useState({ slug: "", publisher: "", domain: "", source_type: "official", note: "" });
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const s = await api.createSource({
        source_id: `admin-${f.slug.trim().toLowerCase()}`, publisher: f.publisher, domain: f.domain,
        source_type: f.source_type, note: f.note.trim() || null,
      });
      onDone(`Dodano źródło ${s.publisher} (${s.publishing_domain}). Teraz dodaj do niego dokumenty.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Section title="Nowe źródło ręczne">
      <form className="adm-form adm-card" onSubmit={submit}>
        {error && <Notice tone="danger">{error}</Notice>}
        <TextField label="Identyfikator" hint="Zostanie poprzedzony „admin-”. Małe litery, cyfry i myślniki." value={f.slug} required maxLength={70} onChange={(e) => setF({ ...f, slug: e.target.value })} />
        <TextField label="Instytucja" value={f.publisher} required maxLength={200} onChange={(e) => setF({ ...f, publisher: e.target.value })} />
        <TextField label="Domena" hint="Np. stat.gov.pl. Dokumenty muszą pochodzić z tej domeny (https)." value={f.domain} required onChange={(e) => setF({ ...f, domain: e.target.value })} />
        <Select label="Rodzaj" value={f.source_type} onChange={(e) => setF({ ...f, source_type: e.target.value })} options={TYPE_OPTIONS} />
        <TextArea label="Notatka" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        <Button type="submit" disabled={!f.slug || !f.publisher || !f.domain}>
          Dodaj źródło
        </Button>
      </form>
    </Section>
  );
}

function DocumentsView({ id }: { id: string }) {
  const [st, reload] = useAsync(() => api.documents(id), [id]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [withdrawing, setWithdrawing] = useState<Document | null>(null);
  const [note, setNote] = useState("");

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.addDocument(id, url.trim());
      setMsg({
        tone: r.action === "unchanged" ? "warning" : "success",
        text: r.action === "unchanged" ? `Ten dokument już jest w bazie: ${r.title}` : `Dodano: ${r.title}`,
      });
      setUrl("");
      reload();
    } catch (err) {
      setMsg({ tone: "danger", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    if (!withdrawing) return;
    const d = withdrawing;
    setWithdrawing(null);
    try {
      await api.withdraw(d.evidence_id, note);
      setMsg({ tone: "success", text: `Wycofano z wyszukiwania: ${d.title}` });
      setNote("");
      reload();
    } catch (err) {
      setMsg({ tone: "danger", text: err instanceof Error ? err.message : String(err) });
    }
  }

  async function restore(d: Document) {
    try {
      await api.restore(d.evidence_id);
      setMsg({ tone: "success", text: `Przywrócono: ${d.title}` });
      reload();
    } catch (err) {
      setMsg({ tone: "danger", text: err instanceof Error ? err.message : String(err) });
    }
  }

  return (
    <>
      <Breadcrumbs items={[{ label: "Źródła", href: "/zrodla" }, { label: "Dokumenty" }]} />
      <Loaded value={st} retry={reload}>
        {({ source, items }) => (
          <>
            <h1 className="app-h1">{source.admin_publisher ?? source.publisher}</h1>
            <p className="app-lead">
              Domena {source.publishing_domain} · {SOURCE_TYPE_PL[source.admin_source_type ?? source.source_type] ?? source.source_type}
              {!source.enabled && " · źródło wyłączone z wyszukiwania"}
            </p>
            {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
            <Section title="Dodaj dokument">
              <form className="adm-inline-form" onSubmit={add}>
                <TextField
                  label="Adres artykułu"
                  hint={`https:// w domenie ${source.publishing_domain}. Pobieramy zgodnie z robots.txt, z odstępem 3 s; blokad i CAPTCHA nie obchodzimy.`}
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  required
                />
                <Button type="submit" disabled={busy || !url.trim()} icon={busy ? "loader" : "download"}>
                  {busy ? "Pobieranie…" : "Pobierz i dodaj"}
                </Button>
              </form>
            </Section>
            <Section title={`Dokumenty (${items.length})`}>
              <DataTable
                label="Dokumenty źródła"
                rows={items}
                rowKey={(d) => d.evidence_id}
                empty="To źródło nie ma jeszcze dokumentów."
                columns={[
                  {
                    key: "t", label: "Tytuł", header: true,
                    render: (d) => (
                      <a className="mp-link" href={d.citation_url} target="_blank" rel="noopener noreferrer">
                        {d.title}
                        <Icon name="external-link" size={16} />
                        <span className="mp-sr"> (otwiera się w nowej karcie)</span>
                      </a>
                    ),
                  },
                  { key: "p", label: "Opublikowano", render: (d) => formatDateTime(d.published_at) },
                  { key: "f", label: "Pobrano", render: (d) => formatDateTime(d.fetched_at) },
                  { key: "v", label: "Wersja", num: true, render: (d) => d.version },
                  {
                    key: "s", label: "Stan",
                    render: (d) =>
                      d.withdrawn_at ? (
                        <>
                          <Badge tone="warning">wycofany {formatDateTime(d.withdrawn_at)}</Badge>
                          {d.withdrawn_note && <span className="app-muted"> · {d.withdrawn_note}</span>}
                        </>
                      ) : d.is_current ? (
                        <Badge tone="success">w wyszukiwaniu</Badge>
                      ) : (
                        <span className="app-muted">starsza wersja</span>
                      ),
                  },
                  {
                    key: "a", label: "Akcje",
                    render: (d) =>
                      d.withdrawn_at ? (
                        <Button variant="secondary" onClick={() => void restore(d)}>
                          Przywróć
                        </Button>
                      ) : (
                        <Button variant="secondary" className="adm-btn-danger" onClick={() => setWithdrawing(d)}>
                          Wycofaj
                        </Button>
                      ),
                  },
                ]}
              />
            </Section>
            <ConfirmDialog
              open={withdrawing !== null}
              title="Wycofać dokument z wyszukiwania?"
              confirmLabel="Wycofaj"
              danger
              onConfirm={() => void withdraw()}
              onCancel={() => setWithdrawing(null)}
            >
              <p>{withdrawing?.title}</p>
              <p className="app-muted">
                Dokument nie będzie cytowany w nowych odpowiedziach, a kolejne importy go nie przywrócą. Rekord zostaje w bazie,
                więc dawne cytaty dalej da się sprawdzić. Można to cofnąć.
              </p>
              <TextField label="Powód (opcjonalnie)" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            </ConfirmDialog>
          </>
        )}
      </Loaded>
    </>
  );
}
