// Admin API client (backend/admin/src/admin/app.py). Types mirror its JSON; change them together.
// Every request carries the session cookie (same origin) and the panel's CSRF header.

export type Group = "success" | "no_result" | "degraded" | "failure";
export type Money = Record<string, number>; // currency -> amount (never converted)

export interface Meta {
  outcomes: { key: string; label: string; group: Group }[];
  groups: Record<Group, string[]>;
  source_types: string[];
  channels: string[];
}

export interface Window {
  total: number;
  by_group: Record<Group, number>;
  by_outcome: Record<string, number>;
  failure_rate: number;
  cost: Money;
  calls: number;
  tokens: { input: number; output: number };
  duration_ms: { median: number; p95: number };
}

export interface Overview {
  generated_at: string;
  windows: { "24h": Window; "7d": Window };
  daily: ({ day: string; total: number; cost: Money } & Record<Group, number>)[];
  alerts: AlertCounts;
  sources: { total: number; disabled: number; with_errors: number };
  corpus: Record<string, number>;
}

export interface CheckRow {
  check_id: string;
  created_at: string;
  input_masked: string;
  status: string;
  channel: string | null;
  outcome: string | null;
  outcome_pl: string;
  group: Group;
  error_type: string | null;
  model: string | null;
  duration_ms: number | null;
  llm_calls: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: number | null;
  cost_currency: string | null;
  review_label?: string | null;
}

export interface LoggedClaim {
  claim: string;
  verdict: string;
  verdict_pl: string;
  explanation: string;
  citations: { n: number; role: string; title: string; publisher: string; url: string | null; date_label: string }[];
  notes: string[];
  error?: string | null;
  error_type?: string | null;
  invented_ids?: string[];
  shortlist_ids?: string[];
  downgraded_from?: string | null;
  known_disinformation?: boolean;
  strength?: { level: string; reasons: string[] } | null;
  live?: Record<string, unknown> | null;
}

export interface CheckDetail extends CheckRow {
  corpus_version: number;
  embedding_model: string | null;
  result: {
    status: string;
    message: string | null;
    claims: LoggedClaim[];
    inputs: { id: string; kind: string; label: string; readable: boolean; limitations: string[] }[];
    injection_detected: boolean;
    error?: string | null;
    error_type?: string | null;
    usage?: {
      calls: number;
      input_tokens: number;
      output_tokens: number;
      cost: number | null;
      currency: string | null;
      by_call?: { call: string; in: number; out: number }[];
    } | null;
  };
  review: { label: string; note: string | null; reviewed_at: string } | null;
}

export interface Failures {
  from: string;
  to: string;
  by_outcome: { outcome: string; label: string; group: Group; count: number }[];
  daily: ({ day: string } & Record<Exclude<Group, "success">, number>)[];
  error_types: { error_type: string; outcome: string; count: number }[];
  unreviewed: number;
}

export interface UsageRow {
  key: string;
  checks: number | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: Money;
}
export interface UsageReport {
  from: string;
  to: string;
  group: string;
  rows: UsageRow[];
  totals: Omit<UsageRow, "key"> & { unpriced_checks: number };
}

export interface AdminSource {
  source_id: string;
  publisher: string;
  admin_publisher: string | null;
  publishing_domain: string;
  source_type: string;
  admin_source_type: string | null;
  citation_url: string | null;
  refresh_policy: string | null;
  last_success_at: string | null;
  last_error: string | null;
  enabled: boolean;
  admin_note: string | null;
  admin_updated_at: string | null;
  current_records: number;
  withdrawn_records: number;
}

export interface Document {
  evidence_id: number;
  source_id?: string;
  upstream_id: string;
  version: number;
  is_current: boolean;
  kind: string;
  title: string;
  citation_url: string;
  published_at: string | null;
  fetched_at: string | null;
  withdrawn_at: string | null;
  withdrawn_note: string | null;
}

export interface TrustedDomain {
  domain: string;
  kind: string;
  enabled: boolean;
  note: string | null;
  added_at: string;
}

