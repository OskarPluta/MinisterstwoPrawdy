// Ministerstwo Prawdy design system: a typed React port of components/bundle.js.
// Markup and mp-* class names match the original one to one, so components.css applies unchanged.
// Props follow components/index.d.ts. One addition: NavLink can be a button (onClick, pressed),
// used for the "Wersja kontrastowa" toggle in the utility bar.
import { Fragment, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { ICONS, type IconName } from "./icons";

export type { IconName };
export type Tone = "info" | "success" | "warning" | "danger";
export interface NavLink {
  label: string;
  href: string;
  current?: boolean;
  /** Renders a <button> instead of a link (toggles). */
  onClick?: () => void;
  /** aria-pressed for toggle buttons. */
  pressed?: boolean;
}

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export const TONE_ICON: Record<Tone, IconName> = {
  info: "info",
  success: "circle-check",
  warning: "triangle-alert",
  danger: "circle-x",
};
export const TONE_WORD: Record<Tone, string> = { info: "Informacja", success: "Gotowe", warning: "Uwaga", danger: "Błąd" };

/* Icon: Lucide outline glyphs, 24px grid, 2px stroke, drawn in currentColor. */
export interface IconProps {
  name: IconName;
  size?: number;
  /** Accessible name; omit for decorative icons. */
  label?: string;
  className?: string;
}
export function Icon({ name, size = 20, label, className }: IconProps) {
  return (
    <svg
      className={cx("mp-icon", className)}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {ICONS[name].map(([Tag, attrs], i) => (
        <Tag key={i} {...attrs} />
      ))}
    </svg>
  );
}

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** primary = the page's one main action (uppercase, blue fill); secondary = outline; link = text-only. */
  variant?: "primary" | "secondary" | "link";
  size?: "md" | "lg";
  /** Renders an <a> instead of a <button>. */
  href?: string;
  /** Opens in a new tab, adds the external-link icon and a screen-reader note. */
  external?: boolean;
  icon?: IconName;
  iconPosition?: "start" | "end";
  /** Full-width (phones, narrow forms). */
  block?: boolean;
  children: ReactNode;
}
export function Button({
  variant = "primary",
  size = "md",
  href,
  external,
  icon,
  iconPosition = "end",
  block,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const name = icon ?? (external ? "external-link" : null);
  const ic = name ? <Icon name={name} size={size === "lg" ? 22 : 20} /> : null;
  const kids = (
    <>
      {iconPosition === "start" && ic}
      <span>{children}</span>
      {iconPosition !== "start" && ic}
      {external && <span className="mp-sr"> (otwiera się w nowej karcie)</span>}
    </>
  );
  const cls = cx("mp-btn", `mp-btn-${variant}`, size === "lg" && "mp-btn-lg", block && "mp-btn-block", className);
  if (href) {
    return (
      <a
        className={cls}
        href={href}
        id={rest.id}
        onClick={rest.onClick as React.MouseEventHandler<HTMLAnchorElement> | undefined}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      >
        {kids}
      </a>
    );
  }
  return (
    <button type={type} className={cls} {...rest}>
      {kids}
    </button>
  );
}

/** Service header: red top stripe, optional utility bar, wordmark, Search / Log-in actions, main nav. */
export interface SiteHeaderProps {
  serviceName?: string;
  tagline?: string;
  homeHref?: string;
  utilityLinks?: NavLink[];
  nav?: NavLink[];
  /** Search link target; pass null to hide Search. */
  searchHref?: string | null;
  loginHref?: string;
  loginLabel?: string;
  skipTo?: string;
}
export function SiteHeader({
  serviceName = "Ministerstwo Prawdy",
  tagline,
  homeHref = "/",
  utilityLinks = [],
  nav = [],
  searchHref = "#szukaj",
  loginHref,
  loginLabel = "Zaloguj się",
  skipTo = "#main",
}: SiteHeaderProps) {
  return (
    <header className="mp-header">
      <a className="mp-skip mp-focus" href={skipTo}>
        Przejdź do treści
      </a>
      {utilityLinks.length > 0 && (
        <div className="mp-utility">
          <div className="mp-container">
            {utilityLinks.map((l, i) =>
              l.onClick ? (
                <button key={i} type="button" className="mp-focus mp-utility-btn" onClick={l.onClick} aria-pressed={l.pressed}>
                  {l.label}
                </button>
              ) : (
                <a key={i} href={l.href} className="mp-focus">
                  {l.label}
                </a>
              ),
            )}
          </div>
        </div>
      )}
      <div className="mp-container mp-header-main">
        <a className="mp-brand mp-focus" href={homeHref}>
          <span className="mp-brand-name">{serviceName}</span>
          <span className="mp-brand-rule" aria-hidden="true" />
          {tagline && <span className="mp-brand-tag">{tagline}</span>}
        </a>
        <div className="mp-header-actions">
          {searchHref !== null && (
            <a className="mp-iconbtn mp-focus" href={searchHref}>
              <Icon name="search" />
              <span className="mp-iconbtn-label">Szukaj</span>
            </a>
          )}
          {loginHref && (
            <a className="mp-iconbtn mp-focus" href={loginHref}>
              <Icon name="user" />
              <span className="mp-iconbtn-label">{loginLabel}</span>
            </a>
          )}
        </div>
      </div>
      {nav.length > 0 && (
        <nav className="mp-nav" aria-label="Menu główne">
          <div className="mp-container">
            <ul>
              {nav.map((n, i) => (
                <li key={i}>
                  <a href={n.href} className="mp-focus" aria-current={n.current ? "page" : undefined}>
                    {n.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </nav>
      )}
    </header>
  );
}

/** Home icon, then each level; the last item is the current page (not a link). */
export interface BreadcrumbsProps {
  items: { label: string; href?: string }[];
  homeHref?: string;
}
export function Breadcrumbs({ items, homeHref = "/" }: BreadcrumbsProps) {
  return (
    <nav className="mp-crumbs" aria-label="Ścieżka nawigacji">
      <ol>
        <li>
          <a href={homeHref} className="mp-focus">
            <Icon name="house" size={18} />
            <span className="mp-sr">Strona główna</span>
          </a>
        </li>
        {items.map((it, i) => (
          <li key={i}>
            <Icon name="chevron-right" size={16} className="mp-sep" />
            {i === items.length - 1 ? (
              <span aria-current="page">{it.label}</span>
            ) : (
              <a href={it.href} className="mp-link">
                {it.label}
              </a>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Shared label / hint / error wiring for TextField and TextArea. */
export function useFieldIds(id: string | undefined, hint?: string, error?: string) {
  const auto = useId();
  const fid = id ?? `mp-field-${auto}`;
  const hintId = `${fid}-hint`;
  const errId = `${fid}-err`;
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(" ") || undefined;
  return { fid, hintId, errId, describedBy };
}

export function FieldMessages({ hint, error, hintId, errId }: { hint?: string; error?: string; hintId: string; errId: string }) {
  return (
    <>
      {hint && (
        <span className="mp-field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="mp-field-error" id={errId}>
          <Icon name="circle-x" size={18} />
          <span className="mp-sr">Błąd: </span>
          {error}
        </span>
      )}
    </>
  );
}

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
  width?: "sm" | "md" | "full";
}
export function TextField({ label, hint, error, width, className, id, ...rest }: TextFieldProps) {
  const { fid, hintId, errId, describedBy } = useFieldIds(id, hint, error);
  return (
    <div className={cx("mp-field", width && `mp-field-${width}`, error && "mp-field-invalid", className)}>
      <label className="mp-field-label" htmlFor={fid}>
        {label}
      </label>
      <FieldMessages hint={hint} error={error} hintId={hintId} errId={errId} />
      <input
        className="mp-field-input"
        id={fid}
        type="text"
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
    </div>
  );
}

/** Boxed message with tone colour, icon and a title word. */
export interface NoticeProps {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
  className?: string;
}
export function Notice({ tone = "info", title, children, className }: NoticeProps) {
  return (
    <div className={cx("mp-notice", `mp-tone-${tone}`, className)} role={tone === "danger" ? "alert" : "status"}>
      <Icon name={TONE_ICON[tone]} size={24} className="mp-notice-icon" />
      <div>
        <p className="mp-notice-title">{title ?? TONE_WORD[tone]}</p>
        {children && <div className="mp-notice-body">{children}</div>}
      </div>
    </div>
  );
}

/** A titled section of a service page. */
export interface InfoSectionProps {
  title: string;
  id?: string;
  level?: 2 | 3;
  children: ReactNode;
  className?: string;
}
export function InfoSection({ title, id, level = 2, children, className }: InfoSectionProps) {
  const Tag = level === 3 ? "h3" : "h2";
  const titleId = id ? `${id}-t` : undefined;
  return (
    <section className={cx("mp-section", className)} id={id} aria-labelledby={titleId}>
      <Tag className="mp-section-title" id={titleId}>
        {title}
      </Tag>
      <div className="mp-section-body">{children}</div>
    </section>
  );
}

/** Numbered "what to do" steps. */
export function ServiceSteps({ steps, className }: { steps: ReactNode[]; className?: string }) {
  return (
    <ol className={cx("mp-steps", className)}>
      {steps.map((s, i) => (
        <li key={i}>{s}</li>
      ))}
    </ol>
  );
}

/** The answer a check service returns: tone, kicker, title, key-value details, optional footer content. */
export interface StatusPanelProps {
  tone?: Tone;
  kicker?: string;
  title: string;
  details?: { label: string; value: ReactNode }[];
  children?: ReactNode;
  className?: string;
  /** id of the title, so focus can be moved to it after a check. */
  titleId?: string;
}
export function StatusPanel({ tone = "info", kicker, title, details = [], children, className, titleId }: StatusPanelProps) {
  return (
    <section className={cx("mp-status", `mp-tone-${tone}`, className)} aria-labelledby={titleId}>
      <div className="mp-status-head">
        <Icon name={TONE_ICON[tone]} size={40} className="mp-status-icon" />
        <div>
          <span className="mp-status-kicker">{kicker ?? TONE_WORD[tone]}</span>
          <h2 className="mp-status-title" id={titleId} tabIndex={titleId ? -1 : undefined}>
            {title}
          </h2>
        </div>
      </div>
      {details.length > 0 && (
        <dl className="mp-status-list">
          {details.map((d, i) => (
            <Fragment key={i}>
              <dt>{d.label}</dt>
              <dd>{d.value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {children && <div className="mp-status-foot">{children}</div>}
    </section>
  );
}

/** "Ostatnia aktualizacja" + publishing units, at the end of the content column. */
export function PageMeta({ updated, publishedBy = [], className }: { updated?: string; publishedBy?: string[]; className?: string }) {
  return (
    <div className={cx("mp-meta", className)}>
      {updated && (
        <span>
          Ostatnia aktualizacja: <strong>{updated}</strong>
        </span>
      )}
      {publishedBy.length > 0 && (
        <span>
          Materiał opublikowany przez: <strong>{publishedBy.join(", ")}</strong>
        </span>
      )}
    </div>
  );
}

export interface SiteFooterProps {
  name?: string;
  columns?: { title: string; links: { label: string; href: string }[] }[];
  bottomLinks?: { label: string; href: string }[];
  note?: string;
}
export function SiteFooter({ name = "Ministerstwo Prawdy", columns = [], bottomLinks = [], note = "" }: SiteFooterProps) {
  return (
    <footer className="mp-footer">
      <div className="mp-container">
        <p className="mp-footer-brand">{name}</p>
        {columns.length > 0 && (
          <div className="mp-footer-cols">
            {columns.map((c, i) => (
              <div key={i}>
                <h2 className="mp-footer-title">{c.title}</h2>
                <ul>
                  {c.links.map((l, j) => (
                    <li key={j}>
                      <a href={l.href}>{l.label}</a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        <div className="mp-footer-bottom">
          <span>{note}</span>
          {bottomLinks.length > 0 && (
            <ul>
              {bottomLinks.map((l, j) => (
                <li key={j}>
                  <a href={l.href}>{l.label}</a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </footer>
  );
}
