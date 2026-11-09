import { useState } from "react";
import { actorScope, useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import { ArtifactList } from "../../components/MediaPreview";
import { VoiceClone } from "./SpeechTools";
import { VoicePicker } from "./VoicePicker";
import { speechConnectionName } from "./types";
import type { SpeechArtifact, SpeechView, VoiceProfile } from "./types";

const billingNames: Record<string, string> = {
  plus: "Plus 网页额度",
  api: "开发者 API 余额",
  free: "免费模型路线",
};

/** Both role settings and connection settings edit the same persisted voice profile. */
export function VoiceProfileEditor({
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
  const connections = view.connections.filter(
    (item) => item.enabled && item.configured && item.capabilities.tts,
  );
  const [connectionId, setConnectionId] = useState(
    profile?.connection_id ?? connections[0]?.id ?? "",
  );
  const [voiceId, setVoiceId] = useState(profile?.voice_id ?? "");
  const [voiceName, setVoiceName] = useState(profile?.voice_name ?? "");
  const [model, setModel] = useState(profile?.model ?? "s2.1-pro");
  const [mode, setMode] = useState(profile?.output_mode ?? "both");
  const [sample, setSample] = useState("你好，今天过得怎么样？");
  const action = useDomainCommand<VoiceProfile>(
    "voice-profile:" + actor,
    "actor",
  );
  const preview = useDomainCommand<SpeechArtifact>(
    ["speech-preview", actor, connectionId, voiceId, model].join(":"),
    "actor",
  );
  const connection = connections.find((item) => item.id === connectionId);
  const ready = !!connection && !!voiceId.trim();

  return (
    <div className="domain-form role-voice-settings">
      {profile?.voice_id && (
        <p className="voice-binding" role="status">
          已绑定：{profile.voice_name || profile.voice_id}
          {profile.output_mode === "text" &&
            " · 当前只发送文字，可在下方调整输出方式。"}
        </p>
      )}
      {!connections.length ? (
        <p>
          尚无可用的语音连接。请先在{" "}
          <a href="#/settings/speech">声音与语音设置</a>{" "}
          中连接账户，再回来选择音色。
        </p>
      ) : (
        <label>
          语音账户
          <select
            value={connectionId}
            onChange={(event) => {
              setConnectionId(event.target.value);
              setVoiceId("");
              setVoiceName("");
            }}
          >
            {!connection && (
              <option value={connectionId}>
                原绑定连接暂不可用，请选择账户
              </option>
            )}
            {connections.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id} · {speechConnectionName(item)}
              </option>
            ))}
          </select>
        </label>
      )}
      {connection?.capabilities.voices && (
        <VoicePicker
          key={connection.id}
          connection={connection}
          selected={voiceId}
          onSelect={(voice) => {
            setVoiceId(voice.id);
            setVoiceName(voice.name);
          }}
        />
      )}
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready) return;
          const data = new FormData(event.currentTarget);
          void action
            .run(
              "speech/profiles.save",
              {
                ...actorScope(actor),
                id: profile?.id ?? actor,
                connection_id: connectionId,
                voice_id: voiceId.trim(),
                voice_name: voiceName || null,
                model,
                language: data.get("language"),
                output_mode: mode,
                dictionary_ids: data.getAll("dictionary"),
                vocal_defaults: {
                  ...profile?.vocal_defaults,
                  speed: Number(data.get("pace")),
                  tone: data.get("direction"),
                },
              },
              profile?.revision ?? 0,
            )
            .then((result) => {
              if (result?.data?.id) void refresh();
            });
        }}
      >
        {voiceId && (
          <p className="voice-selection" role="status">
            已选音色：{voiceName || voiceId}
            {voiceName && (
              <span className="muted voice-id">音色 ID：{voiceId}</span>
            )}
          </p>
        )}
        <details>
          <summary>手动填写音色 ID</summary>
          <label>
            音色 ID
            <input
              value={voiceId}
              placeholder="粘贴已知的音色 ID"
              onChange={(event) => {
                setVoiceId(event.target.value);
                setVoiceName("");
              }}
            />
          </label>
        </details>
        <label>
          输出方式
          <select
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as VoiceProfile["output_mode"])
            }
          >
            <option value="both">文字与语音</option>
            <option value="audio">语音</option>
            <option value="natural">自然选择</option>
            <option value="text">文字</option>
          </select>
        </label>
        <details>
          <summary>更多声音设置</summary>
          <div className="domain-form">
            <div className="domain-grid">
              <label>
                模型
                <input
                  list={"fish-models-" + actor}
                  value={model}
                  required
                  onChange={(event) => setModel(event.target.value)}
                />
                <datalist id={"fish-models-" + actor}>
                  <option value="s2-pro" />
                  <option value="s2.1-pro" />
                  <option value="s2.1-pro-free" />
                  <option value="drama-3" />
                </datalist>
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
                placeholder="例如：清晰、轻松"
              />
            </label>
            {!!view.dictionaries.length && (
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
            )}
          </div>
        </details>
        <button className="button primary" disabled={action.busy || !ready}>
          绑定到当前角色
        </button>
        <p className="muted">保存后，角色会按所选输出方式使用这条声线。</p>
      </form>
      <CommandFeedback {...action} />
      <label>
        试听台词
        <textarea
          value={sample}
          onChange={(event) => setSample(event.target.value)}
        />
      </label>
      <button
        className="button"
        disabled={preview.busy || !ready || !sample.trim()}
        onClick={() => {
          void preview.run("speech/preview", {
            ...actorScope(actor),
            ...(profile
              ? { profile_id: profile.id, profile_revision: profile.revision }
              : {}),
            connection_id: connectionId,
            voice_id: voiceId.trim(),
            model,
            text: sample,
          });
        }}
      >
        试听所选音色
      </button>
      <CommandFeedback {...preview} />
      {preview.result?.data?.artifacts && (
        <ArtifactList artifacts={preview.result.data.artifacts} />
      )}
      {preview.result?.data?.artifacts && (
        <p className="muted">
          合成模型：{preview.result.data.model} ·
          {billingNames[preview.result.data.billing_source] ??
            preview.result.data.billing_source}{" "}
          ·{preview.result.data.usage == null ? "用量未知" : "已返回实际用量"}
        </p>
      )}
      {connections.some((item) => item.capabilities.clone) && (
        <DetailCard title="创建私有音色">
          <VoiceClone connections={connections} />
        </DetailCard>
      )}
      <a href="#/settings/speech">管理语音连接与发音词典</a>
    </div>
  );
}
