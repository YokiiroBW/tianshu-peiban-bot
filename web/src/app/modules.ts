import { lazy } from "react";
import {
  BookOpen,
  CalendarDays,
  Boxes,
  Folder,
  House,
  LayoutDashboard,
  MessageCircle,
  UsersRound,
  Settings,
  Rss,
  Link,
  Download,
  UserRound,
  Music2,
  Activity,
  HardDrive,
  Puzzle,
  Sparkles,
  BookMarked,
  PenLine,
  SlidersHorizontal,
  ScrollText,
  ChartNoAxesCombined,
  ShieldCheck,
} from "lucide-react";

// Local navigation metadata, never a cross-product API or a connection-health registry.
export const modules = [
  {
    id: "workbench",
    label: "工作台",
    icon: LayoutDashboard,
    group: "个人空间",
    description: "关注事项与近期安排",
    sections: [],
  },
  {
    id: "companion",
    label: "角色",
    icon: MessageCircle,
    group: "个人空间",
    description: "文字对话与角色近况",
    sections: [
      "Web 聊天",
      "生活",
      "人格与世界",
      "角色管理",
      "日记书架",
      "创作书架",
      "角色设置",
      "小屋",
      "成长与手记",
      "约定与事项",
    ],
  },
  {
    id: "room",
    label: "小屋",
    icon: House,
    group: "个人空间",
    description: "小屋环境预览",
    sections: [],
  },
  {
    id: "memory",
    label: "用户",
    icon: UsersRound,
    group: "个人空间",
    description: "从一个人开始，了解彼此的共同经历",
    sections: ["用户档案", "群画像", "账号关联", "本人记忆"],
  },
  {
    id: "subscriptions",
    label: "订阅",
    icon: Rss,
    group: "个人空间",
    description: "关注视频、音乐、书籍和漫画，管理订阅和本地收藏",
    sections: [
      "视频订阅",
      "链接下载",
      "下载中心",
      "账号与媒体库",
      "概览",
      "音乐订阅",
      "运行状态",
      "存储空间",
      "书籍订阅",
      "漫画订阅",
      "表情图库",
    ],
  },
  {
    id: "resources",
    label: "资料与资源",
    icon: BookOpen,
    group: "工作空间",
    description: "研究资料、资产与归档",
    sections: ["研究资料", "资产库"],
  },
  {
    id: "home",
    label: "家庭与服务",
    icon: Boxes,
    group: "工作空间",
    description: "设备与容器的实际状态",
    sections: ["家庭设备", "容器", "节点与游戏服", "身体与活动"],
  },
  {
    id: "projects",
    label: "项目",
    icon: Folder,
    group: "工作空间",
    description: "任务、文档与交接",
    sections: ["项目工作区", "经验与交接"],
  },
  {
    id: "plugins",
    label: "插件",
    icon: Puzzle,
    group: "管理",
    description: "安装、配置和运行插件",
    sections: [],
  },
  {
    id: "logs",
    label: "运行日志",
    icon: ScrollText,
    group: "管理",
    description: "查找实际运行记录和采集覆盖",
    sections: [],
  },
  {
    id: "statistics",
    label: "系统统计",
    icon: ChartNoAxesCombined,
    group: "管理",
    description: "查看业务、模型与资源的实际统计",
    sections: [],
  },
  {
    id: "operations",
    label: "运维事件",
    icon: ShieldCheck,
    group: "管理",
    description: "查看故障、恢复与通知配置",
    sections: [],
  },
  {
    id: "settings",
    label: "任务与设置",
    icon: Settings,
    group: "管理",
    description: "任务记录与各项连接设置",
    sections: [
      "任务记录",
      "连接总览",
      "模型供应商",
      "机器人接入",
      "资产库接入",
      "家庭设备接入",
      "访问与域名",
      null, // Reserved: old administrator links now open the user directory.
      "角色技能",
      "位置与天气",
      "记忆检索",
      "节点连接",
      "链路图",
      "声音与语音",
      "图像引擎",
    ],
  },
] as const;

export type Module = (typeof modules)[number];
export type ModuleId = Module["id"];

export const userNavigation = [
  { section: 0, icon: UserRound, href: "#/people" },
  { section: 1, icon: UsersRound, href: "#/groups" },
  { section: 2, icon: Link, href: "#/people/accounts" },
  { section: 3, icon: BookOpen, href: "#/memory" },
] as const;

export const roleNavigation = [
  { section: 3, icon: UsersRound, href: "#/roles" },
  { section: 0, icon: MessageCircle, href: "#/roles/chat" },
  { section: 1, icon: Sparkles, href: "#/roles/life" },
  { section: 4, icon: BookMarked, href: "#/roles/diaries" },
  { section: 5, icon: PenLine, href: "#/roles/writing" },
  { section: 2, icon: BookOpen, href: "#/roles/persona" },
  { section: 6, icon: SlidersHorizontal, href: "#/roles/settings" },
  { section: 7, icon: House, href: "#/roles/room" },
  { section: 8, icon: Sparkles, href: "#/roles/growth" },
  { section: 9, icon: CalendarDays, href: "#/roles/commitments" },
] as const;

