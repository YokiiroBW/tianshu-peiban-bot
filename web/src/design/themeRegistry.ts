/**
 * 天枢 (Tianshu) · 零重构插件化多主题引擎 (Pluggable Multi-Theme Registry)
 * =========================================================================
 * 设计目标：
 * 1. 将「页面骨骼组件」与「视觉意境令牌 (Design Tokens)」100% 解耦；
 * 2. 内置从先前方案中精选出的 8 套无布局冲突东方人文主题，每套均具备完整「☀️ 昼境 / 🌙 夜境」双模式；
 * 3. 提供标准化的声明式主题接口 `ThemePresetDefinition` 与运行时注册 API：
 *    - 后期新增主题只需在 `BUILTIN_THEME_PRESETS` 中追加一段纯 JSON 配置，或通过
 *      `window.__TIANSHU_THEMES__.register(themeJson)` / 外观面板「导入主题 JSON」动态注入，
 *      永远不需要再次重构任何业务组件源码。
 */

export interface ThemeTokenSet {
  "--bg-canvas": string;
  "--bg-sidebar": string;
  "--bg-card": string;
  "--bg-card-alt": string;
  "--bg-subtle": string;
  "--bg-elevated": string;
  "--text-primary": string;
  "--text-secondary": string;
  "--text-muted": string;
  "--accent": string;
  "--accent-soft": string;
  "--accent-border": string;
  "--seal": string;
  "--seal-soft": string;
  "--seal-border": string;
  "--amber": string;
  "--amber-soft": string;
  "--border-outer": string;
  "--border-inner": string;
  "--corner-ornament": string;
  "--shadow-card": string;
  "--shadow-hover": string;
  "--canvas-glow": string;
  "--radius-control"?: string;
  "--radius-panel"?: string;
  "--radius-shell"?: string;
  /** 兼容特殊深色侧栏主题（如 8-10 紫宸天枢：昼境下侧栏依然为玄色大漆） */
  "--sidebar-text-primary"?: string;
  "--sidebar-text-secondary"?: string;
  "--sidebar-text-muted"?: string;
  "--sidebar-card-bg"?: string;
  "--sidebar-subtle-bg"?: string;
  "--sidebar-border"?: string;
}

export interface ThemePresetDefinition {
  /** 唯一主题 ID，例如 "curio-807" */
  id: string;
  /** 方案编号展示铭牌，例如 "8-07" */
  code: string;
  /** 主题中文名 */
  name: string;
  /** 意境副标题 */
  subtitle: string;
  /** 篆印单字 */
  seal: string;
  /** 卡片折角样式：bracket=宋式折角花线, soft=温润圆角, crisp=碑刻方框, glass=琉璃玉框 */
  cornerStyle: "bracket" | "soft" | "crisp" | "glass";
  /** 预览色板（用于主题画廊卡片直观展示） */
  previewColors: {
    dayBg: string;
    dayCard: string;
    dayAccent: string;
    daySeal: string;
    nightBg: string;
    nightAccent: string;
  };
  /** 昼夜双境完整 CSS 变量表 */
  tokens: {
    day: ThemeTokenSet;
    night: ThemeTokenSet;
  };
}

/**
 * 精选 8 套完全兼容、无布局冲突的东方美学双境主题集
 */
