import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  useDomainResource,
  useDomainTarget,
} from "../../api/useDomainResource";
import { uploadMedia } from "../../api/uploads";
import type { Coverage, MediaRef, Page, SourceRef } from "../../api/contracts";
import { MediaPreview } from "../../components/MediaPreview";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ContentAcquire } from "./ContentAcquire";
import type { Reading } from "./domainTypes";

type Fetched = {
  id: string;
  source_ref: SourceRef;
  content: string | null;
  media: MediaRef | null;
  representation: string;
  coverage: Coverage;
  next_position: number | null;
  analysis_completed: false;
};
export function ReadingPanel({ canManage }: { canManage: boolean }) {
  const { session } = useAuth();
  const target = useDomainTarget();
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<Page<Reading>>("life/reading", {
    cursor,
    limit: 30,
  });
  const open = useDomainCommand<{ id: string }>("reading-open");
  const read = useDomainCommand<Fetched>("reading-current");
  const controls = useDomainCommand("reading-controls");
  const [selected, setSelected] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const row = view.data?.items.find((item) => item.id === selected);
  function acquired(source: SourceRef) {
    if (!canManage) return;
    void open
      .run("life/reading/open", {
        source_ref: source,
        requested: {
          position: source.locator?.start ?? 0,
          max_characters: 12000,
        },
      })
      .then((result) => {
        if (result?.data) {
          setSelected(result.data.id);
          void view.refresh();
        }
      });
  }
  return (
    <section className="life-card">
      <div className="section-heading">
        <div>
          <h3>阅读与学习</h3>
          <p className="muted">
            保存原件位置与实际取到的范围。抓到目录或摘要不会算作读完正文。
          </p>
        </div>
        <button className="button" onClick={() => void view.refresh()}>
          读取进度
        </button>
      </div>
      {canManage && (
        <>
          <ContentAcquire onAcquired={acquired} />
          <label className="domain-form">
            从自己的文件开始
            <input
              type="file"
              accept="text/*,.txt,.md,.html,.json"
              disabled={uploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file || !session?.authenticated) return;
                setUploading(true);
                setError("");
                void uploadMedia(
                  file,
                  { ...target, actor_id: target.actor_id ?? undefined },
                  session.csrf,
                  new AbortController().signal,
                )
                  .then((uploaded) => acquired(uploaded.source_ref))
                  .catch((cause) =>
                    setError(
                      cause instanceof Error ? cause.message : "上传结果待确认",
                    ),
                  )
                  .finally(() => setUploading(false));
              }}
            />
          </label>
          <details>
            <summary>按已授权来源编号打开</summary>
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                acquired({
                  source_id: String(data.get("source_id")),
                  revision: Number(data.get("revision")),
                  locator: {},
                });
              }}
            >
              <label>
                来源编号
                <input name="source_id" required />
              </label>
              <label>
                原件修订
                <input
                  name="revision"
                  type="number"
                  min="1"
                  defaultValue="1"
                  required
                />
              </label>
              <button className="button" disabled={open.busy}>
                打开原件
              </button>
            </form>
          </details>
          <CommandFeedback {...open} />
        </>
      )}
      {(view.error || error) && <p role="alert">{view.error || error}</p>}
      <div className="domain-grid">
        {view.data?.items.map((item) => (
          <article className="domain-record" key={item.id}>
            <strong>
              {(
                {
                  reading: "正在阅读",
                  paused: "暂时放下",
                  completed: "本次原件范围已取完",
                  closed: "已结束",
                } as Record<string, string>
              )[item.state] ?? item.state}
            </strong>
            <p>
              已取 {item.covered.length} 段 · 下一字符位置{" "}
              {item.next_position ?? "当前窗口末尾"}
            </p>
            <SourceEvidence sources={[item.source_ref]} />
            <div className="domain-actions">
              <button className="button" onClick={() => setSelected(item.id)}>
                查看这份进度
              </button>
              {canManage && (
                <>
                  {item.state === "reading" && (
                    <button
                      className="text-link"
                      disabled={controls.busy}
                      onClick={() =>
                        void controls
                          .run(
                            "life/reading/pause",
                            { id: item.id },
                            item.revision,
                          )
                          .then((result) => {
                            if (result) void view.refresh();
                          })
                      }
                    >
                      暂停
                    </button>
                  )}
                  {item.state === "paused" && (
                    <button
                      className="text-link"
                      disabled={controls.busy}
                      onClick={() =>
                        void controls
                          .run(
                            "life/reading/resume",
                            { id: item.id },
                            item.revision,
                          )
                          .then((result) => {
                            if (result) void view.refresh();
                          })
                      }
                    >
                      恢复阅读
                    </button>
                  )}
                  {item.state !== "closed" && (
                    <button
                      className="text-link"
                      disabled={controls.busy}
                      onClick={() =>
                        void controls
                          .run(
                            "life/reading/close",
                            { id: item.id },
                            item.revision,
                          )
                          .then((result) => {
                            if (result) void view.refresh();
                          })
                      }
                    >
                      结束本次阅读
                    </button>
                  )}
                </>
              )}
            </div>
          </article>
        ))}
      </div>
      <CommandFeedback {...controls} />
      {row && (
        <section className="domain-record">
          <h4>当前原件</h4>
          {canManage && row.state === "reading" && (
            <button
              className="button"
              disabled={read.busy || view.loading}
              onClick={() =>
                void read
                  .run("life/reading/read", { id: row.id }, row.revision)
                  .then((result) => {
                    if (result) void view.refresh();
                  })
              }
            >
              {row.covered.length ? "取得下一段实际正文" : "开始读取正文"}
            </button>
          )}
          <CommandFeedback {...read} />
          {read.result?.data.id === row.id && (
            <>
              <article className="life-revision">
                <p style={{ whiteSpace: "pre-wrap" }}>
                  {read.result.data.content ?? "这份原件没有文本表示。"}
                </p>
              </article>
              {read.result.data.media && (
                <MediaPreview media={read.result.data.media} />
              )}
              <p>
                表示：{read.result.data.representation} · 实际范围{" "}
                {read.result.data.coverage.ranges
                  ?.map((range) => `${range.start}–${range.end}`)
                  .join("、") || "原件引用"}
              </p>
              <p className="muted">
                这是取回记录；是否完成分析另有实际模型回执。
              </p>
            </>
          )}
        </section>
      )}
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(view.data!.next_cursor)}
          >
            继续看阅读记录
          </button>
        )}
      </div>
    </section>
  );
}
