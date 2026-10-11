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
  /** the historical log the model uses for this snapshot (same rule as the AI pages) */
  model_history?: { id: string; as_of_date: string; same_date: boolean } | null;
  /** newer uploads that are not part of the snapshot yet (e.g. only one book loaded for a later date) */
  pending?: { book: Book; as_of_date: string }[];
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

let onUnauthorized: (() => void) | null = null;
/** The auth module registers what to do when the API says the token is missing, expired or revoked. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

function toApiError(status: number, body: unknown, requestId?: string): ApiError {
  if (status === 401 && onUnauthorized) queueMicrotask(() => onUnauthorized?.());
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
      if (xhr.status >= 200 && xhr.status < 300) { clearAiCache(); resolve(body as IngestResult); }
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

export interface ErrorCsvQuery {
  book?: Book;
  severity?: Severity;
  rule_code?: string;
}

/** The CSV endpoint needs the auth header, so it is fetched and saved as a blob. It is the Error report table
 * with the same filters applied, every page included (no batch ids = everything active, like the table). */
export async function downloadErrorsCsv(q: ErrorCsvQuery = {}): Promise<void> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v) params.set(k, String(v));
  const qs = params.toString();
  const res = await fetch(`${API_URL}/api/ingest/errors.csv${qs ? `?${qs}` : ""}`, {
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

/* ------------------------------------------------------------------ AI gap ladder (behavioural model) */
export type Side = "asset" | "liability";
export type Segment = "Retail" | "Corporate" | "SME" | "Institutional";

export interface ModelSummary {
  name: string;
  historical_batch: { id: string; filename: string; as_of_date: string; rows: number };
  type: string;
  trained_on: number;
  early_exit_rate: number;
  holdout_auc: number | null;
  holdout_brier: number | null;
  holdout_f1: number | null;
  holdout_accuracy: number | null;
}

interface AiMeta {
  as_of_date: string;
  calculation_date: string;
  past_due_policy: PastDuePolicy;
  scope_location: string | null;
  market_shift_pp: number;
  currencies: string[];
  locations: string[];
  model: ModelSummary;
}

export interface AiLadderResponse extends LadderResponse {
  metadata: LadderResponse["metadata"] & AiMeta & {
    shifted_count: number;
    moved_count: number;
    average_p_early: number;
    average_shift_days: number;
  };
}

export interface MonthPoint {
  month: number;
  end_date: string;
  det_net: number;
  ai_net: number;
  det_cum: number;
  ai_cum: number;
  diff: number;
  note: string;
  bucket: BucketKey | null;
}

export interface CompareBucket {
  bucket: BucketKey;
  label: string;
  start_date: string;
  end_date: string | null;
  det_assets: number;
  det_liabilities: number;
  det_net: number;
  ai_assets: number;
  ai_liabilities: number;
  ai_net: number;
  delta: number;
  det_count: number;
  ai_count: number;
  p5: number | null;
  p50: number | null;
  p95: number | null;
  p_shortfall: number | null;
}

export interface Recommendation {
  currency: string;
  location: string | null;
  scope: string;
  bucket: BucketKey;
  severity: "high" | "medium" | "low";
  /** fund = raise money · cover = roll an earlier surplus forward · retain = keep early-withdrawal deposits
   *  · buffer = standby line for the bad case · keep_liquid / invest = what to do with a surplus */
  kind: "fund" | "cover" | "retain" | "buffer" | "keep_liquid" | "invest";
  action: string;
  rationale: string;
  amount: number;
  deterministic_net: number;
  ai_net: number;
  /** where the action comes from: the bucket window, the rule that fired and the figures it used */
  evidence?: {
    bucket_start: string | null;
    bucket_end: string | null;
    view: string;
    base: string;
    rule: string;
    sources: BucketKey[];
    figures: { label: string; value: number }[];
  };
}

export interface CompareResponse {
  metadata: AiMeta & {
    simulations: number;
    currency: string;
    location: string | null;
    side: Side | null;
    segment: Segment | null;
    horizon_months: number;
  };
  kpis: {
    moved: number;
    total: number;
    shifted: number;
    biggest_gain: { month: number; amount: number };
    deepest_opposite: { month: number; amount: number };
    det_total: number;
    ai_total: number;
  };
  months: MonthPoint[];
  buckets: CompareBucket[];
  recommendations: Recommendation[];
  overview: Recommendation[];
  notes: string[];
}

export interface DriverImportance { label: string; strength: number; log_odds: number; level: "High" | "Med" | "Low" | "~0" }

export interface WhyResponse {
  metadata: AiMeta & { currency: string; location: string | null };
  buckets: { bucket: BucketKey; moved_in: number; moved_out: number; shift: number; det_net: number; ai_net: number; text: string; largest: boolean }[];
  drivers: DriverImportance[];
  who_moved: { total: number; groups: { label: string; segment: string | null; side: Side | null; count: number; share: number }[] };
  scenario: {
    market_shift_pp: number;
    loans_repaid_early: { base: number; scenario: number; change: number };
    deposits_withdrawn_early: { base: number; scenario: number; change: number };
    buckets: { bucket: BucketKey; base: number; scenario: number; change: number }[];
    headline: { bucket: BucketKey; base: number; scenario: number; change: number };
    note: string;
  };
}

export interface ReasonChip { text: string; tone: "up" | "down" | "neutral" | "warn" }

export interface AiTxn {
  transaction_id: string;
  book: Side;
  entity: string;
  location: string;
  customer_id: string;
  customer_segment: Segment;
  currency: string;
  amount: number;
  interest_rate_pct: number;
  market_rate_pct: number;
  spread_pp: number;
  transaction_date: string;
  contractual_maturity_date: string;
  adjusted_maturity_date: string;
  expected_exit_date: string | null;
  shift_days: number;
  p_early: number;
  p_early_lifetime: number;
  interest_amount: number;
  cash_at_maturity: number;
  bucket: BucketKey | null;
  deterministic_bucket: BucketKey | null;
  moved: boolean;
  days_to_maturity: number;
  past_due: boolean;
  reasons: ReasonChip[];
}

export interface AiTxnPage {
  total: number;
  page: number;
  page_size: number;
  calculation_date: string;
  kpis: {
    count: number;
    currencies?: string[];
    /** only when the selection is a single currency (amounts in different currencies are never added) */
    cash_pulled_in: number | null;
    cash_pulled_in_by_currency?: Record<string, number> | null;
    avg_p_early: number;
    avg_spread_pp: number;
  };
  summary: { assets: number; liabilities: number; net_gap: number; currency: string } | null;
  items: AiTxn[];
}

export interface Driver {
  feature: string;
  label: string;
  value: string;
  log_odds: number;
  effect_pp: number;
  direction: "raises" | "lowers" | "neutral";
}

export interface AiExplanation {
  transaction_id: string;
  type: Side;
  currency: string;
  location: string;
  customer_segment: Segment;
  calculation_date: string;
  p_early_base: number;
  p_early: number;
  elapsed_fraction: number;
  transaction_date: string;
  contractual_maturity_date: string;
  expected_exit_date: string | null;
  adjusted_maturity_date: string;
  shift_days: number;
  deterministic_bucket: BucketKey | null;
  ai_bucket: BucketKey | null;
  cash_at_maturity: number;
  interest_rate_pct: number;
  market_rate_pct: number;
  spread_pp: number;
  tenor_years: number;
  formula: string;
  reasons: ReasonChip[];
  rationale: { average_probability: number; summary: string; drivers: Driver[] };
}

export interface Classification { threshold: number; accuracy: number; precision: number; recall: number; f1: number; tp: number; fp: number; fn: number; tn: number }

export interface ModelCard {
  name: string;
  type: string;
  historical_batch: { id: string; filename: string; as_of_date: string };
  trained_on: number;
  early_exits: number;
  early_exit_rate: number;
  iterations: number;
  metrics: {
    holdout?: string;
    train_rows?: number;
    test_rows?: number;
    test_early_rate?: number;
    auc?: number;
    brier?: number;
    brier_baseline?: number;
    at_50?: Classification;
    best_f1?: Classification;
    calibration?: { predicted: number; observed: number; n: number }[];
    in_sample_auc?: number;
    history_rows?: number;
    open_rows?: number;
  };
  importance: DriverImportance[];
  drivers: { feature: string; label: string; coefficient: number; per: string; std: number | null }[];
  exit_timing: { min: number; median: number; max: number; histogram: { from: number; to: number; count: number }[] };
  assumptions: string[];
}

export interface CompareQuery extends LadderQuery {
  currency?: string;
  location?: string;
  side?: Side;
  segment?: Segment;
  simulations?: number;
}
export interface WhyQuery extends LadderQuery {
  currency?: string;
  location?: string;
  market_shift_pp?: number;
}
export interface AiTxnQuery extends LadderQuery {
  currency?: string;
  location?: string;
  bucket?: BucketKey | "past_due";
  type?: Side;
  segment?: Segment;
  p_min?: number;
  p_max?: number;
  spread_min?: number;
  spread_max?: number;
  moved_only?: boolean;
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}
const qb = (o: object) =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, typeof v === "boolean" ? (v ? "true" : undefined) : v])) as Record<string, string | number | undefined>;

