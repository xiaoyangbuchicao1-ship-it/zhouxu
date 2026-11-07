/* The HTTP adapter validates the versioned Workbench contract at the boundary. */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type RunStatus =
  | "queued" | "running" | "waiting_user" | "completed"
  | "failed" | "stopped" | "interrupted";

export interface Project { id: string; name: string; conversation_count: number; created_at: string; updated_at: string; }
export type TaskMode = "chat" | "work";
export type ModelChoice = "deepseek-v4" | "gpt";
export interface ModelOption { id: ModelChoice; label: string; available: boolean; }
export interface Conversation { id: string; title: string; project_id: string | null; task_mode: TaskMode; pinned: boolean; preview?: string; material_count?: number; created_at: string; updated_at: string; }
export interface Material { id: string; filename: string; status: string; size_bytes?: number; content_type?: string; issue?: string | null; }
export interface Resource extends Material { chunk_count?: number; conversation_count?: number; created_at: string; updated_at: string; }
export interface Passage { id: string; ordinal: number; locator?: string; heading_path?: string[] | string; text: string; enabled?: boolean; }
export interface Activity { id: string; stage: string; status: string; label: string; detail?: string; }
export interface Message { id: string; role: "user" | "assistant"; content: string; created_at: string; run_id?: string | null; status?: RunStatus | string; error_code?: string | null; materials?: Material[]; updates?: string[]; activity?: Activity[]; }
export interface ConversationDetail { conversation: Conversation; messages: Message[]; pending_materials: Material[]; linked_resources: Resource[]; }
export interface RunPayload { text: string; facts: Record<string, unknown>; constraints: string[]; material_ids: string[]; attached_material_ids?: string[]; model_id?: ModelChoice | null; }
export interface RunEnvelope { run_id: string; conversation_id: string; status: RunStatus; revision: number; payload: RunPayload; pending_request_ids: string[]; answers: Record<string, unknown>; output?: string | null; partial_output?: string | null; error?: { code?: string; message: string; retryable?: boolean } | null; retry_of_run_id?: string | null; completed_units?: number | null; total_units?: number | null; current_unit?: string | null; created_at?: string; started_at?: string | null; first_event_at?: string | null; first_delta_at?: string | null; completed_at?: string | null; last_sequence?: number; }
export interface InputRequest { request_id: string; run_id: string; question: string; options: { value: string; label: string }[]; allow_free_text: boolean; required: boolean; status: "pending" | "answered" | "cancelled"; answer?: unknown; }
export interface RunCommandResponse { run: RunEnvelope; }
export interface SessionSnapshot { conversation_id: string; status: "open" | "closed"; active_run_id?: string | null; queued_run_ids: string[]; waiting_run_ids: string[]; runs: RunEnvelope[]; }
export type StreamEvent =
  | { type: "delta"; run_id: string; sequence: number; text: string }
  | { type: "answer"; run_id: string; sequence: number; text: string }
  | { type: "progress"; run_id: string; sequence: number; activity: Activity }
  | { type: "done"; run_id: string; sequence: number; text: string; run?: RunEnvelope | null }
  | { type: "error"; run_id: string; sequence: number; message: string }
  | { type: "lifecycle"; run_id: string; sequence: number; run: RunEnvelope };

export function acceptStreamSequence(lastSequence: number, event: StreamEvent): boolean {
  return event.type === "lifecycle" || event.sequence > lastSequence;
}

export function mergeRunMessages(messages: Message[], liveRunId: string | (string | undefined)[] | undefined): Message[] {
  const ids = new Set(Array.isArray(liveRunId) ? liveRunId : [liveRunId]);
  return messages.filter((message) => !message.run_id || !ids.has(message.run_id));
}

export class WorkbenchError extends Error { constructor(message: string, public status = 0) { super(message); this.name = "WorkbenchError"; } }
const base = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
const segment = encodeURIComponent;
const REQUEST_TIMEOUT_MS = 600_000;