export const BUILTIN_THEME_PRESETS: ThemePresetDefinition[] = [
  // 1. 方案 8-07：多宝博古 · 官窑粉青（默认旗舰款）
  {
    id: "curio-807",
    code: "8-07",
    name: "多宝博古 · 官窑粉青",
    subtitle: "宋式格架规整列阵 · 宣纸粉青与乌金大漆双境",
    seal: "博",
    cornerStyle: "bracket",
    previewColors: {
      dayBg: "#f3efe4",
      dayCard: "#faf8f2",
      dayAccent: "#355e58",
      daySeal: "#b3422c",
      nightBg: "#121514",
      nightAccent: "#d4a85f",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f3efe4",
        "--bg-sidebar": "#eae4d5",
        "--bg-card": "#faf8f2",
        "--bg-card-alt": "#f4f0e6",
        "--bg-subtle": "#eee9dc",
        "--bg-elevated": "rgba(250, 248, 242, 0.94)",
        "--text-primary": "#1f2421",
        "--text-secondary": "#4e5651",
        "--text-muted": "#7b827c",
        "--accent": "#355e58",
        "--accent-soft": "rgba(53, 94, 88, 0.11)",
        "--accent-border": "rgba(53, 94, 88, 0.34)",
        "--seal": "#b3422c",
        "--seal-soft": "rgba(179, 66, 44, 0.11)",
        "--seal-border": "rgba(179, 66, 44, 0.34)",
        "--amber": "#9c6b26",
        "--amber-soft": "rgba(156, 107, 38, 0.12)",
        "--border-outer": "#c2b9a5",
        "--border-inner": "#dcd5c4",
        "--corner-ornament": "#8d826c",
        "--shadow-card": "0 3px 12px rgba(45, 40, 30, 0.045)",
        "--shadow-hover": "0 8px 24px rgba(45, 40, 30, 0.09)",
        "--canvas-glow":
          "radial-gradient(circle at 80% 8%, rgba(179, 66, 44, 0.045), transparent 45%)",
        "--radius-control": "2px",
        "--radius-panel": "2px",
        "--radius-shell": "2px",
      },
      night: {
        "--bg-canvas": "#121514",
        "--bg-sidebar": "#171b1a",
        "--bg-card": "#1c2120",
        "--bg-card-alt": "#212726",
        "--bg-subtle": "#252c2a",
        "--bg-elevated": "rgba(28, 33, 32, 0.95)",
        "--text-primary": "#eae4d7",
        "--text-secondary": "#a8a295",
        "--text-muted": "#706c63",
        "--accent": "#d4a85f",
        "--accent-soft": "rgba(212, 168, 95, 0.14)",
        "--accent-border": "rgba(212, 168, 95, 0.38)",
        "--seal": "#d96b52",
        "--seal-soft": "rgba(217, 107, 82, 0.16)",
        "--seal-border": "rgba(217, 107, 82, 0.42)",
        "--amber": "#e2b86b",
        "--amber-soft": "rgba(226, 184, 107, 0.15)",
        "--border-outer": "rgba(212, 168, 95, 0.32)",
        "--border-inner": "rgba(212, 168, 95, 0.14)",
        "--corner-ornament": "rgba(212, 168, 95, 0.62)",
        "--shadow-card": "0 8px 28px rgba(0, 0, 0, 0.45)",
        "--shadow-hover": "0 12px 36px rgba(0, 0, 0, 0.65)",
        "--canvas-glow":
          "radial-gradient(circle at 80% 8%, rgba(212, 168, 95, 0.08), transparent 50%)",
        "--radius-control": "2px",
        "--radius-panel": "2px",
        "--radius-shell": "2px",
      },
    },
  },

  // 2. 方案 8-01：汝窑天青 · 宋韵折枝
  {
    id: "ruyao-801",
    code: "8-01",
    name: "汝窑天青 · 宋韵折枝",
    subtitle: "雨过天青云破处 · 温润汝瓷与柔光画卷",
    seal: "汝",
    cornerStyle: "soft",
    previewColors: {
      dayBg: "#f6f4ec",
      dayCard: "#fcfbf7",
      dayAccent: "#3b6e6c",
      daySeal: "#c44a32",
      nightBg: "#111818",
      nightAccent: "#68b0ab",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f6f4ec",
        "--bg-sidebar": "#efece2",
        "--bg-card": "#fcfbf7",
        "--bg-card-alt": "#f5f2e8",
        "--bg-subtle": "#f1eee4",
        "--bg-elevated": "rgba(252, 251, 247, 0.94)",
        "--text-primary": "#1e2421",
        "--text-secondary": "#4e5853",
        "--text-muted": "#828c86",
        "--accent": "#3b6e6c",
        "--accent-soft": "rgba(59, 110, 108, 0.11)",
        "--accent-border": "rgba(59, 110, 108, 0.32)",
        "--seal": "#c44a32",
        "--seal-soft": "rgba(196, 74, 50, 0.11)",
        "--seal-border": "rgba(196, 74, 50, 0.32)",
        "--amber": "#b08d57",
        "--amber-soft": "rgba(176, 141, 87, 0.14)",
        "--border-outer": "#d6d0c0",
        "--border-inner": "#e6e1d3",
        "--corner-ornament": "#86a3a1",
        "--shadow-card": "0 8px 24px -8px rgba(30, 36, 33, 0.06)",
        "--shadow-hover": "0 14px 32px -8px rgba(59, 110, 108, 0.14)",
        "--canvas-glow":
          "radial-gradient(at 85% 8%, rgba(59, 110, 108, 0.09) 0px, transparent 48%), radial-gradient(at 12% 88%, rgba(196, 74, 50, 0.06) 0px, transparent 45%)",
        "--radius-control": "6px",
        "--radius-panel": "8px",
        "--radius-shell": "8px",
      },
      night: {
        "--bg-canvas": "#111818",
        "--bg-sidebar": "#151e1e",
        "--bg-card": "#1b2626",
        "--bg-card-alt": "#202d2d",
        "--bg-subtle": "#243333",
        "--bg-elevated": "rgba(27, 38, 38, 0.95)",
        "--text-primary": "#e8f0ef",
        "--text-secondary": "#9eb2b0",
        "--text-muted": "#687d7b",
        "--accent": "#68b0ab",
        "--accent-soft": "rgba(104, 176, 171, 0.15)",
        "--accent-border": "rgba(104, 176, 171, 0.38)",
        "--seal": "#e07a64",
        "--seal-soft": "rgba(224, 122, 100, 0.16)",
        "--seal-border": "rgba(224, 122, 100, 0.38)",
        "--amber": "#d8b074",
        "--amber-soft": "rgba(216, 176, 116, 0.15)",
        "--border-outer": "rgba(104, 176, 171, 0.28)",
        "--border-inner": "rgba(104, 176, 171, 0.14)",
        "--corner-ornament": "rgba(104, 176, 171, 0.55)",
        "--shadow-card": "0 10px 28px rgba(0, 0, 0, 0.45)",
        "--shadow-hover": "0 14px 36px rgba(0, 0, 0, 0.62)",
        "--canvas-glow":
          "radial-gradient(at 80% 10%, rgba(104, 176, 171, 0.10) 0px, transparent 50%)",
        "--radius-control": "6px",
        "--radius-panel": "8px",
        "--radius-shell": "8px",
      },
    },
  },

  // 3. 方案 8-02：曲水流觞 · 梅子青竹
  {
    id: "meizi-802",
    code: "8-02",
    name: "曲水流觞 · 梅子青竹",
    subtitle: "龙泉梅子青釉 · 兰亭修竹与清润屏风",
    seal: "竹",
    cornerStyle: "soft",
    previewColors: {
      dayBg: "#f1f5ef",
      dayCard: "#fafcf9",
      dayAccent: "#366351",
      daySeal: "#b55338",
      nightBg: "#111714",
      nightAccent: "#72b896",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f1f5ef",
        "--bg-sidebar": "#e6ede4",
        "--bg-card": "#fafcf9",
        "--bg-card-alt": "#f2f6f0",
        "--bg-subtle": "#eaf0e8",
        "--bg-elevated": "rgba(250, 252, 249, 0.94)",
        "--text-primary": "#1c2621",
        "--text-secondary": "#485950",
        "--text-muted": "#788a80",
        "--accent": "#366351",
        "--accent-soft": "rgba(54, 99, 81, 0.11)",
        "--accent-border": "rgba(54, 99, 81, 0.34)",
        "--seal": "#b55338",
        "--seal-soft": "rgba(181, 83, 56, 0.11)",
        "--seal-border": "rgba(181, 83, 56, 0.34)",
        "--amber": "#9e7332",
        "--amber-soft": "rgba(158, 115, 50, 0.12)",
        "--border-outer": "#c5d4c9",
        "--border-inner": "#dbe5dd",
        "--corner-ornament": "#6f9685",
        "--shadow-card": "0 6px 20px rgba(28, 45, 36, 0.05)",
        "--shadow-hover": "0 12px 28px rgba(54, 99, 81, 0.12)",
        "--canvas-glow":
          "radial-gradient(at 90% 10%, rgba(56, 102, 82, 0.09) 0px, transparent 50%), radial-gradient(at 10% 85%, rgba(180, 130, 75, 0.07) 0px, transparent 48%)",
        "--radius-control": "6px",
        "--radius-panel": "10px",
        "--radius-shell": "10px",
      },
      night: {
        "--bg-canvas": "#111714",
        "--bg-sidebar": "#151d19",
        "--bg-card": "#1b241f",
        "--bg-card-alt": "#202b25",
        "--bg-subtle": "#24322b",
        "--bg-elevated": "rgba(27, 36, 31, 0.95)",
        "--text-primary": "#e5efe9",
        "--text-secondary": "#9cb0a5",
        "--text-muted": "#677a70",
        "--accent": "#72b896",
        "--accent-soft": "rgba(114, 184, 150, 0.15)",
        "--accent-border": "rgba(114, 184, 150, 0.38)",
        "--seal": "#dc7d60",
        "--seal-soft": "rgba(220, 125, 96, 0.16)",
        "--seal-border": "rgba(220, 125, 96, 0.38)",
        "--amber": "#dbb26b",
        "--amber-soft": "rgba(219, 178, 107, 0.15)",
        "--border-outer": "rgba(114, 184, 150, 0.28)",
        "--border-inner": "rgba(114, 184, 150, 0.14)",
        "--corner-ornament": "rgba(114, 184, 150, 0.55)",
        "--shadow-card": "0 10px 28px rgba(0, 0, 0, 0.48)",
        "--shadow-hover": "0 14px 36px rgba(0, 0, 0, 0.65)",
        "--canvas-glow":
          "radial-gradient(at 85% 10%, rgba(114, 184, 150, 0.09) 0px, transparent 50%)",
        "--radius-control": "6px",
        "--radius-panel": "10px",
        "--radius-shell": "10px",
      },
    },
  },

  // 4. 方案 8-03：钦天星盘 · 霁蓝鎏金
  {
    id: "jilan-803",
    code: "8-03",
    name: "钦天星盘 · 霁蓝鎏金",
    subtitle: "宣德霁蓝釉与浑仪铜轨 · 观星台典藏格架",
    seal: "星",
    cornerStyle: "bracket",
    previewColors: {
      dayBg: "#f2f1eb",
      dayCard: "#fbfaf5",
      dayAccent: "#2c5566",
      daySeal: "#b87d3b",
      nightBg: "#0f151b",
      nightAccent: "#67a9c7",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f2f1eb",
        "--bg-sidebar": "#e7e5dc",
        "--bg-card": "#fbfaf5",
        "--bg-card-alt": "#f3f1e9",
        "--bg-subtle": "#eceae0",
        "--bg-elevated": "rgba(251, 250, 245, 0.94)",
        "--text-primary": "#1b2329",
        "--text-secondary": "#4a5761",
        "--text-muted": "#7a8791",
        "--accent": "#2c5566",
        "--accent-soft": "rgba(44, 85, 102, 0.11)",
        "--accent-border": "rgba(44, 85, 102, 0.35)",
        "--seal": "#b87d3b",
        "--seal-soft": "rgba(184, 125, 59, 0.12)",
        "--seal-border": "rgba(184, 125, 59, 0.36)",
        "--amber": "#b34832",
        "--amber-soft": "rgba(179, 72, 50, 0.12)",
        "--border-outer": "#c4c0b3",
        "--border-inner": "#dad6ca",
        "--corner-ornament": "#6c8694",
        "--shadow-card": "0 4px 16px rgba(27, 35, 41, 0.05)",
        "--shadow-hover": "0 10px 26px rgba(44, 85, 102, 0.11)",
        "--canvas-glow":
          "radial-gradient(at 50% 0%, rgba(44, 78, 94, 0.10) 0px, transparent 55%)",
        "--radius-control": "4px",
        "--radius-panel": "5px",
        "--radius-shell": "5px",
      },
      night: {
        "--bg-canvas": "#0f151b",
        "--bg-sidebar": "#131b23",
        "--bg-card": "#18222c",
        "--bg-card-alt": "#1d2935",
        "--bg-subtle": "#22303e",
        "--bg-elevated": "rgba(24, 34, 44, 0.95)",
        "--text-primary": "#e6edf2",
        "--text-secondary": "#9babb8",
        "--text-muted": "#667684",
        "--accent": "#67a9c7",
        "--accent-soft": "rgba(103, 169, 199, 0.15)",
        "--accent-border": "rgba(103, 169, 199, 0.38)",
        "--seal": "#e0a858",
        "--seal-soft": "rgba(224, 168, 88, 0.16)",
        "--seal-border": "rgba(224, 168, 88, 0.42)",
        "--amber": "#de735b",
        "--amber-soft": "rgba(222, 115, 91, 0.15)",
        "--border-outer": "rgba(103, 169, 199, 0.28)",
        "--border-inner": "rgba(103, 169, 199, 0.14)",
        "--corner-ornament": "rgba(224, 168, 88, 0.58)",
        "--shadow-card": "0 10px 30px rgba(0, 0, 0, 0.52)",
        "--shadow-hover": "0 14px 38px rgba(0, 0, 0, 0.68)",
        "--canvas-glow":
          "radial-gradient(at 50% 0%, rgba(103, 169, 199, 0.12) 0px, transparent 55%)",
        "--radius-control": "4px",
        "--radius-panel": "5px",
        "--radius-shell": "5px",
      },
    },
  },

  // 5. 方案 8-05：经折手卷 · 书院檀褐
  {
    id: "tanmu-805",
    code: "8-05",
    name: "经折手卷 · 书院檀褐",
    subtitle: "古籍善本与紫檀书架 · 碑刻方框与朱砂藏书印",
    seal: "檀",
    cornerStyle: "crisp",
    previewColors: {
      dayBg: "#f2ede2",
      dayCard: "#faf7f0",
      dayAccent: "#5c4938",
      daySeal: "#c03f2b",
      nightBg: "#171411",
      nightAccent: "#cda072",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f2ede2",
        "--bg-sidebar": "#e6dfd1",
        "--bg-card": "#faf7f0",
        "--bg-card-alt": "#f3ede1",
        "--bg-subtle": "#ede6d8",
        "--bg-elevated": "rgba(250, 247, 240, 0.95)",
        "--text-primary": "#261f19",
        "--text-secondary": "#5c5046",
        "--text-muted": "#8c7f73",
        "--accent": "#5c4938",
        "--accent-soft": "rgba(92, 73, 56, 0.11)",
        "--accent-border": "rgba(92, 73, 56, 0.36)",
        "--seal": "#c03f2b",
        "--seal-soft": "rgba(192, 63, 43, 0.11)",
        "--seal-border": "rgba(192, 63, 43, 0.36)",
        "--amber": "#a66c24",
        "--amber-soft": "rgba(166, 108, 36, 0.12)",
        "--border-outer": "#c7bba6",
        "--border-inner": "#ddd4c3",
        "--corner-ornament": "#8c755d",
        "--shadow-card": "0 3px 12px rgba(46, 34, 22, 0.05)",
        "--shadow-hover": "0 8px 24px rgba(46, 34, 22, 0.10)",
        "--canvas-glow":
          "radial-gradient(at 80% 10%, rgba(92, 73, 56, 0.06) 0px, transparent 50%)",
        "--radius-control": "2px",
        "--radius-panel": "3px",
        "--radius-shell": "3px",
      },
      night: {
        "--bg-canvas": "#171411",
        "--bg-sidebar": "#1e1a16",
        "--bg-card": "#25201b",
        "--bg-card-alt": "#2b2520",
        "--bg-subtle": "#322b25",
        "--bg-elevated": "rgba(37, 32, 27, 0.95)",
        "--text-primary": "#ede5dc",
        "--text-secondary": "#b0a396",
        "--text-muted": "#7a6e63",
        "--accent": "#cda072",
        "--accent-soft": "rgba(205, 160, 114, 0.15)",
        "--accent-border": "rgba(205, 160, 114, 0.38)",
        "--seal": "#de6852",
        "--seal-soft": "rgba(222, 104, 82, 0.16)",
        "--seal-border": "rgba(222, 104, 82, 0.40)",
        "--amber": "#e0b265",
        "--amber-soft": "rgba(224, 178, 101, 0.15)",
        "--border-outer": "rgba(205, 160, 114, 0.28)",
        "--border-inner": "rgba(205, 160, 114, 0.14)",
        "--corner-ornament": "rgba(205, 160, 114, 0.55)",
        "--shadow-card": "0 10px 28px rgba(0, 0, 0, 0.50)",
        "--shadow-hover": "0 14px 36px rgba(0, 0, 0, 0.68)",
        "--canvas-glow":
          "radial-gradient(at 80% 10%, rgba(205, 160, 114, 0.09) 0px, transparent 50%)",
        "--radius-control": "2px",
        "--radius-panel": "3px",
        "--radius-shell": "3px",
      },
    },
  },

  // 6. 方案 8-06：冰裂青玉 · 琉璃半透
  {
    id: "binglie-806",
    code: "8-06",
    name: "冰裂青玉 · 琉璃半透",
    subtitle: "南宋官窑冰裂纹与半透青玉 · 清透呼吸感",
    seal: "玉",
    cornerStyle: "glass",
    previewColors: {
      dayBg: "#eaf1ef",
      dayCard: "#fcfffe",
      dayAccent: "#2d7a73",
      daySeal: "#c85a32",
      nightBg: "#0e1615",
      nightAccent: "#56bdb3",
    },
    tokens: {
      day: {
        "--bg-canvas": "#eaf1ef",
        "--bg-sidebar": "rgba(235, 244, 241, 0.86)",
        "--bg-card": "rgba(252, 255, 254, 0.86)",
        "--bg-card-alt": "rgba(241, 248, 246, 0.88)",
        "--bg-subtle": "rgba(230, 240, 237, 0.85)",
        "--bg-elevated": "rgba(252, 255, 254, 0.92)",
        "--text-primary": "#162624",
        "--text-secondary": "#425c58",
        "--text-muted": "#73918d",
        "--accent": "#2d7a73",
        "--accent-soft": "rgba(45, 122, 115, 0.12)",
        "--accent-border": "rgba(45, 122, 115, 0.34)",
        "--seal": "#c85a32",
        "--seal-soft": "rgba(200, 90, 50, 0.12)",
        "--seal-border": "rgba(200, 90, 50, 0.34)",
        "--amber": "#b37c2e",
        "--amber-soft": "rgba(179, 124, 46, 0.13)",
        "--border-outer": "#bdd1cc",
        "--border-inner": "#d5e4e0",
        "--corner-ornament": "#649e98",
        "--shadow-card":
          "0 10px 28px -8px rgba(26, 60, 56, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.9)",
        "--shadow-hover": "0 16px 36px -8px rgba(45, 122, 115, 0.15)",
        "--canvas-glow":
          "radial-gradient(at 15% 15%, rgba(69, 140, 131, 0.16) 0px, transparent 50%), radial-gradient(at 85% 20%, rgba(143, 188, 179, 0.18) 0px, transparent 52%)",
        "--radius-control": "8px",
        "--radius-panel": "12px",
        "--radius-shell": "12px",
      },
      night: {
        "--bg-canvas": "#0e1615",
        "--bg-sidebar": "rgba(18, 28, 27, 0.90)",
        "--bg-card": "rgba(23, 36, 34, 0.88)",
        "--bg-card-alt": "rgba(28, 43, 41, 0.90)",
        "--bg-subtle": "rgba(32, 49, 47, 0.85)",
        "--bg-elevated": "rgba(23, 36, 34, 0.95)",
        "--text-primary": "#e6f4f2",
        "--text-secondary": "#9ab8b4",
        "--text-muted": "#62827e",
        "--accent": "#56bdb3",
        "--accent-soft": "rgba(86, 189, 179, 0.15)",
        "--accent-border": "rgba(86, 189, 179, 0.38)",
        "--seal": "#e57c59",
        "--seal-soft": "rgba(229, 124, 89, 0.16)",
        "--seal-border": "rgba(229, 124, 89, 0.40)",
        "--amber": "#e2b568",
        "--amber-soft": "rgba(226, 181, 104, 0.15)",
        "--border-outer": "rgba(86, 189, 179, 0.30)",
        "--border-inner": "rgba(86, 189, 179, 0.15)",
        "--corner-ornament": "rgba(86, 189, 179, 0.58)",
        "--shadow-card": "0 12px 32px rgba(0, 0, 0, 0.50)",
        "--shadow-hover": "0 16px 40px rgba(0, 0, 0, 0.68)",
        "--canvas-glow":
          "radial-gradient(at 20% 15%, rgba(86, 189, 179, 0.12) 0px, transparent 50%)",
        "--radius-control": "8px",
        "--radius-panel": "12px",
        "--radius-shell": "12px",
      },
    },
  },

  // 7. 方案 8-09：水墨丹青 · 宣白飞白
  {
    id: "shuimo-809",
    code: "8-09",
    name: "水墨丹青 · 宣白飞白",
    subtitle: "徽墨焦浓重淡清 · 纯净生宣与朱砂钤印",
    seal: "墨",
    cornerStyle: "crisp",
    previewColors: {
      dayBg: "#f5f5f2",
      dayCard: "#ffffff",
      dayAccent: "#2b3330",
      daySeal: "#c83e2b",
      nightBg: "#121313",
      nightAccent: "#d4d9d6",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f5f5f2",
        "--bg-sidebar": "#ecece7",
        "--bg-card": "#ffffff",
        "--bg-card-alt": "#f7f7f4",
        "--bg-subtle": "#f0f0eb",
        "--bg-elevated": "rgba(255, 255, 255, 0.95)",
        "--text-primary": "#181a19",
        "--text-secondary": "#4a4e4c",
        "--text-muted": "#858a88",
        "--accent": "#2b3330",
        "--accent-soft": "rgba(43, 51, 48, 0.09)",
        "--accent-border": "rgba(43, 51, 48, 0.32)",
        "--seal": "#c83e2b",
        "--seal-soft": "rgba(200, 62, 43, 0.11)",
        "--seal-border": "rgba(200, 62, 43, 0.36)",
        "--amber": "#966d2c",
        "--amber-soft": "rgba(150, 109, 44, 0.12)",
        "--border-outer": "#cfcfc8",
        "--border-inner": "#e2e2dc",
        "--corner-ornament": "#6c7370",
        "--shadow-card": "0 4px 16px rgba(24, 26, 25, 0.04)",
        "--shadow-hover": "0 10px 26px rgba(24, 26, 25, 0.08)",
        "--canvas-glow":
          "radial-gradient(at 92% 5%, rgba(30, 35, 34, 0.05) 0px, transparent 45%), radial-gradient(at 10% 90%, rgba(196, 58, 43, 0.04) 0px, transparent 45%)",
        "--radius-control": "4px",
        "--radius-panel": "6px",
        "--radius-shell": "6px",
      },
      night: {
        "--bg-canvas": "#121313",
        "--bg-sidebar": "#171918",
        "--bg-card": "#1d201f",
        "--bg-card-alt": "#222625",
        "--bg-subtle": "#272b2a",
        "--bg-elevated": "rgba(29, 32, 31, 0.95)",
        "--text-primary": "#eceeed",
        "--text-secondary": "#a2a8a5",
        "--text-muted": "#6c7370",
        "--accent": "#d4d9d6",
        "--accent-soft": "rgba(212, 217, 214, 0.13)",
        "--accent-border": "rgba(212, 217, 214, 0.34)",
        "--seal": "#e05a47",
        "--seal-soft": "rgba(224, 90, 71, 0.16)",
        "--seal-border": "rgba(224, 90, 71, 0.42)",
        "--amber": "#dbb065",
        "--amber-soft": "rgba(219, 176, 101, 0.15)",
        "--border-outer": "rgba(212, 217, 214, 0.24)",
        "--border-inner": "rgba(212, 217, 214, 0.12)",
        "--corner-ornament": "rgba(224, 90, 71, 0.55)",
        "--shadow-card": "0 10px 28px rgba(0, 0, 0, 0.52)",
        "--shadow-hover": "0 14px 36px rgba(0, 0, 0, 0.68)",
        "--canvas-glow":
          "radial-gradient(at 85% 10%, rgba(224, 90, 71, 0.07) 0px, transparent 50%)",
        "--radius-control": "4px",
        "--radius-panel": "6px",
        "--radius-shell": "6px",
      },
    },
  },

  // 8. 方案 8-10：紫宸天枢 · 玄栏宣卷
  {
    id: "zichen-810",
    code: "8-10",
    name: "紫宸天枢 · 玄栏宣卷",
    subtitle: "玄漆描金立柜配温润熟宣主卷 · 殿堂级明暗对比",
    seal: "宸",
    cornerStyle: "bracket",
    previewColors: {
      dayBg: "#f5f2e9",
      dayCard: "#fcfbf7",
      dayAccent: "#366b68",
      daySeal: "#c64b32",
      nightBg: "#121514",
      nightAccent: "#d4a85f",
    },
    tokens: {
      day: {
        "--bg-canvas": "#f5f2e9",
        "--bg-sidebar": "#19201e",
        "--bg-card": "#fcfbf7",
        "--bg-card-alt": "#f4f1e6",
        "--bg-subtle": "#efeae0",
        "--bg-elevated": "rgba(252, 251, 247, 0.95)",
        "--text-primary": "#1e2421",
        "--text-secondary": "#4e5853",
        "--text-muted": "#7e8782",
        "--accent": "#366b68",
        "--accent-soft": "rgba(54, 107, 104, 0.11)",
        "--accent-border": "rgba(54, 107, 104, 0.34)",
        "--seal": "#c64b32",
        "--seal-soft": "rgba(198, 75, 50, 0.11)",
        "--seal-border": "rgba(198, 75, 50, 0.35)",
        "--amber": "#a67329",
        "--amber-soft": "rgba(166, 115, 41, 0.12)",
        "--border-outer": "#c9c2b0",
        "--border-inner": "#dfd9ca",
        "--corner-ornament": "#877d67",
        "--shadow-card": "0 4px 16px rgba(30, 36, 33, 0.05)",
        "--shadow-hover": "0 10px 26px rgba(30, 36, 33, 0.10)",
        "--canvas-glow":
          "radial-gradient(at 80% 10%, rgba(54, 107, 104, 0.07) 0px, transparent 50%)",
        "--radius-control": "4px",
        "--radius-panel": "6px",
        "--radius-shell": "6px",
        "--sidebar-text-primary": "#eae4d7",
        "--sidebar-text-secondary": "#a8a295",
        "--sidebar-text-muted": "#77736a",
        "--sidebar-card-bg": "#232b29",
        "--sidebar-subtle-bg": "#202725",
        "--sidebar-border": "rgba(212, 168, 95, 0.28)",
      },
      night: {
        "--bg-canvas": "#121514",
        "--bg-sidebar": "#151918",
        "--bg-card": "#1c2120",
        "--bg-card-alt": "#212726",
        "--bg-subtle": "#252c2a",
        "--bg-elevated": "rgba(28, 33, 32, 0.95)",
        "--text-primary": "#eae4d7",
        "--text-secondary": "#a8a295",
        "--text-muted": "#706c63",
        "--accent": "#d4a85f",
        "--accent-soft": "rgba(212, 168, 95, 0.14)",
        "--accent-border": "rgba(212, 168, 95, 0.38)",
        "--seal": "#d96b52",
        "--seal-soft": "rgba(217, 107, 82, 0.16)",
        "--seal-border": "rgba(217, 107, 82, 0.42)",
        "--amber": "#e2b86b",
        "--amber-soft": "rgba(226, 184, 107, 0.15)",
        "--border-outer": "rgba(212, 168, 95, 0.32)",
        "--border-inner": "rgba(212, 168, 95, 0.14)",
        "--corner-ornament": "rgba(212, 168, 95, 0.62)",
        "--shadow-card": "0 8px 28px rgba(0, 0, 0, 0.45)",
        "--shadow-hover": "0 12px 36px rgba(0, 0, 0, 0.65)",
        "--canvas-glow":
          "radial-gradient(circle at 80% 8%, rgba(212, 168, 95, 0.08), transparent 50%)",
        "--radius-control": "4px",
        "--radius-panel": "6px",
        "--radius-shell": "6px",
      },
    },
  },
];

