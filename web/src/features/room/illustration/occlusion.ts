/** Foreground silhouettes in the shared 1536 × 1024 painted canvas.
 * Reuse the registered room plates, rather than repainting different day/night furniture.
 * This mask is for the seated reading position; a moving actor needs depth-dependent furniture layers.
 */
export function createReadingForeground(): Path2D {
  const mask = new Path2D();
  // Writing desk and the near chair: the back tabletop edge is the occlusion boundary.
  mask.moveTo(475, 715);
  mask.lineTo(795, 617);
  mask.lineTo(943, 681);
  mask.lineTo(944, 856);
  mask.lineTo(845, 916);
  mask.lineTo(827, 956);
  mask.lineTo(630, 957);
  mask.lineTo(474, 858);
  mask.closePath();

  // Vase, leaves and individual flowers extend above the tabletop. Keep the gaps
  // between stems transparent so the character is not cut off by a rectangular patch.
  mask.moveTo(543, 705);
  mask.bezierCurveTo(538, 716, 536, 735, 548, 742);
  mask.bezierCurveTo(570, 750, 575, 729, 567, 710);
  mask.closePath();
  const leaf = (
    x: number,
    y: number,
    rx: number,
    ry: number,
    angle: number,
  ) => {
    mask.moveTo(x + Math.cos(angle) * rx, y + Math.sin(angle) * rx);
    mask.ellipse(x, y, rx, ry, angle, 0, Math.PI * 2);
  };
  leaf(527, 680, 14, 6, 0.5);
  leaf(532, 688, 12, 6, 0.25);
  leaf(527, 706, 13, 6, -0.65);
  leaf(545, 684, 12, 5, -1.9);
  leaf(544, 668, 10, 5, -1.8);
  leaf(578, 665, 12, 5, -0.5);
  leaf(573, 689, 12, 6, -0.4);
  leaf(586, 691, 12, 5, 0.2);
  leaf(577, 704, 12, 6, 0.7);
  leaf(550, 706, 13, 6, 0.45);
  leaf(548, 654, 5, 8, -0.3);
  leaf(518, 658, 5, 7, 0.5);

  const flower = (x: number, y: number) => {
    leaf(x, y, 4, 4, 0);
    for (let i = 0; i < 6; i++) {
      const angle = (i * Math.PI) / 3;
      leaf(x + Math.cos(angle) * 5, y + Math.sin(angle) * 5, 6, 3, angle);
    }
  };
  flower(501, 643);
  flower(532, 636);
  // Narrow stems, matching the source drawing rather than filling their bounding box.
  for (const [x1, y1, x2, y2] of [
    [551, 714, 503, 648],
    [555, 711, 533, 642],
    [557, 711, 565, 645],
    [556, 711, 583, 664],
  ]) {
    mask.moveTo(x1 - 1, y1);
    mask.lineTo(x2 - 1, y2);
    mask.lineTo(x2 + 1, y2);
    mask.lineTo(x1 + 1, y1);
    mask.closePath();
  }
  return mask;
}