/* Response store for the AI gap ladder: the same request (same user, same parameters) is answered from memory
 * for 10 minutes, and identical requests in flight share one promise. Switching tabs or pages therefore never
 * refetches or recomputes. A successful upload clears it (see uploadBook), and failures are never stored. */
const AI_TTL_MS = 10 * 60 * 1000;
const aiStore = new Map<string, { at: number; promise: Promise<unknown> }>();
let dataVersion = 0;
/** Bumped on every successful upload: pages that keep results in memory compare it to drop stale ones. */
export const getDataVersion = (): number => dataVersion;
export function clearAiCache(): void {
  aiStore.clear();
  dataVersion += 1;
}
function cachedJson<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const key = `${tokenProvider() ?? ""}|${path}|${JSON.stringify(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").sort())}`;
  const hit = aiStore.get(key);
  if (hit && Date.now() - hit.at < AI_TTL_MS) return hit.promise as Promise<T>;
  const promise = getJson<T>(path, params);
  aiStore.set(key, { at: Date.now(), promise });
  promise.catch(() => aiStore.delete(key));
  return promise;
}

export const getAiLadder = (p: LadderQuery = {}) => cachedJson<AiLadderResponse>("/api/behavioral/ladder", q(p));
export const getLadderCached = (p: LadderQuery = {}) => cachedJson<LadderResponse>("/api/ladder", q(p));
export const getComparison = (p: CompareQuery = {}) => cachedJson<CompareResponse>("/api/behavioral/compare", q(p));
export const getWhy = (p: WhyQuery = {}) => cachedJson<WhyResponse>("/api/behavioral/why", q(p));
export const getAiTransactions = (p: AiTxnQuery) => cachedJson<AiTxnPage>("/api/behavioral/transactions", qb(p));
export const getAiExplanation = (id: string, p: LadderQuery = {}) =>
  cachedJson<AiExplanation>(`/api/behavioral/explain/${encodeURIComponent(id)}`, q(p));
