import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceForget } from "./SourceForget";
import { useMemoryCursor } from "./pagination";
import { assertionNames, showValue, time, type Claim } from "./types";
import "./memoryTimeline.css";

function ClaimCard({
  item,
  scope,
  refresh,
}: {
  item: Claim;
  scope: DomainTarget;
  refresh: () => Promise<void>;
}) {
  const { session } = useAuth();
  const permissions = session?.principal?.permissions ?? [];
  const manage =
    permissions.includes("*") || permissions.includes("memory.manage");
  const share =
    permissions.includes("*") || permissions.includes("memory.share");
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(showValue(item.value) ?? "");
  const [negative, setNegative] = useState(!item.polarity);
  const [error, setError] = useState("");
  const command = useDomainCommand<Claim>(
    `claim:${item.claim_id}:${item.revision}`,
    "actor",
  );
  const ordinary =
    ["interest", "hobby", "preference.ordinary", "public_interest"].includes(
      item.predicate,
    ) &&
    item.assertion === "self_confirmed" &&
    Object.keys(item.conditions).length === 0;
  return (
    <li className="memory-record">
      <div className="section-heading">
        <h3>{item.predicate}</h3>
        <small>
          {assertionNames[item.assertion] ?? item.assertion} ·{" "}
          {item.polarity ? "肯定" : "否定"} · v{item.revision}
        </small>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{showValue(item.value)}</p>
      <p className="muted">
        主体：{item.subject_id} · 原发言人：{item.speaker_id ?? "未登记"} ·
        观察于 {time(item.observed_at)}
        {item.valid_until && ` · 有效至 ${time(item.valid_until)}`}
      </p>
      {!!Object.keys(item.conditions).length && (
        <details>
          <summary>适用条件</summary>
          <pre>{JSON.stringify(item.conditions, null, 2)}</pre>
        </details>
      )}
      {(item.contradictions.length > 0 || item.supersedes.length > 0) && (
        <p className="muted">
          {item.contradictions.length > 0 &&
            `存在 ${item.contradictions.length} 条矛盾关联。`}
          {item.supersedes.length > 0 &&
            `修订替代 ${item.supersedes.length} 条旧记录。`}
        </p>
      )}
      <SourceEvidence sources={item.source_refs} scope={scope} />
      {manage && (
        <button
          className="text-link"
          onClick={() => setEditing((before) => !before)}
        >
          更正这条记忆
        </button>
      )}
      {editing && (
        <form
          className="memory-edit"
          onSubmit={async (event) => {
            event.preventDefault();
            setError("");
            let parsed: unknown = value;
            if (typeof item.value !== "string") {
              try {
                parsed = JSON.parse(value);
              } catch {
                setError("这条结构化记录需要有效 JSON 值。");
                return;
              }
            }
            if (
              await command.run(
                "memory/claims/correct",
                {
                  ...scope,
                  claim_id: item.claim_id,
                  value: parsed,
                  polarity: !negative,
                  conditions: item.conditions,
                },
                item.revision,
              )
            ) {
              setEditing(false);
              await refresh();
            }
          }}
        >
          <label>
            {typeof item.value === "string"
              ? "更正后的内容"
              : "更正后的结构化值"}
            <textarea
              required
              rows={4}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={negative}
              onChange={(event) => setNegative(event.target.checked)}
            />
            这是否定陈述
          </label>
          <button className="button" disabled={command.busy}>
            保存本次明确更正
          </button>
          {error && <p role="alert">{error}</p>}
        </form>
      )}
      {share && ordinary && (
        <button
          className="text-link"
          disabled={command.busy}
          onClick={async () => {
            if (
              await command.run(
                "memory/claims/share",
                { ...scope, claim_id: item.claim_id },
                item.revision,
              )
            )
              await refresh();
          }}
        >
          明确披露这条普通兴趣到角色公共范围
        </button>
      )}
      {item.source_refs.map((source) => (
        <SourceForget
          key={JSON.stringify(source)}
          source={source}
          scope={scope}
          onChanged={refresh}
        />
      ))}
      <CommandFeedback {...command} />
    </li>
  );
}

export default function MemoryRecords({
  scope,
  subjectId,
}: {
  scope: DomainTarget;
  subjectId?: string | null;
}) {
  const pages = useMemoryCursor({ ...scope, subject_id: subjectId }, "claims");
  const view = useDomainResource<Page<Claim>>(
    "memory/claims",
    {
      ...scope,
      subject_id: subjectId ?? null,
      cursor: pages.cursor,
      limit: 40,
    },
    !!scope.actor_id,
  );
  return (
    <section className="memory-records">
      <div className="section-heading">
        <h3>有来源的记忆条目</h3>
        <button className="button" onClick={() => void view.refresh()}>
          刷新
        </button>
      </div>
      {view.loading && <p>正在核验记忆来源…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          <p className="muted">
            {view.data.coverage.complete
              ? "本次范围已读完"
              : `范围仍有后续或缺口：${view.data.coverage.missing.join("、") || "可继续翻页"}`}
          </p>
          {!view.data.items.length && <p>本批没有可显示的有效记忆。</p>}
          <ul className="memory-record-list">
            {view.data.items.map((item) => (
              <ClaimCard
                key={`${item.claim_id}:${item.revision}`}
                item={item}
                scope={scope}
                refresh={view.refresh}
              />
            ))}
          </ul>
        </>
      )}
      <div className="models-actions">
        {pages.previous && (
          <button
            className="button"
            disabled={view.loading}
            onClick={pages.back}
          >
            上一批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            disabled={view.loading}
            onClick={() => pages.next(view.data!.next_cursor!)}
          >
            下一批记忆
          </button>
        )}
      </div>
    </section>
  );
}
