// Admin UI pieces built on the Ministerstwo Prawdy design system (../../frontend/src/design).
// Text from the model or the user is always rendered as plain text, never as HTML or Markdown.
import { useCallback, useEffect, useRef, useState, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { Button, FieldMessages, Icon, Notice, cx, useFieldIds, type IconName, type Tone } from "../../frontend/src/design/components";

// ---- data loading ------------------------------------------------------------------------------

export type Async<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "failed"; error: string };

/** Runs `load` on mount and whenever `deps` change; `reload` runs it again. Stale answers are dropped. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): [Async<T>, () => void] {
  const [st, setSt] = useState<Async<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    load().then(
      (data) => live && setSt({ state: "ok", data }),
      (e: unknown) => live && setSt({ state: "failed", error: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [st, useCallback(() => setTick((t) => t + 1), [])];
}

export function Loading({ label = "Wczytywanie…" }: { label?: string }) {
  return (
    <p className="adm-loading" role="status">
      <Icon name="loader" className="mp-spin" /> {label}
    </p>
  );
}

export function Failed({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <Notice tone="danger" title="Nie udało się wczytać danych">
      <p>{error}</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Spróbuj ponownie
        </Button>
      )}
    </Notice>
  );
}

/** Renders the loaded value, or the loading / error state. */
export function Loaded<T>({ value, retry, children }: { value: Async<T>; retry?: () => void; children: (data: T) => ReactNode }) {
  if (value.state === "loading") return <Loading />;
  if (value.state === "failed") return <Failed error={value.error} onRetry={retry} />;
  return <>{children(value.data)}</>;
}

// ---- small display pieces --------------------------------------------------------------------------

const BADGE_ICON: Record<Tone, IconName> = { success: "circle-check", info: "info", warning: "triangle-alert", danger: "circle-x" };

/** Status word with an icon (colour is never the only signal). */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={cx("mp-state", `adm-badge-${tone}`)}>
      <Icon name={BADGE_ICON[tone]} size={18} />
      {children}
    </span>
  );
}

export function StatTiles({ items }: { items: { label: string; value: ReactNode; hint?: string }[] }) {
  return (
    <dl className="app-totals adm-tiles">
      {items.map((it) => (
        <div key={it.label}>
          <dt>{it.label}</dt>
          <dd>{it.value}</dd>
          {it.hint && <p className="adm-tile-hint">{it.hint}</p>}
        </div>
      ))}
    </dl>
  );
}

