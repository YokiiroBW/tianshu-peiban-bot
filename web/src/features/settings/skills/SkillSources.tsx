import { humanizeCode } from "../../../design/humanize";
import { useState } from "react";
import { useDomainCommand } from "../../../api/useDomainResource";
import { Drawer } from "../../../components/Drawer";
import { SourceEvidence } from "../../../components/SourceEvidence";
import { CommandFeedback } from "../../../components/CommandFeedback";
import type { SkillSource } from "./types";
export function SkillSources({
  actor,
  sources,
  canSave,
  canRefresh,
  refresh,
}: {
  actor: string;
  sources: SkillSource[];
  canSave: boolean;
  canRefresh: boolean;
  refresh: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<SkillSource | "new" | null>(null);
  const action = useDomainCommand(`catalog-sources:${actor}`, "actor");
  return (
    <section className="panel skills-sources">
      <div className="section-heading">
        <div>
          <h3>只读目录来源</h3>
          <p>
            远程manifest只贡献目录信息，实际执行仍由已安装的正式适配器负责。
          </p>
        </div>
        {canSave && (
          <button className="button" onClick={() => setEditing("new")}>
            添加来源
          </button>
        )}
      </div>
      {sources.map((source) => (
        <article className="domain-record" key={source.source_id}>
          <header>
            <strong>{source.name}</strong>
            <span className="badge">{source.state}</span>
          </header>
          <p>{source.manifest_url}</p>
          <p>
            {source.enabled ? "目录来源已启用" : "目录来源已停用"} ·{" "}
            {source.credential_configured ? "已保存访问凭据" : "未配置访问凭据"}
          </p>
          {source.last_refreshed_at && (
            <p>
              最近实际读取：
              {new Date(source.last_refreshed_at).toLocaleString("zh-CN")}
            </p>
          )}
          {source.error_code && <p role="alert">{humanizeCode(source.error_code, "能力来源读取未完成")}</p>}
          <SourceEvidence sources={source.source_refs ?? []} />
          <div className="domain-actions">
            {canSave && (
              <button
                className="button"
                onClick={() => setEditing(structuredClone(source))}
              >
                编辑来源
              </button>
            )}
            {canRefresh && (
              <button
                className="button"
                disabled={action.busy || !source.enabled}
                onClick={() =>
                  void action
                    .run(
                      "capabilities/catalog/sources.refresh",
                      {
                        actor_id: actor,
                        person_id: null,
                        conversation_id: null,
                        source_id: source.source_id,
                      },
                      source.revision,
                    )
                    .then((result) => {
                      if (result) void refresh();
                    })
                }
              >
                读取实际目录
              </button>
            )}
          </div>
        </article>
      ))}
      {!sources.length && (
        <p className="muted">
          尚无外部manifest来源。已安装的内建与插件能力仍在同一目录中。
        </p>
      )}
      <CommandFeedback {...action} />
      <Drawer
        title="目录来源"
        open={editing !== null}
        onClose={() => setEditing(null)}
        variant="card"
      >
        {editing && (
          <SourceEditor
            key={
              editing === "new"
                ? "new"
                : `${editing.source_id}:${editing.revision}`
            }
            actor={actor}
            value={editing === "new" ? null : editing}
            close={() => setEditing(null)}
            saved={() => {
              setEditing(null);
              void refresh();
            }}
          />
        )}
      </Drawer>
    </section>
  );
}
function SourceEditor({
  actor,
  value,
  close,
  saved,
}: {
  actor: string;
  value: SkillSource | null;
  close: () => void;
  saved: () => void;
}) {
  const action = useDomainCommand(
    `catalog-source:${actor}:${value?.source_id ?? "new"}`,
    "actor",
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void action
          .run(
            "capabilities/catalog/sources.save",
            {
              actor_id: actor,
              person_id: null,
              conversation_id: null,
              source_id: data.get("source_id"),
              name: data.get("name"),
              manifest_url: data.get("url"),
              quota_group: data.get("quota_group"),
              expected_sha256: data.get("sha") || null,
              enabled: data.get("enabled") === "on",
              ...(data.get("token") ? { token: data.get("token") } : {}),
            },
            value?.revision ?? 0,
          )
          .then((result) => {
            if (result) saved();
          });
      }}
    >
      <label>
        来源标识
        <input
          name="source_id"
          defaultValue={value?.source_id ?? ""}
          readOnly={!!value}
          required
          maxLength={100}
          pattern="[A-Za-z0-9_.-]+"
        />
      </label>
      <label>
        名称
        <input name="name" defaultValue={value?.name ?? ""} required />
      </label>
      <label>
        manifest地址
        <input
          type="url"
          name="url"
          defaultValue={value?.manifest_url ?? ""}
          required
          placeholder="https://…/capabilities.json"
        />
      </label>
      <label>
        真实资源配额组
        <input
          name="quota_group"
          defaultValue={value?.quota_group ?? ""}
          required
        />
      </label>
      <label>
        固定SHA-256（可留空）
        <input
          name="sha"
          defaultValue={value?.expected_sha256 ?? ""}
          pattern="[0-9a-f]{64}"
        />
      </label>
      <label>
        新的访问token
        <input
          name="token"
          type="password"
          autoComplete="new-password"
          placeholder={
            value?.credential_configured ? "留空保留已存凭据" : "可留空"
          }
        />
      </label>
      <label className="inline-check">
        <input
          name="enabled"
          type="checkbox"
          defaultChecked={value?.enabled ?? true}
        />
        启用来源
      </label>
      <div className="domain-actions">
        <button className="button primary" disabled={action.busy}>
          保存来源
        </button>
        <button type="button" className="button" onClick={close}>
          收起
        </button>
      </div>
      <CommandFeedback {...action} />
    </form>
  );
}
