import { ProviderModelsPanel } from "./ProviderModelsPanel";
import { TasksPanel } from "./TasksPanel";
import { AccessPanel } from "./AccessPanel";
import { ConnectionsPanel } from "./ConnectionsPanel";
import { ExternalConnectionsPanel } from "./ExternalConnectionsPanel";
import { BotAdapterPanel } from "./BotAdapterPanel";
import { SkillsPanel } from "./skills/SkillsPanel";
import { WeatherPanel } from "./WeatherPanel";
import "./settings.css";
import { RetrievalSettings } from "../memory/RetrievalSettings";
import { NodeConnections } from "../infrastructure/NodeConnections";
import { lazy, Suspense } from "react";

const FlowGraphPage = lazy(() => import("../flow-graph/FlowGraphPage"));

const setupLinks = [
  {
    href: "#/settings/speech",
    title: "声音与语音",
    detail: "连接 Fish Plus 或原生 API，绑定角色音色、试听和管理读音。",
  },
  {
    href: "#/settings/images",
    title: "图像引擎",
    detail: "配置 ComfyUI、NovelAI、GPT Image 和角色参考。",
  },
  {
    href: "#/system/graph",
    title: "链路图",
    detail: "查看账号、角色、会话关系，以及真实运行关联事件。",
  },
  {
    href: "#/settings/nodes",
    title: "节点连接",
    detail: "从已登记目录选择 NAS、Linux 与 OpenWrt 节点，查看后台采集状态。",
  },
  {
    href: "#/settings/retrieval",
    title: "记忆检索",
    detail: "选择关键词、向量或混合检索，配置嵌入与重排序模型。",
  },
  {
    href: "#/settings/models",
    title: "模型供应商",
    detail: "配置模型与用量，检查回复能力。",
  },
  {
    href: "#/settings/channels",
    title: "机器人接入",
    detail: "选择平台与插件地址，检测机器人并管理允许范围。",
  },
  {
    href: "#/settings/assets",
    title: "资产库接入",
    detail: "管理 AssetLink 地址与凭据，执行只读检测。",
  },
  {
    href: "#/settings/home",
    title: "家庭设备接入",
    detail: "管理 Home Assistant 地址、实体与凭据。",
  },
  {
    href: "#/settings/access",
    title: "访问与域名",
    detail: "设置网页访问地址与 HTTPS。",
  },
  {
    href: "#/settings/skills",
    title: "角色技能",
    detail: "查看可用能力、技能来源与逐角色启停设置。",
  },
  {
    href: "#/settings/weather",
    title: "位置与天气",
    detail: "配置天气连接，为角色选择天气位置和显示时区。",
  },
];

function ConnectionsOverview() {
  return (
    <div className="settings-overview">
      <section
        className="panel settings-directory"
        aria-labelledby="settings-directory-title"
      >
        <div className="section-heading">
          <h2 id="settings-directory-title">连接设置入口</h2>
        </div>
        <p>
          按要配置的服务进入独立页面。下方目录显示所选角色的真实配置、对端支持、验证与执行权限。
        </p>
        <div className="settings-directory-grid">
          {setupLinks.map((link) => (
            <a
              className="settings-directory-link"
              href={link.href}
              key={link.href}
            >
              <strong>{link.title}</strong>
              <span>{link.detail}</span>
            </a>
          ))}
        </div>
      </section>
      <ConnectionsPanel />
    </div>
  );
}

export default function SettingsPage({ section }: { section: number }) {
  if (section === 12)
    return (
      <Suspense fallback={<p role="status">正在载入链路图…</p>}>
        <FlowGraphPage />
      </Suspense>
    );
  if (section === 11) return <NodeConnections />;
  if (section === 10) return <RetrievalSettings />;
  if (section === 0) return <TasksPanel />;
  if (section === 1) return <ConnectionsOverview />;
  if (section === 2) return <ProviderModelsPanel />;
  if (section === 3) return <BotAdapterPanel />;
  if (section === 4)
    return <ExternalConnectionsPanel key="assets" kind="assets" />;
  if (section === 5) return <ExternalConnectionsPanel key="home" kind="home" />;
  if (section === 8) return <SkillsPanel />;
  if (section === 9) return <WeatherPanel />;
  return <AccessPanel />;
}
