import { useEffect, useState } from "react";
import { Plus, Copy } from "lucide-react";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { AccountSession } from "../../api/session";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Actor, Persona, PersonaCatalog } from "./roleTypes";

type Edition = {
  persona: Partial<Persona>;
  expectedRevision: number | null;
  copy: boolean;
};
type History = { revision: number; note: string; created_at: string };
const labels = {
  author_text: "完整作者人格",
  tone: "语气",
  style: "表达方式",
  address: "称呼约定",
  world: "角色世界背景",
} as const;
export default function PersonaAuthor({
  actor = "",
}: {
  session: AccountSession;
  actor?: string;
}) {
  const catalog = useDomainResource<PersonaCatalog>(
    "companion/personas/catalog",
    GLOBAL_SCOPE,
  );
  const currentRole = useDomainResource<Actor>(
    "companion/roles/get",
    actorScope(actor),
    !!actor,
  );
  const [selected, setSelected] = useState("");
  const [version, setVersion] = useState<number | null>(null);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [compare, setCompare] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [edition, setEdition] = useState<Edition | null>(null);
  const index = catalog.data?.items.find(
    (item) => item.persona_id === selected,
  );
  const view = useDomainResource<Persona>(
    "companion/personas/view",
    {
      ...GLOBAL_SCOPE,
      persona_id: selected,
      ...(version ? { revision: version } : {}),
    },
    !!selected,
  );
  const previous = useDomainResource<Persona>(
    "companion/personas/view",
    { ...GLOBAL_SCOPE, persona_id: selected, revision: compare },
    !!selected && compare !== null,
  );
  const history = useDomainResource<Page<History>>(
    "companion/personas/history",
    { ...GLOBAL_SCOPE, persona_id: selected, cursor: historyCursor, limit: 40 },
    !!selected,
  );
  const apply = useDomainCommand("persona-apply", "actor");
  useEffect(() => {
    if (!selected && catalog.data?.items.length)
      setSelected(catalog.data.items[0].persona_id);
  }, [catalog.data, selected]);
  useEffect(() => {
    if (view.accessLost) setEdition(null);
  }, [view.accessLost]);
  function choose(id: string) {
    setSelected(id);
    setVersion(null);
    setHistoryCursor(null);
    setCompare(null);
    setEdition(null);
  }
  return (
    <div className="persona-author persona-author-layout scroll-layout">
      <nav className="persona-author-list scroll-pane" aria-label="人格列表">
        <div className="persona-list-heading">
          <h3>人格列表</h3>
          {catalog.data?.can_manage && (
            <button
              className="button primary"
              onClick={() =>
                setEdition({
                  persona: { name: "", author_text: "" },
                  expectedRevision: null,
                  copy: false,
                })
              }
            >
              <Plus />
              新建
            </button>
          )}
        </div>
        <input
          type="search"
          aria-label="查找人格"
          placeholder="查找人格"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        {catalog.error && <p role="alert">{catalog.error}</p>}
        {catalog.data?.items
          .filter((item) =>
            item.name.toLowerCase().includes(filter.toLowerCase()),
          )
          .map((item) => (
            <button
              type="button"
              className="persona-list-item"
              aria-selected={item.persona_id === selected}
              key={item.persona_id}
              onClick={() => choose(item.persona_id)}
            >
              <strong>{item.name}</strong>
              <small>
                当前作者修订 {item.current_revision}
                {currentRole.data?.persona_id === item.persona_id
                  ? ` · 角色固定使用 ${currentRole.data.persona_revision}`
                  : ""}
              </small>
            </button>
          ))}
      </nav>
      <section className="persona-author-detail scroll-pane">
        {view.error && <p role="alert">{view.error}</p>}
        {view.loading && !view.data && <p>正在读取作者人格…</p>}
        {view.data && (
          <>
            <header className="section-heading">
              <div>
                <h3>{view.data.name}</h3>
                <p>
                  已保存修订 {view.data.revision} ·
                  作者正文不会被主观手记自动改写。
                </p>
              </div>
              {catalog.data?.can_manage && (
                <div className="domain-actions">
                  <button
                    className="button"
                    onClick={() =>
                      setEdition({
                        persona: structuredClone(view.data!),
                        expectedRevision: index!.revision,
                        copy: false,
                      })
                    }
                  >
                    编辑为新修订
                  </button>
                  <button
                    className="button"
                    onClick={() =>
                      setEdition({
                        persona: {
                          ...structuredClone(view.data!),
                          name: view.data!.name + " 副本",
                        },
                        expectedRevision: null,
                        copy: true,
                      })
                    }
                  >
                    <Copy />
                    复制为新人格
                  </button>
                </div>
              )}
            </header>
            <div className={previous.data ? "domain-grid" : ""}>
              <PersonaText value={view.data} />
              {previous.data && (
                <section>
                  <h4>对照修订 {previous.data.revision}</h4>
                  <PersonaText value={previous.data} />
                </section>
              )}
            </div>
            {previous.error && <p role="alert">{previous.error}</p>}
            {currentRole.data?.can_manage && (
              <section className="panel">
                <h4>应用给当前角色</h4>
                <p>
                  {currentRole.data.name}当前固定修订：
                  {currentRole.data.persona_id === selected
                    ? currentRole.data.persona_revision
                    : "使用其他人格或尚未选择"}
                  。
                </p>
                <button
                  className="button primary"
                  disabled={apply.busy}
                  onClick={() =>
                    void apply
                      .run(
                        "companion/roles/save",
                        {
                          actor_id: actor,
                          person_id: null,
                          conversation_id: null,
                          name: currentRole.data!.name,
                          enabled: currentRole.data!.enabled,
                          persona_id: selected,
                          persona_revision: view.data!.revision,
                        },
                        currentRole.data!.revision,
                      )
                      .then((result) => {
                        if (result) void currentRole.refresh();
                      })
                  }
                >
                  明确使用修订 {view.data.revision}
                </button>
                <CommandFeedback {...apply} />
              </section>
            )}
            <section className="panel">
              <h4>作者修订记录</h4>
              {history.error && <p role="alert">{history.error}</p>}
              {history.data?.items.map((item) => (
                <div className="domain-record" key={item.revision}>
                  <strong>修订 {item.revision}</strong>
                  <small>
                    {new Date(item.created_at).toLocaleString("zh-CN")}
                  </small>
                  <p>{item.note || "未填写修订说明"}</p>
                  <div className="domain-actions">
                    <button
                      className="text-link"
                      onClick={() => setVersion(item.revision)}
                    >
                      查看这一版
                    </button>
                    <button
                      className="text-link"
                      onClick={() => setCompare(item.revision)}
                    >
                      与当前显示版对照
                    </button>
                  </div>
                </div>
              ))}
              <div className="domain-actions">
                {compare !== null && (
                  <button className="button" onClick={() => setCompare(null)}>
                    收起对照
                  </button>
                )}
                {version !== null && (
                  <button className="button" onClick={() => setVersion(null)}>
                    查看最新作者修订
                  </button>
                )}
                {historyCursor && (
                  <button
                    className="button"
                    onClick={() => setHistoryCursor(null)}
                  >
                    回到最新记录
                  </button>
                )}
                {history.data?.next_cursor && (
                  <button
                    className="button"
                    onClick={() => setHistoryCursor(history.data!.next_cursor)}
                  >
                    更早的修订
                  </button>
                )}
              </div>
            </section>
          </>
        )}
        {!selected && !catalog.loading && (
          <p>从左侧选择或新建一份完整作者人格。</p>
        )}
      </section>
      <Drawer
        title={edition?.expectedRevision === null ? "新的人格" : "编辑为新修订"}
        open={edition !== null}
        onClose={() => setEdition(null)}
        variant="card"
      >
        {edition && (
          <PersonaEditor
            key={`${edition.persona.persona_id ?? "new"}:${edition.persona.revision ?? 0}:${edition.copy}`}
            value={edition}
            close={() => setEdition(null)}
            saved={(id, revision) => {
              setEdition(null);
              choose(id);
              setVersion(revision);
              void catalog.refresh();
              void history.refresh();
            }}
          />
        )}
      </Drawer>
    </div>
  );
}
function PersonaText({ value }: { value: Persona }) {
  return (
    <article className="persona-text">
      {Object.entries(labels).map(([key, label]) => (
        <section key={key}>
          <h4>{label}</h4>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {value[key as keyof typeof labels] || "未单独填写"}
          </p>
        </section>
      ))}
    </article>
  );
}
function PersonaEditor({
  value,
  close,
  saved,
}: {
  value: Edition;
  close: () => void;
  saved: (id: string, revision: number) => void;
}) {
  const action = useDomainCommand<{ persona_id: string; revision: number }>(
    `persona:${value.persona.persona_id ?? "new"}:${value.copy}`,
    "global",
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const fields = Object.fromEntries(
          Object.keys(labels).map((key) => [key, String(data.get(key) ?? "")]),
        );
        void action
          .run(
            "companion/personas/save",
            {
              ...(!value.copy && value.persona.persona_id
                ? { persona_id: value.persona.persona_id }
                : {}),
              name: data.get("name"),
              ...fields,
              note: data.get("note"),
            },
            value.expectedRevision,
          )
          .then((result) => {
            if (result?.data)
              saved(result.data.persona_id, result.data.revision);
          });
      }}
    >
      <label>
        人格名称
        <input
          name="name"
          defaultValue={value.persona.name ?? ""}
          required
          maxLength={128}
        />
      </label>
      {Object.entries(labels).map(([key, label]) => (
        <label key={key}>
          {label}
          <textarea
            name={key}
            defaultValue={value.persona[key as keyof typeof labels] ?? ""}
            required={key === "author_text"}
            maxLength={key === "author_text" ? 200000 : 20000}
            rows={key === "author_text" ? 16 : 5}
          />
        </label>
      ))}
      <label>
        本次修订说明
        <textarea name="note" maxLength={20000} />
      </label>
      <div className="domain-actions">
        <button className="button primary" disabled={action.busy}>
          保存作者修订
        </button>
        <button type="button" className="button" onClick={close}>
          收起
        </button>
      </div>
      <CommandFeedback {...action} />
    </form>
  );
}
