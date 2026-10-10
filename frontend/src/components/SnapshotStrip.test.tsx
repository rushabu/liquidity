import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { batch, emptySummary } from "../test/fixtures";
import { SnapshotStrip } from "./SnapshotStrip";

const wrap = (s: Parameters<typeof SnapshotStrip>[0]["summary"]) => render(<MemoryRouter><SnapshotStrip summary={s} /></MemoryRouter>);

it("is disabled with no data", () => {
  wrap(emptySummary());
  expect(screen.getByText("No data loaded yet")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View deterministic ladder/ })).toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByRole("link", { name: /View deterministic ladder/ })).not.toHaveAttribute("href");
});

it("stays disabled until all three books share the snapshot", () => {
  wrap({ ...emptySummary(), as_of_date: "2026-10-10", rows_stored_total: 753, books: { ...emptySummary().books, asset: batch() } });
  expect(screen.getByText(/1 of 3 books loaded/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View deterministic ladder/ })).toHaveAttribute("aria-disabled", "true");
});

it("links to /ladder once ready and states the stored row count", () => {
  wrap({ as_of_date: "2026-10-10", rows_stored_total: 18035, ready: true,
         books: { asset: batch(), liability: batch({ id: "b2", book: "liability" }), historical: batch({ id: "b3", book: "historical" }) } });
  expect(screen.getByText("18,035")).toBeInTheDocument();
  expect(screen.getByText("2026-10-10")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View deterministic ladder/ })).toHaveAttribute("href", "/ladder");
});
