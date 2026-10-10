import { useEffect, useRef, useState } from "react";
import { getExplanation, type ApiError, type Explanation, type LadderQuery } from "../api/client";

export function ExplainPanel({ id, query, onClose }: { id: string; query: LadderQuery; onClose: () => void }) {
  const [data, setData] = useState<Explanation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  const { calc_date, past_due_policy } = query;
  useEffect(() => {
    const mine = ++seq.current;
    setData(null); setErr(null);
    getExplanation(id, { calc_date, past_due_policy })
      .then((d) => { if (mine === seq.current) setData(d); }, (e: ApiError) => { if (mine === seq.current) setErr(e.message); });
  }, [id, calc_date, past_due_policy]);
  return (
    <section className="card explain" aria-label={`Explanation for ${id}`}>
      <div className="table-head">
        <h2>How {id} reaches its bucket</h2>
        <button type="button" className="btn secondary" onClick={onClose}>Close</button>
      </div>
      {err && <p className="tbl-error" role="alert">{err}</p>}
      {!data && !err && <p className="loading">Loading…</p>}
      {data && (
        <>
          {!data.stored_interest_matches_recomputation && (
            <p className="note" role="alert">Stored interest differs from a fresh recomputation. Re-ingest this snapshot.</p>
          )}
          <ol className="steps">
            {data.steps.map((s) => (
              <li key={s.n}>
                <b>{s.title}</b>
                <code>{s.working}</code>
                <span className="step-result">{s.result}</span>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
