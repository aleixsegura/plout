import { useId, useRef, useState } from "react";
import useContainerWidth from "./useContainerWidth";

export interface BarSegment {
  key: string;
  value: number;
  color: string;
  label: string;
}

export interface Bar {
  key: string;
  /** Axis label under the bar. */
  label: string;
  /** Used when `segments` is absent (single-colour bar). */
  value?: number;
  color?: string;
  /** Stacked segments, bottom first. */
  segments?: BarSegment[];
}

interface Props {
  bars: Bar[];
  height?: number;
  /** Bar drawn with the accent colour (e.g. the selected month). */
  highlightKey?: string | null;
  onSelect?: (key: string) => void;
  formatValue: (v: number) => string;
  /** Tooltip title per bar (defaults to the label). */
  tooltipTitle?: (bar: Bar) => string;
  ariaLabel: string;
  /** Show every bar's total above it (only for a handful of bars). */
  showValues?: boolean;
}

const PAD_TOP = 12;
const PAD_BOTTOM = 22;
const PAD_X = 6;
const PAD_LEFT = 52; // gutter for the tick labels, so they never sit on a bar
const GAP_PX = 2; // surface gap between stacked segments

function total(bar: Bar): number {
  return bar.segments ? bar.segments.reduce((s, x) => s + x.value, 0) : bar.value ?? 0;
}

/** Vertical bars (optionally stacked), hand-rolled SVG so the palette tokens
 * apply directly. Rounded data-ends at the top, a hairline baseline, three
 * recessive gridlines and a per-bar hover tooltip. */
export default function Bars({
  bars,
  height = 200,
  highlightKey = null,
  onSelect,
  formatValue,
  tooltipTitle,
  ariaLabel,
  showValues = false,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<string | null>(null);
  const id = useId();
  const width = useContainerWidth(wrapRef);
  const plotH = height - PAD_TOP - PAD_BOTTOM;
  const max = Math.max(...bars.map(total), 0);
  const niceMax = niceCeil(max);
  const n = bars.length;
  const slot = (width - PAD_LEFT - PAD_X) / Math.max(n, 1);
  const barW = Math.min(slot * 0.62, 48);
  const y = (v: number) => PAD_TOP + plotH - (niceMax > 0 ? (v / niceMax) * plotH : 0);
  const ticks = [niceMax / 3, (2 * niceMax) / 3, niceMax];
  const hovered = bars.find((b) => b.key === hover) ?? null;

  return (
    <div className="relative" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="block max-w-full"
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
        {niceMax > 0 &&
          ticks.map((t) => (
            <g key={t}>
              <line x1={PAD_LEFT} x2={width - PAD_X} y1={y(t)} y2={y(t)} stroke="var(--hairline)" strokeWidth={1} />
              <text x={PAD_LEFT - 8} y={y(t) + 3.5} textAnchor="end" className="fill-muted tabular-nums" style={{ fontSize: 10 }}>
                {formatValue(t)}
              </text>
            </g>
          ))}
        <line x1={PAD_LEFT} x2={width - PAD_X} y1={y(0)} y2={y(0)} stroke="var(--hairline-strong)" strokeWidth={1} />
        {bars.map((bar, i) => {
          const x = PAD_LEFT + slot * i + (slot - barW) / 2;
          const isHi = bar.key === highlightKey;
          const isHover = bar.key === hover;
          const sum = total(bar);
          const clip = `${id}-clip-${i}`;
          const topY = y(sum);
          const segs = bar.segments ?? [{ key: bar.key, value: bar.value ?? 0, color: bar.color ?? "var(--chart-1)", label: bar.label }];
          let cursor = 0;
          return (
            <g
              key={bar.key}
              className={onSelect ? "cursor-pointer" : ""}
              opacity={hover === null || isHover ? 1 : 0.55}
              onMouseEnter={() => setHover(bar.key)}
              onClick={onSelect ? () => onSelect(bar.key) : undefined}
            >
              {/* hit target wider than the bar */}
              <rect x={PAD_LEFT + slot * i} y={PAD_TOP} width={slot} height={plotH + PAD_BOTTOM} fill="transparent" />
              <clipPath id={clip}>
                <rect x={x} y={topY} width={barW} height={Math.max(y(0) - topY, 0)} rx={4} ry={4} />
              </clipPath>
              <g clipPath={`url(#${clip})`}>
                {segs.map((seg) => {
                  const y0 = y(cursor);
                  cursor += seg.value;
                  const y1 = y(cursor);
                  const h = Math.max(y0 - y1 - GAP_PX, 0);
                  return (
                    <rect
                      key={seg.key}
                      x={x}
                      y={y1 + (h > 0 ? GAP_PX / 2 : 0)}
                      width={barW}
                      height={h}
                      fill={isHi && !bar.segments ? "var(--accent)" : seg.color}
                    />
                  );
                })}
              </g>
              {/* the bottom edge stays square: only the data end is rounded */}
              <rect x={x} y={Math.max(y(0) - 4, topY)} width={barW} height={Math.min(4, y(0) - topY)} fill={isHi && !bar.segments ? "var(--accent)" : segs[0]?.color} />
              {isHi && (
                <line x1={x - 4} x2={x + barW + 4} y1={y(0) + 3} y2={y(0) + 3} stroke="var(--accent)" strokeWidth={2} strokeLinecap="round" />
              )}
              {showValues && sum > 0 && (
                <text x={x + barW / 2} y={topY - 6} textAnchor="middle" className="fill-secondary tabular-nums" style={{ fontSize: 10 }}>
                  {formatValue(sum)}
                </text>
              )}
              <text
                x={x + barW / 2}
                y={height - 6}
                textAnchor="middle"
                className={isHi ? "fill-accent font-semibold" : "fill-muted"}
                style={{ fontSize: 11 }}
              >
                {bar.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute left-1/2 top-0 z-10 -translate-x-1/2 rounded-lg border border-hairline-strong bg-elevated px-3 py-2 text-xs shadow-pop"
          role="tooltip"
        >
          <div className="mb-1 font-semibold text-primary">{tooltipTitle ? tooltipTitle(hovered) : hovered.label}</div>
          {(hovered.segments ?? []).filter((s) => s.value > 0).slice().reverse().map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5 text-secondary">
                <span className="inline-block size-2 rounded-[2px]" style={{ background: s.color }} />
                {s.label}
              </span>
              <span className="tabular-nums text-primary">{formatValue(s.value)}</span>
            </div>
          ))}
          <div className={`flex items-center justify-between gap-4 ${hovered.segments ? "mt-1 border-t border-hairline pt-1" : ""}`}>
            <span className="text-secondary">Total</span>
            <span className="font-semibold tabular-nums text-primary">{formatValue(total(hovered))}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Round a maximum up to a "nice" tick ceiling (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8 × 10^n). */
function niceCeil(v: number): number {
  if (v <= 0) return 0;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const f = v / base;
  const nice = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((n) => f <= n) ?? 10;
  return nice * base;
}
