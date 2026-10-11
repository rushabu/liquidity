import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import * as client from "../api/client";
import { clearSticky } from "../lib/sticky";
import { toParams, WhatIfPage } from "./WhatIfPage";

const B = ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"] as const;

const resp = (q: client.WhatIfQuery): client.WhatIfResponse => {
  const shifted = (q.liability_shift_months ?? 0) !== 0 || (q.asset_shift_days ?? 0) !== 0;
  return {
    metadata: { base: "deterministic", as_of_date: "2026-10-10", calculation_date: "2026-10-10", currency: q.currency ?? "EUR",
      location: q.location ?? null, currencies: ["EUR", "USD"], locations: ["Paris", "London"], scope_location: null,
      shifts: { asset: { months: 0, days: q.asset_shift_days ?? 0, text: "x" }, liability: { months: q.liability_shift_months ?? 0, days: 0, text: "y" } },
      scenario: shifted ? "Assets unchanged · liabilities 1 month earlier" : "Assets unchanged · liabilities unchanged" },
    kpis: { total: 373, moved: shifted ? 12 : 0, shifted: shifted ? 300 : 0, pulled_to_today: shifted ? 4 : 0,
      worst_base: { bucket: "3-12M", net: -591.8e6 }, worst_scenario: { bucket: "3-12M", net: shifted ? -640e6 : -591.8e6 }, total_net: -392.4e6 },
    buckets: B.map((b, i) => ({ bucket: b, label: b, start_date: "2026-10-10", end_date: null, base_assets: 0, base_liabilities: 0,
      base_net: [140.6e6, 19.8e6, -591.8e6, -28.9e6, 67.9e6][i], scen_assets: 0, scen_liabilities: 0,
      scen_net: [140.6e6, 19.8e6, -591.8e6, -28.9e6, 67.9e6][i] + (shifted && i === 0 ? -48.2e6 : 0),
      delta: shifted && i === 0 ? -48.2e6 : 0, base_cum: 0, scen_cum: 0, moved_in: 0, moved_out: 0 })),
    movers_total: shifted ? 1 : 0,
    movers: shifted ? [{ transaction_id: "LIA-000042", book: "liability", location: "Paris", customer_segment: "Retail", cash_at_maturity: 1.2e6,
      base_date: "2026-11-15", scenario_date: "2026-10-15", base_bucket: "1-3M", scenario_bucket: "0-1M", pulled_to_today: false }] : [],
    recommendations: [{ currency: "EUR", location: null, scope: "EUR", bucket: "3-12M", severity: "medium", kind: "fund",
      action: "Raise 6–12M funding – EUR 451.2M via term deposits / CDs by 11 Jan 2027", rationale: "Why.", amount: 451.2e6, deterministic_net: 0, ai_net: 0 }],
    notes: ["note"],
  };
};

const spy = vi.spyOn(client, "getWhatIf");
beforeEach(() => { clearSticky(); client.clearAiCache(); spy.mockReset(); spy.mockImplementation(async (q) => resp(q ?? {})); });

const page = () => render(<MemoryRouter><WhatIfPage /></MemoryRouter>);

it("converts the shift controls to API parameters", () => {
  expect(toParams({ n: 1, unit: "months", dir: "earlier" })).toEqual({ months: -1, days: 0 });
  expect(toParams({ n: 2, unit: "weeks", dir: "later" })).toEqual({ months: 0, days: 14 });
  expect(toParams({ n: 10, unit: "days", dir: "earlier" })).toEqual({ months: 0, days: -10 });
  expect(toParams({ n: 99, unit: "months", dir: "later" })).toEqual({ months: 24, days: 0 });
});

it("starts with liabilities 1 month earlier and shows impact, actions and movers", async () => {
  page();
  expect(await screen.findByText("Assets unchanged · liabilities 1 month earlier")).toBeInTheDocument();
  expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ liability_shift_months: -1, liability_shift_days: 0, asset_shift_days: 0 }));
  expect(spy.mock.calls.at(-1)![0]).not.toHaveProperty("base");
  const table = screen.getByRole("region", { name: "Bucket table" });
  expect(within(table).getByText("−48.2M")).toBeInTheDocument();
  expect(screen.getByText(/Raise 6–12M funding – EUR 451.2M/)).toBeInTheDocument();
  expect(screen.getByText("LIA-000042")).toBeInTheDocument();
});

it("is contractual only: no AI toggle, no preset buttons; currency filter and zero shift still work", async () => {
  page();
  await screen.findByText("LIA-000042");
  expect(screen.queryByRole("tab", { name: /AI-adjusted/ })).not.toBeInTheDocument();
  for (const name of ["Liabilities 1 month earlier", "Assets 2 weeks later", "Stress: both", "Reset"])
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  expect(screen.getByText(/No ML model is used/)).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Currency"), "USD");
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ currency: "USD" })));
  const amount = screen.getByLabelText("Liability maturities (deposits): amount");
  await userEvent.clear(amount);
  await userEvent.type(amount, "0");
  expect(await screen.findByText("No contract changes bucket in this scenario.")).toBeInTheDocument();
});

it("chart: labelled change row, hover shows contractual vs scenario figures in words", async () => {
  page();
  await screen.findByText("LIA-000042");
  const chart = screen.getByRole("region", { name: "Net gap chart" });
  expect(within(chart).getByText("Change")).toBeInTheDocument();
  expect(within(chart).getByText("Contractual")).toBeInTheDocument();
  const hit = chart.querySelectorAll(".gapchart-hit")[0] as SVGRectElement;
  await userEvent.click(hit);
  const tip = await within(chart).findByRole("status");
  expect(tip).toHaveTextContent("+140,600,000.00");
  expect(tip).toHaveTextContent("+92,400,000.00");
  expect(tip).toHaveTextContent("−48,200,000.00");
  expect(tip).toHaveTextContent("Surplus shrinks by 48.2M");
});

it("edits a shift by hand: amount, unit and direction", async () => {
  page();
  await screen.findByText("LIA-000042");
  const amount = screen.getByLabelText("Asset maturities (loans): amount");
  await userEvent.clear(amount);
  await userEvent.type(amount, "3");
  await userEvent.selectOptions(screen.getByLabelText("Asset maturities (loans): unit"), "weeks");
  const dir = screen.getByRole("radiogroup", { name: "Asset maturities (loans): direction" });
  await userEvent.click(within(dir).getByRole("radio", { name: "later" }));
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ asset_shift_days: 21, asset_shift_months: 0 })));
});

it("drops remembered results after an upload and never shows old figures under an error", async () => {
  const first = page();
  await screen.findByText("LIA-000042");
  first.unmount();
  client.clearAiCache();                                   // what a successful upload does
  spy.mockImplementation(() => new Promise(() => {}));     // new data still loading
  page();
  expect(screen.getByText(/Running the scenario/)).toBeInTheDocument();
  expect(screen.queryByText("LIA-000042")).not.toBeInTheDocument();
});

it("hides the previous result when a new request fails", async () => {
  page();
  await screen.findByText("LIA-000042");
  spy.mockRejectedValue(new client.ApiError("NETWORK", "Cannot reach the server."));
  await userEvent.selectOptions(screen.getByLabelText("Currency"), "USD");
  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server.");
  expect(screen.queryByText("LIA-000042")).not.toBeInTheDocument();
});

it("shows the server error", async () => {
  spy.mockRejectedValue(new client.ApiError("MODEL_NOT_READY", "The behavioural model needs the historical log."));
  page();
  expect(await screen.findByRole("alert")).toHaveTextContent("historical log");
});
