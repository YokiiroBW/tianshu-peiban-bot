import * as THREE from "three";
import {
  approach,
  localHour,
  resolveRoom,
  type PreviewInput,
  type RoomValues,
} from "./environment";
import { buildRoom } from "./scene";

export type RenderStatus = "loading" | "ready" | "lost" | "error";
export function mountRoom(
  canvas: HTMLCanvasElement,
  getInput: () => PreviewInput,
  simple: boolean,
  onStatus: (status: RenderStatus) => void,
) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: "low-power",
  });
  const room = buildRoom();
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, simple ? 1 : 1.5));
  room.quality(simple);
  const camera = new THREE.OrthographicCamera(-5, 5, 4, -4, 0.1, 60);
  camera.position.set(9, 7.8, 11);
  camera.lookAt(0, 1.2, 0);
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let reduced =
    motion.matches || document.documentElement.dataset.motion === "reduce";
  let alive = true;
  let lost = false;
  let failed = false;
  let frame = 0;
  let timer = 0;
  let previous = performance.now();
  let elapsed = 0;
  let wind = 0;
  let rendered = 0;
  let current: RoomValues | null = null;

  function stop() {
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    frame = timer = 0;
  }
  function schedule() {
    if (!alive || lost || failed || document.hidden || frame) return;
    frame = requestAnimationFrame(draw);
  }
  function draw(now: number) {
    frame = 0;
    if (!alive || lost || failed || document.hidden) return;
    // Bound background recovery and long stalls; never replay missed frames.
    const dt = Math.min(0.1, Math.max(0, (now - previous) / 1000));
    previous = now;
    const input = getInput();
    const hour = input.hour ?? localHour();
    const target = resolveRoom(hour, input.overrides);
    if (!current || reduced) current = { ...target };
    else
      for (const key of Object.keys(target) as (keyof RoomValues)[])
        current[key] = approach(current[key], target[key], dt, 7);
    wind = reduced ? 0 : approach(wind, current.window, dt, 1.5);
    if (!reduced) elapsed += dt;
    room.update(hour, current, wind, elapsed);
    room.appearance(input);
    try {
      renderer.render(room.scene, camera);
      rendered++;
      canvas.dataset.frames = String(rendered);
      canvas.dataset.wind = wind.toFixed(4);
      canvas.dataset.window = current.window.toFixed(4);
      canvas.dataset.motion = reduced ? "reduced" : "normal";
      canvas.dataset.drawCalls = String(renderer.info.render.calls);
      canvas.dataset.triangles = String(renderer.info.render.triangles);
      onStatus("ready");
    } catch {
      failed = true;
      onStatus("error");
      return;
    }
    // One owner and one pending callback. Static reduced-motion previews idle fully.
    if (!reduced || input.hour === null) {
      clearTimeout(timer);
      timer = window.setTimeout(
        () => {
          timer = 0;
          schedule();
        },
        reduced ? 1000 : 1000 / 30,
      );
    }
  }
  function resize() {
    if (!alive || lost) return;
    const { width, height } = canvas.getBoundingClientRect();
    const aspect = width / Math.max(height, 1);
    const viewHeight = Math.max(8.2, 9.3 / aspect);
    camera.left = (-viewHeight * aspect) / 2;
    camera.right = (viewHeight * aspect) / 2;
    camera.top = viewHeight / 2;
    camera.bottom = -viewHeight / 2;
    camera.updateProjectionMatrix();
    renderer.setSize(Math.max(1, width), Math.max(1, height), false);
    schedule();
  }
  function visibility() {
    stop();
    if (!document.hidden) {
      previous = performance.now();
      current = null;
      wind = 0;
      schedule();
    }
  }
  function preference() {
    const next =
      motion.matches || document.documentElement.dataset.motion === "reduce";
    if (next === reduced) return;
    reduced = next;
    stop();
    previous = performance.now();
    schedule();
  }
  function contextLost(event: Event) {
    event.preventDefault();
    lost = true;
    stop();
    onStatus("lost");
  }
  function contextRestored() {
    lost = false;
    failed = false;
    current = null;
    wind = 0;
    previous = performance.now();
    resize();
  }
  const observer = new ResizeObserver(resize);
  const preferences = new MutationObserver(preference);
  observer.observe(canvas);
  preferences.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-motion"],
  });
  document.addEventListener("visibilitychange", visibility);
  motion.addEventListener("change", preference);
  canvas.addEventListener("webglcontextlost", contextLost);
  canvas.addEventListener("webglcontextrestored", contextRestored);
  resize();
  return {
    update: schedule,
    dispose() {
      alive = false;
      stop();
      observer.disconnect();
      preferences.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      motion.removeEventListener("change", preference);
      canvas.removeEventListener("webglcontextlost", contextLost);
      canvas.removeEventListener("webglcontextrestored", contextRestored);
      room.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}
