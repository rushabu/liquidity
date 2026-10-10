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
