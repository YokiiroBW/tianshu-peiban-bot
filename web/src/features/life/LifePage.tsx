import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useDomainResource } from "../../api/useDomainResource";
import { StatePanel } from "../../components/StatePanel";
import { getShichenInfo } from "../../design/shichen";
import { DailyLife } from "./DailyLife";
import { LifeWorkspace } from "./LifeWorkspace";
import { WritingPanel } from "./WritingPanel";
import { DiaryPanel } from "./DiaryPanel";
import type { LifeAccessView, LifeState } from "./domainTypes";
import "./life.css";

export default function LifePage({
  mode = "life",
}: {
  mode?: "life" | "diary" | "writing";
}) {
  const workspace = useRoleWorkspace();
  const access = useDomainResource<LifeAccessView>(
    "life/access",
    {},
    !!workspace.actor,
    30000,
  );
  const state = useDomainResource<LifeState>(
    "life/state",
    {},
    !!workspace.actor && mode === "life" && access.data?.can_read === true,
    15000,
  );
  const scopeKey = `${workspace.actor}:${workspace.conversation?.conversation_id ?? ""}:${workspace.conversation?.person_id ?? ""}`;
  const name =
    workspace.roles.find((role) => role.id === workspace.actor)?.name ?? "角色";
  const shichen = getShichenInfo(new Date(), state.data?.timezone);

  if (!workspace.actor)
    return (
      <StatePanel kind="empty" title="先选择一位角色">
        <p>从侧栏打开角色的生活、日记与作品。</p>
      </StatePanel>
    );

  return (
    <div className="life-page scroll-page">
      <header className="page-top-bar role-section-heading">
        <div className="page-identity role-identity-title">
          <span className="role-seal-avatar role-context-avatar">
            {name.slice(0, 1)}
          </span>
          <div className="page-title-wrap">
            <h2>
              <span>
                {name}的
                {mode === "diary"
                  ? "日记"
                  : mode === "writing"
                    ? "创作"
                    : "生活"}
              </span>
              <span className="cinnabar-seal">
                印 ·{" "}
                {mode === "diary"
                  ? "日末手书"
                  : mode === "writing"
                    ? "文心雕龙"
                    : "生活卷宗"}
              </span>
              <span className="celadon-tag">
                {shichen.isDaytime ? "角色昼间" : "角色夜间"} ·{" "}
                {shichen.fullLabel}
              </span>
            </h2>
            <p className="page-title-meta">
              {mode === "life"
                ? "近况、计划与真实经历，也留一点想象的空间。"
                : mode === "diary"
                  ? "翻开日常里值得记住的一页。"
                  : "故事、灵感与尚未完成的篇章。"}
            </p>
          </div>
        </div>
        <button
          className="button curio-btn"
          onClick={() => {
            void access.refresh();
            void state.refresh();
          }}
        >
          ↻ 刷新
        </button>
      </header>
      {access.error && <p role="alert">{access.error}</p>}
      {state.error && <p role="alert">{state.error}</p>}
      {!access.data && !access.error && (
        <p role="status">正在读取当前角色的可用操作…</p>
      )}
      {access.data &&
        (mode === "diary" ? (
          <DiaryPanel key={scopeKey} canManage={access.data.can_manage} />
        ) : mode === "writing" ? (
          <WritingPanel key={scopeKey} canManage={access.data.can_manage} />
        ) : access.data.can_read ? (
          <LifeWorkspace
            key={scopeKey}
            canManage={access.data.can_manage}
            state={state.data}
            refresh={state.refresh}
            overview={
              state.data ? (
                <DailyLife state={state.data} />
              ) : (
                <p role="status">正在读取生活近况…</p>
              )
            }
          />
        ) : (
          <p>当前账号可阅读已发布内容，尚无生活状态读取权限。</p>
        ))}
    </div>
  );
}
