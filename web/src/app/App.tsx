import { Suspense, useEffect, useRef, useState } from "react";
import {
  Menu,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  WifiOff,
} from "lucide-react";
import {
  modules,
  pages,
  resolveRoute,
  routeHref,
  subscriptionNavigation,
  userNavigation,
  roleNavigation,
} from "./modules";
import { RoleWorkspaceProvider, RolePicker } from "./RoleWorkspace";
import "./role-workspace.css";
import {
  AuthProvider,
  AccountPage,
  authRoute,
  loginHref,
  returnTarget,
  useAuth,
} from "./Auth";
import { Drawer } from "../components/Drawer";
import { PageBoundary } from "../components/PageBoundary";
import { CommandMonitor } from "../components/CommandMonitor";
import { StatePanel } from "../components/StatePanel";
import { UnavailablePage } from "../components/UnavailablePage";
import { BrandAstrolabeSvg } from "../components/curio/OrientalWidgets";
import { getShichenInfo } from "../design/shichen";
import {
  BUILTIN_THEME_PRESETS,
  applyThemePresetToDom,
  getAllThemePresets,
  registerThemePreset,
  removeCustomThemePreset,
  type ThemePresetDefinition,
} from "../design/themeRegistry";

type Preferences = {
  theme: "system" | "light" | "dark";
  preset: string;
  motion: boolean;
  transparency: boolean;
  sidebarCollapsed: boolean;
};

const defaults: Preferences = {
  theme: "system",
  preset: "curio-807",
  motion: false,
  transparency: false,
  sidebarCollapsed: false,
};

const SAMPLE_CUSTOM_THEME: ThemePresetDefinition = {
  id: "dunhuang-811",
  code: "8-11",
  name: "敦煌藻井 · 石绿赭金",
  subtitle: "莫高窟壁画矿物色 · 石绿丹砂与泥金飞天",
  seal: "敦",
  cornerStyle: "bracket",
  previewColors: {
    dayBg: "#f4ede0",
    dayCard: "#fbf7ef",
    dayAccent: "#2e6e5e",
    daySeal: "#c2492c",
    nightBg: "#161310",
    nightAccent: "#deb068",
  },
  tokens: {
    day: {
      "--bg-canvas": "#f4ede0",
      "--bg-sidebar": "#eadfcd",
      "--bg-card": "#fbf7ef",
      "--bg-card-alt": "#f3ebdc",
      "--bg-subtle": "#ede3d1",
      "--bg-elevated": "rgba(251, 247, 239, 0.95)",
      "--text-primary": "#241d17",
      "--text-secondary": "#594c40",
      "--text-muted": "#8a7b6c",
      "--accent": "#2e6e5e",
      "--accent-soft": "rgba(46, 110, 94, 0.12)",
      "--accent-border": "rgba(46, 110, 94, 0.36)",
      "--seal": "#c2492c",
      "--seal-soft": "rgba(194, 73, 44, 0.12)",
      "--seal-border": "rgba(194, 73, 44, 0.36)",
      "--amber": "#b57c24",
      "--amber-soft": "rgba(181, 124, 36, 0.14)",
      "--border-outer": "#c9b89c",
      "--border-inner": "#dfd2bc",
      "--corner-ornament": "#9c7a54",
      "--shadow-card": "0 4px 16px rgba(45, 34, 20, 0.06)",
      "--shadow-hover": "0 10px 26px rgba(46, 110, 94, 0.12)",
      "--canvas-glow":
        "radial-gradient(at 80% 10%, rgba(194, 73, 44, 0.07) 0px, transparent 50%)",
      "--radius-control": "3px",
      "--radius-panel": "4px",
      "--radius-shell": "4px",
    },
    night: {
      "--bg-canvas": "#161310",
      "--bg-sidebar": "#1d1814",
      "--bg-card": "#241e19",
      "--bg-card-alt": "#2b241e",
      "--bg-subtle": "#322a23",
      "--bg-elevated": "rgba(36, 30, 25, 0.95)",
      "--text-primary": "#f0e6d8",
      "--text-secondary": "#b5a592",
      "--text-muted": "#7d6f5f",
      "--accent": "#deb068",
      "--accent-soft": "rgba(222, 176, 104, 0.15)",
      "--accent-border": "rgba(222, 176, 104, 0.38)",
      "--seal": "#e06b4f",
      "--seal-soft": "rgba(224, 107, 79, 0.16)",
      "--seal-border": "rgba(224, 107, 79, 0.42)",
      "--amber": "#5bb5a2",
      "--amber-soft": "rgba(91, 181, 162, 0.15)",
      "--border-outer": "rgba(222, 176, 104, 0.30)",
      "--border-inner": "rgba(222, 176, 104, 0.14)",
      "--corner-ornament": "rgba(222, 176, 104, 0.62)",
      "--shadow-card": "0 10px 28px rgba(0, 0, 0, 0.52)",
      "--shadow-hover": "0 14px 36px rgba(0, 0, 0, 0.68)",
      "--canvas-glow":
        "radial-gradient(at 80% 10%, rgba(222, 176, 104, 0.09) 0px, transparent 50%)",
      "--radius-control": "3px",
      "--radius-panel": "4px",
      "--radius-shell": "4px",
    },
  },
};

