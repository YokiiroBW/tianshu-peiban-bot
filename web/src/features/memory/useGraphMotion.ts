import { useEffect, useRef, useState } from "react";

/** Graph motion is independent of decorative page transitions and can be paused. */
export function useGraphMotion(active: boolean, paused: boolean) {
  const [floating, setFloating] = useState(true);
  const elapsed = useRef(0);
  const [time, setTime] = useState(0);

  useEffect(() => {
    if (!active || !floating || paused) return;
    let frame = 0,
      previous = 0,
      published = 0;
    const tick = (now: number) => {
      // Use elapsed time even when an embedded preview throttles animation frames.
      if (previous) elapsed.current += (now - previous) / 1000;
      previous = now;
      if (now - published >= 32) {
        setTime(elapsed.current);
        published = now;
      }
      frame = requestAnimationFrame(tick);
    };
    const visible = () => {
      cancelAnimationFrame(frame);
      previous = 0;
      if (!document.hidden) frame = requestAnimationFrame(tick);
    };
    visible();
    document.addEventListener("visibilitychange", visible);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [active, floating, paused]);

  return { time, floating, toggle: () => setFloating((value) => !value) };
}
