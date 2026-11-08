import { useState } from "react";
import { Settings2 } from "lucide-react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { ModelFunctionsPanel } from "../settings/ModelFunctionsPanel";
import { SkillsPanel } from "../settings/skills/SkillsPanel";
import { RoleSpeechSettings } from "../multimodal/RoleSpeechSettings";
export default function RoleSettings() {
  const workspace = useRoleWorkspace();
  const [tab, setTab] = useState<"models" | "skills" | "speech">("models");
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
            <p>设置角色的模型分工、声音与可用能力。</p>
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
          aria-selected={tab === "speech"}
          onClick={() => setTab("speech")}
        >
          声音与语音
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
      ) : tab === "speech" ? (
        <RoleSpeechSettings key={workspace.actor} actor={workspace.actor} />
      ) : tab === "skills" ? (
        <SkillsPanel key={workspace.actor} actorId={workspace.actor} />
      ) : (
        <ModelFunctionsPanel key={workspace.actor} actorId={workspace.actor} />
      )}
    </section>
  );
}