/** Plain text block (user / model text): white-space kept, never parsed as markup. */
export function TextBlock({ children, className }: { children: string; className?: string }) {
  return <pre className={cx("adm-text", className)}>{children}</pre>;
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="adm-section">
      <div className="adm-section-head">
        <h2 className="mp-section-title">{title}</h2>
        {actions && <div className="adm-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

// ---- tables ----------------------------------------------------------------------------------------

export interface Column<T> {
  key: string;
  label: string;
  num?: boolean;
  /** The row header cell (first column on phones). */
  header?: boolean;
  render: (row: T) => ReactNode;
}

/** Generic table on the design system's .mp-table (stacked cards on phones, labels from data-label). */
export function DataTable<T>({ label, columns, rows, rowKey, empty = "Brak pozycji." }: {
  label: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  empty?: string;
}) {
  if (!rows.length) return <p className="app-muted">{empty}</p>;
  return (
    <div className="mp-table-wrap" role="region" aria-label={label} tabIndex={0}>
      <table className="mp-table adm-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.num ? "mp-num" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) =>
                c.header ? (
                  <th key={c.key} scope="row">
                    {c.render(r)}
                  </th>
                ) : (
                  <td key={c.key} className={c.num ? "mp-num" : undefined} data-label={c.label}>
                    {c.render(r)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- form controls (same markup and classes as the design system's TextField) ---------------------------

export function Select({ label, hint, error, options, className, id, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  hint?: string;
  error?: string;
  options: { value: string; label: string }[];
}) {
  const { fid, hintId, errId, describedBy } = useFieldIds(id, hint, error);
  return (
    <div className={cx("mp-field", error && "mp-field-invalid", className)}>
      <label className="mp-field-label" htmlFor={fid}>
        {label}
      </label>
      <FieldMessages hint={hint} error={error} hintId={hintId} errId={errId} />
      <select className="mp-field-input adm-select" id={fid} aria-describedby={describedBy} {...rest}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function TextArea({ label, hint, error, className, id, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const { fid, hintId, errId, describedBy } = useFieldIds(id, hint, error);
  return (
    <div className={cx("mp-field", error && "mp-field-invalid", className)}>
      <label className="mp-field-label" htmlFor={fid}>
        {label}
      </label>
      <FieldMessages hint={hint} error={error} hintId={hintId} errId={errId} />
      <textarea className="mp-field-input adm-textarea" id={fid} aria-describedby={describedBy} {...rest} />
    </div>
  );
}

export function Checkbox({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="adm-check">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

// ---- confirmation dialog ---------------------------------------------------------------------------

/** Native modal <dialog>: focus is trapped by the browser, Esc cancels. */
export function ConfirmDialog({ open, title, children, confirmLabel, danger, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="adm-dialog" onCancel={(e) => (e.preventDefault(), onCancel())} aria-labelledby="adm-dialog-title">
      <h2 id="adm-dialog-title" className="adm-dialog-title">
        {title}
      </h2>
      <div className="adm-dialog-body">{children}</div>
      <div className="adm-actions">
        <Button variant={danger ? "secondary" : "primary"} className={danger ? "adm-btn-danger" : undefined} onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button variant="link" onClick={onCancel}>
          Anuluj
        </Button>
      </div>
    </dialog>
  );
}

// ---- chart -----------------------------------------------------------------------------------------

export interface BarSeries {
  key: string;
  label: string;
  tone: Tone | "muted";
}

/** Stacked bars per day as inline SVG, with a legend and a text summary for screen readers. */
export function StackedBars({ data, series, label }: {
  data: { label: string; values: Record<string, number> }[];
  series: BarSeries[];
  label: string;
}) {
  const totals = data.map((d) => series.reduce((s, x) => s + (d.values[x.key] ?? 0), 0));
  const max = Math.max(1, ...totals);
  const W = 720;
  const H = 180;
  const bw = W / Math.max(1, data.length);
  const every = data.length <= 16 ? 1 : Math.ceil(data.length / 12);
  const summary = `${label}: ${data.map((d, i) => `${d.label} ${totals[i]}`).join(", ")}`;
  return (
    <figure className="adm-chart">
      {/* Bars stretch to the width; labels are HTML below, so text is never distorted. */}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} preserveAspectRatio="none">
        <line x1="0" x2={W} y1={H - 0.5} y2={H - 0.5} className="adm-chart-axis" />
        {data.map((d, i) => {
          let y = H;
          return (
            <g key={d.label}>
              {series.map((s) => {
                const v = d.values[s.key] ?? 0;
                const h = (v / max) * (H - 6);
                y -= h;
                return v ? <rect key={s.key} x={i * bw + bw * 0.15} y={y} width={bw * 0.7} height={h} className={`adm-fill-${s.tone}`} /> : null;
              })}
            </g>
          );
        })}
      </svg>
      <div className="adm-chart-labels" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${Math.max(1, data.length)}, 1fr)` }}>
        {data.map((d, i) => (
          <span key={d.label}>{i % every === 0 ? d.label : ""}</span>
        ))}
      </div>
      <figcaption className="adm-legend">
        {series.map((s) => (
          <span key={s.key}>
            <span className={`adm-swatch adm-fill-${s.tone}`} aria-hidden="true" />
            {s.label}
          </span>
        ))}
        <span className="app-muted">najwyższy słupek: {max.toLocaleString("pl-PL")}</span>
      </figcaption>
    </figure>
  );
}

// ---- date range ----------------------------------------------------------------------------------

export function DateRange({ from, to, onChange }: { from: string; to: string; onChange: (from: string, to: string) => void }) {
  return (
    <div className="adm-filters" role="group" aria-label="Zakres dat">
      <div className="mp-field">
        <label className="mp-field-label" htmlFor="adm-from">
          Od
        </label>
        <input id="adm-from" className="mp-field-input" type="date" value={from} max={to} onChange={(e) => onChange(e.target.value, to)} />
      </div>
      <div className="mp-field">
        <label className="mp-field-label" htmlFor="adm-to">
          Do
        </label>
        <input id="adm-to" className="mp-field-input" type="date" value={to} min={from} onChange={(e) => onChange(from, e.target.value)} />
      </div>
    </div>
  );
}
