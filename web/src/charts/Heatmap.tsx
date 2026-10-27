import { useRef, useState, type ReactNode } from "react";
import { rho as rhoText } from "../format";
import useContainerWidth from "./useContainerWidth";

interface Props {
  labels: string[];
  /** Square, symmetric matrix in −1…1; NaN cells render as "no data". */
  matrix: number[][];
  /** Row/column order to draw in (e.g. clusterOrder). */
  order: number[];
  /** Indices whose axis label is drawn in the accent (e.g. the MIS). */
  highlight?: Set<number>;
  tooltip: (i: number, j: number) => ReactNode;
  legend: { negative: string; positive: string };
  ariaLabel: string;
}

const LABEL_W = 64;
const LABEL_H = 22;
const MAX_CELL = 46;
const GAP = 2;
/** Cells this size or bigger carry their ρ as text. */
const MIN_CELL_FOR_VALUE = 30;

/** Diverging fill: madder above zero, woad below, neutral grey at zero (the
 * --corr-* tokens). Mixed in OKLab so equal |ρ| reads as equal weight. */
export function correlationFill(rho: number): string {
  if (!Number.isFinite(rho)) return "var(--bg-elevated)";
  const pole = rho >= 0 ? "var(--corr-pos)" : "var(--corr-neg)";
  return `color-mix(in oklab, ${pole} ${Math.round(Math.min(1, Math.abs(rho)) * 100)}%, var(--corr-mid))`;
}

/** Correlation matrix as a diverging heatmap with a per-cell hover tooltip.
 * The diagonal (always 1) is drawn blank so it doesn't dominate the scale. */
export default function Heatmap({ labels, matrix, order, highlight, tooltip, legend, ariaLabel }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useContainerWidth(wrapRef);
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const n = order.length;
  const cell = Math.max(12, Math.min(MAX_CELL, Math.floor((width - LABEL_W) / Math.max(n, 1))));
  const size = cell * n;
  const height = size + LABEL_H;

  return (
    <div ref={wrapRef} className="relative">
      <svg
        viewBox={`0 0 ${LABEL_W + size} ${height}`}
        width={LABEL_W + size}
        height={height}
        className="block max-w-full"
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
        {order.map((row, r) => (
          <g key={row}>
            <text
              x={LABEL_W - 8}
              y={r * cell + cell / 2 + 3.5}
              textAnchor="end"
              className={highlight?.has(row) ? "fill-accent font-semibold" : hover?.i === row ? "fill-primary" : "fill-secondary"}
              style={{ fontSize: 11 }}
            >
              {labels[row]}
            </text>
            {order.map((col, c) => {
              const rho = matrix[row][col];
              const diagonal = row === col;
              const active = hover && (hover.i === row || hover.j === col);
              return (
                <g key={col} onMouseEnter={() => setHover(diagonal ? null : { i: row, j: col })}>
                  <rect
                    x={LABEL_W + c * cell + GAP / 2}
                    y={r * cell + GAP / 2}
                    width={cell - GAP}
                    height={cell - GAP}
                    rx={3}
                    fill={diagonal ? "var(--bg-elevated)" : correlationFill(rho)}
                    opacity={hover === null || active ? 1 : 0.6}
                  />
                  {!diagonal && cell >= MIN_CELL_FOR_VALUE && (
                    <text
                      x={LABEL_W + c * cell + cell / 2}
                      y={r * cell + cell / 2 + 3.5}
                      textAnchor="middle"
                      className={Math.abs(rho) >= 0.6 ? "fill-surface tabular-nums" : "fill-primary tabular-nums"}
                      style={{ fontSize: 10.5, pointerEvents: "none" }}
                    >
                      {rhoText(rho)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        ))}
        {order.map((col, c) => (
          <text
            key={col}
            x={LABEL_W + c * cell + cell / 2}
            y={size + 15}
            textAnchor="middle"
            className={highlight?.has(col) ? "fill-accent font-semibold" : hover?.j === col ? "fill-primary" : "fill-secondary"}
            style={{ fontSize: cell >= 40 ? 11 : 9.5 }}
          >
            {labels[col]}
          </text>
        ))}
      </svg>

      <div className="mt-3 flex items-center gap-2 text-[11px] text-secondary" style={{ marginLeft: LABEL_W }}>
        <span className="tabular-nums">−1</span>
        <span
          className="h-2 w-40 rounded-full"
          style={{ background: "linear-gradient(to right, var(--corr-neg), var(--corr-mid), var(--corr-pos))" }}
          aria-hidden
        />
        <span className="tabular-nums">+1</span>
        <span className="ml-2 text-muted">
          {legend.negative} · {legend.positive}
        </span>
      </div>

      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-hairline-strong bg-elevated px-3 py-2 text-xs shadow-pop"
          style={{
            left: Math.min(LABEL_W + (order.indexOf(hover.j) + 1) * cell + 8, Math.max(0, width - 200)),
            top: order.indexOf(hover.i) * cell,
          }}
          role="tooltip"
        >
          {tooltip(hover.i, hover.j)}
        </div>
      )}
    </div>
  );
}
