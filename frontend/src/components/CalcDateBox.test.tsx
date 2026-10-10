import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CalcDateBox } from "./CalcDateBox";

it("shows the source hint and no error for a valid date", () => {
  render(<CalcDateBox value="2026-10-10" onChange={() => {}} source="filename" />);
  expect(screen.getByLabelText("Calculation date")).toHaveValue("2026-10-10");
  expect(screen.getByLabelText("Calculation date")).toHaveAttribute("aria-invalid", "false");
  expect(screen.getByText(/Read from the file name/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("flags an impossible date with an accessible error", () => {
  render(<CalcDateBox value="2026-02-30" onChange={() => {}} source="manual" />);
  expect(screen.getByLabelText("Calculation date")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("YYYY-MM-DD");
});

it("reports typing to the parent", async () => {
  const onChange = vi.fn();
  render(<CalcDateBox value="" onChange={onChange} source="none" />);
  await userEvent.type(screen.getByLabelText("Calculation date"), "2");
  expect(onChange).toHaveBeenCalledWith("2");
});
