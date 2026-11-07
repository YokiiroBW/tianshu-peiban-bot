import { useEffect, useMemo, useRef, useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Coverage, SourceRef } from "../../api/contracts";
import { DetailCard } from "../../components/DetailCard";
import { Drawer } from "../../components/Drawer";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { Conversation } from "../people/types";
import { CoverageNote } from "./EvidenceDetails";
import { groupCloud, type CloudNode, type CloudLink } from "./groupCloud";
import { time } from "./types";

type Message = {
  id: string;
  text: string;
  text_truncated?: boolean;
  author: string;
  created_at: string;
  source_refs: SourceRef[];
};
type Activity = {
  days: number;
  timezone: string;
  observed_at: string;
  message_count: number;
  participant_count: number;
  coverage: Coverage;
  daily: { date: string; count: number }[];
  hourly: { hour: number; count: number }[];
  topics: (CloudNode & { authors: number })[];
  topic_links: CloudLink[];
  people: CloudNode[];
  interactions: CloudLink[];
  messages: Record<string, Message>;
};

function Cloud({
  title,
  nodes,
  links,
  messages,
  scope,
  noun,
}: {
  title: string;
  nodes: CloudNode[];
  links: CloudLink[];
  messages: Record<string, Message>;
  scope: { actor_id: string; conversation_id: string; person_id: null };
  noun: string;
}) {
  const [selected, setSelected] = useState<CloudNode | null>(null);
  const frame = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setWidth(Math.max(240, element.clientWidth - 32)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const points = useMemo(
    () => groupCloud(nodes, links, width),
    [nodes, links, width],
  );
  const byId = new Map(points.map((point) => [point.id, point]));
  return (
    <section className="group-insight-card" ref={frame}>
      <h3>{title}</h3>
      {!nodes.length ? (
        <p className="muted">这段时间还没有足够的可读记录。</p>
      ) : (
        <>
          <svg
            className="group-cloud"
            viewBox={`0 0 ${width} 360`}
            role="group"
            aria-label={title}
          >
            {links.map((link) => {
              const first = byId.get(link.from),
                second = byId.get(link.to);
              return first && second ? (
                <line
                  key={link.from + ":" + link.to}
                  x1={first.x}
                  y1={first.y}
                  x2={second.x}
                  y2={second.y}
                  strokeWidth={Math.min(6, 1 + Math.log2(link.count))}
                >
                  <title>
                    {first.label} ↔ {second.label}：{link.count} 次
                  </title>
                </line>
              ) : null;
            })}
            {points.map((point) => (
              <g
                key={point.id}
                role="button"
                tabIndex={0}
                aria-label={`${point.label}，${point.count} 条消息，查看依据`}
                onClick={() => setSelected(point)}
                onKeyDown={(event) => {
                  if (["Enter", " "].includes(event.key)) {
                    event.preventDefault();
                    setSelected(point);
                  }
                }}
              >
                <circle
                  cx={point.x}
                  cy={point.y}
                  r={Math.min(27, 12 + Math.sqrt(point.count) * 2)}
                />
                <text x={point.x} y={point.y + 43} textAnchor="middle">
                  {point.label.length > 12
                    ? point.label.slice(0, 11) + "…"
                    : point.label}
                </text>
                <text
                  className="group-cloud-count"
                  x={point.x}
                  y={point.y + 4}
                  textAnchor="middle"
                >
                  {point.count}
                </text>
              </g>
            ))}
          </svg>
          <div className="group-topic-chips">
            {nodes.map((node) => (
              <button
                className="button"
                key={node.id}
                onClick={() => setSelected(node)}
              >
                {node.label} <small>{node.count}</small>
              </button>
            ))}
          </div>
          <Drawer
            variant="card"
            title={selected?.label ?? noun}
            open={!!selected}
            onClose={() => setSelected(null)}
          >
            {selected && (
              <>
                <p>{selected.count} 条相关消息</p>
                <ul className="group-evidence-list">
                  {selected.message_ids
                    .map((id) => messages[id])
                    .filter(Boolean)
                    .map((message) => (
                      <li key={message.id}>
                        <small>
                          {message.author} · {time(message.created_at)}
                        </small>
                        <p className="memory-prewrap">{message.text}</p>
                        {message.text_truncated && (
                          <p className="muted">
                            仅显示前 1200 字，可从来源记录查看完整内容。
                          </p>
                        )}
                        <SourceEvidence
                          sources={message.source_refs}
                          scope={scope}
                        />
                      </li>
                    ))}
                </ul>
                {!selected.message_ids.length && (
                  <p>该成员作为提及或回复对象出现，当前窗口没有其本人发言。</p>
                )}
              </>
            )}
          </Drawer>
        </>
      )}
    </section>
  );
}

export function GroupActivity({
  group,
  view = "activity",
}: {
  group: Conversation;
  view?: "activity" | "relations" | "impression";
}) {
  const [days, setDays] = useState(7);
  const scope = {
    actor_id: group.actor_id,
    conversation_id: group.conversation_id,
    person_id: null,
  };
  const activity = useDomainResource<Activity>("conversation/group-insights", {
    ...scope,
    days,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  const data = activity.data;
  const maximum = Math.max(1, ...(data?.daily.map((item) => item.count) ?? []));
  const busiest = data?.hourly.reduce(
    (best, row) => (row.count > best.count ? row : best),
    { hour: 0, count: 0 },
  );
  return (
    <section className="group-activity">
      <div className="section-heading">
        <label>
          查看时段{" "}
          <select
            aria-label="群画像时段"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            <option value={1}>最近 24 小时</option>
            <option value={7}>最近 7 天</option>
            <option value={30}>最近 30 天</option>
          </select>
        </label>
        <button
          className="button"
          disabled={activity.loading}
          onClick={() => void activity.refresh()}
        >
          刷新群动态
        </button>
      </div>
      {activity.loading && <p role="status">正在读取群内活动…</p>}
      {activity.error && <p role="alert">{activity.error}</p>}
      {data && (
        <>
          <div className="group-stat-strip">
            <span>
              <strong>{data.message_count}</strong> 条可读消息
            </span>
            <span>
              <strong>{data.participant_count}</strong> 位发言成员
            </span>
            <span>
              <strong>{data.topics[0]?.label ?? "尚无话题"}</strong> 常见关键词
            </span>
          </div>
          {view === "impression" && (
            <p className="group-impression-summary">
              最近{days === 1 ? " 24 小时" : ` ${days} 天`}，读到{" "}
              {data.message_count} 条群消息，{data.participant_count}{" "}
              位成员参与。
              {data.topics.length > 0 &&
                `常见话题关键词有${data.topics
                  .slice(0, 3)
                  .map((topic) => topic.label)
                  .join("、")}。`}
              {busiest &&
                busiest.count > 0 &&
                `消息较多的时段是 ${busiest.hour}:00—${busiest.hour + 1}:00。`}
            </p>
          )}
          {view === "activity" && (
            <>
              <section className="group-insight-card">
                <h3>群热度</h3>
                <p className="muted">
                  按消息接收时间统计，时区 {data.timezone}。
                </p>
                <div
                  className="group-heat-bars"
                  role="img"
                  aria-label="每天群消息数量"
                >
                  {data.daily.map((day) => (
                    <div
                      key={day.date}
                      title={`${day.date}：${day.count} 条消息`}
                    >
                      <span
                        className="group-heat-bar"
                        style={{
                          height: `${Math.max(2, (day.count / maximum) * 100)}%`,
                        }}
                      />
                      <small>{day.date.slice(5)}</small>
                      <span className="sr-only">{day.count} 条消息</span>
                    </div>
                  ))}
                </div>
                <DetailCard
                  title="活跃时段与每日热度"
                  label="查看逐日与小时统计"
                >
                  <p>按 {data.timezone} 时间统计。</p>
                  <table>
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>消息</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.daily.map((day) => (
                        <tr key={day.date}>
                          <td>{day.date}</td>
                          <td>{day.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h4>一天中的活跃时段</h4>
                  <ul>
                    {data.hourly.map((hour) => (
                      <li key={hour.hour}>
                        {hour.hour}:00—{hour.hour + 1}:00 · {hour.count} 条
                      </li>
                    ))}
                  </ul>
                </DetailCard>
              </section>
              <Cloud
                key={`topics:${days}:${data.observed_at}`}
                title="热门话题与点云"
                noun="话题记录"
                nodes={data.topics}
                links={data.topic_links}
                messages={data.messages}
                scope={scope}
              />
              <p className="muted">
                关键词越常出现，点越大；连线表示在同条消息中共同出现。点开查看原话。
              </p>
            </>
          )}
          {view === "relations" && (
            <>
              <Cloud
                key={`people:${days}:${data.observed_at}`}
                title="群成员互动关系"
                noun="群互动记录"
                nodes={data.people.slice(0, 60)}
                links={data.interactions}
                messages={data.messages}
                scope={scope}
              />
              {data.people.length > 60 && (
                <p className="muted">图中展示当前窗口发言最多的 60 位成员。</p>
              )}
              <p className="muted">
                点大小代表发言数量，连线代表已核验的提及或回复次数。可点击成员查看相关交流。
              </p>
              {!data.interactions.length && (
                <p>当前窗口未记录到可核验的提及或回复关系。</p>
              )}
            </>
          )}
          {!data.coverage.complete && (
            <p className="muted">
              这里只统计当前可读的最近消息（最多 1000
              条），可能不含该时段全部活动。
            </p>
          )}
          <DetailCard title="群动态读取范围" label="查看统计范围">
            <CoverageNote coverage={data.coverage} />
            <p>
              统计截至 {time(data.observed_at)}
              。未接入、撤回或当前无权读取的消息不计入。
            </p>
          </DetailCard>
        </>
      )}
    </section>
  );
}
