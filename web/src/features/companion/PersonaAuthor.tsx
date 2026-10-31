import { Drawer } from "../../components/Drawer";
import { DetailCard } from "../../components/DetailCard";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Copy, MoreHorizontal, Plus } from "lucide-react";
import { author, PersonaError, reason, type SessionState } from "./personaApi";
import { requestId } from "../../app/requestId";

type Content = {
  persona: string;
  tone: string;
  style: string;
  address: string;
};
type Item = {
  id: string;
  kind: "profile" | "role";
  name: string;
  description: string;
  version: number;
  state: string;
  published_revision: string | null;
  draft_revision: string | null;
  last_applied_profile_revision: string | null;
  applied_state?: "never" | "current" | "previous" | "unknown";
  content?: Content;
};
type Form = { name: string; description: string; content: Content };
const blank: Form = {
  name: "",
  description: "",
  content: { persona: "", tone: "", style: "", address: "" },
};
const extras = [
  { key: "tone", label: "语气", max: 20000 },
  { key: "style", label: "表达风格", max: 20000 },
  { key: "address", label: "称呼", max: 4000 },
] as const;

function fromItem(item: Item): Form {
  return {
    name: item.name,
    description: item.description || "",
    content: {
      persona: item.content?.persona || "",
      tone: item.content?.tone || "",
      style: item.content?.style || "",
      address: item.content?.address || "",
    },
  };
}

function label(item: Item, index: number) {
  return item.kind === "role" && item.name === item.id
    ? `角色 ${index + 1}`
    : item.name;
}

function badge(item: Item) {
  if (item.kind === "role") {
    if (item.published_revision && item.draft_revision)
      return "当前使用 · 有新草稿";
    return item.published_revision ? "当前使用" : "草稿";
  }
  if (item.applied_state === "current")
    return item.draft_revision &&
      item.last_applied_profile_revision !== item.draft_revision
      ? "当前使用 · 有新草稿"
      : "当前使用";
  return "草稿";
}

