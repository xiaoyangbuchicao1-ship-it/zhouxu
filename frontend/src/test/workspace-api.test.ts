import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readStream,
  workspaceApi,
  materialStatus,
  type StreamEvent,
} from "@/lib/workspace-api";

afterEach(() => vi.unstubAllGlobals());
const terminal = {
  type: "done",
  user_message: { id: "u", content: "问题" },
  assistant_message: { id: "a", content: "舟序回答" },
};

function reader(text: string, step = 1) {
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.length) controller.close();
      else {
        controller.enqueue(bytes.slice(offset, offset + step));
        offset += step;
      }
    },
  }).getReader();
}

describe("workspace wire contract", () => {
  it("handles split UTF-8, CRLF, progress, and a final line without newline", async () => {
    const events: StreamEvent[] = [];
    await readStream(
      reader(
        `${JSON.stringify({ type: "progress", activity: { id: "p", label: "整理材料" } })}\r\n${JSON.stringify({ type: "delta", text: "舟序回答😀" })}\n${JSON.stringify(terminal)}`,
      ),
      (e) => events.push(e),
    );
    expect(events.map((e) => e.type)).toEqual(["progress", "delta", "done"]);
    expect(events[1]).toEqual({ type: "delta", text: "舟序回答😀" });
  });
  it("never calls an unterminated or failed stream saved", async () => {
    await expect(
      readStream(reader('{"type":"delta","text":"未完成"}\n'), () => {}),
    ).rejects.toThrow("尚未保存");
    await expect(
      readStream(
        reader('{"type":"error","message":"模型暂时不可用"}\n'),
        () => {},
      ),
    ).rejects.toThrow("模型暂时不可用");
  });
  it("rejects malformed data and malformed completion", async () => {
    await expect(readStream(reader("not-json\n"), () => {})).rejects.toThrow(
      "不完整",
    );
    await expect(
      readStream(reader('{"type":"done"}\n'), () => {}),
    ).rejects.toThrow("保存状态");
  });
  it("stops at completion without waiting for the socket to close", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(JSON.stringify(terminal) + "\n"),
        );
      },
    });
    const streamReader = stream.getReader();
    await expect(readStream(streamReader, () => {})).resolves.toBeUndefined();
    await streamReader.cancel();
  });
  it("uses bearer authentication and body-only raw file uploads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "m", status: "read" }), {
          status: 201,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["合同内容"], "合同 sample.md", {
      type: "text/markdown",
    });
    await workspaceApi("qa-session").uploadMaterial("c/1", file);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "/api/conversations/c%2F1/materials?filename=%E5%90%88%E5%90%8C%20sample.md",
    );
    expect(options.body).toBe(file);
    expect(options.headers.get("Content-Type")).toBe("text/markdown");
    expect(options.headers.get("Authorization")).toBe("Bearer qa-session");
    expect(options.credentials).toBe("omit");
  });
  it("blocks empty and oversized files before sending", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const api = workspaceApi("qa-session");
    expect(() => api.uploadResource(new File([], "empty.txt"))).toThrow(
      "文件内容为空",
    );
    const big = new File(["x"], "large.txt");
    Object.defineProperty(big, "size", { value: 30 * 1024 * 1024 + 1 });
    expect(() => api.uploadResource(big)).toThrow("30 MB");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("supports 204 mutations and distinguishes project ungrouping", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = workspaceApi("qa-session");
    await api.deleteProject("p1");
    await api.move("c1", "p2", true);
    await api.link("c1", "r1", true);
    expect(
      fetchMock.mock.calls.map(([url, opts]) => [url, opts.method]),
    ).toEqual([
      ["/api/projects/p1", "DELETE"],
      ["/api/projects/p2/conversations/c1", "DELETE"],
      ["/api/conversations/c1/resources/r1", "DELETE"],
    ]);
  });
  it("does not retry uncertain writes and translates expired auth", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(
        new Response('{"detail":"authentication_required"}', { status: 401 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await expect(workspaceApi("qa-session").create()).rejects.toThrow(
      "刷新确认",
    );
    await expect(workspaceApi("qa-session").projects()).rejects.toMatchObject({
      status: 401,
      message: "登录状态已失效，请重新登录。",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("aborts account-scoped work when the session ends", async () => {
    const fetchMock = vi.fn((_url, options) => {
      expect(options.signal.aborted).toBe(true);
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });
    vi.stubGlobal("fetch", fetchMock);
    const lifetime = new AbortController();
    lifetime.abort();
    await expect(
      workspaceApi("qa-session", lifetime.signal).projects(),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
  it("translates real parser states without claiming failed extraction succeeded", () => {
    expect(materialStatus({ id: "a", filename: "a.md", status: "read" })).toBe(
      "已读取",
    );
    expect(
      materialStatus({
        id: "b",
        filename: "b.pdf",
        status: "read_ocr",
        issue: "请核对原件",
      }),
    ).toBe("需要检查");
  });
});
