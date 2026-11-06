import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSession } from "@/lib/auth";
import { useStreamingText } from "@/hooks/use-streaming-text";
import { useWorkspace } from "@/hooks/use-workspace";
import type { StreamEvent } from "@/lib/workbench-api";

const backend = vi.hoisted(() => ({ api: {} as Record<string, ReturnType<typeof vi.fn>> }));
vi.mock("@/lib/workbench-api", async importOriginal => ({ ...await importOriginal<object>(), workbenchApi: () => backend.api }));
afterEach(() => { cleanup(); sessionStorage.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => <MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter>;
  const session = { account: { id: "qa" }, session: { token: "offline-test" } } as AuthSession;
  return renderHook(() => useWorkspace(session, "c"), { wrapper });
}
function service() {
  const run = { run_id: "r", conversation_id: "c", revision: 0, status: "running", payload: { text: "第一条", material_ids: [], facts: {}, constraints: [] }, pending_request_ids: [], last_sequence: 0 };
  backend.api = {
    projects: vi.fn(async () => ({ projects: [] })), conversations: vi.fn(async () => ({ conversations: [] })), resources: vi.fn(async () => ({ resources: [] })),
    detail: vi.fn(async () => ({ conversation: { id: "c" }, messages: [], pending_materials: [], linked_resources: [] })),
    snapshot: vi.fn(async () => ({ snapshot: { runs: [] } })),
    submit: vi.fn(async () => ({ run })),
    uploadMaterial: vi.fn(async () => ({ id: "file-a", filename: "材料.txt", status: "uploaded" })),
    stream: vi.fn(() => new Promise(() => {})),
  };
  return run;
}

describe("task input ownership", () => {
  it("defaults to chat and sends work permission only on a new request", async () => {
    service(); const hook=mount();
    expect(hook.result.current.taskMode).toBe('chat');
    act(()=>hook.result.current.setTaskMode('work'));
    expect(backend.api.submit).not.toHaveBeenCalled();
    act(()=>hook.result.current.setDraft('生成证据文件'));
    await act(async()=>{await hook.result.current.send();});
    expect(backend.api.submit.mock.calls[0][9]).toBe('work');
    act(()=>hook.result.current.setTaskMode('chat'));
    expect(backend.api.submit).toHaveBeenCalledTimes(1);
  });
  it("keeps typing during upload-created conversation navigation", async () => {
    service();
    backend.api.create = vi.fn(async () => ({ id: 'created-chat' }));
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    const wrapper = ({children}:PropsWithChildren) => <MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter>;
    const session = {account:{id:'qa'},session:{token:'offline-test'}} as AuthSession;
    const hook = renderHook(({id})=>useWorkspace(session,id),{wrapper,initialProps:{id:undefined as string|undefined}});
    const previousInputHandler = hook.result.current.setDraft;
    await act(async()=>{await hook.result.current.ensureConversation();});
    act(()=>previousInputHandler('上传后正在输入的问题'));
    hook.rerender({id:'created-chat'});
    expect(hook.result.current.draft).toBe('上传后正在输入的问题');
  });
  it("consumes sent attachments without narrowing scope, preserves the next draft after done", async () => {
    service(); let receive: (event: StreamEvent) => void = () => {};
    backend.api.stream = vi.fn((_c, _r, listener) => { receive = listener; return new Promise(() => {}); });
    const hook = mount();
    await act(async () => { await hook.result.current.uploadFiles([new File(["真实单元测试数据"], "材料.txt")]); });
    expect(hook.result.current.draftMaterials).toHaveLength(1);
    act(() => hook.result.current.setDraft("第一条"));
    await act(async () => { await hook.result.current.send(); });
    expect(backend.api.submit.mock.calls[0][2]).toEqual([]);
    expect(backend.api.submit.mock.calls[0][6]).toEqual(["file-a"]);
    expect(hook.result.current.draftMaterials).toHaveLength(0);
    expect(hook.result.current.turn?.materials[0].id).toBe("file-a");
    act(() => hook.result.current.setDraft("下一条还没有发送"));
    act(() => receive({ type: "done", run_id: "r", sequence: 1, text: "第一条完成" }));
    expect(hook.result.current.draft).toBe("下一条还没有发送");
  });

  it("keeps draft and attachment on rejected submission", async () => {
    service(); backend.api.submit = vi.fn(async () => { throw new Error("拒绝提交"); });
    const hook = mount();
    await act(async () => { await hook.result.current.uploadFiles([new File(["正文"], "材料.txt")]); });
    act(() => hook.result.current.setDraft("不能丢失"));
    await act(async () => { await hook.result.current.send(); });
    expect(hook.result.current.draft).toBe("不能丢失");
    expect(hook.result.current.draftMaterials).toHaveLength(1);
    expect(hook.result.current.notice).toBe("拒绝提交");
  });

  it("allows new uploads while a run is running", async () => {
    service(); const hook = mount();
    act(() => hook.result.current.setDraft("第一条"));
    await act(async () => { await hook.result.current.send(); });
    expect(hook.result.current.busy).toBe(true);
    await act(async () => { await hook.result.current.uploadFiles([new File(["补充"], "新材料.txt")]); });
    expect(hook.result.current.draftMaterials).toHaveLength(1);
    expect(backend.api.uploadMaterial).toHaveBeenCalledOnce();
  });
});

describe("stream presentation", () => {
  it("reveals real received characters progressively and handles authoritative replacement", async () => {
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 5));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => window.clearTimeout(id));
    const hook = renderHook(({ target }) => useStreamingText(target, true), { initialProps: { target: "" } });
    hook.rerender({ target: "原文😀逐字呈现" });
    expect(hook.result.current).toBe("");
    await waitFor(() => expect(hook.result.current.length).toBeGreaterThan(0));
    await waitFor(() => expect(hook.result.current).toBe("原文😀逐字呈现"));
    hook.rerender({ target: "更正后的正式内容" });
    expect(hook.result.current).toBe("更正后的正式内容");
  });
});
