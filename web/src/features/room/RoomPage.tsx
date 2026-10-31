import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import { Sun, LampDesk, Lamp, Wind, SlidersHorizontal } from "lucide-react";
import {
  formatHour,
  localHour,
  resolveRoom,
  type Overrides,
  type RoomValues,
} from "./environment";
import type { RenderStatus } from "./renderer";
import {
  mountIllustration,
  type IllustrationInput,
} from "./illustration/compositor";
import "./room.css";
import { useRoomLife } from "./useRoomLife";
import { showState } from "../life/runtimeApi";

const objects = [
  { key: "window", label: "窗户", hint: "向外开启 · 关闭仍透光", icon: Wind },
  { key: "sheer", label: "纱帘", hint: "柔化日光 · 半透明", icon: Sun },
  {
    key: "blackout",
    label: "遮光帘",
    hint: "遮挡窗外与直射光",
    icon: SlidersHorizontal,
  },
  { key: "desk", label: "书桌灯", hint: "照亮桌面与近处地板", icon: LampDesk },
  { key: "bedside", label: "床头灯", hint: "照亮床边与枕边", icon: Lamp },
] as const;

export default function RoomPage() {
  const life = useRoomLife();
  const [illustrated, setIllustrated] = useState(true);
  const [motion, setMotion] = useState(true);
  const [character, setCharacter] = useState(true);
  const [hour, setHour] = useState<number | null>(null);
  const [clockHour, setClockHour] = useState(localHour);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [simple, setSimple] = useState(false);
  const [retry, setRetry] = useState(0);
  const [status, setStatus] = useState<RenderStatus>("loading");
  const canvasHost = useRef<HTMLDivElement>(null);
  const renderer = useRef<{ update: () => void; dispose: () => void } | null>(
    null,
  );
  const input = useRef<IllustrationInput>({
    hour,
    overrides,
    motion,
    character,
  });
  const shownHour =
    hour ??
    (life.current ? localHour(new Date(), life.current.timezone) : clockHour);
  const values = resolveRoom(shownHour, overrides);
  const unavailable = status === "error" || status === "lost";
  useEffect(() => {
    input.current = {
      hour: hour ?? shownHour,
      overrides,
      motion,
      character,
      pose: life.pose,
      garment: life.garment,
    };
    renderer.current?.update();
  }, [hour, shownHour, overrides, motion, character, life.pose, life.garment]);
  useEffect(() => {
    if (!canvasHost.current) return;
    // The effect owns its canvas as well as the GPU context. StrictMode's
    // setup/cleanup/setup gets a fresh canvas instead of a deliberately lost one.
    const surface = document.createElement("canvas");
    surface.setAttribute("role", "img");
    surface.setAttribute(
      "aria-label",
      illustrated
        ? "插画小屋：窗边阅读的人物，纱帘、书桌和床。使用下方控件调整昼夜、纱帘和床头灯。"
        : "原环境预览：窗户、双层帘、书桌灯和床头灯。",
    );
    surface.setAttribute("aria-describedby", "room-caption");
    canvasHost.current.append(surface);
    setStatus("loading");
    let disposed = false;
    if (illustrated) {
      try {
        renderer.current = mountIllustration(
          surface,
          () => input.current,
          setStatus,
        );
      } catch {
        setStatus("error");
      }
    } else {
      void import("./renderer")
        .then(({ mountRoom }) => {
          if (!disposed)
            renderer.current = mountRoom(
              surface,
              () => input.current,
              simple,
              setStatus,
            );
        })
        .catch(() => {
          if (!disposed) setStatus("error");
        });
    }
    return () => {
      disposed = true;
      renderer.current?.dispose();
      renderer.current = null;
      surface.remove();
    };
  }, [illustrated, simple, retry]);
  useEffect(() => {
    if (hour !== null) return;
    let timer = 0;
    const sync = () => {
      clearTimeout(timer);
      if (document.hidden) return;
      setClockHour(localHour());
      timer = window.setTimeout(sync, 1000);
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [hour]);
  function manual(key: keyof RoomValues, value: number) {
    setOverrides((previous) => ({ ...previous, [key]: value }));
  }
  function restore(key: (typeof objects)[number]["key"]) {
    setOverrides((previous) => {
      const next = { ...previous };
      delete next[key];
      if (key === "desk") delete next.deskWarmth;
      if (key === "bedside") delete next.bedsideWarmth;
      return next;
    });
  }
  return (
    <section
      className={`room-preview ${illustrated ? "room-illustrated" : ""}`}
      aria-labelledby="room-title"
    >
      <header className="room-heading">
        <div>
          <p className="eyebrow">光、微风与阅读角</p>
          <h2 id="room-title">
            {illustrated ? "一窗光，一段安静时光" : "小屋环境预览"}
          </h2>
        </div>
        <button className="button" onClick={() => setIllustrated(!illustrated)}>
          {illustrated ? "原环境预览" : "返回插画小屋"}
        </button>
      </header>
      <div className="room-live">
        <span className="muted">
          当前角色：
          {life.actors.find((row) => row.actor_id === life.actor)?.label ??
            "请在侧栏选择角色"}
        </span>
        <button
          className="button"
          onClick={life.refresh}
          disabled={!life.actor}
        >
          刷新角色状态
        </button>
        {life.error && (
          <p className="error-text" role="alert">
            {life.error}
          </p>
        )}
        {life.loading && !life.current && (
          <p role="status">正在读取角色生活…</p>
        )}
        {life.current && (
          <div className="room-live-details">
            <p>
              <strong>{life.current.activity ?? "暂无已记录活动"}</strong>
              {life.activity && ` · ${showState(life.activity.state)}`}
            </p>
            <p>
              当前穿搭：{life.outfit?.description ?? "尚未选择"} ·{" "}
              {life.current.timezone}
            </p>
            <p>进度：{life.activity?.checkpoint.note || "尚未记录"}</p>
            <p>
              姿态投影：
              {
                {
                  reading: "坐在阅读角",
                  idle: "室内待机",
                  resting: "床边休息",
                  away: "当前不在室内",
                }[life.pose]
              }
            </p>
          </div>
        )}
      </div>
      <p className="room-boundary">
        角色虚构生活的已保存状态驱动画面姿态与当前穿搭参考；环境控件只调整本页预览，不推进日程。
      </p>
      <figure className="room-figure">
        <div className="room-stage" aria-busy={status === "loading"}>
          <div className="room-canvas" ref={canvasHost} hidden={unavailable} />
          {status !== "ready" && (
            <div
              className="room-fallback"
              role={unavailable ? "alert" : "status"}
            >
              <h3>
                {status === "loading"
                  ? "正在准备小屋画面"
                  : status === "lost"
                    ? "图形画面暂时中断"
                    : illustrated
                      ? "小屋素材暂时未能加载"
                      : "当前无法显示 3D 画面"}
              </h3>
              <p>
                {status === "lost"
                  ? "等待浏览器恢复图形资源，也可以重试。预览设置仍在。"
                  : status === "error"
                    ? illustrated
                      ? "请检查网络后重试。"
                      : "浏览器可能未提供 WebGL 2。可以重试，或继续使用下方环境控件查看设定。"
                    : "首次进入需要准备场景与光影。"}
              </p>
              {unavailable && (
                <button
                  className="button"
                  onClick={() => setRetry((n) => n + 1)}
                >
                  重试画面
                </button>
              )}
            </div>
          )}
        </div>
        <figcaption id="room-caption">
          <span>
            {life.current
              ? {
                  reading: "阅读角",
                  idle: "室内待机",
                  resting: "休息",
                  away: "外出",
                }[life.pose]
              : "生活状态尚未取得"}{" "}
            · {illustrated ? "插画投影" : "几何投影"}
          </span>
          <span>{formatHour(shownHour)} · 晴天曲线示意</span>
        </figcaption>
      </figure>
      <div className="room-time">
        <div className="room-time-heading">
          <label htmlFor="room-time">
            预览时间 <strong>{formatHour(shownHour)}</strong>
          </label>
          <span className="muted">
            {hour === null
              ? `跟随角色时钟 · ${life.current?.timezone ?? "Asia/Shanghai"}`
              : "时间已固定 · 不推进生活事件"}
          </span>
        </div>
        <input
          id="room-time"
          type="range"
          min="0"
          max="1439"
          step="1"
          value={Math.floor(shownHour * 60)}
          aria-valuetext={formatHour(shownHour)}
          onChange={(e) => setHour(Number(e.target.value) / 60)}
        />
        <div className="room-time-actions">
          <div className="room-presets" aria-label="时间快捷预览">
            {[
              [8, "清晨"],
              [14, "午后"],
              [18.5, "黄昏"],
              [23, "夜晚"],
            ].map(([value, label]) => (
              <button
                key={label}
                className="button"
                aria-pressed={hour === value}
                onClick={() => setHour(Number(value))}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            className="button"
            aria-pressed={hour === null}
            onClick={() => {
              setClockHour(localHour());
              setHour(null);
            }}
          >
            跟随本机时间
          </button>
        </div>
      </div>
      <DetailCard
        title={
          <>
            环境控制{" "}
            <span className="muted">
              {illustrated ? "微风、纱帘与床头灯" : "窗、双层帘与两盏灯"}
            </span>
          </>
        }
        className="room-controls"
      >
        <p className="room-control-note">
          调整后该物件保持手动；点击“恢复自动”才重新跟随本地昼夜示意规则。开度
          100% 表示完全拉开。
        </p>
        <div className="room-object-list">
          {objects
            .filter(
              ({ key }) =>
                !illustrated || ["window", "sheer", "bedside"].includes(key),
            )
            .map(
              ({
                key,
                label: originalLabel,
                hint: originalHint,
                icon: Icon,
              }) => {
                const label =
                  illustrated && key === "window" ? "微风" : originalLabel;
                const hint =
                  illustrated && key === "window"
                    ? "轻拂窗边纱帘"
                    : illustrated && key === "bedside"
                      ? "夜间床边的暖光"
                      : originalHint;
                const lamp = key === "desk" || key === "bedside";
                const warmthKey =
                  key === "desk" ? "deskWarmth" : "bedsideWarmth";
                const held =
                  overrides[key] !== undefined ||
                  (lamp && overrides[warmthKey] !== undefined);
                return (
                  <fieldset className="room-object" key={key}>
                    <legend>
                      <Icon aria-hidden="true" />
                      {label}
                    </legend>
                    <div className="room-object-meta">
                      <span>{hint}</span>
                      <span className="room-mode">
                        {held ? "手动保持" : "本地自动"}
                      </span>
                    </div>
                    <label htmlFor={`room-${key}`}>
                      {lamp
                        ? "亮度"
                        : illustrated && key === "window"
                          ? "强度"
                          : "开度"}
                      <strong>{Math.round(values[key] * 100)}%</strong>
                    </label>
                    <input
                      id={`room-${key}`}
                      aria-label={`${label}${lamp ? "亮度" : illustrated && key === "window" ? "强度" : "开度"}`}
                      type="range"
                      min="0"
                      max="100"
                      value={Math.round(values[key] * 100)}
                      onChange={(e) =>
                        manual(key, Number(e.target.value) / 100)
                      }
                    />
                    {lamp && !illustrated && (
                      <>
                        <label htmlFor={`room-${warmthKey}`}>
                          光色
                          <strong>
                            {Math.round(5000 - values[warmthKey] * 2300)} K
                          </strong>
                        </label>
                        <input
                          id={`room-${warmthKey}`}
                          aria-label={`${label}光色`}
                          type="range"
                          min="0"
                          max="100"
                          value={Math.round(values[warmthKey] * 100)}
                          aria-valuetext={`约 ${Math.round(5000 - values[warmthKey] * 2300)} K`}
                          onChange={(e) =>
                            manual(warmthKey, Number(e.target.value) / 100)
                          }
                        />
                      </>
                    )}
                    <div className="room-object-actions">
                      <button
                        className="button"
                        onClick={() => manual(key, values[key] > 0 ? 0 : 1)}
                      >
                        {values[key] > 0 ? "关闭" : lamp ? "开启" : "打开"}
                        {label}
                      </button>
                      <button
                        className="button"
                        disabled={!held}
                        aria-label={`恢复${label}自动`}
                        onClick={() => restore(key)}
                      >
                        恢复自动
                      </button>
                    </div>
                  </fieldset>
                );
              },
            )}
        </div>
        {!illustrated && (
          <label className="room-quality">
            <input
              type="checkbox"
              checked={simple}
              onChange={(e) => setSimple(e.target.checked)}
            />
            简化画质<span className="muted">降低分辨率与阴影精度</span>
          </label>
        )}
        {illustrated && (
          <div className="room-illustration-options">
            <label>
              <input
                type="checkbox"
                checked={motion}
                onChange={(e) => setMotion(e.target.checked)}
              />
              人物呼吸与帘动
            </label>
            <label>
              <input
                type="checkbox"
                checked={character}
                onChange={(e) => setCharacter(e.target.checked)}
              />
              显示角色姿态
            </label>
          </div>
        )}
      </DetailCard>
      <DetailCard title="关于此预览与素材" className="room-notes">
        {illustrated ? (
          <p>
            分层插画呈现昼夜与纱帘；读取角色活动后投影阅读、待机、休息或外出。穿搭参考来自当前服装的授权原件，画面使用有限姿态，不是连续走动动画。床头灯仅在夜间显示，天气请以生活页实际天气卡为准。
          </p>
        ) : (
          <>
            <p>
              房间和人物使用现有几何模型，角色已保存状态选择有限姿态；当前服装的授权参考图投影在衣着模型上。窗扇、双层帘和风摆、日光与两灯局部光照保留。
            </p>
            <p>
              昼夜为虚构的 06:00–19:00
              晴天曲线，无地点、真实天气或精确天文数据。纱帘采用简化透光和网格形变，不是布料或全局光照物理模拟。减少动态遵循系统与外观设置，停止风摆和开合过渡，保留目标状态。
            </p>
            <p>
              当前活动与穿搭读取同一生活服务；这里不会保存房间预览参数、发送生活命令或操作
              HA 设备。
            </p>
          </>
        )}
      </DetailCard>
      <a className="text-link room-companion" href="#/roles/life">
        查看日程与日记
      </a>
      <a className="text-link room-companion" href="#/roles/chat">
        前往陪伴
      </a>
    </section>
  );
}
