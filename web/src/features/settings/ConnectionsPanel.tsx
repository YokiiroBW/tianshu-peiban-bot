import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";

type Connection = {
  id: string;
  state:
    | "connected"
    | "unverified"
    | "not_configured"
    | "unauthorized"
    | "unavailable"
    | "not_implemented";
  code: string;
  detail: string | null;
  checked_at: string | null;
};
type View = { connections: Connection[] };

const labels: Record<string, { name: string; href: string; note: string }> = {
  dialogue: { name: "文字对话", href: "#/roles/chat", note: "会话和模型回复" },
  providers: {
    name: "模型供应商",
    href: "#/settings/models",
    note: "供应商配置与短回复测试",
  },
  personas: {
    name: "人格",
    href: "#/roles/persona",
    note: "角色目录与已发布版本",
  },
  knowledge: {
    name: "项目知识",
    href: "#/resources/knowledge",
    note: "研究资料与项目目录",
  },
  life: {
    name: "角色生活与日记",
    href: "#/roles/life",
    note: "持久化生活与已发布日记",
  },
  assets: { name: "资产库", href: "#/resources/assets", note: "受管资产元数据" },
  home: { name: "家庭设备", href: "#/home/devices", note: "设备读数与受限控制" },
  tasks: { name: "任务记录", href: "#/system/tasks", note: "平台已记录的操作" },
  memory_profiles: {
    name: "人物画像",
    href: "#/groups",
    note: "人物与共同经历",
  },
};
const stateLabels: Record<
  Connection["state"],
  { tone: "blue" | "yellow" | "red" | "gray"; label: string }
> = {
  connected: { tone: "blue", label: "已实际读取" },
  unverified: { tone: "yellow", label: "尚未验证" },
  not_configured: { tone: "gray", label: "未配置" },
  unauthorized: { tone: "red", label: "未授权" },
  unavailable: { tone: "red", label: "暂时不可用" },
  not_implemented: { tone: "gray", label: "尚未提供" },
};

function nextStep(row: Connection) {
  switch (row.state) {
    case "connected":
      return "已有真实业务读取记录。到对应页面查看本次内容和时间。";
    case "unverified":
      return "打开对应页面实际读取一次，再返回刷新状态。";
    case "not_configured":
      return row.id === "providers"
        ? "到模型与用量配置外部供应商并执行短回复测试。"
        : "请部署管理员在服务端登记此能力及权限；网页无需填写内置服务地址或令牌。";
    case "unauthorized":
      return "请部署管理员核对当前账号与服务身份的读取授权。";
    case "unavailable":
      return "到对应页面重试读取；若仍失败，请将状态码交给部署管理员排查。";
    case "not_implemented":
      return "当前没有可用的浏览器功能，无需在连接设置中寻找地址或令牌。";
  }
}

export function ConnectionsPanel() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const current = useRef<AbortController | null>(null);

  async function load() {
    if (!csrf) return;
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    setBusy(true);
    setError("");
    setView(null);
    try {
      const answer = await integrationPost<View>(
        "connections/view",
        {},
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setView(answer);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    return () => current.current?.abort();
  }, [csrf]);

  return (
    <section className="panel connections" aria-label="功能连接状态">
      <div className="section-heading">
        <h2>功能连接</h2>
        <button className="button" onClick={() => void load()} disabled={busy}>
          <RefreshCw aria-hidden="true" />
          刷新状态
        </button>
      </div>
      <p>
        状态来自部署配置与最近一次真实业务读取。进入对应页面可触发读取，再返回刷新；容器运行本身不代表功能已连接。
      </p>
      {busy && (
        <StatePanel kind="loading" title="正在读取连接状态">
          <p>请稍候。</p>
        </StatePanel>
      )}
      {error && (
        <StatePanel
          kind="error"
          title="连接状态无法读取"
          action={
            <button className="button" onClick={() => void load()}>
              重新读取
            </button>
          }
        >
          <p>{error}</p>
        </StatePanel>
      )}
      {view && (
        <ul className="connection-list">
          {view.connections.map((row) => {
            const spec = labels[row.id] ?? {
              name: row.id,
              href: "#/workbench",
              note: "",
            };
            const display = stateLabels[row.state] ?? stateLabels.unavailable;
            return (
              <li key={row.id}>
                <div>
                  <h3>
                    <a href={spec.href}>{spec.name}</a>
                  </h3>
                  <p>{spec.note}</p>
                  <p>{row.detail ?? nextStep(row)}</p>
                  <small>
                    状态码：{row.code}
                    {row.checked_at
                      ? ` · 最近读取：${row.checked_at}`
                      : " · 尚无成功读取记录"}
                  </small>
                </div>
                <StatusRail tone={display.tone} label={display.label} />
              </li>
            );
          })}
        </ul>
      )}
      {view && view.connections.length === 0 && (
        <StatePanel kind="empty" title="没有连接记录">
          <p>平台成功返回了空的功能目录。</p>
        </StatePanel>
      )}
    </section>
  );
}
