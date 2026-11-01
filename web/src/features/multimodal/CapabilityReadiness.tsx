import { actorScope, useDomainResource } from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import type { SkillsCatalog } from "../settings/skills/types";
import { capabilityReason, stateOf } from "../settings/skills/presentation";

/** The same capability catalog drives settings, activity choices and readiness. */
export function CapabilityReadiness({
  ids,
  title,
}: {
  ids: string[];
  title: string;
}) {
  const { actor } = useRoleWorkspace();
  const view = useDomainResource<SkillsCatalog>(
    "capabilities/catalog/view",
    actorScope(actor),
    !!actor,
  );
  const items =
    view.data?.items.filter((item) => ids.includes(item.definition.id)) ?? [];
  if (!actor) return null;
  return (
    <section className="domain-record" aria-label={title}>
      <div className="section-heading">
        <h3>{title}</h3>
        <button
          type="button"
          className="text-link"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          读取能力状态
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && !view.data && (
        <p role="status">正在读取当前角色可用能力…</p>
      )}
      {items.map((item) => (
        <div key={item.definition.id}>
          <p>
            <strong>{item.definition.title}</strong> · {stateOf(item)}
          </p>
          {item.availability.reason && (
            <p>{capabilityReason(item.availability.reason)}</p>
          )}
          <p className="muted">
            最近实际验证：
            {item.availability.verified_at
              ? new Date(item.availability.verified_at).toLocaleString("zh-CN")
              : "尚无记录"}
          </p>
        </div>
      ))}
      {view.data && !items.length && (
        <p>当前目录尚未提供此能力的可用性记录。</p>
      )}
    </section>
  );
}
