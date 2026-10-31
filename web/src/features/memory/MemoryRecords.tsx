import { useState } from "react";
import { StatePanel } from "../../components/StatePanel";
import { DetailCard } from "../../components/DetailCard";
import "./memoryTimeline.css";

type Provenance = {
  kind: string;
  subject_person_id: string;
  organized_at: string;
  effective_at: string | null;
  expires_at: string;
  expired: boolean;
  importance: string;
  retention: string | null;
  valid_until?: string | null;
  replacement_count?: number;
  related_memory_count?: number;
  memory_kind: string | null;
  memory_state: string | null;
  source_count: number;
  context_count: number;
  source_excerpts: { observed_at: string; text: string; truncated: boolean }[];
  excerpts_truncated: boolean;
};

export type RecordGroup = {
  semantic_group_id: string;
  category?: string;
  field_key?: string | null;
  item_key?: string | null;
  units: {
    record_id: string;
    record_version: number;
    statement: string;
    conditions?: unknown;
    negations?: unknown;
    valid_time?: unknown;
    uncertainty?: unknown;
    reality?: unknown;
  }[];
  provenance?: Provenance;
};

const names: Record<string, string> = {
  summary: "经历与表达",
  portrait: "人物画像",
  profile: "人物画像",
  preference: "偏好",
  preferences: "偏好",
  fact: "事实",
  identity: "身份",
  relationship: "关系",
  interest: "兴趣",
  habit: "习惯",
  experience: "经历",
  thought: "想法",
  agreement: "约定",
  plan: "计划",
  described: "已表达",
  considering: "考虑中",
  confirmed: "已确认",
  completed: "已完成",
  cancelled: "已取消",
  unknown: "进展未明",
  low: "一般",
  normal: "普通",
  high: "重要",
  short: "短期",
  medium: "中期",
  long: "长期",
};

function date(value: string | null | undefined) {
  if (!value) return "时间未记录";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "时间未记录"
    : parsed.toLocaleString("zh-CN");
}

