import { useMemo, useRef, useState, type ReactNode } from "react";
import type { Edge } from "../correlation";
import useContainerWidth from "./useContainerWidth";

export interface GraphNode {
  label: string;
  /** Portfolio weight (0–1): drives the node's size. */
  weight: number;
  /** Drawn filled in the accent: a member of the maximum independent set. */
  highlighted: boolean;
}

interface Props {
  nodes: GraphNode[];
  edges: Edge[];
  /** Full correlation matrix: positions come from it, not from the current
   * edges, so moving θ redraws the links without the nodes jumping around. */
  corr: number[][];
  theta: number;
  height?: number;
  tooltip: (i: number) => ReactNode;
  ariaLabel: string;
}

const PAD = 34;
const MIN_R = 6;
const MAX_R = 18;

/**
 * Deterministic 2-D embedding where on-screen distance tracks the correlation
 * distance √(2(1−ρ)): correlated holdings sit close, offsetting ones far apart.
 * Plain stress minimisation from a circle start — no randomness, so the same
 * matrix always draws the same picture.
 */
export function embed(corr: number[][], iterations = 400): { x: number; y: number }[] {
  const n = corr.length;
  const pos = corr.map((_, i) => ({
    x: Math.cos((2 * Math.PI * i) / Math.max(n, 1)),
    y: Math.sin((2 * Math.PI * i) / Math.max(n, 1)),
  }));
  if (n < 2) return pos.map(() => ({ x: 0, y: 0 }));
  const target = (i: number, j: number) => {
    const rho = corr[i][j];
    return Math.sqrt(2 * (1 - (Number.isFinite(rho) ? rho : 0)));
  };
  for (let step = 0; step < iterations; step++) {
    const rate = 0.1 * (1 - step / iterations) + 0.01;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[j].x - pos[i].x;
        const dy = pos[j].y - pos[i].y;
        const dist = Math.hypot(dx, dy) || 1e-6;
        const move = (rate * (dist - target(i, j))) / dist / 2;
        pos[i].x += dx * move;
        pos[i].y += dy * move;
        pos[j].x -= dx * move;
        pos[j].y -= dy * move;
      }
    }
  }
  return pos;
}

/** The market graph: holdings as nodes, an edge wherever ρ ≥ θ, the maximum
 * independent set filled in the accent. Hovering a node isolates its links. */
export default function MarketGraph({ nodes, edges, corr, theta, height = 360, tooltip, ariaLabel }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useContainerWidth(wrapRef);
  const [hover, setHover] = useState<number | null>(null);
  const layout = useMemo(() => embed(corr), [corr]);

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

  return (
    <div ref={wrapRef} className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="block max-w-full"
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
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
              <circle
                cx={px(i)}
                cy={py(i)}
                r={radius(i)}
                fill={node.highlighted ? "var(--accent)" : "var(--bg-surface)"}
                stroke={node.highlighted ? "var(--bg-surface)" : "var(--text-secondary)"}
                strokeWidth={2}
              />
              <text
                x={px(i)}
                y={py(i) - radius(i) - 5}
                textAnchor="middle"
                className={node.highlighted ? "fill-accent font-semibold" : "fill-secondary"}
                style={{ fontSize: 11, pointerEvents: "none" }}
              >
                {node.label}
              </text>
            </g>
          );
        })}
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
