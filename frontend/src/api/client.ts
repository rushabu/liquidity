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
