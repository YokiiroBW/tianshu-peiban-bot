import { useEffect, useRef, useState, type ReactNode } from "react";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { DetailCard } from "../../components/DetailCard";
import GroupGraph from "./GroupGraph";
import MemoryRecords from "./MemoryRecords";
import type {
  EvidenceRecord,
  Group,
  Insights,
  RecordPage,
  TopicPage,
} from "./groupTypes";
import "./groups.css";

type Filters = {
  keyword: string | null;
  sender_id: string | null;
  start_timestamp: number | null;
  end_timestamp: number | null;
};
type Search = {
  records: EvidenceRecord[];
  next_cursor: string | null;
  visibility?: {
    withdrawn: number;
    unsupported: number;
    archive_returned: number;
  };
};
type Evidence = {
  status: "archive_current" | "changed" | "missing";
  target: EvidenceRecord | null;
  context: { before: EvidenceRecord[]; after: EvidenceRecord[] };
};
const EMPTY_FILTERS: Filters = {
  keyword: null,
  sender_id: null,
  start_timestamp: null,
  end_timestamp: null,
};

export default function GroupInsights({
  roleId,
  roleVersion,
  csrf,
  fixedGroupId,
  renderOverview,
}: {
  roleId: string;
  roleVersion: number;
  csrf: string;
  fixedGroupId: string;
  renderOverview: (controls: ReactNode, summary: ReactNode) => ReactNode;
}) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [groupId, setGroupId] = useState("");
  const [window, setWindow] = useState("7d");
  const [data, setData] = useState<Insights | null>(null);
  const [search, setSearch] = useState<Search | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);
  const [topicsOpen, setTopicsOpen] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [sender, setSender] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const base = { role_id: roleId, role_version: roleVersion };

  function clear() {
    controller.current?.abort();
    setData(null);
    setSearch(null);
    setEvidence(null);
    setSelectedTopic(null);
    setTopicsOpen(false);
    setError("");
  }
  async function call<T>(path: string, body: object, signal: AbortSignal) {
    return integrationPost<T>(
      `memory/${path}`,
      { ...base, group_id: groupId, ...body },
      csrf,
      signal,
    );
  }
  async function run<T>(
    work: (signal: AbortSignal) => Promise<T>,
    accept: (value: T) => void,
  ) {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setBusy(true);
    setError("");
    try {
      const value = await work(next.signal);
      if (!next.signal.aborted) accept(value);
    } catch (cause) {
      if (!next.signal.aborted) {
        setData(null);
        setSearch(null);
        setEvidence(null);
        setError(readFailure(cause));
      }
    } finally {
      if (controller.current === next) setBusy(false);
    }
  }

  useEffect(() => {
    const guard = new AbortController();
    const directory = async () => {
      if (document.hidden) return;
      try {
        const result = await integrationPost<{
          available: boolean;
          groups: Group[];
        }>("memory/groups", base, csrf, guard.signal);
        if (guard.signal.aborted) return;
        setConfigured(result.available);
        const current = fixedGroupId;
        if (!result.groups.some((g) => g.id === current)) {
          clear();
          setGroupId("");
        } else setGroupId(current);
      } catch (cause) {
        if (!guard.signal.aborted) {
          clear();
          setGroupId("");
          setError(readFailure(cause));
        }
      }
    };
    void directory();
    const timer = globalThis.window.setInterval(() => void directory(), 15000);
    const visible = () => {
      clear();
      if (!document.hidden) {
        void directory();
        setRefresh((v) => v + 1);
      }
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      guard.abort();
      controller.current?.abort();
      globalThis.window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [roleId, roleVersion, csrf, fixedGroupId]);

  useEffect(() => {
    clear();
    if (groupId && !document.hidden)
      void run(
        (signal) => call<Insights>("group-insights", { window }, signal),
        setData,
      );
    return () => controller.current?.abort();
  }, [groupId, window, refresh]);

  function findHistory(cursor: string | null = null) {
    const current = cursor
      ? filters
      : {
          keyword: keyword.trim() || null,
          sender_id: sender.trim() || null,
          start_timestamp: after
            ? Math.floor(new Date(after).getTime() / 1000)
            : null,
          end_timestamp: before
            ? Math.floor(new Date(before).getTime() / 1000)
            : null,
        };
    if (
      current.start_timestamp !== null &&
      current.end_timestamp !== null &&
      current.start_timestamp > current.end_timestamp
    ) {
      setError("开始时间不能晚于结束时间。");
      return;
    }
    setEvidence(null);
    if (!cursor) {
      setSearch(null);
      setFilters(current);
    }
    void run(
      (signal) =>
        call<Search>(
          "group-history",
          { filters: current, cursor, limit: 20 },
          signal,
        ),
      (value) =>
        setSearch((previous) => ({
          ...value,
          records: cursor
            ? [...(previous?.records ?? []), ...value.records]
            : value.records,
        })),
    );
  }
  function showEvidence(locator: string, hash = locator.split(":").at(-1)!) {
    setEvidence(null);
    void run(
      (signal) =>
        call<Evidence>(
          "group-evidence",
          { locator, stored_content_sha256: hash },
          signal,
        ),
      setEvidence,
    );
  }
  function moreTopics() {
    if (!topics?.next_cursor || !data) return;
    void run(
      (signal) =>
        call<TopicPage>(
          "group-topics",
          { cursor: topics.next_cursor, ...data.window },
          signal,
        ),
      (value) =>
        setData((old) =>
          old
            ? {
                ...old,
                topics: {
                  state: "available",
                  data: { ...value, items: [...topics.items, ...value.items] },
                },
              }
            : old,
        ),
    );
  }
  const stats = data?.stats.state === "available" ? data.stats.data : undefined;
  const topics =
    data?.topics.state === "available" ? data.topics.data : undefined;
  const records =
    data?.records.state === "available" ? data.records.data : undefined;
  const allTopics =
    topics?.items.flatMap((segment) =>
      segment.topics.map((topic) => ({
        ...topic,
        key: `${segment.segment_id}/${topic.topic_id}`,
        recorded_at: segment.recorded_at,
        coverage: segment.coverage,
      })),
    ) ?? [];
  const topic = allTopics.find((t) => t.key === selectedTopic);
  const maxHeat = Math.max(1, ...(stats?.buckets.map((b) => b.messages) ?? []));
  const recordIndex = new Map(
    records?.items.flatMap((item) =>
      item.units.map((unit) => [unit.record_id, unit] as const),
    ) ?? [],
  );

  return (
    <div className="group-workspace">
      {renderOverview(
        <div className="group-toolbar group-statistics-controls">
          <label>
            统计窗口
            <select
              aria-label="统计窗口"
              value={window}
              disabled={busy || !groupId}
              onChange={(e) => setWindow(e.target.value)}
            >
              <option value="24h">最近 24 小时</option>
              <option value="7d">最近 7 天</option>
              <option value="30d">最近 30 天</option>
            </select>
          </label>
          <button
            className="button"
            disabled={busy || !groupId}
            onClick={() => setRefresh((v) => v + 1)}
          >
            刷新当前群
          </button>
        </div>,
        <>
          {configured === null && !error && (
            <p className="muted">正在读取当前授权群…</p>
          )}
          {configured && !groupId && (
            <p className="muted">当前角色没有此群的可读取内容。</p>
          )}
          {data && (
            <>
              <div className="group-metrics group-overview-metrics">
                {stats && (
                  <>
                    <span>
                      <strong>{stats.coverage.messages_scanned}</strong>{" "}
                      条已扫描消息
                    </span>
                    <span>
                      <strong>{stats.authors.length}</strong> 个发言账号
                    </span>
                    <span>
                      <strong>{stats.coverage.broadcast_messages}</strong>{" "}
                      条全体提醒
                    </span>
                  </>
                )}
                {topics && (
                  <span>
                    <strong>
                      {allTopics.length}
                      {topics.next_cursor ? "+" : ""}
                    </strong>{" "}
                    个已加载话题
                  </span>
                )}
              </div>
              <p className="muted group-window-range">
                {new Date(data.window.start_timestamp * 1000).toLocaleString()}{" "}
                — {new Date(data.window.end_timestamp * 1000).toLocaleString()}
              </p>
            </>
          )}
        </>,
      )}
      {configured === false && (
        <StatePanel kind="unconfigured" title="该群内容暂不可读取">
          <p>当前角色尚无此群的可读取内容，基础档案和群状态仍可在上方查看。</p>
        </StatePanel>
      )}
      {error && (
        <StatePanel kind="error" title="读取未完成">
          <p>{error}</p>
          <button className="button" onClick={() => setRefresh((v) => v + 1)}>
            重新读取
          </button>
        </StatePanel>
      )}
      {busy && <p role="status">正在核验并读取…</p>}
      {data && (
        <>
          {Object.entries({
            互动统计: data.stats,
            整理话题: data.topics,
            共享画像: data.records,
          }).map(
            ([label, part]) =>
              part.state === "unavailable" && (
                <StatePanel key={label} kind="error" title={`${label}暂不可用`}>
                  <p>本次没有取到结果。状态：{part.code}</p>
                </StatePanel>
              ),
          )}
          {stats && (
            <section className="panel">
              <h2>群聊热度</h2>
              <p className="muted">
                {stats.coverage.complete
                  ? "本次窗口内的归档扫描完整。"
                  : "已达到单次扫描上限，以下统计仅覆盖已扫描部分，请缩小窗口。"}
                时间桶按 UTC+08:00 固定偏移显示。未解析回复{" "}
                {stats.coverage.unresolved_reply}，未解析提及{" "}
                {stats.coverage.unresolved_mention}。互动结构不可解析{" "}
                {stats.coverage.relation_unavailable_messages} 条。
              </p>
              <div className="group-heat" aria-label="消息热度时间桶">
                {stats.buckets.map((bucket) => (
                  <div
                    key={bucket.start_timestamp}
                    className="group-heat-bucket"
                  >
                    <span
                      style={{
                        height: `${Math.max(2, (bucket.messages / maxHeat) * 100)}%`,
                      }}
                    >
                      <span className="sr-only">
                        {new Date(
                          bucket.start_timestamp * 1000,
                        ).toLocaleString()}
                        ：{bucket.messages} 条，{bucket.active_senders} 位作者
                      </span>
                    </span>
                    <small
                      title={new Date(
                        bucket.start_timestamp * 1000,
                      ).toLocaleString()}
                    >
                      {bucket.messages}
                    </small>
                  </div>
                ))}
              </div>
              <details>
                <summary>查看时间桶明细</summary>
                <div className="group-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>消息</th>
                        <th>活跃作者</th>
                        <th>回复</th>
                        <th>提及</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.buckets.map((b) => (
                        <tr key={b.start_timestamp}>
                          <td>
                            {new Date(
                              b.start_timestamp * 1000,
                            ).toLocaleString()}
                          </td>
                          <td>{b.messages}</td>
                          <td>{b.active_senders}</td>
                          <td>{b.reply_messages}</td>
                          <td>{b.mention_messages}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </section>
          )}
          <section className="panel">
            <h2>互动与话题</h2>
            <GroupGraph
              stats={stats}
              topics={topics}
              evidence={showEvidence}
              selectTopic={setSelectedTopic}
              actions={
                <>
                  <DetailCard
                    title="话题明细"
                    open={topicsOpen}
                    onOpenChange={setTopicsOpen}
                  >
                    <p className="muted">
                      当前已加载 {topics?.items.length ?? 0} 段、
                      {allTopics.length} 个话题
                      {topics?.next_cursor ? "，仍有后续内容" : ""}
                      。这里只展示当前窗口已读取的整理结果。
                    </p>
                    <p className="muted">
                      连线表示实际回复、@
                      或话题归属；图中位置只用于排布，不代表亲密程度、语义距离或共同观点。图中最多绘制
                      80 个话题，账号和人物身份保持各自归属。
                    </p>
                    {topics?.omitted_invalidated && (
                      <p className="muted">已省略来源失效的话题。</p>
                    )}
                    {allTopics.length ? (
                      <ul className="memory-list group-topic-details">
                        {allTopics.map((item) => (
                          <li key={item.key}>
                            <button
                              onClick={() => {
                                setSelectedTopic(item.key);
                                setTopicsOpen(false);
                              }}
                            >
                              {item.summary}
                              <small>
                                {item.person_ids.join("、") || "未归属人物"}
                              </small>
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>当前窗口尚无已加载的话题。</p>
                    )}
                    {topics?.next_cursor && (
                      <button
                        className="button"
                        disabled={busy}
                        onClick={moreTopics}
                      >
                        继续加载话题
                      </button>
                    )}
                  </DetailCard>
                  {topics?.next_cursor && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={moreTopics}
                    >
                      继续加载话题
                    </button>
                  )}
                </>
              }
            />
            {topic && (
              <article className="group-selection">
                <h3>当前话题</h3>
                <p>{topic.summary}</p>
                <p>参与人物：{topic.person_ids.join("、") || "未归属人物"}</p>
                <p>
                  整理于 {new Date(topic.recorded_at).toLocaleString()}；
                  {topic.coverage.omitted_context
                    ? "部分上下文未覆盖"
                    : "该段上下文未标记缺省"}
                  。
                </p>
                <ul>
                  {topic.memory_refs.map((ref) => (
                    <li key={`${ref.record_id}:${ref.record_version}`}>
                      {recordIndex.get(ref.record_id)?.record_version ===
                      ref.record_version
                        ? recordIndex.get(ref.record_id)!.statement
                        : "当前画像页尚未加载这条依据"}{" "}
                      <small>
                        {ref.subject_person_id} · {ref.record_id} v
                        {ref.record_version}
                      </small>
                    </li>
                  ))}
                </ul>
                <details>
                  <summary>查看原归属依据</summary>
                  <ul>
                    {topic.member_refs.map((ref) => (
                      <li key={ref.member_id}>
                        {ref.role === "human"
                          ? `人物 ${ref.person_id}`
                          : `BOT ${ref.actor_id}`}{" "}
                        · {ref.reality} ·{" "}
                        {new Date(ref.observed_at).toLocaleString()} ·{" "}
                        {ref.member_id}
                      </li>
                    ))}
                  </ul>
                </details>
              </article>
            )}
          </section>
          {records && (
            <section className="panel">
              <h2>当前群画像</h2>
              {records.items.length ? (
                <MemoryRecords
                  scope="当前群共享画像"
                  records={records.items}
                  cursor={records.next_cursor}
                  busy={busy}
                  error={error}
                />
              ) : (
                <p>当前群范围暂无有效共享画像。</p>
              )}
              {records.next_cursor && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      (signal) =>
                        call<RecordPage>(
                          "group-records",
                          { cursor: records.next_cursor },
                          signal,
                        ),
                      (value) =>
                        setData((old) =>
                          old
                            ? {
                                ...old,
                                records: {
                                  state: "available",
                                  data: {
                                    ...value,
                                    items: [...records.items, ...value.items],
                                  },
                                },
                              }
                            : old,
                        ),
                    )
                  }
                >
                  继续读取群画像
                </button>
              )}
            </section>
          )}
        </>
      )}
      {groupId && (
        <section className="panel">
          <h2>查询已归档聊天</h2>
          <p className="muted">
            可查询该群已经采集或导入的完整历史范围。保持原作者与上下文；原话代表当时表达，不代表当前观点。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              findHistory();
            }}
            className="group-history-form"
          >
            <label>
              关键词{" "}
              <input
                aria-label="历史关键词"
                maxLength={256}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </label>
            <label>
              作者 ID{" "}
              <input
                aria-label="历史作者ID"
                maxLength={128}
                value={sender}
                onChange={(e) => setSender(e.target.value)}
              />
            </label>
            <label>
              开始{" "}
              <input
                aria-label="历史开始时间"
                type="datetime-local"
                value={after}
                onChange={(e) => setAfter(e.target.value)}
              />
            </label>
            <label>
              结束{" "}
              <input
                aria-label="历史结束时间"
                type="datetime-local"
                value={before}
                onChange={(e) => setBefore(e.target.value)}
              />
            </label>
            <button className="button" disabled={busy}>
              查询
            </button>
          </form>
          {search && (
            <>
              <p>
                本页结果已按归档定位与哈希复核。来源于聊天归档，不据此认定用户当前观点。
              </p>
              {search.visibility && (
                <p className="muted">
                  本页过滤已明确遗忘 {search.visibility.withdrawn}{" "}
                  条；来源尚无法映射 {search.visibility.unsupported} 条。
                </p>
              )}
              <ul className="memory-list">
                {search.records.map((row) => (
                  <li key={row.locator}>
                    <strong>QQ {row.sender_id}</strong>
                    <small>
                      {new Date(row.timestamp * 1000).toLocaleString()}
                    </small>
                    <p className="group-original">{row.text}</p>
                    <button
                      className="button"
                      onClick={() =>
                        showEvidence(row.locator, row.stored_content_sha256)
                      }
                    >
                      查看原话与邻近消息
                    </button>
                  </li>
                ))}
              </ul>
              {!search.records.length && <p>本页没有可用归档结果。</p>}
              {search.next_cursor && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => findHistory(search.next_cursor)}
                >
                  下一页历史
                </button>
              )}
            </>
          )}
          {evidence && (
            <article className="group-selection" aria-label="原始证据">
              <h3>原话与上下文</h3>
              {evidence.status !== "archive_current" ? (
                <p>归档记录已变化或不可见，请重新查询。</p>
              ) : (
                [
                  ...evidence.context.before,
                  ...(evidence.target ? [evidence.target] : []),
                  ...evidence.context.after,
                ].map((row) => (
                  <div
                    key={row.locator}
                    data-target={
                      row.locator === evidence.target?.locator || undefined
                    }
                  >
                    <strong>QQ {row.sender_id}</strong>
                    <small>
                      {new Date(row.timestamp * 1000).toLocaleString()}
                    </small>
                    <p className="group-original">{row.text}</p>
                  </div>
                ))
              )}
            </article>
          )}
        </section>
      )}
    </div>
  );
}
