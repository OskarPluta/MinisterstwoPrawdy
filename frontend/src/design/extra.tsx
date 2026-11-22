// Components the product needs that the design system does not ship. Built from the same tokens
// and rules: square shapes, 1px hairlines, status always as icon + word, no coloured left border.
import type { TextareaHTMLAttributes } from "react";
import type { Citation, Role, Source } from "../api";
import { formatDate } from "../api";
import { cx, FieldMessages, Icon, useFieldIds, type IconName } from "./components";

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
              {c.attribution && <p className="mp-cite-credit">{c.attribution}</p>}
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
            <th scope="col">Licencja</th>
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
              <td data-label="Licencja">
                {s.license ?? "nie określono"}
                {s.terms_url?.startsWith("https://") && (
                  <>
                    {" "}
                    <a className="mp-link" href={s.terms_url} target="_blank" rel="noopener noreferrer">
                      warunki
                      <span className="mp-sr"> korzystania ze źródła {s.publisher} (otwiera się w nowej karcie)</span>
                    </a>
                  </>
                )}
              </td>
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
