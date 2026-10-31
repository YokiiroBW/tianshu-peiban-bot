import { DetailCard } from "../../components/DetailCard";
import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import {
  integrationPost,
  readFailure,
  writeFailure,
} from "../../app/integrationApi";
import { showState } from "../life/runtimeApi";
import type {
  control_read_response,
  manage_response,
} from "../life/runtimeTypes";
import { RuntimeFeedback } from "../life/RuntimeFeedback";
import type { Person, Role } from "./types";

type View = {
  control: control_read_response;
  delivery: {
    available: boolean;
    items: {
      expression_id: string;
      kind: string;
      state: string;
      final: boolean;
      created_at: number;
      sent_segments: number;
      total_segments: number;
    }[];
  };
  private_life: { available: boolean; code: string };
};
type Subscription = control_read_response["subscriptions"][number];
type ContactTest = {
  id: string;
  value: {
    subscription_id: string;
    expression_id: string;
    due_at: number;
    expires_at: number;
  };
};
export function ProactivePanel({
  person,
  role,
  csrf,
}: {
  person: Person;
  role: Role;
  csrf: string;
}) {
  const sources = person.sources.filter((x) =>
    x.conversation.startsWith("private:"),
  );
  const [selectedConversation, setConversation] = useState("");
  const conversation = sources.some(
    (source) => source.conversation === selectedConversation,
  )
    ? selectedConversation
    : (sources[0]?.conversation ?? "");
  const [view, setView] = useState<View | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [receipt, setReceipt] = useState<manage_response | null>(null),
    [epoch, setEpoch] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null),
    [timezone, setTimezone] = useState("Asia/Shanghai"),
    [quietStart, setQuietStart] = useState("22:00"),
    [quietEnd, setQuietEnd] = useState("08:00"),
    [cooldown, setCooldown] = useState(3600),
    [quota, setQuota] = useState(3),
    [unanswered, setUnanswered] = useState(1),
    [expiry, setExpiry] = useState(3600);
  const current = useRef<AbortController | null>(null),
    pending = useRef<{ semantic: string; id: string } | null>(null),
    contactTests = useRef(new Map<string, ContactTest>());
  const selection = { actor_id: role.id, qq_id: person.qqId, conversation };
  const confirmedReply = view?.delivery.items.find(
    (row) =>
      row.kind === "response" &&
      row.state === "sent" &&
      row.final &&
      row.sent_segments > 0,
  );
  useEffect(() => {
    setView(null);
    setReceipt(null);
    setEditing(null);
    pending.current = null;
    contactTests.current.clear();
  }, [role.id, person.qqId, conversation, csrf]);
  useEffect(() => {
    if (!conversation) return;
    const control = new AbortController();
    current.current?.abort();
    current.current = control;
    setBusy(true);
    setError("");
    void integrationPost<View>(
      "people-life/view",
      selection,
      csrf,
      control.signal,
    )
      .then((value) => {
        if (!control.signal.aborted) {
          setView(value);
          const existing =
            value.control.subscriptions.find((row) => row.id === editing?.id) ??
            value.control.subscriptions[0];
          if (existing) {
            if (!editing) edit(existing);
            else setEditing(existing);
          }
        }
      })
      .catch((cause) => {
        if (!control.signal.aborted) setError(readFailure(cause));
      })
      .finally(() => {
        if (!control.signal.aborted) setBusy(false);
      });
    return () => control.abort();
  }, [role.id, person.qqId, conversation, csrf, epoch]);
  async function mutate(
    path: "subscription" | "state" | "motive",
    value: object,
    version: number,
    stableId?: string,
  ) {
    if (busy) return;
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    setError("");
    setReceipt(null);
    const semantic = JSON.stringify([path, selection, value, version]);
    if (!stableId && pending.current?.semantic !== semantic)
      pending.current = { semantic, id: requestId() };
    try {
      const result = await integrationPost<manage_response>(
        `people-life/${path}`,
        {
          ...selection,
          value,
          expected_revision: version,
          client_request_id: stableId ?? pending.current!.id,
        },
        csrf,
        control.signal,
      );
      if (!control.signal.aborted) {
        if (!stableId && result.result.state !== "unknown")
          pending.current = null;
        setReceipt(result);
        setEpoch((x) => x + 1);
      }
    } catch (cause) {
      if (!control.signal.aborted) setError(writeFailure(cause));
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  function testContact(row: Subscription) {
    if (busy) return;
    let contactTest = contactTests.current.get(row.id);
    if (!contactTest) {
      if (!confirmedReply) return;
      const due = Date.now() / 1000;
      contactTest = {
        id: requestId(),
        value: {
          subscription_id: row.id,
          expression_id: confirmedReply.expression_id,
          due_at: due,
          expires_at: due + row.expiry_seconds,
        },
      };
      contactTests.current.set(row.id, contactTest);
    }
    void mutate("motive", contactTest.value, 0, contactTest.id);
  }
  function edit(row: Subscription) {
    setEditing(row);
    setTimezone(row.timezone_name);
    setQuietStart(row.quiet_start);
    setQuietEnd(row.quiet_end);
    setCooldown(row.cooldown_seconds);
    setQuota(row.daily_quota);
    setUnanswered(row.unanswered_limit);
    setExpiry(row.expiry_seconds);
  }
  return (
    <div className="life-runtime-grid">
      <section className="panel">
        <h3>主动联系偏好</h3>
        {sources.length > 1 && (
          <label>
            私聊来源
            <select
              value={conversation}
              onChange={(e) => setConversation(e.target.value)}
            >
              {sources.map((row, index) => (
                <option
                  value={row.conversation}
                  key={`${row.connection.id}:${row.conversation}`}
                >
                  {row.connection.name} · 私聊 {index + 1}
                </option>
              ))}
            </select>
          </label>
        )}
        {!conversation ? (
          <p>尚无已登记的真实私聊来源，不能代填人物会话或开启主动联系。</p>
        ) : (
          <>
            <button
              className="button"
              disabled={busy}
              onClick={() => setEpoch((x) => x + 1)}
            >
              刷新偏好与送达
            </button>
            {view?.control.subscriptions.map((row) => (
              <article key={row.id} className="life-record">
                <header>
                  <strong>{showState(row.state)}</strong>
                  <small>版本 {row.version}</small>
                </header>
                <p>
                  安静时段 {row.quiet_start}–{row.quiet_end} ·{" "}
                  {row.timezone_name}
                </p>
                <p>
                  每天最多 {row.daily_quota} 次 · 间隔{" "}
                  {row.cooldown_seconds / 60} 分钟
                </p>
                <div className="life-action-row">
                  <button
                    className="button"
                    disabled={
                      busy ||
                      row.state !== "active" ||
                      (!confirmedReply && !contactTests.current.has(row.id))
                    }
                    onClick={() => testContact(row)}
                  >
                    {contactTests.current.has(row.id)
                      ? "检查本次主动联系"
                      : "测试主动联系"}
                  </button>
                  <button
                    className="button"
                    onClick={() => {
                      edit(row);
                      setEditorOpen(true);
                    }}
                  >
                    修改偏好
                  </button>
                  <button
                    className="button"
                    disabled={busy || row.state === "revoked"}
                    onClick={() =>
                      void mutate(
                        "state",
                        {
                          id: row.id,
                          state: row.state === "active" ? "paused" : "active",
                        },
                        row.version,
                      )
                    }
                  >
                    {row.state === "active" ? "暂停主动联系" : "恢复主动联系"}
                  </button>
                  <button
                    className="button"
                    disabled={busy || row.state === "revoked"}
                    onClick={() =>
                      void mutate(
                        "state",
                        { id: row.id, state: "revoked" },
                        row.version,
                      )
                    }
                  >
                    撤销订阅
                  </button>
                </div>
                <p className="muted">
                  使用最近一次已确认送达的私聊回复测试主动联系，仍遵守当前偏好。
                </p>
              </article>
            ))}
            <DetailCard
              title={editing ? "修改主动偏好" : "登记主动偏好"}
              open={editorOpen}
              onOpenChange={setEditorOpen}
            >
              {error && <p role="alert">{error}</p>}
              <form
                className="life-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void mutate(
                    "subscription",
                    {
                      timezone_name: timezone,
                      quiet_start: quietStart,
                      quiet_end: quietEnd,
                      cooldown_seconds: cooldown,
                      daily_quota: quota,
                      unanswered_limit: unanswered,
                      expiry_seconds: expiry,
                    },
                    editing?.version ?? 0,
                  );
                }}
              >
                <h4>{editing ? "修改主动偏好" : "登记主动偏好"}</h4>
                <label>
                  时区
                  <input
                    required
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                  />
                </label>
                <div className="life-form-row">
                  <label>
                    安静时段开始
                    <input
                      required
                      type="time"
                      value={quietStart}
                      onChange={(e) => setQuietStart(e.target.value)}
                    />
                  </label>
                  <label>
                    结束
                    <input
                      required
                      type="time"
                      value={quietEnd}
                      onChange={(e) => setQuietEnd(e.target.value)}
                    />
                  </label>
                </div>
                <div className="life-form-row">
                  <label>
                    最少间隔（秒）
                    <input
                      required
                      type="number"
                      min={0}
                      value={cooldown}
                      onChange={(e) => setCooldown(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    每天次数
                    <input
                      required
                      type="number"
                      min={0}
                      max={100}
                      value={quota}
                      onChange={(e) => setQuota(Number(e.target.value))}
                    />
                  </label>
                </div>
                <div className="life-form-row">
                  <label>
                    连续未回复上限
                    <input
                      required
                      type="number"
                      min={1}
                      max={100}
                      value={unanswered}
                      onChange={(e) => setUnanswered(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    待发送内容有效期（秒）
                    <input
                      required
                      type="number"
                      min={1}
                      value={expiry}
                      onChange={(e) => setExpiry(Number(e.target.value))}
                    />
                  </label>
                </div>
                <button className="button primary" disabled={busy}>
                  保存主动偏好
                </button>
              </form>
            </DetailCard>
          </>
        )}
        <RuntimeFeedback busy={busy} error={error} receipt={receipt} />
      </section>
      <section className="panel">
        <h3>投递历史</h3>
        {view?.delivery.available === false && <p>当前连接未提供投递状态。</p>}
        {view?.delivery.available && view.delivery.items.length === 0 && (
          <p className="muted">当前来源没有已记录的投递。</p>
        )}
        {view?.delivery.items.map((row) => (
          <article className="life-record" key={row.expression_id}>
            <strong>{showState(row.state)}</strong>
            <p>
              {new Date(row.created_at * 1000).toLocaleString("zh-CN")} · 已送{" "}
              {row.sent_segments}/{row.total_segments} 段
              {row.final ? " · 输出已结束" : " · 输出仍在继续"}
            </p>
            {row.state === "unknown" && (
              <p>结果未确认，不能视作失败或再次发送。</p>
            )}
          </article>
        ))}
        <p className="muted">
          投递状态不会授予这位人物的私有生活正文读取权限。
        </p>
      </section>
    </div>
  );
}
