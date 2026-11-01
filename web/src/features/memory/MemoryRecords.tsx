import { useId, useState } from "react";
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
import {
  CoverageNote,
  EvidenceDetails,
  EvidenceHistory,
  EvidenceRelations,
} from "./EvidenceDetails";
import {
  assertionNames,
  currentMemory,
  showValue,
  type Claim,
  type MemoryTimeQuery,
} from "./types";
import "./memoryTimeline.css";

function ClaimEditor({
  item,
  scope,
  refresh,
  close,
}: {
  item: Claim;
  scope: DomainTarget;
  refresh: () => Promise<void>;
  close: () => void;
}) {
  const valueLabelId = useId();
  const conditionsLabelId = useId();
  const [value, setValue] = useState(showValue(item.value) ?? "");
  const [negative, setNegative] = useState(!item.polarity);
  const [conditions, setConditions] = useState(showValue(item.conditions));
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const command = useDomainCommand<Claim>(
    `claim:${item.claim_id}:${item.revision}`,
    "actor",
  );
  return (
    <form
      className="memory-edit"
      onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        let parsed: unknown = value;
        let parsedConditions: Record<string, unknown>;
        try {
          if (typeof item.value !== "string") parsed = JSON.parse(value);
          const next = JSON.parse(conditions);
          if (!next || typeof next !== "object" || Array.isArray(next))
            throw new Error();
          parsedConditions = next;
        } catch {
          setError("结构化值和适用条件需要有效 JSON，条件必须为对象。");
          return;
        }
        if (
          await command.run(
            "memory/claims/correct",
            {
              ...scope,
              claim_id: item.claim_id,
              value: parsed,
              polarity: !negative,
              conditions: parsedConditions,
              ...(reason.trim() ? { reason } : {}),
            },
            item.revision,
          )
        ) {
          close();
          await refresh();
        }
      }}
    >
      <p>
        编辑当前版本 v{item.revision}
        。保存时核对该版本；并发变化会提示重新读取。
      </p>
      <label>
        <span id={valueLabelId}>
          {typeof item.value === "string" ? "更正后的内容" : "更正后的结构化值"}
        </span>
        <textarea
          aria-labelledby={valueLabelId}
          required
          rows={4}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      <label>
        <span id={conditionsLabelId}>适用条件（JSON 对象）</span>
        <textarea
          aria-labelledby={conditionsLabelId}
          rows={3}
          value={conditions}
          onChange={(event) => setConditions(event.target.value)}
        />
      </label>
      <label>
        本次修订原因
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
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
      <div className="models-actions">
        <button className="button" disabled={command.busy}>
          保存本次明确更正
        </button>
        <button type="button" className="button" onClick={close}>
          取消
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <CommandFeedback {...command} />
    </form>
  );
}

function CurrentClaimEditor({
  id,
  scope,
  refresh,
  close,
}: {
  id: string;
  scope: DomainTarget;
  refresh: () => Promise<void>;
  close: () => void;
}) {
  const current = useDomainResource<Claim>("memory/claims/get", {
    ...scope,
    claim_id: id,
    time_mode: "current",
  });
  return (
    <div>
      {current.loading && <p role="status">正在读取可更正的当前版本…</p>}
      {current.error && <p role="alert">{current.error}</p>}
      {current.data && (
        <ClaimEditor
          key={`${current.data.claim_id}:${current.data.revision}`}
          item={current.data}
          scope={scope}
          refresh={refresh}
          close={close}
        />
      )}
    </div>
  );
}

