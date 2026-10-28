import { describe, expect, it } from "vitest";
import { labelBox, placeLabels, type LabelSpot } from "./labelPlacement";

const above: LabelSpot = { x: 100, y: 80, anchor: "middle" };
const below: LabelSpot = { x: 100, y: 130, anchor: "middle" };

describe("placeLabels", () => {
  it("keeps the first spot when it is free", () => {
    expect(placeLabels([{ width: 30, candidates: [above, below] }], [], 300, 300)).toEqual([above]);
  });

  it("moves off a mark sitting on the preferred spot", () => {
    const placed = placeLabels([{ width: 30, candidates: [above, below] }], [{ x: 100, y: 76, r: 10 }], 300, 300);
    expect(placed).toEqual([below]);
  });

  it("doesn't stack two labels on the same spot", () => {
    const placed = placeLabels(
      [
        { width: 30, candidates: [above, below] },
        { width: 30, candidates: [above, below] },
      ],
      [],
      300,
      300,
    );
    expect(placed).toEqual([above, below]);
  });

  it("stays inside the frame", () => {
    const outside: LabelSpot = { x: 5, y: 5, anchor: "end" };
    const placed = placeLabels([{ width: 40, candidates: [outside, below] }], [], 300, 300);
    expect(placed).toEqual([below]);
  });

  it("anchors the box like SVG text", () => {
    expect(labelBox({ x: 50, y: 20, anchor: "start" }, 10).x0).toBeLessThan(50);
    expect(labelBox({ x: 50, y: 20, anchor: "end" }, 10).x1).toBeGreaterThan(50);
    expect(labelBox({ x: 50, y: 20, anchor: "end" }, 10).x0).toBeLessThan(40);
  });
});
