import { useEffect, useState } from "react";
import { MapPin, Smile, BookOpen } from "lucide-react";
import { DayPeriodArt } from "./DayPeriodArt";
import { WeatherCard } from "./WeatherCard";
import { EpisodeTimeline } from "./EpisodeTimeline";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { Activity, LifeState } from "./domainTypes";
import { activityLabels, timeLabel } from "./domainTypes";
import { SourceEvidence } from "../../components/SourceEvidence";

export function DailyLife({ state }: { state: LifeState }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(timer);
  }, []);
  const activities = useDomainResource<Page<Activity>>(
    "life/activities",
    { limit: 40 },
    true,
    30000,
  );
  const current = state.activity;
  const feelings = state.affect.feelings.filter((feeling) =>
    feeling.appraisal.trim(),
  );
  return (
    <div className="life-daily">
      <section className="life-overview">
        <WeatherCard
          locationId={state.weather_location}
          date={now}
          timezone={state.timezone}
        />
        <div className="life-clock">
          <DayPeriodArt date={now} timezone={state.timezone} />
          <div className="life-clock-copy">
            <strong>
              {now.toLocaleTimeString("zh-CN", {
                timeZone: state.timezone,
                hour: "2-digit",
                minute: "2-digit",
              })}
            </strong>
            <span className="life-clock-caption">{state.timezone}</span>
          </div>
        </div>
        <div className="life-mood-overview">
          <span className="life-round-icon">
            <Smile />
          </span>
          <div>
            <small>此刻感受</small>
            <strong className="life-mood-pill">
              {feelings[0]?.appraisal ?? "暂时没有新的主观记录"}
            </strong>
          </div>
        </div>
        <div className="life-place">
          <span className="life-round-icon">
            <MapPin />
          </span>
          <div>
            <small>角色生活中的地点</small>
            <strong>
              {state.world_id} · {state.room_id}
            </strong>
            <span className="muted">想象空间</span>
          </div>
        </div>
      </section>
      {state.missing.length > 0 && (
        <p className="muted">本次近况缺少：{state.missing.join("、")}</p>
      )}
      <div className="life-dashboard">
        <aside className="life-aside">
          <section className="life-card life-now">
            <h3 className="life-card-title">此刻</h3>
            <img className="life-scene" src="/life-evening.png" alt="" />
            {current ? (
              <>
                <div className="life-now-meta">
                  <strong>{current.title}</strong>
                  <span className="life-state-pill">
                    {activityLabels[current.state] ?? current.state}
                  </span>
                </div>
                <p>{current.waiting_reason ?? "正在按已保存的活动继续。"}</p>
                <small>已保存 {current.checkpoint_step} 个实际步骤</small>
                <SourceEvidence sources={current.source_refs} />
              </>
            ) : (
              <p className="muted">
                当前没有正在进行的活动。生活会按已配置的作息与动机继续安排。
              </p>
            )}
          </section>
          <section className="life-card life-mood-card">
            <h3 className="life-card-title">心情与感想</h3>
            {feelings.length ? (
              <>
                {feelings.slice(0, 3).map((feeling) => (
                  <p key={feeling.event_id}>{feeling.appraisal}</p>
                ))}
                <SourceEvidence sources={state.affect.source_refs} />
              </>
            ) : (
              <p className="muted">暂时没有新的主观记录。</p>
            )}
          </section>
          <a className="life-card life-diary-link" href="#/roles/diaries">
            <BookOpen />
            <strong>翻开日记</strong>
            <span>看看留下了什么</span>
          </a>
        </aside>
        <section className="life-card life-schedule">
          <div className="life-schedule-heading">
            <div>
              <h3 className="life-card-title">活动与安排</h3>
              <p className="muted">活动保留实际状态，等待与未知会继续显示。</p>
            </div>
            <button
              className="button"
              onClick={() => void activities.refresh()}
            >
              刷新
            </button>
          </div>
          {activities.error && <p role="alert">{activities.error}</p>}
          <ol className="life-plan">
            {activities.data?.items.map((item) => (
              <li
                key={item.id}
                className={
                  item.id === current?.id
                    ? "current"
                    : item.state === "completed"
                      ? "elapsed"
                      : "future"
                }
              >
                <div className="life-phase-time">
                  {item.next_due_at
                    ? timeLabel(item.next_due_at, state.timezone)
                    : "按进度继续"}
                </div>
                <span className="life-phase-node" />
                <div>
                  <div className="life-phase-title">
                    <strong>{item.title}</strong>
                    <span className="life-state-pill">
                      {activityLabels[item.state] ?? item.state}
                    </span>
                  </div>
                  {item.waiting_reason && (
                    <p className="life-summary">{item.waiting_reason}</p>
                  )}
                  {item.action_id && (
                    <a
                      href={`#/system/tasks?action=${encodeURIComponent(item.action_id)}`}
                    >
                      查看原执行
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {activities.data && !activities.data.items.length && (
            <p className="muted">还没有已保存的活动安排。</p>
          )}
        </section>
      </div>
      <EpisodeTimeline />
    </div>
  );
}
