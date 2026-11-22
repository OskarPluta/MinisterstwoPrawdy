// Download of one displayed result as JSON or CSV, built in the browser (no server call).
// Same format as the analyst export of the check log (backend/rag/src/rag/export.py): JSON
// `prawdowid-export` v1 and the same CSV columns, so files can be merged. Fields the browser does
// not know (check_ref, model, corpus version) stay empty. The check_id (the deletion key) is never
// written: a downloaded file may be forwarded.
import type { CheckResult, ClaimResult } from "./api";

export const EXPORT_FORMAT = "prawdowid-export";
export const EXPORT_VERSION = 1;
const LIST_SEP = " | ";
const NOTICE =
  "Wynik jednego sprawdzenia zapisany w przeglądarce. Dane osobowe w treści są zamaskowane, cytaty mają do 400 znaków i link do źródła.";

/** Mirrors CSV_COLUMNS in rag/export.py. */
export const CSV_COLUMNS = [
  "check_ref", "checked_at_warsaw", "checked_at_utc", "status", "claim_no", "claims_in_check",
  "claim", "verdict", "verdict_pl", "downgraded_from", "strength", "strength_pl", "strength_reasons",
  "known_disinformation", "conflict", "explanation", "notes", "citation_count", "citations_supports",
  "citations_contradicts", "citation_urls", "publishers", "message", "injection_detected", "shared",
  "model", "embedding_model", "corpus_version", "duration_ms",
] as const;

type Column = (typeof CSV_COLUMNS)[number];
type Cell = string | number | boolean | null | undefined;

/** Text a spreadsheet could run as a formula (= + - @ tab CR, also full-width) gets a leading apostrophe. */
const FORMULA_START = /^[=+\-@\t\r＝＋－＠]/;

export function safeCell(value: Cell): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  return FORMULA_START.test(value) ? "'" + value : value;
}

/** RFC 4180: quote cells with a comma, quote or line break; double inner quotes. */
function quote(cell: string): string {
  return /[",\r\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell;
}

/** UTC ISO 8601 (a value without an offset is UTC, as in timeutil.parse_ts) → Date, or null. */
function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function utcIso(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Wall clock in Europe/Warsaw: "YYYY-MM-DD HH:MM:SS" and the UTC offset in minutes. */
function warsaw(d: Date): { wall: string; offset: number } {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Warsaw", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(d);
  const p = (t: string) => parts.find((x) => x.type === t)?.value ?? "00";
  const wall = `${p("year")}-${p("month")}-${p("day")} ${p("hour")}:${p("minute")}:${p("second")}`;
  const asUtc = Date.UTC(+p("year"), +p("month") - 1, +p("day"), +p("hour"), +p("minute"), +p("second"));
  return { wall, offset: Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / 60000) };
}

function localIso(d: Date): string {
  const { wall, offset } = warsaw(d);
  const sign = offset < 0 ? "-" : "+";
  const abs = Math.abs(offset);
  return `${wall.replace(" ", "T")}${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

function claimRecord(c: ClaimResult) {
  return {
    claim: c.claim,
    verdict: c.verdict,
    verdict_pl: c.verdict_pl,
    downgraded_from: c.downgraded_from ?? null,
    explanation: c.explanation,
    strength: c.strength ? { level: c.strength.level, level_pl: c.strength.level_pl, reasons: [...c.strength.reasons] } : null,
    known_disinformation: Boolean(c.known_disinformation),
    conflict: Boolean(c.conflict),
    notes: [...c.notes],
    // Allowlist: passage_id is an internal database reference.
    citations: c.citations.map((s) => ({
      n: s.n, role: s.role, title: s.title, publisher: s.publisher, url: s.url, date_label: s.date_label,
      text: s.text, is_verbatim: s.is_verbatim, attribution: s.attribution ?? null,
    })),
  };
}

export interface ExportContext {
  /** When the check ran (UTC ISO 8601); the shared page knows it from the server. */
  checkedAt: string | null;
  /** true on a shared page; null when the browser does not know. */
  shared: boolean | null;
}

function checkRecord(res: CheckResult, ctx: ExportContext) {
  const t = parseUtc(ctx.checkedAt);
  return {
    check_ref: null,
    checked_at: t ? utcIso(t) : null,
    checked_at_local: t ? localIso(t) : null,
    status: res.status,
    message: res.message,
    injection_detected: Boolean(res.injection_detected),
    shared: ctx.shared,
    corpus_version: null,
    model: null,
    embedding_model: null,
    duration_ms: res.duration_ms,
    claims: res.claims.map(claimRecord),
  };
}

export function resultAsJson(res: CheckResult, ctx: ExportContext, now: Date = new Date()): string {
  const doc = {
    format: EXPORT_FORMAT,
    version: EXPORT_VERSION,
    exported_at: utcIso(now),
    timezone: "Europe/Warsaw",
    filters: null,
    count: 1,
    notice: NOTICE,
    checks: [checkRecord(res, ctx)],
  };
  return JSON.stringify(doc, null, 2) + "\n";
}

const join = (values: (string | null | undefined)[]) => values.filter((v) => v).join(LIST_SEP);

/** One row per claim (a result without claims gives one row), UTF-8 with BOM, CRLF line ends. */
export function resultAsCsv(res: CheckResult, ctx: ExportContext): string {
  const rec = checkRecord(res, ctx);
  const t = parseUtc(rec.checked_at);
  const base: Partial<Record<Column, Cell>> = {
    check_ref: rec.check_ref, checked_at_warsaw: t ? warsaw(t).wall : null, checked_at_utc: rec.checked_at,
    status: rec.status, claims_in_check: rec.claims.length, message: rec.message,
    injection_detected: rec.injection_detected, shared: rec.shared, model: rec.model,
    embedding_model: rec.embedding_model, corpus_version: rec.corpus_version, duration_ms: rec.duration_ms,
  };
  const rows: Partial<Record<Column, Cell>>[] = rec.claims.length ? [] : [base];
  rec.claims.forEach((c, i) => {
    rows.push({
      ...base,
      claim_no: i + 1,
      claim: c.claim,
      verdict: c.verdict,
      verdict_pl: c.verdict_pl,
      downgraded_from: c.downgraded_from,
      strength: c.strength?.level,
      strength_pl: c.strength?.level_pl,
      strength_reasons: join(c.strength?.reasons ?? []),
      known_disinformation: c.known_disinformation,
      conflict: c.conflict,
      explanation: c.explanation,
      notes: join(c.notes),
      citation_count: c.citations.length,
      citations_supports: c.citations.filter((s) => s.role === "supports").length,
      citations_contradicts: c.citations.filter((s) => s.role === "contradicts").length,
      citation_urls: join(c.citations.map((s) => s.url)),
      publishers: join([...new Set(c.citations.map((s) => s.publisher))]),
    });
  });
  const lines = [CSV_COLUMNS.join(","), ...rows.map((r) => CSV_COLUMNS.map((k) => quote(safeCell(r[k]))).join(","))];
  return "﻿" + lines.join("\r\n") + "\r\n";
}

/** prawdowid-wynik-<YYYY-MM-DD in Warsaw>.<ext> */
export function exportFilename(ext: "json" | "csv", now: Date = new Date()): string {
  return `prawdowid-wynik-${warsaw(now).wall.slice(0, 10)}.${ext}`;
}

/** Save text as a file through a temporary object URL. */
export function downloadText(filename: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.hidden = true;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
