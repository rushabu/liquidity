import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ladderResponse, ladderRow } from "../test/fixtures";
import { GapChart } from "./GapChart";

const rows = () => ladderResponse().ladder.slice(0, 5);

it("names both series, explains the net gap and labels it per bucket", () => {
  render(<GapChart rows={rows()} currency="EUR" scope="all locations" />);
  expect(screen.getByRole("heading", { name: "Assets vs liabilities by maturity bucket" })).toBeInTheDocument();
  expect(screen.getByText(/in EUR, all locations\. Net gap = assets − liabilities/)).toBeInTheDocument();
  expect(screen.getByText("cash in").closest("li")).toHaveTextContent("Assets");
  expect(screen.getByText("cash out").closest("li")).toHaveTextContent("Liabilities");
  expect(screen.getByText("+140.6M")).toBeInTheDocument();
  expect(screen.getByText("-591.8M")).toBeInTheDocument();
});

it("describes assets, liabilities and the net gap of every bucket for screen readers", () => {
  render(<GapChart rows={rows()} currency="EUR" />);
  const label = screen.getByRole("img").getAttribute("aria-label")!;
  expect(label).toContain("0-1M: assets 140.6M, liabilities 0, net surplus 140.6M");
  expect(label).toContain("3-12M: assets 0, liabilities 591.8M, net shortfall 591.8M");
});

it("draws one column per non-zero side and uses clean axis ticks", () => {
  const two = [ladderRow("0-1M", 0, { asset_cash_at_maturity: 300e6, liability_cash_at_maturity: 120e6, net_gap: 180e6, gap_status: "surplus" }),
    ladderRow("1-3M", 0, { asset_cash_at_maturity: 0, liability_cash_at_maturity: 0, net_gap: 0, gap_status: "balanced" })];
  const { container } = render(<GapChart rows={two} currency="USD" />);
  expect(container.querySelectorAll("path")).toHaveLength(2);
  expect([...container.querySelectorAll(".gapchart-tick")].map((t) => t.textContent)).toEqual(["0", "100M", "200M", "300M"]);
});

it("shows exact amounts on hover and opens the bucket on click", async () => {
  const onSelect = vi.fn();
  const { container } = render(<GapChart rows={rows()} currency="EUR" onSelect={onSelect} />);
  const hit = container.querySelectorAll(".gapchart-hit")[2];
  await userEvent.hover(hit);
  expect(screen.getByText("-591,834,004.01")).toBeInTheDocument();
  expect(screen.getByText("Net gap · shortfall")).toBeInTheDocument();
  await userEvent.click(hit);
  expect(onSelect).toHaveBeenCalledWith("3-12M");
});
