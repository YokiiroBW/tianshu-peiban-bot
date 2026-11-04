import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { TurnEvidence } from "../companion/TurnEvidence";
import type { Conversation } from "../people/types";
import { ArchivePanel } from "../resources/ArchivePanel";
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
  const view = useDomainResource<Projection>("memory/projection", {
    ...scope,
    group_id: group.conversation_id,
    ...query,
  });
  const [tab, setTab] = useState("processing");
  const [turnCursor, setTurnCursor] = useState<string | null>(null);
  const turns = useDomainResource<Page<GroupMessage>>(
    "conversation/messages", { ...scope, cursor: turnCursor, limit: 25 }, tab === "turns",
  );
  const turnRows = [...new Map((turns.data?.items ?? []).filter((item) => item.turn_id).map((item): [string, GroupMessage] => [item.turn_id!, item])).values()];
  const processing = view.data?.group_consolidation;
  const subjectId = view.data?.group?.subject_id;
  const identities = Object.fromEntries(
    (processing?.subjects ?? []).map((person) => [
      person.person_id,
      person.display_name || person.person_id,
    ]),
  );
  return (
    <div className="group-workspace">
      <MemoryTimeFilter value={query} onChange={setQuery} />
      <nav className="models-actions" aria-label="群会话内容">
        {[
          ["processing", "整理与逐人覆盖"],
          ["claims", "共享事实与认识"],
          ["episodes", "经历与反思"],
          ["matters", "共享事项"],
          ["turns", "本轮取材记录"],
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
      <section className="panel" key={JSON.stringify([tab, query])}>
        {view.error && <p role="alert">{view.error}</p>}
        {tab === "processing" && (
          <>
            <div className="section-heading">
              <h3>共享材料与逐人覆盖</h3>
              <button
                className="button"
                disabled={view.loading}
                onClick={() => void view.refresh()}
              >
                刷新处理记录
              </button>
            </div>
            <p className="muted">
              一份群材料可覆盖多个人物；作者、讨论对象和执行身份分别保留。这里只显示服务器返回的当前处理状态，历史时间选择不会把它变成历史事实。
            </p>
            {view.loading && <p role="status">正在读取共享整理回执…</p>}
            {processing ? (
              <>
                <CoverageNote coverage={processing.coverage} />
                <ul className="memory-record-list">
                  {processing.items.map((item) => (
                    <li
                      className="memory-record"
                      key={item.batch_id ?? item.episode_id}
                    >
                      <EpisodeProcessing
                        episodeId={item.episode_id}
                        revision={item.revision}
                        initial={item}
                        scope={scope}
                        refresh={view.refresh}
                        identities={identities}
                      />
                    </li>
                  ))}
                </ul>
                {!processing.items.length && <p>当前可读窗口没有整理记录。</p>}
              </>
            ) : (
              !view.loading && <p>尚未取得可读取的群整理状态。</p>
            )}
            <p>
              完整处理记录可在“经历与反思”按批继续读取；未返回的成员和材料不视为已覆盖。
            </p>
          </>
        )}
        {tab === "claims" &&
          (subjectId ? (
            <MemoryRecords
              scope={scope}
              subjectId={subjectId}
              query={query}
              identities={identities}
            />
          ) : (
            <p>正在等待服务器核验群共享主体。</p>
          ))}
        {tab === "episodes" && (
          <MemoryReview scope={scope} query={query} identities={identities} />
        )}
        {tab === "matters" &&
          (subjectId ? (
            <Matters
              scope={scope}
              subjectId={subjectId}
              query={query}
              identities={identities}
            />
          ) : (
            <p>正在等待服务器核验群共享主体。</p>
          ))}
        {tab === "archive" && <ArchivePanel scope={scope} />}
        {tab === "turns" && <>
          <h3>群会话中的实际取材</h3>
          <p className="muted">按当前权限读取每轮记录；历史未记录不代表没有选择。</p>
          {turns.error && <p role="alert">{turns.error}</p>}
          {turns.loading && <p role="status">正在读取群会话…</p>}
          {turnRows.map((row) => <article key={row.turn_id} className="memory-record">
            <p>{row.created_at ? new Date(row.created_at).toLocaleString("zh-CN") : "时间未记录"}</p>
            <TurnEvidence turnId={row.turn_id!} selector={scope} />
          </article>)}
          {turns.data && !turnRows.length && <p>当前页没有可读取的会话轮次。</p>}
          <div className="domain-actions">{turnCursor && <button className="button" onClick={() => setTurnCursor(null)}>回到最新轮次</button>}{turns.data?.next_cursor && <button className="button" onClick={() => setTurnCursor(turns.data!.next_cursor)}>读取更早轮次</button>}</div>
        </>}
      </section>
    </div>
  );
}
