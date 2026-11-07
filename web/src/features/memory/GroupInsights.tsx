import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { DetailCard } from "../../components/DetailCard";
import { TurnEvidence } from "../companion/TurnEvidence";
import type { Conversation } from "../people/types";
import { ReplyPermissions } from "../people/ReplyPermissions";
import { ArchivePanel } from "../resources/ArchivePanel";
import { GroupActivity } from "./GroupActivity";
import MemoryRecords from "./MemoryRecords";
import MemoryReview from "./MemoryReview";
import { Matters } from "./Matters";
import { MemoryTimeFilter, useMemoryTime } from "./MemoryTimeFilter";
import { CoverageNote } from "./EvidenceDetails";
import { EpisodeProcessing } from "./ConsolidationStatus";
import type { Projection } from "./types";
import "./memory.css";

type GroupMessage = { id: number; turn_id: string | null; created_at: string };

export default function GroupInsights({ group }: { group: Conversation }) {
  const scope = {
    actor_id: group.actor_id,
    conversation_id: group.conversation_id,
    person_id: null,
  };
  const [query, setQuery] = useMemoryTime();
  const [tab, setTab] = useState("activity");
  const view = useDomainResource<Projection>(
    "memory/projection",
    { ...scope, group_id: group.conversation_id, ...query },
    ["slang", "impression", "records"].includes(tab),
  );
  const [turnCursor, setTurnCursor] = useState<string | null>(null);
  const turns = useDomainResource<Page<GroupMessage>>(
    "conversation/messages",
    { ...scope, cursor: turnCursor, limit: 25 },
    tab === "records",
  );
  const turnRows = [
    ...new Map(
      (turns.data?.items ?? [])
        .filter((item) => item.turn_id)
        .map((item): [string, GroupMessage] => [item.turn_id!, item]),
    ).values(),
  ];
  const processing = view.data?.group_consolidation;
  const subjectId = view.data?.group?.subject_id;
  const identities = {
    [`group:${group.conversation_id}`]: group.label,
    ...Object.fromEntries(
      (processing?.subjects ?? []).map((person) => [
        person.person_id,
        person.display_name || "群成员",
      ]),
    ),
  };
  return (
    <div className="group-workspace" aria-label={group.label}>
      <nav
        className="workspace-tabs-bar group-content-tabs"
        aria-label="群画像内容"
      >
        {[
          ["activity", "群热度与话题"],
          ["slang", "群黑话"],
          ["impression", "群印象"],
          ["relations", "群关系"],
          ["settings", "群聊权限"],
          ["episodes", "群经历"],
          ["records", "记录与依据"],
        ].map(([id, label]) => (
          <button
            className={`ws-tab${tab === id ? " active" : ""}`}
            key={id}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <section className="panel" key={tab}>
        {tab === "activity" && <GroupActivity group={group} />}
        {tab === "relations" && (
          <GroupActivity group={group} view="relations" />
        )}
        {["slang", "impression"].includes(tab) && (
          <>
            {tab === "impression" && (
              <GroupActivity group={group} view="impression" />
            )}
            <MemoryTimeFilter compact value={query} onChange={setQuery} />
            {view.error && <p role="alert">{view.error}</p>}
            {view.loading && <p role="status">正在核验群记忆…</p>}
            {subjectId && (
              <MemoryRecords
                key={JSON.stringify([tab, query])}
                scope={scope}
                subjectId={subjectId}
                query={query}
                identities={identities}
                predicate={
                  tab === "slang" ? "language.term_meaning" : undefined
                }
                excludePredicate={
                  tab === "impression" ? "language.term_meaning" : undefined
                }
                title={tab === "slang" ? "这个群怎样说话" : "对这个群的认识"}
                description={
                  tab === "slang"
                    ? "群里常用词的含义与语境，供角色理解后续聊天。不同解释保留各自来源。"
                    : "群整体的共同话题、氛围与相处习惯，供角色判断怎样参与。推测可以修订。"
                }
              />
            )}
          </>
        )}
        {tab === "settings" && (
          <ReplyPermissions
            actorId={group.actor_id}
            conversationId={group.conversation_id}
          />
        )}
        {tab === "episodes" && (
          <MemoryReview scope={scope} identities={identities} />
        )}
        {tab === "records" && (
          <>
            <h3>记录与依据</h3>
            <p className="muted">查看群记忆怎样形成、共同事项与历史原文。</p>
            {view.error && <p role="alert">{view.error}</p>}
            <DetailCard title="群材料处理记录" label="查看整理进展与诊断">
              <button
                className="button"
                disabled={view.loading}
                onClick={() => void view.refresh()}
              >
                刷新处理记录
              </button>
              {processing ? (
                <>
                  <CoverageNote coverage={processing.coverage} />
                  {processing.items.map((item) => (
                    <EpisodeProcessing
                      key={item.batch_id ?? item.episode_id}
                      episodeId={item.episode_id}
                      revision={item.revision}
                      initial={item}
                      scope={scope}
                      refresh={view.refresh}
                      identities={identities}
                    />
                  ))}
                  {!processing.items.length && (
                    <p>当前可读窗口没有整理记录。</p>
                  )}
                </>
              ) : (
                <p>尚未取得可读取的群整理状态。</p>
              )}
            </DetailCard>
            {subjectId && (
              <DetailCard title="群共同事项">
                <Matters
                  scope={scope}
                  subjectId={subjectId}
                  query={query}
                  identities={identities}
                />
              </DetailCard>
            )}
            <DetailCard title="群历史原文">
              <ArchivePanel scope={scope} />
            </DetailCard>
            <DetailCard title="每轮实际取材">
              {turns.error && <p role="alert">{turns.error}</p>}
              {turns.loading && <p role="status">正在读取群会话…</p>}
              {turnRows.map((row) => (
                <article key={row.turn_id} className="memory-record">
                  <p>
                    {row.created_at
                      ? new Date(row.created_at).toLocaleString("zh-CN")
                      : "时间未记录"}
                  </p>
                  <TurnEvidence turnId={row.turn_id!} selector={scope} />
                </article>
              ))}
              {turns.data && !turnRows.length && (
                <p>当前页没有可读取的会话轮次。</p>
              )}
              <div className="domain-actions">
                {turnCursor && (
                  <button
                    className="button"
                    onClick={() => setTurnCursor(null)}
                  >
                    回到最新轮次
                  </button>
                )}
                {turns.data?.next_cursor && (
                  <button
                    className="button"
                    onClick={() => setTurnCursor(turns.data!.next_cursor)}
                  >
                    读取更早轮次
                  </button>
                )}
              </div>
            </DetailCard>
          </>
        )}
      </section>
    </div>
  );
}
