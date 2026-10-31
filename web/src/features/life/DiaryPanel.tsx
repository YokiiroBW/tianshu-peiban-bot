import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Coverage, Page, SourceRef } from "../../api/contracts";
import { BookShelf } from "../../components/BookShelf";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import {
  PublicationAudience,
  type PublicationSelection,
} from "./PublicationAudience";
import { timeLabel } from "./domainTypes";

type Diary = {
  id: string;
  revision: number;
  day: string;
  state: string;
  current_revision?: number;
  published_revision: number | null;
  generation_id?: string | null;
  reason?: string | null;
  source_refs: SourceRef[];
};
type DiaryRead = {
  diary: Diary;
  version: {
    revision: number;
    content: string;
    created_at: string;
    source_refs?: SourceRef[];
    recipe?: { source_refs: SourceRef[]; coverage?: Coverage };
  };
};
const stateLabels: Record<string, string> = {
  collecting: "正在整理经历",
  generating: "正在写日记",
  cancelling: "正在停止后续写入",
  unknown: "原模型结果待确认",
  draft: "草稿",
  published: "已发布",
  failed: "本次生成未完成",
  empty: "没有新增材料",
  cancelled: "已停止本次生成",
};
export function DiaryPanel({ canManage }: { canManage: boolean }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const list = useDomainResource<Page<Diary>>(
    "life/diaries",
    { cursor, limit: 30 },
    true,
    15000,
  );
  const request = useDomainCommand<{ id: string }>("diary-request");
  const [selected, setSelected] = useState<Diary | null>(null);
  useEffect(() => {
    if (list.accessLost) setSelected(null);
  }, [list.accessLost]);
  const row =
    list.data?.items.find((item) => item.id === selected?.id) ?? selected;
  return (
    <>
      <section className="panel">
        {canManage && (
          <form
            className="domain-form"
            onSubmit={(event) => {
              event.preventDefault();
              const day = String(new FormData(event.currentTarget).get("day"));
              const previous = list.data?.items.find(
                (item) => item.day === day,
              );
              void request
                .run(
                  "life/diaries/request",
                  { day },
                  previous?.revision ?? null,
                )
                .then((result) => {
                  if (result) {
                    void list.refresh();
                  }
                });
            }}
          >
            <label>
              按角色时区记下哪一天
              <input
                name="day"
                type="date"
                defaultValue={new Date().toLocaleDateString("sv-SE")}
                required
              />
            </label>
            <button className="button primary" disabled={request.busy}>
              依据实际经历整理日记
            </button>
            <p className="muted">
              只使用有权读取的实际经历，生成后先留作草稿。
            </p>
            <CommandFeedback {...request} />
          </form>
        )}
        {list.error && <p role="alert">{list.error}</p>}
      </section>
      <BookShelf
        entries={(list.data?.items ?? []).map((item) => ({
          id: item.id,
          title: item.day,
          badge: stateLabels[item.state] ?? item.state,
          category: item.day.slice(0, 7).replace("-", " 年 ") + " 月",
          selected: selected?.id === item.id,
        }))}
        empty={canManage ? "还没有写下的日记" : "还没有可阅读的已发布日记"}
        onOpen={(id) =>
          setSelected(list.data!.items.find((item) => item.id === id)!)
        }
      />
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批日记
          </button>
        )}
        {list.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(list.data!.next_cursor)}
          >
            继续翻日记
          </button>
        )}
      </div>
      <Drawer
        title={row?.day ?? "日记"}
        open={!!row}
        onClose={() => setSelected(null)}
        variant="card"
      >
        {row && (
          <DiarySheet
            key={row.id}
            row={row}
            canManage={canManage}
            refreshList={list.refresh}
          />
        )}
      </Drawer>
    </>
  );
}

function DiarySheet({
  row,
  canManage,
  refreshList,
}: {
  row: Diary;
  canManage: boolean;
  refreshList: () => Promise<void>;
}) {
  const [revision, setRevision] = useState<number | null>(null);
  const read = useDomainResource<DiaryRead>(
    "life/diaries/read",
    { id: row.id, ...(revision ? { revision } : {}) },
    !!row.current_revision || !canManage || !!row.published_revision,
    15000,
  );
  const action = useDomainCommand(row.id);
  const [editing, setEditing] = useState<DiaryRead | null>(null);
  const [audience, setAudience] = useState<PublicationSelection>({
    reader_ids: [],
    conversation_ids: [],
  });
  useEffect(() => {
    if (read.accessLost) setEditing(null);
  }, [read.accessLost]);
  const current = read.data?.diary ?? row;
  const reload = () => {
    void read.refresh();
    void refreshList();
  };
  return (
    <section className="domain-workspace">
      <p>
        <span className="badge">
          {stateLabels[current.state] ?? current.state}
        </span>
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
              第 {read.data.version.revision} 版 ·{" "}
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
          {read.data.version.recipe?.coverage &&
            !read.data.version.recipe.coverage.complete && (
              <p className="muted">
                本版材料有明确省略：
                {read.data.version.recipe.coverage.missing.join("、")}
              </p>
            )}
        </>
      )}
      {canManage && (
        <>
          <div className="domain-actions">
            {read.data && (
              <button
                className="button"
                onClick={() => setEditing(structuredClone(read.data!))}
              >
                编辑这一版
              </button>
            )}
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
            {!["collecting", "generating", "cancelling", "unknown"].includes(
              current.state,
            ) && (
              <button
                className="button"
                disabled={action.busy}
                onClick={() =>
                  void action
                    .run(
                      "life/diaries/request",
                      { day: current.day },
                      current.revision,
                    )
                    .then((result) => {
                      if (result) reload();
                    })
                }
              >
                依据新增经历更新
              </button>
            )}
            {["collecting", "generating", "cancelling", "unknown"].includes(
              current.state,
            ) && (
              <>
                <button
                  className="button"
                  disabled={action.busy}
                  onClick={() =>
                    void action
                      .run(
                        "life/diaries/cancel",
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
                        "life/diaries/reconcile",
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
          {editing && (
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                const content = String(
                  new FormData(event.currentTarget).get("content"),
                );
                void action
                  .run(
                    "life/diaries/revise",
                    { id: editing.diary.id, content },
                    editing.diary.revision,
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
                编辑正文
                <textarea
                  name="content"
                  defaultValue={editing.version.content}
                  required
                  maxLength={100000}
                  rows={16}
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
          {!!current.current_revision &&
            !["collecting", "generating", "cancelling", "unknown"].includes(
              current.state,
            ) && (
              <form
                className="domain-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void action
                    .run(
                      "life/diaries/publish",
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
