import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import * as client from "../api/client";
import { ladderResponse } from "../test/fixtures";
import { LadderPage } from "./LadderPage";

const show = () => render(<MemoryRouter><LadderPage /></MemoryRouter>);
afterEach(() => vi.restoreAllMocks());

it("shows the ladder with exact net gaps and a roll-up by default", async () => {
  vi.spyOn(client, "getLadder").mockResolvedValue(ladderResponse());
  show();
  expect((await screen.findAllByText("140,596,536.28", { selector: "td" })).length).toBeGreaterThan(0);
  expect(screen.getAllByText("591,834,004.01", { selector: "td" }).length).toBeGreaterThan(0);
  expect(screen.getAllByText("-591,834,004.01", { selector: "td" })).toHaveLength(1);
  expect(screen.getByText(/1,856 of 1,856 transactions/)).toBeInTheDocument();
  expect(screen.getAllByText("shortfall").length).toBeGreaterThan(0);
});

it("explains a not-ready snapshot and points to the upload page", async () => {
  vi.spyOn(client, "getLadder").mockRejectedValue(new client.ApiError("SNAPSHOT_NOT_READY", "The ladder needs an active asset book AND liability book", 409));
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(/asset book AND liability book/);
  expect(screen.getByRole("link", { name: "Go to upload" })).toHaveAttribute("href", "/upload");
});

it("rejects an invalid calculation date without calling the API again", async () => {
  const spy = vi.spyOn(client, "getLadder").mockResolvedValue(ladderResponse());
  show();
  await screen.findAllByText("140,596,536.28", { selector: "td" });
  await userEvent.type(screen.getByLabelText(/Calculation date/), "2026-13-45");
  expect(screen.getByText(/real date/)).toBeInTheDocument();
  expect(spy).toHaveBeenCalledTimes(1);
});

it("opens the drill-down for a bucket and then the step-by-step explanation", async () => {
  vi.spyOn(client, "getLadder").mockResolvedValue(ladderResponse());
  const txn = vi.spyOn(client, "getLadderTransactions").mockResolvedValue({
    total: 1, page: 1, page_size: 25, calculation_date: "2026-10-10",
    summary: { assets: 5, liabilities: 2, net_gap: 3, currency: "EUR" },
    items: [{ transaction_id: "AST-006273", book: "asset", entity: "IBC UK", location: "London", customer_id: "CUS-1", currency: "EUR", amount: 1000,
      interest_rate_pct: 5, transaction_date: "2026-06-20", maturity_date: "2026-12-20", tenor_days: 183, interest_amount: 25.07, cash_at_maturity: 1025.07,
      bucket: "1-3M", days_to_maturity: 71, past_due: false }] });
  vi.spyOn(client, "getExplanation").mockResolvedValue({
    transaction_id: "AST-006273", type: "asset", currency: "EUR", location: "London", bucket: "1-3M", past_due: false, status: "in_ladder",
    interest: 25.07, cash_at_maturity: 1025.07, stored_interest_matches_recomputation: true, calculation_date: "2026-10-10",
    steps: [{ n: 1, title: "Read the stored transaction", working: "w", result: "r" }, { n: 3, title: "Simple interest", working: "1000 × 5%", result: "25.07" }] });
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Show 1-3M transactions" }));
  await waitFor(() => expect(txn).toHaveBeenCalledWith(expect.objectContaining({ currency: "EUR", bucket: "1-3M" })));
  await userEvent.click(await screen.findByRole("button", { name: "AST-006273" }));
  expect(await screen.findByText("Simple interest")).toBeInTheDocument();
});

it("keeps the newest result when an older request answers last", async () => {
  let late!: (v: client.LadderResponse) => void;
  vi.spyOn(client, "getLadder")
    .mockResolvedValueOnce(ladderResponse())
    .mockImplementationOnce(() => new Promise((r) => { late = r; }))
    .mockResolvedValueOnce(ladderResponse({ warnings: ["newest"] }));
  show();
  const policy = await screen.findByLabelText(/Past-due contracts/);
  await userEvent.selectOptions(policy, "bucket_0_1m");
  await userEvent.selectOptions(policy, "exclude");
  expect(await screen.findByText("newest")).toBeInTheDocument();
  await act(async () => late(ladderResponse({ warnings: ["stale"] })));
  expect(screen.queryByText("stale")).not.toBeInTheDocument();
  expect(screen.getByText("newest")).toBeInTheDocument();
});
