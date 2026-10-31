import { DetailCard } from "../../components/DetailCard";
import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import { readFailure } from "../../app/integrationApi";
import { useDraftId, useLifeAction, useLifeResource } from "./useLifeRuntime";
import { ContentAcquire } from "./ContentAcquire";
import { ComfyBackendPanel } from "./ComfyBackendPanel";
import { ImageDimensions } from "./ImageDimensions";
import { compileComfy } from "./comfyApi";
import { ComfyCompilePreview } from "./ComfyCompilePreview";
import type { ComfyCompile } from "./comfyTypes";
import { ContentViewer } from "./ContentViewer";
import { ResourceFeedback, RuntimeFeedback } from "./RuntimeFeedback";
import { originalImage, showState, type LifeAccess } from "./runtimeApi";
import type { content_ref, outfits_record } from "./runtimeTypes";

export function PrivateImage({
  access,
  mediaId,
  caption,
}: {
  access: LifeAccess;
  mediaId: string;
  caption: string;
}) {
  const [url, setUrl] = useState(""),
    [error, setError] = useState(""),
    [open, setOpen] = useState(false),
    [retry, setRetry] = useState(0),
    [extension, setExtension] = useState("png");
  useEffect(() => {
    setUrl("");
    setError("");
    if (!open) return;
    const control = new AbortController();
    let objectUrl = "";
    void originalImage(access, mediaId, control.signal)
      .then((blob) => {
        if (control.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        setExtension(
          blob.type === "image/jpeg"
            ? "jpg"
            : blob.type === "image/webp"
              ? "webp"
              : "png",
        );
      })
      .catch((cause) => {
        if (!control.signal.aborted) setError(readFailure(cause));
      });
    return () => {
      control.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [access.actor, access.csrf, mediaId, open, retry]);
  return (
    <div className="life-private-image">
      {!open ? (
        <button className="button" onClick={() => setOpen(true)}>
          打开原图
        </button>
      ) : url ? (
        <>
          <a href={url} target="_blank" rel="noreferrer">
            <img src={url} alt={caption} />
          </a>
          <a className="button" href={url} download={`原图.${extension}`}>
            保存原图
          </a>
        </>
      ) : error ? (
        <>
          <p className="error-text" role="alert">
            {error}
          </p>
          <button className="button" onClick={() => setRetry((x) => x + 1)}>
            重试读取原图
          </button>
        </>
      ) : (
        <p role="status">正在取得经授权的原图…</p>
      )}
    </div>
  );
}

export function WardrobePanel({ access }: { access: LifeAccess }) {
  const outfits = useLifeResource(access, "outfits"),
    state = useLifeResource(access, "state");
  const action = useLifeAction(access, () => {
    outfits.refresh();
    state.refresh();
  });
  const draft = useDraftId("outfit");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<outfits_record | null>(null),
    [description, setDescription] = useState(""),
    [prompt, setPrompt] = useState("");
  const [reference, setReference] = useState<outfits_record["reference"]>(null);
  function clearDraft() {
    setEditing(null);
    setEditorOpen(false);
    setDescription("");
    setPrompt("");
    setReference(null);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const receipt = await action.run(
      "outfit.put",
      {
        id: editing?.id || draft.id,
        description,
        prompt,
        reference,
        activities: editing?.activities ?? [],
        ...(reference && typeof reference === "object"
          ? { source_scope: {}, query: {} }
          : {}),
      },
      editing?.version ?? 0,
    );
    if (receipt && receipt.result.state !== "unknown") {
      draft.reset();
      clearDraft();
    }
  }
  const current = state.items[0]?.outfit_ref;
  return (
    <>
      <section className="panel">
        <h3>衣柜与当前穿搭</h3>
        <ResourceFeedback {...outfits} empty={!outfits.items.length} />
        <div className="life-records">
          {outfits.items.map((row) => (
            <article className="life-record" key={row.id}>
              <header>
                <strong>{row.description}</strong>
                {current === row.id && (
                  <span className="status-pill">当前穿搭</span>
                )}
              </header>
              <p>{row.prompt}</p>
              <div className="life-action-row">
                <button
                  className="button"
                  onClick={() => {
                    setEditing(row);
                    setDescription(row.description);
                    setPrompt(row.prompt);
                    setReference(row.reference);
                  }}
                >
                  编辑服装
                </button>
                <button
                  className="button"
                  disabled={action.busy || current === row.id}
                  onClick={() =>
                    void action.run(
                      "outfit.select",
                      { id: row.id },
                      state.items[0]?.actor_version ?? null,
                    )
                  }
                >
                  设为当前穿搭
                </button>
              </div>
            </article>
          ))}
        </div>
        {outfits.cursor && (
          <button className="button" onClick={() => void outfits.more()}>
            继续读取衣柜
          </button>
        )}
        <DetailCard
          title={editing ? "编辑穿搭" : "新增穿搭"}
          open={editorOpen || !!editing}
          onOpenChange={(open) => {
            setEditorOpen(open);
            if (!open) clearDraft();
          }}
        >
          <h4>{editing ? "编辑服装" : "添加服装"}</h4>
          <ContentAcquire
            key={editing?.id ?? draft.id}
            access={access}
            imagesOnly
            onAcquired={(ref) => {
              if (ref.kind === "image") setReference({ ...ref, kind: "image" });
            }}
          />
          {reference && typeof reference === "object" && (
            <ContentViewer
              key={reference.object_id + reference.version}
              access={access}
              contentRef={reference}
            />
          )}
          {reference && (
            <button className="button" onClick={() => setReference(null)}>
              移除此服装参考
            </button>
          )}
          <RuntimeFeedback {...action} />
          <form className="life-form" onSubmit={save}>
            <label>
              服装描述
              <input
                required
                maxLength={1000}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label>
              外观细节
              <textarea
                required
                maxLength={4000}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={action.busy}>
              保存服装
            </button>
            {editing && (
              <button
                className="button"
                type="button"
                onClick={() => {
                  clearDraft();
                }}
              >
                取消编辑
              </button>
            )}
          </form>
        </DetailCard>
        <RuntimeFeedback {...action} />
      </section>
    </>
  );
}

export function ImageStudioPanel({ access }: { access: LifeAccess }) {
  const outfits = useLifeResource(access, "outfits");
  return (
    <>
      <IdentityReference access={access} />
      <ImageStudio access={access} outfits={outfits.items} />
    </>
  );
}

function IdentityReference({ access }: { access: LifeAccess }) {
  const records = useLifeResource(access, "image_reference"),
    action = useLifeAction(access, records.refresh);
  const row = records.items[0];
  const [ref, setRef] = useState<content_ref | null>(null);
  return (
    <section className="panel">
      <h3>角色形象参考</h3>
      <ResourceFeedback {...records} empty={!records.items.length} />
      <p>
        当前参考：
        {row?.content_ref
          ? `已配置 · 原件版本 ${row.content_ref.version}`
          : "尚未配置"}
      </p>
      <ContentAcquire access={access} imagesOnly onAcquired={setRef} />
      {(ref ?? row?.content_ref) && (
        <ContentViewer
          key={`${(ref ?? row?.content_ref)?.object_id}:${(ref ?? row?.content_ref)?.version}`}
          access={access}
          contentRef={(ref ?? row?.content_ref)!}
        />
      )}
      <div className="life-action-row">
        <button
          className="button primary"
          disabled={action.busy || !ref || !row}
          onClick={async () => {
            const answer = await action.run(
              "actor.image-reference.configure",
              { content_ref: ref, scope: {}, query: {} },
              row?.version ?? 0,
            );
            if (answer && answer.result.state !== "unknown") setRef(null);
          }}
        >
          设为角色形象参考
        </button>
        <button
          className="button"
          disabled={action.busy || !row?.content_ref}
          onClick={() =>
            void action.run(
              "actor.image-reference.configure",
              { content_ref: null, scope: null, query: null },
              row?.version ?? 0,
            )
          }
        >
          清除形象参考
        </button>
      </div>
      <RuntimeFeedback {...action} />
    </section>
  );
}

function ImageStudio({
  access,
  outfits,
}: {
  access: LifeAccess;
  outfits: outfits_record[];
}) {
  const album = useLifeResource(access, "album"),
    jobs = useLifeResource(access, "image_jobs"),
    backend = useLifeResource(access, "image_backend"),
    activities = useLifeResource(access, "activities");
  const action = useLifeAction(access, () => {
    jobs.refresh();
    album.refresh();
  });
  const draft = useDraftId("image");
  const albumDrafts = useRef(new Map<string, string>());
  const [scene, setScene] = useState(""),
    [outfit, setOutfit] = useState(""),
    [reference, setReference] = useState(""),
    [activity, setActivity] = useState("");
  const [width, setWidth] = useState(768),
    [height, setHeight] = useState(1024),
    [steps, setSteps] = useState(20),
    [negative, setNegative] = useState(""),
    [pose, setPose] = useState(""),
    [camera, setCamera] = useState(""),
    [positive, setPositive] = useState(""),
    [assist, setAssist] = useState(false);
  const [preview, setPreview] = useState<ComfyCompile | null>(null),
    [previewBusy, setPreviewBusy] = useState(false),
    [previewError, setPreviewError] = useState("");
  const previewRequest = useRef<AbortController | null>(null);
  useEffect(() => () => previewRequest.current?.abort(), []);
  useEffect(() => {
    setPreview(null);
    setPreviewError("");
    previewRequest.current?.abort();
    setPreviewBusy(false);
  }, [
    scene,
    outfit,
    activity,
    width,
    height,
    steps,
    negative,
    pose,
    camera,
    positive,
    assist,
  ]);
  const intent = {
    background: scene,
    pose,
    camera,
    positive,
    negative,
    ...(outfit
      ? { outfit: outfits.find((item) => item.id === outfit)?.prompt ?? "" }
      : {}),
  };
  useEffect(() => {
    if (!jobs.items.some((row) => ["queued", "running"].includes(row.state)))
      return;
    const timer = window.setInterval(() => {
      if (!document.hidden) jobs.refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, [jobs.items]);
  async function generate(event: React.FormEvent) {
    event.preventDefault();
    const source = album.items.find((x) => x.id === reference);
    const answer = await action.run("image.request", {
      id: draft.id,
      activity_id: activity || null,
      outfit_id: outfit || null,
      scene: scene || null,
      edit_source_id: null,
      scope: source?.scope ?? null,
      ...(source ? { edit_source_ref: source.content_ref, query: {} } : {}),
      parameters: { width, height, steps, negative },
      intent,
      assist_model: assist,
    });
    if (answer && answer.result.state !== "unknown") draft.reset();
  }
  return (
    <div className="life-runtime-grid">
      <section className="panel">
        <h3>图像创作</h3>
        <p className="muted">
          此次服装和参考原图用于本次创作，当前穿搭仍由衣柜单独选择。
        </p>
        <ResourceFeedback {...backend} empty={!backend.items.length} />
        <p>
          图像后端：{showState(backend.items[0]?.state ?? "not_configured")}
        </p>
        <DetailCard title={"生成图像设置"}>
          <RuntimeFeedback {...action} />
          <form className="life-form" onSubmit={generate}>
            <label>
              场景
              <textarea
                value={scene}
                onChange={(e) => setScene(e.target.value)}
                maxLength={2000}
              />
            </label>
            <label>
              关联日程或活动
              <select
                value={activity}
                onChange={(e) => setActivity(e.target.value)}
              >
                <option value="">自由拍照</option>
                {activities.items
                  .filter((item) => item.state !== "cancelled")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              此次服装
              <select
                value={outfit}
                onChange={(e) => setOutfit(e.target.value)}
              >
                <option value="">沿用当前穿搭</option>
                {outfits.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.description}
                  </option>
                ))}
              </select>
            </label>
            <label>
              形象参考或原图续作
              <select
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              >
                <option value="">新图创作</option>
                {album.items
                  .filter((row) => row.state === "available")
                  .map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.caption}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              姿态与动作
              <input
                value={pose}
                onChange={(e) => setPose(e.target.value)}
                maxLength={2000}
                placeholder="自然站立、坐在窗边阅读等"
              />
            </label>
            <label>
              镜头与构图
              <input
                value={camera}
                onChange={(e) => setCamera(e.target.value)}
                maxLength={2000}
                placeholder="全身、半身、特写等"
              />
            </label>
            <label>
              补充细节
              <input
                value={positive}
                onChange={(e) => setPositive(e.target.value)}
                maxLength={2000}
              />
            </label>
            <ImageDimensions
              width={width}
              height={height}
              onChange={(w, h) => {
                setWidth(w);
                setHeight(h);
              }}
            />
            <div className="life-form-row">
              <label>
                步数
                <input
                  type="number"
                  min={1}
                  max={150}
                  value={steps}
                  onChange={(e) => setSteps(Number(e.target.value))}
                />
              </label>
            </div>
            <label>
              排除细节
              <input
                value={negative}
                onChange={(e) => setNegative(e.target.value)}
                maxLength={4000}
              />
            </label>
            <label className="life-check">
              <input
                type="checkbox"
                checked={assist}
                onChange={(e) => setAssist(e.target.checked)}
              />
              使用角色模型转译拍摄提示词
            </label>
            <button
              type="button"
              className="button"
              disabled={previewBusy}
              onClick={() => {
                const control = new AbortController();
                previewRequest.current?.abort();
                previewRequest.current = control;
                setPreviewBusy(true);
                setPreviewError("");
                void compileComfy(
                  access,
                  intent,
                  { width, height, steps },
                  assist,
                  control.signal,
                )
                  .then((result) => {
                    if (!control.signal.aborted) setPreview(result);
                  })
                  .catch((cause) => {
                    if (!control.signal.aborted)
                      setPreviewError(readFailure(cause));
                  })
                  .finally(() => {
                    if (!control.signal.aborted) setPreviewBusy(false);
                  });
              }}
            >
              {previewBusy ? "正在编译…" : "预览本次提示词与节点"}
            </button>
            <button className="button primary" disabled={action.busy}>
              提交{reference ? "原图续作" : "创作"}
            </button>
          </form>
        </DetailCard>
        {previewError && (
          <p role="alert" className="error-text">
            {previewError}
          </p>
        )}
        {preview && <ComfyCompilePreview result={preview} />}
        <RuntimeFeedback {...action} />
      </section>
      <section className="panel">
        <h3>原任务</h3>
        <button className="button" onClick={jobs.refresh}>
          查询原任务
        </button>
        <ResourceFeedback {...jobs} empty={!jobs.items.length} />
        {jobs.items.map((job) => (
          <article key={job.id} className="life-record">
            <header>
              <strong>
                创作 ·{" "}
                {job.created_at
                  ? new Date(job.created_at * 1000).toLocaleString("zh-CN")
                  : "时间未记录"}
              </strong>
              <span className="status-pill">{showState(job.state)}</span>
            </header>
            {job.error_code && <p className="error-text">{job.error_code}</p>}
            {["queued", "running", "unknown"].includes(job.state) && (
              <button
                className="button"
                disabled={action.busy}
                onClick={() =>
                  void action.run("image.cancel", { id: job.id }, job.version)
                }
              >
                取消原任务
              </button>
            )}
            {job.artifacts.map((item, index) => (
              <div key={item.media_id}>
                <PrivateImage
                  access={access}
                  mediaId={item.media_id}
                  caption="本次创作原图"
                />
                <button
                  className="button"
                  disabled={
                    action.busy ||
                    album.items.some(
                      (row) =>
                        row.media_id === item.media_id &&
                        row.state === "available",
                    )
                  }
                  onClick={() => {
                    const key = `${job.id}:${index}`;
                    if (!albumDrafts.current.has(key))
                      albumDrafts.current.set(key, `album:${requestId()}`);
                    void action.run(
                      "album.attach",
                      {
                        id: albumDrafts.current.get(key),
                        job_id: job.id,
                        artifact_index: index,
                        activity_id: null,
                        caption: "创作原图",
                        scope: null,
                      },
                      0,
                    );
                  }}
                >
                  收进相册
                </button>
              </div>
            ))}
          </article>
        ))}
      </section>
      <section className="panel">
        <h3>持久相册</h3>
        <ResourceFeedback {...album} empty={!album.items.length} />
        {album.items.map((row) => (
          <article className="life-record" key={row.id}>
            <h4>{row.caption}</h4>
            <p>
              {showState(row.state)} · 版本 {row.version}
            </p>
            {row.state === "available" && (
              <PrivateImage
                access={access}
                mediaId={row.media_id}
                caption={row.caption}
              />
            )}
            <div className="life-action-row">
              <button
                className="button"
                disabled={row.state !== "available"}
                onClick={() => setReference(row.id)}
              >
                用于原图续作
              </button>
              <button
                className="button"
                disabled={action.busy}
                onClick={() =>
                  void action.run(
                    "album.remove",
                    { id: row.id, reason: "用户移除相册记录" },
                    row.version,
                  )
                }
              >
                移出相册
              </button>
            </div>
          </article>
        ))}
        {album.cursor && (
          <button className="button" onClick={() => void album.more()}>
            继续读取相册
          </button>
        )}
      </section>
      <ComfyBackendPanel access={access} refreshed={backend.refresh} />
    </div>
  );
}
