import { useState, type ReactNode } from "react";
import { LifecyclePanel } from "./LifecyclePanel";
import { WardrobePanel } from "./WardrobePanel";
import { ReadingPanel } from "./ReadingPanel";
import { EpisodeTimeline } from "./EpisodeTimeline";
import { LifeRoutinePanel } from "./LifeRoutinePanel";
import ImagesPage from "../multimodal/ImagesPage";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
import type { LifeState } from "./domainTypes";
import { DayPlanPanel } from "./DayPlanPanel";
import { DiaryPanel } from "./DiaryPanel";
const tabs = [
  { id: "overview", label: "总览" },
  { id: "day", label: "今日安排" },
  { id: "goals", label: "长期目标" },
  { id: "now", label: "活动与进度" },
  { id: "diary", label: "日记" },
  { id: "memory", label: "经历与记忆" },
  { id: "wardrobe", label: "衣柜" },
  { id: "reading", label: "阅读与学习" },
  { id: "images", label: "生图与相册" },
  { id: "rhythm", label: "作息与联系" },
] as const;
export function LifeWorkspace({
  canManage,
  state,
  refresh,
  overview,
}: {
  canManage: boolean;
  state: LifeState | null;
  refresh: () => Promise<void>;
  overview: ReactNode;
}) {
  const [tab, setTab] = useState<(typeof tabs)[number]["id"]>(
    () =>
      tabs.find((item) => item.id === readRolePreference("tianshu-life-tab"))
        ?.id ?? "overview",
  );
  return (
    <section className="life-workspace">
      <div
        className="life-workspace-tabs"
        role="tablist"
        aria-label="角色生活工作区"
      >
        {tabs.map((item) => (
          <button
            className="button"
            role="tab"
            id={`life-tab-${item.id}`}
            aria-controls={`life-panel-${item.id}`}
            aria-selected={tab === item.id}
            key={item.id}
            onClick={() => {
              setTab(item.id);
              saveRolePreference("tianshu-life-tab", item.id);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`life-panel-${tab}`}
        aria-labelledby={`life-tab-${tab}`}
      >
        {tab === "overview" ? (
          overview
        ) : tab === "day" ? (
          <DayPlanPanel
            canManage={canManage}
            timezone={state?.timezone ?? "Asia/Shanghai"}
            refreshState={refresh}
          />
        ) : tab === "goals" ? (
          <LifecyclePanel
            canManage={canManage}
            refreshState={refresh}
            section="goals"
          />
        ) : tab === "now" ? (
          <LifecyclePanel
            canManage={canManage}
            refreshState={refresh}
            section="activities"
            onReviewDay={() => {
              setTab("day");
              saveRolePreference("tianshu-life-tab", "day");
            }}
          />
        ) : tab === "diary" ? (
          <DiaryPanel canManage={canManage} />
        ) : tab === "memory" ? (
          <EpisodeTimeline />
        ) : tab === "wardrobe" ? (
          <WardrobePanel
            canManage={canManage}
            state={state}
            refreshState={refresh}
          />
        ) : tab === "images" ? (
          <ImagesPage />
        ) : tab === "rhythm" ? (
          <LifeRoutinePanel
            canManage={canManage}
            state={state}
            refreshState={refresh}
          />
        ) : (
          <ReadingPanel canManage={canManage} />
        )}
      </div>
    </section>
  );
}