const CUSTOM_THEMES_STORAGE_KEY = "tianshu.customThemes.v1";
const optionalTokenKeys = [
  "--radius-control",
  "--radius-panel",
  "--radius-shell",
  "--sidebar-text-primary",
  "--sidebar-text-secondary",
  "--sidebar-text-muted",
  "--sidebar-card-bg",
  "--sidebar-subtle-bg",
  "--sidebar-border",
];
const requiredTokenKeys = Object.keys(
  BUILTIN_THEME_PRESETS[0].tokens.day,
).filter((key) => !optionalTokenKeys.includes(key));
const themeTokenKeys = new Set([...requiredTokenKeys, ...optionalTokenKeys]);
const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const isText = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

function isTokenSet(value: unknown): boolean {
  return (
    isRecord(value) &&
    requiredTokenKeys.every((key) => isText(value[key])) &&
    Object.entries(value).every(
      ([key, token]) => themeTokenKeys.has(key) && isText(token),
    )
  );
}

/**
 * 读取用户或插件动态注册的自定义主题列表
 */
export function loadCustomThemePresets(): ThemePresetDefinition[] {
  try {
    const raw = localStorage.getItem(CUSTOM_THEMES_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidThemePreset);
  } catch {
    return [];
  }
}

export function isValidThemePreset(
  candidate: unknown,
): candidate is ThemePresetDefinition {
  if (!isRecord(candidate)) return false;
  const obj = candidate;
  return (
    ["id", "code", "name", "subtitle", "seal"].every((key) =>
      isText(obj[key]),
    ) &&
    ["bracket", "soft", "crisp", "glass"].includes(String(obj.cornerStyle)) &&
    isRecord(obj.previewColors) &&
    Object.keys(BUILTIN_THEME_PRESETS[0].previewColors).every((key) =>
      isText((obj.previewColors as Record<string, unknown>)[key]),
    ) &&
    isRecord(obj.tokens) &&
    isTokenSet(obj.tokens.day) &&
    isTokenSet(obj.tokens.night)
  );
}

