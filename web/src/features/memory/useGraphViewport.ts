import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from "react";

type Position = { x: number; y: number };
type Viewport = Position & { zoom: number };
type Gesture = {
  pointerId: number;
  pointId: string | null;
  client: Position;
  start: Position;
  viewport: Viewport;
  offset: Position;
  moved: boolean;
};
const INITIAL: Viewport = { x: 0, y: 0, zoom: 1 };

function svgPosition(svg: SVGSVGElement, clientX: number, clientY: number) {
  const matrix = svg.getScreenCTM();
  return matrix
    ? new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse())
    : null;
}

// The graph's existing coordinate space is 900 × 480. Zoom keeps the anchor in place.
function zoomAround(
  previous: Viewport,
  value: number,
  anchor: Position,
): Viewport {
  const zoom = Math.min(3, Math.max(0.5, value));
  const ratio = zoom / previous.zoom;
  return {
    zoom,
    x: anchor.x - 450 - (anchor.x - 450 - previous.x) * ratio,
    y: anchor.y - 240 - (anchor.y - 240 - previous.y) * ratio,
  };
}

export function useGraphViewport(
  enabled: boolean,
  activatePoint: (id: string) => void,
) {
  const svg = useRef<SVGSVGElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const suppressClick = useRef(false);
  const activate = useRef(activatePoint);
  activate.current = activatePoint;
  const [viewport, setViewport] = useState(INITIAL);
  const [offsets, setOffsets] = useState<Record<string, Position>>({});
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const element = svg.current;
    if (!enabled || !element) return;
    const wheel = (event: WheelEvent) => {
      const anchor = svgPosition(element, event.clientX, event.clientY);
      if (!anchor) return;
      event.preventDefault();
      if (gesture.current) return;
      const units =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? element.clientHeight
            : 1;
      const delta = Math.max(-120, Math.min(120, event.deltaY * units));
      setViewport((previous) =>
        zoomAround(previous, previous.zoom * Math.exp(-delta * 0.002), anchor),
      );
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [enabled]);

  function start(event: PointerEvent<SVGSVGElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    const position = svgPosition(
      event.currentTarget,
      event.clientX,
      event.clientY,
    );
    if (!position) return;
    const pointId =
      event.target instanceof Element
        ? (event.target
            .closest("[data-point-id]")
            ?.getAttribute("data-point-id") ?? null)
        : null;
    suppressClick.current = false;
    gesture.current = {
      pointerId: event.pointerId,
      pointId,
      client: { x: event.clientX, y: event.clientY },
      start: position,
      viewport,
      offset: pointId ? (offsets[pointId] ?? { x: 0, y: 0 }) : { x: 0, y: 0 },
      moved: false,
    };
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const position = svgPosition(
      event.currentTarget,
      event.clientX,
      event.clientY,
    );
    if (!position) return;
    if (
      !current.moved &&
      Math.hypot(
        event.clientX - current.client.x,
        event.clientY - current.client.y,
      ) < 4
    )
      return;
    current.moved = true;
    const dx = position.x - current.start.x,
      dy = position.y - current.start.y;
    if (current.pointId) {
      const id = current.pointId;
      setOffsets((previous) => ({
        ...previous,
        [id]: {
          x: current.offset.x + dx / current.viewport.zoom,
          y: current.offset.y + dy / current.viewport.zoom,
        },
      }));
    } else {
      setViewport({
        ...current.viewport,
        x: current.viewport.x + dx,
        y: current.viewport.y + dy,
      });
    }
  }
  function finish(event: PointerEvent<SVGSVGElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    gesture.current = null;
    setDragging(false);
    suppressClick.current = event.type === "pointerup";
    if (event.type === "pointerup" && !current.moved && current.pointId)
      activate.current(current.pointId);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function reset() {
    setViewport(INITIAL);
    setOffsets({});
  }
  return {
    svg,
    zoom: viewport.zoom,
    transform: `translate(${450 + viewport.x} ${240 + viewport.y}) scale(${viewport.zoom}) translate(-450 -240)`,
    offsets,
    dragging,
    reset,
    setZoom: (value: number) =>
      setViewport((previous) =>
        zoomAround(previous, value, { x: 450, y: 240 }),
      ),
    handlers: {
      onPointerDown: start,
      onPointerMove: move,
      onPointerUp: finish,
      onPointerCancel: finish,
      onLostPointerCapture: finish,
      onClickCapture: (event: MouseEvent<SVGSVGElement>) => {
        if (!suppressClick.current) return;
        event.preventDefault();
        event.stopPropagation();
        suppressClick.current = false;
      },
    },
  };
}
