import { useEffect, useRef, useState } from "react";
import { getLadderTransactions, type ApiError, type BucketKey, type LadderQuery, type LadderTxnPage } from "../api/client";
import { formatInt, formatMoney } from "../lib/format";

export interface Cell { currency: string; location: string | null; bucket: BucketKey | "past_due" }

const PAGE = 25;

export function DrillDown({ cell, query, onExplain, onClose }: { cell: Cell; query: LadderQuery; onExplain: (id: string) => void; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<LadderTxnPage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  const { currency, location, bucket } = cell;
  const { calc_date, past_due_policy } = query;

  useEffect(() => { setPage(1); }, [currency, location, bucket]);
  useEffect(() => {
    const mine = ++seq.current;
    setData(null); setErr(null);
    getLadderTransactions({ currency, location: location ?? undefined, bucket, calc_date, past_due_policy, page, page_size: PAGE, sort: "maturity_date" })
      .then((d) => { if (mine === seq.current) setData(d); }, (e: ApiError) => { if (mine === seq.current) setErr(e.message); });
  }, [currency, location, bucket, calc_date, past_due_policy, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  return (
    <section className="card" aria-label="Transactions behind the selected cell">
      <div className="table-head">
        <div>
          <h2>{bucket === "past_due" ? "Past-due" : bucket} · {currency} · {location ?? "all locations"}</h2>
          {data?.summary && (
            <p className="hint">Assets {formatMoney(data.summary.assets)} − liabilities {formatMoney(data.summary.liabilities)} = <b>{formatMoney(data.summary.net_gap)}</b></p>
          )}
        </div>
        <button type="button" className="btn secondary" onClick={onClose}>Close</button>
      </div>
      {err && <p className="tbl-error" role="alert">{err}</p>}
      {!data && !err && <p className="loading">Loading…</p>}
      {data && data.total === 0 && <p className="empty">No transactions in this cell.</p>}
      {data && data.total > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Side</th><th>Location</th><th className="num">Principal</th><th className="num">Interest</th><th className="num">Cash at maturity</th><th>Maturity</th><th className="num">Days</th></tr></thead>
              <tbody>
                {data.items.map((t) => (
                  <tr key={t.transaction_id}>
                    <td className="file"><button type="button" className="link-btn" onClick={() => onExplain(t.transaction_id)}>{t.transaction_id}</button></td>
                    <td>{t.book}</td><td>{t.location}</td>
                    <td className="num mono">{formatMoney(t.amount)}</td><td className="num mono">{formatMoney(t.interest_amount)}</td>
                    <td className="num mono">{formatMoney(t.cash_at_maturity)}</td><td className="file">{t.maturity_date}</td>
                    <td className="num mono">{t.days_to_maturity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span>{formatInt(data.total)} transactions · page {page} of {pages}</span>
            <div>
              <button type="button" className="btn secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button type="button" className="btn secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
