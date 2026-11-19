import { useRef, useState } from "react";
import { niceCeil } from "./Bars";
import useContainerWidth from "./useContainerWidth";

export interface StepPoint {
  key: string;
  value: number;
  /** Axis label under this point ("" for none). */
  label: string;
}

interface Props {
  points: StepPoint[];
  height?: number;
  /** CSS colour of the line; the fill is the same colour at low alpha. */
  color?: string;
  formatValue: (v: number) => string;
  tooltipTitle: (p: StepPoint) => string;
  ariaLabel: string;
}

// Same frame as Bars, so the two read as one family on the page.
const PAD_TOP = 12;
const PAD_BOTTOM = 22;
const PAD_LEFT = 52;

/** A step-shaped area: each point holds its value across its whole slot, so a
 * series that moves in plateaus (daily interest on a balance) reads as levels
 * rather than a forest of thin bars. Hover shows the day under the cursor. */
export default function StepArea({ points, height = 200, color = "var(--gain)", formatValue, tooltipTitle, ariaLabel }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useContainerWidth(wrapRef);
  const [hover, setHover] = useState<number | null>(null);
  const n = points.length;
  const plotH = height - PAD_TOP - PAD_BOTTOM;
  const plotW = width - PAD_LEFT;
  const niceMax = niceCeil(Math.max(...points.map((p) => p.value), 0));
  const step = plotW / Math.max(n, 1);
  const x = (i: number) => PAD_LEFT + i * step;
  const y = (v: number) => PAD_TOP + plotH - (niceMax > 0 ? (v / niceMax) * plotH : 0);
  const ticks = [niceMax / 3, (2 * niceMax) / 3, niceMax];

  if (n === 0) return null;

  let line = `M${x(0)},${y(points[0].value)}`;
  points.forEach((p, i) => {
    if (i > 0) line += ` V${y(p.value)}`;
    line += ` H${x(i + 1)}`;
  });
  const area = `${line} V${y(0)} H${x(0)} Z`;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - PAD_LEFT) / step);
    setHover(i >= 0 && i < n ? i : null);
  };
  const hovered = hover !== null ? points[hover] : null;
  const hoverX = hover !== null ? x(hover) + step / 2 : 0;

  return (
    <div className="relative" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="block max-w-full"
        role="img"
        aria-label={ariaLabel}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {niceMax > 0 &&
          ticks.map((t) => (
            <g key={t}>
              <line x1={PAD_LEFT} x2={width} y1={y(t)} y2={y(t)} stroke="var(--hairline)" strokeWidth={1} />
              <text x={0} y={y(t) + 3.5} className="fill-muted tabular-nums" style={{ fontSize: 10 }}>
                {formatValue(t)}
              </text>
            </g>
          ))}
        <path d={area} fill={color} opacity={0.14} />
        <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
        <line x1={PAD_LEFT} x2={width} y1={y(0)} y2={y(0)} stroke="var(--hairline-strong)" strokeWidth={1} />
        {points.map((p, i) => {
          if (!p.label) return null;
          const w = p.label.length * 6.5;
          const cx = Math.min(Math.max(x(i) + step / 2, PAD_LEFT + w / 2), width - w / 2);
          return (
            <text key={p.key} x={cx} y={height - 6} textAnchor="middle" className="fill-muted" style={{ fontSize: 11 }}>
              {p.label}
            </text>
          );
        })}
        {hovered && (
          <g pointerEvents="none">
            <line x1={hoverX} x2={hoverX} y1={PAD_TOP} y2={y(0)} stroke="var(--hairline-strong)" strokeWidth={1} />
            <circle cx={hoverX} cy={y(hovered.value)} r={4} fill="var(--surface)" stroke={color} strokeWidth={2} />
          </g>
        )}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg border border-hairline-strong bg-elevated px-3 py-2 text-xs shadow-pop"
          style={{ left: Math.min(Math.max(hoverX, PAD_LEFT + 60), width - 60) }}
          role="tooltip"
        >
          <div className="mb-0.5 font-semibold text-primary">{tooltipTitle(hovered)}</div>
          <div className="tabular-nums text-primary">{formatValue(hovered.value)}</div>
        </div>
      )}
    </div>
  );
}
