export type CloudNode = {
  id: string;
  label: string;
  count: number;
  message_ids: string[];
};
export type CloudLink = { from: string; to: string; count: number };

/** Stable force layout: only supplied, observed links pull nodes together. */
export function groupCloud(
  nodes: CloudNode[],
  links: CloudLink[],
  width = 720,
  height = 360,
) {
  const points = nodes.map((node, index) => ({
    ...node,
    x: width / 2 + Math.cos(index * 2.39996) * Math.sqrt(index + 1) * 45,
    y: height / 2 + Math.sin(index * 2.39996) * Math.sqrt(index + 1) * 35,
  }));
  const byId = new Map(points.map((point) => [point.id, point]));
  for (let iteration = 0; iteration < 90; iteration++) {
    const force = points.map((point) => ({
      x: (width / 2 - point.x) * 0.003,
      y: (height / 2 - point.y) * 0.003,
    }));
    points.forEach((first, i) =>
      points.slice(i + 1).forEach((second, offset) => {
        const j = i + offset + 1;
        const dx = first.x - second.x,
          dy = first.y - second.y;
        const distance = Math.max(20, Math.hypot(dx, dy));
        const repulsion =
          6000 / (distance * distance) + Math.max(0, 80 - distance) * 0.2;
        force[i].x += (dx / distance) * repulsion;
        force[i].y += (dy / distance) * repulsion;
        force[j].x -= (dx / distance) * repulsion;
        force[j].y -= (dy / distance) * repulsion;
      }),
    );
    for (const link of links) {
      const first = byId.get(link.from),
        second = byId.get(link.to);
      if (!first || !second) continue;
      const dx = second.x - first.x,
        dy = second.y - first.y;
      const strength = 0.008 * Math.min(4, Math.sqrt(link.count));
      const distance = Math.max(1, Math.hypot(dx, dy));
      const spring = ((distance - 140) / distance) * strength;
      const i = points.indexOf(first),
        j = points.indexOf(second);
      force[i].x += dx * spring;
      force[i].y += dy * spring;
      force[j].x -= dx * spring;
      force[j].y -= dy * spring;
    }
    points.forEach((point, i) => {
      point.x = Math.max(50, Math.min(width - 50, point.x + force[i].x * 3));
      point.y = Math.max(40, Math.min(height - 60, point.y + force[i].y * 3));
    });
  }
  return points;
}