export const getModelCard = () => cachedJson<ModelCard>("/api/behavioral/model");

/** Download a CSV that needs the auth header (fetched as a blob, then saved). */
async function downloadCsv(path: string, params: Record<string, string | number | undefined>, filename: string) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && qs.set(k, String(v)));
  const res = await fetch(`${API_URL}${path}${qs.size ? `?${qs}` : ""}`, { headers: authHeaders() }).catch(() => {
    throw new ApiError("NETWORK", "Cannot reach the server.");
  });
  if (!res.ok) throw toApiError(res.status, await res.json().catch(() => null));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
export const downloadAiPredictions = (p: AiTxnQuery) =>
  downloadCsv("/api/behavioral/transactions.csv", qb({ ...p, page: undefined, page_size: undefined, sort: undefined, order: undefined }), "liquidplus_ai_predictions.csv");
export const downloadHistoryPredictions = () =>
  downloadCsv("/api/behavioral/history-predictions.csv", {}, "liquidplus_history_predictions.csv");

/* ------------------------------------------------------------------ what-if tenor shifts */

export interface WhatIfQuery {
  asset_shift_months?: number;
  asset_shift_days?: number;
  liability_shift_months?: number;
  liability_shift_days?: number;
  currency?: string;
  location?: string;
  calc_date?: string;
}

export interface WhatIfBucket {
  bucket: BucketKey;
  label: string;
  start_date: string;
  end_date: string | null;
  base_assets: number;
  base_liabilities: number;
  base_net: number;
  scen_assets: number;
  scen_liabilities: number;
  scen_net: number;
  delta: number;
  base_cum: number;
  scen_cum: number;
  moved_in: number;
  moved_out: number;
}

export interface WhatIfMover {
  transaction_id: string;
  book: "asset" | "liability";
  location: string;
  customer_segment: string | null;
  cash_at_maturity: number;
  base_date: string;
  scenario_date: string;
  base_bucket: BucketKey;
  scenario_bucket: BucketKey;
  pulled_to_today: boolean;
}

export interface WhatIfResponse {
  metadata: {
    base: "deterministic";        // always the contractual ladder: no ML model in what-if
    as_of_date: string;
    calculation_date: string;
    currency: string;
    location: string | null;
    currencies: string[];
    locations: string[];
    scope_location: string | null;
    shifts: Record<"asset" | "liability", { months: number; days: number; text: string }>;
    scenario: string;
  };
  kpis: {
    total: number;
    moved: number;
    shifted: number;
    pulled_to_today: number;
    worst_base: { bucket: BucketKey; net: number };
    worst_scenario: { bucket: BucketKey; net: number };
    total_net: number;
  };
  buckets: WhatIfBucket[];
  movers_total: number;
  movers: WhatIfMover[];
  recommendations: Recommendation[];
  notes: string[];
}

export const getWhatIf = (p: WhatIfQuery = {}) => cachedJson<WhatIfResponse>("/api/whatif", q(p));
