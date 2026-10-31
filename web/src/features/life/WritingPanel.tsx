import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Page, SourceRef } from "../../api/contracts";
import { BookShelf } from "../../components/BookShelf";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import {
  PublicationAudience,
  type PublicationSelection,
} from "./PublicationAudience";
import { timeLabel } from "./domainTypes";

type Work = {
  id: string;
  title: string;
  chapter_count: number;
  revision?: number;
  synopsis?: string;
  outline?: string;
  characters?: string;
};
type Chapter = {
  id: string;
  work_id: string;
  revision: number;
  position: number;
  title: string;
  plan?: string;
  state: string;
  current_revision?: number;
  published_revision: number | null;
  reason?: string | null;
  review_state?: string | null;
  review_note?: string | null;
  generation_id?: string | null;
};
type ChapterRead = {
  chapter: Chapter;
  version: {
    revision: number;
    content: string;
    created_at: string;
    source_refs?: SourceRef[];
    recipe?: { source_refs: SourceRef[] };
  };
};
const labels: Record<string, string> = {
  planned: "已记下章节计划",
  generating: "正在创作",
  cancelling: "正在停止后续写入",
  unknown: "原模型结果待确认",
  draft: "草稿",
  published: "已发布",
  failed: "本次生成未完成",
  cancelled: "已停止本次生成",
};
export function WritingPanel({ canManage }: { canManage: boolean }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const works = useDomainResource<Page<Work>>("life/works", {
    cursor,
    limit: 30,
  });
  const [selected, setSelected] = useState<Work | null>(null);
  const [creating, setCreating] = useState(false);
  return (
    <div className="domain-workspace">
      <div className="section-heading">
        <p className="muted">
          作品、提纲、人物和章节各自保存修订；模型生成先留作草稿。
        </p>
        {canManage && (
          <button className="button primary" onClick={() => setCreating(true)}>
            开始一部作品
          </button>
        )}
      </div>
      {works.error && <p role="alert">{works.error}</p>}
      {creating && (
        <WorkEditor
          value={null}
          close={() => setCreating(false)}
          saved={() => {
            setCreating(false);
            void works.refresh();
          }}
        />
      )}
      <BookShelf
        entries={(works.data?.items ?? []).map((item) => ({
          id: item.id,
          title: item.title,
          badge: `${item.chapter_count}章`,
          category: canManage ? "作品书架" : "已发布作品",
          selected: item.id === selected?.id,
        }))}
        onOpen={(id) =>
          setSelected(works.data!.items.find((item) => item.id === id)!)
        }
        empty={canManage ? "故事从第一句话开始" : "还没有可阅读的已发布作品"}
      />
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批作品
          </button>
        )}
        {works.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(works.data!.next_cursor)}
          >
            继续翻书架
          </button>
        )}
      </div>
      {selected && (
        <WorkChapters
          key={selected.id}
          work={selected}
          canManage={canManage}
          close={() => setSelected(null)}
          refreshWorks={works.refresh}
        />
      )}
    </div>
  );
}

function WorkEditor({
  value,
  close,
  saved,
}: {
  value: Work | null;
  close: () => void;
  saved: () => void;
}) {
  const action = useDomainCommand(`work:${value?.id ?? "new"}`);
  return (
    <DetailCard title={value ? "作品设定" : "新的作品"}>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void action
            .run(
              "life/works.save",
              {
                ...(value ? { id: value.id } : {}),
                title: data.get("title"),
                synopsis: data.get("synopsis"),
                outline: data.get("outline"),
                characters: data.get("characters"),
              },
              value?.revision ?? null,
            )
            .then((result) => {
              if (result) saved();
            });
        }}
      >
        <label>
          作品名
          <input
            name="title"
            defaultValue={value?.title ?? ""}
            required
            maxLength={1000}
          />
        </label>
        <label>
          简介
          <textarea
            name="synopsis"
            defaultValue={value?.synopsis ?? ""}
            rows={4}
            maxLength={100000}
          />
        </label>
        <label>
          故事提纲
          <textarea
            name="outline"
            defaultValue={value?.outline ?? ""}
            rows={8}
            maxLength={100000}
          />
        </label>
        <label>
          人物与设定
          <textarea
            name="characters"
            defaultValue={value?.characters ?? ""}
            rows={8}
            maxLength={100000}
          />
        </label>
        <div className="domain-actions">
          <button className="button primary" disabled={action.busy}>
            保存作品设定
          </button>
          <button className="button" type="button" onClick={close}>
            收起
          </button>
        </div>
        <CommandFeedback {...action} />
      </form>
    </DetailCard>
  );
}