export default function MemoryRecords({
  scope,
  records,
  cursor,
  busy,
  error,
  more,
  moreLabel = "继续读取记忆",
}: {
  scope: string;
  records: RecordGroup[];
  cursor: string | null;
  busy: boolean;
  error: string;
  more?: () => void;
  moreLabel?: string;
}) {
  const [view, setView] = useState("all");
  if (busy && records.length === 0)
    return (
      <StatePanel kind="loading" title="正在读取记忆条目">
        <p>请稍候。</p>
      </StatePanel>
    );
  if (!busy && !error && records.length === 0 && !cursor)
    return (
      <StatePanel kind="empty" title="这个范围没有有效记忆">
        <p>{scope}成功返回空列表。其它范围可能有不同内容。</p>
      </StatePanel>
    );
  const visible = records
    .filter(
      (group) =>
        view === "all" ||
        (view === "portrait"
          ? group.provenance?.kind === "portrait"
          : group.provenance?.kind !== "portrait"),
    )
    .sort((left, right) => {
      const stamp = (group: RecordGroup) => {
        const parsed = Date.parse(
          group.provenance?.effective_at ||
            group.provenance?.organized_at ||
            "",
        );
        return Number.isNaN(parsed) ? 0 : parsed;
      };
      return stamp(right) - stamp(left);
    });
  return (
    <>
      <div
        className="memory-view-filter"
        role="group"
        aria-label="记忆内容筛选"
      >
        {[
          ["all", "全部"],
          ["experience", "经历与表达"],
          ["portrait", "人物画像"],
        ].map(([value, label]) => (
          <button
            key={value}
            className="button"
            aria-pressed={view === value}
            onClick={() => setView(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="muted">
        已加载条目按依据时间排列{cursor ? "，可继续读取后续页" : ""}
        。画像是可修订的归纳。
      </p>
      {visible.length === 0 && !busy && (
        <p className="muted">已加载范围内没有这类内容。</p>
      )}
      <ul className="memory-records">
        {visible.map((group) => {
          const info = group.provenance;
          return (
            <li key={group.semantic_group_id}>
              <h3>
                {info
                  ? names[
                      info.kind === "portrait"
                        ? "portrait"
                        : info.memory_kind || info.kind
                    ] || "记忆"
                  : [
                      names[group.category || ""] || group.category,
                      group.field_key,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "记忆"}
              </h3>
              {info && (
                <div className="memory-record-meta">
                  <span>{date(info.effective_at || info.organized_at)}</span>
                  {info.memory_state && (
                    <span>{names[info.memory_state] || info.memory_state}</span>
                  )}
                  <span>{names[info.importance] || info.importance}</span>
                  {info.retention && (
                    <span>{names[info.retention] || info.retention}保留</span>
                  )}
                  {info.expired && <span>已过期，仅供回顾</span>}
                  {!!info.replacement_count && (
                    <span>已更新 {info.replacement_count} 条既有记忆</span>
                  )}
                </div>
              )}
              <ul>
                {group.units.map((unit) => (
                  <li key={unit.record_id}>
                    <p>{unit.statement}</p>
                    {[
                      ["条件", unit.conditions],
                      ["否定", unit.negations],
                      ["有效时间", unit.valid_time],
                      ["不确定性", unit.uncertainty],
                      ["现实依据", unit.reality],
                    ].some(([, value]) => hasValue(value)) && (
                      <DetailCard title="查看事实的适用条件">
                        <dl className="memory-fact-conditions">
                          {[
                            ["条件", unit.conditions],
                            ["否定", unit.negations],
                            ["有效时间", unit.valid_time],
                            ["不确定性", unit.uncertainty],
                            ["现实依据", unit.reality],
                          ]
                            .filter(([, value]) => hasValue(value))
                            .map(([label, value]) => (
                              <div key={String(label)}>
                                <dt>{String(label)}</dt>
                                <dd>
                                  {typeof value === "string"
                                    ? value
                                    : JSON.stringify(value)}
                                </dd>
                              </div>
                            ))}
                        </dl>
                      </DetailCard>
                    )}
                  </li>
                ))}
              </ul>
              <details className="memory-provenance">
                <summary>查看整理依据</summary>
                {info ? (
                  <>
                    <p>
                      整理于 {date(info.organized_at)}；保留至{" "}
                      {date(info.expires_at)}。
                    </p>
                    <p>
                      直接依据 {info.source_count}{" "}
                      份来源，整理时结合了对话上下文。
                    </p>
                    {!!info.replacement_count && (
                      <p>
                        本条替代了 {info.replacement_count}{" "}
                        条先前记忆，当前内容依据本次原话保存。
                      </p>
                    )}
                    {!!info.related_memory_count && (
                      <p>
                        整理时关联了 {info.related_memory_count} 条已有记忆。
                      </p>
                    )}
                    {info.valid_until && (
                      <p>
                        约定期限：{date(info.valid_until)}
                        。期限不代表实际完成时间。
                      </p>
                    )}
                    {info.source_excerpts.map((excerpt, index) => (
                      <blockquote key={index}>
                        <small>{date(excerpt.observed_at)}</small>
                        <p>{excerpt.text}</p>
                        {excerpt.truncated && (
                          <small>原话较长，此处展示摘录。</small>
                        )}
                      </blockquote>
                    ))}
                    {info.excerpts_truncated && <p>此处只展示部分直接来源。</p>}
                    {!info.source_excerpts.length && (
                      <p>本条未附可展示的原话摘录。</p>
                    )}
                  </>
                ) : (
                  <p>这条记录未附新版整理信息。</p>
                )}
                <small>
                  {group.units.some((unit) => unit.record_version > 1) &&
                    "这条记录已经更正。 "}
                  记录 {group.semantic_group_id} · 版本{" "}
                  {group.units.map((unit) => unit.record_version).join("、")}
                </small>
              </details>
            </li>
          );
        })}
      </ul>
      {cursor && more && (
        <button className="button" disabled={busy} onClick={more}>
          {moreLabel}
        </button>
      )}
    </>
  );
}

function hasValue(value: unknown) {
  return (
    value != null &&
    value !== "" &&
    JSON.stringify(value) !== "{}" &&
    JSON.stringify(value) !== "[]"
  );
}
