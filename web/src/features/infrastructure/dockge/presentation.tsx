import { IntegrationError, readFailure } from "../../../app/integrationApi";
import { health, time } from "../presentation";
import type { ControlObservation, Maintenance } from "./types";

const reasons: Record<string, string> = {
  infrastructure_observation_stale: "上次读回已过期，请刷新后核对当前状态",
  not_sampled: "尚未取得控制状态读回",
  infrastructure_control_not_configured: "部署端尚未配置 Dockge 控制",
  infrastructure_executor_unavailable: "节点控制执行器不可用",
  infrastructure_store_unavailable: "操作记录暂时无法读取",
  infrastructure_node_disabled: "这个节点已停用",
  infrastructure_node_not_registered: "节点登记已改变，请重新选择",
  infrastructure_stack_not_found: "栈已变化或不存在，请刷新清单",
  infrastructure_container_not_found: "容器已重建或不存在，请重新选择",
  infrastructure_operation_not_found: "尚未查到原操作，请继续查询确认送达情况",
  infrastructure_plan_expired: "预览已过期，请重新预览当前配置",
  infrastructure_target_changed: "目标或配置已改变，请刷新后重新预览",
  infrastructure_maintenance_required: "这个服务由专用维护流程管理",
  maintenance_required: "这个服务由专用维护流程管理",
  protected_stack: "这是受保护的服务栈",
  protected_dependency: "这是 Dockge 控制所依赖的服务",
  protected_project: "这是受运行保护的应用",
  stack_not_platform_owned: "此应用的配置由 Dockge 外部管理",
  stack_revision_conflict: "应用配置已改变，请重新预览",
  configuration_restore_does_not_restore_data:
    "恢复配置与运行版本，保留现有数据写入",
  operation_not_found: "节点尚未查到原操作",
  published_port_in_use: "发布端口已被其他服务占用",
  published_port_invalid: "发布端口不在有效范围内",
  published_port_duplicate: "配置重复发布了同一端口",
  published_port_host_unavailable: "发布端口绑定的主机地址不可用",
  published_port_unverifiable: "尚不能确认发布端口是否可用",
  maintenance_owned_target: "此目标由专用维护流程管理",
  protected_bind_root: "挂载涉及受保护服务的数据目录",
  plan_expired: "计划已经过期，请重新预览",
  stack_configuration_not_owned: "此应用的配置由外部管理，不能在这里修改或恢复",
  configuration_restore_not_data_rollback:
    "恢复配置和运行版本，不回滚已发生的数据写入",
  image_update_not_data_rollback:
    "镜像更新保留现有数据，回退镜像不会回滚数据写入",
  restore_referenced_configuration_version:
    "将恢复此版本包含的受管配置输入，请核对预览",
  restore_would_remove_new_reference_file:
    "恢复会移除后来新增的配置输入，当前不能执行",
  existing_bind_source_changed: "现有挂载目录或读写方式已改变，请核对配置",
  local_image_required: "恢复需要的原镜像在节点上不可用",
  version_not_found: "所选配置版本已不可读取，请刷新版本列表",
  compose_normalized_config_invalid: "Compose 配置无法解析为可执行的部署",
  compose_project_identity_conflict: "Compose 项目身份与已登记应用不一致",
  compose_reference_outside_stack:
    "Compose 配置引用了栈目录外的文件，当前无法读取",
  stack_already_exists: "该应用项目已存在，请从清单选择",
  create_reference_files_not_registered: "新应用引用了尚未登记的配置文件",
  worker_interrupted: "节点执行进程中断，结果须查询原操作核对",
  command_outcome_unknown: "节点命令结果未知，只查询原操作",
  control_command_failed: "节点命令执行失败，请核对实际运行结果",
  external_edit_detected: "Dockge 配置已在外部修改，请重新预览",
  revision_conflict: "连接或配置版本已改变，请刷新后重新预览",
  port_conflict: "发布端口与现有服务冲突",
  pull_failed: "镜像拉取失败，查看实际运行读回",
  idempotency_conflict: "原请求编号已对应其他操作，请查询操作记录",
};
export function controlReasons(codes: string[]) {
  return codes.map((code) => reasons[code] ?? code).join(" · ");
}
export function controlFailure(cause: unknown) {
  return cause instanceof IntegrationError
    ? `${reasons[cause.code] ?? readFailure(cause)}（${cause.code}）`
    : "连接中断，尚未取得回执。请查询原操作确认结果。";
}
export const runtimeLabels: Record<string, string> = {
  running: "运行中",
  stopped: "已停止",
  partial: "部分运行",
  unknown: "运行未知",
  absent: "未创建容器",
};
export function runtimeLabel(state: string) {
  return runtimeLabels[state] ?? "运行未知";
}
export function healthLabel(state: string) {
  if (state === "mixed") return "健康状态混合";
  if (state === "not_configured") return "未配置检查";
  return health[state] ?? "检查未知";
}
export function ControlSource({ value }: { value: ControlObservation }) {
  return (
    <p className="dockge-source">
      Docker / Compose 实际读回 ·{" "}
      {value.freshness === "current" ? "当前" : "上次读数"} ·{" "}
      {time(value.observed_at)}
      {value.status !== "ok" &&
        ` · ${{ partial: "部分结果", unavailable: "不可用", unsupported: "不支持", unknown: "未知" }[value.status] ?? "未知"}`}
      {value.reason_codes.length > 0 &&
        ` · ${controlReasons(value.reason_codes)}`}
    </p>
  );
}
export function MaintenanceNotice({ value }: { value: Maintenance }) {
  return value.required ? (
    <div className="dockge-maintenance" role="note">
      <strong>专用维护 · {value.owner ?? "维护归属待配置"}</strong>
      <p>
        {controlReasons(value.reason_codes) ||
          "此服务受运行保护，请从其专用维护入口操作。"}
      </p>
    </div>
  ) : null;
}
export function currentControl(observation: ControlObservation) {
  return (
    observation.freshness === "current" &&
    ["ok", "partial"].includes(observation.status)
  );
}
