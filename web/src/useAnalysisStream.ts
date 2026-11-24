import { useCallback, useEffect, useRef, useState } from "react";

// Client half of the dev-server analysis endpoints (web/server/analysisPlugin.ts).
// The answer lives only in this hook's state: nothing is cached or stored, so
// unmounting, closing the panel or asking again drops the previous text.

/** Analysis is served by the Vite dev server; a static build has no endpoint. */
export const analysisAvailable = import.meta.env.DEV;

type ServerEvent =
  | { type: "status"; tool: string }
  | { type: "text"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; model: string; inputTokens: number; outputTokens: number };

export type AnalysisStatus = "idle" | "streaming" | "done" | "error" | "stopped";

export interface AnalysisState {
  status: AnalysisStatus;
  text: string;
  /** Server tool currently running ("web_search", "web_fetch"), while streaming. */
  activity: string | null;
  error: string | null;
  usage: { model: string; inputTokens: number; outputTokens: number } | null;
}

/** Split an SSE buffer into the `data:` payloads of its complete frames and
 * the unfinished tail. Accepts \n or \r\n line endings. */
export function parseSseChunk(buffer: string): { events: string[]; rest: string } {
  const frames = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = frames.pop() ?? "";
  const events: string[] = [];
  for (const frame of frames) {
    const data = frame
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (data) events.push(data);
  }
  return { events, rest };
}

const IDLE: AnalysisState = { status: "idle", text: "", activity: null, error: null, usage: null };

export function useAnalysisStream(url: string) {
  const [state, setState] = useState<AnalysisState>(IDLE);
  const controller = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  /** User-requested stop: keep the partial answer and leave the "streaming" state. */
  const cancel = useCallback(() => {
    stop();
    setState((s) => (s.status === "streaming" ? { ...s, status: "stopped", activity: null } : s));
  }, [stop]);

  const start = useCallback(
    async (body: Record<string, unknown>) => {
      stop();
      const abort = new AbortController();
      controller.current = abort;
      setState({ ...IDLE, status: "streaming" });

      const apply = (event: ServerEvent) =>
        setState((s) => {
          switch (event.type) {
            case "status":
              return { ...s, activity: event.tool };
            case "text":
              return { ...s, activity: null, text: s.text + event.text };
            case "error":
              return { ...s, status: "error", activity: null, error: event.message };
            case "done":
              return { ...s, status: s.status === "error" ? "error" : "done", activity: null, usage: event };
          }
        });

      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: abort.signal,
        });
        if (!res.ok || !res.body || !res.headers.get("content-type")?.includes("text/event-stream")) {
          const detail = res.ok ? "" : (await res.text()).slice(0, 300);
          throw new Error(detail || `HTTP ${res.status}`);
        }

        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;
          for (const data of parsed.events) {
            try {
              apply(JSON.parse(data) as ServerEvent);
            } catch {
              // A garbled frame must not abort the whole answer.
            }
          }
        }
        // The server always ends with done or error; a bare close means it dropped.
        setState((s) =>
          s.status === "streaming" ? { ...s, status: "error", activity: null, error: "Connection closed early." } : s,
        );
      } catch (err) {
        if (abort.signal.aborted) return;
        setState((s) => ({
          ...s,
          status: "error",
          activity: null,
          error: err instanceof Error ? err.message : String(err),
        }));
      } finally {
        if (controller.current === abort) controller.current = null;
      }
    },
    [url, stop],
  );

  const reset = useCallback(() => {
    stop();
    setState(IDLE);
  }, [stop]);

  // Leaving the page cancels the request, so the server stops generating.
  useEffect(() => stop, [stop]);

  return { state, start, stop: cancel, reset };
}