/**
 * 获取全部可用主题（内置 8 套 + 动态扩展主题）
 */
export function getAllThemePresets(): ThemePresetDefinition[] {
  const custom = loadCustomThemePresets();
  const map = new Map<string, ThemePresetDefinition>();
  for (const preset of BUILTIN_THEME_PRESETS) {
    map.set(preset.id, preset);
  }
  for (const preset of custom) {
    map.set(preset.id, preset);
  }
  return Array.from(map.values());
}

/**
 * 动态注册或更新一套新主题（无需重构任何源码）
 */
export function registerThemePreset(
  preset: ThemePresetDefinition,
): ThemePresetDefinition[] {
  if (!isValidThemePreset(preset)) {
    throw new Error(
      "主题定义格式不完整：请参考示例，填写名称、预览色板及完整的昼夜主题变量。",
    );
  }
  const custom = loadCustomThemePresets().filter(
    (item) => item.id !== preset.id,
  );
  custom.push(preset);
  try {
    localStorage.setItem(CUSTOM_THEMES_STORAGE_KEY, JSON.stringify(custom));
  } catch {
    // ignore storage quota errors
  }
  window.dispatchEvent(
    new CustomEvent("tianshu:themes-updated", { detail: { preset } }),
  );
  return getAllThemePresets();
}

