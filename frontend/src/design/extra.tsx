// Components the product needs that the design system does not ship. Built from the same tokens
// and rules: square shapes, 1px hairlines, status always as icon + word, no coloured left border.
import { useEffect, useRef, useState, type TextareaHTMLAttributes } from "react";
import type { Citation, InputSummary, Role, Source } from "../api";
import { formatDate } from "../api";
import { Button, cx, FieldMessages, Icon, useFieldIds, type IconName } from "./components";

/** Multi-line TextField: same label, hint and error wiring, plus a character counter. */
export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
  hint?: string;
  error?: string;
  /** Shows "n / max znaków" under the field; does not block typing (validate on submit). */
  maxChars?: number;
}
export function TextArea({ label, hint, error, maxChars, className, id, value, ...rest }: TextAreaProps) {
  const { fid, hintId, errId, describedBy } = useFieldIds(id, hint, error);
  const count = typeof value === "string" ? value.length : 0;
  const counterId = `${fid}-count`;
  return (
    <div className={cx("mp-field", error && "mp-field-invalid", className)}>
      <label className="mp-field-label" htmlFor={fid}>
        {label}
      </label>
      <FieldMessages hint={hint} error={error} hintId={hintId} errId={errId} />
      <textarea
        className="mp-field-input mp-textarea"
        id={fid}
        value={value}
        aria-describedby={[describedBy, maxChars ? counterId : null].filter(Boolean).join(" ") || undefined}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
      {maxChars !== undefined && (
        <span className={cx("mp-field-hint mp-counter", count > maxChars && "mp-counter-over")} id={counterId}>
          {count.toLocaleString("pl-PL")} / {maxChars.toLocaleString("pl-PL")} znaków
        </span>
      )}
    </div>
  );
}

const ROLE: Record<Role, { word: string; icon: IconName; cls: string }> = {
  supports: { word: "potwierdza", icon: "circle-check", cls: "mp-role-supports" },
  contradicts: { word: "przeczy", icon: "circle-x", cls: "mp-role-contradicts" },
  context: { word: "kontekst", icon: "info", cls: "mp-role-context" },
};

