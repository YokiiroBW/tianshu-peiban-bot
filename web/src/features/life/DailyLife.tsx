import { useEffect, useState } from "react";
import { BookOpen } from "lucide-react";
import { DayPeriodArt } from "./DayPeriodArt";
import { WeatherCard } from "./WeatherCard";
import { EpisodeTimeline } from "./EpisodeTimeline";
import { actorScope, useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { Activity, LifeState, Routine, GrowthView } from "./domainTypes";
import {
  activityLabels,
  activityWaitingReason,
  timeLabel,
} from "./domainTypes";
import { LifeScheduleStatus } from "./LifeScheduleStatus";
import { SourceEvidence } from "../../components/SourceEvidence";
import {
  CurioSectionHead,
  GuqinStringsSvg,
  LanternGlowSvg,
  SundialCompassSvg,
} from "../../components/curio/OrientalWidgets";
import { getShichenInfo, routineTimeProgress } from "../../design/shichen";
import { dedupeTitleAndBody } from "../../design/humanize";

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
  const routine = useDomainResource<Routine>(
    "life/routine",
    actorScope(state.actor_id),
    true,
    30000,
  );
  const growth = useDomainResource<GrowthView>(
    "companion/growth/view",
    { limit: 20 },
    true,
    30000,
  );
  const notes = (growth.data?.reflections ?? [])
    .filter((item) => item.kind === "reflection")
    .sort((left, right) =>
      (right.occurred_at ?? "").localeCompare(left.occurred_at ?? ""),
    )
    .slice(0, 3);
  const feelings = state.affect.feelings.filter((feeling) =>
    feeling.appraisal.trim(),
  );
  const shichen = getShichenInfo(now, state.timezone);
  const routineProgress = routine.data?.enabled
    ? routineTimeProgress(
        now,
        routine.data.timezone,
        routine.data.wake_at,
        routine.data.sleep_at,
      )
    : null;
  const placeName =
    state.world_id === "default" && state.room_id === "room"
      ? "日常小屋"
      : "角色的生活空间";

  return (
    <div className="life-daily">
      {/* 一体咬合式四联天象博古横格 (Curio Ambient Lattice) */}
      <section className="curio-ambient-lattice life-overview">
        {/* 格壹：节气气象 */}
        <WeatherCard
          locationId={state.weather_location}
          date={now}
          timezone={state.timezone}
        />

        {/* 格贰：昼夜圭表 */}
        <div className="ambient-cell life-clock">
          <div className="cell-head">
            <span className="cell-kicker">昼夜圭表 · 角色时辰</span>
            <span className="celadon-tag life-clock-caption">
              {state.timezone}
            </span>
          </div>
          <div className="cell-main curio-clock-row">
            <div className="life-clock-copy">
              <div className="big-metric">
                <strong>
                  {now.toLocaleTimeString("zh-CN", {
                    timeZone: state.timezone,
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </strong>
                <small>{shichen.caption}</small>
              </div>
              <div className="curio-clock-tags">
                <span className="meta-tag">{shichen.nextTransitionLabel}</span>
                <span className="meta-tag">{shichen.remainingLabel}</span>
              </div>
            </div>
            <div className="curio-clock-art-wrap">
              <SundialCompassSvg branch={shichen.branch} />
              <div className="sr-only">
                <DayPeriodArt date={now} timezone={state.timezone} />
              </div>
            </div>
          </div>
          <div className="cell-foot">
            <span>按角色时区显示昼夜</span>
            <a className="ink-link" href="#/roles/life">
              作息圭表 →
            </a>
          </div>
        </div>

        {/* 格叁：此刻感受 */}
        <div className="ambient-cell life-mood-overview">
          <div className="cell-head">
            <span className="cell-kicker">此刻感受 · 心绪脉络</span>
            <GuqinStringsSvg />
          </div>
          <div className="cell-main">
            <div className="mood-quote-box">
              <small className="sr-only">此刻感受</small>
              <strong className="life-mood-pill">
                {feelings[0]?.appraisal ?? "暂时没有新的主观记录"}
              </strong>
            </div>
            <div className="curio-clock-tags">
              <span className="meta-tag">主观感受</span>
              <span className="meta-tag">来自经历记录</span>
            </div>
          </div>
          <div className="cell-foot">
            <span>心绪与主观手记独立分卷</span>
            <a className="ink-link" href="#/roles/growth">
              心境溯源 →
            </a>
          </div>
        </div>

        {/* 格肆：角色生活中的地点 */}
        <div className="ambient-cell life-place">
          <div className="cell-head">
            <span className="cell-kicker">角色生活中的地点</span>
            <span className="cinnabar-seal">
              斋馆 · {shichen.isDaytime ? "晴窗" : "暖烛"}
            </span>
          </div>
          <div className="cell-main curio-place-row">
            <div>
              <small className="sr-only">角色生活中的地点</small>
              <strong className="curio-place-title">{placeName}</strong>
              <p className="muted curio-place-desc">
                <span>想象空间</span> · 轩窗静谧，案头陈设随心境与时辰自然流转
              </p>
            </div>
            <LanternGlowSvg />
          </div>
          <div className="cell-foot">
            <span>室内陈设可交互调节</span>
            <a className="ink-link" href="#/roles/room">
              步入小屋 →
            </a>
          </div>
        </div>
      </section>

      {state.missing.length > 0 && (
        <p className="muted">本次近况缺少：{state.missing.join("、")}</p>
      )}

      {/* 下方双栏博古列阵 (Life Bento Grid) */}
      <div className="life-bento-grid life-dashboard">
        {/* 左栏：此刻实景画框 + 主观手记 + 心情/日记门户 */}
        <aside className="life-left-stack life-aside">
          {/* 1. 此刻 · 轩窗实景画框 */}
          <section className="curio-card life-card life-now">
            <CurioSectionHead
              seal="景"
              title={<span className="life-card-title">此刻</span>}
              subtitle="当前场景与持续活动"
              action={
                <span className="celadon-tag">
                  {current
                    ? (activityLabels[current.state] ?? current.state)
                    : shichen.poeticMood}
                </span>
              }
            />
            <div className="scene-mount">
              <img className="life-scene" src="/life-evening.png" alt="" />
              <div className="scene-badge-bar">
                <span className="scene-glass-pill">📍 {placeName}</span>
                <span className="scene-glass-pill">
                  🕰️ {shichen.name} · {shichen.clockText}
                </span>
              </div>
              <div className="scene-caption">
                <h4>
                  {current ? current.title : `${shichen.fullLabel} · 轩窗小憩`}
                </h4>
                <p>
                  {current
                    ? current.waiting_reason
                      ? activityWaitingReason(current.waiting_reason)
                      : "正在按已保存的活动继续。"
                    : "当前时段留白养神，等待下一次生活安排或伙伴来访。"}
                </p>
              </div>
            </div>
            {current ? (
              <div className="curio-now-details">
                <div className="life-now-meta">
                  <strong>{current.title}</strong>
                  <span className="life-state-pill">
                    {activityLabels[current.state] ?? current.state}
                  </span>
                </div>
                <p>
                  {current.waiting_reason
                    ? activityWaitingReason(current.waiting_reason)
                    : "正在按已保存的活动继续。"}
                </p>
                <small className="meta-tag">
                  已保存 {current.checkpoint_step} 个实际步骤
                </small>
                <SourceEvidence sources={current.source_refs} />
              </div>
            ) : (
              <p className="muted curio-now-empty">当前没有正在进行的活动。</p>
            )}
          </section>

          {/* 2. 最近的主观手记（保持 h3 为 section 直接子元素以兼容测试定位器，并消除标题与正文重复） */}
          <section className="curio-card life-card life-subjective-notes">
            <h3 className="life-card-title curio-inline-heading">
              <span className="section-seal-icon" aria-hidden="true">
                记
              </span>
              <span>最近的主观手记</span>
            </h3>
            <p className="muted curio-section-lead">
              来自角色对真实经历的整理，与此刻的情绪分开记录。
            </p>
            {growth.error && (
              <p role="alert">暂时无法读取当前范围的主观手记。</p>
            )}
            <div className="curio-notes-stack">
              {notes.map((note) => {
                const { displayTitle, displayBody } = dedupeTitleAndBody(
                  note.title,
                  note.content,
                );
                return (
                  <article
                    key={`${note.id}:${note.revision}`}
                    className="note-scroll-box"
                  >
                    <strong className="note-scroll-title">
                      {displayTitle}
                    </strong>
                    {displayBody && (
                      <p className="note-scroll-body">{displayBody}</p>
                    )}
                    <SourceEvidence sources={note.evidence} />
                  </article>
                );
              })}
            </div>
            {growth.data && !notes.length && (
              <p className="muted">当前可读范围还没有主观手记。</p>
            )}
            <div className="curio-card-foot-link">
              <a className="ink-link" href="#/roles/growth">
                查看经历与成长 →
              </a>
            </div>
          </section>

          {/* 3. 心情与感想 + 翻开日记双联博古卡 */}
          <div className="portal-mini-grid">
            <section className="curio-card life-card life-mood-card">
              <h3 className="life-card-title curio-inline-heading">
                <span className="section-seal-icon" aria-hidden="true">
                  感
                </span>
                <span>心情与感想</span>
              </h3>
              {feelings.length ? (
                <>
                  {feelings.slice(0, 3).map((feeling) => (
                    <p key={feeling.event_id} className="curio-mood-line">
                      {feeling.appraisal}
                    </p>
                  ))}
                  <SourceEvidence sources={state.affect.source_refs} />
                </>
              ) : (
                <p className="muted">暂时没有新的主观记录。</p>
              )}
            </section>

            <a
              className="curio-card life-card life-diary-link portal-mini-card"
              href="#/roles/diaries"
            >
              <span className="portal-mini-icon">
                <BookOpen />
              </span>
              <strong>翻开日记</strong>
              <span>看看留下了什么</span>
            </a>
          </div>
        </aside>

        {/* 右栏：活动与安排 · 起居注 */}
        <section className="curio-card life-card life-schedule">
          <CurioSectionHead
            seal="历"
            title={<span className="life-card-title">活动与安排</span>}
            subtitle="活动保留实际状态，等待与未知会继续显示。"
            action={
              <button
                className="button curio-btn"
                onClick={() => void activities.refresh()}
              >
                ↻ 刷新
              </button>
            }
          />
          {activities.error && <p role="alert">{activities.error}</p>}
          {routine.error && <p role="alert">暂时无法读取生活调度状态。</p>}
          {routine.data && (
            <div className="scheduler-banner">
              <LifeScheduleStatus routine={routine.data} />
              <div className="clepsydra-box">
                <div className="clepsydra-label">
                  <span>作息更漏 · 清醒时段进度</span>
                  <span>
                    {routine.data.wake_at} – {routine.data.sleep_at}
                  </span>
                </div>
                {routineProgress !== null && (
                  <div
                    className="clepsydra-track"
                    role="progressbar"
                    aria-label="清醒时段时间进度"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(routineProgress)}
                  >
                    <div
                      className="clepsydra-fill"
                      style={{ width: `${routineProgress}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          <ol className="life-plan curio-timeline">
            {activities.data?.items.map((item) => {
              const isCurrent = item.id === current?.id;
              const isElapsed = item.state === "completed";
              return (
                <li
                  key={item.id}
                  className={`timeline-entry ${
                    isCurrent ? "current" : isElapsed ? "elapsed" : "future"
                  }`}
                >
                  <span
                    className={`timeline-knot life-phase-node${isCurrent ? " active" : isElapsed ? " done" : ""}`}
                  />
                  <div className="timeline-card">
                    <div className="timeline-head">
                      <div className="life-phase-title">
                        <span className="timeline-time life-phase-time">
                          {item.next_due_at
                            ? timeLabel(item.next_due_at, state.timezone)
                            : "按进度继续"}
                        </span>
                        <strong className="timeline-title">{item.title}</strong>
                      </div>
                      <span className="life-state-pill">
                        {activityLabels[item.state] ?? item.state}
                      </span>
                    </div>
                    {item.waiting_reason && (
                      <p className="timeline-desc life-summary">
                        {activityWaitingReason(item.waiting_reason)}
                      </p>
                    )}
                    <div className="timeline-foot">
                      <span className="meta-tag">
                        已保存 {item.checkpoint_step} 个步骤
                      </span>
                      {item.action_id && (
                        <a
                          className="ink-link"
                          href={`#/system/tasks?action=${encodeURIComponent(item.action_id)}`}
                        >
                          查看原执行 →
                        </a>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
          {activities.data && !activities.data.items.length && (
            <p className="muted curio-empty-schedule">
              还没有已保存的活动安排。
            </p>
          )}
        </section>
      </div>

      {/* 底部：经历与记忆时间轴 */}
      <EpisodeTimeline />
    </div>
  );
}
