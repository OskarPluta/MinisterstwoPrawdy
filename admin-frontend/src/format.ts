// Display helpers. Times are stored in UTC and shown in Europe/Warsaw; costs stay in their own currency.
import type { Tone } from "../../frontend/src/design/components";
import type { Group, Money } from "./api";

const TZ = "Europe/Warsaw";

function parse(iso: string): Date | null {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) || !iso.includes("T");
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTime(iso: string | null | undefined): string {
  const d = iso ? parse(iso) : null;
  if (!d) return "—";
  return new Intl.DateTimeFormat("pl-PL", {
    timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(d);
}

/** "2026-11-22" (a Warsaw day from the API) -> "22.11". */
export function formatDay(day: string): string {
  const [, m, d] = day.split("-");
  return d && m ? `${d}.${m}` : day;
}

export const formatInt = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("pl-PL"));
export const formatPct = (x: number) => `${(x * 100).toLocaleString("pl-PL", { maximumFractionDigits: 1 })}%`;

export function formatMoney(m: Money | null | undefined): string {
  const parts = Object.entries(m ?? {}).map(
    ([cur, v]) => `${v.toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: v < 1 ? 4 : 2 })} ${cur}`,
  );
  return parts.length ? parts.join(" · ") : "—";
}

export function formatCost(amount: number | null, currency: string | null): string {
  return amount === null || !currency ? "—" : formatMoney({ [currency]: amount });
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toLocaleString("pl-PL", { maximumFractionDigits: 1 })} s`;
}

/** Danger only for technical failures: "no evidence" and other no-result outcomes are never red. */
export const GROUP_TONE: Record<Group, Tone> = { success: "success", no_result: "info", degraded: "warning", failure: "danger" };
export const GROUP_PL: Record<Group, string> = {
  success: "werdykt",
  no_result: "bez werdyktu",
  degraded: "częściowy błąd",
  failure: "błąd techniczny",
};
export const CHANNEL_PL: Record<string, string> = { web: "WWW", discord: "Discord", cli: "CLI", eval: "ewaluacja" };
export const SOURCE_TYPE_PL: Record<string, string> = {
  official: "Komunikaty",
  structured: "Dane strukturalne",
  factcheck: "Weryfikacje i bazy dezinformacji",
  secondary: "Źródło wtórne",
};
export const CALL_PL: Record<string, string> = { extract: "wyodrębnienie twierdzeń", verify: "weryfikacja", transcribe: "odczyt obrazu (OCR)" };

/** Today in Warsaw as YYYY-MM-DD, and `days` back. */
export function warsawDay(offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(d);
}