function messageFor(status: number, detail: unknown) {
  if (status === 401) return "登录状态已失效，请重新登录。";
  if (status === 404) return "这项内容已不存在，或无法访问。";
  if (status === 409) return "当前运行状态不允许执行这个操作。";
  if (status === 413) return "文件超过当前配置允许的大小。";
  if (typeof detail === "string" && !/^[a-z_]+$/.test(detail)) return detail;
  if (status >= 500) return "舟序服务暂时无法完成处理，请稍后重试。";
  return "操作未完成，请稍后重试。";
}

export function workbenchApi(token: string, lifetime?: AbortSignal) {
  async function request<T>(path: string, options: RequestInit = {}, signal?: AbortSignal): Promise<T> {
    const controller = new AbortController();
    const signals = [lifetime, signal].filter(Boolean) as AbortSignal[];
    const abort = () => controller.abort();
    signals.forEach((s) => { if (s.aborted) abort(); s.addEventListener("abort", abort, { once: true }); });
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const headers = new Headers(options.headers);
      headers.set("Authorization", `Bearer ${token}`);
      if (typeof options.body === "string") headers.set("Content-Type", "application/json");
      const response = await fetch(`${base}${path}`, { ...options, headers, signal: controller.signal, credentials: "omit", cache: "no-store" });
      const data = await response.json().catch(() => undefined);
      if (!response.ok) throw new WorkbenchError(messageFor(response.status, data?.detail), response.status);
      return data as T;
    } catch (error) {
      if (signals.some((s) => s.aborted) || error instanceof WorkbenchError) throw error;
      throw new WorkbenchError(controller.signal.aborted ? "等待服务超时，请刷新确认运行状态。" : "无法连接舟序服务，请稍后重试。");
    } finally { window.clearTimeout(timeout); signals.forEach((s) => s.removeEventListener("abort", abort)); }
  }
  const conversationPath = (id: string) => `/workspace/conversations/${segment(id)}`;
  const runPath = (id: string) => `/workspace/runs/${segment(id)}`;
  const material = (raw: any): Resource => ({ id: raw.material_id, filename: raw.name, status: raw.status, size_bytes: raw.size_bytes, issue: raw.issue ?? null, content_type: raw.media_type, created_at: raw.created_at || new Date().toISOString(), updated_at: raw.updated_at || raw.created_at || new Date().toISOString() });
  const conversation = (raw: any): Conversation => ({ id: raw.conversation_id, title: raw.title, project_id: raw.project_id ?? null, task_mode: raw.task_mode === "work" ? "work" : "chat", pinned: raw.pinned, created_at: raw.created_at, updated_at: raw.updated_at, material_count: raw.material_count });
  const detail = (raw: any): ConversationDetail => ({ conversation: conversation(raw.conversation), messages: (raw.messages || []).map((m: any) => ({ id: m.message_id, role: m.role, content: m.content, created_at: m.created_at, run_id: m.run_id, status: m.status, error_code: m.error_code ?? null, materials: (m.materials || []).map(material), updates: m.updates || [] })), pending_materials: [], linked_resources: (raw.materials || []).map(material) });
  return {
    conversations: async (signal?: AbortSignal) => ({ conversations: (await request<any>("/workspace/conversations", {}, signal)).conversations.map(conversation) }),
    create: async (_projectId?: string, taskMode: TaskMode = "chat") => conversation((await request<any>("/workspace/conversations", { method: "POST", body: JSON.stringify({ title: "", task_mode: taskMode }) })).conversation),
    update: async (id: string, input: { title?: string; pinned?: boolean }) => conversation((await request<any>(conversationPath(id), { method: "PATCH", body: JSON.stringify(input) })).conversation),
    deleteChat: (id: string) => request<void>(conversationPath(id), { method: "DELETE" }),
    pin: (id: string, pinned: boolean) => request<void>(conversationPath(id), { method: "PATCH", body: JSON.stringify({ pinned }) }),
    detail: async (id: string, signal?: AbortSignal) => detail(await request<any>(conversationPath(id), {}, signal)),
    models: async (signal?: AbortSignal): Promise<{ models: ModelOption[] }> => ({ models: (await request<any>("/workspace/models", {}, signal)).models as ModelOption[] }),
    projects: async (signal?: AbortSignal): Promise<{ projects: Project[] }> => ({ projects: (await request<any>("/workspace/projects", {}, signal)).projects.map((item: any) => ({ id: item.project_id, name: item.name, conversation_count: item.conversation_count, created_at: item.created_at, updated_at: item.updated_at })) }),
    createProject: async (name: string): Promise<Project> => { const item = await request<any>("/workspace/projects", { method: "POST", body: JSON.stringify({ name }) }); return { id: item.project_id, name: item.name, conversation_count: item.conversation_count, created_at: item.created_at, updated_at: item.updated_at }; },
    updateProject: async (id: string, name: string): Promise<Project> => { const item = await request<any>(`/workspace/projects/${segment(id)}`, { method: "PATCH", body: JSON.stringify({ name }) }); return { id: item.project_id, name: item.name, conversation_count: item.conversation_count, created_at: item.created_at, updated_at: item.updated_at }; },
    deleteProject: async (id: string): Promise<void> => { await request<void>(`/workspace/projects/${segment(id)}`, { method: "DELETE" }); },
    move: async (id: string, projectId: string, remove = false): Promise<void> => { await request<void>(`/workspace/conversations/${segment(id)}/project`, { method: "PUT", body: JSON.stringify({ project_id: remove ? null : projectId }) }); },
    resources: async (signal?: AbortSignal) => ({ resources: (await request<any>("/workspace/materials", {}, signal)).materials.map(material) }),
    uploadResource: async (file: File) => material((await request<any>("/workspace/materials", { method: "POST", body: (() => { const f = new FormData(); f.append("file", file, file.name); return f; })() })).material),
    deleteResource: (id: string) => request<void>(`/workspace/materials/${segment(id)}`, { method: "DELETE" }),
    passages: async (id: string, signal?: AbortSignal, cursor?: string): Promise<{ chunks: Passage[]; next_cursor?: string | null; truncated?: boolean; parse_status?: string; complete?: boolean; notices?: string[] }> => request(`/workspace/materials/${segment(id)}/passages${cursor ? `?cursor=${segment(cursor)}` : ""}`, {}, signal),
    uploadMaterial: async (id: string, file: File) => material((await request<any>(`${conversationPath(id)}/materials`, { method: "POST", body: (() => { const f = new FormData(); f.append("file", file, file.name); return f; })() })).material),
    removeMaterial: (id: string, materialId: string) => request<void>(`${conversationPath(id)}/materials/${segment(materialId)}`, { method: "DELETE" }),
    link: (id: string, resourceId: string, remove = false) => request<void>(`${conversationPath(id)}/materials/${segment(resourceId)}`, { method: remove ? "DELETE" : "PUT" }),
    submit: (id: string, text: string, materialIds: string[] = [], signal?: AbortSignal, clientRequestId: string = crypto.randomUUID(), retryOfRunId?: string, attachedIds: string[] = [], skillId?: string, caseId?: string, taskMode: "chat" | "work" = "chat", modelId: ModelChoice = "deepseek-v4") => request<RunCommandResponse>(`${conversationPath(id)}/runs`, { method: "POST", body: JSON.stringify({ conversation_id: id, client_request_id: clientRequestId, text, model_id: modelId, facts: { task_mode: taskMode, ...(skillId ? {skill_id:skillId} : {}), ...(caseId ? {case_id:caseId} : {}) }, constraints: [], material_ids: materialIds, attached_material_ids: attachedIds, retry_of_run_id: retryOfRunId }) }, signal),
    run: (runId: string, conversationId: string, signal?: AbortSignal) => request<RunCommandResponse>(`${runPath(runId)}?conversation_id=${segment(conversationId)}`, {}, signal),
    inputRequest: (runId: string, conversationId: string, requestId: string, signal?: AbortSignal) => request<{ request: InputRequest }>(`${runPath(runId)}/input-requests/${segment(requestId)}?conversation_id=${segment(conversationId)}`, {}, signal),
    snapshot: async (id: string, signal?: AbortSignal) => ({ snapshot: await request<SessionSnapshot>(`${conversationPath(id)}/snapshot`, {}, signal) }),
    stop: (runId: string, conversationId: string) => request<RunCommandResponse>(`${runPath(runId)}/stop`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) }),
    continueRun: (runId: string, conversationId: string) => request<RunCommandResponse>(`${runPath(runId)}/continue`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) }),
    interrupt: (runId: string, conversationId: string) => request<RunCommandResponse>(`${runPath(runId)}/interrupt`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) }),
    resume: (runId: string, conversationId: string, requestId: string, answer: unknown, attachedIds: string[] = []) => request<RunCommandResponse>(`${runPath(runId)}/resume`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId, request_id: requestId, answer, attached_material_ids: attachedIds }) }),
    steer: (runId: string, conversationId: string, text: string, materialIds: string[] = [], attachedIds: string[] = [], skillId?: string, caseId?: string, taskMode: "chat" | "work" = "chat", modelId: ModelChoice = "deepseek-v4") => request<RunCommandResponse>(`${runPath(runId)}/steer`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId, text, model_id: modelId, facts: { task_mode: taskMode, ...(skillId ? {skill_id:skillId} : {}), ...(caseId ? {case_id:caseId} : {}) }, constraints: [], material_ids: materialIds, attached_material_ids: attachedIds }) }),
    async stream(conversationId: string, runId: string, onEvent: (event: StreamEvent) => void, signal: AbortSignal, after = 0) {
      const response = await fetch(`${base}${conversationPath(conversationId)}/events?run_id=${segment(runId)}&after_sequence=${after}`, { headers: { Authorization: `Bearer ${token}` }, signal, credentials: "omit", cache: "no-store" });
      if (!response.ok) { const data = await response.json().catch(() => undefined); throw new WorkbenchError(messageFor(response.status, data?.detail), response.status); }
      if (!response.body) throw new WorkbenchError("没有收到运行事件。");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done }); let index;
        while ((index = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1); if (!line) continue;
          const event = JSON.parse(line); const sequence = Number(event.sequence || event.event?.sequence || 0);
          if (event.type === "lifecycle") onEvent({ type: "lifecycle", run_id: event.run_id, sequence, run: event.run });
          else if (event.type === "stream") {
            const e = event.event; const baseEvent = { run_id: e.run_id, sequence: Number(e.sequence || 0) };
            if (e.type === "delta" || e.type === "answer") onEvent({ ...baseEvent, type: e.type, text: e.text || "" });
            else if (e.type === "progress") onEvent({ ...baseEvent, type: "progress", activity: { id: `${e.run_id}:${e.stage || "progress"}`, stage: e.stage || "run", status: "running", label: e.label || "正在处理" } });
            else if (e.type === "error") onEvent({ ...baseEvent, type: "error", message: e.error?.message || "运行未完成。" });
            else if (e.type === "done") onEvent({ ...baseEvent, type: "done", text: e.text || "", run: e.run || null });
          } else if (["delta", "answer", "progress", "done", "error"].includes(event.type)) {
            const baseEvent = { run_id: event.run_id, sequence };
            if (event.type === "delta" || event.type === "answer") onEvent({ ...baseEvent, type: event.type, text: event.text || "" });
            else if (event.type === "progress") onEvent({ ...baseEvent, type: "progress", activity: { id: `${event.run_id}:${event.stage || "progress"}`, stage: event.stage || "run", status: "running", label: event.label || "正在处理" } });
            else if (event.type === "done") onEvent({ ...baseEvent, type: "done", text: event.text || "", run: event.run || null });
            else onEvent({ ...baseEvent, type: "error", message: event.error?.message || "运行未完成。" });
          }
        }
        if (done) break;
      }
    },
  };
}

export function materialStatus(material: Material) { return ({ uploaded: "已接收", parsing: "解析中", ready: "可供检索", failed: "读取失败", unsupported: "暂不支持" } as Record<string, string>)[material.status] || "状态未知"; }
