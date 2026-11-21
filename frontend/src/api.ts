// Contract with backend/api (FastAPI). Types mirror the dataclasses in backend/rag/src/rag/verify.py
// (CheckResult, ClaimResult, Citation) minus the internal fields the API strips.

export type CheckStatus = "ok" | "needs_clarification" | "out_of_scope" | "too_long" | "error";
export type Verdict = "true" | "false" | "misleading" | "insufficient" | "unverifiable" | "error";
export type Role = "supports" | "contradicts" | "context";

export interface Citation {
  n: number;
  passage_id: string;
  role: Role;
  title: string;
  publisher: string;
  /** From the database only (never from model text); null when the stored URL is not http(s). */
  url: string | null;
  /** DD.MM.RRRR, Europe/Warsaw. */
  date_label: string;
  text: string;
  /** false: structured data rendered by a fixed template ("Dane ze źródła"), not a quote. */
  is_verbatim: boolean;
}

export interface ClaimResult {
  claim: string;
  verdict: Verdict;
  verdict_pl: string;
  /** Written by the model: plain text, never rendered as HTML or Markdown. */
  explanation: string;
  citations: Citation[];
  notes: string[];
  downgraded_from: string | null;
  conflict: boolean;
}

export interface CheckResult {
  check_id: string;
  status: CheckStatus;
  message: string | null;
  claims: ClaimResult[];
  injection_detected: boolean;
  duration_ms: number;
}

export interface Source {
  source_id: string;
  publisher: string;
  publishing_domain: string;
  source_type: "official" | "structured" | string;
  citation_url: string | null;
  terms_url: string | null;
  /** UTC, ISO 8601. */
  last_success_at: string | null;
  last_error: string | null;
  current_records: number;
}

export interface CorpusTotals {
  distinct_records: number;
  current_records: number;
  evidence_versions: number;
  publishing_domains: number;
  source_types: number;
  passages_searchable: number;
}

export interface SourcesResponse {
  totals: CorpusTotals;
  sources: Source[];
}

/** Same wording as MSG_OUTAGE in verify.py, for when the backend cannot be reached at all. */
export const MSG_OUTAGE = "Usługa weryfikacji jest chwilowo niedostępna. Spróbuj ponownie później.";
/** MAX_INPUT_CHARS in verify.py. */
export const MAX_INPUT_CHARS = 10_000;

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(MSG_OUTAGE);
  }
  if (!res.ok) {
    // 502 from the frontend proxy, 503 when the LLM is not configured, 5xx on crashes.
    let detail = MSG_OUTAGE;
    if (res.status < 500) {
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* keep the generic message */
      }
    }
    throw new ApiError(detail);
  }
  return (await res.json()) as T;
}

export function verify(text: string, now?: string): Promise<CheckResult> {
  return call<CheckResult>("/api/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(now ? { text, now } : { text }),
  });
}

export function getSources(): Promise<SourcesResponse> {
  return call<SourcesResponse>("/api/sources");
}

/** UTC ISO 8601 → DD.MM.RRRR in Europe/Warsaw. */
export function formatDate(iso: string | null): string {
  if (!iso) return "brak danych";
  // The backend reads values without an offset as UTC (timeutil.parse_ts); JS would read them as local time.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) || !iso.includes("T");
  const d = new Date(hasZone ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "brak danych";
  return new Intl.DateTimeFormat("pl-PL", { timeZone: "Europe/Warsaw", day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}