export interface AlertCounts {
  open: number;
  errors: number;
}
export interface Alert {
  alert_id: number;
  created_at: string;
  last_seen_at: string;
  kind: string;
  severity: "error" | "warning";
  fingerprint: string;
  check_id: string | null;
  message: string;
  count: number;
  notified_at: string | null;
  acknowledged_at: string | null;
}

export interface AuditEntry {
  audit_id: number;
  at: string;
  action: string;
  target: string | null;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

const BASE = "/admin/api";
const MSG_DOWN = "Panel administracyjny jest chwilowo niedostępny. Sprawdź, czy działa `uv run admin`.";
let onUnauthorized: () => void = () => {};

/** Called on every 401 (expired session): the app shows the login page. */
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      credentials: "same-origin",
      headers: { "X-Requested-With": "admin-panel", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
  } catch {
    throw new ApiError(MSG_DOWN, 0);
  }
  if (res.status === 401 && path !== "/login") onUnauthorized();
  if (!res.ok) {
    let detail = res.status >= 500 ? MSG_DOWN : `Błąd ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = "Niepoprawne dane formularza.";
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

function qs(params: Record<string, string | number | boolean | string[] | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const api = {
  login: (password: string) => call<void>("/login", json("POST", { password })),
  logout: () => call<void>("/logout", json("POST")),
  me: () => call<{ ok: boolean; expires_at: string }>("/me"),
  meta: () => call<Meta>("/meta"),
  overview: () => call<Overview>("/overview"),

  checks: (f: { cursor?: string | null; channel?: string; group?: string; outcome?: string; q?: string; from?: string; to?: string; unreviewed?: boolean }) =>
    call<{ items: CheckRow[]; next: string | null }>(`/checks${qs({ ...f, limit: 50 })}`),
  check: (id: string) => call<CheckDetail>(`/checks/${encodeURIComponent(id)}`),
  review: (id: string, label: string, note: string) => call<{ label: string; note: string | null }>(`/checks/${encodeURIComponent(id)}/review`, json("PUT", { label, note })),
  unreview: (id: string) => call<void>(`/checks/${encodeURIComponent(id)}/review`, json("DELETE")),

  failures: (from?: string, to?: string) => call<Failures>(`/failures${qs({ from, to })}`),
  usage: (group: string, from?: string, to?: string) => call<UsageReport>(`/usage${qs({ group, from, to })}`),

  sources: () => call<{ items: AdminSource[] }>("/sources"),
  updateSource: (id: string, body: { enabled: boolean; publisher: string | null; source_type: string | null; note: string | null }) =>
    call<AdminSource>(`/sources/${encodeURIComponent(id)}`, json("PATCH", body)),
  createSource: (body: { source_id: string; publisher: string; domain: string; source_type: string; note: string | null }) =>
    call<AdminSource>("/sources", json("POST", body)),
  documents: (id: string) => call<{ source: AdminSource; items: Document[] }>(`/sources/${encodeURIComponent(id)}/documents`),
  addDocument: (id: string, url: string) =>
    call<{ action: string; evidence_id: number | null; title: string; citation_url: string }>(`/sources/${encodeURIComponent(id)}/documents`, json("POST", { url })),
  withdraw: (eid: number, note: string) => call<Document>(`/documents/${eid}/withdraw`, json("POST", { note })),
  restore: (eid: number) => call<Document>(`/documents/${eid}/restore`, json("POST")),

  domains: () => call<{ items: TrustedDomain[] }>("/trusted-domains"),
  saveDomain: (domain: string, note: string | null, enabled = true) => call<{ domain: string }>("/trusted-domains", json("POST", { domain, note, enabled })),
  deleteDomain: (domain: string) => call<void>(`/trusted-domains/${encodeURIComponent(domain)}`, json("DELETE")),

  alerts: (open: boolean) => call<{ items: Alert[]; counts: AlertCounts }>(`/alerts${qs({ open })}`),
  alertCount: () => call<AlertCounts>("/alerts/count"),
  ack: (id: number) => call<AlertCounts>(`/alerts/${id}/ack`, json("POST")),
  ackAll: () => call<AlertCounts>("/alerts/ack-all", json("POST")),

  audit: () => call<{ items: AuditEntry[] }>("/audit"),
};
