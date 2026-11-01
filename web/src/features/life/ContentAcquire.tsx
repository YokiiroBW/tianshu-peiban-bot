import { useDomainCommand } from "../../api/useDomainResource";
import type { SourceRef } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import { ConsumptionProgress } from "./ActivityProgress";
import type { Consumption, Continuation } from "./domainTypes";

type ContentResult = {
  source: SourceRef;
  original_source?: SourceRef;
  source_refs: SourceRef[];
  content: {
    title: string | null;
    text: string;
    next_url?: string | null;
    posts?: {
      id: string;
      floor?: number;
      author: string;
      text: string;
      quotes?: { author?: string; text: string; floor?: number }[];
    }[];
  };
  continuation?: Continuation | null;
  follow_up?: Continuation | null;
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
  next_actions?: (Continuation & {
    candidate_key?: string;
    content_key?: string;
  })[];
  discovery_continuation?: Continuation | null;
  sources?: { source: string; state: string; code: string | null }[];
};
type VideoResult = {
  analysis: string;
  consumption: Consumption;
  continuation?: Continuation | null;
};

export function ContentAcquire({
  onAcquired,
}: {
  onAcquired: (source: SourceRef) => void;
}) {
  const read = useDomainCommand<ContentResult>("content-reading");
  const discover = useDomainCommand<Discovery>("content-discovery");
  const video = useDomainCommand<VideoResult>("video-observation");
  const busy = read.busy || discover.busy || video.busy;
  const found =
    discover.result?.receipt?.execution === "succeeded"
      ? discover.result.data
      : null;
  const body =
    read.result?.receipt?.execution === "succeeded" &&
    read.result.receipt.effect !== "unknown"
      ? read.result.data
      : null;
  const watched =
    video.result?.receipt?.execution === "succeeded" &&
    video.result.receipt.effect !== "unknown"
      ? video.result.data
      : null;
  function follow(next: Continuation) {
    // Continue from the server's real source and checkpoint, never a percentage.
    if (next.capability_id === "video.observe")
      void video.run("content/video.observe", next.arguments);
    else if (next.capability_id === "content.discover")
      void discover.run("content/discover", next.arguments);
    else if (next.capability_id === "content.read")
      void read.run("content/read", next.arguments);
  }
  const readable = (next: Continuation | null | undefined) =>
    !!next &&
    ["video.observe", "content.read", "content.discover"].includes(
      next.capability_id,
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
              intention: data.get("query"),
              mode: data.get("mode"),
              sources: ["web", "forum", "bilibili"],
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
          <button className="button" disabled={busy}>
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
          <button className="button" disabled={busy}>
            取得正文
          </button>
          <CommandFeedback {...read} />
        </form>
      </div>
      {found && (
        <section>
          <div className="domain-grid">
            {(found.candidates ?? []).map((item) => {
              const next = found.next_actions?.find(
                (value) =>
                  value.candidate_key === item.candidate_key ||
                  value.content_key === item.content_key,
              );
              return (
                <article className="domain-record" key={item.candidate_key}>
                  <strong>{item.title}</strong>
                  <p>{item.summary}</p>
                  <small>发现候选 · 尚未取得或理解实际正文</small>
                  <div className="domain-actions">
                    <a href={item.url} target="_blank" rel="noreferrer">
                      打开原链接
                    </a>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        next && readable(next)
                          ? follow(next)
                          : void read.run("content/read", {
                              url: item.url,
                              mode: "research",
                              render: "auto",
                            })
                      }
                    >
                      {next?.capability_id === "video.observe"
                        ? "观看实际音画"
                        : "读取实际正文"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
          {found.sources?.some((item) =>
            ["partial", "unavailable", "not_configured"].includes(item.state),
          ) && (
            <p className="muted">
              部分内容源本次没有返回完整候选，可保留已有发现继续阅读。
            </p>
          )}
          {readable(found.discovery_continuation) && (
            <button
              className="button"
              disabled={busy}
              onClick={() => follow(found.discovery_continuation!)}
            >
              继续寻找下一批内容
            </button>
          )}
        </section>
      )}
      {body?.source && body.content && (
        <article className="domain-record">
          <h4>{body.content.title ?? "已取得的正文"}</h4>
          {body.content.posts?.length ? (
            body.content.posts.map((post) => (
              <section className="life-forum-post" key={post.id}>
                <strong>
                  {post.author}
                  {post.floor !== undefined ? ` · ${post.floor} 楼` : ""}
                </strong>
                {post.quotes?.map((quote, index) => (
                  <blockquote key={index}>
                    {quote.author ? `${quote.author}：` : "引用："}
                    {quote.text}
                  </blockquote>
                ))}
                <p className="life-result-copy">{post.text}</p>
              </section>
            ))
          ) : (
            <p className="life-result-copy">
              {body.content.text?.slice(0, 3000)}
            </p>
          )}
          {body.content.text?.length > 3000 && (
            <p className="muted">
              这里只预览部分正文，继续阅读沿原件位置推进。
            </p>
          )}
          <SourceEvidence sources={body.source_refs ?? [body.source]} />
          <div className="domain-actions">
            <button className="button" onClick={() => onAcquired(body.source)}>
              加入持续阅读
            </button>
            {readable(body.continuation) && (
              <button
                className="button"
                disabled={busy}
                onClick={() => follow(body.continuation!)}
              >
                从实际位置取得下一段
              </button>
            )}
            {readable(body.follow_up) && (
              <button
                className="button"
                disabled={busy}
                onClick={() => follow(body.follow_up!)}
              >
                查看主题的新回复
              </button>
            )}
          </div>
        </article>
      )}
      <details>
        <summary>实际观看视频片段</summary>
        <form
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void video.run("content/video.observe", {
              url: data.get("url"),
              start: Number(data.get("start")),
              ...(data.get("end") ? { end: Number(data.get("end")) } : {}),
              question: data.get("question"),
              observation_mode: "auto",
            });
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
                placeholder="留空按原件实际时长"
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
          <button className="button" disabled={busy}>
            观看指定窗口
          </button>
          <CommandFeedback {...video} />
        </form>
      </details>
      {watched?.consumption && (
        <article className="domain-record">
          <h4>本次实际观看</h4>
          <p className="life-result-copy">{watched.analysis}</p>
          <ConsumptionProgress value={watched.consumption} />
          {readable(watched.continuation) ? (
            <button
              className="button"
              disabled={busy}
              onClick={() => follow(watched.continuation!)}
            >
              从实际位置继续本次窗口
            </button>
          ) : (
            <p className="muted">
              本次回执没有提供后续位置；这不代表所有细节都已理解。
            </p>
          )}
        </article>
      )}
    </DetailCard>
  );
}
