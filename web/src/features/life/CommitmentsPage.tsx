import { useState } from "react";
import {
  actorScope,
  useDomainResource,
  useDomainCommand,
} from "../../api/useDomainResource";
import {
  useRoleWorkspace,
  type ConversationSelection,
} from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import { taskHref } from "../../api/commandRecovery";
import type { Page, SourceRef } from "../../api/contracts";
import type { LifeAccessView } from "./domainTypes";
import { SchemaFields } from "../plugins/SchemaFields";
import type { JsonSchema } from "../plugins/types";
import "../multimodal/multimodal.css";

type Commitment = {
  id: string;
  revision: number;
  title: string;
  due_at: string | null;
  timezone: string;
  state: string;
  reason: string | null;
  matter_id: string | null;
  can_cancel: boolean;
  can_reschedule: boolean;
  target_ref: string | null;
  person_id: string | null;
  capability_id: string | null;
  arguments: Record<string, unknown>;
  recurrence: "daily" | "weekly" | null;
  weekdays: number[];
  source_refs: SourceRef[];
  action_id: string | null;
  last_expression_id: string | null;
};
type Choice = {
  definition: {
    id: string;
    title: string;
    description: string;
    input_schema: JsonSchema;
    model_tool: { name: string } | null;
    public_command: { name: string } | null;
  };
  can_execute: boolean;
  availability: { reason: string | null };
};
const states: Record<string, string> = {
  scheduled: "已约定",
  waiting: "等待合适时机",
  dispatching: "提交中",
  waiting_action: "等待动作",
  waiting_expression: "等待表达",
  completed: "已完成",
  cancelled: "已取消",
  expired: "已错过时间窗",
  failed: "执行失败",
  unknown: "结果未知",
};
const week = [
  "星期一",
  "星期二",
  "星期三",
  "星期四",
  "星期五",
  "星期六",
  "星期日",
];
function localTime(value: string | null) {
  if (!value) return "";
  const time = new Date(value);
  return new Date(time.getTime() - time.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function Editor({ item, onSaved }: { item?: Commitment; onSaved: () => void }) {
  const workspace = useRoleWorkspace();
  const [target, setTarget] = useState(
    item?.target_ref ?? workspace.conversation?.conversation_id ?? "",
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const conversations = useDomainResource<Page<ConversationSelection>>(
    "identity/conversations",
    { ...actorScope(workspace.actor), cursor, limit: 100 },
  );
  const selected = conversations.data?.items.find(
    (row) => row.conversation_id === target,
  );
  const person =
    selected?.person_id ??
    (target === item?.target_ref
      ? item?.person_id
      : target === workspace.conversation?.conversation_id
        ? workspace.conversation.person_id
        : null);
  const scope = {
    actor_id: workspace.actor,
    conversation_id: target || null,
    person_id: person ?? null,
  };
  const catalog = useDomainResource<Choice[]>("capabilities/discover", scope);
  const [capability, setCapability] = useState(item?.capability_id ?? "");
  const [argumentsValue, setArguments] = useState<Record<string, unknown>>(
    item?.arguments ?? {},
  );
  const [repeat, setRepeat] = useState(item?.recurrence ?? "");
  const [weekdays, setWeekdays] = useState(item?.weekdays ?? []);
  const [error, setError] = useState("");
  const command = useDomainCommand(`commitment:${item?.id ?? "new"}:${target}`);
  const definition = catalog.data?.find(
    (row) => row.definition.id === capability,
  )?.definition;
  const hidden = new Set([
    "actor_id",
    "target_ref",
    "trigger",
    "window",
    "dedup_key",
    "motivation",
  ]);
  const schema = definition
    ? {
        ...definition.input_schema,
        properties: Object.fromEntries(
          Object.entries(definition.input_schema.properties ?? {}).filter(
            ([key]) => !hidden.has(key),
          ),
        ),
        required: definition.input_schema.required?.filter(
          (key) => !hidden.has(key),
        ),
      }
    : null;
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        const data = new FormData(event.currentTarget);
        if (!capability && !target) {
          setError("请选择实际接收提醒的会话。");
          return;
        }
        if (repeat === "weekly" && !weekdays.length) {
          setError("每周约定至少选择一天。");
          return;
        }
        void command
          .run(
            "life/commitments.save",
            {
              ...scope,
              ...(item ? { id: item.id } : {}),
              title: data.get("title"),
              due_at: data.get("due_at")
                ? new Date(String(data.get("due_at"))).toISOString()
                : null,
              timezone: data.get("timezone"),
              recurrence: repeat || null,
              weekdays: repeat === "weekly" ? weekdays : [],
              capability_id: capability || null,
              arguments: argumentsValue,
              ...(item?.matter_id ? { matter_id: item.matter_id } : {}),
              source_refs: item?.source_refs ?? [],
            },
            item?.revision ?? null,
          )
          .then((result) => {
            if (result && !result.receipt) onSaved();
          });
      }}
    >
      <label>
        约定内容
        <input
          name="title"
          required
          maxLength={2000}
          defaultValue={item?.title}
          placeholder="例如：周六早上提醒我去图书馆"
        />
      </label>
      <div className="domain-grid">
        <label>
          计划时间（当前浏览器时区）
          <input
            type="datetime-local"
            name="due_at"
            defaultValue={localTime(item?.due_at ?? null)}
          />
        </label>
        <label>
          重复计划采用的时区
          <input
            name="timezone"
            required
            defaultValue={
              item?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone
            }
          />
        </label>
        <label>
          重复方式
          <select
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
          >
            <option value="">单次</option>
            <option value="daily">每天</option>
            <option value="weekly">每周指定日</option>
          </select>
        </label>
      </div>
      {repeat === "weekly" && (
        <fieldset>
          <legend>每周日期</legend>
          {week.map((name, index) => (
            <label key={index}>
              <input
                type="checkbox"
                checked={weekdays.includes(index)}
                onChange={(event) =>
                  setWeekdays(
                    event.target.checked
                      ? [...weekdays, index]
                      : weekdays.filter((day) => day !== index),
                  )
                }
              />
              {name}
            </label>
          ))}
        </fieldset>
      )}
      <label>
        实际目标会话
        <select
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value="">不投递，只执行能力</option>
          {target && !selected && <option value={target}>{target}</option>}
          {conversations.data?.items.map((row) => (
            <option key={row.conversation_id} value={row.conversation_id}>
              {row.label} · {row.kind}
            </option>
          ))}
        </select>
      </label>
      <div className="domain-actions">
        {conversations.data?.next_cursor && (
          <button
            type="button"
            className="button"
            onClick={() => setCursor(conversations.data!.next_cursor)}
          >
            下一页会话
          </button>
        )}
        {cursor && (
          <button
            type="button"
            className="button"
            onClick={() => setCursor(null)}
          >
            会话第一页
          </button>
        )}
      </div>
      {conversations.error && <p role="alert">{conversations.error}</p>}
      <label>
        到期执行
        <select
          value={capability}
          onChange={(event) => {
            setCapability(event.target.value);
            setArguments({});
          }}
        >
          <option value="">发送文字提醒</option>
          {capability && !definition && (
            <option value={capability}>{capability}（当前定义未读取）</option>
          )}
          {catalog.data
            ?.filter(
              (row) =>
                row.definition.model_tool ||
                row.definition.public_command ||
                row.definition.id === "images.generate",
            )
            .map((row) => (
              <option
                key={row.definition.id}
                value={row.definition.id}
                disabled={!row.can_execute}
              >
                {row.definition.title}
                {row.can_execute
                  ? ""
                  : ` · ${row.availability.reason || "当前不可执行"}`}
              </option>
            ))}
        </select>
      </label>
      {catalog.error && <p role="alert">{catalog.error}</p>}
      {definition && <p>{definition.description}</p>}
      {schema && (
        <SchemaFields
          schema={schema}
          value={argumentsValue}
          onChange={setArguments}
        />
      )}
      {capability === "images.generate" && (
        <p className="muted">
          自拍目标、日程触发、原约定身份和有限时间窗由服务端在到期时冻结；明确要求分享会保留约定投递语义。
        </p>
      )}
      <p className="muted">
        明确约定的提醒时间优先于默认安静时段；实际发送仍核验当前会话权限。未定时间的约定等待补全，不会显示已完成。
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        className="button primary"
        disabled={command.busy || !workspace.actor}
      >
        保存约定
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
export default function CommitmentsPage() {
  const workspace = useRoleWorkspace();
  const access = useDomainResource<LifeAccessView>(
    "life/access",
    {},
    !!workspace.actor,
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<Page<Commitment>>(
    "life/commitments",
    { cursor, limit: 40 },
    !!workspace.actor && access.data?.can_read === true,
  );
  const action = useDomainCommand("commitment-controls");
  const [editing, setEditing] = useState<Commitment | "new" | null>(null);
  return (
    <div className="domain-workspace">
      <header className="domain-toolbar">
        <div>
          <p className="eyebrow">约定与持续事项</p>
          <h2>接下来，我们有什么约定</h2>
        </div>
        <div className="domain-actions">
          {access.data?.can_manage && (
            <button className="button" onClick={() => setEditing("new")}>
              新增约定
            </button>
          )}
          <button
            className="button"
            disabled={!workspace.actor || view.loading}
            onClick={() => void view.refresh()}
          >
            读取约定
          </button>
        </div>
      </header>
      {!workspace.actor && <p>先选择一位角色。</p>}
      {access.error && <p role="alert">{access.error}</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {editing && access.data?.can_manage && (
        <DetailCard
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
          title={editing === "new" ? "新增约定" : "编辑未开始的约定"}
        >
          <Editor
            key={`${workspace.actor}:${editing === "new" ? "new" : editing.id + ":" + editing.revision}`}
            item={editing === "new" ? undefined : editing}
            onSaved={() => {
              setEditing(null);
              void view.refresh();
            }}
          />
          <button className="button" onClick={() => setEditing(null)}>
            关闭编辑
          </button>
        </DetailCard>
      )}
      <CommandFeedback {...action} />
      {view.loading && !view.data && <p role="status">正在读取当前约定…</p>}
      {view.data && (
        <section className="panel">
          <h3>当前约定</h3>
          {!view.data.coverage.complete && (
            <p className="muted">
              本次读取有缺口：
              {view.data.coverage.missing.join("、") || "仍有后续页面"}
            </p>
          )}
          {!view.data.items.length && (
            <p className="muted">当前可读范围没有约定。</p>
          )}
          <div className="domain-grid">
            {view.data.items.map((item) => (
              <article
                className="domain-record"
                key={`${item.id}:${item.revision}`}
              >
                <header>
                  <strong>{item.title}</strong>
                  <span className="badge">
                    {states[item.state] ?? item.state}
                  </span>
                </header>
                <p>
                  {item.due_at
                    ? new Date(item.due_at).toLocaleString("zh-CN", {
                        timeZone: item.timezone,
                      })
                    : "时间尚未确定"}{" "}
                  · {item.timezone}
                </p>
                <p>
                  {item.recurrence === "daily"
                    ? "每天"
                    : item.recurrence === "weekly"
                      ? item.weekdays.map((day) => week[day]).join("、")
                      : "单次约定"}{" "}
                  · {item.capability_id || "文字提醒"}
                </p>
                <p>目标：{item.target_ref ?? "无投递目标"}</p>
                {item.reason && <p>{item.reason}</p>}
                {item.matter_id && <p>关联事项：{item.matter_id}</p>}
                <SourceEvidence
                  sources={item.source_refs}
                  scope={{
                    actor_id: workspace.actor,
                    person_id: item.person_id,
                    conversation_id: item.target_ref,
                  }}
                />
                {access.data?.can_manage && item.can_reschedule && (
                  <button
                    className="button"
                    disabled={action.busy}
                    onClick={() => setEditing(item)}
                  >
                    编辑时间与安排
                  </button>
                )}
                {access.data?.can_manage && item.can_cancel && (
                  <button
                    className="button"
                    disabled={action.busy}
                    onClick={() =>
                      void action
                        .run(
                          "life/commitments.cancel",
                          { id: item.id },
                          item.revision,
                        )
                        .then((result) => {
                          if (result) void view.refresh();
                        })
                    }
                  >
                    取消约定
                  </button>
                )}
                {item.action_id && (
                  <a
                    href={taskHref({
                      client_request_id: "",
                      action_id: item.action_id,
                      target: {
                        actor_id: workspace.actor,
                        person_id: item.person_id,
                        conversation_id: item.target_ref,
                      },
                    })}
                  >
                    读取原动作
                  </a>
                )}
                {item.last_expression_id && (
                  <a
                    href={`#/system/tasks?expression=${encodeURIComponent(item.last_expression_id)}&actor_id=${encodeURIComponent(workspace.actor)}&conversation_id=${encodeURIComponent(item.target_ref ?? "")}&person_id=${encodeURIComponent(item.person_id ?? "")}`}
                  >
                    读取原表达
                  </a>
                )}
              </article>
            ))}
          </div>
          <div className="domain-actions">
            {cursor && (
              <button className="button" onClick={() => setCursor(null)}>
                回到第一页
              </button>
            )}
            {view.data.next_cursor && (
              <button
                className="button"
                onClick={() => setCursor(view.data!.next_cursor)}
              >
                继续读取
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
