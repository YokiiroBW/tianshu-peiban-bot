import {
  approach,
  daylight,
  localHour,
  resolveRoom,
  type PreviewInput,
} from "../environment";
import { createReadingForeground } from "./occlusion";

const WIDTH = 1536;
const HEIGHT = 1024;
const assetFiles = {
  day: "room-clean-afternoon-v1.png",
  night: "room-clean-night-v1.png",
  lamp: "room-clean-night-lamp-v1.png",
  character: "character-reading-v1.png",
  sheerOpen: "sheer-open-v1.png",
  sheerClosed: "sheer-closed-v1.png",
} as const;
type AssetName = keyof typeof assetFiles;
type Assets = Record<AssetName, HTMLImageElement>;

export type IllustrationInput = PreviewInput & {
  character: boolean;
  motion: boolean;
};
type VisualState = { day: number; sheer: number; wind: number; lamp: number };
export type IllustrationStatus = "loading" | "ready" | "error";

/** A fixed illustration stage. It does not own living state or perform requests to business APIs. */
export function mountIllustration(
  canvas: HTMLCanvasElement,
  getInput: () => IllustrationInput,
  onStatus: (status: IllustrationStatus) => void,
) {
  const context = canvas.getContext("2d", { alpha: true });
  if (!context) throw new Error("Canvas 2D is unavailable");
  const ctx = context;
  const media = matchMedia("(prefers-reduced-motion: reduce)");
  const readingForeground = createReadingForeground();
  let alive = true;
  let assets: Assets | undefined;
  let lampLayer: HTMLCanvasElement | undefined;
  let frame = 0;
  let timer = 0;
  let lastTime = performance.now();
  let elapsed = 0;
  let frames = 0;
  let current: VisualState | undefined;
  let pixelRatio = 1;
  onStatus("loading");

  function stop() {
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    frame = timer = 0;
  }
  function schedule() {
    if (!alive || !assets || document.hidden || frame) return;
    frame = requestAnimationFrame(draw);
  }
  function targetState(input: IllustrationInput): VisualState {
    const hour = input.hour ?? localHour();
    const values = resolveRoom(hour, input.overrides);
    return {
      // Two registered painted states, not a dark filter over baked afternoon sunlight.
      day: Math.min(1, daylight(hour) * 1.2),
      sheer: values.sheer,
      wind: values.window,
      lamp: values.bedside,
    };
  }
  function drawBase(state: VisualState) {
    if (!assets) return;
    ctx.drawImage(assets.night, 0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = state.day;
    ctx.drawImage(assets.day, 0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = state.lamp * (1 - state.day);
    if (lampLayer) ctx.drawImage(lampLayer, 0, 0);
    ctx.globalAlpha = 1;
  }
  function curtainClip() {
    // Fixed-camera occlusion: window opening minus the foreground plant and chair.
    // These masks are authored for this illustration only; they are not 3D geometry.
    const path = new Path2D();
    path.moveTo(141, 209);
    path.lineTo(593, 88);
    path.lineTo(590, 454);
    path.lineTo(139, 593);
    path.closePath();
    path.moveTo(133, 455);
    path.lineTo(142, 423);
    path.lineTo(172, 431);
    path.lineTo(170, 410);
    path.lineTo(196, 425);
    path.lineTo(213, 415);
    path.lineTo(232, 425);
    path.lineTo(260, 423);
    path.lineTo(262, 446);
    path.lineTo(280, 456);
    path.lineTo(268, 480);
    path.lineTo(269, 498);
    path.lineTo(244, 514);
    path.lineTo(233, 548);
    path.lineTo(245, 571);
    path.lineTo(198, 604);
    path.lineTo(143, 568);
    path.closePath();
    path.moveTo(384, 455);
    path.lineTo(421, 428);
    path.lineTo(515, 413);
    path.lineTo(547, 429);
    path.lineTo(553, 464);
    path.lineTo(612, 481);
    path.lineTo(649, 502);
    path.lineTo(645, 654);
    path.lineTo(374, 654);
    path.closePath();
    ctx.clip(path, "evenodd");
  }
  function drawCurtains(state: VisualState, moving: boolean) {
    if (!assets) return;
    ctx.save();
    curtainClip();
    ctx.filter = `brightness(${0.4 + state.day * 0.6}) saturate(${0.75 + state.day * 0.25})`;
    // A minute shear leaves the hanging edge anchored without alpha seams between image strips.
    const curtain = (
      image: HTMLImageElement,
      source: number[],
      destination: number[],
      phase: number,
    ) => {
      const [sx, sy, sw, sh] = source;
      const [dx, dy, dw, dh] = destination;
      const sway = moving
        ? Math.sin(elapsed * 1.4 + phase) * 0.005 * state.wind
        : 0;
      ctx.save();
      ctx.translate(dx, dy);
      ctx.transform(1, 0, sway, 1, 0, 0);
      ctx.drawImage(image, sx, sy, sw, sh, 0, 0, dw, dh);
      ctx.restore();
    };
    // Opacity implements a gauze appearance; the generated source cloth itself is mostly opaque.
    ctx.globalAlpha = 0.77 * (1 - state.sheer);
    curtain(assets.sheerClosed, [380, 27, 775, 940], [139, 92, 436, 486], 0);
    ctx.globalAlpha = 0.88 * state.sheer;
    curtain(assets.sheerOpen, [130, 182, 165, 536], [146, 183, 105, 399], 0);
    curtain(assets.sheerOpen, [464, 95, 155, 480], [476, 88, 113, 350], 1.3);
    ctx.restore();
  }
  function drawCharacter(
    state: VisualState,
    input: IllustrationInput,
    moving: boolean,
  ) {
    if (!assets || !input.character || input.pose === "away") return;
    ctx.save();
    ctx.filter = `brightness(${0.43 + state.day * 0.57}) saturate(${0.72 + state.day * 0.28})`;
    const breathe = moving ? Math.sin(elapsed * 1.55) * 0.002 : 0;
    // One continuous painted sprite. This is subtle breathing, not skeletal animation or page turning.
    // Feet are registered to the rug, behind the desk. Keep this contact anchor
    // fixed during breathing rather than scaling around the top of the sprite.
    ctx.translate(550, 663);
    ctx.scale(1, 1 + breathe);
    ctx.translate(-550, -663);
    // Reuse the painted seated actor for the finite locations. Rest is a bedside
    // seated projection; idle remains at the chair. Neither invents a new sprite
    // or claims walking, sleep animation, or a different skeletal pose.
    if (input.pose === "resting") ctx.translate(690, 55);
    else if (input.pose === "idle") ctx.translate(5, 6);
    ctx.drawImage(assets.character, 423, 292, 424, 526, 427, 359, 246, 304);
    if (input.garment) {
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(550, 507, 54, 65, 0, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(input.garment, 496, 442, 108, 130);
      ctx.restore();
    }
    ctx.restore();
  }
  function drawForeground(state: VisualState) {
    // Background → curtains → character → foreground, all sharing the same
    // day/night/lamp blend. The near desk and flowers must cover the far actor.
    ctx.save();
    ctx.clip(readingForeground);
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    drawBase(state);
    ctx.restore();
  }
  function draw(now: number) {
    frame = 0;
    if (!alive || !assets || document.hidden) return;
    const dt = Math.min(0.1, Math.max(0, (now - lastTime) / 1000));
    lastTime = now;
    const input = getInput();
    const reduced =
      media.matches || document.documentElement.dataset.motion === "reduce";
    const moving = input.motion && !reduced;
    const target = targetState(input);
    if (!current || reduced) current = { ...target };
    else
      for (const key of Object.keys(target) as (keyof VisualState)[])
        current[key] = approach(current[key], target[key], dt, 5);
    if (moving) elapsed += dt;
    const scale = canvas.width / WIDTH;
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    drawBase(current);
    drawCurtains(current, moving);
    drawCharacter(current, input, moving);
    drawForeground(current);
    canvas.dataset.frames = String(++frames);
    canvas.dataset.motion = moving ? "normal" : "reduced";
    canvas.dataset.day = current.day.toFixed(3);
    canvas.dataset.ready = "true";
    onStatus("ready");
    const transitioning = (Object.keys(target) as (keyof VisualState)[]).some(
      (key) => Math.abs(target[key] - current![key]) > 0.001,
    );
    if (moving || transitioning || input.hour === null) {
      clearTimeout(timer);
      timer = window.setTimeout(
        () => {
          timer = 0;
          schedule();
        },
        moving || transitioning ? 1000 / 30 : 1000,
      );
    }
  }
  function resize() {
    if (!alive) return;
    pixelRatio = Math.min(devicePixelRatio, 2);
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * pixelRatio));
    canvas.height = Math.round((canvas.width * HEIGHT) / WIDTH);
    schedule();
  }
  function update() {
    stop();
    lastTime = performance.now();
    schedule();
  }
  function visibility() {
    stop();
    if (!document.hidden) {
      lastTime = performance.now();
      current = undefined;
      schedule();
    }
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  document.addEventListener("visibilitychange", visibility);
  media.addEventListener("change", update);
  const preferences = new MutationObserver(update);
  preferences.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-motion"],
  });
  resize();
  const pendingImages: HTMLImageElement[] = [];
  const loads = Object.entries(assetFiles).map(
    ([name, file]) =>
      new Promise<[AssetName, HTMLImageElement]>((resolve, reject) => {
        const image = new Image();
        pendingImages.push(image);
        image.onload = () => {
          if (image.naturalWidth !== WIDTH || image.naturalHeight !== HEIGHT)
            reject(new Error(`Wrong layer dimensions: ${file}`));
          else resolve([name as AssetName, image]);
        };
        image.onerror = () =>
          reject(new Error(`Cannot load illustration layer: ${file}`));
        image.src = `/room-illustration-assets/${file}`;
      }),
  );
  void Promise.all(loads)
    .then((entries) => {
      if (!alive) return;
      assets = Object.fromEntries(entries) as Assets;
      lampLayer = document.createElement("canvas");
      lampLayer.width = WIDTH;
      lampLayer.height = HEIGHT;
      const layer = lampLayer.getContext("2d")!;
      layer.drawImage(assets.lamp, 0, 0);
      // A local mask prevents generative relighting changes elsewhere from following the lamp switch.
      layer.globalCompositeOperation = "destination-in";
      const falloff = layer.createRadialGradient(1138, 477, 40, 1138, 477, 365);
      falloff.addColorStop(0, "#fff");
      falloff.addColorStop(0.62, "#fff");
      falloff.addColorStop(1, "#fff0");
      layer.fillStyle = falloff;
      layer.fillRect(0, 0, WIDTH, HEIGHT);
      schedule();
    })
    .catch(() => {
      if (alive) {
        stop();
        canvas.dataset.ready = "false";
        onStatus("error");
      }
    });

  return {
    update,
    dispose() {
      if (!alive) return;
      alive = false;
      stop();
      observer.disconnect();
      preferences.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      media.removeEventListener("change", update);
      for (const image of pendingImages) {
        image.onload = image.onerror = null;
        image.removeAttribute("src");
      }
      assets = undefined;
      if (lampLayer) lampLayer.width = lampLayer.height = 0;
      lampLayer = undefined;
      canvas.width = canvas.height = 0;
    },
  };
}
