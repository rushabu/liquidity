import type { BucketKey, Segment, Side } from "../../api/client";

/** State shared by the AI gap ladder tabs. "" = not filtered. */
export interface Shared {
  currency: string;
  setCurrency: (c: string) => void;
  location: string;
  setLocation: (l: string) => void;
}

/** Filters handed to the Drill-down tab from a click on another tab. */
export interface DrillPreset {
  id?: number;                 // set per click, so the same preset is applied once
  bucket?: BucketKey;
  currency?: string;
  side?: Side;
  segment?: Segment;
  moved_only?: boolean;
}

/** What the page header needs from whichever view loaded first. */
export interface PageMeta { calculation_date: string; model: { name: string } }
