import { useState } from "react";
import {
  actorScope,
  useDomainResource,
  useDomainCommand,
} from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList } from "../../components/MediaPreview";
import { DetailCard } from "../../components/DetailCard";
import {
  SpeechTranscription,
  VoiceBrowser,
  SpeechOAuthComplete,
} from "./SpeechTools";
import type {
  FishConnection,
  SpeechView,
  VoiceProfile,
  SpeechArtifact,
  PronunciationDictionary,
} from "./types";
import "./multimodal.css";
import { CapabilityReadiness } from "./CapabilityReadiness";

const capabilityNames: Record<string, string> = {
  tts: "语音合成",
  asr: "文件转写",
  stream: "增量语音",
  timestamps: "时间对齐",
  voices: "音色查询",
  clone: "声音创建",
  dictionaries: "发音词典",
};
const billingNames = {
  plus: "Plus 网页额度",
  api: "开发者 API 余额",
  free: "免费模型路线",
};

export default function SpeechPage() {
  const workspace = useRoleWorkspace();
  const view = useDomainResource<SpeechView>(
    "speech/view",
    actorScope(workspace.actor),
  );
  return (
    <div className="domain-workspace">
      <header className="domain-toolbar">
        <div>
          <p className="eyebrow">声音与语音</p>
          <h2>保持她的声音，自然表达</h2>
        </div>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          读取状态
        </button>
      </header>
      <SpeechOAuthComplete refresh={view.refresh} />
      <CapabilityReadiness
        ids={["speech.synthesize"]}
        title="当前角色语音表达"
      />
      {view.error && (
        <p role="alert" className="error-text">
          {view.error}
        </p>
      )}
      {view.loading && !view.data && <p role="status">正在读取语音连接…</p>}
      {view.data && (
        <>
          <section className="panel">
            <h3>Fish Audio 连接</h3>
            <p>
              Plus MCP 使用网页账户额度；原生 API
              单独计费。路线不可用时保留原因，由你选择下一条路线。
            </p>
            <div className="domain-grid">
              {view.data.connections.map((connection) => (
                <ConnectionCard
                  key={`${connection.id}:${connection.revision}`}
                  connection={connection}
                  refresh={view.refresh}
                />
              ))}
            </div>
            {!view.data.connections.length && (
              <p className="muted">还没有配置语音连接。</p>
            )}
            <DetailCard title="添加语音连接">
              <ConnectionEditor refresh={view.refresh} />
            </DetailCard>
          </section>
          <section className="panel">
            <h3>角色声线与输出方式</h3>
            <p>
              固定音色与本次语气分开保存。文字、语音、双模式或自然选择都沿同一表达链发送。
            </p>
            {workspace.actor &&
              !view.data.profiles.some(
                (profile) =>
                  profile.actor_id === workspace.actor && profile.voice_id,
              ) && (
                <p role="status">
                  此角色尚未绑定音色。连接功能仍可分别使用，需要语音表达时再选择并保存声线。
                </p>
              )}
            {!workspace.actor ? (
              <p>先选择一位角色。</p>
            ) : (
              <VoiceProfileEditor
                key={`${workspace.actor}:${JSON.stringify(view.data.profiles.map((profile) => [profile.id, profile.revision]))}`}
                actor={workspace.actor}
                profile={
                  view.data.profiles.find(
                    (profile) => profile.id === workspace.actor,
                  ) ??
                  (view.data.profiles.filter(
                    (profile) => profile.actor_id === workspace.actor,
                  ).length === 1
                    ? view.data.profiles.find(
                        (profile) => profile.actor_id === workspace.actor,
                      )
                    : undefined)
                }
                view={view.data}
                refresh={view.refresh}
              />
            )}
          </section>
          <VoiceBrowser connections={view.data.connections} />
          <section className="panel">
            <h3>中文读音词典</h3>
            <p>为人名、多音字和项目名保存明确读音，并通过试听核对。</p>
            {view.data.dictionaries.map((dictionary) => (
              <DetailCard
                key={`${dictionary.id}:${dictionary.revision}`}
                title={dictionary.name}
              >
                <DictionaryEditor
                  dictionary={dictionary}
                  refresh={view.refresh}
                />
              </DetailCard>
            ))}
            <DetailCard title="新建发音词典">
              <DictionaryEditor refresh={view.refresh} />
            </DetailCard>
          </section>
          <SpeechTranscription connections={view.data.connections} />
        </>
      )}
    </div>
  );
}