export default function PersonaAuthor({
  session,
  actor = "",
}: {
  session: SessionState;
  actor?: string;
}) {
  const [profiles, setProfiles] = useState<Item[]>([]);
  const [targets, setTargets] = useState<Item[]>([]);
  const [permissions, setPermissions] = useState({
    create: false,
    edit: false,
    apply: false,
  });
  const [selected, setSelected] = useState<Item | null>(null);
  const [copySource, setCopySource] = useState<{
    id: string;
    version: number;
  } | null>(null);
  const [form, setForm] = useState<Form>(blank);
  const [baseline, setBaseline] = useState<Form>(blank);
  const [editing, setEditing] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState("");
  const [targetVersion, setTargetVersion] = useState<number | null>(null);
  const [choosingTarget, setChoosingTarget] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loaded, setLoaded] = useState(false);
  const request = useRef<{ body: string; id: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const initialized = useRef(false);
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const protect = (event: Event) => {
      if (
        dirtyRef.current &&
        !window.confirm("有尚未保存的输入。离开后会丢失，确定继续吗？")
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    window.addEventListener("tianshu:before-route", protect);
    window.addEventListener("tianshu:before-role", protect);
    return () => {
      controller.current?.abort();
      window.removeEventListener("beforeunload", warn);
      window.removeEventListener("tianshu:before-route", protect);
      window.removeEventListener("tianshu:before-role", protect);
    };
  }, []);

  const call = useCallback(
    async <T,>(
      path: "profiles" | "view" | "create" | "save" | "apply",
      body: object,
    ) => {
      const next = new AbortController();
      controller.current = next;
      try {
        return await author<T>(path, body, session.csrf, next.signal);
      } catch (cause) {
        if (
          cause instanceof PersonaError &&
          ["session_expired", "unauthorized"].includes(cause.code)
        )
          window.dispatchEvent(new Event("tianshu:session-lost"));
        throw cause;
      }
    },
    [session.csrf],
  );

  const load = useCallback(async () => {
    const result = await call<{
      profiles: Item[];
      targets: Item[];
      permissions: { create: boolean; edit: boolean; apply: boolean };
    }>("profiles", {});
    setProfiles(result.profiles);
    setTargets(result.targets);
    setPermissions(result.permissions);
    if (!initialized.current) {
      initialized.current = true;
      const first =
        result.targets.find((item) => item.id === actor) ??
        result.profiles[0] ??
        result.targets[0];
      if (first) {
        const answer = await call<{ item: Item }>("view", { id: first.id });
        const next = fromItem(answer.item);
        setSelected(answer.item);
        setForm(next);
        setBaseline(next);
        if (first.kind === "role") {
          setTarget(first.id);
          setTargetVersion(answer.item.version);
        }
      }
    }
    setLoaded(true);
  }, [call, actor]);

  useEffect(() => {
    void load().catch((cause) => setError(reason(cause)));
  }, [load]);

  const mayLeave = () =>
    !dirty || window.confirm("有尚未保存的输入。离开后会丢失，确定继续吗？");

  const open = async (id: string) => {
    if (busy || !mayLeave()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const answer = await call<{ item: Item }>("view", { id });
      const next = fromItem(answer.item);
      setSelected(answer.item);
      setCopySource(null);
      setForm(next);
      setBaseline(next);
      setEditing(false);
      setMobileDetail(true);
      setChoosingTarget(false);
      setTarget(answer.item.kind === "role" ? id : "");
      setTargetVersion(
        answer.item.kind === "role" ? answer.item.version : null,
      );
      request.current = null;
    } catch (cause) {
      setError(reason(cause));
    } finally {
      setBusy(false);
    }
  };

  const create = () => {
    if (busy || !mayLeave()) return;
    setSelected(null);
    setCopySource(null);
    setForm(blank);
    setBaseline(blank);
    setTarget("");
    setTargetVersion(null);
    setEditing(true);
    setMobileDetail(true);
    setChoosingTarget(false);
    setError("");
    setNotice("");
    request.current = null;
  };

  const copy = () => {
    if (!selected || busy || !mayLeave()) return;
    setCopySource({ id: selected.id, version: selected.version });
    setForm({
      ...form,
      name: `${label(
        selected,
        targets.findIndex((item) => item.id === selected.id),
      )} 副本`,
    });
    setBaseline(blank);
    setSelected(null);
    setTarget("");
    setTargetVersion(null);
    setEditing(true);
    setChoosingTarget(false);
    setNotice("");
    request.current = null;
  };

  const chooseTarget = async (id: string) => {
    if (!id) {
      setTarget("");
      setTargetVersion(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const answer = await call<{ item: Item }>("view", { id });
      setTarget(id);
      setTargetVersion(answer.item.version);
    } catch (cause) {
      setError(reason(cause));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (apply: boolean) => {
    if (busy || !form.name.trim() || !form.content.persona.trim()) return;
    if (
      apply &&
      (!selected ||
        !target ||
        targetVersion === null ||
        !permissions.apply ||
        !permissions.edit)
    )
      return;
    if (!apply && !(selected ? permissions.edit : permissions.create)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const path = apply ? "apply" : selected ? "save" : "create";
      const body: Record<string, unknown> = { ...form };
      if (!selected && copySource)
        Object.assign(body, {
          copy_from: copySource.id,
          copy_expected: copySource.version,
        });
      if (selected)
        Object.assign(body, { id: selected.id, expected: selected.version });
      if (apply)
        Object.assign(body, { target, target_expected: targetVersion });
      const signature = JSON.stringify([path, body]);
      if (request.current?.body !== signature)
        request.current = { body: signature, id: requestId() };
      body.client_request_id = request.current.id;
      const result = await call<{ item: Item; target?: Item }>(path, body);
      request.current = null;
      const next = { ...result.item, content: { ...form.content } };
      setSelected(next);
      setCopySource(null);
      setBaseline(form);
      setEditing(false);
      setChoosingTarget(false);
      if (result.target) setTargetVersion(result.target.version);
      else if (next.kind === "role") setTargetVersion(next.version);
      setNotice(apply ? "已保存并应用。" : "草稿已保存。");
      await load();
    } catch (cause) {
      setError(reason(cause));
    } finally {
      setBusy(false);
    }
  };

  const all = [...profiles, ...targets];
  const visible = all.filter((item) =>
    label(item, targets.indexOf(item))
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );
  const selectedName = selected
    ? label(
        selected,
        targets.findIndex((item) => item.id === selected.id),
      )
    : "新建人格";
  const canEdit = selected ? permissions.edit : permissions.create;

  return (
    <div
      className={`persona-author persona-author-layout scroll-layout${mobileDetail ? " persona-show-detail" : ""}`}
    >
      <nav className="persona-author-list scroll-pane" aria-label="人格列表">
        <div className="persona-list-heading">
          <h3>人格列表</h3>
          <button
            type="button"
            className="button primary"
            onClick={create}
            disabled={busy || !permissions.create}
            aria-label="新建人格"
          >
            <Plus aria-hidden="true" /> <span>新建</span>
          </button>
        </div>
        <input
          className="persona-search"
          type="search"
          placeholder="搜索人格"
          aria-label="搜索人格"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {!loaded && !error && <p className="muted">正在读取人格…</p>}
        {loaded && !all.length && <p className="muted">还没有人格。</p>}
        <div className="persona-list-items">
          {visible.map((item) => (
            <button
              key={item.id}
              type="button"
              className="persona-subject"
              aria-pressed={selected?.id === item.id}
              onClick={() => void open(item.id)}
            >
              <span className="persona-list-name">
                {label(item, targets.indexOf(item))}
              </span>
              <span className="persona-list-badge">{badge(item)}</span>
            </button>
          ))}
        </div>
      </nav>
      <section
        className="persona-author-form scroll-pane"
        aria-label="人格详情"
        tabIndex={0}
      >
        <button
          type="button"
          className="button persona-mobile-back"
          onClick={() => setMobileDetail(false)}
        >
          <ArrowLeft aria-hidden="true" /> 人格列表
        </button>
        {error && (
          <p className="persona-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="persona-notice" role="status">
            {notice}
          </p>
        )}
        {!loaded && !error ? (
          <p>正在读取人格…</p>
        ) : !selected && !editing ? (
          <p className="muted">选择左侧人格查看详情。</p>
        ) : (
          <>
            <div className="persona-author-title">
              <div>
                <h3>{selectedName}</h3>
                {selected && (
                  <span className="persona-list-badge">
                    {badge(
                      profiles.find((item) => item.id === selected.id) ||
                        selected,
                    )}
                  </span>
                )}
              </div>
              <div className="persona-title-actions">
                {selected && canEdit && !editing && (
                  <button
                    type="button"
                    className="button"
                    onClick={() => setEditing(true)}
                  >
                    编辑
                  </button>
                )}
                {selected && permissions.create && (
                  <details className="persona-more-menu">
                    <summary aria-label="更多操作">
                      <MoreHorizontal aria-hidden="true" />
                    </summary>
                    <button type="button" className="button" onClick={copy}>
                      <Copy aria-hidden="true" /> 复制为新人格
                    </button>
                  </details>
                )}
              </div>
            </div>
            {editing ? (
              <Drawer
                title={`编辑人格 · ${selectedName}`}
                variant="card"
                open={true}
                onClose={() => {
                  if (mayLeave()) {
                    setForm(baseline);
                    setEditing(false);
                    setChoosingTarget(false);
                  }
                }}
              >
                {error && <p role="alert">{error}</p>}
                {notice && <p role="status">{notice}</p>}
                <div className="persona-fields">
                  <label className="persona-author-field">
                    名称
                    <input
                      value={
                        selected?.kind === "role" && form.name === selected.id
                          ? ""
                          : form.name
                      }
                      placeholder={
                        selected?.kind === "role" && form.name === selected.id
                          ? "为角色起个名字"
                          : "人格名称"
                      }
                      maxLength={80}
                      onChange={(event) =>
                        setForm({ ...form, name: event.target.value })
                      }
                    />
                  </label>
                  <label className="persona-author-field">
                    人设正文
                    <textarea
                      className="persona-main-text"
                      value={form.content.persona}
                      maxLength={20000}
                      rows={12}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          content: {
                            ...form.content,
                            persona: event.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <DetailCard
                    title="更多设置"
                    className="persona-extra-settings"
                  >
                    <label className="persona-author-field">
                      简介
                      <textarea
                        value={form.description}
                        maxLength={400}
                        rows={2}
                        onChange={(event) =>
                          setForm({ ...form, description: event.target.value })
                        }
                      />
                    </label>
                    {extras.map(({ key, label: fieldLabel, max }) => (
                      <label key={key} className="persona-author-field">
                        {fieldLabel}
                        <textarea
                          value={form.content[key]}
                          maxLength={max}
                          rows={3}
                          onChange={(event) =>
                            setForm({
                              ...form,
                              content: {
                                ...form.content,
                                [key]: event.target.value,
                              },
                            })
                          }
                        />
                      </label>
                    ))}
                  </DetailCard>
                  {choosingTarget && selected?.kind === "profile" && (
                    <div className="persona-apply-target">
                      <label htmlFor="persona-target">应用到</label>
                      <select
                        id="persona-target"
                        value={target}
                        disabled={busy}
                        onChange={(event) =>
                          void chooseTarget(event.target.value)
                        }
                      >
                        <option value="">选择角色</option>
                        {targets.map((item, index) => (
                          <option key={item.id} value={item.id}>
                            {label(item, index)}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  {dirty && (
                    <p className="muted" role="status">
                      有未保存的修改
                    </p>
                  )}
                  <div className="persona-author-actions">
                    <button
                      type="button"
                      className="button"
                      disabled={
                        busy ||
                        !canEdit ||
                        !form.name.trim() ||
                        !form.content.persona.trim()
                      }
                      onClick={() => void submit(false)}
                    >
                      {busy ? "保存中…" : "保存草稿"}
                    </button>
                    <button
                      type="button"
                      className="button primary"
                      disabled={
                        busy ||
                        !selected ||
                        !permissions.edit ||
                        !permissions.apply ||
                        !form.name.trim() ||
                        !form.content.persona.trim()
                      }
                      onClick={() => {
                        if (selected?.kind === "profile" && !target)
                          setChoosingTarget(true);
                        else void submit(true);
                      }}
                    >
                      保存并应用
                    </button>
                    {selected && (
                      <button
                        type="button"
                        className="button persona-cancel"
                        onClick={() => {
                          if (mayLeave()) {
                            setForm(baseline);
                            setEditing(false);
                            setChoosingTarget(false);
                          }
                        }}
                      >
                        取消
                      </button>
                    )}
                  </div>
                  {!selected && (
                    <p className="muted">保存草稿后可选择角色应用。</p>
                  )}
                </div>
              </Drawer>
            ) : (
              <div className="persona-view">
                <h4>人设正文</h4>
                <p className="persona-body">
                  {form.content.persona || "暂无正文"}
                </p>
                {(form.description ||
                  extras.some(({ key }) => form.content[key])) && (
                  <DetailCard
                    title="更多设置"
                    className="persona-extra-settings"
                  >
                    {form.description && (
                      <p>
                        <strong>简介</strong>
                        <br />
                        {form.description}
                      </p>
                    )}
                    {extras.map(
                      ({ key, label: fieldLabel }) =>
                        form.content[key] && (
                          <p key={key}>
                            <strong>{fieldLabel}</strong>
                            <br />
                            {form.content[key]}
                          </p>
                        ),
                    )}
                  </DetailCard>
                )}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
