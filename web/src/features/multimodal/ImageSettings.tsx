import { useState } from "react";
import { useAuth } from "../../app/Auth";
import { actorScope, useDomainCommand } from "../../api/useDomainResource";
import { uploadMedia } from "../../api/uploads";
import type { MediaRef } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { MediaPreview } from "../../components/MediaPreview";
import type { ImageEngine, ImageProfile, ImageView } from "./types";
export const engineNames = {
  comfyui: "ComfyUI",
  nai: "NovelAI",
  gpt_image: "GPT Image",
};
export function EngineEditor({
  engine,
  refresh,
}: {
  engine?: ImageEngine;
  refresh: () => Promise<void>;
}) {
  const [kind, setKind] = useState(engine?.engine ?? "comfyui");
  const [error, setError] = useState("");
  const command = useDomainCommand(
    "image-engine:" + (engine?.id ?? "new"),
    "global",
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        const form = event.currentTarget;
        const data = new FormData(form);
        let parameters: Record<string, unknown>;
        try {
          parameters = JSON.parse(String(data.get("parameters")));
          if (
            !parameters ||
            typeof parameters !== "object" ||
            Array.isArray(parameters)
          )
            throw new Error();
        } catch {
          setError("引擎参数必须是JSON对象。");
          return;
        }
        void command
          .run(
            "images/engines.save",
            {
              id: data.get("id"),
              engine: kind,
              endpoint: String(data.get("endpoint")) || null,
              model: data.get("model"),
              account_id: data.get("account"),
              quota_group: data.get("quota"),
              reference_mode: String(data.get("reference_mode")) || null,
              parameters,
              enabled: data.get("enabled") === "on",
              ...(data.get("key") ? { api_key: data.get("key") } : {}),
            },
            engine?.revision ?? 0,
          )
          .then((answer) => {
            if (answer) {
              const secret =
                form.querySelector<HTMLInputElement>('[name="key"]');
              if (secret) secret.value = "";
              void refresh();
            }
          });
      }}
    >
      <div className="domain-grid">
        <label>
          连接标识
          <input
            name="id"
            defaultValue={engine?.id}
            readOnly={!!engine}
            required
          />
        </label>
        <label>
          引擎
          <select
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as ImageEngine["engine"])
            }
          >
            {Object.entries(engineNames).map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label>
          原生服务地址
          <input
            type="url"
            name="endpoint"
            defaultValue={engine?.endpoint ?? ""}
            required={kind === "comfyui"}
            placeholder={
              kind === "comfyui" ? "http://主机:8188" : "留空使用官方服务"
            }
          />
        </label>
        <label>
          模型 / ComfyUI资源名称
          <input name="model" defaultValue={engine?.model} required />
        </label>
        <label>
          供应商账号标识
          <input name="account" defaultValue={engine?.account_id} required />
        </label>
        <label>
          共享GPU或额度组
          <input name="quota" defaultValue={engine?.quota_group} required />
        </label>
        <label>
          API Key（留空保留）
          <input name="key" type="password" autoComplete="new-password" />
        </label>
        {kind === "nai" && (
          <label>
            参考方式
            <select
              name="reference_mode"
              defaultValue={engine?.reference_mode ?? ""}
            >
              <option value="">无参考图</option>
              <option value="precise">精确角色参考</option>
              <option value="vibe">氛围迁移</option>
              <option value="img2img">图生图</option>
            </select>
          </label>
        )}
      </div>
      <label>
        原生引擎参数
        <textarea
          name="parameters"
          rows={5}
          defaultValue={JSON.stringify(engine?.parameters ?? {}, null, 2)}
        />
      </label>
      <p className="muted">
        参数按所选原生引擎验证。共享资源组连接真实Broker额度；缺少实际用量时保持未知。
      </p>
      <label>
        <input
          name="enabled"
          type="checkbox"
          defaultChecked={engine?.enabled ?? true}
        />
        启用
      </label>
      {error && <p role="alert">{error}</p>}
      <button className="button primary" disabled={command.busy}>
        保存引擎
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
export function ProfileEditor({
  actor,
  profile,
  view,
  refresh,
}: {
  actor: string;
  profile?: ImageProfile;
  view: ImageView;
  refresh: () => Promise<void>;
}) {
  const { session } = useAuth();
  const scope = actorScope(actor);
  const command = useDomainCommand("image-profile:" + actor, "actor");
  const [references, setReferences] = useState<MediaRef[]>(
    profile?.references ?? [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="domain-form">
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void command
            .run(
              "images/profiles.save",
              {
                ...scope,
                id: profile?.id ?? actor,
                engine_id: data.get("engine"),
                workflow_id: String(data.get("workflow")) || null,
                character: data.get("character"),
                style: data.get("style"),
                references,
                outfit_defaults: { description: data.get("outfit") },
              },
              profile?.revision ?? 0,
            )
            .then((answer) => {
              if (answer) void refresh();
            });
        }}
      >
        <label>
          固定角色外观
          <textarea
            name="character"
            defaultValue={profile?.character}
            required
          />
        </label>
        <div className="domain-grid">
          <label>
            默认引擎
            <select name="engine" defaultValue={profile?.engine_id} required>
              <option value="">请选择</option>
              {view.engines.map((engine) => (
                <option key={engine.id} value={engine.id}>
                  {engineNames[engine.engine]} · {engine.model || "默认模型"}
                </option>
              ))}
            </select>
          </label>
          <label>
            ComfyUI工作流
            <select name="workflow" defaultValue={profile?.workflow_id ?? ""}>
              <option value="">原生引擎无需工作流</option>
              {view.workflows.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          固定画面风格
          <textarea name="style" defaultValue={profile?.style ?? ""} />
        </label>
        <label>
          无当前穿搭时使用的服装描述
          <textarea
            name="outfit"
            defaultValue={String(profile?.outfit_defaults.description ?? "")}
          />
        </label>
        <div className="domain-grid">
          {references.map((media, index) => (
            <div key={media.media_id + index}>
              <MediaPreview media={media} scope={scope} />
              <button
                className="text-link"
                type="button"
                onClick={() =>
                  setReferences((current) =>
                    current.filter((_, position) => position !== index),
                  )
                }
              >
                移除此参考
              </button>
            </div>
          ))}
        </div>
        <button className="button primary" disabled={command.busy || busy}>
          保存角色形象
        </button>
        <CommandFeedback {...command} />
      </form>
      <label>
        添加参考图
        <input
          type="file"
          accept="image/*"
          multiple
          disabled={busy}
          onChange={(event) => {
            const files = [...(event.target.files ?? [])];
            if (!session || !files.length) return;
            setBusy(true);
            setError("");
            void (async () => {
              for (const file of files) {
                const uploaded = await uploadMedia(
                  file,
                  scope,
                  session.csrf,
                  new AbortController().signal,
                );
                setReferences((current) => [
                  ...current,
                  uploaded.artifact.media,
                ]);
              }
            })()
              .catch((cause) =>
                setError(
                  cause instanceof Error ? cause.message : "参考上传未确认。",
                ),
              )
              .finally(() => setBusy(false));
          }}
        />
      </label>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