/**
 * 移除一套自定义主题
 */
export function removeCustomThemePreset(id: string): ThemePresetDefinition[] {
  const custom = loadCustomThemePresets().filter((item) => item.id !== id);
  try {
    localStorage.setItem(CUSTOM_THEMES_STORAGE_KEY, JSON.stringify(custom));
  } catch {
    // ignore
  }
  window.dispatchEvent(
    new CustomEvent("tianshu:themes-updated", { detail: { removedId: id } }),
  );
  return getAllThemePresets();
}

/**
 * 将指定主题预设与昼夜模式（light/dark）应用到 document.documentElement
 */
export function applyThemePresetToDom(
  presetId: string,
  resolvedMode: "light" | "dark",
): ThemePresetDefinition {
  const all = getAllThemePresets();
  const preset =
    all.find((item) => item.id === presetId) ?? BUILTIN_THEME_PRESETS[0];
  const root = document.documentElement;
  root.dataset.theme = resolvedMode;
  root.dataset.preset = preset.id;
  root.dataset.cornerStyle = preset.cornerStyle;

  const tokenSet =
    resolvedMode === "dark" ? preset.tokens.night : preset.tokens.day;

  // 可选圆角和侧栏变量不能从上一套主题泄漏到下一套主题。
  for (const key of themeTokenKeys) {
    root.style.removeProperty(key);
  }

  for (const [key, value] of Object.entries(tokenSet)) {
    if (typeof value === "string") {
      root.style.setProperty(key, value);
    }
  }

  return preset;
}

// 挂载全局免编译主题扩展网关 window.__TIANSHU_THEMES__
declare global {
  interface Window {
    __TIANSHU_THEMES__?: {
      list: () => ThemePresetDefinition[];
      register: (preset: ThemePresetDefinition) => ThemePresetDefinition[];
      remove: (id: string) => ThemePresetDefinition[];
      apply: (
        presetId: string,
        mode?: "light" | "dark",
      ) => ThemePresetDefinition;
    };
  }
}

if (typeof window !== "undefined") {
  window.__TIANSHU_THEMES__ = {
    list: getAllThemePresets,
    register: registerThemePreset,
    remove: removeCustomThemePreset,
    apply: (presetId: string, mode: "light" | "dark" = "light") =>
      applyThemePresetToDom(presetId, mode),
  };
}
