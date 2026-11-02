import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { showValue, type Consolidation } from "./types";
import "./memory.css";

const stateNames: Record<string, string> = {
  prepared: "材料已固定，等待原执行",
  waiting_model: "等待原模型请求结果",
  quarantined: "原请求结果未知，已隔离，不阻塞后续整理",
  consolidation_unknown: "原请求结果未知，已隔离，不阻塞后续整理",
  consolidation_stale: "迟到结果已过期，原材料等待明确重新准备",
  ready: "结果待原子提交",
  applied: "本批已提交",
  stale: "固定材料已变化",
  failed: "本批未提交",
  revoked: "来源已撤回",
  unknown: "原执行结果未知",
  pending: "等待整理",
  queued: "已排队",
  running: "原整理正在进行",
  completed: "已完成本次材料",
  completed_with_missing: "本次材料已处理，仍有证据缺项",
  legacy_abandoned: "旧结果映射无法证明",
  no_original_material:
    "这条记录没有可用于整理的原始材料，未从中抽取人物事实。",
  claim_subject_forbidden: "整理结果引用了未授权对象，本批未写入人物记忆。",
  invalid_claim_predicate: "整理结果的字段格式未通过校验，本批未写入人物记忆。",
  no_extractable_text: "当前材料没有可用于整理的文字内容。",
  source_unavailable: "原始依据已不可用。",
};
const actions: Record<string, { label: string; description: string }> = {
  observe_original_attempt: {
    label: "观察原执行",
    description:
      "仅观察原 batch、step 和 attempt；原结果未确认前不会新建模型请求。",
  },
  refresh_material: {
    label: "重新准备当前材料",
    description: "按原权限读取当前材料，继续尚未推进的处理位置。",
  },
  wait_for_new_evidence: {
    label: "等待新证据",
    description: "当前材料已处理；未决项需要新的证据，不重复整理同一材料。",
  },
  configure_budget: {
    label: "先调整材料预算",
    description: "当前输入或最小输出窗口超出预算；调整配置后再继续。",
  },
  legacy_application_abandoned: {
    label: "旧结果未应用",
    description:
      "旧映射无法证明，保留原执行回执；尚未处理材料由当前整理流程接续。",
  },
};
export function ConsolidationStatus({
  value,
  identities = {},
}: {
  value: Consolidation;
  identities?: Record<string, string>;
}) {
  const recovery = value.action ? actions[value.action] : undefined;
  return (
    <div className="memory-consolidation">
      <p>
        <strong>整理状态：{stateNames[value.status] ?? "状态尚未确认"}</strong>
        {value.batch_id ? ` · 批次 ${value.batch_id}` : " · 尚无固定批次"}
      </p>
      {value.held_for_refresh && (
        <p>
          迟到结果与当前材料或记忆版本不一致，未写入；原材料保留，需明确重新准备后继续。
        </p>
      )}
      {recovery && (
        <p>
          {recovery.label}：{recovery.description}
        </p>
      )}
      {value.fragment_cursor != null && (
        <details>
          <summary>原件片段处理位置</summary>
          <pre>{showValue(value.fragment_cursor)}</pre>
        </details>
      )}
      {!!value.coverage?.missing?.length && (
        <p className="muted">
          本次材料仍有 {value.coverage.missing.length}{" "}
          项缺失，不能视为全部完成。
        </p>
      )}
      {!!value.targets?.length && (
        <p>
          固定目标：
          {value.targets
            .map((target) => identities[target.subject_id] ?? target.subject_id)
            .join("、")}
        </p>
      )}
      {!!value.target_results?.length && (
        <div className="memory-target-results">
          <h4>已提交的逐人覆盖</h4>
          <ul>
            {value.target_results.map((result) => (
              <li key={result.subject_id}>
                <strong>
                  {identities[result.subject_id] ?? result.subject_id}
                </strong>{" "}
                ·{" "}
                {{
                  changed: "已有变化",
                  no_change: "已处理，无需变化",
                  unresolved: "仍待证据",
                }[result.status] ?? "覆盖尚未确认"}
                <small>
                  {" "}
                  · 已处理材料 {result.processed_material_ids.length} 项 ·
                  已提交操作 {result.operation_ids.length} 项
                </small>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!!value.unresolved?.length && (
        <details open>
          <summary>未决与缺项（{value.unresolved.length}）</summary>
          <ul>
            {value.unresolved.map((item, index) => (
              <li key={index}>
                {item.subject_id
                  ? (identities[item.subject_id] ?? item.subject_id)
                  : "共享材料"}{" "}
                ·{" "}
                {typeof item.reason === "string"
                  ? item.reason
                  : "缺少充分证据，暂不形成确定结论"}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!value.context_unfulfilled?.length && (
        <p>本次补查仍有 {value.context_unfulfilled.length} 项材料未满足。</p>
      )}
      {(value.error_code || value.step_ids?.length || value.coverage) && (
        <details>
          <summary>处理依据与诊断</summary>
          <pre>{showValue(value)}</pre>
        </details>
      )}
    </div>
  );
}

export function EpisodeProcessing({
  episodeId,
  revision,
  initial,
  extractionState,
  scope,
  refresh,
  identities = {},
}: {
  episodeId: string;
  revision: number;
  initial?: Consolidation;
  extractionState?: string;
  scope: DomainTarget;
  refresh: () => Promise<void>;
  identities?: Record<string, string>;
}) {
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (permission) => permission === "*" || permission === "memory.manage",
  );
  const [inspection, setInspection] = useState<{
    snapshot: Consolidation | undefined;
  } | null>(null);
  const inspect = inspection !== null;
  const status = useDomainResource<Consolidation & { revision: number }>(
    "memory/extraction/status",
    { ...scope, episode_id: episodeId },
    inspect,
  );
  const command = useDomainCommand<{
    action: string;
    queued: boolean;
    batch_id: string | null;
  }>(`memory-extraction:${episodeId}`, "actor");
  const observed =
    !status.loading && inspection?.snapshot === initial ? status.data : null;
  const value = status.accessLost ? undefined : (observed ?? initial);
  return (
    <div>
      {value?.status ? (
        <ConsolidationStatus value={value} identities={identities} />
      ) : (
        <p>
          {extractionState && !status.accessLost
            ? `记忆整理：${stateNames[extractionState] ?? "原记录的整理状态尚未确认"}`
            : "尚未取得实际整理批次和恢复动作。"}
        </p>
      )}
      <button
        className="text-link"
        disabled={status.loading}
        onClick={() => {
          setInspection({ snapshot: initial });
          if (inspect) void status.refresh();
        }}
      >
        读取最新处理状态
      </button>
      {status.error && <p role="alert">{status.error}</p>}
      {manageable &&
        value?.action &&
        ["observe_original_attempt", "refresh_material"].includes(
          value.action,
        ) && (
          <button
            className="button"
            disabled={command.busy || status.loading}
            onClick={async () => {
              const result = await command.run(
                "memory/extraction/resume",
                { ...scope, episode_id: episodeId },
                observed?.revision ?? revision,
              );
              setInspection({ snapshot: initial });
              if (result) {
                if (inspect) await status.refresh();
                await refresh();
              }
            }}
          >
            {actions[value.action].label}
          </button>
        )}
      {value?.action === "configure_budget" && (
        <a className="text-link" href="#/settings/models">
          查看模型与预算配置
        </a>
      )}
      <CommandFeedback {...command} />
      {command.result?.data && (
        <p role="status">
          {actions[command.result.data.action]?.description ??
            "已收到命令回执，实际处理结果请读取最新状态。"}
          {command.result.data.queued
            ? " 已排队，尚未表示整理成功。"
            : " 当前未派发新整理任务。"}
        </p>
      )}
    </div>
  );
}
