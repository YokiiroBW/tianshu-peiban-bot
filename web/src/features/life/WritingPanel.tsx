import { BookShelf } from "../../components/BookShelf";
import { Drawer } from "../../components/Drawer";
import { useEffect, useState } from "react";
import { ResourceFeedback, RuntimeFeedback } from "./RuntimeFeedback";
import { useDraftId, useLifeResource, useLifeAction } from "./useLifeRuntime";
import { showState, type LifeAccess } from "./runtimeApi";
import type { chapter_record, works_record } from "./runtimeTypes";

export function WritingPanel({ access }: { access: LifeAccess }) {
  const works = useLifeResource(access, "works"),
    action = useLifeAction(access, works.refresh),
    draft = useDraftId("work");
  const [selected, setSelected] = useState(""),
    [title, setTitle] = useState(""),
    [outline, setOutline] = useState(""),
    [characters, setCharacters] = useState("");
  const work = works.items.find((x) => x.id === selected);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    const result = await action.run(
      "writing.create",
      {
        id: draft.id,
        title,
        outline,
        characters: characters
          .split("\n")
          .filter((x) => x.trim())
          .map((line) => {
            const [name, ...description] = line.split("：");
            return {
              name: name.trim(),
              description: description.join("：").trim() || "未补充人物设定",
            };
          }),
        recipe: "chapter",
      },
      0,
    );
    if (result && result.result.state !== "unknown") {
      draft.reset();
      setSelected(result.result.id);
      setTitle("");
      setOutline("");
      setCharacters("");
    }
  }
  return (
    <div className="writing-workspace">
      <section className="panel">
        <h3>作品与原稿</h3>
        <button className="button" onClick={works.refresh}>
          读取作品版本
        </button>
        <ResourceFeedback {...works} empty={!works.items.length} />
        <BookShelf
          entries={works.items.map((row) => ({
            id: row.id,
            title: row.title,
            subtitle: row.outline,
            badge: `${row.chapter_ids.length} 章`,
            category: row.state === "published" ? "已发布作品" : "写作中",
            selected: selected === row.id,
          }))}
          onOpen={setSelected}
          empty="书架上还没有作品"
        />
        {works.cursor && (
          <button className="button" onClick={() => void works.more()}>
            继续读取作品
          </button>
        )}
        <details className="writing-create">
          <summary className="button">开始新作品</summary>
          <form className="life-form" onSubmit={create}>
            <h4>开始新作品</h4>
            <label>
              作品名称
              <input
                required
                maxLength={200}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              大纲
              <textarea
                required
                maxLength={4000}
                value={outline}
                onChange={(e) => setOutline(e.target.value)}
              />
            </label>
            <label>
              人物设定
              <textarea
                value={characters}
                maxLength={8000}
                onChange={(e) => setCharacters(e.target.value)}
                placeholder="每行一位人物：姓名：设定"
              />
            </label>
            <button className="button primary" disabled={action.busy}>
              保存作品与大纲
            </button>
          </form>
        </details>
        <RuntimeFeedback {...action} />
      </section>
      <Drawer
        title={work?.title ?? "作品"}
        open={!!work}
        onClose={() => setSelected("")}
        variant="card"
      >
        {work && (
          <WorkChapters
            key={work.id}
            access={access}
            work={work}
            refreshed={works.refresh}
          />
        )}
      </Drawer>
    </div>
  );
}
function WorkChapters({
  access,
  work,
  refreshed,
}: {
  access: LifeAccess;
  work: works_record;
  refreshed: () => void;
}) {
  const action = useLifeAction(access, refreshed),
    draft = useDraftId("chapter");
  const [title, setTitle] = useState(""),
    [goal, setGoal] = useState(""),
    [selected, setSelected] = useState(work.chapter_ids[0] ?? "");
  const ids =
    selected && !work.chapter_ids.includes(selected)
      ? [...work.chapter_ids, selected]
      : work.chapter_ids;
  async function add(e: React.FormEvent) {
    e.preventDefault();
    const answer = await action.run(
      "chapter.add",
      { id: draft.id, work_id: work.id, title, goal, order: null },
      work.version,
    );
    if (answer && answer.result.state !== "unknown") {
      draft.reset();
      setSelected(answer.result.id);
      setTitle("");
      setGoal("");
    }
  }
  return (
    <section className="panel">
      <h3>{work.title} · 章节</h3>
      <p className="muted">
        生成保存为原稿，经审阅后发布；继续编辑沿用同一章节的当前版本。
      </p>
      {!ids.length && <p className="muted">这部作品尚无章节。</p>}
      {ids.map((id, index) => (
        <article className="life-record" key={id}>
          <button className="button" onClick={() => setSelected(id)}>
            打开第 {index + 1} 章当前原稿
          </button>
        </article>
      ))}
      <form className="life-form" onSubmit={add}>
        <h4>添加章节</h4>
        <label>
          章节名称
          <input
            required
            maxLength={1000}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          本章目标
          <textarea
            required
            maxLength={4000}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
          />
        </label>
        <button className="button" disabled={action.busy}>
          保存章节目标
        </button>
      </form>
      <RuntimeFeedback {...action} />
      {selected && (
        <CurrentChapter key={selected} access={access} id={selected} />
      )}
    </section>
  );
}
function CurrentChapter({ access, id }: { access: LifeAccess; id: string }) {
  const records = useLifeResource(access, "chapter", {
    objectId: id,
    chapterView: "current",
  });
  const chapter = records.items[0];
  useEffect(() => {
    if (!chapter || !["generating", "queued"].includes(chapter.state)) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) records.refresh();
    }, 4000);
    return () => clearInterval(timer);
  }, [chapter]);
  return (
    <>
      <ResourceFeedback {...records} empty={!chapter} />
      <button className="button" onClick={records.refresh}>
        读取当前章节版本
      </button>
      {chapter && (
        <ChapterEditor
          key={`${chapter.id}:${chapter.version}`}
          access={access}
          chapter={chapter}
          refreshed={records.refresh}
        />
      )}
    </>
  );
}
function ChapterEditor({
  access,
  chapter,
  refreshed,
}: {
  access: LifeAccess;
  chapter: chapter_record;
  refreshed: () => void;
}) {
  const action = useLifeAction(access, refreshed);
  const [content, setContent] = useState(chapter.content ?? ""),
    [reason, setReason] = useState(""),
    [notes, setNotes] = useState(""),
    [drift, setDrift] = useState(false);
  return (
    <div className="life-record life-chapter">
      <h4>
        {chapter.title} · 当前原稿版本 {chapter.version}
      </h4>
      <p>{showState(chapter.state)}</p>
      {chapter.content === null ? (
        <p className="muted">尚无可读原稿。</p>
      ) : (
        <details open>
          <summary>实读当前原稿</summary>
          <div className="life-original-text">{chapter.content}</div>
        </details>
      )}
      <button
        className="button"
        disabled={action.busy}
        onClick={() =>
          void action.run(
            "chapter.generate",
            { id: chapter.id },
            chapter.version,
          )
        }
      >
        {chapter.content ? "基于原章节继续创作" : "生成本章原稿"}
      </button>
      <form
        className="life-form"
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(
            "chapter.revise",
            { id: chapter.id, content, reason },
            chapter.version,
          );
        }}
      >
        <label>
          修订正文
          <textarea
            required
            maxLength={8000}
            rows={12}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        <label>
          修订说明
          <input
            required
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <button className="button" disabled={action.busy}>
          保存新原稿版本
        </button>
      </form>
      <label>
        审阅意见
        <textarea
          maxLength={2000}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </label>
      <label className="life-check">
        <input
          type="checkbox"
          checked={drift}
          onChange={(e) => setDrift(e.target.checked)}
        />
        我已检查大纲与人物设定的变化
      </label>
      <div className="life-action-row">
        <button
          className="button"
          disabled={action.busy || chapter.content === null}
          onClick={() =>
            void action.run(
              "chapter.review",
              {
                id: chapter.id,
                decision: "approved",
                notes,
                acknowledge_drift: drift,
              },
              chapter.version,
            )
          }
        >
          通过审阅
        </button>
        <button
          className="button"
          disabled={action.busy || chapter.content === null}
          onClick={() =>
            void action.run(
              "chapter.review",
              {
                id: chapter.id,
                decision: "changes_requested",
                notes,
                acknowledge_drift: drift,
              },
              chapter.version,
            )
          }
        >
          请继续修改
        </button>
        <button
          className="button primary"
          disabled={action.busy || chapter.content === null}
          onClick={() =>
            void action.run(
              "chapter.publish",
              { id: chapter.id },
              chapter.version,
            )
          }
        >
          发布当前审阅版本
        </button>
      </div>
      <RuntimeFeedback {...action} />
    </div>
  );
}
