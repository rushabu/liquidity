import { useId } from "react";
import { isValidIsoDate } from "../lib/dates";

interface Props {
  value: string;
  onChange: (v: string) => void;
  source: "filename" | "manual" | "none";
}

/** The snapshot ("calculation") date shared by all three uploads. */
export function CalcDateBox({ value, onChange, source }: Props) {
  const id = useId();
  const invalid = value !== "" && !isValidIsoDate(value);
  const helpId = `${id}-help`;
  const help =
    source === "filename" ? "Read from the file name (DDMMYYYY). Applies to all three books."
    : source === "manual" ? "Set manually. Applies to all three books."
    : "Choose a file named like “asset_book - 10102026.xlsx”, or type the date.";
  return (
    <div className="datebox">
      <label htmlFor={id}>Calculation date</label>
      <input
        id={id}
        className="mono"
        type="text"
        inputMode="numeric"
        placeholder="YYYY-MM-DD"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value.trim())}
        aria-invalid={invalid}
        aria-describedby={helpId}
      />
      {invalid ? (
        <p id={helpId} className="err" role="alert">Enter a real date as YYYY-MM-DD.</p>
      ) : (
        <p id={helpId} className="help">{help}</p>
      )}
    </div>
  );
}
