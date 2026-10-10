/** Typed client for the Liquid+ ingestion API. The base URL lives here and
 *  nowhere else (VITE_API_URL). The frontend never talks to Supabase. */

export type Book = "asset" | "liability" | "historical";
export type Severity = "error" | "warning";

export interface ErrorItem {
  source_row: number | null;
  transaction_id: string | null;
  field: string | null;
  value: string | null;
  rule_code: string;
  message: string;
  severity: Severity;
}

export interface IngestResult {
  batch_id: string;
  book: Book;
  as_of_date: string;
  filename: string;
  rows_read: number;
  rows_valid: number;
  rows_rejected: number;
  warnings: number;
  duration_ms: number;
  replaced_batch_id: string | null;
  errors_preview: ErrorItem[];
}

export interface Batch {
  id: string;
  book: Book;
  as_of_date: string;
  source_filename: string;
  uploaded_by: string | null;
  status: "completed" | "failed";
  rows_read: number;
  rows_valid: number;
  rows_rejected: number;
  warnings_count: number;
  duration_ms: number;
  is_active: boolean;
  created_at: string;
}

export interface Summary {
  as_of_date: string | null;
  books: Record<Book, Batch | null>;
  rows_stored_total: number;
  ready: boolean;
}

export interface ErrorRow extends ErrorItem {
  id: number;
  batch_id: string;
  book: Book;
  file: string;
}

