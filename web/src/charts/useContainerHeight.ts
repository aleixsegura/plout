import { useEffect, useState } from "react";

/** Height of a box sized by its layout (e.g. a grid cell stretched to match its
 * neighbour), so a chart drawn at a fixed pixel height can fill it. Returns a
 * callback ref, so it also works when the box mounts after the first render. */
export default function useContainerHeight(fallback: number): [(el: HTMLDivElement | null) => void, number] {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(fallback);
  useEffect(() => {
    if (!el) return;
    const update = () => setHeight(Math.max(Math.floor(el.clientHeight), fallback));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el, fallback]);
  return [setEl, height];
}
