import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/client";
import { errPage } from "../test/fixtures";
import { ErrorReportTable } from "./ErrorReportTable";

const getErrors = vi.fn();
const download = vi.fn();
vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  getErrors: (...a: unknown[]) => getErrors(...a),
  downloadErrorsCsv: (...a: unknown[]) => download(...a),
}));

beforeEach(() => { getErrors.mockReset(); download.mockReset(); });

it("renders the six columns and the rows", async () => {
  getErrors.mockResolvedValue(errPage(3));
  render(<ErrorReportTable refreshKey={0} />);
  const table = await screen.findByRole("table");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
    ["File", "Row", "Transaction", "Field", "Value found", "Rule broken"]);
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(within(table).getByText("AST-007026")).toBeInTheDocument();
  expect(screen.getByText("Showing 1–3 of 3")).toBeInTheDocument();
});

it("shows the empty state", async () => {
  getErrors.mockResolvedValue(errPage(0));
  render(<ErrorReportTable refreshKey={0} />);
  expect(await screen.findByText("No rejected rows")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Download error report/ })).toBeDisabled();
});

it("shows a loading state first", () => {
  getErrors.mockReturnValue(new Promise(() => {}));
  render(<ErrorReportTable refreshKey={0} />);
  expect(screen.getByText(/Loading error report/)).toBeInTheDocument();
});

it("shows an error state and retries", async () => {
  getErrors.mockRejectedValueOnce(new ApiError("NETWORK", "Cannot reach the server.")).mockResolvedValue(errPage(1));
  render(<ErrorReportTable refreshKey={0} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server.");
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("table")).toBeInTheDocument();
});

it("paginates on the server, 50 per page", async () => {
  getErrors.mockImplementation(async (q) => errPage(120, { page: q.page, items: errPage(q.page === 3 ? 20 : 50).items }));
  render(<ErrorReportTable refreshKey={0} />);
  expect(await screen.findByText("Showing 1–50 of 120")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Showing 51–100 of 120")).toBeInTheDocument();
  expect(getErrors).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 50 }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Showing 101–120 of 120")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
});

it("sends the book, severity and rule filters and returns to page 1", async () => {
  getErrors.mockResolvedValue(errPage(120));
  render(<ErrorReportTable refreshKey={0} />);
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.selectOptions(screen.getByLabelText("Book"), "historical");
  await userEvent.selectOptions(screen.getByLabelText("Severity"), "warning");
  await userEvent.selectOptions(screen.getByLabelText("Rule"), "CURRENCY_INVALID");
  await waitFor(() => expect(getErrors).toHaveBeenLastCalledWith(
    expect.objectContaining({ book: "historical", severity: "warning", rule_code: "CURRENCY_INVALID", page: 1 })));
});

it("downloads the CSV with the same filters as the table and surfaces failures", async () => {
  getErrors.mockResolvedValue(errPage(1));
  download.mockRejectedValueOnce(new Error("Download failed")).mockResolvedValue(undefined);
  render(<ErrorReportTable refreshKey={0} />);
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "Download error report (CSV)" }));
  expect(download).toHaveBeenLastCalledWith({ book: undefined, severity: undefined, rule_code: undefined });
  expect(await screen.findByText("Download failed")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Book"), "historical");
  await userEvent.selectOptions(screen.getByLabelText("Severity"), "error");
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "Download error report (CSV)" }));
  expect(download).toHaveBeenLastCalledWith({ book: "historical", severity: "error", rule_code: undefined });
});

it("reloads when refreshKey changes", async () => {
  getErrors.mockResolvedValue(errPage(1));
  const { rerender } = render(<ErrorReportTable refreshKey={0} />);
  await screen.findByRole("table");
  const calls = getErrors.mock.calls.length;
  rerender(<ErrorReportTable refreshKey={1} />);
  await waitFor(() => expect(getErrors.mock.calls.length).toBeGreaterThan(calls));
});
