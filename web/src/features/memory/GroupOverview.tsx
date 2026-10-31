import { useEffect, useState, type ReactNode } from "react";
import { DetailCard } from "../../components/DetailCard";
import {
  GROUP_MODE_LABELS,
  parseGroupKeywords,
  type GroupMode,
  type GroupRule,
} from "../settings/GroupPolicyEditor";
import type { GroupEntry } from "./groupTypes";

const stamp = (value: number | null) =>
  value === null ? "尚未收到消息" : new Date(value * 1000).toLocaleString();

export function GroupOverview({
  group,
  roleName,
  disabled,
  saving,
  error,
  controls,
  summary,
  onSave,
}: {
  group: GroupEntry;
  roleName: string;
  disabled: boolean;
  saving: boolean;
  error: string;
  controls?: ReactNode;
  summary?: ReactNode;
  onSave: (rule: GroupRule) => Promise<boolean>;
}) {
  const [mode, setMode] = useState(group.rule.mode);
  const [keywordsOpen, setKeywordsOpen] = useState(false);
  const [keywords, setKeywords] = useState("");
  useEffect(() => setMode(group.rule.mode), [group.rule.mode]);

  async function changeMode(value: GroupMode) {
    setMode(value);
    if (!(await onSave({ ...group.rule, mode: value })))
      setMode(group.rule.mode);
  }
  async function saveKeywords() {
    if (await onSave({ ...group.rule, keywords: parseGroupKeywords(keywords) }))
      setKeywordsOpen(false);
  }
  return (
    <section className="panel group-directory-detail">
      <div className="group-overview-heading">
        <h2>{group.name ?? `群 ${group.group_id}`}</h2>
        <div className="group-overview-actions">
          <select
            className="group-mode-select"
            aria-label="此群状态"
            title="群聊状态，选择后保存"
            value={mode}
            disabled={disabled}
            onChange={(event) =>
              void changeMode(event.target.value as GroupMode)
            }
          >
            {Object.entries(GROUP_MODE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <DetailCard
            title="回复关键词"
            label={
              group.rule.keywords.length
                ? `回复关键词 · ${group.rule.keywords.length}`
                : "回复关键词"
            }
            open={keywordsOpen}
            onOpenChange={(open) => {
              setKeywordsOpen(open);
              if (open) setKeywords(group.rule.keywords.join("\n"));
            }}
          >
            <p className="muted">
              {group.name ?? `群 ${group.group_id}`} · {roleName}
            </p>
            <label className="group-keywords-field">
              回复关键词
              <textarea
                aria-label="此群关键词"
                rows={6}
                value={keywords}
                disabled={disabled}
                onChange={(event) => setKeywords(event.target.value)}
                placeholder="每行一个原文关键词，不使用正则"
              />
            </label>
            <p className="muted">
              每行一个关键词，按原文字面匹配；关键词可触发“启用回复”和“主动”状态下的回复。
            </p>
            {error && <p role="alert">{error}</p>}
            <div className="group-toolbar">
              <button
                className="button primary"
                disabled={disabled}
                onClick={() => void saveKeywords()}
              >
                {saving ? "正在保存…" : "保存关键词"}
              </button>
              <button
                className="button"
                disabled={saving}
                onClick={() => setKeywordsOpen(false)}
              >
                取消
              </button>
            </div>
          </DetailCard>
        </div>
        {(saving || group.pending) && (
          <span className="muted" role="status">
            {saving ? "正在保存…" : "等待宿主确认"}
          </span>
        )}
      </div>
      <dl className="group-directory-facts">
        <div>
          <dt>群号</dt>
          <dd>{group.group_id}</dd>
        </div>
        <div>
          <dt>群人数</dt>
          <dd>
            {group.member_count ?? "未知"}
            {group.max_member_count !== null
              ? ` / ${group.max_member_count}`
              : ""}
          </dd>
        </div>
        <div>
          <dt>所属 BOT</dt>
          <dd>
            {group.owner_name} · {group.account_id}
          </dd>
        </div>
        <div>
          <dt>绑定角色</dt>
          <dd>{roleName}</dd>
        </div>
        <div>
          <dt>首次发现</dt>
          <dd>{stamp(group.first_seen_at)}</dd>
        </div>
        <div>
          <dt>最近收到消息</dt>
          <dd>{stamp(group.last_seen_at)}</dd>
        </div>
      </dl>
      {mode === "active" && !group.ordinary_claim_available && (
        <p className="group-overview-notice" role="status">
          当前宿主尚未报告普通消息参与能力，主动参与暂不可用。
        </p>
      )}
      {!group.read_enabled && (
        <p className="group-overview-notice" role="status">
          此 BOT 的历史读取与新增观察已暂停，群状态与已有档案保留。
        </p>
      )}
      {controls && <div className="group-overview-controls">{controls}</div>}
      {summary && <div className="group-overview-summary">{summary}</div>}
    </section>
  );
}
