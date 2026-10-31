import { DetailCard } from "../../../components/DetailCard";
import { History } from "./History";
import { RelationshipForm, types } from "./RelationshipForm";
import { useRelationships, type RelationshipTarget } from "./useRelationships";
import "./relationships.css";

const stages: Record<string, string> = {
  deeply_distant: "深度疏远",
  strongly_distant: "明显疏远",
  distant: "疏远",
  acquaintance: "相识",
  familiar: "熟悉",
  close: "亲近",
  intimate: "亲密",
  deeply_intimate: "深度亲密",
};

type Props = { csrf: string; selection?: RelationshipTarget };

export default function RelationshipPanel(props: Props) {
  return (
    <RelationshipContent
      key={`${props.csrf}:${props.selection?.roleId ?? ""}:${props.selection?.personId ?? ""}`}
      {...props}
    />
  );
}

function RelationshipContent({ csrf, selection }: Props) {
  const state = useRelationships(csrf, selection);
  const projection = state.view?.projection;
  return (
    <section
      className="relationships"
      aria-label="角色人物关系管理"
      aria-busy={state.busy || state.saving}
    >
      <h3>关系与好感</h3>
      <p>关系类型、称呼和好感只影响角色表达，回复权限在用户档案中单独设置。</p>
      <p>私聊可使用关系与称呼；群聊不公开私聊关系、称呼或分数。</p>
      {!selection && (
        <div className="relationship-selectors">
          <label>
            关系角色
            <select
              value={state.roleId}
              onChange={(event) => state.chooseRole(event.target.value)}
              disabled={state.busy && !state.catalog}
            >
              <option value="">请选择角色</option>
              {state.catalog?.roles.map((role) => (
                <option
                  key={role.id}
                  value={role.id}
                  disabled={!role.available}
                >
                  {role.label}
                  {role.available ? "" : "（暂不可用）"}
                </option>
              ))}
            </select>
          </label>
          <label>
            关系人物
            <select
              value={state.personId}
              onChange={(event) => state.choosePerson(event.target.value)}
              disabled={!state.roleId}
            >
              <option value="">请选择人物</option>
              {state.catalog?.items.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {state.catalog && !state.catalog.items.length && (
        <p>尚无已确认人物。观察归档建立身份后才能选择。</p>
      )}
      {!selection && state.catalog?.next_after && (
        <button
          className="button"
          onClick={() => void state.morePeople()}
          disabled={state.busy || state.saving}
        >
          加载更多人物
        </button>
      )}
      <button
        className="button"
        onClick={() =>
          void (state.personId ? state.refresh() : state.loadCatalog())
        }
        disabled={state.busy || state.saving}
      >
        刷新关系
      </button>
      {state.busy && <p role="status">正在读取当前关系…</p>}
      {state.saving && (
        <>
          <p role="status">正在提交，尚未确认保存…</p>
          <button className="button" onClick={state.cancelWaiting}>
            取消等待
          </button>
        </>
      )}
      {state.error && (
        <p role="alert" className="chat-error">
          {state.error}
        </p>
      )}
      {state.notice && <p role="status">{state.notice}</p>}
      {projection && (
        <>
          <div className="relationship-summary" aria-label="当前关系">
            <strong>
              {types[projection.relationship_type] ?? "未指定"}
              {projection.display_label ? ` · ${projection.display_label}` : ""}
            </strong>
            <p>
              好感 <output aria-label="当前好感">{projection.score}</output> ·{" "}
              {stages[projection.stage] ?? "当前阶段"} ·{" "}
              {projection.frozen ? "已冻结" : "自动变化开启"}
            </p>
          </div>
          <DetailCard title={"修改关系与好感"}>
            {state.error && <p role="alert">{state.error}</p>}
            {state.notice && <p role="status">{state.notice}</p>}
            <RelationshipForm
              key={`${state.roleId}:${state.personId}`}
              projection={projection}
              disabled={state.busy || state.saving || state.uncertain}
              onSave={state.save}
            />
          </DetailCard>
          <History
            items={state.view?.items ?? []}
            more={state.view?.has_more ?? false}
          />
        </>
      )}
    </section>
  );
}
