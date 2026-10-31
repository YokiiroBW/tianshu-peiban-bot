import { Drawer } from "../../../components/Drawer";
import { useEffect, useState, type FormEvent } from "react";
import { requestId } from "../../../app/requestId";
import { readFailure } from "../../../app/integrationApi";
import { credentialStatus } from "./api";
import type {
  CredentialChange,
  CredentialView,
  SkillAccess,
  SkillSource,
} from "./types";

const states: Record<SkillSource["state"], string> = {
  configured: "已配置 · 尚未刷新",
  disabled: "已停用",
  ready: "目录已读取",
  unreachable: "无法连接",
  invalid: "目录内容无效",
};

export function SkillSources({
  access,
  sources,
  busy,
  refresh,
  save,
}: {
  access: SkillAccess;
  sources: SkillSource[];
  busy: boolean;
  refresh: (source: SkillSource) => void;
  save: (
    value: object,
    credential: CredentialChange,
    revision: number | null,
  ) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<SkillSource | null | undefined>();
  return (
    <section
      className="panel skills-sources"
      aria-labelledby="skill-sources-title"
    >
      <div className="section-heading">
        <h2 id="skill-sources-title">外部技能目录</h2>
        <button
          className="button"
          disabled={busy}
          onClick={() => setEditing(null)}
        >
          添加目录
        </button>
      </div>
      <p className="muted">
        刷新已配置的目录，查看来源发布的技能。未接入执行适配器的条目会显示待接入。
      </p>
      {sources.length === 0 && <p>尚未添加外部目录，内置技能可以直接管理。</p>}
      <ul className="skills-source-list">
        {sources.map((source) => (
          <li key={source.source_id}>
            <div>
              <h3>{source.name}</h3>
              <p>{states[source.state]}</p>
              <p className="skills-url">{source.manifest_url}</p>
              <small>
                版本 {source.version}
                {source.last_refreshed_at
                  ? ` · 最近刷新 ${source.last_refreshed_at}`
                  : " · 尚未刷新"}
              </small>
              {source.error_code && (
                <p className="skills-error">状态码：{source.error_code}</p>
              )}
            </div>
            <div className="skills-actions">
              <button
                className="button"
                disabled={busy || !source.enabled}
                onClick={() => refresh(source)}
              >
                刷新目录
              </button>
              <button
                className="button"
                disabled={busy}
                onClick={() => setEditing(source)}
              >
                编辑目录
              </button>
            </div>
          </li>
        ))}
      </ul>
      {editing !== undefined && (
        <Drawer
          title={editing ? "编辑技能目录" : "添加技能目录"}
          variant="card"
          open={true}
          onClose={() => setEditing(undefined)}
        >
          <SourceForm
            key={`${access.actor}:${editing?.source_id ?? "new"}`}
            access={access}
            source={editing}
            busy={busy}
            cancel={() => setEditing(undefined)}
            save={async (value, credential, revision) => {
              const success = await save(value, credential, revision);
              if (success) setEditing(undefined);
              return success;
            }}
          />
        </Drawer>
      )}
    </section>
  );
}

function SourceForm({
  access,
  source,
  busy,
  save,
  cancel,
}: {
  access: SkillAccess;
  source: SkillSource | null;
  busy: boolean;
  save: (
    value: object,
    credential: CredentialChange,
    revision: number | null,
  ) => Promise<boolean>;
  cancel: () => void;
}) {
  const [sourceId] = useState(
    () => source?.source_id ?? `source:${requestId()}`,
  );
  const [name, setName] = useState(source?.name ?? "");
  const [url, setUrl] = useState(source?.manifest_url ?? "");
  const [enabled, setEnabled] = useState(source?.enabled ?? true);
  const [sha, setSha] = useState(source?.expected_sha256 ?? "");
  const [token, setToken] = useState("");
  const [clear, setClear] = useState(false);
  const [credentials, setCredentials] = useState<CredentialView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    credentialStatus(access, { source_id: sourceId }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setCredentials(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(readFailure(cause));
      });
    return () => controller.abort();
  }, [access.actor, access.csrf, sourceId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!credentials) return;
    await save(
      {
        source_id: sourceId,
        name,
        manifest_url: url,
        enabled,
        expected_sha256: sha || null,
      },
      clear
        ? { action: "clear" }
        : token
          ? { action: "replace", value: token }
          : { action: "keep" },
      credentials.revision,
    );
  }
  return (
    <form
      className="skills-form external-form"
      aria-label="技能目录设置"
      onSubmit={(event) => void submit(event)}
    >
      <h3>{source ? "编辑外部目录" : "添加外部目录"}</h3>
      <label>
        目录名称
        <input
          value={name}
          required
          maxLength={128}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        目录清单地址
        <input
          type="url"
          value={url}
          required
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://example.org/skills.json"
        />
      </label>
      <label>
        固定内容 SHA256（可选）
        <input
          value={sha}
          pattern="[0-9a-f]{64}"
          onChange={(event) => setSha(event.target.value)}
          placeholder="需要固定目录内容时填写"
        />
      </label>
      <label>
        目录访问令牌（可选）
        <input
          type="password"
          autoComplete="new-password"
          value={token}
          disabled={clear}
          onChange={(event) => setToken(event.target.value)}
          placeholder={
            credentials?.credential_configured
              ? "已保存；留空保留"
              : "无令牌可留空"
          }
        />
      </label>
      {credentials?.credential_configured && (
        <label className="skills-check">
          <input
            type="checkbox"
            checked={clear}
            onChange={(event) => setClear(event.target.checked)}
          />
          清除已保存令牌
        </label>
      )}
      <label className="skills-check">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        启用此目录
      </label>
      {error && (
        <p className="skills-error" role="alert">
          {error}
        </p>
      )}
      <div className="skills-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={busy || !credentials}
        >
          保存目录
        </button>
        <button
          className="button"
          type="button"
          disabled={busy}
          onClick={cancel}
        >
          取消编辑
        </button>
      </div>
    </form>
  );
}
