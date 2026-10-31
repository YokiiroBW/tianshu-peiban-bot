import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type GraphEdge = {
  from: string;
  to: string;
  active: boolean;
  dashed?: boolean;
};

/** DOM anchors keep curves aligned with real card sizes, column scroll and zoom. */
export function GraphBoard({
  edges,
  children,
}: {
  edges: GraphEdge[];
  children: ReactNode;
}) {
  const board = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<(GraphEdge & { d: string })[]>([]);
  useLayoutEffect(() => {
    const element = board.current;
    if (!element) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const base = element.getBoundingClientRect();
      const nodes = new Map(
        [...element.querySelectorAll<HTMLElement>("[data-flow-node]")].map(
          (node) => [node.dataset.flowNode!, node],
        ),
      );
      const anchor = (id: string, right: boolean) => {
        const node = nodes.get(id);
        if (!node) return null;
        const rect = node.getBoundingClientRect();
        const column = node
          .closest(".flow-column-body")!
          .getBoundingClientRect();
        const y = rect.top + rect.height / 2;
        if (y < column.top || y > column.bottom) return null;
        return {
          x: (right ? rect.right : rect.left) - base.left,
          y: y - base.top,
        };
      };
      setPaths(
        edges.flatMap((edge) => {
          const from = anchor(edge.from, true);
          const to = anchor(edge.to, false);
          if (!from || !to) return [];
          const bend = Math.max(24, (to.x - from.x) * 0.48);
          return [
            {
              ...edge,
              d: `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`,
            },
          ];
        }),
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    element
      .querySelectorAll<HTMLElement>("[data-flow-node]")
      .forEach((node) => observer.observe(node));
    element.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      observer.disconnect();
      element.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(frame);
    };
  }, [edges, children]);
  return (
    <div
      className="flow-board-viewport"
      tabIndex={0}
      aria-label="关系图，可横向滚动"
    >
      <div className="flow-board" ref={board}>
        <svg
          className="flow-connectors"
          aria-hidden="true"
          data-testid="flow-connectors"
        >
          {paths.map((path) => (
            <path
              key={`${path.from}:${path.to}`}
              d={path.d}
              className={`${path.active ? "is-active" : ""} ${path.dashed ? "is-dashed" : ""}`}
              data-from={path.from}
              data-to={path.to}
            />
          ))}
        </svg>
        {children}
      </div>
    </div>
  );
}
