import { useState } from "react";
import { GitBranch, Route } from "lucide-react";
import RelationshipView from "./RelationshipView";
import RuntimeView from "./RuntimeView";
import "./flow-graph.css";

export default function FlowGraphPage() {
  const [view, setView] = useState<"relationships" | "runtime">(
    "relationships",
  );
  return (
    <section className="panel flow-page" aria-labelledby="flow-graph-title">
      <header className="flow-page-header">
        <div>
          <p className="eyebrow">连接与运行</p>
          <h2 id="flow-graph-title">链路图</h2>
          <p>从账号到会话，或沿一个真实关联查看运行证据。</p>
        </div>
        <div
          className="flow-tabs"
          role="tablist"
          aria-label="链路图视图"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === "Home"
                ? "relationships"
                : event.key === "End"
                  ? "runtime"
                  : view === "relationships"
                    ? "runtime"
                    : "relationships";
            setView(next);
            document.getElementById(`flow-tab-${next}`)?.focus();
          }}
        >
          <button
            id="flow-tab-relationships"
            type="button"
            role="tab"
            tabIndex={view === "relationships" ? 0 : -1}
            aria-selected={view === "relationships"}
            aria-controls="flow-panel-relationships"
            onClick={() => setView("relationships")}
          >
            <GitBranch aria-hidden="true" />
            配置关系
          </button>
          <button
            id="flow-tab-runtime"
            type="button"
            role="tab"
            tabIndex={view === "runtime" ? 0 : -1}
            aria-selected={view === "runtime"}
            aria-controls="flow-panel-runtime"
            onClick={() => setView("runtime")}
          >
            <Route aria-hidden="true" />
            实际运行
          </button>
        </div>
      </header>
      <div
        role="tabpanel"
        id="flow-panel-relationships"
        aria-labelledby="flow-tab-relationships"
        hidden={view !== "relationships"}
      >
        {view === "relationships" && <RelationshipView />}
      </div>
      <div
        role="tabpanel"
        id="flow-panel-runtime"
        aria-labelledby="flow-tab-runtime"
        hidden={view !== "runtime"}
      >
        {view === "runtime" && <RuntimeView />}
      </div>
    </section>
  );
}
