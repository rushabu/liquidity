export type ChipState = "Idle" | "Uploading" | "Completed" | "Warnings" | "Failed";

const TONE: Record<ChipState, string> = {
  Idle: "neutral", Uploading: "primary", Completed: "success", Warnings: "warning", Failed: "error",
};

export function StatusChip({ state }: { state: ChipState }) {
  return <span className="chip" data-tone={TONE[state]}>{state}</span>;
}
