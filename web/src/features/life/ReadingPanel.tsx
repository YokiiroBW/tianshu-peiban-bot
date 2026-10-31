import { useState } from "react";
import { ContentAcquire } from "./ContentAcquire";
import { ContentViewer, contentLabel, rangeText } from "./ContentViewer";
import { RuntimeFeedback, ResourceFeedback } from "./RuntimeFeedback";
import { useDraftId, useLifeAction, useLifeResource } from "./useLifeRuntime";
import { showState, type LifeAccess } from "./runtimeApi";
import type { content_ref, reading_record } from "./runtimeTypes";
import { LifeEvidence } from "./LifeEvidence";

export function ReadingPanel({ access }: { access: LifeAccess }) {
  const records = useLifeResource(access, "reading"),
    action = useLifeAction(access, records.refresh),
    draft = useDraftId("reading");
  const [ref, setRef] = useState<content_ref | null>(null),
    [mode, setMode] = useState<"solo" | "together">("together"),
    [selected, setSelected] = useState<string | null>(null),
    [resumeEpoch, setResumeEpoch] = useState(0);
  const row = records.items.find((x) => x.id === selected);
  async function open() {
    if (!ref) return;
    const answer = await action.run(
      "reading.open",
      {
        id: draft.id,
        query: {},
        scope: {},
        content_ref: ref,
        mode,
        participants: [],
      },
      0,
    );
    if (answer && answer.result.state !== "unknown") {
      setSelected(answer.result.id);
      setRef(null);
      draft.reset();
    }
  }
  return (
    <div className="life-runtime-grid">
      <section className="panel">
        <h3>原文与共同阅读</h3>
        <ContentAcquire access={access} onAcquired={setRef} />
        {ref && (
          <div className="life-record">
            <p>
              已取得 {contentLabel(ref.kind)} 原件 · 版本 {ref.version}
            </p>
            <label>
              阅读方式
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as typeof mode)}
              >
                <option value="together">和你共读</option>
                <option value="solo">角色自己读</option>
              </select>
            </label>
            <button
              className="button primary"
              disabled={action.busy}
              onClick={() => void open()}
            >
              建立阅读进度
            </button>
            <ContentViewer
              key={`${ref.object_id}:${ref.version}`}
              access={access}
              contentRef={ref}
            />
          </div>
        )}
        <RuntimeFeedback {...action} />
      </section>
      <section className="panel">
        <h3>阅读进度与收藏</h3>
        <button className="button" onClick={records.refresh}>
          刷新阅读记录
        </button>
        <ResourceFeedback {...records} empty={!records.items.length} />
        {records.items.map((item) => (
          <article className="life-record" key={item.id}>
            <header>
              <strong>
                {contentLabel(item.content_ref.kind)} · 版本{" "}
                {item.content_ref.version}
              </strong>
              <span className="status-pill">{showState(item.state)}</span>
            </header>
            <p>
              {item.mode === "together" ? "共同阅读" : "角色自主阅读"} ·
              上次位置 {rangeText(item.position)}
            </p>
            <p className="muted">
              已实读：
              {item.coverage.length
                ? item.coverage.map(rangeText).join("；")
                : "尚未读取正文"}
            </p>
            <LifeEvidence sources={item.content_ref.sources} />
            <div className="life-action-row">
              <button
                className="button"
                onClick={() => {
                  setSelected(item.id);
                  setResumeEpoch((epoch) => epoch + 1);
                }}
              >
                从断点继续
              </button>
              {item.state === "paused" ? (
                <button
                  className="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(
                      "reading.resume",
                      { id: item.id },
                      item.version,
                    )
                  }
                >
                  恢复阅读
                </button>
              ) : (
                !["closed", "completed", "unavailable"].includes(
                  item.state,
                ) && (
                  <button
                    className="button"
                    disabled={action.busy}
                    onClick={() =>
                      void action.run(
                        "reading.pause",
                        { id: item.id, reason: "用户暂停阅读" },
                        item.version,
                      )
                    }
                  >
                    暂停阅读
                  </button>
                )
              )}
              <button
                className="button"
                disabled={action.busy || item.state === "closed"}
                onClick={() =>
                  void action.run(
                    "reading.close",
                    { id: item.id, reason: "用户结束阅读" },
                    item.version,
                  )
                }
              >
                结束阅读
              </button>
            </div>
          </article>
        ))}
        {records.cursor && (
          <button className="button" onClick={() => void records.more()}>
            继续读取记录
          </button>
        )}
      </section>
      {row && (
        <ReadingSession
          key={`${row.id}:${row.content_ref.version}:${resumeEpoch}`}
          access={access}
          row={row}
          refreshed={records.refresh}
        />
      )}
    </div>
  );
}
function ReadingSession({
  access,
  row,
  refreshed,
}: {
  access: LifeAccess;
  row: reading_record;
  refreshed: () => void;
}) {
  const next = row.position.end;
  const total = row.content_ref.coverage.total ?? row.content_ref.coverage.end;
  const initial = {
    unit: row.position.unit,
    start: next < total ? next : row.position.start,
    end: Math.min(total, next + (row.position.unit === "seconds" ? 30 : 8000)),
  };
  return (
    <section className="panel life-span">
      <h3>继续实读原件</h3>
      <ContentViewer
        access={access}
        contentRef={row.content_ref}
        readingId={row.id}
        initialRange={initial.end > initial.start ? initial : row.position}
        onRead={refreshed}
      />
    </section>
  );
}
