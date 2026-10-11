import { useEffect } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { DrillView } from "../components/ai/DrillView";
import { LadderView } from "../components/ai/LadderView";
import type { DrillPreset, PageMeta, Shared } from "../components/ai/types";
import { WhyView } from "../components/ai/WhyView";
import { useSticky } from "../lib/sticky";

const VIEWS = [
  { key: "ladder", label: "Ladder" },
  { key: "why", label: "Why it changed" },
  { key: "drill", label: "Drill-down" },
] as const;
type View = (typeof VIEWS)[number]["key"];

/** Probabilistic (AI-adjusted) gap ladder: Ladder, Why it changed and Drill-down share currency/location.
 *  The deterministic-vs-probabilistic comparison is its own page (/compare). */
export function AiGapPage() {
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.find((v) => v.key === params.get("view"))?.key ?? "ladder") as View;
  const [currency, setCurrency] = useSticky("ai.currency", "");
  const [location, setLocation] = useSticky("ai.location", "");
  const [preset, setPreset] = useSticky<DrillPreset | null>("ai.preset", null);
  const [meta, setMeta] = useSticky<PageMeta | null>("ai.meta", null);
  const [visited, setVisited] = useSticky<View[]>("ai.visited", []);
  useEffect(() => { setVisited((v) => (v.includes(view) ? v : [...v, view])); }, [view, setVisited]);
  const shared: Shared = { currency, setCurrency, location, setLocation };
  const go = (v: View) => setParams(v === "ladder" ? {} : { view: v });
  const drill = (p: DrillPreset) => { setPreset({ ...p, id: Date.now() }); go("drill"); };

  if (params.get("view") === "compare") return <Navigate to="/compare" replace />;   // old links

  return (
    <main className="page ai-page">
      <div className="page-head">
        <div>
          <h1>Probabilistic gap ladder</h1>
          <p>
            Behaviour-adjusted cash flows (AI-predicted early exits)
            {meta && <> · calculation date {meta.calculation_date} · model {meta.model.name}</>}
          </p>
        </div>
        <div className="segmented" role="tablist" aria-label="Probabilistic ladder views">
          {VIEWS.map((v) => (
            <button key={v.key} type="button" role="tab" aria-selected={view === v.key} onClick={() => go(v.key)}>{v.label}</button>
          ))}
        </div>
      </div>
      {/* Views stay mounted once opened (hidden when inactive): switching tabs keeps their data and filters. */}
      {[...new Set<View>([...visited, view])].map((v) => (
        <div key={v} hidden={v !== view} className="ai-view">
          {v === "ladder" && <LadderView shared={shared} onDrill={drill} onMeta={setMeta} />}
          {v === "why" && <WhyView shared={shared} onDrill={drill} onMeta={setMeta} />}
          {v === "drill" && <DrillView shared={shared} preset={preset} />}
        </div>
      ))}
      <p className="meta">Needs the asset, liability and historical books for the same date. <Link to="/upload">Data ingestion</Link> · <Link to="/ladder">Deterministic ladder</Link> · <Link to="/compare">Comparison</Link></p>
    </main>
  );
}