export interface ErrorPage {
  total: number;
  page: number;
  page_size: number;
  items: ErrorRow[];
  rule_codes: string[];
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public details: Record<string, unknown> = {},
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const API_URL: string = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const MAX_UPLOAD_MB: number = Number(import.meta.env.VITE_MAX_UPLOAD_MB ?? 25);

/** The auth module plugs in here (returns the Supabase access token). */
let tokenProvider: () => string | null = () => {
  try {
    return sessionStorage.getItem("liquidplus.token");
  } catch {
    return null;
  }
};
export function setTokenProvider(fn: () => string | null): void {
  tokenProvider = fn;
}

function authHeaders(): Record<string, string> {
  const t = tokenProvider();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

function toApiError(status: number, body: unknown, requestId?: string): ApiError {
  const e = (body as ErrorEnvelope | null)?.error;
  return new ApiError(e?.code ?? "HTTP_ERROR", e?.message ?? `Request failed (${status})`, status, e?.details ?? {}, requestId);
}

async function getJson<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
  const url = `${API_URL}${path}${q.size ? `?${q}` : ""}`;
  let res: Response;
  try {
    res = await fetch(url, { headers: authHeaders() });
  } catch {
    throw new ApiError("NETWORK", "Cannot reach the server. Check your connection and that the API is running.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw toApiError(res.status, body, res.headers.get("x-request-id") ?? undefined);
  return body as T;
}

export const getSummary = (asOf?: string) => getJson<Summary>("/api/ingest/summary", { as_of_date: asOf });
export const getBatches = (limit = 20) => getJson<{ items: Batch[] }>("/api/ingest/batches", { limit });

export interface ErrorQuery {
  book?: Book;
  severity?: Severity;
  rule_code?: string;
  page?: number;
  page_size?: number;
}
export const getErrors = (q: ErrorQuery) =>
  getJson<ErrorPage>("/api/ingest/errors", { ...q } as Record<string, string | number | undefined>);

/** Upload with real progress (fetch has no upload progress events). */
export function uploadBook(
  book: Book,
  file: File,
  asOfDate: string | null,
  onProgress: (fraction: number) => void,
): { promise: Promise<IngestResult>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<IngestResult>((resolve, reject) => {
    xhr.open("POST", `${API_URL}/api/ingest/${book}`);
    Object.entries(authHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON error page */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as IngestResult);
      else reject(toApiError(xhr.status, body, xhr.getResponseHeader("x-request-id") ?? undefined));
    };
    xhr.onerror = () => reject(new ApiError("NETWORK", "Upload failed: cannot reach the server."));
    xhr.onabort = () => reject(new ApiError("ABORTED", "Upload cancelled."));
    const form = new FormData();
    form.append("file", file);
    if (asOfDate) form.append("as_of_date", asOfDate);
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

/** The CSV endpoint needs the auth header, so it is fetched and saved as a blob. */
export async function downloadErrorsCsv(batchIds: string[]): Promise<void> {
  const res = await fetch(`${API_URL}/api/ingest/errors.csv?batch_ids=${encodeURIComponent(batchIds.join(","))}`, {
    headers: authHeaders(),
  }).catch(() => {
    throw new ApiError("NETWORK", "Cannot reach the server.");
  });
  if (!res.ok) throw toApiError(res.status, await res.json().catch(() => null));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "liquidplus_error_report.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------------ gap ladder */
export type BucketKey = "0-1M" | "1-3M" | "3-12M" | "1-5Y" | ">5Y";
export type PastDuePolicy = "exclude" | "bucket_0_1m";

export interface LadderRow {
  currency: string;
  location: string | null;            // null = currency roll-up across the locations you may see
  scope: "location" | "currency_total";
  bucket: BucketKey;
  bucket_label: string;
  start_date: string;
  end_date: string | null;
  asset_principal: number;
  asset_cash_at_maturity: number;
  liability_principal: number;
  liability_cash_at_maturity: number;
  net_gap: number;
  gap_status: "surplus" | "shortfall" | "balanced";
  asset_count: number;
  liability_count: number;
}

export interface LadderResponse {
  metadata: {
    as_of_date: string;
    calculation_date: string;
    past_due_policy: PastDuePolicy;
    day_count_basis: number;
    scope_location: string | null;
    asset_batch: { id: string; filename: string; rows: number };
    liability_batch: { id: string; filename: string; rows: number };
    transactions_read: number;
    transactions_in_ladder: number;
    past_due_count: number;
  };
  buckets: { key: BucketKey; label: string; start_date: string; end_date: string | null }[];
  ladder: LadderRow[];
  warnings: string[];
  assumptions: string[];
}

export interface LadderTxn {
  transaction_id: string;
  book: "asset" | "liability";
  entity: string;
  location: string;
  customer_id: string;
  currency: string;
  amount: number;
  interest_rate_pct: number;
  transaction_date: string;
  maturity_date: string;
  tenor_days: number;
  interest_amount: number;
  cash_at_maturity: number;
  bucket: BucketKey | null;
  days_to_maturity: number;
  past_due: boolean;
}

export interface LadderTxnPage {
  total: number;
  page: number;
  page_size: number;
  calculation_date: string;
  summary: { assets: number; liabilities: number; net_gap: number; currency: string } | null;
  items: LadderTxn[];
}

export interface Explanation {
  transaction_id: string;
  type: "asset" | "liability";
  currency: string;
  location: string;
  bucket: BucketKey | null;
  past_due: boolean;
  status: "in_ladder" | "past_due";
  interest: number;
  cash_at_maturity: number;
  stored_interest_matches_recomputation: boolean;
  calculation_date: string;
  steps: { n: number; title: string; working: string; result: string }[];
}

export interface LadderQuery {
  calc_date?: string;
  past_due_policy?: PastDuePolicy;
}
export interface LadderTxnQuery extends LadderQuery {
  currency?: string;
  location?: string;
  bucket?: BucketKey | "past_due";
  type?: "asset" | "liability";
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}
const q = (o: object) => o as Record<string, string | number | undefined>;
export const getLadder = (p: LadderQuery = {}) => getJson<LadderResponse>("/api/ladder", q(p));
export const getLadderTransactions = (p: LadderTxnQuery) => getJson<LadderTxnPage>("/api/ladder/transactions", q(p));
export const getExplanation = (id: string, p: LadderQuery = {}) =>
  getJson<Explanation>(`/api/ladder/explain/${encodeURIComponent(id)}`, q(p));
