import { describe, expect, it } from "vitest";
import { cliEventToAnalysisEvent, type CliParseState } from "./claudeCli";

const fresh = (): CliParseState => ({ model: "", finished: false });

describe("cliEventToAnalysisEvent", () => {
  it("remembers the model from the init line and reports it on done", () => {
    const state = fresh();
    expect(cliEventToAnalysisEvent({ type: "system", subtype: "init", model: "claude-opus-5-5" }, state)).toBeNull();
    const done = cliEventToAnalysisEvent(
      {
        type: "result",
        subtype: "success",
        is_error: false,
        usage: { input_tokens: 4, cache_creation_input_tokens: 3000, cache_read_input_tokens: 2000, output_tokens: 250 },
      },
      state,
    );
    expect(done).toEqual({ type: "done", model: "claude-opus-5-5", inputTokens: 5004, outputTokens: 250 });
    expect(state.finished).toBe(true);
  });

  it("maps CLI tool starts to the activity names the page knows", () => {
    const start = (name: string) => ({
      type: "stream_event",
      event: { type: "content_block_start", content_block: { type: "tool_use", name, input: {} } },
    });
    expect(cliEventToAnalysisEvent(start("WebSearch"), fresh())).toEqual({ type: "status", tool: "web_search" });
    expect(cliEventToAnalysisEvent(start("WebFetch"), fresh())).toEqual({ type: "status", tool: "web_fetch" });
  });

  it("forwards text deltas and drops thinking, signatures and tool input", () => {
    const delta = (d: object) => ({ type: "stream_event", event: { type: "content_block_delta", delta: d } });
    expect(cliEventToAnalysisEvent(delta({ type: "text_delta", text: "Hola" }), fresh())).toEqual({
      type: "text",
      text: "Hola",
    });
    expect(cliEventToAnalysisEvent(delta({ type: "thinking_delta", thinking: "…" }), fresh())).toBeNull();
    expect(cliEventToAnalysisEvent(delta({ type: "signature_delta", signature: "x" }), fresh())).toBeNull();
    expect(cliEventToAnalysisEvent(delta({ type: "input_json_delta", partial_json: "{" }), fresh())).toBeNull();
    expect(cliEventToAnalysisEvent({ type: "assistant", message: { content: [] } }, fresh())).toBeNull();
    expect(cliEventToAnalysisEvent({ type: "rate_limit_event" }, fresh())).toBeNull();
  });

  it("turns an error result into an error event", () => {
    const state = fresh();
    expect(
      cliEventToAnalysisEvent({ type: "result", subtype: "success", is_error: true, result: "Not logged in" }, state),
    ).toEqual({ type: "error", message: "Claude Code: Not logged in" });
    expect(state.finished).toBe(true);
    expect(cliEventToAnalysisEvent({ type: "result", subtype: "error_max_turns", is_error: true }, fresh())).toEqual({
      type: "error",
      message: "Claude Code: error_max_turns",
    });
  });
});
