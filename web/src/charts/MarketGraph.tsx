import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Edge } from "../correlation";
import { placeLabels, textWidth, type LabelSpot } from "./labelPlacement";
import useContainerWidth from "./useContainerWidth";

export interface GraphNode {
  label: string;
  /** Portfolio weight (0–1): drives the node's size. */
  weight: number;
  /** Drawn filled in the accent: a member of the maximum independent set. */
  highlighted: boolean;
  /** A member that still has links, all to holdings left outside the set: it
   * stands in for its cluster rather than being uncorrelated with everything,
   * so it's drawn half-filled. */
  linked?: boolean;
  /** Carries an outsized share of the risk: drawn with a madder halo. */
  flagged?: boolean;
}

/** Holdings that move as a block: drawn as a madder blob around their nodes. */
export interface GraphGroup {
  members: number[];
  label: string;
}

interface Props {
  nodes: GraphNode[];
  edges: Edge[];
  /** Full correlation matrix: positions come from it, not from the current
   * edges, so moving θ redraws the links without the nodes jumping around. */
  corr: number[][];
  theta: number;
  groups?: GraphGroup[];
  /** Fixed plot height; omit to fill the parent's remaining height (the
   * parent must be a flex column), never below MIN_FILL_HEIGHT. */
  height?: number;
  tooltip: (i: number) => ReactNode;
  ariaLabel: string;
}

const PAD = 34;
const MIN_FILL_HEIGHT = 360;
/** Least correlation distance between a group member and a holding outside
 * its group (√(2(1−ρ)) at ρ ≈ 0.5), and the extra pull of those springs. */
const APART = 1;
const GROUP_WEIGHT = 3;
const MIN_R = 6;
const MAX_R = 18;

/**
 * Deterministic 2-D embedding where on-screen distance tracks the correlation
 * distance √(2(1−ρ)): correlated holdings sit close, offsetting ones far apart.
 * Plain stress minimisation from a circle start — no randomness, so the same
 * matrix always draws the same picture.
 *
 * Members of a `group` are pulled to half their distance, and everyone else is
 * kept at least a group-width away from them, both with extra weight: eleven
 * correlations can't all fit in a plane, and when something has to give it
 * shouldn't be the group's blob running through holdings outside it.
 */
export function embed(corr: number[][], groups: number[][] = [], iterations = 400): { x: number; y: number }[] {
  const n = corr.length;
  const pos = corr.map((_, i) => ({
    x: Math.cos((2 * Math.PI * i) / Math.max(n, 1)),
    y: Math.sin((2 * Math.PI * i) / Math.max(n, 1)),
  }));
  if (n < 2) return pos.map(() => ({ x: 0, y: 0 }));
  const groupOf = new Map<number, number>();
  groups.forEach((g, k) => g.forEach((i) => groupOf.set(i, k)));
  const base = (i: number, j: number) => {
    const rho = corr[i][j];
    return Math.sqrt(2 * (1 - (Number.isFinite(rho) ? rho : 0)));
  };
  const together = (i: number, j: number) => groupOf.has(i) && groupOf.get(i) === groupOf.get(j);
  const apart = (i: number, j: number) => (groupOf.has(i) || groupOf.has(j)) && !together(i, j);
  const target = (i: number, j: number) =>
    together(i, j) ? base(i, j) / 2 : apart(i, j) ? Math.max(base(i, j), APART) : base(i, j);
  const weight = (i: number, j: number) => (together(i, j) || apart(i, j) ? GROUP_WEIGHT : 1);
  for (let step = 0; step < iterations; step++) {
    const rate = 0.1 * (1 - step / iterations) + 0.01;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[j].x - pos[i].x;
        const dy = pos[j].y - pos[i].y;
        const dist = Math.hypot(dx, dy) || 1e-6;
        const move = (rate * weight(i, j) * (dist - target(i, j))) / dist / 2;
        pos[i].x += dx * move;
        pos[i].y += dy * move;
        pos[j].x -= dx * move;
        pos[j].y -= dy * move;
      }
    }
  }
  return pos;
}

