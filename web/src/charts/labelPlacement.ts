// Greedy label placement for the SVG charts: each label tries its candidate
// spots in order of preference and keeps the first one that collides with
// nothing — marks, labels already placed, the chart's edges — or, when every
// spot collides, the one that collides least.

export type Anchor = "start" | "middle" | "end";

export interface LabelSpot {
  /** Text anchor point, as SVG <text> x/y (y is the baseline). */
  x: number;
  y: number;
  anchor: Anchor;
}

export interface LabelRequest {
  /** Rendered text width in px (measureText). */
  width: number;
  /** Spots in order of preference. */
  candidates: LabelSpot[];
}

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Mark {
  x: number;
  y: number;
  r: number;
}

/** 11px text: cap height above the baseline, descenders below. */
const ASCENT = 9;
const DESCENT = 3;
/** Clear space kept around every label. */
const MARGIN = 2;

export function labelBox({ x, y, anchor }: LabelSpot, width: number): Box {
  const x0 = anchor === "start" ? x : anchor === "end" ? x - width : x - width / 2;
  return { x0: x0 - MARGIN, y0: y - ASCENT - MARGIN, x1: x0 + width + MARGIN, y1: y + DESCENT + MARGIN };
}

function overlap(a: Box, b: Box): number {
  const w = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
  const h = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
  return w > 0 && h > 0 ? w * h : 0;
}

function area(b: Box): number {
  return (b.x1 - b.x0) * (b.y1 - b.y0);
}

/**
 * Places `requests` in order (earlier ones get first pick), avoiding `marks`
 * (circles, taken by their bounding square so a label never grazes one), each
 * other, and the `width`×`height` frame. Returns one spot per request.
 */
export function placeLabels(requests: LabelRequest[], marks: Mark[], width: number, height: number): LabelSpot[] {
  const frame: Box = { x0: 0, y0: 0, x1: width, y1: height };
  const obstacles: Box[] = marks.map((m) => ({ x0: m.x - m.r, y0: m.y - m.r, x1: m.x + m.r, y1: m.y + m.r }));
  return requests.map(({ width: textWidth, candidates }) => {
    let best = candidates[0];
    let bestCost = Infinity;
    for (const spot of candidates) {
      const box = labelBox(spot, textWidth);
      // Leaving the frame clips the text: worse than overlapping a mark.
      let cost = 3 * (area(box) - overlap(box, frame));
      for (const o of obstacles) cost += overlap(box, o);
      if (cost < bestCost) {
        best = spot;
        bestCost = cost;
        if (cost === 0) break;
      }
    }
    obstacles.push(labelBox(best, textWidth));
    return best;
  });
}

let context: CanvasRenderingContext2D | null | undefined;
const widths = new Map<string, number>();

/** Width of `text` in the interface font at 11px; a per-character estimate
 * where there's no canvas (tests). */
export function textWidth(text: string, weight: 400 | 600 = 400): number {
  const key = `${weight}:${text}`;
  const cached = widths.get(key);
  if (cached !== undefined) return cached;
  if (context === undefined) context = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
  let width = text.length * (weight === 600 ? 6.8 : 6.4);
  if (context) {
    context.font = `${weight} 11px "Manrope Variable", system-ui, sans-serif`;
    width = context.measureText(text).width;
  }
  widths.set(key, width);
  return width;
}
