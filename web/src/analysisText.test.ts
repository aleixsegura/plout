import { describe, expect, it } from "vitest";
import { parse } from "./Markdown";
import { parseSseChunk } from "./useAnalysisStream";

describe("parseSseChunk", () => {
  it("returns complete frames and keeps the partial tail", () => {
    const r = parseSseChunk('data: {"a":1}\n\ndata: {"b"');
    expect(r.events).toEqual(['{"a":1}']);
    expect(r.rest).toBe('data: {"b"');
  });
  it("accepts CRLF and data: without a space", () => {
    expect(parseSseChunk('data:{"a":1}\r\n\r\n').events).toEqual(['{"a":1}']);
  });
});

describe("markdown parse", () => {
  it("handles fences, rules and an unclosed fence", () => {
    expect(parse("a\n---\n```\nx\n- y\n```\nb")).toEqual([
      { kind: "p", text: "a" },
      { kind: "hr" },
      { kind: "code", text: "x\n- y" },
      { kind: "p", text: "b" },
    ]);
    expect(parse("```\npartial")).toEqual([{ kind: "code", text: "partial" }]);
  });
  it("groups bullets and numbered items", () => {
    expect(parse("- a\n- b\n1. c")).toEqual([
      { kind: "ul", items: ["a", "b"] },
      { kind: "ol", items: ["c"] },
    ]);
  });
});