function ClaimCard({
  item,
  scope,
  refresh,
  historical,
  identities,
}: {
  item: Claim;
  scope: DomainTarget;
  refresh: () => Promise<void>;
  historical: boolean;
  identities: Record<string, string>;
}) {
  const { session } = useAuth();
  const permissions = session?.principal?.permissions ?? [];
  const manage =
    permissions.includes("*") || permissions.includes("memory.manage");
  const share =
    permissions.includes("*") || permissions.includes("memory.share");
  const [editing, setEditing] = useState(false);
  const command = useDomainCommand<Claim>(
    `claim-share:${item.claim_id}:${item.revision}`,
    "actor",
  );
  const ordinary =
    !historical &&
    item.memory_kind !== "hypothesis" &&
    (!item.current_status || item.current_status === "current") &&
    ["interest", "hobby", "preference.ordinary", "public_interest"].includes(
      item.predicate,
    ) &&
    item.assertion === "self_confirmed" &&
    Object.keys(item.conditions).length === 0;
  return (
    <li className="memory-record">
      <div className="section-heading">
        <h4>{item.predicate}</h4>
        <small>
          {item.memory_kind === "hypothesis" ? "可修订认识" : "有依据的事实"} ·{" "}
          {assertionNames[item.assertion] ?? item.assertion} · v{item.revision}
        </small>
      </div>
      <p className="memory-prewrap">{showValue(item.value)}</p>
      <EvidenceDetails item={item} scope={scope} identities={identities} />
      <EvidenceRelations
        relations={item.relations}
        scope={scope}
        identities={identities}
      />
      <SourceEvidence sources={item.source_refs} scope={scope} />
      <EvidenceHistory
        kind="claims"
        id={item.claim_id}
        scope={scope}
        identities={identities}
      />
      {manage && (
        <button
          className="text-link"
          aria-expanded={editing}
          onClick={() => setEditing(!editing)}
        >
          {historical ? "读取当前版本并更正" : "更正这条记忆"}
        </button>
      )}
      {editing && (
        <CurrentClaimEditor
          id={item.claim_id}
          scope={scope}
          refresh={refresh}
          close={() => setEditing(false)}
        />
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
  query = currentMemory,
  identities = {},
}: {
  scope: DomainTarget;
  subjectId?: string | null;
  query?: MemoryTimeQuery;
  identities?: Record<string, string>;
}) {
  const pages = useMemoryCursor(
    { ...scope, subject_id: subjectId, ...query },
    "claims",
  );
  const view = useDomainResource<Page<Claim>>(
    "memory/claims",
    {
      ...scope,
      subject_id: subjectId ?? null,
      ...query,
      cursor: pages.cursor,
      limit: 40,
    },
    !!scope.actor_id,
  );
  const groups = [
    {
      name: "当前事实与明确陈述",
      items:
        view.data?.items.filter((item) => item.memory_kind !== "hypothesis") ??
        [],
    },
    {
      name: "可修订的人物认识",
      items:
        view.data?.items.filter((item) => item.memory_kind === "hypothesis") ??
        [],
    },
  ];
  return (
    <section className="memory-records">
      <div className="section-heading">
        <h3>有来源的记忆条目</h3>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          刷新
        </button>
      </div>
      <p className="muted">
        明确自述、转述与可修订认识分别保留。支持、反例和适用条件随证据展示，重复记录不代表更多独立支持。
      </p>
      {view.loading && <p role="status">正在核验记忆来源…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          <CoverageNote coverage={view.data.coverage} />
          {!view.data.items.length && <p>本批没有可显示的有效记忆。</p>}
          {groups.map(
            (group) =>
              !!group.items.length && (
                <section key={group.name}>
                  <h3>
                    {query.time_mode !== "current" &&
                    group.name === "当前事实与明确陈述"
                      ? "指定时间的事实与明确陈述"
                      : group.name}
                  </h3>
                  <ul className="memory-record-list">
                    {group.items.map((item) => (
                      <ClaimCard
                        key={`${item.claim_id}:${item.revision}`}
                        item={item}
                        scope={scope}
                        refresh={view.refresh}
                        historical={query.time_mode !== "current"}
                        identities={identities}
                      />
                    ))}
                  </ul>
                </section>
              ),
          )}
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
