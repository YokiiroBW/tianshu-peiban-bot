import { requestId } from "../../app/requestId";
import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, Puzzle, RefreshCw, Search, Upload } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { readFailure, writeFailure } from "../../app/integrationApi";
import { pluginCall } from "./api";
import { InstancePanel, states } from "./InstancePanel";
import type { Installation, Invocation, PluginOverview } from "./types";
import "./plugins.css";

export default function PluginCenter() {
  const { session } = useAuth();
  const [overview, setOverview] = useState<PluginOverview | null>(null);
  const [tab, setTab] = useState("installed");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [calls, setCalls] = useState<Invocation[]>([]);
  const [logs, setLogs] = useState<unknown[]>([]);
  const [schedules, setSchedules] = useState<unknown[]>([]);
  const [newPackage, setNewPackage] = useState("");
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState("git");
  const [source, setSource] = useState("");
  const [commit, setCommit] = useState("");
  const controller = useRef<AbortController | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const intents = useRef(new Map<string, string>());
  const currentSelection = useRef(selected);
  currentSelection.current = selected;
  const csrf = session?.csrf ?? "";
  const selectedInstance = overview?.instances.find(
    (item) => item.id === selected,
  );
  const reload = useCallback(
    async (signal: AbortSignal) => {
      const result = await pluginCall<PluginOverview>("view", {}, csrf, signal);
      setOverview(result);
      if (selected) {
        const [invocations, entries, jobs] = await Promise.all([
          pluginCall<{ items: Invocation[] }>(
            "invocations",
            { instance_id: selected },
            csrf,
            signal,
          ),
          pluginCall<{ items: unknown[] }>(
            "logs",
            { instance_id: selected },
            csrf,
            signal,
          ),
          pluginCall<{ items: unknown[] }>(
            "schedules",
            { instance_id: selected },
            csrf,
            signal,
          ),
        ]);
        if (currentSelection.current === selected) {
          setCalls(invocations.items);
          setLogs(entries.items);
          setSchedules(jobs.items);
        }
      }
    },
    [csrf, selected],
  );
  useEffect(() => {
    const active = new AbortController();
    controller.current = active;
    const poll = () => {
      if (!document.hidden)
        void reload(active.signal).catch((cause) => {
          if (!active.signal.aborted) setError(readFailure(cause));
        });
    };
    poll();
    const timer = window.setInterval(poll, 5000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      active.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [reload]);
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => {
    setCalls([]);
    setLogs([]);
    setSchedules([]);
  }, [selected]);

  async function mutate(operation: string, body: object): Promise<unknown> {
    const document = { ...body } as Record<string, unknown>;
    const semantic = { ...document };
    delete semantic.client_request_id;
    const intentKey = operation + JSON.stringify(semantic);
    if ("client_request_id" in document) {
      document.client_request_id =
        intents.current.get(intentKey) ?? String(document.client_request_id);
      intents.current.set(intentKey, String(document.client_request_id));
    }
    mutation.current?.abort();
    const active = new AbortController();
    mutation.current = active;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await pluginCall<unknown>(
        operation,
        document,
        csrf,
        active.signal,
      );
      intents.current.delete(intentKey);
      if (operation === "create") {
        setSelected((result as { id: string }).id);
        setNewPackage("");
        setName("");
      }
      if (operation === "uninstall") setSelected("");
      if (operation === "invoke")
        setNotice("动作已受理，可在实例任务中查看结果。");
      else if (operation === "install")
        setNotice("正在安装，进度会在下方更新。");
      else setNotice("操作已保存。");
      await reload(active.signal).catch((cause) => {
        if (!active.signal.aborted) setError(readFailure(cause));
      });
      return result;
    } catch (cause) {
      if (!active.signal.aborted) setError(writeFailure(cause));
      return undefined;
    } finally {
      if (!active.signal.aborted) setBusy(false);
    }
  }

  async function upload(file: File) {
    if (file.size > 6 * 1024 * 1024) {
      setError("安装包超过 6 MiB，请使用 Git 或本地开发目录安装。");
      return;
    }
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    await mutate("install", {
      source: { type: "zip", data },
      client_request_id: requestId(),
    });
  }

  const packages =
    overview?.packages.filter((item) =>
      `${item.manifest.name} ${item.manifest.id}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    ) ?? [];
  const pending =
    overview?.installations.filter((item) => item.execution !== "succeeded") ??
    [];
  return (
    <div className="plugins-page">
      <div className="plugins-toolbar">
        <nav className="plugins-tabs" aria-label="插件分类">
          {[
            ["installed", "已安装"],
            ["discover", "发现插件"],
            ["develop", "开发"],
          ].map(([id, label]) => (
            <button
              key={id}
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <label className="plugins-search">
          <Search size={17} />
          <input
            aria-label="搜索插件"
            placeholder="搜索插件"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button
          aria-label="刷新插件"
          onClick={() =>
            controller.current &&
            void reload(controller.current.signal).catch((cause) =>
              setError(readFailure(cause)),
            )
          }
        >
          <RefreshCw size={17} />
        </button>
      </div>
      {error && (
        <p className="plugins-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="plugins-note" role="status">
          {notice}
        </p>
      )}
      {!overview && !error && <p role="status">正在读取插件…</p>}
      {pending.length > 0 && (
        <div className="plugins-installations">
          {pending.map((item: Installation) => (
            <p key={item.id}>
              安装：{states[item.execution] ?? item.execution}
              {item.error && ` · ${item.error.code}`}
            </p>
          ))}
        </div>
      )}
      {tab === "installed" && overview && (
        <>
          <div className="plugins-grid">
            {packages.map((item) => (
              <article className="panel plugins-package" key={item.id}>
                <div className="plugins-inline">
                  <Puzzle size={23} />
                  <h2>{item.manifest.name}</h2>
                  <small>{item.manifest.version}</small>
                </div>
                <p>{item.manifest.description}</p>
                <div className="plugins-instances">
                  {overview.instances
                    .filter(
                      (instance) =>
                        instance.package_id === item.manifest.id &&
                        instance.version === item.manifest.version,
                    )
                    .map((instance) => (
                      <button
                        key={instance.id}
                        aria-pressed={selected === instance.id}
                        onClick={() => setSelected(instance.id)}
                      >
                        <span>{instance.name}</span>
                        <small>
                          {states[instance.state] ?? instance.state}
                        </small>
                      </button>
                    ))}
                </div>
                <button
                  onClick={() => {
                    setNewPackage(item.id);
                    setName(item.manifest.name);
                  }}
                >
                  <Plus size={15} />
                  新建实例
                </button>
              </article>
            ))}
          </div>
          {packages.length === 0 && (
            <section className="panel plugins-empty">
              <Puzzle size={32} />
              <p>还没有安装插件。</p>
              <button onClick={() => setTab("discover")}>发现插件</button>
            </section>
          )}
          {newPackage && (
            <form
              className="panel plugins-create"
              onSubmit={(event) => {
                event.preventDefault();
                void mutate("create", {
                  package_id: newPackage,
                  name,
                  client_request_id: requestId(),
                });
              }}
            >
              <label className="plugins-field">
                实例名称
                <input
                  value={name}
                  required
                  maxLength={128}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <button className="button primary" disabled={busy}>
                创建实例
              </button>
              <button type="button" onClick={() => setNewPackage("")}>
                取消
              </button>
            </form>
          )}
          {selectedInstance && (
            <InstancePanel
              key={selectedInstance.id}
              instance={selectedInstance}
              calls={calls}
              logs={logs}
              schedules={schedules}
              mutate={mutate}
              busy={busy}
              csrf={csrf}
              versions={overview.packages
                .filter(
                  (item) => item.manifest.id === selectedInstance.package_id,
                )
                .map((item) => item.manifest.version)}
            />
          )}
        </>
      )}
      {tab === "discover" && (
        <>
          <div className="plugins-grid">
            {overview?.catalog
              .filter((item) => `${item.name} ${item.id}`.includes(query))
              .map((item) => (
                <article className="panel plugins-package" key={item.source_id}>
                  <div className="plugins-inline">
                    <Puzzle size={23} />
                    <h2>{item.name}</h2>
                    <small>{item.version}</small>
                  </div>
                  <p>{item.description}</p>
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() =>
                      void mutate("install", {
                        source: { type: "builtin", id: item.source_id },
                        client_request_id: requestId(),
                      })
                    }
                  >
                    安装
                  </button>
                </article>
              ))}
          </div>
          <section className="panel plugins-config">
            <h2>安装其他插件</h2>
            <p className="plugins-note">
              安装你信任的插件。每个实例独立运行，插件代码仍拥有服务进程的系统访问能力。
            </p>
            <label className="plugins-upload">
              <Upload size={17} />
              选择 ZIP 安装包
              <input
                aria-label="选择 ZIP 安装包"
                type="file"
                accept=".zip"
                disabled={busy}
                onChange={(event) => {
                  if (event.target.files?.[0])
                    void upload(event.target.files[0]).catch((cause) =>
                      setError(readFailure(cause)),
                    );
                  event.target.value = "";
                }}
              />
            </label>
            <form
              className="plugins-config"
              onSubmit={(event) => {
                event.preventDefault();
                void mutate("install", {
                  source:
                    sourceType === "git"
                      ? { type: "git", url: source, commit }
                      : { type: "directory", path: source },
                  client_request_id: requestId(),
                });
              }}
            >
              <label className="plugins-field">
                来源
                <select
                  value={sourceType}
                  onChange={(event) => setSourceType(event.target.value)}
                >
                  <option value="git">Git 仓库</option>
                  <option value="directory">服务器上的开发目录</option>
                </select>
              </label>
              <label className="plugins-field">
                {sourceType === "git" ? "HTTPS 仓库地址" : "目录绝对路径"}
                <input
                  value={source}
                  required
                  onChange={(event) => setSource(event.target.value)}
                />
              </label>
              {sourceType === "git" && (
                <label className="plugins-field">
                  固定提交
                  <input
                    value={commit}
                    pattern="[0-9a-f]{40}"
                    required
                    placeholder="40 位提交 SHA"
                    onChange={(event) => setCommit(event.target.value)}
                  />
                </label>
              )}
              <button disabled={busy}>开始安装</button>
            </form>
          </section>
        </>
      )}
      {tab === "develop" && (
        <section className="panel plugins-config">
          <h2>编写插件</h2>
          <p>
            最小插件包含 plugin.json 和
            main.py。使用同一个动作声明生成网页参数表单、命令与工具描述。
          </p>
          <pre>{`from tianshu_plugin import Plugin, action\n\nclass Hello(Plugin):\n    @action(id="hello", title="问候",\n        input_schema={"type": "object"},\n        output_schema={"type": "string"})\n    async def hello(self, call, args):\n        return "你好"`}</pre>
          <p>当前宿主能力：{overview?.capabilities.join("、")}</p>
          <p className="plugins-note">
            消息、群管理、模型、文件与下载提供者尚未接入通用宿主；缺少的能力会显示为不可用。
          </p>
        </section>
      )}
      {(overview?.notifications.length ?? 0) > 0 && (
        <section className="panel plugins-records">
          <h2>插件通知</h2>
          {overview!.notifications
            .slice(-20)
            .reverse()
            .map((item) => (
              <article key={item.id}>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
              </article>
            ))}
        </section>
      )}
    </div>
  );
}
