import { describe, expect, it } from "vitest";
import { convexHull, distanceToHull } from "./MarketGraph";

describe("convexHull / distanceToHull", () => {
  const square = convexHull([
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 10, y: 10 },
    { x: 0, y: 10 },
    { x: 5, y: 5 },
  ]);

  it("drops interior points", () => {
    expect(square).toHaveLength(4);
  });

  it("is 0 inside and the gap to the nearest edge outside", () => {
    expect(distanceToHull({ x: 5, y: 5 }, square)).toBe(0);
    expect(distanceToHull({ x: 15, y: 5 }, square)).toBeCloseTo(5);
    expect(distanceToHull({ x: 13, y: 14 }, square)).toBeCloseTo(5);
  });

  it("measures to a segment or a point for small groups", () => {
    const segment = convexHull([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
    ]);
    expect(distanceToHull({ x: 5, y: 4 }, segment)).toBeCloseTo(4);
    expect(distanceToHull({ x: 3, y: 4 }, [{ x: 0, y: 0 }])).toBeCloseTo(5);
  });
});
