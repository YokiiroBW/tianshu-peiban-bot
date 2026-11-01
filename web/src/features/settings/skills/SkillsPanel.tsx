import { useState } from "react";
import { useRoleWorkspace } from "../../../app/RoleWorkspace";
import {
  actorScope,
  useDomainResource,
  useDomainCommand,
} from "../../../api/useDomainResource";
import { CommandFeedback } from "../../../components/CommandFeedback";
import { DetailCard } from "../../../components/DetailCard";
import { SkillSources } from "./SkillSources";
import type { SkillsCatalog } from "./types";
import "./skills.css";
import { capabilityReason, settings, stateOf } from "./presentation";
export function SkillsPanel({ actorId }: { actorId?: string }) {
  const workspace = useRoleWorkspace();
  const actor = actorId ?? workspace.actor;
  const view = useDomainResource<SkillsCatalog>(
    "capabilities/catalog/view",
    actorScope(actor),
    !!actor,
  );
  const command = useDomainCommand(`catalog-policy:${actor}`, "actor");
  const [query, setQuery] = useState("");
  const items = view.data?.items ?? [];
  const allowed = (id: string) =>
    items.some((item) => item.definition.id === id && item.can_execute);
  const canSave = allowed("capabilities.catalog.settings.save");
  return (
    <section className="skills-page domain-workspace">
      <header className="section-heading">
        <div>
          <h2>角色能力目录</h2>
          <p>模型工具、明确命令与生活安排共用这些能力定义及执行回执。</p>
        </div>
        <button
          className="button"
          disabled={!actor || view.loading}
          onClick={() => void view.refresh()}
        >
          读取当前目录
        </button>
      </header>
      {!actor && <p>先选择一位角色。</p>}
      {view.error && <p role="alert">{view.error}</p>}
      <label>
        查找能力
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="名称、能力标识或说明"
        />
      </label>
      <div className="domain-grid">
        {items
          .filter(
            (item) =>
              (item.definition.model_tool ||
                item.definition.public_command ||
                item.source_ids.length > 0) &&
              `${item.definition.title} ${item.definition.id} ${item.definition.description}`
                .toLowerCase()
                .includes(query.toLowerCase()),
          )
          .map((item) => (
            <article className="skill-card panel" key={item.definition.id}>
              <header>
                <h3>{item.definition.title}</h3>
                <span className="badge">{stateOf(item)}</span>
              </header>
              <p>{item.definition.description}</p>
              <small>
                {item.definition.id} · 定义修订 {item.definition.version} ·{" "}
                {item.definition.effect === "read" ? "读取能力" : "会产生变更"}
              </small>
              {item.availability.reason && (
                <p>{capabilityReason(item.availability.reason)}</p>
              )}
              {item.availability.provider_supported === null && (
                <p className="muted">供应商实际支持情况尚未确认。</p>
              )}
              {item.availability.verified_at && (
                <p>
                  最近实际核对：
                  {new Date(item.availability.verified_at).toLocaleString(
                    "zh-CN",
                  )}
                </p>
              )}
              {item.definition.public_command && (
                <p>
                  明确命令：{item.definition.public_command.name}
                  {item.definition.public_command.aliases.length
                    ? ` · ${item.definition.public_command.aliases.join("、")}`
                    : ""}
                </p>
              )}
              <div className="domain-actions">
                {canSave && (
                  <button
                    className="button"
                    disabled={command.busy}
                    onClick={() =>
                      void command
                        .run(
                          "capabilities/catalog/settings.save",
                          {
                            actor_id: actor,
                            person_id: null,
                            conversation_id: null,
                            capabilities: {
                              [item.definition.id]: { enabled: !item.enabled },
                            },
                          },
                          view.data!.revision,
                        )
                        .then((result) => {
                          if (result) void view.refresh();
                        })
                    }
                  >
                    {item.enabled ? "停用此角色的能力" : "启用此角色的能力"}
                  </button>
                )}
                {settings[item.definition.id.split(".")[0]] && (
                  <a href={settings[item.definition.id.split(".")[0]]}>
                    配置所属服务
                  </a>
                )}
              </div>
              {!item.availability.implemented && (
                <p className="muted">目录已收录，当前没有可执行适配器。</p>
              )}
            </article>
          ))}
      </div>
      <CommandFeedback {...command} />
      {view.data && (
        <SkillSources
          actor={actor}
          sources={view.data.sources}
          canSave={allowed("capabilities.catalog.sources.save")}
          canRefresh={allowed("capabilities.catalog.sources.refresh")}
          refresh={view.refresh}
        />
      )}
      <DetailCard title="插件与实际配置">
        <p>
          插件安装、版本与角色binding在插件中心管理；声音、图像、设备等凭据由各自设置页面管理。
        </p>
        <a className="button" href="#/plugins">
          打开插件中心
        </a>
      </DetailCard>
    </section>
  );
}
