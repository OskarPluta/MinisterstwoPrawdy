import { useState, type FormEvent } from "react";
import { Button, Notice, TextField } from "../../../frontend/src/design/components";
import { api, type TrustedDomain } from "../api";
import type { PageProps } from "../App";
import { formatDateTime } from "../format";
import { Badge, ConfirmDialog, DataTable, Loaded, Section, useAsync } from "../ui";

export function DomainsPage(_: PageProps) {
  const [st, reload] = useAsync(() => api.domains(), []);
  const [domain, setDomain] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [removing, setRemoving] = useState<TrustedDomain | null>(null);

  async function run(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      setMsg({ tone: "success", text: ok });
      reload();
    } catch (e) {
      setMsg({ tone: "danger", text: e instanceof Error ? e.message : String(e) });
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const r = await api.saveDomain(domain, note.trim() || null);
      setDomain("");
      setNote("");
      return r;
    }, "Dodano domenę.");
  }

  return (
    <>
      <h1 className="app-h1">Domeny weryfikatorów</h1>
      <p className="app-lead">
        Wyszukiwanie na żywo przyjmuje weryfikacje (ClaimReview z Google Fact Check) tylko z tych domen; subdomeny też pasują.
        Zmiana działa od następnego sprawdzenia.
      </p>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <Section title="Dodaj domenę">
        <form className="adm-inline-form" onSubmit={add}>
          <TextField label="Domena" hint="Np. demagog.org.pl (bez https:// i ścieżki)." value={domain} required onChange={(e) => setDomain(e.target.value)} />
          <TextField label="Notatka" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          <Button type="submit" disabled={!domain.trim()}>
            Dodaj
          </Button>
        </form>
      </Section>
      <Loaded value={st} retry={reload}>
        {({ items }) => (
          <Section title={`Zaufane domeny (${items.filter((d) => d.enabled).length} włączonych)`}>
            <DataTable
              label="Zaufane domeny"
              rows={items}
              rowKey={(d) => d.domain}
              columns={[
                { key: "d", label: "Domena", header: true, render: (d) => <span className="adm-mono">{d.domain}</span> },
                { key: "s", label: "Stan", render: (d) => (d.enabled ? <Badge tone="success">włączona</Badge> : <Badge tone="warning">wyłączona</Badge>) },
                { key: "n", label: "Notatka", render: (d) => d.note ?? "—" },
                { key: "t", label: "Dodano", render: (d) => formatDateTime(d.added_at) },
                {
                  key: "a", label: "Akcje",
                  render: (d) => (
                    <div className="adm-actions">
                      <Button variant="secondary" onClick={() => void run(() => api.saveDomain(d.domain, d.note, !d.enabled), d.enabled ? "Wyłączono domenę." : "Włączono domenę.")}>
                        {d.enabled ? "Wyłącz" : "Włącz"}
                      </Button>
                      <Button variant="link" onClick={() => setRemoving(d)}>
                        Usuń
                      </Button>
                    </div>
                  ),
                },
              ]}
            />
          </Section>
        )}
      </Loaded>
      <ConfirmDialog
        open={removing !== null}
        title={`Usunąć domenę ${removing?.domain ?? ""}?`}
        confirmLabel="Usuń domenę"
        danger
        onConfirm={() => {
          const d = removing;
          setRemoving(null);
          if (d) void run(() => api.deleteDomain(d.domain), "Usunięto domenę.");
        }}
        onCancel={() => setRemoving(null)}
      >
        <p>Weryfikacje z tej domeny przestaną być przyjmowane. Już zapisane rekordy zostają w bazie.</p>
      </ConfirmDialog>
    </>
  );
}
