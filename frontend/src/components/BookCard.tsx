import { useEffect, useId, useRef, useState } from "react";
import { ApiError, MAX_UPLOAD_MB, uploadBook, type Book, type IngestResult } from "../api/client";
import { asOfFromFilename } from "../lib/dates";
import { checkFile, ACCEPTED_EXT } from "../lib/files";
import { formatBytes, formatDuration, formatInt } from "../lib/format";
import { StatusChip, type ChipState } from "./StatusChip";

type Phase = "idle" | "waiting" | "sending" | "processing" | "done" | "failed";

interface Props {
  book: Book;
  title: string;
  hint: string;
  /** Called when a file is chosen; returns the snapshot date to upload with (null = none yet). */
  onFileChosen: (fileDate: string | null) => string | null;
  /** The current valid snapshot date (used to start a held upload once the date is fixed). */
  asOf: string | null;
  onUploaded: (r: IngestResult) => void;
}

export function BookCard({ book, title, hint, onFileChosen, asOf, onUploaded }: Props) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<(() => void) | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<ApiError | { code: string; message: string; details?: Record<string, unknown> } | null>(null);
  const [over, setOver] = useState(false);
  const [dateNote, setDateNote] = useState<string | null>(null);

  useEffect(() => () => abort.current?.(), []);

  function start(f: File, date: string) {
    setPhase("sending"); setProgress(0); setError(null); setResult(null);
    const { promise, abort: a } = uploadBook(book, f, date, (p) => {
      setProgress(p);
      if (p >= 1) setPhase("processing");
    });
    abort.current = a;
    promise
      .then((r) => { setResult(r); setPhase("done"); onUploaded(r); })
      .catch((e: ApiError) => { if (e.code !== "ABORTED") { setError(e); setPhase("failed"); } })
      .finally(() => { abort.current = null; });
  }

  function choose(f: File | undefined | null) {
    if (!f) return;
    const problem = checkFile(f, MAX_UPLOAD_MB);
    setFile(f);
    setResult(null); setDateNote(null);
    if (problem) { setError({ code: "CLIENT_FILE", message: problem }); setPhase("failed"); return; }
    setError(null);
    const fileDate = asOfFromFilename(f.name);
    const date = onFileChosen(fileDate);
    if (fileDate && date && fileDate !== date)
      setDateNote(`The file name says ${fileDate}, but the calculation date is ${date}. It will be uploaded as ${date}.`);
    if (!date) { setPhase("waiting"); return; }
    start(f, date);
  }

  // an upload held for a missing date starts as soon as a valid date is entered
  useEffect(() => {
    if (phase === "waiting" && file && asOf) start(file, asOf);
  }, [asOf]);

  const busy = phase === "sending" || phase === "processing";
  const chip: ChipState =
    busy ? "Uploading"
    : phase === "failed" ? "Failed"
    : phase === "done" ? (result && (result.rows_rejected > 0 || result.warnings > 0) ? "Warnings" : "Completed")
    : "Idle";

  const live =
    phase === "done" && result ? `${title}: ${chip}. ${result.rows_valid} rows stored, ${result.rows_rejected} rejected.`
    : phase === "failed" && error ? `${title}: upload failed. ${error.message}`
    : busy ? `${title}: uploading` : "";

  return (
    <section className="card book" aria-labelledby={`${inputId}-t`}>
      <div className="book-head">
        <h2 id={`${inputId}-t`}>{title}</h2>
        <StatusChip state={chip} />
      </div>
      <p className="hint">{hint}</p>

      <div
        className="dropzone"
        data-over={over}
        data-state={busy ? "busy" : "ready"}
        onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) choose(e.dataTransfer.files?.[0]); }}
      >
        <button type="button" disabled={busy} onClick={() => input.current?.click()}>
          {file ? "Replace file" : "Choose a file"}
        </button>
        <span className="hint">or drop it here ({ACCEPTED_EXT.join(", ")}, max {MAX_UPLOAD_MB} MB)</span>
        <input
          id={inputId}
          ref={input}
          type="file"
          accept={ACCEPTED_EXT.join(",")}
          hidden
          tabIndex={-1}
          aria-label={`${title} file`}
          data-testid={`file-${book}`}
          onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ""; }}
        />
      </div>

      {file && (
        <div className="fileline">
          <span className="name">{file.name}</span>
          <span className="size">{formatBytes(file.size)}</span>
        </div>
      )}

      {busy && (
        <div>
          <div
            className="progress" role="progressbar" aria-label={`${title} upload progress`}
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}
            data-indeterminate={phase === "processing"}
          >
            <div style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
          <p className="meta">{phase === "processing" ? "Validating and storing…" : `Uploading ${Math.round(progress * 100)}%`}</p>
        </div>
      )}

      {phase === "waiting" && <p className="note">Enter a valid calculation date above to start this upload.</p>}
      {dateNote && <p className="note">{dateNote}</p>}

      {phase === "failed" && error && (
        <div className="failure" role="alert">
          {error.message}
          <span className="code">{error.code}</span>
          {Array.isArray(error.details?.missing_columns) && (
            <span className="code">Missing: {(error.details!.missing_columns as string[]).join(", ")}</span>
          )}
          {error instanceof ApiError && error.requestId && error.status >= 500 && (
            <span className="code">Reference: {error.requestId}</span>
          )}
        </div>
      )}

      {phase === "done" && result && (
        <>
          <dl className="stats">
            <div><dt>Rows read</dt><dd>{formatInt(result.rows_read)}</dd></div>
            <div><dt>Stored</dt><dd>{formatInt(result.rows_valid)}</dd></div>
            <div className="rejected"><dt>Rejected</dt><dd>{formatInt(result.rows_rejected)}</dd></div>
          </dl>
          <p className="meta">
            Snapshot <span className="mono">{result.as_of_date}</span> · {formatDuration(result.duration_ms)}
            {result.warnings > 0 && ` · ${result.warnings} warning${result.warnings > 1 ? "s" : ""}`}
            {result.replaced_batch_id && " · replaced the previous upload for this date"}
          </p>
        </>
      )}

      <p className="sr-only" aria-live="polite" role="status">{live}</p>
    </section>
  );
}
