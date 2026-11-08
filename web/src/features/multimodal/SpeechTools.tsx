import { speechConnectionName } from "./types";
import { readableRecord } from "../../design/readableRecord";
import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  useDomainTarget,
  GLOBAL_SCOPE,
} from "../../api/useDomainResource";
import { uploadMedia } from "../../api/uploads";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import type { FishConnection } from "./types";
import { VoicePicker } from "./VoicePicker";

export function SpeechTranscription({
  connections,
}: {
  connections: FishConnection[];
}) {
  const { session } = useAuth();
  const scope = useDomainTarget();
  const action = useDomainCommand<{
    transcript: Record<string, unknown>;
    uncertainty: unknown;
    model: string;
  }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <section className="panel">
      <h3>文件转写</h3>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const file = data.get("audio");
          if (!(file instanceof File) || !file.size || !session) return;
          setBusy(true);
          setError("");
          void uploadMedia(
            file,
            scope,
            session.csrf,
            new AbortController().signal,
          )
            .then((uploaded) =>
              action.run(
                "speech/transcribe",
                {
                  ...scope,
                  connection_id: data.get("connection"),
                  media: uploaded.media,
                  language: String(data.get("language")) || null,
                },
                null,
              ),
            )
            .catch((cause) =>
              setError(
                cause instanceof Error ? cause.message : "原录音上传未确认。",
              ),
            )
            .finally(() => setBusy(false));
        }}
      >
        <label>
          语音连接
          <select name="connection" required>
            {connections
              .filter((x) => x.capabilities.asr)
              .map((x) => (
                <option key={x.id} value={x.id}>
                  {speechConnectionName(x, connections.indexOf(x))}
                </option>
              ))}
          </select>
        </label>
        <label>
          音频原件
          <input name="audio" type="file" accept="audio/*" required />
        </label>
        <label>
          语言提示（可选）
          <input name="language" placeholder="zh" />
        </label>
        <button className="button" disabled={busy || action.busy}>
          上传并转写
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <CommandFeedback {...action} />
      {action.result?.data?.transcript && (
        <>
          <blockquote>
            {typeof action.result.data.transcript.text === "string"
              ? action.result.data.transcript.text
              : "供应商未返回统一文本，请核对实际结果。"}
          </blockquote>
          <p>
            模型：{action.result.data.model} · 不确定性：
            {action.result.data.uncertainty == null
              ? "未知"
              : JSON.stringify(action.result.data.uncertainty)}
          </p>
          <details>
            <summary>真实转写、时间段与置信信息</summary>
            <pre>{readableRecord(action.result.data.transcript)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
export function VoiceBrowser({
  connections,
}: {
  connections: FishConnection[];
}) {
  const available = connections.filter(
    (item) => item.enabled && item.configured && item.capabilities.voices,
  );
  const [selected, setSelected] = useState(available[0]?.id ?? "");
  const connection =
    available.find((item) => item.id === selected) ?? available[0];
  return (
    <section className="panel">
      <h3>查找音色</h3>
      {!connection ? (
        <p>连接语音账户后可以读取音色列表。</p>
      ) : (
        <>
          <label>
            音色连接
            <select
              value={connection.id}
              onChange={(event) => setSelected(event.target.value)}
            >
              {available.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {speechConnectionName(item)}
                </option>
              ))}
            </select>
          </label>
          <VoicePicker key={connection.id} connection={connection} />
        </>
      )}
      <DetailCard title="创建私有音色">
        <VoiceClone connections={connections} />
      </DetailCard>
    </section>
  );
}
export function VoiceClone({ connections }: { connections: FishConnection[] }) {
  const { session } = useAuth();
  const scope = useDomainTarget();
  const clone = useDomainCommand<{ voice: unknown; visibility: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!session) return;
        const data = new FormData(event.currentTarget);
        const files = data
          .getAll("audio")
          .filter(
            (file): file is File => file instanceof File && file.size > 0,
          );
        if (!files.length) return;
        setBusy(true);
        setError("");
        void (async () => {
          const recordings = [];
          for (const file of files) {
            const uploaded = await uploadMedia(
              file,
              scope,
              session.csrf,
              new AbortController().signal,
            );
            recordings.push({
              media: uploaded.media,
              text: String(data.get("transcript") ?? ""),
            });
          }
          await clone.run(
            "speech/voices.clone",
            {
              ...scope,
              connection_id: data.get("connection"),
              name: data.get("name"),
              description: data.get("description"),
              recordings,
              visibility: "private",
            },
            null,
          );
        })()
          .catch((cause) =>
            setError(
              cause instanceof Error ? cause.message : "录音上传未确认。",
            ),
          )
          .finally(() => setBusy(false));
      }}
    >
      <label>
        连接
        <select name="connection" required>
          {connections
            .filter((x) => x.capabilities.clone)
            .map((x) => (
              <option key={x.id} value={x.id}>
                {speechConnectionName(x, connections.indexOf(x))}
              </option>
            ))}
        </select>
      </label>
      <label>
        音色名称
        <input name="name" required />
      </label>
      <label>
        描述
        <textarea name="description" />
      </label>
      <label>
        参考录音
        <input name="audio" type="file" accept="audio/*" multiple required />
      </label>
      <label>
        录音对应台词（可选）
        <textarea name="transcript" />
      </label>
      <p className="muted">
        使用有权提供的录音，创建为私有音色；费用与结果按原回执显示。
      </p>
      <button className="button" disabled={busy || clone.busy}>
        上传并创建音色
      </button>
      {error && <p role="alert">{error}</p>}
      <CommandFeedback {...clone} />
      {clone.result?.data?.voice != null && (
        <pre>{readableRecord(clone.result.data.voice)}</pre>
      )}
    </form>
  );
}
export function SpeechOAuthComplete({
  refresh,
}: {
  refresh: () => Promise<void>;
}) {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const state = params.get("state");
  const [finished, setFinished] = useState(false);
  const command = useDomainCommand("fish-oauth-completion", "global");
  if (
    params.get("tianshu_oauth") !== "speech" ||
    (!code && !params.has("error")) ||
    finished
  )
    return null;
  return (
    <section className="panel">
      <h3>完成官方账户授权</h3>
      {params.get("error") ? (
        <p role="alert">
          官方授权未完成：
          {params.get("error_description") ?? params.get("error")}
        </p>
      ) : (
        <button
          className="button primary"
          disabled={command.busy || !code || !state}
          onClick={() =>
            void command
              .run(
                "speech/connections.complete",
                { ...GLOBAL_SCOPE, code, state },
                null,
              )
              .then((answer) => {
                if (answer) {
                  window.history.replaceState(
                    null,
                    "",
                    window.location.pathname + window.location.hash,
                  );
                  setFinished(true);
                  void refresh();
                }
              })
          }
        >
          完成当前授权
        </button>
      )}
      <CommandFeedback {...command} />
    </section>
  );
}
