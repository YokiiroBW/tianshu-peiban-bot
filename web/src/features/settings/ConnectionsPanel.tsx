import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { actorScope, useDomainResource } from "../../api/useDomainResource";
import type { SkillsCatalog } from "./skills/types";
import { capabilityReason, settings, stateOf } from "./skills/presentation";

export function ConnectionsPanel() {
  const { actor } = useRoleWorkspace();
  const [filter, setFilter] = useState("");
  const view = useDomainResource<SkillsCatalog>(
    "capabilities/catalog/view",
    actorScope(actor),
    !!actor,
  );
  return (
    <section className="panel connections domain-workspace">
      <header className="section-heading">
        <h2>功能连接</h2>
        <button
          className="button"
          disabled={!actor || view.loading}
          onClick={() => void view.refresh()}
        >
          读取当前能力
        </button>
      </header>
      <p>
        角色可用能力来自同一目录。配置、对端支持、实际验证和当前执行权限分别显示。
      </p>
      {!actor && <p>先选择一位角色。</p>}
      {view.error && <p role="alert">{view.error}</p>}
      <label>
        查找能力
        <input
          type="search"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </label>
      {view.data && (
        <ul className="connection-list">
          {view.data.items
            .filter((item) =>
              `${item.definition.id} ${item.definition.title}`
                .toLocaleLowerCase()
                .includes(filter.toLocaleLowerCase()),
            )
            .map((item) => (
              <li key={item.definition.id}>
                <div>
                  <h3>{item.definition.title}</h3>
                  <p>{item.definition.description}</p>
                  <code>{item.definition.id}</code>
                  {item.availability.reason && (
                    <p>{capabilityReason(item.availability.reason)}</p>
                  )}
                  <p>
                    对端支持：
                    {item.availability.provider_supported === null
                      ? "未知"
                      : item.availability.provider_supported
                        ? "支持"
                        : "不支持"}{" "}
                    · 最近实际验证：
                    {item.availability.verified_at
                      ? new Date(item.availability.verified_at).toLocaleString(
                          "zh-CN",
                        )
                      : "尚无记录"}
                  </p>
                  {settings[item.definition.id.split(".")[0]] && (
                    <a
                      className="text-link"
                      href={settings[item.definition.id.split(".")[0]]}
                    >
                      配置所属服务
                    </a>
                  )}
                </div>
                <span className="badge">{stateOf(item)}</span>
              </li>
            ))}
        </ul>
      )}
      <a className="text-link" href="#/settings/skills">
        管理逐角色启停与能力来源
      </a>
    </section>
  );
}
