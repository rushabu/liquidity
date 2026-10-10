import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CompareView } from "../components/ai/CompareView";
import { DrillView } from "../components/ai/DrillView";
import { LadderView } from "../components/ai/LadderView";
import type { DrillPreset, PageMeta, Shared } from "../components/ai/types";
import { WhyView } from "../components/ai/WhyView";
import { useSticky } from "../lib/sticky";

const VIEWS = [
  { key: "compare", label: "Compare" },
  { key: "ladder", label: "AI ladder" },
  { key: "why", label: "Why it changed" },
  { key: "drill", label: "Drill-down" },
] as const;
type View = (typeof VIEWS)[number]["key"];

/** AI gap ladder: deterministic vs behaviour-adjusted cash flows, in four views that share currency/location. */
export function AiGapPage() {
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.find((v) => v.key === params.get("view"))?.key ?? "compare") as View;
  const [currency, setCurrency] = useSticky("ai.currency", "");
  const [location, setLocation] = useSticky("ai.location", "");
  const [preset, setPreset] = useSticky<DrillPreset | null>("ai.preset", null);
  const [meta, setMeta] = useSticky<PageMeta | null>("ai.meta", null);
  const [visited, setVisited] = useSticky<View[]>("ai.visited", []);
  useEffect(() => { setVisited((v) => (v.includes(view) ? v : [...v, view])); }, [view, setVisited]);
  const shared: Shared = { currency, setCurrency, location, setLocation };
  const go = (v: View) => setParams(v === "compare" ? {} : { view: v });
  const drill = (p: DrillPreset) => { setPreset({ ...p, id: Date.now() }); go("drill"); };

  return (
    <main className="page ai-page">
      <div className="page-head">
        <div>
          <h1>AI gap ladder</h1>
          <p>
            Deterministic vs behaviour-adjusted cash flows
            {meta && <> · calculation date {meta.calculation_date} · model {meta.model.name}</>}
          </p>
        </div>
        <div className="segmented" role="tablist" aria-label="AI gap ladder views">
          {VIEWS.map((v) => (
            <button key={v.key} type="button" role="tab" aria-selected={view === v.key} onClick={() => go(v.key)}>{v.label}</button>
          ))}
        </div>
      </div>
      {/* Views stay mounted once opened (hidden when inactive): switching tabs keeps their data and filters. */}
      {[...new Set<View>([...visited, view])].map((v) => (
        <div key={v} hidden={v !== view} className="ai-view">
          {v === "compare" && <CompareView shared={shared} onDrill={drill} onMeta={setMeta} />}
          {v === "ladder" && <LadderView shared={shared} onDrill={drill} onMeta={setMeta} />}
          {v === "why" && <WhyView shared={shared} onDrill={drill} onMeta={setMeta} />}
          {v === "drill" && <DrillView shared={shared} preset={preset} />}
        </div>
      ))}
      <p className="meta">Needs the asset, liability and historical books for the same date. <Link to="/upload">Data ingestion</Link> · <Link to="/ladder">Deterministic ladder</Link></p>
    </main>
  );
}
