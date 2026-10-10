import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "../api/client";
import { batch, emptySummary, errPage, file, result } from "../test/fixtures";
import { UploadPage } from "./UploadPage";

const api = { uploadBook: vi.fn(), getSummary: vi.fn(), getBatches: vi.fn(), getErrors: vi.fn() };
vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  uploadBook: (...a: unknown[]) => api.uploadBook(...a), getSummary: (...a: unknown[]) => api.getSummary(...a),
  getBatches: (...a: unknown[]) => api.getBatches(...a), getErrors: (...a: unknown[]) => api.getErrors(...a),
}));

const date = () => screen.getByLabelText("Calculation date") as HTMLInputElement;
const renderPage = () => render(<MemoryRouter><UploadPage /></MemoryRouter>);

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  api.getSummary.mockResolvedValue(emptySummary());
  api.getBatches.mockResolvedValue({ items: [] });
  api.getErrors.mockResolvedValue(errPage(0));
  api.uploadBook.mockImplementation((book: string) => ({ promise: Promise.resolve(result({ book: book as "asset" })), abort: vi.fn() }));
});

it("renders the page structure from the spec", async () => {
  renderPage();
  expect(screen.getByRole("heading", { name: "Data ingestion" })).toBeInTheDocument();
  for (const t of ["Asset Book", "Liability Book", "Historical Log"]) expect(screen.getByRole("heading", { name: t })).toBeInTheDocument();
  expect(await screen.findByText("No data loaded yet")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Error report" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Upload history" })).toBeInTheDocument();
});

it("auto-fills the shared date from the first file name and uploads with it", async () => {
  renderPage();
  await userEvent.upload(screen.getByTestId("file-asset"), file("asset_book - 10102026.xlsx"));
  expect(date().value).toBe("2026-10-10");
  await waitFor(() => expect(api.uploadBook).toHaveBeenCalledWith("asset", expect.any(File), "2026-10-10", expect.any(Function)));
});

it("shares the date across the three cards and refreshes summary, history and errors after an upload", async () => {
  renderPage();
  await screen.findByText("No data loaded yet");
  const before = { s: api.getSummary.mock.calls.length, b: api.getBatches.mock.calls.length, e: api.getErrors.mock.calls.length };
  await userEvent.upload(screen.getByTestId("file-asset"), file("asset_book - 10102026.xlsx"));
  await userEvent.upload(screen.getByTestId("file-liability"), file("liability_book - 10102026.xlsx"));
  await userEvent.upload(screen.getByTestId("file-historical"), file("historical_log - 10102026.xlsx"));
  await waitFor(() => expect(api.uploadBook).toHaveBeenCalledTimes(3));
  expect(api.uploadBook.mock.calls.map((c) => [c[0], c[2]])).toEqual([
    ["asset", "2026-10-10"], ["liability", "2026-10-10"], ["historical", "2026-10-10"]]);
  await waitFor(() => expect(api.getSummary.mock.calls.length).toBe(before.s + 3));
  expect(api.getBatches.mock.calls.length).toBe(before.b + 3);
  expect(api.getErrors.mock.calls.length).toBeGreaterThan(before.e);
});

it("a manually typed date wins over later file-name dates", async () => {
  renderPage();
  await userEvent.type(date(), "2026-10-09");
  await userEvent.upload(screen.getByTestId("file-asset"), file("asset_book - 10102026.xlsx"));
  expect(date().value).toBe("2026-10-09");
  await waitFor(() => expect(api.uploadBook).toHaveBeenCalledWith("asset", expect.any(File), "2026-10-09", expect.any(Function)));
  expect(await screen.findByText(/file name says 2026-10-10/)).toBeInTheDocument();
});

it("holds an upload when no date can be determined, and starts it after the user types one", async () => {
  renderPage();
  await userEvent.upload(screen.getByTestId("file-asset"), file("asset_book.xlsx"));
  expect(await screen.findByText(/Enter a valid calculation date/)).toBeInTheDocument();
  expect(api.uploadBook).not.toHaveBeenCalled();
  await userEvent.type(date(), "2026-10-10");
  await waitFor(() => expect(api.uploadBook).toHaveBeenCalledOnce());
});

it("cards upload independently: one failing does not affect another", async () => {
  api.uploadBook.mockImplementation((book: string) => ({
    promise: book === "liability" ? Promise.reject(new ApiError("FILE_WRONG_BOOK", "This looks like an asset book", 422)) : Promise.resolve(result()),
    abort: vi.fn() }));
  renderPage();
  await userEvent.upload(screen.getByTestId("file-asset"), file("asset_book - 10102026.xlsx"));
  await userEvent.upload(screen.getByTestId("file-liability"), file("liability_book - 10102026.xlsx"));
  const liability = screen.getByRole("region", { name: "Liability Book" });
  const asset = screen.getByRole("region", { name: "Asset Book" });
  expect(await within(liability).findByText("Failed")).toBeInTheDocument();
  expect(within(liability).getByText("FILE_WRONG_BOOK")).toBeInTheDocument();
  expect(await within(asset).findByText("Warnings")).toBeInTheDocument();      // 2 rejected rows
  expect(within(asset).getByText("753")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Historical Log" })).getByText("Idle")).toBeInTheDocument();
});

it("enables the ladder link once the server says all three books are ready", async () => {
  api.getSummary.mockResolvedValue({ as_of_date: "2026-10-10", rows_stored_total: 18035, ready: true,
    books: { asset: batch(), liability: batch({ id: "b2", book: "liability" }), historical: batch({ id: "b3", book: "historical" }) } });
  renderPage();
  await waitFor(() => expect(screen.getByRole("link", { name: /View deterministic ladder/ })).toHaveAttribute("href", "/ladder"));
  expect(screen.getByText("18,035")).toBeInTheDocument();
});

it("shows a banner when the snapshot cannot be loaded (e.g. not signed in)", async () => {
  api.getSummary.mockRejectedValue(new ApiError("UNAUTHORIZED", "Missing bearer token", 401));
  renderPage();
  expect(await screen.findByRole("alert")).toHaveTextContent("Missing bearer token");
});