function ConnectionCard({
  connection,
  refresh,
}: {
  connection: FishConnection;
  refresh: () => Promise<void>;
}) {
  const action = useDomainCommand<{ authorization_url?: string }>(
    "speech-connection:" + connection.id,
    "global",
  );
  return (
    <article className="domain-record">
      <header>
        <strong>{connection.id}</strong>
        <span className="badge">{connection.state}</span>
      </header>
      <dl>
        <div>
          <dt>接入方式</dt>
          <dd>
            {
              {
                mcp: "Fish Plus MCP",
                native: "Fish原生API",
                edge: "Edge免费语音",
                compatible: "兼容语音API",
              }[connection.transport]
            }
          </dd>
        </div>
        <div>
          <dt>计费来源</dt>
          <dd>{billingNames[connection.billing_source]}</dd>
        </div>
        <div>
          <dt>可用额度</dt>
          <dd>
            {connection.balance
              ? `${connection.balance.value} ${connection.balance.unit}`
              : "未知"}
          </dd>
        </div>
        <div>
          <dt>账户/团队</dt>
          <dd>
            {[
              connection.account_id,
              connection.team_id,
              connection.workspace_id,
            ]
              .filter(Boolean)
              .join(" / ") || "尚未取得"}
          </dd>
        </div>
        <div>
          <dt>可用功能</dt>
          <dd>
            {Object.entries(connection.capabilities)
              .filter(([, supported]) => supported)
              .map(([key]) => capabilityNames[key] ?? key)
              .join("、") || "尚未确认"}
          </dd>
        </div>
      </dl>
      {connection.reason && <p>{connection.reason}</p>}
      <div className="domain-actions">
        {connection.transport === "mcp" && (
          <button
            className="button"
            disabled={action.busy}
            onClick={() =>
              void action.run(
                "speech/connections.connect",
                {
                  connection_id: connection.id,
                  redirect_uri:
                    window.location.origin +
                    window.location.pathname +
                    "?tianshu_oauth=speech",
                },
                connection.revision,
              )
            }
          >
            连接 Plus 账户
          </button>
        )}
        <button
          className="button"
          disabled={action.busy}
          onClick={() =>
            void action
              .run(
                "speech/connections.refresh",
                { connection_id: connection.id },
                connection.revision,
              )
              .then(refresh)
          }
        >
          刷新能力与余额
        </button>
        <button
          className="button"
          disabled={action.busy}
          onClick={() =>
            void action
              .run(
                "speech/connections.disconnect",
                { connection_id: connection.id },
                connection.revision,
              )
              .then(refresh)
          }
        >
          断开连接
        </button>
      </div>
      <CommandFeedback {...action} />
      {action.result?.data?.authorization_url && (
        <a
          className="button primary"
          href={action.result.data.authorization_url}
          target="_blank"
          rel="noreferrer"
        >
          打开官方账户授权
        </a>
      )}
      <DetailCard title="连接设置">
        <ConnectionEditor connection={connection} refresh={refresh} />
      </DetailCard>
    </article>
  );
}

