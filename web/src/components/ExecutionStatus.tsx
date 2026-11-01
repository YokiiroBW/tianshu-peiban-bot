import type { ExecutionObservation } from "../api/commandRecovery";

const lanes: Record<string, string> = {
  control: "运行控制",
  inbound: "消息接收",
  delivery: "消息投递",
  conversation: "对话回应",
  proactive: "主动回应",
  schedule: "日程与约定",
  life: "生活活动",
  planning: "生活规划",
  diary: "日记整理",
  writing: "章节写作",
  memory_consolidation: "记忆整理",
  memory_index: "记忆索引",
  memory_recovery: "记忆恢复",
  source_lifecycle: "来源更新与清理",
  growth: "成长整理",
  images: "生图",
  speech: "语音生成",
  book_audio: "有声书生成",
  perception: "图片与影音理解",
  reactions: "表情图库",
  downloads: "媒体下载",
  media: "媒体查询与编排",
  plugins: "插件执行",
  infrastructure: "基础设施操作",
  maintenance: "资源维护",
  book_sources: "书源与订阅",
  comic_sources: "漫画来源与订阅",
  video_subscriptions: "视频订阅",
  music_playlists: "音乐歌单",
  music_navidrome: "音乐库同步",
  music_lyrics: "歌词更新",
  comic_extensions: "漫画扩展",
  comic_cleanup: "漫画清理",
  media_retention: "媒体保留整理",
  music_library: "音乐库整理",
};

export function executionLaneName(value?: string | null) {
  return value ? (lanes[value] ?? "未识别的执行通道") : "尚未确定";
}

const terminal: Record<string, string> = {
  succeeded: "执行完成，效果与投递以回执为准",
  failed: "执行失败",
  cancelled: "已取消",
  aborted: "已停止",
};

function waitingText(execution: ExecutionObservation | undefined, state?: string) {
  if (state === "unknown") return "结果未知，请核对原任务";
  if (state && terminal[state]) return terminal[state];
  if (execution?.wait_reason === "execution_lane_unresolved")
    return "执行通道信息尚未确定";
  if (execution?.wait_reason === "scheduled") return "尚未到计划时间";
  if (execution?.wait_reason === "native_task_queue") return "等待本功能执行";
  if (execution?.wait_reason === "executing") return "本功能执行中";
  if (execution?.job_state === "doing") return "本功能执行中";
  if (execution?.job_state && terminal[execution.job_state])
    return terminal[execution.job_state];
  return "执行调度信息尚未取得";
}

export function ExecutionStatus({
  execution,
  state,
  compact = false,
}: {
  execution?: ExecutionObservation;
  state?: string;
  compact?: boolean;
}) {
  return (
    <div className="execution-status">
      {!compact && <p>执行通道：{executionLaneName(execution?.execution_lane)}</p>}
      <p>{waitingText(execution, state)}</p>
      {execution?.scheduled_at && execution.wait_reason === "scheduled" && (
        <p>计划时间：{new Date(execution.scheduled_at).toLocaleString("zh-CN")}</p>
      )}
      {execution && (
        <details>
          <summary>调度详情</summary>
          <pre>{JSON.stringify(execution, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