function WorkChapters({
  work,
  canManage,
  close,
  refreshWorks,
}: {
  work: Work;
  canManage: boolean;
  close: () => void;
  refreshWorks: () => Promise<void>;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<{ work: Work; chapters: Page<Chapter> }>(
    "life/chapters",
    { work_id: work.id, cursor, limit: 40 },
    true,
    15000,
  );
  const [selected, setSelected] = useState<Chapter | null>(null);
  const [editing, setEditing] = useState<Work | null>(null);
  const action = useDomainCommand(`chapter-add:${work.id}`);
  const current =
    view.data?.chapters.items.find((item) => item.id === selected?.id) ??
    selected;
  const reload = () => {
    void view.refresh();
    void refreshWorks();
  };
  useEffect(() => {
    if (view.accessLost) {
      setSelected(null);
      setEditing(null);
    }
  }, [view.accessLost]);
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h3>{work.title}</h3>
          <p className="muted">
            {canManage
              ? "创作材料只用于作者工作区，读者看到已发布的修订。"
              : "以下章节是当前账号有权阅读的已发布内容。"}
          </p>
        </div>
        <button className="button" onClick={close}>
          回到书架
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {canManage && view.data?.work.revision && (
        <button
          className="button"
          onClick={() => setEditing(structuredClone(view.data!.work))}
        >
          修改简介、提纲与人物
        </button>
      )}
      {editing && (
        <WorkEditor
          key={`${editing.id}:${editing.revision}`}
          value={editing}
          close={() => setEditing(null)}
          saved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
      <ol className="life-plan">
        {view.data?.chapters.items.map((item) => (
          <li key={item.id}>
            <span className="life-phase-time">第{item.position}章</span>
            <span className="life-phase-node" />
            <button
              className="life-phase-title text-link"
              onClick={() => setSelected(item)}
            >
              <strong>{item.title}</strong>
              <span className="badge">{labels[item.state] ?? item.state}</span>
            </button>
          </li>
        ))}
      </ol>
      {canManage && view.data?.work.revision && (
        <DetailCard title="添加下一章">
          <form
            className="domain-form"
            onSubmit={(event) => {
              event.preventDefault();
              const form = event.currentTarget;
              const data = new FormData(form);
              void action
                .run(
                  "life/chapters.add",
                  {
                    work_id: work.id,
                    title: data.get("title"),
                    plan: data.get("plan"),
                  },
                  view.data!.work.revision!,
                )
                .then((result) => {
                  if (result) {
                    form.reset();
                    reload();
                  }
                });
            }}
          >
            <label>
              章节名
              <input name="title" required maxLength={1000} />
            </label>
            <label>
              这一章想发生什么
              <textarea name="plan" maxLength={20000} rows={4} />
            </label>
            <button className="button primary" disabled={action.busy}>
              保存章节计划
            </button>
            <CommandFeedback {...action} />
          </form>
        </DetailCard>
      )}
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首章
          </button>
        )}
        {view.data?.chapters.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(view.data!.chapters.next_cursor)}
          >
            继续读目录
          </button>
        )}
      </div>
      <Drawer
        title={current?.title ?? "章节"}
        open={!!current}
        onClose={() => setSelected(null)}
        variant="card"
      >
        {current && (
          <ChapterSheet
            key={current.id}
            row={current}
            canManage={canManage}
            refreshList={view.refresh}
          />
        )}
      </Drawer>
    </section>
  );
}