function ConnectionEditor({
  connection,
  refresh,
}: {
  connection?: FishConnection;
  refresh: () => Promise<void>;
}) {
  const action = useDomainCommand(
    "speech-connection-config:" + (connection?.id ?? "new"),
    "global",
  );
  const [transport, setTransport] = useState(connection?.transport ?? "mcp");
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        void action
          .run(
            "speech/connections.save",
            {
              id: data.get("id"),
              transport,
              enabled: data.get("enabled") === "on",
              ...(data.get("api_key") ? { api_key: data.get("api_key") } : {}),
              api_key_required: data.get("api_key_required") === "on",
              account_id: data.get("account_id"),
              quota_group: data.get("quota_group"),
              endpoint: data.get("endpoint") || null,
              team_id: data.get("team_id") || null,
              workspace_id: data.get("workspace_id") || null,
            },
            connection?.revision ?? 0,
          )
          .then((answer) => {
            const secret =
              form.querySelector<HTMLInputElement>('[name="api_key"]');
            if (secret) secret.value = "";
            if (answer) void refresh();
          });
      }}
    >
      <label>
        连接名称
        <input
          name="id"
          required
          defaultValue={connection?.id ?? ""}
          readOnly={!!connection}
        />
      </label>
      <label>
        接入方式
        <select
          value={transport}
          onChange={(event) =>
            setTransport(event.target.value as FishConnection["transport"])
          }
        >
          <option value="mcp">Fish Plus 官方 MCP</option>
          <option value="native">Fish 原生 API</option>
          <option value="edge">Edge 免费语音</option>
          <option value="compatible">兼容语音服务</option>
        </select>
      </label>
      {["native", "compatible"].includes(transport) ? (
        <label>
          API Key
          <input
            type="password"
            name="api_key"
            autoComplete="off"
            placeholder={
              connection?.configured ? "已保存；留空保留" : "输入 API Key"
            }
          />
        </label>
      ) : (
        <p className="muted">
          保存后连接官方账户。MCP
          生成的供应商链接可被持链者访问；天枢会将产物收取到受控媒体库，但无法撤销供应商链接。
        </p>
      )}
      {transport === "compatible" && (
        <label>
          <input
            type="checkbox"
            name="api_key_required"
            defaultChecked={true}
          />
          兼容服务要求API Key
        </label>
      )}
      <label>
        账户标识
        <input
          name="account_id"
          required
          defaultValue={connection?.account_id ?? ""}
        />
      </label>
      <label>
        共享额度组
        <input
          name="quota_group"
          required
          defaultValue={connection?.quota_group ?? ""}
        />
      </label>
      <label>
        服务地址
        <input
          name="endpoint"
          type="url"
          defaultValue={
            "endpoint" in (connection ?? {})
              ? String(
                  (connection as FishConnection & { endpoint?: string })
                    .endpoint ?? "",
                )
              : ""
          }
        />
      </label>
      <label>
        团队
        <input name="team_id" defaultValue={connection?.team_id ?? ""} />
      </label>
      <label>
        工作区
        <input
          name="workspace_id"
          defaultValue={connection?.workspace_id ?? ""}
        />
      </label>
      <label className="inline-check">
        <input
          type="checkbox"
          name="enabled"
          defaultChecked={connection?.enabled ?? true}
        />
        启用此连接
      </label>
      <button className="button primary" disabled={action.busy}>
        保存连接
      </button>
      <CommandFeedback {...action} />
    </form>
  );
}

function VoiceProfileEditor({
  actor,
  profile,
  view,
  refresh,
}: {
  actor: string;
  profile?: VoiceProfile;
  view: SpeechView;
  refresh: () => Promise<void>;
}) {
  const action = useDomainCommand<SpeechArtifact>(actor, "actor");
  const preview = useDomainCommand<SpeechArtifact>("speech-preview:" + actor);
  const [connection, setConnection] = useState(
    profile?.connection_id ?? view.connections[0]?.id ?? "",
  );
  const [voice, setVoice] = useState(profile?.voice_id ?? "");
  const [model, setModel] = useState(profile?.model ?? "s2.1-pro");
  const [sample, setSample] = useState("你好，今天过得怎么样？");
  const [mode, setMode] = useState(profile?.output_mode ?? "natural");
  return (
    <div className="domain-form">
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void action
            .run(
              "speech/profiles.save",
              {
                id: profile?.id ?? actor,
                actor_id: actor,
                connection_id: connection,
                voice_id: voice,
                model,
                language: data.get("language"),
                output_mode: mode,
                dictionary_ids: data.getAll("dictionary"),
                vocal_defaults: {
                  speed: Number(data.get("pace")),
                  tone: data.get("direction"),
                },
              },
              profile?.revision ?? 0,
            )
            .then((result) => {
              if (result) void refresh();
            });
        }}
      >
        <div className="domain-grid">
          <label>
            合成连接
            <select
              required
              value={connection}
              onChange={(event) => setConnection(event.target.value)}
            >
              {view.connections.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {billingNames[item.billing_source]}
                </option>
              ))}
            </select>
          </label>
          <label>
            稳定音色 ID
            <input
              value={voice}
              onChange={(event) => setVoice(event.target.value)}
              required
            />
          </label>
          <label>
            模型
            <input
              list="fish-models"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              required
            />
            <datalist id="fish-models">
              <option value="s2-pro" />
              <option value="s2.1-pro" />
              <option value="s2.1-pro-free" />
              <option value="drama-3" />
            </datalist>
          </label>
          <label>
            输出方式
            <select
              value={mode}
              onChange={(event) =>
                setMode(event.target.value as VoiceProfile["output_mode"])
              }
            >
              <option value="natural">自然选择</option>
              <option value="text">文字</option>
              <option value="audio">语音</option>
              <option value="both">文字与语音</option>
            </select>
          </label>
          <label>
            语言
            <input
              name="language"
              defaultValue={profile?.language ?? "zh-CN"}
            />
          </label>
          <label>
            默认语速
            <input
              name="pace"
              type="number"
              min="0.5"
              max="2"
              step="0.05"
              defaultValue={Number(profile?.vocal_defaults.speed ?? 1)}
            />
          </label>
        </div>
        <label>
          发声风格
          <input
            name="direction"
            defaultValue={String(profile?.vocal_defaults.tone ?? "")}
            placeholder="例如：清晰、轻松，避免过度夸张"
          />
        </label>
        <fieldset>
          <legend>使用的读音词典</legend>
          {view.dictionaries.map((item) => (
            <label className="inline-check" key={item.id}>
              <input
                type="checkbox"
                name="dictionary"
                value={item.id}
                defaultChecked={profile?.dictionary_ids.includes(item.id)}
              />
              {item.name}
            </label>
          ))}
        </fieldset>
        <button className="button primary" disabled={action.busy}>
          保存角色声线
        </button>
      </form>
      <label>
        试听台词
        <textarea
          value={sample}
          onChange={(event) => setSample(event.target.value)}
        />
      </label>
      <button
        className="button"
        disabled={preview.busy || !voice || !connection || !sample.trim()}
        onClick={() =>
          void preview.run("speech/preview", {
            ...(profile
              ? { profile_id: profile.id, profile_revision: profile.revision }
              : {}),
            actor_id: actor,
            connection_id: connection,
            voice_id: voice,
            model,
            text: sample,
          })
        }
      >
        试听当前音色与模型
      </button>
      <CommandFeedback {...action} />
      <CommandFeedback {...preview} />
      {preview.result?.data?.artifacts && (
        <>
          <ArtifactList artifacts={preview.result.data.artifacts} />
          <p className="muted">
            实际模型 {preview.result.data.model} ·{" "}
            {preview.result.data.billing_source} ·{" "}
            {preview.result.data.usage === null ? "费用未知" : "已返回实际用量"}
          </p>
        </>
      )}
    </div>
  );
}

