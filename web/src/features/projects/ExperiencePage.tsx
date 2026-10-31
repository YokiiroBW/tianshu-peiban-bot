import { useState } from "react";
import { useProjectWorkspace } from "../knowledge/workspace";
import { ProjectHeader } from "../knowledge/ProjectHeader";
import { LessonPanel } from "./LessonPanel";
import { SharedExperience } from "./SharedExperience";
import { ContinuationPanel } from "./ContinuationPanel";
import "../knowledge/knowledge.css";
import "./experience.css";

/** Native project learning, explicit shared summaries, and actual checkout facts. */
export default function ExperiencePage() {
  const workspace = useProjectWorkspace();
  const [tab, setTab] = useState("lessons");
  return (
    <div className="experience-page">
      <ProjectHeader workspace={workspace} title="项目经验与续作" />
      <nav className="experience-tabs" aria-label="项目经验页面">
        {[
          ["lessons", "项目错题"],
          ["experiences", "共享经验"],
          ["continuation", "实际续作快照"],
        ].map(([id, label]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>
      {tab === "experiences" ? (
        <SharedExperience
          key={workspace.actor}
          actor={workspace.actor}
          canShare={workspace.view.data?.can_share ?? false}
        />
      ) : (
        workspace.project && (
          <div key={workspace.project.project_id}>
            {tab === "lessons" ? (
              <LessonPanel
                projectId={workspace.project.project_id}
                scope={workspace.scene}
                canWrite={workspace.view.data?.can_write ?? false}
              />
            ) : (
              <ContinuationPanel
                project={workspace.project}
                scope={workspace.scene}
                canWrite={workspace.view.data?.can_write ?? false}
              />
            )}
          </div>
        )
      )}
    </div>
  );
}
