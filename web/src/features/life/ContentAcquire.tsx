import { useState } from "react";
import { useDomainCommand } from "../../api/useDomainResource";
import type { SourceRef } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";

type ContentResult = {
  source: SourceRef;
  original_source?: SourceRef;
  source_refs: SourceRef[];
  content: { title: string | null; text: string; next_url: string | null };
  continuation?: { capability_id: string; arguments: Record<string, unknown> };
};
type Discovery = {
  candidates: {
    candidate_key: string;
    content_key: string;
    title: string;
    url: string;
    summary: string;
    kind: string;
  }[];
  sources: { source: string; state: string; code: string | null }[];
};
type VideoResult = {
  analysis: string;
  consumption: {
    source: SourceRef;
    modalities: string[];
    ranges: { start: number; end: number }[];
    frame_times: number[];
    omitted: string[];
    analysis_completed: boolean;
    model_attempt_id: string | null;
    next_position: { unit: "seconds"; start: number; end: number } | null;
  };
};

export function ContentAcquire({
  onAcquired,
}: {
  onAcquired: (source: SourceRef) => void;
}) {
  const read = useDomainCommand<ContentResult>("content-reading");
  const discover = useDomainCommand<Discovery>("content-discovery");
  const video = useDomainCommand<VideoResult>("video-observation");
  const [lastVideo, setLastVideo] = useState<Record<string, unknown> | null>(
    null,
  );
  return (
    <DetailCard title="寻找内容或打开网页">
      <div className="domain-grid">
        <form
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void discover.run("content/discover", {
              query: data.get("query"),
              mode: data.get("mode"),
              sources: ["web", "bilibili"],
              limit: 8,
            });
          }}
        >
          <label>
            想找什么
            <input
              name="query"
              required
              placeholder="主题、问题或想随意逛逛的方向"
            />
          </label>
          <label>
            这次目的
            <select name="mode">
              <option value="research">查一个问题</option>
              <option value="explore">随意探索</option>
            </select>
          </label>
          <button className="button" disabled={discover.busy}>
            寻找候选内容
          </button>
          <CommandFeedback {...discover} />
        </form>
        <form
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void read.run("content/read", {
              url: data.get("url"),
              mode: "research",
              render: data.get("render"),
            });
          }}
        >
          <label>
            网页或论坛地址
            <input type="url" name="url" required placeholder="https://…" />
          </label>
          <label>
            网页表示
            <select name="render">
              <option value="auto">自动取得实际正文</option>
              <option value="http">HTTP正文</option>
              <option value="browser">浏览器实际DOM</option>
            </select>
          </label>
          <button className="button" disabled={read.busy}>
            取得正文
          </button>
          <CommandFeedback {...read} />
        </form>
      </div>
      {discover.result?.data && (
        <div className="domain-grid">
          {discover.result.data.candidates.map((item) => (
            <article className="domain-record" key={item.candidate_key}>
              <strong>{item.title}</strong>
              <p>{item.summary}</p>
              <small>目录候选 · 尚未阅读正文</small>
              <div className="domain-actions">
                <a href={item.url} target="_blank" rel="noreferrer">
                  打开原链接
                </a>
                <button
                  className="button"
                  disabled={read.busy}
                  onClick={() =>
                    void read.run("content/read", {
                      url: item.url,
                      mode: "research",
                      render: "auto",
                    })
                  }
                >
                  读取正文
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {read.result?.data?.source && (
        <article className="domain-record">
          <h4>{read.result.data.content.title ?? "已取得的正文"}</h4>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {read.result.data.content.text.slice(0, 3000)}
          </p>
          {read.result.data.content.text.length > 3000 && (
            <p className="muted">
              这里只预览前3000字，持续阅读可继续实际原件范围。
            </p>
          )}
          <SourceEvidence sources={read.result.data.source_refs} />
          <button
            className="button"
            onClick={() => onAcquired(read.result!.data.source)}
          >
            加入持续阅读
          </button>
          {read.result.data.continuation && (
            <button
              className="button"
              disabled={read.busy}
              onClick={() =>
                void read.run(
                  "content/read",
                  read.result!.data.continuation!.arguments,
                )
              }
            >
              取得下一页
            </button>
          )}
        </article>
      )}
      <details>
        <summary>实际观看视频片段</summary>
        <form
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const args = {
              url: data.get("url"),
              start: Number(data.get("start")),
              end: Number(data.get("end")),
              question: data.get("question"),
            };
            setLastVideo(args);
            void video.run("content/video.observe", args);
          }}
        >
          <label>
            视频地址
            <input type="url" name="url" required />
          </label>
          <div className="domain-grid">
            <label>
              开始秒数
              <input
                type="number"
                name="start"
                min="0"
                step="0.1"
                defaultValue="0"
                required
              />
            </label>
            <label>
              结束秒数
              <input
                type="number"
                name="end"
                min="0.1"
                step="0.1"
                defaultValue="60"
                required
              />
            </label>
          </div>
          <label>
            想关注什么
            <textarea
              name="question"
              placeholder="留空让角色记录这段实际音画的内容"
            />
          </label>
          <button className="button" disabled={video.busy}>
            观看指定窗口
          </button>
          <CommandFeedback {...video} />
        </form>
        {video.result?.data?.consumption && (
          <article>
            <p style={{ whiteSpace: "pre-wrap" }}>
              {video.result.data.analysis}
            </p>
            <p>
              已处理模态：{video.result.data.consumption.modalities.join("、")}{" "}
              · 实际采样 {video.result.data.consumption.frame_times.length} 帧
            </p>
            <p>
              范围：
              {video.result.data.consumption.ranges
                .map((range) => `${range.start}–${range.end}秒`)
                .join("、")}
            </p>
            {video.result.data.consumption.omitted.length > 0 && (
              <p>
                省略与缺口：{video.result.data.consumption.omitted.join("、")}
              </p>
            )}
            <SourceEvidence sources={[video.result.data.consumption.source]} />
            {video.result.data.consumption.next_position && lastVideo ? (
              <button
                className="button"
                disabled={video.busy}
                onClick={() => {
                  const next = video.result!.data.consumption.next_position!;
                  const args = {
                    ...lastVideo,
                    start: next.start,
                    end: next.end,
                  };
                  setLastVideo(args);
                  void video.run("content/video.observe", args);
                }}
              >
                从实际位置继续本次窗口
              </button>
            ) : (
              <p className="muted">
                本次请求窗口没有后续位置；这不表示观看了整部视频。
              </p>
            )}
          </article>
        )}
      </details>
    </DetailCard>
  );
}
