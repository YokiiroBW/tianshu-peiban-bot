import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { actorScope, useDomainTarget } from "../../api/useDomainResource";
import { StatePanel } from "../../components/StatePanel";
import MemoryRecords from "./MemoryRecords";
import MemoryReview from "./MemoryReview";
import { Matters } from "./Matters";
import { MemoryTimeFilter, useMemoryTime } from "./MemoryTimeFilter";
import { MemoryEvidenceSearch } from "./MemoryEvidenceSearch";
import "./memory.css";

export default function MemoryPage({ section: _section }: { section: number }) {
  const workspace = useRoleWorkspace();
  const selected = useDomainTarget();
  const [scopeMode, setScopeMode] = useState("conversation");
  const [tab, setTab] = useState("claims");
  const [query, setQuery] = useMemoryTime();
  const scope = scopeMode === "actor" ? actorScope(workspace.actor) : selected;
  if (!workspace.actor)
    return (
      <StatePanel kind="empty" title="请选择角色">
        <p>选择角色后读取实际授权范围内的记忆。</p>
      </StatePanel>
    );
  return (
    <div className="memory-page">
      <header className="section-heading">
        <div>
          <h2>记忆与经历</h2>
          <p className="muted">
            条目有原文依据；推测、转述和本人确认分别显示，来源撤回后重新读取会同步失效。
          </p>
        </div>
        <label>
          读取范围
          <select
            value={scopeMode}
            onChange={(event) => setScopeMode(event.target.value)}
          >
            <option value="conversation">当前会话</option>
            <option value="actor">此角色已授权范围</option>
          </select>
        </label>
      </header>
      <MemoryTimeFilter value={query} onChange={setQuery} />
      <nav className="models-actions" aria-label="记忆内容">
        {[
          ["claims", "记忆条目"],
          ["episodes", "经历与反思"],
          ["matters", "事项与约定"],
          ["evidence", "完整证据检索"],
        ].map(([id, name]) => (
          <button
            className="button"
            key={id}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {name}
          </button>
        ))}
      </nav>
      <div key={JSON.stringify([scope, tab, query])}>
        {scopeMode === "conversation" && !selected.conversation_id ? (
          <StatePanel kind="empty" title="请选择当前会话">
            <p>选择会话后读取其实际范围，或显式改为此角色已授权范围。</p>
          </StatePanel>
        ) : (
          <>
            {tab === "claims" ? (
              <MemoryRecords scope={scope} query={query} />
            ) : tab === "episodes" ? (
              <MemoryReview scope={scope} query={query} />
            ) : tab === "evidence" ? (
              <MemoryEvidenceSearch scope={scope} query={query} />
            ) : (
              <Matters scope={scope} query={query} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
