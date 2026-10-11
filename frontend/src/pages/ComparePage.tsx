import { useNavigate } from "react-router-dom";
import { CompareView } from "../components/ai/CompareView";
import type { DrillPreset, PageMeta, Shared } from "../components/ai/types";
import { useSticky } from "../lib/sticky";

/** Comparison: deterministic (contractual) vs probabilistic (AI-adjusted) ladders, KPIs, monthly curve and the
 *  recommended actions. A bucket click opens the probabilistic Drill-down with that bucket preselected. */
export function ComparePage() {
  const navigate = useNavigate();
  const [currency, setCurrency] = useSticky("ai.currency", "");
  const [location, setLocation] = useSticky("ai.location", "");
  const [, setPreset] = useSticky<DrillPreset | null>("ai.preset", null);
  const [meta, setMeta] = useSticky<PageMeta | null>("ai.meta", null);
  const shared: Shared = { currency, setCurrency, location, setLocation };
  const drill = (p: DrillPreset) => { setPreset({ ...p, id: Date.now() }); navigate("/ai?view=drill"); };

  return (
    <main className="page ai-page">
      <div className="page-head">
        <div>
          <h1>Comparison</h1>
          <p>
            Deterministic (contractual) vs probabilistic (behaviour-adjusted) gap ladder, with recommended actions
            {meta && <> · calculation date {meta.calculation_date} · model {meta.model.name}</>}
          </p>
        </div>
      </div>
      <CompareView shared={shared} onDrill={drill} onMeta={setMeta} />
    </main>
  );
}
