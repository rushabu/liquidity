import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/client";
import { file, result } from "../test/fixtures";
import { BookCard } from "./BookCard";

const upload = vi.fn();
vi.mock("../api/client", async (orig) => ({ ...(await orig<typeof import("../api/client")>()), uploadBook: (...a: unknown[]) => upload(...a) }));

const props = { book: "asset" as const, title: "Asset Book", hint: "Loans", onUploaded: vi.fn() };
const pick = (name: string, size?: number) => userEvent.upload(screen.getByTestId("file-asset"), file(name, size));

function deferred() {
  let resolve!: (r: unknown) => void, reject!: (e: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(() => { upload.mockReset(); props.onUploaded.mockReset(); });

it("starts Idle with an accessible drop-zone button", () => {
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  expect(screen.getByText("Idle")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Choose a file" })).toBeEnabled();
});

it("uploads on selection with the shared date and shows the three stats", async () => {
  upload.mockReturnValue({ promise: Promise.resolve(result()), abort: vi.fn() });
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("asset_book - 10102026.xlsx");
  await waitFor(() => expect(screen.getByText("Warnings")).toBeInTheDocument());     // 2 rejected -> Warnings chip
  expect(upload).toHaveBeenCalledWith("asset", expect.any(File), "2026-10-10", expect.any(Function));
  expect(screen.getByText("755")).toBeInTheDocument();
  expect(screen.getByText("753")).toBeInTheDocument();
  expect(screen.getByText("Rejected").nextSibling).toHaveTextContent("2");
  expect(props.onUploaded).toHaveBeenCalledOnce();
  expect(screen.getByRole("status")).toHaveTextContent("753 rows stored, 2 rejected");
});

it("shows Completed when nothing was rejected and there are no warnings", async () => {
  upload.mockReturnValue({ promise: Promise.resolve(result({ rows_rejected: 0, rows_valid: 755 })), abort: vi.fn() });
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("a.xlsx");
  expect(await screen.findByText("Completed")).toBeInTheDocument();
});

it("drives the progress bar from upload progress, then shows Processing", async () => {
  const d = deferred();
  let report: (f: number) => void = () => {};
  upload.mockImplementation((_b, _f, _d, onProgress) => { report = onProgress; return { promise: d.promise, abort: vi.fn() }; });
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("a.xlsx");
  expect(screen.getByText("Uploading")).toBeInTheDocument();
  act(() => report(0.4));
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
  act(() => report(1));
  expect(screen.getByText("Validating and storing…")).toBeInTheDocument();
  await act(async () => { d.resolve(result()); await d.promise; });
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it("blocks the drop zone while uploading", async () => {
  upload.mockReturnValue({ promise: new Promise(() => {}), abort: vi.fn() });
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("a.xlsx");
  expect(screen.getByRole("button", { name: "Replace file" })).toBeDisabled();
});

it("rejects a wrong file type on the client without calling the API", async () => {
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  // userEvent honours the accept attribute, so fire the change directly like a drag-and-drop would
  const input = screen.getByTestId("file-asset");
  await act(async () => { input.dispatchEvent(Object.assign(new Event("change", { bubbles: true }), {})); });
  const drop = screen.getByRole("button", { name: "Choose a file" }).closest(".dropzone")!;
  await act(async () => {
    drop.dispatchEvent(Object.assign(new Event("drop", { bubbles: true, cancelable: true }), { dataTransfer: { files: [file("notes.pdf")] } }));
  });
  expect(screen.getByRole("alert")).toHaveTextContent("Unsupported file type");
  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
});

it("shows the server's file-level failure with its code and missing columns", async () => {
  const err = new ApiError("FILE_MISSING_COLUMNS", "Missing required column(s): Currency", 422, { missing_columns: ["Currency", "Loan_Type"] });
  upload.mockImplementation(() => ({ promise: Promise.reject(err), abort: vi.fn() }));
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("a.csv");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Missing required column(s): Currency");
  expect(alert).toHaveTextContent("FILE_MISSING_COLUMNS");
  expect(alert).toHaveTextContent("Missing: Currency, Loan_Type");
  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(props.onUploaded).not.toHaveBeenCalled();
});

it("shows a reference id for server errors", async () => {
  upload.mockImplementation(() => ({ promise: Promise.reject(new ApiError("INTERNAL_ERROR", "Unexpected server error.", 500, {}, "abc123")), abort: vi.fn() }));
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("a.csv");
  expect(await screen.findByText("Reference: abc123")).toBeInTheDocument();
});

it("holds the upload until a valid date exists, then starts it", async () => {
  upload.mockReturnValue({ promise: Promise.resolve(result()), abort: vi.fn() });
  const { rerender } = render(<BookCard {...props} asOf={null} onFileChosen={() => null} />);
  await pick("asset_book.xlsx");
  expect(screen.getByText(/Enter a valid calculation date/)).toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
  rerender(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await waitFor(() => expect(upload).toHaveBeenCalledWith("asset", expect.any(File), "2026-10-10", expect.any(Function)));
});

it("warns when the file name date differs from the shared calculation date", async () => {
  upload.mockReturnValue({ promise: Promise.resolve(result()), abort: vi.fn() });
  render(<BookCard {...props} asOf="2026-10-10" onFileChosen={() => "2026-10-10"} />);
  await pick("asset_book - 09102026.xlsx");
  expect(await screen.findByText(/file name says 2026-10-09/)).toBeInTheDocument();
  expect(upload).toHaveBeenCalledWith("asset", expect.any(File), "2026-10-10", expect.any(Function));
});
