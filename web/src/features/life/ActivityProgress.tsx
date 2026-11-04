import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { ActionView } from "../../api/commandRecovery";
import { ArtifactList } from "../../components/MediaPreview";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { SourceRef } from "../../api/contracts";
import type { Activity, Consumption } from "./domainTypes";
import { activityWaitingReason, timeLabel } from "./domainTypes";

const executions: Record<string, string> = {
  pending: "原任务等待执行",
  running: "原任务仍在处理",
  succeeded: "原执行已有结果",
  unknown: "原执行结果尚未确认",
  failed: "原执行未完成",
  cancelled: "原执行已取消",
};

export function ConsumptionProgress({ value }: { value: Consumption }) {
  const modalities = value.model_input_modalities ?? value.modalities ?? [];
  const labels: Record<string, string> = {
    video: "视频输入",
    image: "采样画面",
    audio: "音频输入",
    text: "文本",
  };
  return (
    <div className="life-consumption">
      <p>
        实际处理范围：
        {value.ranges
          ?.map((range) => `${range.start}–${range.end} 秒`)
          .join("、") || "当前回执没有提供明确区间"}
      </p>
      {modalities.length > 0 && (
        <p>
          本次模型材料：
          {modalities.map((item) => labels[item] ?? "其他材料").join("、")}
          {value.frame_times?.length
            ? ` · ${value.frame_times.length} 个采样画面`
            : ""}
        </p>
      )}
      <p className="muted">
        {value.analysis_completed
          ? "本次材料已完成分析。"
          : "尚未确认本次分析完成。"}
        分段或采样处理不表示逐帧理解了整部视频。
      </p>
      {value.omitted?.length ? (
        <p className="muted">
          仍有采样或模态覆盖缺口，可展开原文依据查看实际范围。
        </p>
      ) : null}
      {value.unresolved_questions?.length ? (
        <div>
          <strong>还没弄清楚</strong>
          <ul>
            {value.unresolved_questions.map((item, index) => (
              <li key={index}>{item.question}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {value.source && <SourceEvidence sources={[value.source]} />}
    </div>
  );
}

export function ActivityProgress({ activity }: { activity: Activity }) {
  const [open, setOpen] = useState(false);
  const [inspectCheckpoint, setInspectCheckpoint] = useState(false);
  const detail = useDomainResource<{ id: string; revision: number; checkpoint_data: NonNullable<Activity["checkpoint_data"]> }>(
    "life/activities/checkpoint", { id: activity.id }, inspectCheckpoint,
  );
  const checkpoint: NonNullable<Activity["checkpoint_data"]> = detail.data?.checkpoint_data ?? activity.checkpoint_data ?? {};
  const actionId = activity.action_id ?? checkpoint.completed_action_id;
  const original = useDomainResource<ActionView>(
    "capabilities/get",
    { action_id: actionId },
    !!actionId && open,
    5000,
    (value) => ["pending", "running"].includes(value.receipt?.execution ?? ""),
  );
  const receipt = original.data?.receipt;
  const result = receipt?.result as
    | {
        analysis?: string;
        content?: { title?: string; text?: string };
        source_refs?: SourceRef[];
        consumption?: Consumption;
        sharing?: { notice?: string | null; decision?: string };
      }
    | undefined;
  const units: Record<string, string> = {
    characters: "字符",
    seconds: "秒",
    step: "步",
  };
  return (
    <div className="life-activity-progress">
      {activity.waiting_reason && (
        <p>{activityWaitingReason(activity.waiting_reason)}</p>
      )}
      <p className="muted">
        已保存 {activity.checkpoint_step} 个实际步骤
        {activity.checkpoint_position
          ? ` · 位置 ${activity.checkpoint_position} ${units[activity.checkpoint_unit ?? ""] ?? ""}`
          : ""}
      </p>
      {activity.next_due_at && (
        <p>下次推进：{timeLabel(activity.next_due_at)}</p>
      )}
      {activity.wake_condition && (
        <p className="muted">
          {activityWaitingReason(activity.wake_condition.kind)}
        </p>
      )}
      {!activity.checkpoint_data && <div>
        <button className="text-link" disabled={detail.loading} onClick={() => {
          if (inspectCheckpoint) void detail.refresh(); else setInspectCheckpoint(true);
        }}>读取完整活动进度</button>
        {detail.loading && <p role="status">正在读取活动进度…</p>}
        {detail.error && <p role="alert">{detail.error}</p>}
        {detail.data && detail.data.revision !== activity.revision && <p>活动已更新；这里显示最新进度，请刷新活动列表后编辑。</p>}
      </div>}
      {checkpoint.exploration?.intention && (
        <p>继续想弄清楚：{checkpoint.exploration.intention}</p>
      )}
      {checkpoint.consumption && (
        <ConsumptionProgress value={checkpoint.consumption} />
      )}
      {checkpoint.coverage &&
        !checkpoint.coverage.complete &&
        !checkpoint.consumption && (
          <p className="muted">
            本次只取到了部分原件，后续会从保存的位置继续。
          </p>
        )}
      {actionId && (
        <details onToggle={(event) => setOpen(event.currentTarget.open)}>
          <summary>查看实际结果与产物</summary>
          <button
            className="text-link"
            disabled={original.loading}
            onClick={() => void original.refresh()}
          >
            重新读取原结果
          </button>
          {original.loading && !original.data && (
            <p role="status">正在读取原执行回执…</p>
          )}
          {original.error && <p role="alert">{original.error}</p>}
          {receipt && (
            <>
              <p role="status">
                {executions[receipt.execution] ?? "正在核对原执行"}
                {receipt.effect === "unknown" ? "，外部效果仍未知" : ""}
              </p>
              {receipt.execution === "succeeded" &&
                receipt.effect !== "unknown" && (
                  <>
                    {result?.analysis && (
                      <p className="life-result-copy">{result.analysis}</p>
                    )}
                    {result?.content?.text && (
                      <div>
                        <h5>{result.content.title ?? "本次取得的正文"}</h5>
                        <p className="life-result-copy">
                          {result.content.text.slice(0, 2400)}
                        </p>
                      </div>
                    )}
                    {result?.consumption && (
                      <ConsumptionProgress value={result.consumption} />
                    )}
                  </>
                )}
              <ArtifactList artifacts={receipt.artifacts} />
              {result?.sharing?.notice && (
                <p role="status">{result.sharing.notice}</p>
              )}
              {receipt.delivery === "unknown" && <p>分享投递结果尚未确认。</p>}
              {receipt.delivery === "delivered" && (
                <p>已确认这份内容的投递。</p>
              )}
              {result?.source_refs?.length ? (
                <SourceEvidence sources={result.source_refs} />
              ) : null}
            </>
          )}
          <a
            className="text-link"
            href={`#/system/tasks?action=${encodeURIComponent(actionId)}`}
          >
            打开原任务详情
          </a>
        </details>
      )}
    </div>
  );
}
