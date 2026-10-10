import { render, screen } from "@testing-library/react";
import { batch } from "../test/fixtures";
import { BatchHistory } from "./BatchHistory";

it("lists uploads with Active and Replaced badges", () => {
  render(<BatchHistory loading={false} error={null} items={[batch(), batch({ id: "b0", is_active: false, rows_valid: 700 })]} />);
  expect(screen.getByText("Active")).toBeInTheDocument();
  expect(screen.getByText("Replaced")).toBeInTheDocument();
  expect(screen.getAllByText("Asset Book")).toHaveLength(2);
});

it("covers empty, loading and error states", () => {
  const { rerender } = render(<BatchHistory loading={false} error={null} items={[]} />);
  expect(screen.getByText("No uploads yet")).toBeInTheDocument();
  rerender(<BatchHistory loading error={null} items={[]} />);
  expect(screen.getByText("Loading…")).toBeInTheDocument();
  rerender(<BatchHistory loading={false} error="boom" items={[]} />);
  expect(screen.getByRole("alert")).toHaveTextContent("boom");
});
