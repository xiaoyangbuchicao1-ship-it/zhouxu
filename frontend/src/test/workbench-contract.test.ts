import { describe, expect, it, vi } from "vitest";
import { acceptStreamSequence, mergeRunMessages, workbenchApi, type Message, type StreamEvent } from "@/lib/workbench-api";

describe("Workbench stream contract", () => {
  it("rejects duplicate or regressing sequences", () => {
    const event = { type: "delta", run_id: "r", sequence: 2, text: "x" } as StreamEvent;
    expect(acceptStreamSequence(1, event)).toBe(true);
    expect(acceptStreamSequence(2, event)).toBe(false);
    expect(acceptStreamSequence(3, event)).toBe(false);
  });
  it("allows lifecycle metadata without treating done as terminal", () => {
    const event = { type: "lifecycle", run_id: "r", sequence: 0, run: { status: "running" } } as StreamEvent;
    expect(acceptStreamSequence(99, event)).toBe(true);
  });
  it("removes only the same run from the historical projection", () => {
    const messages = [
      { id: "1", run_id: "r1", role: "user", content: "a", created_at: "" },
      { id: "2", run_id: "r2", role: "user", content: "b", created_at: "" },
    ] as Message[];
    expect(mergeRunMessages(messages, "r1").map((message) => message.run_id)).toEqual(["r2"]);
  });
  it("accepts authoritative answer replacements and stable stage identities across NDJSON chunks", async () => {
    const values = [
      { type: "stream", event: { type: "delta", run_id: "r", sequence: 1, text: "先检索" } },
      { type: "stream", event: { type: "answer", run_id: "r", sequence: 2, text: "" } },
      { type: "progress", run_id: "r", sequence: 3, stage: "reasoning", label: "正在生成回答" },
      { type: "progress", run_id: "r", sequence: 4, stage: "reasoning", label: "正在生成回答" },
      { type: "answer", run_id: "r", sequence: 5, text: "正式答案" },
    ];
    const wire = new TextEncoder().encode(values.map((v) => JSON.stringify(v) + "\n").join(""));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(wire.slice(0, 29)); controller.enqueue(wire.slice(29)); controller.close(); },
    }), { status: 200 })));
    const received: StreamEvent[] = [];
    try {
      await workbenchApi("offline-unit-test").stream("chat", "r", (event) => received.push(event), new AbortController().signal);
      const answers = received.filter((event) => event.type === "answer");
      expect(answers.map((event) => event.text)).toEqual(["", "正式答案"]);
      const stages = received.filter((event) => event.type === "progress");
      expect(new Set(stages.map((event) => event.activity.id)).size).toBe(1);
      expect(received.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5]);
    } finally { vi.unstubAllGlobals(); }
  });
  it("keeps model labels public while sending only the stable choice id", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: typeof init?.body === "string" ? init.body : undefined });
      const payload = String(input).endsWith("/workspace/models")
        ? { models: [{ id: "deepseek-v4", label: "DeepSeek v4", available: true }, { id: "gpt", label: "GPT", available: true }] }
        : { run: { run_id: "run-1", conversation_id: "chat-1", status: "queued", revision: 1, payload: { text: "q", facts: {}, constraints: [], material_ids: [], model_id: "gpt" }, pending_request_ids: [], answers: {} } };
      return new Response(JSON.stringify(payload), { status: 202, headers: { "Content-Type": "application/json" } });
    }));
    try {
      const api = workbenchApi("offline-unit-test");
      const models = await api.models();
      expect(models.models.map((item) => item.label)).toEqual(["DeepSeek v4", "GPT"]);
      await api.submit("chat-1", "q", [], undefined, "request-1", undefined, [], undefined, undefined, "chat", "gpt");
      const body = JSON.parse(calls[1].body || "{}");
      expect(body.model_id).toBe("gpt");
    } finally { vi.unstubAllGlobals(); }
  });
});
