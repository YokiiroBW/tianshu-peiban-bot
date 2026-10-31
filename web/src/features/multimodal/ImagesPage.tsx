import { useEffect, useRef, useState } from "react";
import { apiRead, commandTarget } from "../../api/client";
import {
  actorScope,
  useDomainCommand,
  useDomainResource,
  useDomainTarget,
} from "../../api/useDomainResource";
import { taskHref, type ActionView } from "../../api/commandRecovery";
import { useAuth } from "../../app/Auth";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList, MediaPreview } from "../../components/MediaPreview";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import type { Page } from "../../api/contracts";
import { EngineEditor, ProfileEditor, engineNames } from "./ImageSettings";
import { WorkflowEditor } from "./WorkflowEditor";
import type { ImageView, ImagePreview, SceneSnapshot } from "./types";
import "./multimodal.css";
const executionNames: Record<string, string> = {
  pending: "等待生成",
  running: "正在生成",
  succeeded: "生成完成",
  failed: "生成失败",
  cancelled: "已取消",
  unknown: "结果未知",
};
export default function ImagesPage({
  settings = false,
}: {
  settings?: boolean;
}) {
  const scope = useDomainTarget();
  return (
    <ImageWorkspace
      key={JSON.stringify({ ...scope, settings })}
      settings={settings}
    />
  );
}
function ImageWorkspace({ settings }: { settings: boolean }) {
  const { session } = useAuth();
  const scope = useDomainTarget();
  const actor = scope.actor_id;
  const view = useDomainResource<ImageView>(
    "images/view",
    settings ? actorScope(actor) : scope,
    true,
    5000,
    (value) =>
      value.jobs.some((job) => ["pending", "running"].includes(job.execution)),
  );
  const command = useDomainCommand("image-generation");
  const [engineId, setEngineId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [purpose, setPurpose] = useState("selfie");
  const [delivery, setDelivery] = useState(false);
  const [outfit, setOutfit] = useState("");
  const [outfitCursor, setOutfitCursor] = useState<string | null>(null);
  const outfits = useDomainResource<Page<{ id: string; description: string }>>(
    "life/outfits",
    { cursor: outfitCursor, limit: 40 },
    !!actor && !settings,
  );
  const [preview, setPreview] = useState<ImagePreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  const profile =
    view.data?.profiles.find((value) => value.id === actor) ??
    (view.data?.profiles.length === 1 ? view.data.profiles[0] : undefined);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (view.accessLost) setPreview(null);
  }, [view.accessLost]);
  useEffect(() => {
    setEngineId((current) =>
      view.data?.engines.some((engine) => engine.id === current)
        ? current
        : (profile?.engine_id ?? view.data?.engines[0]?.id ?? ""),
    );
  }, [view.data?.engines, profile?.engine_id]);
  function intent() {
    return {
      ...scope,
      engine_id: engineId,
      ...(profile ? { profile_id: profile.id } : {}),
      purpose,
      prompt,
      delivery_kind: delivery ? "requested_delivery" : "create_only",
      target_ref: delivery ? scope.conversation_id : null,
      ...(outfit ? { outfit_id: outfit } : {}),
      ...(preview && typeof preview.scene.revision === "string"
        ? { scene_revision: preview.scene.revision }
        : {}),
    };
  }
  async function prepare() {
    if (!session || !actor) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await apiRead<ImagePreview>(
        "images/preview",
        intent(),
        session.csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setPreview(result);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : "预览未取得。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className="domain-workspace">
      <header className="domain-toolbar">
        <div>
          <p className="eyebrow">{settings ? "图像引擎" : "生图与相册"}</p>
          <h2>{settings ? "连接创作工具" : "此刻的她，与真实完成的作品"}</h2>
        </div>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          读取状态
        </button>
      </header>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          {settings ? (
            <section className="panel">
              <h3>原生图像引擎</h3>
              <div className="domain-grid">
                {view.data.engines.map((engine) => (
                  <article
                    className="domain-record"
                    key={engine.id + engine.revision}
                  >
                    <h4>
                      {engineNames[engine.engine]} · {engine.id}
                    </h4>
                    <p>
                      {engine.enabled ? "启用" : "停用"} ·{" "}
                      {engine.configured ? engine.state : "未配置"} ·{" "}
                      {engine.model}
                    </p>
                    {engine.reason && <p>{engine.reason}</p>}
                    <DetailCard title="编辑引擎">
                      <EngineEditor engine={engine} refresh={view.refresh} />
                    </DetailCard>
                  </article>
                ))}
              </div>
              <DetailCard title="添加引擎">
                <EngineEditor refresh={view.refresh} />
              </DetailCard>
            </section>
          ) : (
            <>
              <section className="panel">
                <h3>拍一张，或继续创作</h3>
                {!actor ? (
                  <p>先选择一位角色。</p>
                ) : (
                  <form
                    className="domain-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void command
                        .run("images/generate", intent(), null)
                        .then(() => void view.refresh());
                    }}
                  >
                    <div className="domain-grid">
                      <label>
                        创作方式
                        <select
                          value={purpose}
                          onChange={(event) => {
                            setPurpose(event.target.value);
                            setPreview(null);
                          }}
                        >
                          <option value="selfie">结合当前生活自拍</option>
                          <option value="creation">明确主题创作</option>
                        </select>
                      </label>
                      <label>
                        原生引擎
                        <select
                          value={engineId}
                          onChange={(event) => {
                            setEngineId(event.target.value);
                            setPreview(null);
                          }}
                          required
                        >
                          <option value="">选择引擎</option>
                          {view.data.engines.map((engine) => (
                            <option
                              key={engine.id}
                              value={engine.id}
                              disabled={!engine.enabled || !engine.configured}
                            >
                              {engineNames[engine.engine]} · {engine.model} ·{" "}
                              {engine.state}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        本次穿搭
                        <select
                          value={outfit}
                          onChange={(event) => {
                            setOutfit(event.target.value);
                            setPreview(null);
                          }}
                        >
                          <option value="">使用当前穿搭</option>
                          {outfit &&
                            !outfits.data?.items.some(
                              (value) => value.id === outfit,
                            ) && (
                              <option value={outfit}>已选择的衣柜条目</option>
                            )}
                          {outfits.data?.items.map((value) => (
                            <option key={value.id} value={value.id}>
                              {value.description}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    {outfits.error && <p role="alert">{outfits.error}</p>}
                    {outfits.data?.next_cursor && (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() =>
                          setOutfitCursor(outfits.data!.next_cursor)
                        }
                      >
                        下一页穿搭
                      </button>
                    )}
                    {outfitCursor && (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => setOutfitCursor(null)}
                      >
                        首批穿搭
                      </button>
                    )}
                    <label>
                      本次画面要求
                      <textarea
                        value={prompt}
                        onChange={(event) => {
                          setPrompt(event.target.value);
                          setPreview(null);
                        }}
                        required={purpose === "creation"}
                        placeholder="当前小屋、穿搭与活动会进入真实场景快照。"
                      />
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={delivery}
                        disabled={!scope.conversation_id}
                        onChange={(event) => {
                          setDelivery(event.target.checked);
                          setPreview(null);
                        }}
                      />
                      生成后发送到当前已选择会话
                    </label>
                    {!scope.conversation_id && (
                      <p className="muted">
                        当前只创建作品。先选择真实会话后可明确要求发送。
                      </p>
                    )}
                    <p className="muted">
                      NovelAI仅接受实际用户触发。生成、外部效果和投递结果分别读取，不自动切换付费路线。
                    </p>
                    <div className="domain-actions">
                      <button
                        type="button"
                        className="button"
                        disabled={busy || !engineId}
                        onClick={() => void prepare()}
                      >
                        预览本次场景与编译提示
                      </button>
                      <button
                        className="button primary"
                        disabled={command.busy || !engineId || !profile}
                      >
                        生成新作品
                      </button>
                    </div>
                    {!profile && <p>请先保存角色形象与引擎绑定。</p>}
                  </form>
                )}
                {error && <p role="alert">{error}</p>}
                {preview && <ImageCompilation preview={preview} />}
                <CommandFeedback {...command} />
              </section>
              <section className="panel">
                <h3>创作进度</h3>
                {!view.data.jobs.length && <p>当前没有可读创作任务。</p>}
                <div className="domain-grid">
                  {view.data.jobs.map((job) => (
                    <ImageJob
                      key={job.action_id}
                      job={job}
                      refresh={view.refresh}
                    />
                  ))}
                </div>
              </section>
              <section className="panel">
                <h3>相册</h3>
                <p>展示实际产物；查看已有照片不会重新生成。</p>
                <ArtifactList artifacts={view.data.album} />
                {!view.data.album.length && <p>当前范围尚无可读作品。</p>}
              </section>
            </>
          )}
          {actor && (
            <section className="panel">
              <h3>角色形象与默认引擎</h3>
              <ProfileEditor
                key={actor + ":" + (profile?.revision ?? 0)}
                actor={actor}
                profile={profile}
                view={view.data}
                refresh={view.refresh}
              />
            </section>
          )}
          <DetailCard title="ComfyUI工作流与语义绑定">
            <DetailCard title="导入新的API工作流">
              <WorkflowEditor refresh={view.refresh} />
            </DetailCard>
            {view.data.workflows.map((workflow) => (
              <DetailCard
                key={workflow.id + workflow.revision}
                title={`${workflow.name} · 修订${workflow.revision}`}
              >
                <WorkflowEditor workflow={workflow} refresh={view.refresh} />
              </DetailCard>
            ))}
          </DetailCard>
        </>
      )}
    </div>
  );
}
function ImageJob({
  job,
  refresh,
}: {
  job: ImageView["jobs"][number];
  refresh: () => Promise<void>;
}) {
  const { session } = useAuth();
  const scope = useDomainTarget();
  const command = useDomainCommand("image-action:" + job.action_id);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  async function control(operation: string) {
    if (!session) return;
    setReading(true);
    setError("");
    try {
      const original = await apiRead<ActionView>(
        "capabilities/get",
        { ...scope, action_id: job.action_id },
        session.csrf,
        new AbortController().signal,
      );
      if (typeof original.action.revision !== "number")
        throw new Error("原动作修订不可用。");
      await command.run(
        operation,
        { ...scope, action_id: job.action_id },
        original.action.revision,
      );
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "原账本暂不可读。");
    } finally {
      setReading(false);
    }
  }
  return (
    <article className="domain-record">
      <header>
        <strong>{executionNames[job.execution] ?? job.execution}</strong>
        <a
          className="text-link"
          href={taskHref({
            action_id: job.action_id,
            attempt_id: job.attempt_id,
            client_request_id: "",
            target: commandTarget(scope),
          })}
        >
          原任务与投递详情
        </a>
      </header>
      {job.reason && <p>{job.reason}</p>}
      <p>外部效果：{job.effect}</p>
      <ArtifactList artifacts={job.artifacts} />
      <div className="domain-actions">
        {["pending", "running"].includes(job.execution) && (
          <button
            className="button"
            disabled={command.busy || reading}
            onClick={() => void control("capabilities/cancel")}
          >
            请求取消后续生成
          </button>
        )}
        {job.execution === "unknown" && (
          <button
            className="button"
            disabled={command.busy || reading}
            onClick={() => void control("capabilities/reconcile")}
          >
            核对原生成
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      <CommandFeedback {...command} />
    </article>
  );
}
function Scene({ scene }: { scene: SceneSnapshot }) {
  const labels: Record<string, string> = {
    captured_at: "快照时间",
    timezone: "时区",
    local_time: "角色当地时间",
    activity: "当前活动",
    place: "场景",
    weather: "天气",
    outfit: "穿搭",
    light: "光照",
  };
  return (
    <dl className="scene-summary">
      {Object.entries(scene)
        .filter(([key]) => key in labels)
        .map(([key, value]) => (
          <div key={key}>
            <dt>{labels[key]}</dt>
            <dd>
              {value == null
                ? "未知"
                : typeof value === "object"
                  ? JSON.stringify(value)
                  : String(value)}
            </dd>
          </div>
        ))}
    </dl>
  );
}
function ImageCompilation({ preview }: { preview: ImagePreview }) {
  return (
    <div className="domain-record">
      <h4>本次场景快照</h4>
      <Scene scene={preview.scene} />
      {preview.warnings.map((warning) => (
        <p key={warning}>{warning}</p>
      ))}
      <SourceEvidence sources={preview.source_refs ?? []} />
      <DetailCard title="实际编译提示与参数">
        <p>{preview.compiled.prompt}</p>
        {preview.compiled.negative_prompt && (
          <p>排除内容：{preview.compiled.negative_prompt}</p>
        )}
        <pre>{JSON.stringify(preview.compiled.parameters, null, 2)}</pre>
        <div className="domain-grid">
          {preview.compiled.references.map((media, index) => (
            <MediaPreview key={media.media_id + index} media={media} />
          ))}
        </div>
      </DetailCard>
    </div>
  );
}