function readPreferences(): Preferences {
  try {
    const saved = JSON.parse(
      localStorage.getItem("tianshu.appearance") ?? "null",
    );
    const normalizedTheme =
      saved?.theme === "day"
        ? "light"
        : saved?.theme === "night"
          ? "dark"
          : saved?.theme === "auto"
            ? "system"
            : saved?.theme;
    return {
      theme: ["system", "light", "dark"].includes(normalizedTheme)
        ? normalizedTheme
        : "system",
      preset:
        typeof saved?.preset === "string" && saved.preset.trim()
          ? saved.preset
          : "curio-807",
      motion: saved?.motion === true,
      transparency: saved?.transparency === true,
      sidebarCollapsed: saved?.sidebarCollapsed === true,
    };
  } catch {
    return defaults;
  }
}

export function App() {
  return (
    <AuthProvider>
      <RoleWorkspaceProvider>
        <CommandMonitor />
        <AppShell />
      </RoleWorkspaceProvider>
    </AuthProvider>
  );
}

function AppShell() {
  const auth = useAuth();
  const [hash, setHash] = useState(() => {
    if (
      new URLSearchParams(window.location.search).get("tianshu_oauth") ===
      "speech"
    ) {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search + "#/settings/speech",
      );
    }
    return window.location.hash;
  });
  const acceptedHash = useRef(window.location.hash);
  const [drawer, setDrawer] = useState<"navigation" | "appearance" | null>(
    null,
  );
  const [preferences, setPreferences] = useState(readPreferences);
  const [themeList, setThemeList] = useState<ThemePresetDefinition[]>(() =>
    getAllThemePresets(),
  );
  const [customJson, setCustomJson] = useState("");
  const [customNotice, setCustomNotice] = useState("");
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  const [offline, setOffline] = useState(!navigator.onLine);
  const [now, setNow] = useState(() => new Date());
  const main = useRef<HTMLElement>(null);
  const route = resolveRoute(hash);
  const current = route?.module;
  const section = route?.section ?? 0;
  const account = authRoute(hash);
  const accountTitle = hash.startsWith("#/setup") ? "首次设置" : "登录";
  const setupNeeded = auth.session?.initialized === false;
  const shichen = getShichenInfo(now);
  const activePreset =
    themeList.find((item) => item.id === preferences.preset) ??
    BUILTIN_THEME_PRESETS[0];

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    const syncThemes = () => setThemeList(getAllThemePresets());
    window.addEventListener("tianshu:themes-updated", syncThemes);
    return () => {
      clearInterval(timer);
      window.removeEventListener("tianshu:themes-updated", syncThemes);
    };
  }, []);

  useEffect(() => {
    if (auth.loading || auth.error || !auth.session) return;
    if (setupNeeded && !hash.startsWith("#/setup")) {
      window.location.hash = `#/setup?next=${encodeURIComponent(account ? returnTarget(hash) : hash || "#/workbench")}`;
    } else if (!auth.session.authenticated && !account) {
      window.location.hash = loginHref(hash);
    } else if (auth.session.authenticated && !setupNeeded && account) {
      window.location.hash = returnTarget(hash);
    }
  }, [auth.loading, auth.error, auth.session, setupNeeded, hash, account]);

  useEffect(() => {
    const change = () => {
      const next = window.location.hash;
      if (next !== acceptedHash.current) {
        const leaving = new CustomEvent("tianshu:before-route", {
          cancelable: true,
          detail: { from: acceptedHash.current, to: next },
        });
        if (!window.dispatchEvent(leaving)) {
          window.history.pushState(
            null,
            "",
            window.location.pathname +
              window.location.search +
              acceptedHash.current,
          );
          return;
        }
        acceptedHash.current = next;
      }
      setHash(next);
      setDrawer(null);
    };
    const online = () => setOffline(!navigator.onLine);
    window.addEventListener("hashchange", change);
    window.addEventListener("online", online);
    window.addEventListener("offline", online);
    return () => {
      window.removeEventListener("hashchange", change);
      window.removeEventListener("online", online);
      window.removeEventListener("offline", online);
    };
  }, []);

  useEffect(() => {
    document.title = `${account ? accountTitle : (current?.sections[section] ?? current?.label ?? "页面不存在")} · 天枢`;
    const frame = requestAnimationFrame(() =>
      main.current?.focus({ preventScroll: true }),
    );
    return () => cancelAnimationFrame(frame);
  }, [hash, current, section, account, accountTitle]);

  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolvedMode: "light" | "dark" =
        preferences.theme === "system"
          ? media.matches
            ? "dark"
            : "light"
          : preferences.theme;
      applyThemePresetToDom(preferences.preset, resolvedMode);
    };
    apply();
    document.documentElement.dataset.motion = preferences.motion
      ? "reduce"
      : "system";
    document.documentElement.dataset.transparency = preferences.transparency
      ? "reduce"
      : "system";
    try {
      localStorage.setItem("tianshu.appearance", JSON.stringify(preferences));
      setStorageUnavailable(false);
    } catch {
      setStorageUnavailable(true);
    }
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preferences, themeList]);

  if (
    auth.loading ||
    auth.error ||
    !auth.session?.authenticated ||
    setupNeeded ||
    account
  ) {
    return (
      <main className="auth-screen" id="main" ref={main} tabIndex={-1}>
        <div className="auth-entry curio-card">
          <header className="auth-brand">
            <span className="brand-symbol brand-emblem">
              <BrandAstrolabeSvg />
            </span>
            <h1>天枢</h1>
            <p>{activePreset.name} · 属于你的陪伴与生活空间</p>
          </header>
          <PageBoundary>
            <AccountPage />
          </PageBoundary>
          <p className="auth-footer">天枢 · 个人空间 · {shichen.caption}</p>
        </div>
      </main>
    );
  }

  const navigation = (
    <nav className="sidebar-scroll" aria-label="工作区导航">
      {["个人空间", "工作空间", "管理"].map((group) => (
        <div className="nav-group" key={group}>
          <p className="nav-label nav-section-label">{group}</p>
          {modules
            .filter((item) => item.group === group && item.id !== "room")
            .map((item) => {
              const children =
                item.id === "subscriptions"
                  ? subscriptionNavigation
                  : item.id === "companion"
                    ? roleNavigation
                    : item.id === "memory"
                      ? userNavigation
                      : [];
              return (
                <div key={item.id}>
                  <a
                    className="nav-item"
                    href={
                      item.id === "companion"
                        ? "#/roles"
                        : routeHref(
                            item.id,
                            item.id === "subscriptions" ? 4 : 0,
                          )
                    }
                    aria-label={item.label}
                    title={item.label}
                    aria-current={current?.id === item.id ? "page" : undefined}
                    onClick={() => setDrawer(null)}
                  >
                    <item.icon aria-hidden="true" />
                    <span>{item.label}</span>
                  </a>
                  {children.length > 0 && current?.id === item.id && (
                    <nav
                      className="nav-subsections subnav-tree"
                      aria-label={`${item.label}页面`}
                    >
                      {item.id === "companion" && <RolePicker />}
                      {children.map((child) => {
                        const childLabel =
                          item.sections[child.section] ?? item.label;
                        const isRoleLife =
                          item.id === "companion" && child.section === 1;
                        return (
                          <a
                            key={child.section}
                            className="subnav-link"
                            href={child.href}
                            aria-label={childLabel}
                            title={childLabel}
                            aria-current={
                              section === child.section ? "page" : undefined
                            }
                            onClick={() => setDrawer(null)}
                          >
                            <child.icon aria-hidden="true" />
                            <span>{childLabel}</span>
                            {isRoleLife && (
                              <small className="subnav-shichen">
                                {shichen.name}
                              </small>
                            )}
                          </a>
                        );
                      })}
                    </nav>
                  )}
                </div>
              );
            })}
        </div>
      ))}
    </nav>
  );

  let page;
  if (!current)
    page = (
      <StatePanel
        kind="empty"
        title="找不到这个页面"
        action={
          <a className="button" href="#/workbench">
            返回工作台
          </a>
        }
      >
        <p>这个入口不存在，或地址不完整。请从导航选择工作区。</p>
      </StatePanel>
    );
  else if (current.id === "workbench") page = <pages.workbench />;
  else if (current.id === "room") page = <pages.room />;
  else if (current.id === "companion" && section === 0)
    page = <pages.companion />;
  else if (current.id === "companion" && section === 2)
    page = <pages.personas />;
  else if (current.id === "companion" && section === 3) page = <pages.roles />;
  else if (current.id === "companion" && section === 1) page = <pages.life />;
  else if (current.id === "companion" && section === 4)
    page = <pages.life mode="diary" />;
  else if (current.id === "companion" && section === 5)
    page = <pages.life mode="writing" />;
  else if (current.id === "companion" && section === 6)
    page = <pages.roleSettings />;
  else if (current.id === "companion" && section === 7) page = <pages.room />;
  else if (current.id === "companion" && section === 8) page = <pages.growth />;
  else if (current.id === "companion" && section === 9)
    page = <pages.commitments />;
  else if (current.id === "home" && section === 0) page = <pages.home />;
  else if (current.id === "home" && (section === 1 || section === 2))
    page = <pages.infrastructure section={section} />;
  else if (current.id === "memory") page = <pages.memory section={section} />;
  else if (current.id === "resources" && section === 0)
    page = <pages.knowledge />;
  else if (current.id === "resources" && section === 1)
    page = <pages.resources />;
  else if (current.id === "subscriptions" && section === 10)
    page = <pages.reactions />;
  else if (current.id === "subscriptions")
    page = <pages.media section={section} />;
  else if (current.id === "projects" && section === 0)
    page = <pages.knowledge />;
  else if (current.id === "projects" && section === 1)
    page = <pages.experience />;
  else if (current.id === "plugins") page = <pages.plugins />;
  else if (current.id === "settings" && section === 13) page = <pages.speech />;
  else if (current.id === "settings" && section === 14)
    page = <pages.images settings />;
  else if (current.id === "settings")
    page = <pages.settings section={section} />;
  else page = <UnavailablePage module={current} section={section} />;

  return (
    <>
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          main.current?.focus();
        }}
      >
        跳到主要内容
      </a>
      <div
        className={`app-shell is-contained-page${preferences.sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}
      >
        <aside
          className={`sidebar glass curio-sidebar${current?.id === "subscriptions" || current?.id === "memory" || current?.id === "companion" ? " has-subnavigation" : ""}`}
        >
          <div className="sidebar-brand brand-header">
            <div className="brand-top-row">
              <a
                className="brand"
                href="#/workbench"
                aria-label="天枢工作台"
                title="天枢工作台"
              >
                <span className="brand-symbol brand-emblem">
                  <BrandAstrolabeSvg />
                </span>
                <span className="brand-copy">
                  <strong className="brand-title">天枢</strong>
                  <small className="brand-subtitle">
                    {activePreset.code} · {activePreset.name}
                  </small>
                </span>
              </a>
              <button
                className="icon-button sidebar-toggle"
                aria-label={
                  preferences.sidebarCollapsed ? "展开侧栏" : "收起侧栏"
                }
                title={preferences.sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
                aria-expanded={!preferences.sidebarCollapsed}
                onClick={() =>
                  setPreferences((previous) => ({
                    ...previous,
                    sidebarCollapsed: !previous.sidebarCollapsed,
                  }))
                }
              >
                {preferences.sidebarCollapsed ? (
                  <PanelLeftOpen aria-hidden="true" />
                ) : (
                  <PanelLeftClose aria-hidden="true" />
                )}
              </button>
            </div>

            {/* 昼夜模式切换槽 */}
            <div
              className="theme-switch-dock"
              role="group"
              aria-label="昼夜境切换"
            >
              <button
                type="button"
                className={`theme-btn${preferences.theme === "light" ? " active" : ""}`}
                onClick={() =>
                  setPreferences((prev) => ({ ...prev, theme: "light" }))
                }
                title="昼境（浅色）"
              >
                ☀️ 宣纸昼境
              </button>
              <button
                type="button"
                className={`theme-btn${preferences.theme === "dark" ? " active" : ""}`}
                onClick={() =>
                  setPreferences((prev) => ({ ...prev, theme: "dark" }))
                }
                title="夜境（深色）"
              >
                🌙 乌金夜境
              </button>
              <button
                type="button"
                className={`theme-btn${preferences.theme === "system" ? " active" : ""}`}
                onClick={() =>
                  setPreferences((prev) => ({ ...prev, theme: "system" }))
                }
                title="跟随系统环境"
              >
                ⏳ 自动
              </button>
            </div>

            {/* 八套主题意境快速切换栏 */}
            <div className="preset-quick-dock">
              <span className="preset-seal-badge" aria-hidden="true">
                {activePreset.seal}
              </span>
              <select
                className="preset-quick-select"
                aria-label="选择主题风格预设"
                value={preferences.preset}
                onChange={(event) =>
                  setPreferences((prev) => ({
                    ...prev,
                    preset: event.target.value,
                  }))
                }
              >
                {themeList.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.code} · {preset.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="preset-gallery-trigger"
                title="打开多宝主题画廊与扩展接口"
                onClick={() => setDrawer("appearance")}
              >
                画廊
              </button>
            </div>
          </div>

          {navigation}

          <div className="sidebar-note">
            <span className="celadon-tag">{shichen.caption}</span>
            <br />
            <span>各页面显示实际接入与回执状态</span>
          </div>
        </aside>

        <div className="main-column">
          <header className="topbar glass curio-topbar">
            <button
              className="icon-button mobile-menu"
              aria-label="打开导航"
              aria-haspopup="dialog"
              aria-expanded={drawer === "navigation"}
              onClick={() => setDrawer("navigation")}
            >
              <Menu aria-hidden="true" />
            </button>
            <p className="breadcrumb">
              <span className="breadcrumb-seal" aria-hidden="true">
                {activePreset.seal}
              </span>
              <span>天枢</span>
              <span aria-hidden="true">/</span>
              <strong>
                {account
                  ? accountTitle
                  : current?.id === "companion"
                    ? `角色 / ${current.sections[section]}`
                    : (current?.label ?? "页面不存在")}
              </strong>
            </p>
            <button
              type="button"
              className="celadon-tag topbar-preset-chip"
              onClick={() => setDrawer("appearance")}
              title="点击切换八套东方主题或扩展新主题"
            >
              🎨 {activePreset.code} · {activePreset.name}
            </button>
            <span className="celadon-tag topbar-shichen-tag">
              {shichen.fullLabel} · {shichen.poeticMood}
            </span>
            <div className="account-control">
              {auth.session?.authenticated ? (
                <>
                  <span className="account-username">
                    {auth.session.username}
                  </span>
                  <button className="button" onClick={() => void auth.logout()}>
                    退出登录
                  </button>
                </>
              ) : (
                <a
                  className="button"
                  href={setupNeeded ? "#/setup" : loginHref(hash)}
                >
                  {setupNeeded ? "首次设置" : "登录"}
                </a>
              )}
            </div>
            <button
              className="icon-button"
              aria-label="外观设置"
              aria-haspopup="dialog"
              onClick={() => setDrawer("appearance")}
            >
              <Palette aria-hidden="true" />
            </button>
          </header>
          {offline && (
            <div className="offline-notice" role="status">
              <WifiOff aria-hidden="true" />
              浏览器当前离线。已加载的页面仍可浏览。
            </div>
          )}
          <main id="main" ref={main} tabIndex={-1}>
            <h1 className="sr-only">
              {current?.id === "subscriptions" || current?.id === "companion"
                ? current.sections[section]
                : account
                  ? accountTitle
                  : (current?.label ?? "页面不存在")}
            </h1>
            {current &&
              current.id !== "subscriptions" &&
              current.id !== "memory" &&
              current.id !== "companion" &&
              current.sections.length > 0 && (
                <nav
                  className="section-nav curio-section-nav"
                  aria-label={`${current.label}页面`}
                >
                  {current.sections.map((label, index) =>
                    label === null ? null : (
                      <a
                        key={label}
                        href={routeHref(current.id, index)}
                        aria-current={section === index ? "page" : undefined}
                      >
                        {label}
                      </a>
                    ),
                  )}
                </nav>
              )}
            <div
              key={hash}
              className="page-content"
              role="region"
              aria-label={`${current?.label ?? "页面"}内容`}
              tabIndex={0}
            >
              <PageBoundary>
                <Suspense
                  fallback={
                    <StatePanel kind="loading" title="正在展卷读取">
                      <p>请稍候。</p>
                    </StatePanel>
                  }
                >
                  {page}
                </Suspense>
              </PageBoundary>
            </div>
          </main>
          <footer>
            天枢 · 多宝博古个人空间<span>服务状态以各页面真实回执为准</span>
          </footer>
        </div>
      </div>

      <Drawer
        title="导航"
        open={drawer === "navigation"}
        onClose={() => setDrawer(null)}
      >
        {navigation}
      </Drawer>

      <Drawer
        title="外观设置 · 多宝主题画廊"
        open={drawer === "appearance"}
        onClose={() => setDrawer(null)}
      >
        <p className="muted">
          内置 8 套无冲突东方美学意境，每套均支持「宣纸昼境 /
          乌金夜境」双模切换，并提供零重构插件化扩展接口。
        </p>

        {/* 1. 昼夜模式选择 */}
        <fieldset className="appearance-options">
          <legend>昼夜境流转</legend>
          {(["system", "light", "dark"] as const).map((value, index) => (
            <label key={value}>
              <input
                type="radio"
                name="theme"
                value={value}
                checked={preferences.theme === value}
                onChange={() =>
                  setPreferences({ ...preferences, theme: value })
                }
              />
              {
                [
                  "跟随系统（自动流转）",
                  "☀️ 昼境（浅色宣纸系）",
                  "🌙 夜境（深色大漆系）",
                ][index]
              }
            </label>
          ))}
        </fieldset>

        {/* 2. 多宝主题画廊（8 套内置 + 自定义扩展） */}
        <div className="theme-gallery-section">
          <div className="theme-gallery-head">
            <strong>意境主题画廊（共 {themeList.length} 套）</strong>
            <span className="celadon-tag">点选即时切换</span>
          </div>
          <div className="theme-gallery-grid">
            {themeList.map((preset) => {
              const selected = preferences.preset === preset.id;
              const isCustom = !BUILTIN_THEME_PRESETS.some(
                (b) => b.id === preset.id,
              );
              return (
                <div
                  key={preset.id}
                  className={`theme-gallery-card${selected ? " is-selected" : ""}`}
                >
                  <button
                    type="button"
                    className="theme-gallery-card-btn"
                    aria-pressed={selected}
                    onClick={() =>
                      setPreferences((prev) => ({
                        ...prev,
                        preset: preset.id,
                      }))
                    }
                  >
                    <div className="theme-gallery-top">
                      <span
                        className="theme-gallery-seal"
                        style={{
                          background: preset.previewColors.daySeal,
                          color: "#faf8f2",
                        }}
                      >
                        {preset.seal}
                      </span>
                      <div className="theme-gallery-titles">
                        <strong>
                          {preset.code} · {preset.name}
                        </strong>
                        <small>{preset.subtitle}</small>
                      </div>
                    </div>
                    <div
                      className="theme-swatch-row"
                      aria-hidden="true"
                      title="昼境底色 / 昼境主色 / 朱砂印 / 夜境底色 / 夜境高亮"
                    >
                      <span
                        style={{ background: preset.previewColors.dayBg }}
                      />
                      <span
                        style={{ background: preset.previewColors.dayCard }}
                      />
                      <span
                        style={{ background: preset.previewColors.dayAccent }}
                      />
                      <span
                        style={{ background: preset.previewColors.daySeal }}
                      />
                      <span
                        style={{ background: preset.previewColors.nightBg }}
                      />
                      <span
                        style={{ background: preset.previewColors.nightAccent }}
                      />
                    </div>
                  </button>
                  {isCustom && (
                    <button
                      type="button"
                      className="ink-link theme-remove-btn"
                      onClick={() => {
                        removeCustomThemePreset(preset.id);
                        if (preferences.preset === preset.id) {
                          setPreferences((prev) => ({
                            ...prev,
                            preset: "curio-807",
                          }));
                        }
                      }}
                    >
                      移除此扩展主题
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* 3. 免重构插件化主题扩展接口 */}
        <details className="theme-plugin-box">
          <summary className="ink-link">
            ➕ 免重构扩展新主题（JSON 插件接口 / window.__TIANSHU_THEMES__）
          </summary>
          <div className="theme-plugin-body">
            <p className="muted">
              后期新增主题无需修改任何业务组件源码：既可在{" "}
              <code>src/design/themeRegistry.ts</code>{" "}
              中追加配置对象，也可直接在此粘贴或通过{" "}
              <code>window.__TIANSHU_THEMES__.register(json)</code> 动态注入。
            </p>
            <div className="domain-actions">
              <button
                type="button"
                className="button curio-btn"
                onClick={() => {
                  registerThemePreset(SAMPLE_CUSTOM_THEME);
                  setPreferences((prev) => ({
                    ...prev,
                    preset: SAMPLE_CUSTOM_THEME.id,
                  }));
                  setCustomNotice(
                    "已动态注入并启用「8-11 敦煌藻井 · 石绿赭金」新主题！",
                  );
                }}
              >
                ✨ 一键体验注入第 9 套示例主题（8-11 敦煌藻井）
              </button>
              <button
                type="button"
                className="button curio-btn"
                onClick={() => {
                  setCustomJson(JSON.stringify(activePreset, null, 2));
                  setCustomNotice(
                    `已导出「${activePreset.name}」JSON 模板，修改 id/name/颜色后点击下方按钮即可新增！`,
                  );
                }}
              >
                📋 导出当前主题 JSON 作为模板
              </button>
            </div>
            <label className="theme-plugin-label">
              自定义主题 JSON 定义
              <textarea
                rows={7}
                value={customJson}
                onChange={(event) => setCustomJson(event.target.value)}
                placeholder='{"id": "my-theme-812", "code": "8-12", "name": "新主题名称", ...}'
              />
            </label>
            <button
              type="button"
              className="button primary curio-btn"
              disabled={!customJson.trim()}
              onClick={() => {
                try {
                  const parsed = JSON.parse(customJson);
                  registerThemePreset(parsed);
                  setPreferences((prev) => ({ ...prev, preset: parsed.id }));
                  setCustomNotice(`成功注册并启用主题：${parsed.name}`);
                  setCustomJson("");
                } catch (err) {
                  setCustomNotice(
                    err instanceof Error ? err.message : "JSON 格式解析失败",
                  );
                }
              }}
            >
              注入并应用自定义主题
            </button>
            {customNotice && (
              <p role="status" className="celadon-tag">
                {customNotice}
              </p>
            )}
          </div>
        </details>

        <label className="preference-row">
          <span>
            减少动态<small>系统要求减少动态时始终生效</small>
          </span>
          <input
            type="checkbox"
            checked={preferences.motion}
            onChange={(event) =>
              setPreferences({ ...preferences, motion: event.target.checked })
            }
          />
        </label>
        <label className="preference-row">
          <span>
            减少透明度<small>使用更实的宣纸与大漆浮层背景</small>
          </span>
          <input
            type="checkbox"
            checked={preferences.transparency}
            onChange={(event) =>
              setPreferences({
                ...preferences,
                transparency: event.target.checked,
              })
            }
          />
        </label>
        {storageUnavailable && (
          <p role="status">浏览器未允许保存偏好，本次页面内仍然有效。</p>
        )}
      </Drawer>
    </>
  );
}