function DictionaryEditor({
  dictionary,
  refresh,
}: {
  dictionary?: PronunciationDictionary;
  refresh: () => Promise<void>;
}) {
  const action = useDomainCommand(
    "speech-dictionary:" + (dictionary?.id ?? "new"),
    "global",
  );
  const [error, setError] = useState("");
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        setError("");
        const entries = String(data.get("entries"))
          .split("\n")
          .filter((line) => line.trim())
          .map((line) => {
            const separator = line.indexOf("=");
            if (separator < 1) return { text: "", pronunciation: "" };
            return {
              text: line.slice(0, separator).trim(),
              pronunciation: line.slice(separator + 1).trim(),
            };
          });
        if (
          entries.some(
            (entry) =>
              !entry.text || !entry.pronunciation || entry.text.includes("="),
          )
        ) {
          setError("请按 词语=读音 每行填写，词语与读音均不能为空。");
          return;
        }
        void action
          .run(
            "speech/dictionaries.save",
            {
              id: dictionary?.id ?? null,
              name: data.get("name"),
              entries,
              provider_id: data.get("provider_id") || null,
              provider_version: data.get("provider_version") || null,
            },
            dictionary?.revision ?? 0,
          )
          .then((result) => {
            if (result) void refresh();
          });
      }}
    >
      <label>
        词典名称
        <input name="name" defaultValue={dictionary?.name ?? ""} required />
      </label>
      <label>
        读音条目
        <textarea
          name="entries"
          required
          defaultValue={
            dictionary?.entries
              .map((entry) => `${entry.text}=${entry.pronunciation}`)
              .join("\n") ?? ""
          }
          placeholder={"每行一个词，例如：\n重庆=chóng qìng"}
        />
      </label>
      <div className="domain-grid">
        <label>
          供应商词典ID（可选）
          <input
            name="provider_id"
            defaultValue={dictionary?.provider_id ?? ""}
          />
        </label>
        <label>
          供应商词典版本（与ID一起填写）
          <input
            name="provider_version"
            defaultValue={dictionary?.provider_version ?? ""}
          />
        </label>
      </div>
      {error && <p role="alert">{error}</p>}
      <button className="button" disabled={action.busy}>
        保存词典
      </button>
      <CommandFeedback {...action} />
    </form>
  );
}