/** Convex hull (Andrew's monotone chain), counter-clockwise; fewer than three
 * points come back as they are. */
export function convexHull(points: { x: number; y: number }[]): { x: number; y: number }[] {
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (sorted.length < 3) return sorted;
  const cross = (o: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const half = (pts: { x: number; y: number }[]) => {
    const out: { x: number; y: number }[] = [];
    for (const p of pts) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...half(sorted), ...half([...sorted].reverse())];
}

/** Padding between a group's blob and the edge of its biggest node… */
const GROUP_PAD = 9;
/** …shrunk down to this around each member when a holding outside the group
 * sits close, and kept this far from that holding. */
const GROUP_MIN_PAD = 4;
const GROUP_CLEARANCE = 5;

type Point = { x: number; y: number };

/** Distance from `p` to a convex polygon given counter-clockwise (a point or
 * a segment when it has fewer than three vertices); 0 inside it. */
export function distanceToHull(p: Point, hull: Point[]): number {
  const toSegment = (a: Point, b: Point) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const t = dx || dy ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy))) : 0;
    return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
  };
  if (hull.length === 1) return Math.hypot(p.x - hull[0].x, p.y - hull[0].y);
  if (hull.length === 2) return toSegment(hull[0], hull[1]);
  const inside = hull.every((a, k) => {
    const b = hull[(k + 1) % hull.length];
    return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x) >= 0;
  });
  if (inside) return 0;
  return Math.min(...hull.map((a, k) => toSegment(a, hull[(k + 1) % hull.length])));
}
/** How far a flagged node's halo reaches past its radius. */
const HALO = 6;
/** Labels draw over links and blobs; a stroke in the card's colour keeps them legible. */
const TEXT_HALO = { stroke: "var(--bg-surface)", strokeWidth: 3, strokeLinejoin: "round", paintOrder: "stroke" } as const;

/** Spots for a label around a circle of radius `r` at (x, y), most natural first. */
function spotsAround(x: number, y: number, r: number): LabelSpot[] {
  const above = y - r - 5;
  const below = y + r + 11;
  return [
    { x, y: above, anchor: "middle" },
    { x, y: below, anchor: "middle" },
    { x: x + r + 2, y: y + 3, anchor: "start" },
    { x: x - r - 2, y: y + 3, anchor: "end" },
    { x, y: above, anchor: "start" },
    { x, y: above, anchor: "end" },
    { x, y: below, anchor: "start" },
    { x, y: below, anchor: "end" },
  ];
}

/** Spots for a group's label around its blob's bounding box, nearest first. */
function spotsAroundBox(cx: number, cy: number, box: { x0: number; y0: number; x1: number; y1: number }): LabelSpot[] {
  const below = box.y1 + 13;
  const above = box.y0 - 5;
  return [
    { x: cx, y: below, anchor: "middle" },
    { x: cx, y: above, anchor: "middle" },
    { x: box.x1 + 4, y: cy + 3, anchor: "start" },
    { x: box.x0 - 4, y: cy + 3, anchor: "end" },
    { x: cx, y: below, anchor: "start" },
    { x: cx, y: below, anchor: "end" },
    { x: cx, y: above, anchor: "start" },
    { x: cx, y: above, anchor: "end" },
    { x: cx, y: below + 16, anchor: "middle" },
    { x: cx, y: above - 16, anchor: "middle" },
  ];
}

/** The market graph: holdings as nodes, an edge wherever ρ ≥ θ, the maximum
 * independent set filled in the accent. Hovering a node isolates its links. */
