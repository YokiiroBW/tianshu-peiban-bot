import { useEffect, useEffectEvent, useRef, useState } from "react";
import {
  RefreshCw,
  Smile,
  MapPin,
  BookOpen,
  ArrowUpRight,
  Clock3,
  CalendarDays,
  Sun,
  Moon,
  Coffee,
  Sparkles,
} from "lucide-react";
import { DetailTrigger, LifeDetailDialog, type LifeDetail } from "./LifeDetail";
import { WeatherCard } from "./WeatherCard";
import { DayPeriodArt } from "./DayPeriodArt";
import { displayTime } from "./dayPeriod";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { showMood } from "./lifeLabels";

type Phase = {
  phase_id: string;
  minute: number;
  activity: string;
  detail: string | null;
  state: "planned" | "current" | "elapsed" | "skipped";
  generation_state: string;
};
type Today = {
  actor_id: string;
  day: string;
  timezone: string;
  enabled: boolean;
  observed_at: number;
  plan: {
    plan_id: string;
    version: number;
    state: string;
    generation_state: string;
    generated_by: "baseline" | "gateway";
    entries: Phase[];
    current_phase_id: string | null;
  };
};
type Experience = {
  event_id: string;
  known_id: string;
  position: number;
  kind: string;
  summary: string;
  occurred_at: number;
  learned_at: number;
  via: string;
  phase_id: string | null;
  plan_id: string | null;
  generated_by: "baseline" | "gateway" | "simulation";
};
type Timeline = {
  actor_id: string;
  day: string;
  items: Experience[];
  next_after: { position: number; known_id: string } | null;
};

const phaseText = {
  planned: "尚未发生",
  current: "当前阶段",
  elapsed: "阶段已过",
  skipped: "恢复时跳过",
};
const generationText: Record<string, string> = {
  queued: "等待内容生成",
  generating: "正在生成内容",
  interrupted: "生成中断，等待重试",
  superseded: "安排已更新",
  skipped: "恢复时跳过此阶段",
  completed: "内容已生成",
  unavailable: "模型尚未可用，按基础作息推进",
  failed: "内容生成失败，基础作息仍在推进",
};
function generationLabel(state: string) {
  return generationText[state] ?? `生成状态：${state}`;
}
function clockText(minute: number) {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}
function timeText(at: number, timezone: string) {
  return Number.isFinite(at)
    ? new Date(at * 1000).toLocaleString("zh-CN", { timeZone: timezone })
    : "时间未确认";
}

