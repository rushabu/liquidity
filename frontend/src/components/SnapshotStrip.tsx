import { Link } from "react-router-dom";
import type { Summary } from "../api/client";
import { formatInt } from "../lib/format";

export function SnapshotStrip({ summary }: { summary: Summary | null }) {
  const loaded = summary ? Object.values(summary.books).filter(Boolean).length : 0;
  const ready = !!summary?.ready;
  const reasonId = "ladder-reason";
  return (
    <section className="card strip" aria-label="Snapshot">
      <div>
        {summary && summary.as_of_date ? (
          <p className="big">
            <b>{formatInt(summary.rows_stored_total)}</b> rows stored as the <b>{summary.as_of_date}</b> snapshot
          </p>
        ) : (
          <p className="big">No data loaded yet</p>
        )}
        <p className="sub" id={reasonId}>
          {ready ? "All three books are loaded for this date."
            : `${loaded} of 3 books loaded. Load all three for the same date to continue.`}
        </p>
        {summary?.model_history && !summary.model_history.same_date && (
          <p className="sub warn">The AI model uses the historical log of {summary.model_history.as_of_date} (none for {summary.as_of_date}).</p>
        )}
        {!!summary?.pending?.length && (
          <p className="sub warn">
            Not in use yet: {summary.pending.map((p) => `${p.book} ${p.as_of_date}`).join(", ")}. The ladder uses the newest date
            that has both an asset and a liability book.
          </p>
        )}
      </div>
      {ready ? (
        <Link className="btn" to="/ladder">View deterministic ladder →</Link>
      ) : (
        <span className="btn" role="link" aria-disabled="true" aria-describedby={reasonId}>View deterministic ladder →</span>
      )}
    </section>
  );
}
