import { useState } from "react";
import { CaseDetail } from "./pages/CaseDetail";
import { CaseQueue } from "./pages/CaseQueue";
import { SimulateTransaction } from "./pages/SimulateTransaction";

// Three screens, no router library -- a single `View` union is enough state to cover
// "queue", "simulate", and "detail(caseId)", and every transition is an explicit, named
// action (openCase / back / resolved) rather than URL parsing. No websockets/polling either:
// the pipeline is synchronous, so every screen re-fetches once on entry and shows a result,
// not a live stream (Checkpoint C14 non-goals).
type View = { screen: "queue" } | { screen: "simulate" } | { screen: "detail"; caseId: string };

function App() {
  const [view, setView] = useState<View>({ screen: "queue" });
  const [queueRefreshKey, setQueueRefreshKey] = useState(0);

  return (
    <div className="app-shell">
      <header className="app-nav">
        <span className="app-title">Mandate Drift Guard</span>
        <nav>
          <button
            className={view.screen === "queue" || view.screen === "detail" ? "nav-active" : ""}
            onClick={() => setView({ screen: "queue" })}
          >
            Case Queue
          </button>
          <button
            className={view.screen === "simulate" ? "nav-active" : ""}
            onClick={() => setView({ screen: "simulate" })}
          >
            Simulate Transaction
          </button>
        </nav>
      </header>

      <main>
        {view.screen === "queue" && (
          <CaseQueue key={queueRefreshKey} onOpenCase={(caseId) => setView({ screen: "detail", caseId })} />
        )}
        {view.screen === "detail" && (
          <CaseDetail
            caseId={view.caseId}
            onBack={() => setView({ screen: "queue" })}
            onResolved={() => {
              setQueueRefreshKey((k) => k + 1);
              setView({ screen: "queue" });
            }}
          />
        )}
        {view.screen === "simulate" && (
          <SimulateTransaction onOpenCase={(caseId) => setView({ screen: "detail", caseId })} />
        )}
      </main>
    </div>
  );
}

export default App;
