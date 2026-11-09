import { useEffect, useState } from "react";
import { ArrowLeft, Plus, Copy } from "lucide-react";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { AccountSession } from "../../api/session";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Actor, Persona, PersonaCatalog } from "./roleTypes";

type Edition = {
  persona: Partial<Persona>;
  expectedRevision: number | null;
  copy: boolean;
};
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
  const [showDetail, setShowDetail] = useState(false);
  const [filter, setFilter] = useState("");
  const [edition, setEdition] = useState<Edition | null>(null);
  const view = useDomainResource<Persona>(
    "companion/personas/view",
    { ...GLOBAL_SCOPE, persona_id: selected },
    !!selected,
  );
  useEffect(() => {
    if (selected || !catalog.data?.items.length) return;
    if (actor && !currentRole.data && !currentRole.error) return;
    const bound = catalog.data.items.find(
      (item) => item.persona_id === currentRole.data?.persona_id,
    );
    setSelected((bound ?? catalog.data.items[0]).persona_id);
  }, [actor, catalog.data, currentRole.data, currentRole.error, selected]);
  useEffect(() => {
    if (view.accessLost) setEdition(null);
  }, [view.accessLost]);
  function choose(id: string) {
    setSelected(id);
    setShowDetail(true);
    setEdition(null);
  }
  return (
    <div
      className={`persona-author-layout${showDetail ? " persona-show-detail" : ""}`}
    >
      <nav className="persona-author-list" aria-label="人格列表">
        <div className="persona-list-heading">
          <h3>人格档案</h3>
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
          className="persona-search"
          type="search"
          aria-label="查找人格"
          placeholder="查找人格"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        {catalog.error && <p role="alert">{catalog.error}</p>}
        <div className="persona-list-items">
          {catalog.data?.items
            .filter((item) =>
              item.name.toLowerCase().includes(filter.toLowerCase()),
            )
            .map((item) => (
              <button
                type="button"
                className="persona-subject"
                aria-pressed={item.persona_id === selected}
                key={item.persona_id}
                onClick={() => choose(item.persona_id)}
              >
                <span className="persona-list-name">{item.name}</span>
                {currentRole.data?.persona_id === item.persona_id && (
                  <span className="persona-list-badge">当前使用</span>
                )}
              </button>
            ))}
        </div>
        {catalog.data && !catalog.data.items.length && (
          <p className="muted">还没有人格档案。</p>
        )}
      </nav>
      <section className="persona-author-form" aria-label="人格详情">
        <button
          className="button persona-mobile-back"
          onClick={() => setShowDetail(false)}
        >
          <ArrowLeft />
          返回人格列表
        </button>
        {currentRole.error && <p role="alert">{currentRole.error}</p>}
        {view.error && <p role="alert">{view.error}</p>}
        {view.loading && !view.data && <p role="status">正在读取人格档案…</p>}
        {view.data && (
          <>
            <header className="persona-author-title">
              <h3>{view.data.name}</h3>
              {catalog.data?.can_manage && (
                <div className="persona-title-actions">
                  <button
                    className="button primary"
                    onClick={() =>
                      setEdition({
                        persona: structuredClone(view.data!),
                        expectedRevision: view.data!.revision,
                        copy: false,
                      })
                    }
                  >
                    编辑人格
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
                    复制
                  </button>
                </div>
              )}
            </header>
            <PersonaText value={view.data} />
          </>
        )}
        {!selected && !catalog.loading && <p>选择或新建一份人格档案。</p>}
      </section>
      <Drawer
        title={edition?.expectedRevision === null ? "新建人格" : "编辑人格"}
        open={edition !== null}
        onClose={() => setEdition(null)}
        variant="card"
      >
        {edition && (
          <PersonaEditor
            key={`${edition.persona.persona_id ?? "new"}:${edition.persona.revision ?? 0}:${edition.copy}`}
            value={edition}
            close={() => setEdition(null)}
            saved={(id) => {
              choose(id);
              void catalog.refresh();
              void view.refresh();
              void currentRole.refresh();
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
      {Object.entries(labels)
        .filter(([key]) => value[key as keyof typeof labels]?.trim())
        .map(([key, label]) => (
          <section key={key}>
            <h4>{label}</h4>
            <p className="persona-body">{value[key as keyof typeof labels]}</p>
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
  saved: (id: string) => void;
}) {
  const action = useDomainCommand<{ persona_id: string; revision: number }>(
    `persona:${value.persona.persona_id ?? "new"}:${value.copy}`,
    "global",
  );
  return (
    <form
      className="domain-form persona-editor"
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
            },
            value.expectedRevision,
          )
          .then((result) => {
            if (result?.data) saved(result.data.persona_id);
          });
      }}
    >
      <p className="muted">保存后直接更新这份人格，使用它的角色会同步更新。</p>
      <label>
        人格名称
        <input
          name="name"
          defaultValue={value.persona.name ?? ""}
          required
          maxLength={128}
        />
      </label>
      <label>
        {labels.author_text}
        <textarea
          className="persona-editor-main"
          name="author_text"
          defaultValue={value.persona.author_text ?? ""}
          required
          maxLength={200000}
          rows={18}
        />
      </label>
      <fieldset className="persona-editor-sections">
        <legend>表达与角色背景</legend>
        {Object.entries(labels)
          .filter(([key]) => key !== "author_text")
          .map(([key, label]) => (
            <label key={key}>
              {label}
              <textarea
                name={key}
                defaultValue={value.persona[key as keyof typeof labels] ?? ""}
                maxLength={20000}
                rows={4}
              />
            </label>
          ))}
      </fieldset>
      <div className="domain-actions persona-editor-actions">
        <button className="button primary" disabled={action.busy}>
          保存人格
        </button>
        <button type="button" className="button" onClick={close}>
          取消
        </button>
      </div>
      <CommandFeedback {...action} />
    </form>
  );
}
