import { requestId } from "../../app/requestId";
import { integrationPost } from "../../app/integrationApi";
import { apiBlob } from "../../api/client";
import type {
  content_ref,
  content_read_response,
  range,
  manage_response,
  ReadResult,
  Records,
  Resource,
} from "./runtimeTypes";

export type LifeAccess = { actor: string; csrf: string };
export function readLife<K extends keyof Records & Resource>(
  access: LifeAccess,
  resource: K,
  signal: AbortSignal,
  object_id: string | null = null,
  after: string | null = null,
  chapter_view?: "current" | "published",
) {
  return integrationPost<ReadResult<Records[K]>>(
    "life/runtime/read",
    {
      actor_id: access.actor,
      resource,
      object_id,
      expected_revision: null,
      limit: 50,
      after,
      ...(chapter_view ? { chapter_view } : {}),
    },
    access.csrf,
    signal,
  );
}
export function manageLife(
  access: LifeAccess,
  operation: string,
  value: object,
  expected_revision: number | null,
  client_request_id: string,
  signal: AbortSignal,
) {
  return integrationPost<manage_response>(
    "life/runtime/manage",
    {
      actor_id: access.actor,
      operation,
      value,
      expected_revision,
      client_request_id,
    },
    access.csrf,
    signal,
  );
}
export async function privateBlob(
  path: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
) {
  return apiBlob(path, body, csrf, signal);
}
export function readContent(
  access: LifeAccess,
  ref: content_ref,
  selected: range,
  signal: AbortSignal,
  readingId: string | null = null,
) {
  return integrationPost<content_read_response>(
    "life/runtime/content",
    {
      actor_id: access.actor,
      content_ref: ref,
      reading_id: readingId,
      range: selected,
    },
    access.csrf,
    signal,
  );
}
export function originalImage(
  access: LifeAccess,
  media_id: string,
  signal: AbortSignal,
) {
  return privateBlob(
    "life/runtime/media",
    { actor_id: access.actor, media_id },
    access.csrf,
    signal,
  );
}
export function originalContent(
  access: LifeAccess,
  ref: content_ref,
  signal: AbortSignal,
) {
  return privateBlob(
    "content/original",
    {
      actor_id: access.actor,
      client_request_id: requestId(),
      value: { content_ref: ref, range: null },
    },
    access.csrf,
    signal,
  );
}
export const stateNames: Record<string, string> = {
  idle: "暂无待执行目标",
  preparing: "准备中",
  submitted: "已提交执行",
  interrupted: "执行中断",
  tools_pending: "等待工具结果",
  planned: "计划",
  running: "进行中",
  paused: "已暂停",
  dormant: "暂未继续",
  completed: "已完成",
  cancelled: "已取消",
  open: "关注中",
  closed: "已结束",
  queued: "排队中",
  pending: "等待处理",
  unknown: "结果未知",
  failed: "失败",
  available: "可读取",
  unavailable: "无法读取",
  removed: "已移除",
  active: "启用中",
  configured: "已配置",
  not_configured: "未配置",
  disabled: "已停用",
  unreachable: "无法连接",
  workflow_required: "等待选择工作流",
  unsupported: "工作流暂不支持",
  sent: "已送达",
  sending: "发送中",
  partial: "部分送达",
  approved: "已通过",
  changes_requested: "待修改",
  reading: "阅读中",
  published: "已发布",
  draft: "草稿",
  rejected: "未通过",
  revoked: "已撤销",
};
export const showState = (state: string) => stateNames[state] ?? state;
