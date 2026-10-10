import type { Batch } from "../api/client";
import { BOOK_LABEL } from "../lib/books";
import { formatDateTime } from "../lib/dates";
import { formatInt } from "../lib/format";

interface Props { items: Batch[]; loading: boolean; error: string | null }

export function BatchHistory({ items, loading, error }: Props) {
  return (
    <section className="card" aria-labelledby="hist-h">
      <div className="table-head"><h2 id="hist-h">Upload history</h2></div>
      {error ? (
        <p className="tbl-error" role="alert">{error}</p>
      ) : loading && items.length === 0 ? (
        <p className="loading" aria-busy="true">Loading…</p>
      ) : items.length === 0 ? (
        <p className="empty">No uploads yet</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Uploaded</th><th>Snapshot</th><th>Book</th><th>File</th>
                <th className="num">Read</th><th className="num">Stored</th><th className="num">Rejected</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((b) => (
                <tr key={b.id}>
                  <td>{formatDateTime(b.created_at)}</td>
                  <td className="mono">{b.as_of_date}</td>
                  <td>{BOOK_LABEL[b.book]}</td>
                  <td className="file">{b.source_filename}</td>
                  <td className="num mono">{formatInt(b.rows_read)}</td>
                  <td className="num mono">{formatInt(b.rows_valid)}</td>
                  <td className="num mono" style={{ color: b.rows_rejected ? "var(--error)" : undefined }}>{formatInt(b.rows_rejected)}</td>
                  <td><span className={`badge ${b.is_active ? "active" : "replaced"}`}>{b.is_active ? "Active" : "Replaced"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
