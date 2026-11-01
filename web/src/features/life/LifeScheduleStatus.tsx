import type { Routine } from "./domainTypes";
import { timeLabel } from "./domainTypes";

const planningReasons: Record<string, string> = {
  planned: "最近一次规划已保存活动，将按实际进度继续。",
  planned_with_reduced_context:
    "最近一次规划已保存活动；部分非必要材料因预算限制未参与本次安排。",
  no_meaningful_activity:
    "这次规划没有选择新的活动，将在下一次生活时间再作安排。",
  waiting_for_next_life_window: "正在等待下一次生活安排时间。",
  existing_activity_continues: "已有活动会按保存的进度继续。",
  current_activity_continues: "当前活动仍在进行，本次没有重排。",
  planner_output_invalid: "这次模型输出没有形成有效计划，未保存活动。",
  planner_output_incomplete:
    "原规划结果不完整，尚未保存为有效活动；保留原执行记录供核对。",
  planner_context_budget_exceeded:
    "规划所需材料超出模型上下文预算，未提交新的计划。",
  planner_capability_unavailable: "规划选择的能力当前不可用，未执行该活动。",
  planner_arguments_invalid: "规划中的执行参数未通过校验，未执行该活动。",
  planner_goal_unavailable: "规划引用的目标当前不可用，未执行该活动。",
  planner_original_contract_missing:
    "原规划结果缺少可核验的结构约定，保留原执行结果，没有重新付费提交。",
  planner_original_context_changed:
    "原规划对应的授权场景已变化，只查询原执行结果，没有重新提交。",
  planner_original_request_missing:
    "原规划尚未完整保存请求，仅保留原身份等待核对，不会另建身份重发。",
  original_planner_not_registered_before_deadline:
    "原规划请求在截止前没有登记，已经停止本次提交。",
  planner_unknown: "原规划结果尚未确认，正在保留原请求身份，未重新提交。",
  planner_queued: "原规划请求正在排队。",
  planner_running: "原规划请求正在处理。",
  planner_failed: "原规划请求未完成，请查看原任务的失败原因。",
  planner_cancelled: "原规划请求已取消。",
  activity_planned: "最近一次规划已完成，请查看活动的实际状态。",
};

export function LifeScheduleStatus({ routine }: { routine: Routine }) {
  const status = routine.schedule_status;
  const blocked = status === "blocked";
  const paused = status === "disabled" || !routine.enabled;
  const message = blocked
    ? routine.schedule_reason === "actor_disabled"
      ? "角色已停用，生活调度已暂停。"
      : "当前授权已不足以继续这份作息，生活调度已暂停；请由有权管理该角色的账号重新保存作息。"
    : paused
      ? "持续生活已关闭。"
      : status === "not_started"
        ? "生活作息尚未启动，可以在作息与联系中保存安排。"
        : status === "overdue"
          ? "生活安排已到预定时间，尚未推进；若长时间没有变化，请查看系统任务。"
          : "生活会按作息在下一次时间继续安排。";
  const planning = routine.planning;
  return (
    <div className="life-schedule-status" role={blocked ? "alert" : "status"}>
      <p>{message}</p>
      {routine.last_tick_at && (
        <p className="muted">
          上次生活调度：{timeLabel(routine.last_tick_at, routine.timezone)}
        </p>
      )}
      {routine.enabled && !blocked && routine.next_due_at && (
        <p className="muted">
          下一次生活时间：{timeLabel(routine.next_due_at, routine.timezone)}
        </p>
      )}
      {!blocked && !paused && planning?.reason && (
        <p>
          {planningReasons[planning.reason] ??
            "最近一次规划尚未形成可展示的新活动，可在系统任务查看原执行。"}
        </p>
      )}
      {!blocked && !paused && planning?.pending && !planning.reason && (
        <p>一次生活规划正在等待后台处理。</p>
      )}
    </div>
  );
}
