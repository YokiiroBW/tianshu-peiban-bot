import { humanizeCode, sanitizeInternalText } from "./humanize";

const fieldNames: Record<string, string> = {
  state: "状态",
  status: "状态",
  execution: "执行状态",
  effect: "实际效果",
  delivery: "投递状态",
  dispatch: "提交状态",
  reason: "原因",
  error: "错误说明",
  error_code: "原因",
  message: "说明",
  content: "内容",
  text: "正文",
  title: "标题",
  name: "名称",
  label: "名称",
  description: "说明",
  result: "处理结果",
  data: "记录内容",
  note: "备注",
  summary: "摘要",
  kind: "记录类型",
  type: "类型",
  revision: "版本",
  version: "版本",
  semantic_revision: "语义版本",
  round: "轮次",
  attempts: "执行次数",
  count: "数量",
  total: "总数",
  items: "记录",
  complete: "是否完整",
  missing: "未覆盖部分",
  created_at: "创建时间",
  updated_at: "更新时间",
  observed_at: "记录时间",
  finished_at: "结束时间",
  started_at: "开始时间",
  scheduled_at: "计划时间",
  expires_at: "到期时间",
  heartbeat_at: "心跳时间",
  execution_lane: "执行通道",
  queue_name: "执行队列",
  service_class: "执行类别",
  wait_reason: "等待原因",
  priority: "优先级",
  ready: "是否就绪",
  enabled: "是否启用",
  configured: "是否已配置",
  model: "模型",
  protocol: "通信协议",
  capabilities: "能力",
  parameters: "参数",
  usage: "用量",
  input_tokens: "输入词元",
  output_tokens: "输出词元",
  cached_input_tokens: "缓存命中词元",
  concurrency: "并发上限",
  running: "执行中",
  queued: "排队中",
  available: "可用数量",
  max_queue: "排队上限",
  max_wait_seconds: "排队过期秒数",
  timeout_seconds: "超时秒数",
  rpm: "每分钟请求上限",
  tpm: "每分钟词元上限",
  groups: "共享容量",
  resources: "资源",
  capacity: "容量",
  limit: "上限",
  remaining: "剩余额度",
  used: "已用额度",
  bytes: "字节数",
  url: "地址",
  path: "路径",
  filename: "文件名",
  mime_type: "文件类型",
  size: "大小",
  duration: "时长",
  selected: "是否选入",
  applied: "是否应用",
  verified: "是否验证",
  supported: "是否支持",
};

/** Format secondary records without exposing storage keys or opaque identities. */
export function readableRecord(value: unknown): string {
  function lines(item: unknown, depth: number): string[] {
    if (item == null) return ["未记录"];
    if (typeof item === "boolean") return [item ? "是" : "否"];
    if (typeof item !== "object") return [sanitizeInternalText(String(item))];
    if (Array.isArray(item))
      return item.length
        ? item.flatMap((entry, index) =>
            lines(entry, depth + 1).map(
              (line, n) => `${n ? "  " : `${index + 1}. `}${line}`,
            ),
          )
        : ["暂无记录"];
    return Object.entries(item).flatMap(([key, entry]) => {
      if (
        /^(id|.*_ids?|.*_hash|.*_key|data_generation|quota_group|owner|owner_id)$/.test(
          key,
        )
      )
        return [];
      const name =
        fieldNames[key] ?? (/[^\x00-\x7f]/.test(key) ? key : "补充信息");
      const text =
        typeof entry === "string" &&
        /^(state|status|kind|type|.*_state|.*_reason|reason|error_code|service_class)$/.test(
          key,
        )
          ? [humanizeCode(entry, "详情尚未确认")]
          : lines(entry, depth + 1);
      return text.length === 1
        ? [`${name}：${text[0]}`]
        : [`${name}：`, ...text.map((line) => `  ${line}`)];
    });
  }
  return lines(value, 0).join("\n") || "已保存关联记录";
}
