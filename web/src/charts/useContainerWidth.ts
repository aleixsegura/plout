import { useEffect, useState } from "react";
import type React from "react";

/** The SVG is drawn 1:1 in CSS pixels (text must not scale with the box), so
 * the viewBox width follows the container. */
export default function useContainerWidth(ref: React.RefObject<HTMLDivElement>): number {
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setWidth(Math.max(Math.floor(el.clientWidth), 200));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}
