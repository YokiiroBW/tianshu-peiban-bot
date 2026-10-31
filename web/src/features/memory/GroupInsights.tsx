import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { Conversation } from "../people/types";
import { ArchivePanel } from "../resources/ArchivePanel";
import MemoryRecords from "./MemoryRecords";
import MemoryReview from "./MemoryReview";
import { Matters } from "./Matters";
import { showValue, type Projection } from "./types";

export default function GroupInsights({ group }: { group: Conversation }) {
  const scope = {
    actor_id: group.actor_id,
    conversation_id: group.conversation_id,
    person_id: null,
  };
  const view = useDomainResource<Projection>("memory/projection", {
    ...scope,
    group_id: group.conversation_id,
  });
  const [tab, setTab] = useState("topics");
  const topics =
    view.data?.claims.filter((claim) =>
      /topic|discussion|question|decision|主题|话题/.test(claim.predicate),
    ) ?? [];
  return (
    <div className="group-workspace">
      <nav className="models-actions" aria-label="群会话内容">
        {[
          ["topics", "共同话题"],
          ["claims", "有来源的认识"],
          ["episodes", "经历与反思"],
          ["matters", "群内事项"],
          ["archive", "历史原文"],
        ].map(([id, label]) => (
          <button
            className="button"
            key={id}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <section className="panel" key={tab}>
        {tab === "topics" && (
          <>
            <div className="section-heading">
              <h3>有依据的话题与讨论</h3>
              <button className="button" onClick={() => void view.refresh()}>
                刷新
              </button>
            </div>
            <p className="muted">
              仅展示当前群范围实际记录的话题条目；推测仍按推测显示，不把成员观点合并成全群立场。
            </p>
            {view.error && <p role="alert">{view.error}</p>}
            {view.loading && <p>正在核验话题来源…</p>}
            {topics.map((item) => (
              <article
                className="memory-record"
                key={`${item.claim_id}:${item.revision}`}
              >
                <h4>{item.predicate}</h4>
                <p>{showValue(item.value)}</p>
                <small>
                  主体 {item.subject_id} · {item.assertion} ·{" "}
                  {item.polarity ? "肯定" : "否定"}
                </small>
                <SourceEvidence sources={item.source_refs} scope={scope} />
              </article>
            ))}
            {view.data && !topics.length && (
              <p>
                当前投影尚无可显示的有效话题。可在“有来源的认识”继续分页读取实际条目。
              </p>
            )}
            {view.data && !view.data.coverage.complete && (
              <p className="muted">
                本投影范围存在后续或缺口，请按各内容列表继续读取。
              </p>
            )}
          </>
        )}
        {tab === "claims" && (
          <MemoryRecords
            scope={scope}
            subjectId={`group:${group.conversation_id}`}
          />
        )}
        {tab === "episodes" && <MemoryReview scope={scope} />}
        {tab === "matters" && (
          <Matters scope={scope} subjectId={`group:${group.conversation_id}`} />
        )}
        {tab === "archive" && <ArchivePanel scope={scope} />}
      </section>
    </div>
  );
}
