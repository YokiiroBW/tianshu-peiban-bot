import { useState } from "react";
import { Settings2 } from "lucide-react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { ModelFunctionsPanel } from "../settings/ModelFunctionsPanel";
import { SkillsPanel } from "../settings/skills/SkillsPanel";
export default function RoleSettings() {
  const workspace = useRoleWorkspace();
  const [tab, setTab] = useState<"models" | "skills">("models");
  return (
    <section className="role-settings-page scroll-page">
      <header className="role-section-heading">
        <div className="role-identity-title">
          <span className="role-context-avatar">
            <Settings2 />
          </span>
          <div>
            <h2>
              {workspace.roles.find((role) => role.id === workspace.actor)
                ?.name ?? "选择角色"}
              的设置
            </h2>
            <p>角色功能模型绑定与统一能力目录。</p>
          </div>
        </div>
      </header>
      <nav className="role-settings-tabs" aria-label="角色设置分类">
        <button
          aria-selected={tab === "models"}
          onClick={() => setTab("models")}
        >
          模型分工
        </button>
        <button
          aria-selected={tab === "skills"}
          onClick={() => setTab("skills")}
        >
          角色能力
        </button>
        <a className="button" href="#/roles/persona">
          人格与世界
        </a>
        <a className="button" href="#/roles">
          基本资料
        </a>
      </nav>
      {!workspace.actor ? (
        <p>先选择一位角色。</p>
      ) : tab === "skills" ? (
        <SkillsPanel key={workspace.actor} actorId={workspace.actor} />
      ) : (
        <ModelFunctionsPanel key={workspace.actor} actorId={workspace.actor} />
      )}
    </section>
  );
}