export default function MarketGraph({ nodes, edges, corr, theta, groups = [], height: fixedHeight, tooltip, ariaLabel }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useContainerWidth(wrapRef);
  // Filling: the wrapper takes the card's leftover height (flex-1) and the SVG
  // is laid absolutely inside it, so the drawing never feeds back into the
  // size it was measured from — the graph can shrink as well as grow.
  const [fillHeight, setFillHeight] = useState(MIN_FILL_HEIGHT);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || fixedHeight !== undefined) return;
    const update = () => setFillHeight(Math.max(Math.floor(el.clientHeight), MIN_FILL_HEIGHT));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fixedHeight]);
  const height = fixedHeight ?? fillHeight;
  const [hover, setHover] = useState<number | null>(null);
  const groupKey = groups.map((g) => g.members.join(",")).join("|");
  // groupKey stands in for `groups`, a fresh array on every render.
  const layout = useMemo(() => embed(corr, groups.map((g) => g.members)), [corr, groupKey]);

  const xs = layout.map((p) => p.x);
  const ys = layout.map((p) => p.y);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  // One scale for both axes, so distances keep their meaning.
  const scale = Math.min(
    (width - 2 * PAD) / Math.max(maxX - minX, 1e-6),
    (height - 2 * PAD) / Math.max(maxY - minY, 1e-6),
  );
  const offX = (width - (maxX - minX) * scale) / 2;
  const offY = (height - (maxY - minY) * scale) / 2;
  const px = (i: number) => offX + (layout[i].x - minX) * scale;
  const py = (i: number) => offY + (layout[i].y - minY) * scale;

  const maxWeight = Math.max(...nodes.map((n) => n.weight), 1e-9);
  const radius = (i: number) => MIN_R + (MAX_R - MIN_R) * Math.sqrt(nodes[i].weight / maxWeight);
  const linked = (e: Edge) => hover === null || e.i === hover || e.j === hover;

  // A polygon stroked as wide as the padding, with round joins, is the hull
  // grown by that padding. The padding gives way to any holding outside the
  // group that would otherwise sit under the blob and read as a member; a
  // circle per member keeps each one wrapped however thin the hull gets.
  const blobs = groups.map((group) => {
    const hull = convexHull(group.members.map((i) => ({ x: px(i), y: py(i) })));
    const room = nodes
      .map((_, j) => j)
      .filter((j) => !group.members.includes(j))
      .map((j) => distanceToHull({ x: px(j), y: py(j) }, hull) - radius(j) - GROUP_CLEARANCE);
    const pad = Math.max(
      Math.min(Math.max(...group.members.map(radius)) + GROUP_PAD, ...room),
      Math.min(...group.members.map(radius)) + GROUP_MIN_PAD,
    );
    const xs = group.members.map(px);
    const ys = group.members.map(py);
    return {
      ...group,
      hull,
      pad,
      rings: group.members.map((i) => ({ x: px(i), y: py(i), r: radius(i) + (nodes[i].flagged ? HALO : 0) + GROUP_MIN_PAD })),
      centre: { x: xs.reduce((s, x) => s + x, 0) / xs.length, y: ys.reduce((s, y) => s + y, 0) / ys.length },
      box: {
        x0: Math.min(...group.members.map((i) => px(i) - Math.max(pad, radius(i) + HALO + GROUP_MIN_PAD))),
        y0: Math.min(...group.members.map((i) => py(i) - Math.max(pad, radius(i) + HALO + GROUP_MIN_PAD))),
        x1: Math.max(...group.members.map((i) => px(i) + Math.max(pad, radius(i) + HALO + GROUP_MIN_PAD))),
        y1: Math.max(...group.members.map((i) => py(i) + Math.max(pad, radius(i) + HALO + GROUP_MIN_PAD))),
      },
    };
  });

  // Node labels first, heaviest holdings with first pick; then the groups'.
  const extent = (i: number) => radius(i) + (nodes[i].flagged ? HALO : 0) + 2;
  const byWeight = nodes.map((_, i) => i).sort((a, b) => nodes[b].weight - nodes[a].weight);
  const spots = placeLabels(
    [
      ...byWeight.map((i) => ({
        width: textWidth(nodes[i].label, nodes[i].highlighted ? 600 : 400),
        candidates: spotsAround(px(i), py(i), extent(i)),
      })),
      ...blobs.map((b) => ({ width: textWidth(b.label, 600), candidates: spotsAroundBox(b.centre.x, b.centre.y, b.box) })),
    ],
    nodes.map((_, i) => ({ x: px(i), y: py(i), r: extent(i) })),
    width,
    height,
  );
  const nodeLabel = new Map(byWeight.map((i, k) => [i, spots[k]]));
  const groupLabel = blobs.map((_, k) => spots[byWeight.length + k]);

  return (
    <div ref={wrapRef} className={fixedHeight === undefined ? "relative min-h-[360px] flex-1" : "relative"}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className={`block max-w-full ${fixedHeight === undefined ? "absolute inset-0" : ""}`}
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
        {blobs.map((b) => (
          // The group's opacity keeps the overlap of fill and stroke from doubling up.
          <g key={b.members.join("-")} opacity={0.14} pointerEvents="none">
            <polygon
              points={b.hull.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="var(--loss)"
              stroke="var(--loss)"
              strokeWidth={2 * b.pad}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {b.rings.map((c) => (
              <circle key={`${c.x},${c.y}`} cx={c.x} cy={c.y} r={c.r} fill="var(--loss)" />
            ))}
          </g>
        ))}
        {edges.map((e) => (
          <line
            key={`${e.i}-${e.j}`}
            x1={px(e.i)}
            y1={py(e.i)}
            x2={px(e.j)}
            y2={py(e.j)}
            stroke={hover !== null && linked(e) ? "var(--corr-pos)" : "var(--text-muted)"}
            strokeWidth={hover !== null && linked(e) ? 2 : 1.5}
            strokeLinecap="round"
            // Stronger links read darker: θ maps to faint, ρ = 1 to solid.
            opacity={linked(e) ? 0.3 + 0.6 * ((e.rho - theta) / Math.max(1 - theta, 1e-6)) : 0.08}
          />
        ))}
        {nodes.map((node, i) => {
          const dim = hover !== null && hover !== i && !edges.some((e) => linked(e) && (e.i === i || e.j === i));
          return (
            <g
              key={node.label}
              opacity={dim ? 0.35 : 1}
              onMouseEnter={() => setHover(i)}
              className="cursor-default"
            >
              {/* hit target bigger than the mark */}
              <circle cx={px(i)} cy={py(i)} r={radius(i) + 8} fill="transparent" />
              {node.flagged && (
                <circle cx={px(i)} cy={py(i)} r={radius(i) + 4.5} fill="none" stroke="var(--loss)" strokeWidth={2.5} opacity={0.55} />
              )}
              <circle
                cx={px(i)}
                cy={py(i)}
                r={radius(i)}
                fill={
                  !node.highlighted
                    ? "var(--bg-surface)"
                    : node.linked
                      ? "color-mix(in oklab, var(--accent) 35%, var(--bg-surface))"
                      : "var(--accent)"
                }
                stroke={!node.highlighted ? "var(--text-secondary)" : node.linked ? "var(--accent)" : "var(--bg-surface)"}
                strokeWidth={2}
              />
              <text
                x={nodeLabel.get(i)!.x}
                y={nodeLabel.get(i)!.y}
                textAnchor={nodeLabel.get(i)!.anchor}
                className={node.highlighted ? "fill-accent font-semibold" : "fill-secondary"}
                style={{ fontSize: 11, pointerEvents: "none" }}
                {...TEXT_HALO}
              >
                {node.label}
              </text>
            </g>
          );
        })}
        {blobs.map((b, k) => (
          <text
            key={b.members.join("-")}
            x={groupLabel[k].x}
            y={groupLabel[k].y}
            textAnchor={groupLabel[k].anchor}
            className="fill-loss font-semibold tabular-nums"
            style={{ fontSize: 11, pointerEvents: "none" }}
            {...TEXT_HALO}
          >
            {b.label}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-hairline-strong bg-elevated px-3 py-2 text-xs shadow-pop"
          style={{
            left: Math.min(px(hover) + radius(hover) + 10, Math.max(0, width - 210)),
            top: Math.max(0, py(hover) - 20),
          }}
          role="tooltip"
        >
          {tooltip(hover)}
        </div>
      )}
    </div>
  );
}
