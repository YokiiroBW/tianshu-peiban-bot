import type { Stats, TopicPage } from "./groupTypes";

export type GraphPoint = {
  id: string;
  label: string;
  x: number;
  y: number;
  size: number;
  tone: number;
  topic?: string;
  count?: number;
};
export type GraphLink = {
  from: string;
  to: string;
  count: number;
  locators?: string[];
};

export function pointSeed(id: string) {
  let hash = 0;
  for (const character of id)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  // Adjacent numeric account IDs still need distinct motion phases.
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b);
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b);
  return (hash ^ (hash >>> 16)) >>> 0;
}

// A stable initial constellation, relaxed using only the observed links.
// Coordinates describe the drawing, never semantic distance or relationship strength.
function arrange(points: GraphPoint[], links: GraphLink[]) {
  const index = new Map(points.map((point) => [point.id, point]));
  const ordinals = new Map(points.map((point, i) => [point.id, i]));
  points.forEach((point, i) => {
    const angle = i * 2.399963 + 0.35;
    const radius = Math.sqrt((i + 0.3) / Math.max(1, points.length));
    point.x = 450 + Math.cos(angle) * 300 * radius;
    point.y = 225 + Math.sin(angle) * 150 * radius;
  });
  const anchors = points.map(({ x, y }) => ({ x, y }));
  for (let iteration = 0; iteration < 100; iteration++) {
    const forces = points.map((point, i) => ({
      x: (anchors[i].x - point.x) * 0.025,
      y: (anchors[i].y - point.y) * 0.025,
    }));
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const a = points[i],
          b = points[j];
        const dx = b.x - a.x || 0.1,
          dy = b.y - a.y || 0.1;
        const width = (a.topic ? 72 : 48) + (b.topic ? 72 : 48);
        const height = 60;
        const distance = Math.hypot(dx / width, dy / height);
        if (distance >= 1) continue;
        const push = (1 - distance) * 0.32;
        const fx = (dx / Math.max(distance, 0.1)) * push;
        const fy = (dy / Math.max(distance, 0.1)) * push;
        forces[i].x -= fx;
        forces[i].y -= fy;
        forces[j].x += fx;
        forces[j].y += fy;
      }
    }
    for (const link of links) {
      const a = index.get(link.from),
        b = index.get(link.to);
      if (!a || !b || a === b) continue;
      const distance = Math.hypot(b.x - a.x, b.y - a.y);
      const pull =
        (Math.max(0, distance - 210) * 0.012) / Math.max(1, distance);
      const dx = (b.x - a.x) * pull,
        dy = (b.y - a.y) * pull;
      const ai = ordinals.get(a.id)!,
        bi = ordinals.get(b.id)!;
      forces[ai].x += dx;
      forces[ai].y += dy;
      forces[bi].x -= dx;
      forces[bi].y -= dy;
    }
    points.forEach((point, i) => {
      point.x = Math.min(785, Math.max(115, point.x + forces[i].x));
      point.y = Math.min(365, Math.max(80, point.y + forces[i].y));
    });
  }
  if (points.length) {
    const centerX =
      (Math.min(...points.map((point) => point.x)) +
        Math.max(...points.map((point) => point.x))) /
      2;
    const centerY =
      (Math.min(...points.map((point) => point.y)) +
        Math.max(...points.map((point) => point.y))) /
      2;
    points.forEach((point) => {
      point.x += 450 - centerX;
      point.y += 225 - centerY;
    });
  }
  return { points, links, index };
}

export function buildGroupGraph(
  mode: "interactions" | "topics",
  stats?: Stats,
  topics?: TopicPage,
) {
  const points: GraphPoint[] = [],
    links: GraphLink[] = [];
  if (mode === "interactions") {
    const authors = [...(stats?.authors ?? [])]
      .sort(
        (a, b) =>
          b.messages - a.messages || a.sender_id.localeCompare(b.sender_id),
      )
      .slice(0, 80);
    const maximum = Math.max(1, ...authors.map((author) => author.messages));
    authors.forEach((author) =>
      points.push({
        id: author.sender_id,
        label: `QQ ${author.sender_id}`,
        x: 0,
        y: 0,
        size: 4 + Math.sqrt(author.messages / maximum) * 2.5,
        tone: pointSeed(author.sender_id) % 4,
        count: author.messages,
      }),
    );
    const known = new Set(authors.map((author) => author.sender_id));
    stats?.edges.forEach((edge) => {
      if (known.has(edge.from_sender_id) && known.has(edge.to_sender_id))
        links.push({
          from: edge.from_sender_id,
          to: edge.to_sender_id,
          count: edge.interaction_count,
          locators: edge.evidence_locators,
        });
    });
  } else {
    const items = (topics?.items ?? [])
      .flatMap((segment) =>
        segment.topics.map((topic) => ({
          ...topic,
          key: `${segment.segment_id}/${topic.topic_id}`,
        })),
      )
      .slice(0, 80);
    const people = [
      ...new Set(items.flatMap((topic) => topic.person_ids)),
    ].sort();
    people.forEach((id) =>
      points.push({
        id,
        label: id.replace(/^person:/, ""),
        x: 0,
        y: 0,
        size: 5,
        tone: pointSeed(id) % 4,
      }),
    );
    items.forEach((topic) => {
      points.push({
        id: topic.key,
        label: topic.summary,
        topic: topic.key,
        x: 0,
        y: 0,
        size: 7,
        tone: 4,
        count: topic.person_ids.length,
      });
      topic.person_ids.forEach((id) =>
        links.push({ from: id, to: topic.key, count: 1 }),
      );
    });
  }
  return arrange(points, links);
}

export function graphLinkPath(a: GraphPoint, b: GraphPoint) {
  if (a.id === b.id)
    return `M ${a.x - 10} ${a.y - a.size} c -60 -65 80 -65 20 0`;
  const dx = b.x - a.x,
    dy = b.y - a.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const bend = Math.min(48, length * 0.15);
  return `M ${a.x} ${a.y} Q ${(a.x + b.x) / 2 - (dy / length) * bend} ${(a.y + b.y) / 2 + (dx / length) * bend} ${b.x} ${b.y}`;
}
