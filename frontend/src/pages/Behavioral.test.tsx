import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import * as client from "../api/client";
import { clearSticky } from "../lib/sticky";
import { AiGapPage } from "./AiGapPage";

beforeEach(() => { clearSticky(); client.clearAiCache(); });
afterEach(() => vi.restoreAllMocks());

const model: client.ModelSummary = {
  name: "early-exit-logit-v2", historical_batch: { id: "h1", filename: "historical_log - 10102026.xlsx", as_of_date: "2026-10-10", rows: 16179 },
  type: "logistic regression", trained_on: 14325, early_exit_rate: 0.369, holdout_auc: 0.744, holdout_brier: 0.234, holdout_f1: 0.713, holdout_accuracy: 0.625,
};
const meta = {
  as_of_date: "2026-10-10", calculation_date: "2026-10-10", past_due_policy: "exclude" as const, scope_location: null, market_shift_pp: 0,
  currencies: ["EUR", "USD"], locations: ["London", "Paris"], model,
};
const B: client.BucketKey[] = ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"];
const nets: [number, number][] = [[140.6e6, 140.6e6], [19.8e6, -49e6], [-591.8e6, -445.8e6], [-28.9e6, -129.6e6], [67.9e6, 91.4e6]];

const comparison = (currency = "EUR"): client.CompareResponse => ({
  metadata: { ...meta, simulations: 1000, currency, location: null, side: null, segment: null, horizon_months: 24 },
  kpis: { moved: 21, total: 373, shifted: 342, biggest_gain: { month: 16, amount: 211.9e6 }, deepest_opposite: { month: 6, amount: -138.8e6 },
    det_total: -392.4e6, ai_total: -392.4e6 },
  months: Array.from({ length: 25 }, (_, m) => ({ month: m, end_date: "2026-10-10", det_net: 0, ai_net: 0, det_cum: -m * 1e6, ai_cum: -m * 1.1e6,
    diff: m === 6 ? -138.8e6 : m ? 1e6 : 0, note: m === 6 ? "Deposits expected to leave earlier." : "x", bucket: m ? (m <= 1 ? "0-1M" : m <= 3 ? "1-3M" : "3-12M") : null })),
  buckets: B.map((b, i) => ({ bucket: b, label: b, start_date: "2026-10-10", end_date: null, det_assets: 0, det_liabilities: 0, det_net: nets[i][0],
    ai_assets: 0, ai_liabilities: 0, ai_net: nets[i][1], delta: nets[i][1] - nets[i][0], det_count: 1, ai_count: 1, p5: -1, p50: 0, p95: 1, p_shortfall: 0.5 })),
  recommendations: [{ currency: "EUR", location: null, scope: "EUR", bucket: "1-3M", severity: "high", action: "Raise short-term funding – EUR 49.0M",
    rationale: "Expected shortfall. More.", amount: 49e6, deterministic_net: 19.8e6, ai_net: -49e6 }],
  overview: [], notes: ["note"],
});

const why = (shift = 0): client.WhyResponse => ({
  metadata: { ...meta, currency: "EUR", location: null },
  buckets: B.map((b, i) => ({ bucket: b, moved_in: i === 1 ? 8 : 0, moved_out: 0, shift: nets[i][1] - nets[i][0], det_net: nets[i][0], ai_net: nets[i][1],
    text: i === 1 ? "8 contracts moved in. Mostly Retail deposits paid below market: depositors are likely to leave early, so outflows arrive sooner." : "No contracts change bucket.",
    largest: i === 2 })),
  drivers: [{ label: "Rate vs market", strength: 1, log_odds: 0.9, level: "High" }, { label: "Tenor, amount", strength: 0.01, log_odds: 0.01, level: "~0" }],
  who_moved: { total: 21, groups: [{ label: "SME deposits", segment: "SME", side: "liability", count: 21, share: 1 }] },
  scenario: { market_shift_pp: shift, loans_repaid_early: { base: 47.5, scenario: 47.5 + (shift ? 20 : 0), change: shift ? 20 : 0 },
    deposits_withdrawn_early: { base: 42.9, scenario: 42.9, change: 0 }, buckets: [],
    headline: { bucket: "1-3M", base: -49e6, scenario: shift ? -0.2e6 : -49e6, change: 0 }, note: "n" },
});

