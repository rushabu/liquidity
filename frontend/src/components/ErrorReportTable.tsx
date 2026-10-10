import { useEffect, useRef, useState } from "react";
import { ApiError, downloadErrorsCsv, getErrors, type Book, type ErrorPage, type Severity } from "../api/client";
import { BOOK_LABEL } from "../lib/books";
import { formatInt } from "../lib/format";

const PAGE_SIZE = 50;

interface Props {
  /** bump to reload after an upload */
  refreshKey: number;
  /** active batches that have rejected rows or warnings (feeds the CSV download) */
  csvBatchIds: string[];
}

export function ErrorReportTable({ refreshKey, csvBatchIds }: Props) {
  const [book, setBook] = useState<Book | "">("");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [rule, setRule] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ErrorPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true); setError(null);
    getErrors({ book: book || undefined, severity: severity || undefined, rule_code: rule || undefined, page, page_size: PAGE_SIZE })
      .then((d) => { if (mine === seq.current) setData(d); })
      .catch((e: ApiError) => { if (mine === seq.current) setError(e.message); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [book, severity, rule, page, refreshKey, reloads]);

  // a changed filter or a fresh upload returns to page 1; a stale rule filter is dropped
  useEffect(() => { setPage(1); }, [book, severity, rule, refreshKey]);
  useEffect(() => {
    if (rule && data && !data.rule_codes.includes(rule)) setRule("");
  }, [data, rule]);

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);

  async function download() {
    setDlError(null);
    try { await downloadErrorsCsv(csvBatchIds); } catch (e) { setDlError((e as Error).message); }
  }

  return (
    <section className="card" aria-labelledby="err-h">
      <div className="table-head">
        <h2 id="err-h">Error report</h2>
        <button type="button" className="btn secondary" onClick={download} disabled={csvBatchIds.length === 0}>
          Download error report (CSV)
        </button>
      </div>
      {dlError && <p className="tbl-error" role="alert">{dlError}</p>}

      <div className="filters" role="group" aria-label="Filter the error report">
        <label>Book
          <select value={book} onChange={(e) => setBook(e.target.value as Book | "")}>
            <option value="">All books</option>
            {(Object.keys(BOOK_LABEL) as Book[]).map((b) => <option key={b} value={b}>{BOOK_LABEL[b]}</option>)}
          </select>
        </label>
        <label>Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "")}>
            <option value="">Errors and warnings</option>
            <option value="error">Rejected rows (errors)</option>
            <option value="warning">Warnings</option>
          </select>
        </label>
        <label>Rule
          <select value={rule} onChange={(e) => setRule(e.target.value)}>
            <option value="">All rules</option>
            {(data?.rule_codes ?? []).map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>

      <div style={{ height: 12 }} />
      {error ? (
        <div className="tbl-error" role="alert">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => setReloads((n) => n + 1)}>Retry</button>
        </div>
      ) : loading && !data ? (
        <p className="loading" aria-busy="true">Loading error report…</p>
      ) : total === 0 ? (
        <p className="empty">No rejected rows</p>
      ) : (
        <>
          <div className="table-wrap" aria-busy={loading}>
            <table>
              <caption className="sr-only">Rejected rows and warnings from the active uploads</caption>
              <thead>
                <tr><th>File</th><th className="num">Row</th><th>Transaction</th><th>Field</th><th>Value found</th><th>Rule broken</th></tr>
              </thead>
              <tbody>
                {data!.items.map((r) => (
                  <tr key={r.id}>
                    <td className="file">{r.file}</td>
                    <td className="num mono">{r.source_row ?? "—"}</td>
                    <td className="mono">{r.transaction_id ?? "—"}</td>
                    <td>{r.field ?? "—"}</td>
                    <td className="value">{r.value ?? "—"}</td>
                    <td>
                      <span className="rule" data-sev={r.severity}>{r.rule_code}</span>
                      <span className="rule-msg">{r.message}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span aria-live="polite">Showing {formatInt(from)}–{formatInt(to)} of {formatInt(total)}</span>
            <div>
              <button type="button" className="btn secondary" onClick={() => setPage((p) => p - 1)} disabled={page <= 1 || loading}>Previous</button>
              <span>Page {page} of {pages}</span>
              <button type="button" className="btn secondary" onClick={() => setPage((p) => p + 1)} disabled={page >= pages || loading}>Next</button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
