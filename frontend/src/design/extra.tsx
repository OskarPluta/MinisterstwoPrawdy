// Components the product needs that the design system does not ship. Built from the same tokens
// and rules: 1px hairlines, status always as icon + word, no coloured left border.
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import type { Citation, InputSummary, Role, Source } from "../api";
import { formatDate } from "../api";
import { cx, Icon, type IconName } from "./components";

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
  if (!src)
    return (
      <span className="mp-chip-icon">
        <Icon name="file-text" size={20} />
      </span>
    );
  return <img className="mp-chip-thumb" src={src} alt="" onError={() => setSrc(undefined)} />;
}

export interface ComposerProps {
  id: string;
  /** Accessible name of the text box (visually hidden: the page heading says what to do). */
  label: string;
  placeholder: string;
  /** Short help under the box. */
  hint: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  /** The counter appears only near the limit; it does not block typing (validate on submit). */
  maxChars: number;
  files: File[];
  accept: string;
  /** The attach button is hidden when the file limit is reached. */
  canAdd: boolean;
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  busy: boolean;
  submitLabel: string;
}

/** Message box: grows with the text, Enter submits the surrounding form (Shift+Enter is a new line).
 *  Files can be attached with the button, pasted (Ctrl+V) or dropped onto the box. */
export function Composer({
  id,
  label,
  placeholder,
  hint,
  value,
  onChange,
  error,
  maxChars,
  files,
  accept,
  canAdd,
  onAddFiles,
  onRemoveFile,
  busy,
  submitLabel,
}: ComposerProps) {
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const countId = `${id}-count`;
  const showCount = value.length > maxChars * 0.8;

  // Height follows the content; CSS max-height caps it and the box scrolls from there.
  const fit = useCallback(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);
  useLayoutEffect(fit, [value, fit]);
  useEffect(() => {
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [fit]);

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // isComposing: Enter that confirms an IME candidate must not send.
    if (e.key !== "Enter" || e.shiftKey || e.altKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }

  /** Ctrl+V of a screenshot. Text from Word or a web page also carries a picture of itself: keep only the text then. */
  function onPaste(e: ClipboardEvent) {
    const pasted = Array.from(e.clipboardData.files);
    if (pasted.length && !e.clipboardData.types.includes("text/plain")) {
      e.preventDefault();
      onAddFiles(pasted);
    }
  }

  function onDragOver(e: DragEvent) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDragging(true);
  }

  function onDrop(e: DragEvent) {
    setDragging(false);
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    onAddFiles(Array.from(e.dataTransfer.files));
  }

  return (
    <div className="mp-composer-wrap">
      <div
        className={cx("mp-composer", error && "mp-composer-invalid", dragging && "mp-composer-drag")}
        onPaste={onPaste}
        onDragOver={onDragOver}
        onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setDragging(false)}
        onDrop={onDrop}
        // A click on the box's padding or bar puts the cursor in the text, like in a chat window.
        onMouseDown={(e) => {
          if (e.target === e.currentTarget || (e.target as Element).classList.contains("mp-composer-bar")) {
            e.preventDefault();
            area.current?.focus();
          }
        }}
      >
        <label className="mp-sr" htmlFor={id}>
          {label}
        </label>
        {files.length > 0 && (
          <ul className="mp-chips" aria-label="Dodane pliki">
            {files.map((f, i) => (
              <li key={`${f.name}-${f.size}-${f.lastModified}`} className="mp-chip">
                <Thumb file={f} />
                <span className="mp-chip-name">{f.name}</span>
                <span className="mp-chip-size">{formatSize(f.size)}</span>
                <button type="button" className="mp-chip-x" onClick={() => onRemoveFile(i)} aria-label={`Usuń plik ${f.name}`}>
                  <Icon name="x" size={18} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          ref={area}
          id={id}
          className="mp-composer-input"
          rows={2}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          enterKeyHint="send"
          aria-describedby={[hintId, error ? errId : null, showCount ? countId : null].filter(Boolean).join(" ")}
          aria-invalid={error ? true : undefined}
        />
        <input
          ref={picker}
          type="file"
          className="mp-sr"
          tabIndex={-1}
          aria-hidden="true"
          multiple
          accept={accept}
          onChange={(e) => {
            onAddFiles(Array.from(e.target.files ?? []));
            e.target.value = ""; // the same file can be chosen again after removing it
          }}
        />
        <div className="mp-composer-bar">
          {canAdd && (
            <button type="button" className="mp-composer-attach" onClick={() => picker.current?.click()}>
              <Icon name="paperclip" size={20} />
              <span>Dodaj plik</span>
            </button>
          )}
          <span className={cx("mp-composer-count", value.length > maxChars && "mp-counter-over")} id={countId}>
            {showCount && `${value.length.toLocaleString("pl-PL")} / ${maxChars.toLocaleString("pl-PL")}`}
          </span>
          <button type="submit" className="mp-btn mp-btn-primary mp-composer-send" aria-disabled={busy || undefined}>
            <span>{submitLabel}</span>
            <Icon name={busy ? "loader" : "arrow-right"} size={20} className={busy ? "mp-spin" : undefined} />
          </button>
        </div>
      </div>
      {error && (
        <p className="mp-field-error mp-composer-error" id={errId}>
          <Icon name="circle-x" size={18} />
          <span className="mp-sr">Błąd: </span>
          {error}
        </p>
      )}
      <p className="mp-composer-hint" id={hintId}>
        {hint}
      </p>
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