const txnPage: client.AiTxnPage = {
  total: 1, page: 1, page_size: 10, calculation_date: "2026-10-10", summary: null,
  kpis: { count: 1, cash_pulled_in: -3.89e6, avg_p_early: 0.32, avg_spread_pp: -1.1 },
  items: [{ transaction_id: "LIA-008646", book: "liability", entity: "IBC USA", location: "New York", customer_id: "CUS-1", customer_segment: "SME", currency: "EUR",
    amount: 3792000, interest_rate_pct: 4.3984, market_rate_pct: 5.5, spread_pp: -1.1016, transaction_date: "2026-06-06", contractual_maturity_date: "2027-01-31",
    adjusted_maturity_date: "2027-01-10", expected_exit_date: "2026-11-25", shift_days: 21, p_early: 0.32, p_early_lifetime: 0.38, interest_amount: 99615.45,
    cash_at_maturity: 3891615.45, bucket: "1-3M", deterministic_bucket: "3-12M", moved: true, days_to_maturity: 92, past_due: false,
    reasons: [{ text: "Spread −1.1 pp", tone: "up" }, { text: "Better rates elsewhere", tone: "neutral" }] }],
};
const explanation: client.AiExplanation = {
  transaction_id: "LIA-008646", type: "liability", currency: "EUR", location: "New York", customer_segment: "SME", calculation_date: "2026-10-10",
  p_early_base: 0.38, p_early: 0.32, elapsed_fraction: 0.53, transaction_date: "2026-06-06", contractual_maturity_date: "2027-01-31",
  expected_exit_date: "2026-11-25", adjusted_maturity_date: "2027-01-10", shift_days: 21, deterministic_bucket: "3-12M", ai_bucket: "1-3M",
  cash_at_maturity: 3891615.45, interest_rate_pct: 4.3984, market_rate_pct: 5.5, spread_pp: -1.1016, tenor_years: 0.65,
  formula: "adjusted = 2027-01-31 − 32.0% × (…) = 2027-01-10", reasons: [],
  rationale: { average_probability: 0.35, summary: "38% chance of early withdrawal.", drivers: [
    { feature: "incentive_pp", label: "Rate vs market", value: "Rate spread −1.1 pp", log_odds: 0.18, effect_pp: 4, direction: "raises" },
    { feature: "client_rating", label: "Client rating", value: "Client rating 4", log_odds: -0.08, effect_pp: -2, direction: "lowers" }] },
};

const show = (url = "/ai") => render(<MemoryRouter initialEntries={[url]}><AiGapPage /></MemoryRouter>);

it("Compare shows KPIs, both nets per bucket, the selected month and the actions", async () => {
  vi.spyOn(client, "getComparison").mockResolvedValue(comparison());
  show();
  expect(await screen.findByText("21 / 373")).toBeInTheDocument();
  expect(screen.getByText(/calculation date 2026-10-10 · model early-exit-logit-v2/)).toBeInTheDocument();
  const table = screen.getByRole("region", { name: "Bucket comparison" });
  const row = within(table).getByRole("button", { name: "1-3M" }).closest("tr")!;
  expect(within(row).getByText("+19.8M")).toBeInTheDocument();
  expect(within(row).getByText("−49.0M")).toBeInTheDocument();
  expect(screen.getByText(/Selected: month 6/)).toBeInTheDocument();
  expect(screen.getByText("Deposits expected to leave earlier.", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Raise short-term funding – EUR 49.0M")).toBeInTheDocument();
  expect(screen.getByText(/−392.4M \(same\)/)).toBeInTheDocument();
});

it("Compare re-queries on filters and a bucket click opens the drill-down for that bucket", async () => {
  const cmp = vi.spyOn(client, "getComparison").mockResolvedValue(comparison());
  const txns = vi.spyOn(client, "getAiTransactions").mockResolvedValue(txnPage);
  vi.spyOn(client, "getAiExplanation").mockResolvedValue(explanation);
  show();
  await userEvent.selectOptions(await screen.findByLabelText("Side"), "liability");
  await waitFor(() => expect(cmp).toHaveBeenLastCalledWith(expect.objectContaining({ currency: "EUR", side: "liability" })));
  await userEvent.click(within(screen.getByRole("region", { name: "Bucket comparison" })).getByRole("button", { name: "1-3M" }));
  await waitFor(() => expect(txns).toHaveBeenCalledWith(expect.objectContaining({ bucket: "1-3M", currency: "EUR", moved_only: true, type: "liability" })));
  expect(screen.getByRole("tab", { name: "Drill-down" })).toHaveAttribute("aria-selected", "true");
});

it("Why it changed explains buckets, drivers, movers and runs the market-rate scenario", async () => {
  const spy = vi.spyOn(client, "getWhy").mockImplementation((p) => Promise.resolve(why(p?.market_shift_pp ?? 0)));
  show("/ai?view=why");
  expect(await screen.findByText("Retail deposits")).toBeInTheDocument();
  expect(screen.getByText(/Largest change/)).toBeInTheDocument();
  expect(screen.getByText("High")).toBeInTheDocument();
  expect(screen.getByText("SME deposits 100%")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Market rate shift"), { target: { value: "-0.8" } });
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ market_shift_pp: -0.8 })), { timeout: 2000 });
  expect(await screen.findByText(/\+20/)).toBeInTheDocument();
});