/** Numbered sources under a verdict. Links come only from citation.url (the database). */
export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mp-cites">
      <h3 className="mp-cites-title">Źródła</h3>
      <ol className="mp-cites-list">
        {citations.map((c) => {
          const role = ROLE[c.role] ?? ROLE.context;
          return (
            <li key={c.n} className="mp-cite">
              <div className="mp-cite-head">
                <span className="mp-cite-n" aria-hidden="true">
                  {c.n}
                </span>
                <div className="mp-cite-meta">
                  <p className="mp-cite-title">
                    <span className="mp-sr">Źródło {c.n}: </span>
                    {c.url ? (
                      <a className="mp-link" href={c.url} target="_blank" rel="noopener noreferrer">
                        {c.title}
                        <Icon name="external-link" size={16} className="mp-cite-ext" />
                        <span className="mp-sr"> (otwiera się w nowej karcie)</span>
                      </a>
                    ) : (
                      c.title
                    )}
                  </p>
                  <p className="mp-cite-sub">
                    {c.publisher} · {c.date_label}
                  </p>
                </div>
                <span className={cx("mp-role", role.cls)}>
                  <Icon name={role.icon} size={18} />
                  {role.word}
                </span>
              </div>
              {c.is_verbatim ? (
                <blockquote className="mp-cite-quote">{c.text}</blockquote>
              ) : (
                <div className="mp-cite-quote mp-cite-data">
                  <span className="mp-cite-data-label">Dane ze źródła: </span>
                  {c.text}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const SOURCE_TYPE: Record<string, string> = {
  official: "Komunikaty",
  structured: "Dane strukturalne",
  factcheck: "Weryfikacje i bazy dezinformacji",
};

/** Source catalog for audit. Scrolls horizontally inside its own box on phones. */
export function SourcesTable({ sources }: { sources: Source[] }) {
  return (
    <div className="mp-table-wrap" role="region" aria-label="Lista źródeł" tabIndex={0}>
      <table className="mp-table">
        <thead>
          <tr>
            <th scope="col">Instytucja</th>
            <th scope="col">Domena</th>
            <th scope="col">Rodzaj</th>
            <th scope="col" className="mp-num">
              Rekordy
            </th>
            <th scope="col">Ostatnia aktualizacja</th>
            <th scope="col">Stan</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.source_id}>
              <th scope="row">{s.publisher}</th>
              <td data-label="Domena">
                {s.citation_url?.startsWith("https://") ? (
                  <a className="mp-link" href={s.citation_url} target="_blank" rel="noopener noreferrer">
                    {s.publishing_domain}
                    <span className="mp-sr"> (otwiera się w nowej karcie)</span>
                  </a>
                ) : (
                  s.publishing_domain
                )}
              </td>
              <td data-label="Rodzaj">{SOURCE_TYPE[s.source_type] ?? s.source_type}</td>
              <td className="mp-num" data-label="Rekordy">{s.current_records.toLocaleString("pl-PL")}</td>
              <td data-label="Ostatnia aktualizacja">{formatDate(s.last_success_at)}</td>
              <td data-label="Stan">
                {s.last_error ? (
                  <span className="mp-state mp-state-warning">
                    <Icon name="triangle-alert" size={18} />
                    Ostatnie pobranie nieudane
                  </span>
                ) : s.last_success_at ? (
                  <span className="mp-state mp-state-success">
                    <Icon name="circle-check" size={18} />
                    Pobrano poprawnie
                  </span>
                ) : (
                  <span className="mp-state">
                    <Icon name="info" size={18} />
                    Brak danych o pobraniu
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} kB`;
  return `${(bytes / 1_000_000).toLocaleString("pl-PL", { maximumFractionDigits: 1 })} MB`;
}

/** Small preview so the person can confirm they attached the right picture. */
function Thumb({ file }: { file: File }) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    if (!file.type.startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  if (!src) return <Icon name="file-text" size={28} className="mp-file-icon" />;
  return <img className="mp-file-thumb" src={src} alt="" />;
}

export interface FilePickerProps {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  files: File[];
  accept: string;
  /** Hidden when the limit is reached. */
  canAdd: boolean;
  onAdd: (files: File[]) => void;
  onRemove: (index: number) => void;
}

/** File field: a secondary button opens the system picker; chosen files are listed with a remove button. */
export function FilePicker({ id, label, hint, error, files, accept, canAdd, onAdd, onRemove }: FilePickerProps) {
  const { fid, hintId, errId, describedBy } = useFieldIds(id, hint, error);
  const input = useRef<HTMLInputElement>(null);
  const labelId = `${fid}-label`;
  return (
    <div className={cx("mp-field mp-files", error && "mp-field-invalid")} role="group" aria-labelledby={labelId}>
      <span className="mp-field-label" id={labelId}>
        {label}
      </span>
      <FieldMessages hint={hint} error={error} hintId={hintId} errId={errId} />
      <input
        ref={input}
        id={fid}
        type="file"
        className="mp-sr"
        tabIndex={-1}
        aria-hidden="true"
        multiple
        accept={accept}
        onChange={(e) => {
          onAdd(Array.from(e.target.files ?? []));
          e.target.value = ""; // the same file can be chosen again after removing it
        }}
      />
      {files.length > 0 && (
        <ul className="mp-files-list" aria-label="Dodane pliki">
          {files.map((f, i) => (
            <li key={`${f.name}-${f.size}-${i}`} className="mp-file">
              <Thumb file={f} />
              <span className="mp-file-name">
                {f.name}
                <span className="mp-file-size"> · {formatSize(f.size)}</span>
              </span>
              <Button variant="link" onClick={() => onRemove(i)} aria-label={`Usuń plik ${f.name}`}>
                Usuń
              </Button>
            </li>
          ))}
        </ul>
      )}
      {canAdd && (
        <div>
          <Button variant="secondary" icon="file-text" iconPosition="start" onClick={() => input.current?.click()} aria-describedby={describedBy}>
            {files.length ? "Dodaj kolejny plik" : "Dodaj zdjęcie lub plik PDF"}
          </Button>
        </div>
      )}
    </div>
  );
}

/** What was read from each link, video, image or PDF, and what could not be done. Plain text only:
 *  labels are user input (no links), `text` is OCR or page text. */
export function MaterialList({ inputs, headingLevel = 3 }: { inputs: InputSummary[]; headingLevel?: 2 | 3 }) {
  if (inputs.length === 0) return null;
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className="mp-material" aria-labelledby="material-t">
      <H id="material-t" className="mp-material-title">
        Co odczytaliśmy z przesłanego materiału
      </H>
      <p className="mp-material-lead">
        Materiał to to, co sprawdzamy, a nie dowód. Ocenę opieramy tylko na źródłach z naszej bazy.
      </p>
      <ol className="mp-material-list">
        {inputs.map((m) => (
          <li key={m.id} className="mp-material-item">
            <div className="mp-material-head">
              <span className="mp-material-id" aria-hidden="true">
                {m.id}
              </span>
              <div className="mp-material-meta">
                <p className="mp-material-kind">
                  <span className="mp-sr">Materiał {m.id}: </span>
                  {m.kind_pl}
                </p>
                <p className="mp-material-label">{m.label}</p>
                {m.date_label && <p className="mp-material-date">{m.date_label}</p>}
              </div>
              {m.readable ? (
                <span className="mp-state mp-state-success">
                  <Icon name="circle-check" size={18} />
                  Odczytano
                </span>
              ) : (
                <span className="mp-state mp-state-warning">
                  <Icon name="triangle-alert" size={18} />
                  Nie odczytano
                </span>
              )}
            </div>
            {m.notes.length > 0 && (
              <ul className="mp-material-notes">
                {m.notes.map((n, i) => (
                  <li key={i}>
                    <Icon name="info" size={18} />
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            )}
            {m.limitations.length > 0 && (
              <div className="mp-material-limits">
                <p className="mp-material-limits-title">Ograniczenia</p>
                <ul>
                  {m.limitations.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </div>
            )}
            {m.readable && (
              <details className="mp-material-text">
                <summary>Pokaż odczytany tekst</summary>
                <pre>{m.text}</pre>
              </details>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