function ChapterSheet({
  row,
  canManage,
  refreshList,
}: {
  row: Chapter;
  canManage: boolean;
  refreshList: () => Promise<void>;
}) {
  const [revision, setRevision] = useState<number | null>(null);
  const read = useDomainResource<ChapterRead>(
    "life/chapters/read",
    { id: row.id, ...(revision ? { revision } : {}) },
    !canManage || !!row.current_revision,
    15000,
  );
  const action = useDomainCommand(`chapter:${row.id}`);
  const [editing, setEditing] = useState<{
    revision: number;
    content: string;
  } | null>(null);
  const [audience, setAudience] = useState<PublicationSelection>({
    reader_ids: [],
    conversation_ids: [],
  });
  useEffect(() => {
    if (read.accessLost) setEditing(null);
  }, [read.accessLost]);
  const current = read.data?.chapter ?? row;
  const active = ["generating", "cancelling", "unknown"].includes(
    current.state,
  );
  const reload = () => {
    void read.refresh();
    void refreshList();
  };
  return (
    <section className="domain-workspace">
      <p>
        <span className="badge">{labels[current.state] ?? current.state}</span>
        {current.reason && <> · {current.reason}</>}
      </p>
      {read.error && <p role="alert">{read.error}</p>}
      {read.data && (
        <>
          <article className="life-revision">
            <p style={{ whiteSpace: "pre-wrap" }}>
              {read.data.version.content}
            </p>
            <small>
              第{read.data.version.revision}版 ·{" "}
              {timeLabel(read.data.version.created_at)}
            </small>
          </article>
          <SourceEvidence
            sources={
              read.data.version.source_refs ??
              read.data.version.recipe?.source_refs ??
              []
            }
          />
        </>
      )}
      {canManage && (
        <>
          <div className="domain-actions">
            <button
              className="button"
              disabled={active}
              onClick={() =>
                setEditing({
                  revision: current.revision,
                  content: read.data?.version.content ?? "",
                })
              }
            >
              {read.data ? "编辑这一版正文" : "亲自写下正文"}
            </button>
            {(current.current_revision ?? 0) > 1 && (
              <label>
                历史版本
                <input
                  type="number"
                  min="1"
                  max={current.current_revision}
                  value={revision ?? ""}
                  placeholder="当前版"
                  onChange={(event) =>
                    setRevision(
                      event.target.value ? Number(event.target.value) : null,
                    )
                  }
                />
              </label>
            )}
            {active && (
              <>
                <button
                  className="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action
                      .run(
                        "life/chapters/cancel",
                        { id: current.id },
                        current.revision,
                      )
                      .then((result) => {
                        if (result) reload();
                      })
                  }
                >
                  停止后续写入
                </button>
                <button
                  className="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action
                      .run(
                        "life/chapters/reconcile",
                        { id: current.id },
                        current.revision,
                      )
                      .then((result) => {
                        if (result) reload();
                      })
                  }
                >
                  核对原模型任务
                </button>
              </>
            )}
          </div>
          {!active && (
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action
                  .run(
                    "life/chapters/generate",
                    {
                      id: current.id,
                      instruction: new FormData(event.currentTarget).get(
                        "instruction",
                      ),
                    },
                    current.revision,
                  )
                  .then((result) => {
                    if (result) reload();
                  });
              }}
            >
              <label>
                这次创作要求
                <textarea
                  name="instruction"
                  defaultValue=""
                  maxLength={20000}
                  placeholder="使用已保存的提纲、人物及前文章节继续创作"
                />
              </label>
              <button className="button primary" disabled={action.busy}>
                安排本章创作
              </button>
              <p className="muted">
                安排成功后继续读取实际章节状态，正文完成才成为草稿。
              </p>
            </form>
          )}
          {editing && (
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action
                  .run(
                    "life/chapters/revise",
                    {
                      id: current.id,
                      content: new FormData(event.currentTarget).get("content"),
                    },
                    editing.revision,
                  )
                  .then((result) => {
                    if (result) {
                      setEditing(null);
                      setRevision(null);
                      reload();
                    }
                  });
              }}
            >
              <label>
                章节正文
                <textarea
                  name="content"
                  defaultValue={editing.content}
                  required
                  maxLength={500000}
                  rows={18}
                />
              </label>
              <div className="domain-actions">
                <button className="button primary" disabled={action.busy}>
                  保存为新修订
                </button>
                <button
                  className="button"
                  type="button"
                  onClick={() => setEditing(null)}
                >
                  收起编辑
                </button>
              </div>
            </form>
          )}
          {!!current.current_revision && !active && (
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void action
                  .run(
                    "life/chapters/review",
                    {
                      id: current.id,
                      state: data.get("state"),
                      note: data.get("note"),
                    },
                    current.revision,
                  )
                  .then((result) => {
                    if (result) reload();
                  });
              }}
            >
              <h4>审阅当前修订</h4>
              <label>
                审阅结果
                <select
                  name="state"
                  defaultValue={current.review_state ?? "approved"}
                >
                  <option value="approved">这一版可发布</option>
                  <option value="changes_requested">还需修改</option>
                </select>
              </label>
              <label>
                审阅意见
                <textarea
                  name="note"
                  defaultValue={current.review_note ?? ""}
                  maxLength={20000}
                />
              </label>
              <button className="button" disabled={action.busy}>
                保存审阅结果
              </button>
            </form>
          )}
          {current.review_state === "approved" && !active && (
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action
                  .run(
                    "life/chapters/publish",
                    { id: current.id, ...audience },
                    current.revision,
                  )
                  .then((result) => {
                    if (result) reload();
                  });
              }}
            >
              <PublicationAudience value={audience} onChange={setAudience} />
              <button className="button primary" disabled={action.busy}>
                发布当前修订
              </button>
            </form>
          )}
          <CommandFeedback {...action} />
        </>
      )}
    </section>
  );
}
