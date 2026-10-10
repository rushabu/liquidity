import type { Batch, ErrorPage, IngestResult, Summary } from "../api/client";

export const result = (o: Partial<IngestResult> = {}): IngestResult => ({
  batch_id: "b1", book: "asset", as_of_date: "2026-10-10", filename: "asset_book - 10102026.xlsx",
  rows_read: 755, rows_valid: 753, rows_rejected: 2, warnings: 0, duration_ms: 151, replaced_batch_id: null,
  errors_preview: [], ...o,
});

export const batch = (o: Partial<Batch> = {}): Batch => ({
  id: "b1", book: "asset", as_of_date: "2026-10-10", source_filename: "asset_book - 10102026.xlsx", uploaded_by: "ops",
  status: "completed", rows_read: 755, rows_valid: 753, rows_rejected: 2, warnings_count: 0, duration_ms: 151,
  is_active: true, created_at: "2026-10-10T08:00:00Z", ...o,
});

export const emptySummary = (): Summary => ({
  as_of_date: null, books: { asset: null, liability: null, historical: null }, rows_stored_total: 0, ready: false,
});

export const errPage = (n: number, o: Partial<ErrorPage> = {}): ErrorPage => ({
  total: n, page: 1, page_size: 50, rule_codes: ["CURRENCY_INVALID", "MATURITY_BEFORE_TXN"],
  items: Array.from({ length: Math.min(n, 50) }, (_, i) => ({
    id: i + 1, batch_id: "b1", book: "asset" as const, file: "asset_book - 10102026.xlsx", source_row: 755 + i,
    transaction_id: `AST-00${7026 + i}`, field: "Currency", value: "US", rule_code: "CURRENCY_INVALID",
    message: "Currency: 'US' is not one of: EUR, GBP, HKD, SGD, USD", severity: "error" as const,
  })), ...o,
});

export const file = (name: string, size = 1000) => new File([new Uint8Array(size)], name);

import type { BucketKey, LadderResponse, LadderRow } from "../api/client";

const BK: BucketKey[] = ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"];
export const ladderRow = (bucket: BucketKey, net: number, o: Partial<LadderRow> = {}): LadderRow => ({
  currency: "EUR", location: null, scope: "currency_total", bucket, bucket_label: bucket, start_date: "2026-10-10", end_date: "2026-11-10",
  asset_principal: 0, asset_cash_at_maturity: net > 0 ? net : 0, liability_principal: 0, liability_cash_at_maturity: net < 0 ? -net : 0,
  net_gap: net, gap_status: net > 0 ? "surplus" : net < 0 ? "shortfall" : "balanced", asset_count: 1, liability_count: 1, ...o,
});

export const ladderResponse = (o: Partial<LadderResponse> = {}): LadderResponse => ({
  metadata: { as_of_date: "2026-10-10", calculation_date: "2026-10-10", past_due_policy: "exclude", day_count_basis: 365, scope_location: null,
    asset_batch: { id: "a", filename: "asset_book - 10102026.xlsx", rows: 753 }, liability_batch: { id: "l", filename: "liability_book - 10102026.xlsx", rows: 1103 },
    transactions_read: 1856, transactions_in_ladder: 1856, past_due_count: 0 },
  buckets: BK.map((key) => ({ key, label: key, start_date: "2026-10-10", end_date: null })),
  ladder: [140596536.28, 19816879.09, -591834004.01, -28911011.77, 67932102.58].map((n, i) => ladderRow(BK[i], n))
    .concat([ladderRow("0-1M", -1631461.95, { location: "Hong Kong", scope: "location" })]),
  warnings: [], assumptions: ["Interest: simple, Actual/365."], ...o,
});
