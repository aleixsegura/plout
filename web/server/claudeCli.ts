// Runs one analysis through the local Claude Code CLI (`claude -p`) so it is
// billed to the logged-in Claude plan rather than an API key, and turns its
// stream-json output into the SSE events the page understands.

import { spawn } from "node:child_process";
import { tmpdir } from "node:os";

export const MODEL = "claude-opus-5-5";

/** A run that has produced no result after this long is killed (hung CLI,
 * stalled network): it would otherwise hold a plan slot until the page closes. */
export const RUN_TIMEOUT_MS = 10 * 60_000;
/** After SIGTERM, how long a CLI that ignores it gets before SIGKILL. */
const KILL_GRACE_MS = 3000;

export type AnalysisEvent =
  | { type: "status"; tool: string }
  | { type: "text"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; model: string; inputTokens: number; outputTokens: number };

/** CLI tool names → the activity names the page already labels. */
const TOOL_ACTIVITY: Record<string, string> = { WebSearch: "web_search", WebFetch: "web_fetch" };

/** Per-run parser state: the model reported at start-up, and whether a result
 * line has arrived (so a later non-zero exit isn't reported twice). */
export interface CliParseState {
  model: string;
  finished: boolean;
}

/** Map one parsed stream-json line to the event to forward, if any. */
export function cliEventToAnalysisEvent(line: any, state: CliParseState): AnalysisEvent | null {
  if (!line || typeof line !== "object") return null;

  if (line.type === "system" && line.subtype === "init" && typeof line.model === "string") {
    state.model = line.model;
    return null;
  }

  if (line.type === "stream_event") {
    const e = line.event;
    if (e?.type === "content_block_start" && e.content_block?.type === "tool_use") {
      const name = String(e.content_block.name ?? "");
      return { type: "status", tool: TOOL_ACTIVITY[name] ?? name };
    }
    if (e?.type === "content_block_delta" && e.delta?.type === "text_delta" && e.delta.text) {
      return { type: "text", text: e.delta.text };
    }
    return null;
  }

  if (line.type === "result") {
    state.finished = true;
    if (line.is_error || line.subtype !== "success") {
      const detail =
        typeof line.result === "string" && line.result
          ? line.result
          : Array.isArray(line.errors) && line.errors.length
            ? line.errors.join("; ")
            : String(line.subtype ?? "unknown error");
      return { type: "error", message: `Claude Code: ${detail}` };
    }
    const u = line.usage ?? {};
    return {
      type: "done",
      model: state.model || MODEL,
      inputTokens:
        (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0),
      outputTokens: u.output_tokens ?? 0,
    };
  }

  return null;
}

/** Spawn `claude -p` for one analysis and forward its events to `emit`.
 * Resolves once the process has exited; aborting `signal` kills it. */
export function runClaudeAnalysis(
  system: string,
  userContent: string,
  signal: AbortSignal,
  emit: (event: AnalysisEvent) => void,
): Promise<void> {
  const bin = process.env.PLOUT_CLAUDE_BIN || "claude";
  const args = [
    "-p",
    "--output-format", "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--model", MODEL,
    "--effort", "medium",
    "--system-prompt", system,
    "--tools", "WebSearch,WebFetch",
    "--allowedTools", "WebSearch,WebFetch",
    "--permission-mode", "dontAsk",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--setting-sources", "",
  ];

  // Without an API key in its environment the CLI uses the logged-in Claude
  // plan; with one it would silently bill the key instead.
  const env = { ...process.env };
  delete env.ANTHROPIC_API_KEY;
  delete env.ANTHROPIC_AUTH_TOKEN;

  return new Promise((resolve) => {
    // A neutral cwd keeps the project's CLAUDE.md, skills and hooks out of the prompt.
    const child = spawn(bin, args, { cwd: tmpdir(), env, stdio: ["pipe", "pipe", "pipe"] });
    const state: CliParseState = { model: "", finished: false };
    let stdoutBuf = "";
    let stderr = "";
    let failed = false;
    let timedOut = false;

    let killTimer: NodeJS.Timeout | undefined;
    const terminate = () => {
      if (child.exitCode !== null || child.signalCode !== null) return;
      child.kill("SIGTERM");
      killTimer ??= setTimeout(() => child.kill("SIGKILL"), KILL_GRACE_MS);
    };
    const onAbort = terminate;
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });

    const runTimer = setTimeout(() => {
      if (!state.finished && !signal.aborted) {
        timedOut = true;
        terminate();
      }
    }, RUN_TIMEOUT_MS);

    const handleLine = (raw: string) => {
      if (!raw.trim()) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return;
      }
      const event = cliEventToAnalysisEvent(parsed, state);
      if (event) emit(event);
    };

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdoutBuf += chunk;
      let nl: number;
      while ((nl = stdoutBuf.indexOf("\n")) >= 0) {
        handleLine(stdoutBuf.slice(0, nl));
        stdoutBuf = stdoutBuf.slice(nl + 1);
      }
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      stderr = (stderr + chunk).slice(-2000);
    });

    child.on("error", (err: NodeJS.ErrnoException) => {
      failed = true;
      clearTimeout(runTimer);
      if (signal.aborted) return;
      emit({
        type: "error",
        message:
          err.code === "ENOENT"
            ? `Claude Code CLI (\`${bin}\`) not found on PATH: install it and run \`claude\` once to log in.`
            : `Could not start Claude Code: ${err.message}`,
      });
    });

    child.on("close", (code) => {
      signal.removeEventListener("abort", onAbort);
      clearTimeout(runTimer);
      clearTimeout(killTimer);
      handleLine(stdoutBuf);
      if (timedOut && !state.finished) {
        emit({ type: "error", message: `Claude Code timed out after ${RUN_TIMEOUT_MS / 60_000} minutes.` });
      } else if (!failed && !signal.aborted && !state.finished) {
        const tail = stderr.trim().split("\n").slice(-3).join(" ");
        emit({
          type: "error",
          message: `Claude Code exited with code ${code}${tail ? `: ${tail}` : ""}`,
        });
      }
      resolve();
    });

    // The exports go in through stdin: they can be larger than argv allows.
    child.stdin.on("error", () => {});
    child.stdin.end(userContent);
  });
}