// Stable section indices preserve existing media bookmarks; only their display order differs.
export const subscriptionNavigation = [
  { section: 4, icon: LayoutDashboard, href: "#/media" },
  { section: 0, icon: Rss, href: "#/media/video" },
  { section: 5, icon: Music2, href: "#/media/music" },
  { section: 8, icon: BookOpen, href: "#/media/books" },
  { section: 9, icon: BookOpen, href: "#/media/comics" },
  { section: 1, icon: Link, href: "#/media/download" },
  { section: 2, icon: Download, href: "#/media/jobs" },
  { section: 3, icon: UserRound, href: "#/media/accounts" },
  { section: 6, icon: Activity, href: "#/media/runtime" },
  { section: 7, icon: HardDrive, href: "#/media/storage" },
  { section: 10, icon: Sparkles, href: "#/media/reactions" },
] as const;

// Room remains a separate chunk. Future modules register one lazy page here.
export const pages = {
  logs: lazy(() => import("../features/system-logs/LogsPage")),
  statistics: lazy(() => import("../features/system-statistics/StatisticsPage")),
  operations: lazy(() => import("../features/operations/OperationsPage")),
  speech: lazy(() => import("../features/multimodal/SpeechPage")),
  images: lazy(() => import("../features/multimodal/ImagesPage")),
  reactions: lazy(() => import("../features/multimodal/ReactionsPage")),
  growth: lazy(() => import("../features/life/GrowthPage")),
  commitments: lazy(() => import("../features/life/CommitmentsPage")),
  plugins: lazy(() => import("../features/plugins/PluginCenter")),
  companion: lazy(() => import("../features/companion/CompanionPage")),
  personas: lazy(() => import("../features/companion/PersonaPanel")),
  roles: lazy(() => import("../features/companion/RoleManager")),
  roleSettings: lazy(() => import("../features/companion/RoleSettings")),
  workbench: lazy(() => import("../features/workbench/WorkbenchPage")),
  room: lazy(() => import("../features/room/RoomPage")),
  settings: lazy(() => import("../features/settings/SettingsPage")),
  home: lazy(() => import("../features/home/HomePage")),
  infrastructure: lazy(
    () => import("../features/infrastructure/InfrastructurePage"),
  ),
  resources: lazy(() => import("../features/resources/ResourcesPage")),
  media: lazy(() => import("../features/media/MediaPage")),
  knowledge: lazy(() => import("../features/knowledge/KnowledgePage")),
  experience: lazy(() => import("../features/projects/ExperiencePage")),
  life: lazy(() => import("../features/life/LifePage")),
  memory: lazy(() => import("../features/memory/PeopleWorkspace")),
};

export const sectionPaths: Record<ModuleId, readonly string[]> = {
  "companion": [
    "roles/chat",
    "roles/life",
    "roles/persona",
    "roles",
    "roles/diaries",
    "roles/writing",
    "roles/settings",
    "roles/room",
    "roles/growth",
    "roles/commitments"
  ],
  "memory": [
    "people",
    "groups",
    "people/accounts",
    "memory"
  ],
  "subscriptions": [
    "media/video",
    "media/download",
    "media/jobs",
    "media/accounts",
    "media",
    "media/music",
    "media/runtime",
    "media/storage",
    "media/books",
    "media/comics",
    "media/reactions"
  ],
  "resources": [
    "resources/knowledge",
    "resources/assets"
  ],
  "home": [
    "home/devices",
    "system/containers",
    "system/nodes",
    "home/activity"
  ],
  "projects": [
    "projects/knowledge",
    "projects/experience"
  ],
  "settings": [
    "system/tasks",
    "settings/connections",
    "settings/models",
    "settings/channels",
    "settings/assets",
    "settings/home",
    "settings/access",
    "people",
    "settings/skills",
    "settings/weather",
    "settings/retrieval",
    "settings/nodes",
    "system/graph",
    "settings/speech",
    "settings/images"
  ],
  "workbench": [
    "workbench"
  ],
  "room": ["roles/room"],
  "plugins": [
    "plugins"
  ],
  "logs": ["system/logs"],
  "statistics": ["system/statistics"],
  "operations": ["system/operations"]
};

export function routeHref(module: ModuleId, section = 0) {
  return `#/${sectionPaths[module][section] ?? sectionPaths[module][0]}`;
}
export function resolveRoute(hash: string): { module: Module; section: number } | null {
  const path = hash.replace(/^#\/?/, "").split("?")[0] || "workbench";
  for (const module of modules) {
    if (module.id === "room") continue;
    const section = sectionPaths[module.id].indexOf(path);
    if (section >= 0) return { module, section };
  }
  return null;
}
