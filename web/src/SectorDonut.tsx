import { useMemo } from "react";
import type { Stock } from "./types";
import { useTranslation } from "./i18n/LanguageContext";
import { sectorBreakdown, sectorLabel, sectorVar, type Sector } from "./sectors";
import { donutSegments } from "./charts/arc";

const SIZE = 220;
const CENTER = SIZE / 2;
const RADIUS = 84;
const THICKNESS = 30;
const GAP_DEG = 2; // angular gap between slices, in degrees
const CIRC = 2 * Math.PI * RADIUS;

const pct = (fraction: number) =>
  `${(fraction * 100).toLocaleString("es-ES", { maximumFractionDigits: 1, useGrouping: true })}%`;

const eur = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
  useGrouping: true,
});

interface Props {
  stocks: Stock[];
  active: Sector | null;
  onActiveChange: (sector: Sector | null) => void;
  /** EUR market value per symbol; switches to value weighting. */
  valueBySymbol?: Map<string, number>;
  /** Total cash (DEGIRO + Revolut) in EUR, shown as a stock-less "Cash" slice. */
  cashEur?: number;
  /** Data sources named in the footnote when value-weighted (default "DEGIRO"). */
  sourceLabel?: string;
}

export default function SectorDonut({
  stocks,
  active,
  onActiveChange,
  valueBySymbol,
  cashEur,
  sourceLabel = "DEGIRO",
}: Props) {
  const { t } = useTranslation();
  const slices = useMemo(
    () => sectorBreakdown(stocks, valueBySymbol, cashEur),
    [stocks, valueBySymbol, cashEur],
  );
  const byValue = slices.some((s) => s.value !== null);

  if (slices.length === 0) return null;

  // Geometry shared with charts/Donut (see charts/arc.ts).
  const segments = donutSegments(
    slices.map((s) => s.fraction),
    CIRC,
    GAP_DEG,
  ).map((seg, i) => ({ slice: slices[i], ...seg }));

  const totalCount = slices.reduce((sum, s) => sum + s.count, 0);
  const totalValue = slices.reduce((sum, s) => sum + (s.value ?? 0), 0);
  const hovered = active !== null ? slices.find((s) => s.sector === active) ?? null : null;
  const centerNum = byValue
    ? eur.format(hovered ? hovered.value ?? 0 : totalValue)
    : String(hovered ? hovered.count : totalCount);

  return (
    <figure className="m-0 grid w-fit max-w-full grid-cols-[auto_1fr] items-center gap-x-8 gap-y-2 card pt-5 pb-2 max-[560px]:grid-cols-1 max-[560px]:justify-items-center">
      <div>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className="block h-[220px] w-[220px]"
          role="img"
          aria-label={t.sectorDonut.ariaLabel(totalCount)}
        >
          <g transform={`rotate(-90 ${CENTER} ${CENTER})`}>
            {segments.map(({ slice, dash, gap, offset }) => {
              const isActive = active === slice.sector;
              return (
                <circle
                  key={slice.sector}
                  className="cursor-pointer transition-[stroke-width,opacity] duration-150"
                  cx={CENTER}
                  cy={CENTER}
                  r={RADIUS}
                  fill="none"
                  stroke={sectorVar(slice.colorSlot)}
                  strokeWidth={isActive ? THICKNESS + 6 : THICKNESS}
                  strokeDasharray={`${dash} ${gap}`}
                  strokeDashoffset={offset}
                  opacity={active === null || isActive ? 1 : 0.35}
                  onMouseEnter={() => onActiveChange(slice.sector)}
                  onMouseLeave={() => onActiveChange(null)}
                />
              );
            })}
          </g>
          <text
            x={CENTER}
            y={CENTER - 6}
            textAnchor="middle"
            className={`fill-primary font-semibold ${byValue ? "text-[1.3rem] tabular-nums" : "text-[2rem]"}`}
          >
            {centerNum}
          </text>
          <text
            x={CENTER}
            y={CENTER + 14}
            textAnchor="middle"
            className="fill-secondary text-[0.8rem]"
          >
            {hovered ? sectorLabel(t, hovered.sector) : t.sectorDonut.sectorsCount(slices.length)}
          </text>
        </svg>
      </div>

      <ul className="m-0 flex w-full max-w-[300px] list-none flex-col gap-1.5 p-0 max-[560px]:max-w-[260px]">
        {slices.map((slice) => (
          <li
            key={slice.sector}
            className={`flex cursor-pointer items-center gap-2.5 transition-opacity duration-150 ${
              active === null || active === slice.sector ? "" : "opacity-40"
            }`}
            onMouseEnter={() => onActiveChange(slice.sector)}
            onMouseLeave={() => onActiveChange(null)}
          >
            <span
              className="h-3 w-3 flex-none rounded-[3px]"
              style={{ background: sectorVar(slice.colorSlot) }}
              aria-hidden
            />
            <span className="text-[0.9rem] text-primary">{sectorLabel(t, slice.sector)}</span>
            <span className="ml-auto text-[0.85rem] tabular-nums text-secondary">
              {slice.value !== null ? eur.format(slice.value) : slice.count} ·{" "}
              {pct(slice.fraction)}
            </span>
          </li>
        ))}
      </ul>

      <figcaption className="col-span-full mt-2 text-xs text-muted">
        {byValue
          ? `${t.sectorDonut.weightedBy(sourceLabel)}${
              stocks.length > totalCount ? t.sectorDonut.excludedNote(stocks.length - totalCount) : ""
            }.`
          : t.sectorDonut.equalWeighted}
      </figcaption>
    </figure>
  );
}
