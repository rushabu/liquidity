import { useCallback, useEffect, useRef, useState } from "react";
import { getBatches, getSummary, type ApiError, type Batch, type Summary } from "../api/client";
import { BatchHistory } from "../components/BatchHistory";
import { BookCard } from "../components/BookCard";
import { CalcDateBox } from "../components/CalcDateBox";
import { ErrorReportTable } from "../components/ErrorReportTable";
import { SnapshotStrip } from "../components/SnapshotStrip";
import { BOOKS } from "../lib/books";
import { isValidIsoDate } from "../lib/dates";

export function UploadPage() {
  const [asOf, setAsOf] = useState("");
  const [source, setSource] = useState<"filename" | "manual" | "none">("none");
  const touched = useRef(false);            // true once the user typed a date themselves
  const asOfRef = useRef("");               // synchronous mirror, read by cards right after a file is chosen

  const [summary, setSummary] = useState<Summary | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [histError, setHistError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(async () => {
    const [s, b] = await Promise.allSettled([getSummary(), getBatches(20)]);
    if (s.status === "fulfilled") { setSummary(s.value); setPageError(null); }
    else setPageError((s.reason as ApiError).message);
    if (b.status === "fulfilled") { setBatches(b.value.items); setHistError(null); }
    else setHistError((b.reason as ApiError).message);
    setLoading(false);
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const setDate = (v: string, src: "filename" | "manual" | "none") => { asOfRef.current = v; setAsOf(v); setSource(src); };

  const onManualDate = (v: string) => { touched.current = true; setDate(v, v ? "manual" : "none"); };

  /** Cards call this when a file is picked. Returns the date the upload must use. */
  const onFileChosen = (fileDate: string | null): string | null => {
    if (!touched.current && fileDate) setDate(fileDate, "filename");
    return isValidIsoDate(asOfRef.current) ? asOfRef.current : null;
  };

  const validAsOf = isValidIsoDate(asOf) ? asOf : null;
  const csvBatchIds = summary
    ? Object.values(summary.books).filter((b): b is Batch => !!b && (b.rows_rejected > 0 || b.warnings_count > 0)).map((b) => b.id)
    : [];

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Data ingestion</h1>
          <p>Upload the three books for one calculation date. Valid rows are stored; rejected rows are listed below with the rule they broke.</p>
        </div>
        <CalcDateBox value={asOf} onChange={onManualDate} source={source} />
      </div>

      {pageError && <p className="banner" role="alert">Could not load the current snapshot: {pageError}</p>}

      <div className="grid-3">
        {BOOKS.map((b) => (
          <BookCard key={b.book} {...b} asOf={validAsOf} onFileChosen={onFileChosen} onUploaded={() => void refresh()} />
        ))}
      </div>

      <SnapshotStrip summary={summary} />
      <ErrorReportTable refreshKey={refreshKey} csvBatchIds={csvBatchIds} />
      <BatchHistory items={batches} loading={loading} error={histError} />
    </main>
  );
}
