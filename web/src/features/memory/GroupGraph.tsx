import { useMemo, useState, type ReactNode } from "react";
import { Network, Pause, Play, RotateCcw, Waypoints } from "lucide-react";
import type { Stats, TopicPage } from "./groupTypes";
import { buildGroupGraph, graphLinkPath, pointSeed } from "./groupGraphLayout";
import { useGraphMotion } from "./useGraphMotion";
import { useGraphViewport } from "./useGraphViewport";

export default function GroupGraph({
  stats,
  topics,
  evidence,
  selectTopic,
  actions,
}: {
  stats?: Stats;
  topics?: TopicPage;
  evidence: (locator: string) => void;
  selectTopic: (key: string) => void;
  actions?: ReactNode;
}) {
  const [mode, setMode] = useState<"interactions" | "topics">("interactions");
  const [selected, setSelected] = useState<string | null>(null);
  const graph = useMemo(
    () => buildGroupGraph(mode, stats, topics),
    [stats, topics, mode],
  );
  function activate(id: string) {
    const current = graph.index.get(id);
    if (!current) return;
    setSelected(id);
    if (current.topic) selectTopic(current.topic);
  }
  const viewport = useGraphViewport(graph.points.length > 0, activate);
  const motion = useGraphMotion(graph.points.length > 0, viewport.dragging);
  const positions = new Map(
    graph.points.map((point) => {
      const offset = viewport.offsets[point.id];
      const seed = pointSeed(point.id);
      const phase = (seed % 628) / 100;
      const speed = 0.65 + ((seed >>> 8) % 9) * 0.035;
      // Independent, bounded motion; edges use these same endpoints each frame.
      const dx = (Math.sin(motion.time * speed + phase) - Math.sin(phase)) * 16;
      const dy =
        (Math.cos(motion.time * speed * 0.8 + phase) - Math.cos(phase)) * 12;
      return [
        point.id,
        {
          ...point,
          x: point.x + (offset?.x ?? 0) + dx,
          y: point.y + (offset?.y ?? 0) + dy,
        },
      ] as const;
    }),
  );
  const connected = new Set(selected ? [selected] : []);
  if (selected)
    graph.links.forEach((link) => {
      if (link.from === selected) connected.add(link.to);
      if (link.to === selected) connected.add(link.from);
    });
  function chooseMode(value: "interactions" | "topics") {
    setMode(value);
    setSelected(null);
    viewport.reset();
  }
  const point = selected ? graph.index.get(selected) : null;
  return (
    <section
      className="group-graph"
      aria-label="群互动与话题图"
      data-mode={mode}
    >
      <div className="group-graph-toolbar">
        <div className="group-graph-modes" aria-label="图谱类型">
          <button
            className="button"
            aria-pressed={mode === "interactions"}
            onClick={() => chooseMode("interactions")}
          >
            <Network aria-hidden="true" />
            互动关系
          </button>
          <button
            className="button"
            aria-pressed={mode === "topics"}
            onClick={() => chooseMode("topics")}
          >
            <Waypoints aria-hidden="true" />
            话题点云
          </button>
        </div>
        <div className="group-graph-actions">{actions}</div>
        <div className="group-graph-view-controls">
          <label className="group-graph-zoom">
            <span>缩放</span>
            <input
              aria-label="图缩放"
              type="range"
              min="0.5"
              max="3"
              step="0.01"
              value={viewport.zoom}
              onChange={(e) => viewport.setZoom(Number(e.target.value))}
            />
            <output>{Math.round(viewport.zoom * 100)}%</output>
          </label>
          <button
            className="button group-graph-motion"
            onClick={motion.toggle}
            aria-pressed={motion.floating}
            aria-label={motion.floating ? "暂停浮动" : "开启浮动"}
            title={motion.floating ? "暂停浮动" : "开启浮动"}
          >
            {motion.floating ? (
              <Pause aria-hidden="true" />
            ) : (
              <Play aria-hidden="true" />
            )}
            {motion.floating ? "暂停浮动" : "开启浮动"}
          </button>
          <button className="button" onClick={viewport.reset}>
            <RotateCcw aria-hidden="true" />
            还原视图
          </button>
        </div>
      </div>
      {!graph.points.length ? (
        <p>
          {mode === "interactions"
            ? "当前窗口没有可绘制互动。"
            : "当前页尚无有效整理话题。"}
        </p>
      ) : (
        <div
          className="group-graph-scroll"
          data-dragging={viewport.dragging || undefined}
        >
          <svg
            className="group-graph-canvas"
            ref={viewport.svg}
            {...viewport.handlers}
            viewBox="0 0 900 480"
            role="group"
            aria-label={mode === "interactions" ? "定向互动图" : "整理话题点云"}
          >
            <desc>
              拖动节点调整位置，拖动空白区域平移，滚轮缩放。点的位置仅用于布局。
            </desc>
            <g transform={viewport.transform}>
              {graph.links.map((link, i) => {
                const a = positions.get(link.from)!,
                  b = positions.get(link.to)!;
                return (
                  <path
                    key={`${link.from}/${link.to}/${i}`}
                    d={graphLinkPath(a, b)}
                    strokeWidth={
                      0.75 + Math.min(1.2, Math.log2(link.count + 1) * 0.2)
                    }
                    className="group-graph-edge"
                    data-highlighted={
                      selected === link.from ||
                      selected === link.to ||
                      undefined
                    }
                    data-muted={
                      (!!selected &&
                        selected !== link.from &&
                        selected !== link.to) ||
                      undefined
                    }
                  >
                    <title>{`${a.label} → ${b.label} · ${link.count}`}</title>
                  </path>
                );
              })}
              {[...positions.values()].map((p) => {
                const label =
                  Array.from(p.label).length > 14
                    ? `${Array.from(p.label).slice(0, 12).join("")}…`
                    : p.label;
                return (
                  <g
                    key={p.id}
                    role="button"
                    tabIndex={0}
                    aria-label={p.label}
                    className="group-graph-node"
                    transform={`translate(${p.x} ${p.y})`}
                    data-tone={p.tone}
                    data-selected={selected === p.id || undefined}
                    data-muted={
                      (!!selected && !connected.has(p.id)) || undefined
                    }
                    data-point-id={p.id}
                    onClick={() => activate(p.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        activate(p.id);
                      }
                    }}
                  >
                    <title>
                      {p.label}
                      {p.count !== undefined
                        ? ` · ${p.count}${p.topic ? " 位参与人物" : " 条消息"}`
                        : ""}
                    </title>
                    <circle className="group-graph-node-hit" r="16" />
                    <circle className="group-graph-node-ring" r={p.size + 4} />
                    <circle className="group-graph-node-body" r={p.size} />
                    <text
                      className="group-graph-point-label"
                      textAnchor="middle"
                      y={p.size + 15}
                    >
                      {label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
      )}
      {point && (
        <div className="group-selection">
          <strong>{point.label}</strong>
          {!point.topic && point.count !== undefined && (
            <p>当前统计窗口 {point.count} 条消息</p>
          )}
          {graph.links
            .filter((link) => link.from === point.id && link.locators?.length)
            .map((link) => (
              <p key={link.to}>
                → QQ {link.to} · {link.count} 次定向互动{" "}
                {link.locators!.map((ref, i) => (
                  <button
                    className="button"
                    key={ref}
                    onClick={() => evidence(ref)}
                  >
                    证据 {i + 1}
                  </button>
                ))}
              </p>
            ))}
        </div>
      )}
    </section>
  );
}
