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
import { lifeCommandError, timeLabel, type Routine } from "./domainTypes";
import { localDay } from "./scheduleTime";

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
  automatic?: {
    state: string;
    day: string;
    timezone: string;
    material_revision: number;
    reviewed_revision: number;
    attempt_id: string | null;
    closed_at: string;
    last_reviewed_at: string | null;
    last_error: string | null;
  } | null;
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
function diaryReason(reason: string) {
  const labels: Record<string, string> = {
    diary_no_new_material: "没有新的有效经历，本次没有调用模型重写。",
    source_unavailable: "部分原始依据已失效，需要以当前有效材料重新整理。",
    result_source_unavailable: "原结果的依据已失效，没有写入这份日记。",
    diary_episode_revised: "写作期间原经历已修订，旧正文没有覆盖新材料。",
    generation_request_expired: "原写作请求已到截止时间，本次没有再次提交。",
    diary_original_authorization_changed:
      "原写作的授权版本已变化，保留原请求身份核对。",
    generation_outcome_unknown:
      "原模型任务的结果尚未确认，核对时会沿用原请求。",
    model_completed_after_cancel: "原模型稍后完成，但已按取消要求停止写入。",
    cancelled_before_model_registration: "在模型开始之前已停止本次写作。",
    cancel_requested: "已请求停止后续写入，正在核对原模型任务。",
    diary_generation_failed: "这次没有取得可用正文，可以查看原任务。",
    generation_content_incomplete: "原正文不完整，没有保存为日记。",
  };
  return labels[reason] ?? "这次整理尚未完成，可核对原任务和当前材料。";
}
export function DiaryPanel({ canManage }: { canManage: boolean }) {
  const routine = useDomainResource<Routine>("life/routine", {}, canManage);
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
          <p className="muted">
            角色会在日末按当地日期整理实际经历。有新材料才修订，内容少时可以短写或留白，生成后先保存为草稿。
          </p>
        )}
        <button
          className="button"
          onClick={() => void list.refresh()}
          disabled={list.loading}
        >
          刷新日记状态
        </button>
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
                key={routine.data?.timezone ?? "Asia/Shanghai"}
                name="day"
                type="date"
                defaultValue={localDay(
                  routine.data?.timezone ?? "Asia/Shanghai",
                )}
                required
              />
            </label>
            <button className="button primary" disabled={request.busy}>
              依据实际经历整理日记
            </button>
            <p className="muted">
              只使用有权读取的实际经历，生成后先留作草稿。
            </p>
            <CommandFeedback
              {...request}
              error={lifeCommandError(request.error)}
            />
          </form>
        )}
        {list.error && <p role="alert">{list.error}</p>}
      </section>
      <BookShelf
        entries={(list.data?.items ?? []).map((item) => ({
          id: item.id,
          title: item.day,
          badge:
            item.automatic?.state === "pending"
              ? "有新经历待整理"
              : item.automatic?.state === "blocked"
                ? "自动整理待处理"
                : (stateLabels[item.state] ?? "状态待核对"),
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
  const current =
    read.data?.diary && read.data.diary.revision >= row.revision
      ? read.data.diary
      : row;
  const reload = () => {
    void read.refresh();
    void refreshList();
  };
  return (
    <section className="domain-workspace">
      <p>
        <span className="badge">
          {stateLabels[current.state] ?? "状态待核对"}
        </span>
        {current.reason && <> · {diaryReason(current.reason)}</>}
      </p>
      {canManage && current.automatic && (
        <section className="life-diary-automatic" aria-label="自动日记状态">
          <h4>这一天的自动整理</h4>
          <p>
            {current.automatic.state === "reviewed"
              ? "已经复核本批材料，之后有新经历再继续。"
              : current.automatic.state === "pending"
                ? "有新经历待整理，已排入后台。"
                : current.automatic.state === "blocked"
                  ? "自动整理暂时停在依据或授权检查处。"
                  : (stateLabels[current.automatic.state] ??
                    "正在核对这一批材料。")}
          </p>
          <p className="muted">
            材料版本 {current.automatic.material_revision} · 已复核到{" "}
            {current.automatic.reviewed_revision} · {current.automatic.timezone}
          </p>
          {current.automatic.last_reviewed_at && (
            <p className="muted">
              上次复核：
              {timeLabel(
                current.automatic.last_reviewed_at,
                current.automatic.timezone,
              )}
            </p>
          )}
          {current.automatic.last_error && (
            <p>{diaryReason(current.automatic.last_error)}</p>
          )}
        </section>
      )}
      {current.state === "unknown" && (
        <p role="status">保留原模型请求，只核对原结果；尚未作为写作成功。</p>
      )}
      {!read.data && !read.error && !current.current_revision && (
        <p className="muted">
          这一页还没有可阅读的正文，整理状态会继续显示在这里。
        </p>
      )}
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
                {read.data.version.recipe.coverage.missing.length}{" "}
                项原件范围未参与本版写作，可展开依据查看。
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
          <CommandFeedback {...action} error={lifeCommandError(action.error)} />
        </>
      )}
    </section>
  );
}