it("Drill-down lists contracts with reasons, explains the selected one and exports CSV", async () => {
  const txns = vi.spyOn(client, "getAiTransactions").mockResolvedValue(txnPage);
  vi.spyOn(client, "getAiExplanation").mockResolvedValue(explanation);
  const dl = vi.spyOn(client, "downloadAiPredictions").mockResolvedValue();
  show("/ai?view=drill");
  const row = (await screen.findByRole("button", { name: "Why LIA-008646" })).closest("tr")!;
  expect(within(row).getByText("Liability · SME")).toBeInTheDocument();
  expect(within(row).getByText("3-12M → 1-3M")).toBeInTheDocument();
  expect(within(row).getByText("Better rates elsewhere")).toBeInTheDocument();
  const panel = await screen.findByRole("complementary", { name: "Why LIA-008646 moved" });
  expect(within(panel).getByText("P(early) 32%")).toBeInTheDocument();
  expect(within(panel).getByText("2027-01-10 · 1-3M")).toBeInTheDocument();
  expect(within(panel).getByText("(unchanged)")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("P(early) range"), "40-100");
  await waitFor(() => expect(txns).toHaveBeenLastCalledWith(expect.objectContaining({ p_min: 0.4, p_max: 1 })));
  await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  expect(dl).toHaveBeenCalledWith(expect.objectContaining({ p_min: 0.4, bucket: "1-3M" }));
});

it("explains a missing historical log", async () => {
  vi.spyOn(client, "getComparison").mockRejectedValue(new client.ApiError("MODEL_NOT_READY", "The behavioural model needs the historical log.", 409));
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(/historical log/);
});

it("keeps data and filters when switching tabs: no second request", async () => {
  const cmp = vi.spyOn(client, "getComparison").mockResolvedValue(comparison());
  const w = vi.spyOn(client, "getWhy").mockImplementation((p) => Promise.resolve(why(p?.market_shift_pp ?? 0)));
  show();
  await userEvent.selectOptions(await screen.findByLabelText("Segment"), "Retail");
  await waitFor(() => expect(cmp).toHaveBeenCalledTimes(2));
  await userEvent.click(screen.getByRole("tab", { name: "Why it changed" }));
  expect(await screen.findByText("Retail deposits")).toBeInTheDocument();
  const whyCalls = w.mock.calls.length;
  await userEvent.click(screen.getByRole("tab", { name: "Compare" }));
  expect(screen.getByLabelText("Segment")).toHaveValue("Retail");             // filter kept
  await userEvent.click(screen.getByRole("tab", { name: "Why it changed" }));
  expect(cmp).toHaveBeenCalledTimes(2);                                      // nothing refetched
  expect(w).toHaveBeenCalledTimes(whyCalls);
});

it("the response store answers repeated requests from memory and is cleared on demand", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
  await client.getModelCard();
  await client.getModelCard();
  expect(fetchSpy).toHaveBeenCalledTimes(1);
  client.clearAiCache();
  await client.getModelCard();
  expect(fetchSpy).toHaveBeenCalledTimes(2);
});