/** Persisted Companion projections. A browser read never advances the life clock. */
export function DailyLife({
  actor,
  csrf,
  canRetry,
  snapshot,
}: {
  actor: string;
  csrf: string;
  canRetry: boolean;
  snapshot?: {
    mood: string;
    activity: string | null;
    world_id: string;
    room_id: string;
    changed_at: number;
    observed_at: number;
    timezone: string;
  } | null;
}) {
  const [today, setToday] = useState<Today | null>(null);
  const [day, setDay] = useState("");
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<LifeDetail | null>(null);
  const [weatherClock, setWeatherClock] = useState<{
    timezone: string;
    serverTime: number;
    receivedAt: number;
  } | null>(null);
  const [clockTick, setClockTick] = useState(0);
  const todayReceivedAt = useRef(0);
  useEffect(() => {
    const timer = window.setInterval(
      () => setClockTick((before) => before + 1),
      15000,
    );
    return () => window.clearInterval(timer);
  }, []);
  const active = useRef<AbortController | null>(null);

  function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }
  async function readToday() {
    const controller = start();
    try {
      const next = await integrationPost<Today>(
        "life/today",
        { actor_id: actor },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      todayReceivedAt.current = performance.now();
      setToday(next);
      const selectedDay = day || next.day;
      setDay(selectedDay);
      const events = await integrationPost<Timeline>(
        "life/timeline",
        { actor_id: actor, day: selectedDay, limit: 20, after: null },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setTimeline(events);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function readTimeline(selectedDay: string, more = false) {
    const after = more ? timeline?.next_after : null;
    if (!selectedDay || (more && !after)) return;
    const controller = start();
    setDay(selectedDay);
    if (!more) setTimeline(null);
    try {
      const next = await integrationPost<Timeline>(
        "life/timeline",
        { actor_id: actor, day: selectedDay, limit: 20, after },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted)
        setTimeline((before) => {
          const entries =
            more && before?.day === selectedDay ? before.items : [];
          const found = new Set(entries.map((item) => item.known_id));
          return {
            ...next,
            items: [
              ...entries,
              ...next.items.filter((item) => !found.has(item.known_id)),
            ],
          };
        });
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function retry() {
    if (!today) return;
    const controller = start();
    setNotice("");
    try {
      await integrationPost(
        "life/retry",
        {
          actor_id: actor,
          plan_id: today.plan.plan_id,
          phase_id: null,
          expected_revision: today.plan.version,
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setNotice("内容生成重试已受理。");
      await readToday();
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    void readToday();
    return () => active.current?.abort();
  }, [actor, csrf]);

  const current = today?.plan.entries.find(
    (phase) => phase.phase_id === today.plan.current_phase_id,
  );
  const generating = [
    today?.plan.generation_state,
    current?.generation_state,
  ].some((state) => state === "queued" || state === "generating");
  // Read fresh persisted state even when generation is complete. These reads do
  // not advance the backend clock, and never interrupt an in-flight user read.
  const refreshVisible = useEffectEvent(() => {
    if (!document.hidden && !busy) void readToday();
  });
  useEffect(() => {
    if (!today?.enabled) return;
    const timer = window.setInterval(refreshVisible, generating ? 2000 : 30000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [today?.enabled, generating]);
  const retryable = (state: string) =>
    canRetry &&
    today?.enabled === true &&
    today.plan.state === "active" &&
    ["failed", "unavailable", "interrupted"].includes(state);
  const mood = showMood(snapshot?.mood);
  // The projection exposes opaque references, not human-readable place names.
  const location = snapshot?.room_id ? "角色生活空间" : "暂无位置记录";
  const currentIndex =
    today?.plan.entries.findIndex(
      (phase) => phase.phase_id === current?.phase_id,
    ) ?? -1;
  const nextMinute = today?.plan.entries[currentIndex + 1]?.minute ?? 1440;
  const observed = today
    ? new Date(
        today.observed_at * 1000 +
          Math.max(0, performance.now() - todayReceivedAt.current),
      )
    : null;
  const zone = today?.timezone ?? snapshot?.timezone ?? "UTC";
  const clockZone = weatherClock?.timezone ?? zone;
  const clockAt = weatherClock?.serverTime ?? today?.observed_at ?? 0;
  const receivedAt = weatherClock?.receivedAt ?? todayReceivedAt.current;
  // The monotonic browser timer advances a server timestamp, never the local device date.
  const clockDate = new Date(
    (clockAt + Math.max(0, performance.now() - receivedAt) / 1000) * 1000,
  );
  void clockTick;
  const observedParts = observed
    ? new Intl.DateTimeFormat("en-GB", {
        timeZone: zone,
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(observed)
    : [];
  const observedMinute =
    Number(observedParts.find((part) => part.type === "hour")?.value ?? 0) *
      60 +
    Number(observedParts.find((part) => part.type === "minute")?.value ?? 0);
  const progress = current
    ? Math.max(
        0,
        Math.min(
          100,
          ((observedMinute - current.minute) /
            Math.max(1, nextMinute - current.minute)) *
            100,
        ),
      )
    : 0;
  const phaseDetail = (phase: Phase, index: number): LifeDetail => ({
    title: phase.activity,
    body: phase.detail || "此阶段暂时没有详细内容。",
    meta: `${today?.day} · ${clockText(phase.minute)} — ${clockText(today?.plan.entries[index + 1]?.minute ?? 1440)}`,
    status: `${phaseText[phase.state]} · ${generationLabel(phase.generation_state)}`,
  });
  return (
    <section className="life-daily" aria-label="角色日常" aria-busy={busy}>
      {notice && (
        <p className="life-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <StatePanel
          kind="error"
          title="日常读取失败"
          action={
            <button
              className="button"
              disabled={busy}
              onClick={() => void readToday()}
            >
              重新读取日常
            </button>
          }
        >
          <p>{error}</p>
        </StatePanel>
      )}
      {!today && busy && (
        <StatePanel kind="loading" title="正在读取日常">
          <p>请稍候。</p>
        </StatePanel>
      )}
      {today && (
        <>
          <div className="life-overview">
            <WeatherCard
              actor={actor}
              csrf={csrf}
              onClockChange={setWeatherClock}
              date={clockDate}
              timezone={clockZone}
            />
            <div className="life-clock">
              <DayPeriodArt date={clockDate} timezone={clockZone} />
              <div className="life-clock-copy">
                <strong>
                  {displayTime(clockDate, clockZone, {
                    hour: "2-digit",
                    minute: "2-digit",
                    hourCycle: "h23",
                  })}
                </strong>
                <small>
                  {displayTime(clockDate, clockZone, {
                    month: "long",
                    day: "numeric",
                    weekday: "long",
                  })}
                </small>
                <span className="life-clock-caption">{clockZone}</span>
              </div>
            </div>
            <div className="life-mood-overview">
              <span className="life-round-icon">
                <Smile aria-hidden="true" />
              </span>
              <div>
                <small>心情</small>
                <strong className="life-mood-pill">{mood}</strong>
              </div>
            </div>
            <div className="life-place">
              <span className="life-round-icon">
                <MapPin aria-hidden="true" />
              </span>
              <div>
                <strong>{location}</strong>
                <small
                  className="life-running"
                  data-paused={!today.enabled || undefined}
                >
                  {today.enabled ? "生活进行中" : "生活已暂停"}
                </small>
              </div>
            </div>
          </div>
          <div className="life-dashboard">
            <aside className="life-aside">
              <section className="life-card life-now">
                <h3 className="life-card-title">此刻</h3>
                <img className="life-scene" src="/life-evening.png" alt="" />
                {current ? (
                  <>
                    <div className="life-now-meta">
                      <span>
                        {clockText(current.minute)} — {clockText(nextMinute)}
                      </span>
                      <span className="life-state-pill">
                        {today.enabled ? "进行中" : "已暂停"}
                      </span>
                    </div>
                    <DetailTrigger
                      detail={phaseDetail(current, currentIndex)}
                      onOpen={setDetail}
                    >
                      <h4>
                        {current.activity}
                        <ArrowUpRight aria-hidden="true" size={16} />
                      </h4>
                      <p className="life-summary">
                        {current.detail ||
                          generationLabel(current.generation_state)}
                      </p>
                    </DetailTrigger>
                    <progress
                      className="life-progress"
                      max={100}
                      value={progress}
                      aria-label="当前阶段时间进度"
                    />
                    <small className="life-progress-label">
                      阶段时间 ·{" "}
                      {Math.max(
                        0,
                        Math.min(
                          nextMinute - current.minute,
                          observedMinute - current.minute,
                        ),
                      )}{" "}
                      分钟 / {nextMinute - current.minute} 分钟
                    </small>
                  </>
                ) : (
                  <p className="muted">当前还没有已登记阶段。</p>
                )}
                <div className="life-now-footer">
                  <span>
                    <MapPin aria-hidden="true" size={16} />
                    {location}
                  </span>
                  <span>
                    <Smile aria-hidden="true" size={16} />
                    {mood}
                  </span>
                </div>
              </section>
              <section className="life-card life-mood-card">
                <h3 className="life-card-title">此刻心情</h3>
                <div className="life-mood-content">
                  <span className="life-round-icon">
                    <Smile aria-hidden="true" />
                  </span>
                  <div>
                    <strong>{mood}</strong>
                    <small>
                      {snapshot
                        ? `记录于 ${timeText(snapshot.changed_at, zone)}`
                        : "等待角色状态"}
                    </small>
                  </div>
                </div>
              </section>
              <button
                type="button"
                className="life-card life-diary-link"
                onClick={() => {
                  window.location.hash = "#/roles/diaries";
                }}
              >
                <span className="life-diary-icon">
                  <BookOpen aria-hidden="true" />
                </span>
                <span>
                  <strong>生活日记</strong>
                  <small>把生活的片段，留在字里行间。</small>
                </span>
                <ArrowUpRight aria-hidden="true" size={18} />
              </button>
            </aside>
            <section className="life-card life-schedule">
              <div className="life-schedule-heading">
                <div>
                  <h3 className="life-card-title">今日日程</h3>
                  <span>
                    日常随时间自然展开。
                    {clockZone !== zone ? ` 日程时区 ${zone}` : ""}
                  </span>
                </div>
                <button
                  type="button"
                  className="button life-refresh"
                  disabled={busy}
                  onClick={() => void readToday()}
                  aria-label="刷新日常"
                >
                  <RefreshCw aria-hidden="true" size={16} />
                  刷新
                </button>
              </div>
              {(today.plan.generated_by === "baseline" ||
                today.plan.generation_state !== "completed") && (
                <p className="life-plan-state">
                  {today.plan.generated_by === "baseline"
                    ? "当前采用基础作息 · "
                    : ""}
                  {generationLabel(today.plan.generation_state)}
                </p>
              )}
              {retryable(today.plan.generation_state) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void retry()}
                >
                  重试今日安排内容
                </button>
              )}
              <ol className="life-plan">
                {today.plan.entries.map((phase, index) => {
                  const Icon =
                    phase.minute < 600
                      ? Sun
                      : phase.minute < 720
                        ? BookOpen
                        : phase.minute < 900
                          ? Coffee
                          : phase.minute < 1260
                            ? Sparkles
                            : Moon;
                  return (
                    <li
                      key={phase.phase_id}
                      data-phase={phase.state}
                      data-current={phase.state === "current" || undefined}
                    >
                      <div className="life-phase-time">
                        <time>{clockText(phase.minute)}</time>
                        {phase.state === "current" && (
                          <small>
                            —{" "}
                            {clockText(
                              today.plan.entries[index + 1]?.minute ?? 1440,
                            )}
                          </small>
                        )}
                      </div>
                      <span className="life-phase-node" aria-hidden="true" />
                      <Icon
                        className="life-phase-icon"
                        size={23}
                        aria-hidden="true"
                      />
                      <DetailTrigger
                        detail={phaseDetail(phase, index)}
                        onOpen={setDetail}
                      >
                        <div className="life-phase-title">
                          <strong>{phase.activity}</strong>
                          {phase.state === "current" && (
                            <span className="life-state-pill">
                              {today.enabled ? "进行中" : "已暂停"}
                            </span>
                          )}
                          {phase.state === "planned" && (
                            <span className="life-state-pill future">
                              尚未发生
                            </span>
                          )}
                          {phase.state === "skipped" && (
                            <span className="life-state-pill future">
                              已跳过
                            </span>
                          )}
                        </div>
                        <p className="life-summary">
                          {phase.detail ||
                            generationLabel(phase.generation_state)}
                        </p>
                      </DetailTrigger>
                    </li>
                  );
                })}
              </ol>
              {today.plan.entries.length === 0 && (
                <p className="muted">今日还没有已登记的安排。</p>
              )}
              <div className="life-timeline-legend">
                <span>
                  <i />
                  阶段已过
                </span>
                <span>
                  <i className="current" />
                  进行中
                </span>
                <span>
                  <i className="future" />
                  待发生
                </span>
                <small>轻触片段，读懂日常。</small>
              </div>
            </section>
          </div>
          <section className="life-card life-experiences">
            <div className="section-heading">
              <div>
                <h3 className="life-card-title">已发生经历</h3>
                <p className="muted">留住那些写下的生活片段。</p>
              </div>
              <label className="life-history-date">
                <CalendarDays aria-hidden="true" size={17} />
                <span className="sr-only">经历日期</span>
                <input
                  type="date"
                  aria-label="经历日期"
                  value={day}
                  max={today.day}
                  disabled={busy}
                  onChange={(event) => void readTimeline(event.target.value)}
                />
              </label>
            </div>
            <p className="life-history-caption">
              {day === today.day ? "今天" : day} ·
              仅展示已保存经历，未来安排尚未发生。
            </p>
            {timeline && timeline.items.length === 0 && !error && (
              <p className="muted">这一天还没有已保存经历。</p>
            )}
            <ol className="life-timeline">
              {timeline?.items.map((event) => {
                const source =
                  event.generated_by === "gateway"
                    ? "生成的生活内容"
                    : event.generated_by === "simulation"
                      ? "虚构生活事件"
                      : "基础生活记录";
                const title =
                  event.summary.split(/[。！？\n]/)[0].slice(0, 36) ||
                  "生活片段";
                return (
                  <li key={event.known_id}>
                    <Clock3 aria-hidden="true" size={19} />
                    <DetailTrigger
                      detail={{
                        title,
                        body: event.summary,
                        meta: timeText(event.occurred_at, zone),
                        status: source,
                      }}
                      onOpen={setDetail}
                    >
                      <small>
                        {timeText(event.occurred_at, zone)} · {source}
                      </small>
                      <p className="life-summary">{event.summary}</p>
                      <span className="life-read-detail">
                        阅读片段 <ArrowUpRight size={13} aria-hidden="true" />
                      </span>
                    </DetailTrigger>
                  </li>
                );
              })}
            </ol>
            {timeline?.next_after && (
              <button
                className="button"
                disabled={busy}
                onClick={() => void readTimeline(day, true)}
              >
                继续读取经历
              </button>
            )}
          </section>
        </>
      )}
      <LifeDetailDialog detail={detail} onClose={() => setDetail(null)} />
    </section>
  );
}
