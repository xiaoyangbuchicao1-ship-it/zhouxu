export interface Project {
  id: string;
  name: string;
  conversation_count: number;
  created_at: string;
  updated_at: string;
}
export interface Conversation {
  id: string;
  title: string;
  project_id: string | null;
  pinned: boolean;
  preview?: string;
  material_count?: number;
  created_at: string;
  updated_at: string;
}
export interface Material {
  id: string;
  filename: string;
  status: string;
  issue?: string | null;
}
export interface Resource extends Material {
  content_type?: string;
  chunk_count: number;
  conversation_count: number;
  created_at: string;
  updated_at: string;
}
export interface Passage {
  id: string;
  ordinal: number;
  locator?: string;
  heading_path?: string[] | string;
  text: string;
  enabled?: boolean;
}
export interface Activity {
  id: string;
  stage: string;
  status: string;
  label: string;
  detail?: string;
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  materials?: Material[];
  activity?: Activity[];
}
export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  pending_materials: Material[];
  linked_resources: Resource[];
}
export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "progress"; activity: Activity }
  | {
      type: "done";
      user_message: Message;
      assistant_message: Message;
      perception_run_id?: string;
    }
  | { type: "error"; message: string };

export class WorkspaceError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
    this.name = "WorkspaceError";
  }
}

const base = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
const segment = encodeURIComponent;

async function check(response: Response) {
  if (response.ok) return response;
  const data = await response.json().catch(() => null);
  const message =
    response.status === 401
      ? "登录状态已失效，请重新登录。"
      : response.status === 404
        ? "这项内容已不存在，或无法访问。"
        : response.status >= 500
          ? "服务暂时未能完成处理，请稍后重试。"
          : typeof data?.detail === "string" && !/^[a-z_]+$/.test(data.detail)
            ? data.detail
            : response.status === 413
              ? "单个文件不能超过 30 MB。"
              : "操作未完成，请核对后重试。";
  throw new WorkspaceError(message, response.status);
}

export function workspaceApi(token: string, lifetime?: AbortSignal) {
  async function request<T>(
    path: string,
    options: RequestInit = {},
    signal?: AbortSignal,
  ): Promise<T> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    const signals = [lifetime, signal].filter(Boolean);
    signals.forEach((s) => {
      if (s.aborted) abort();
      s.addEventListener("abort", abort, { once: true });
    });
    // Parsing files can be slower than an ordinary read. Never automatically retry writes.
    const timeout = setTimeout(
      abort,
      options.body instanceof File ? 180000 : 30000,
    );
    try {
      const headers = new Headers(options.headers);
      headers.set("Authorization", `Bearer ${token}`);
      if (typeof options.body === "string")
        headers.set("Content-Type", "application/json");
      const response = await check(
        await fetch(`${base}${path}`, {
          ...options,
          headers,
          signal: controller.signal,
          credentials: "omit",
          cache: "no-store",
        }),
      );
      return response.status === 204 ? undefined : await response.json();
    } catch (error) {
      if (signals.some((s) => s.aborted) || error instanceof WorkspaceError)
        throw error;
      throw new WorkspaceError(
        controller.signal.aborted
          ? "等待服务超时。操作可能仍在处理中，请先刷新确认结果。"
          : "连接未完成。请检查网络，并刷新确认操作结果后再试。",
      );
    } finally {
      clearTimeout(timeout);
      signals.forEach((s) => s.removeEventListener("abort", abort));
    }
  }
  const chat = (id: string) => `/conversations/${segment(id)}`;
  const rawFile = (file: File) => {
    if (file.size > 30 * 1024 * 1024)
      throw new WorkspaceError("单个文件不能超过 30 MB。");
    if (!file.size) throw new WorkspaceError("文件内容为空，请选择其他文件。");
    return {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    };
  };
  return {
    projects: (signal?: AbortSignal) =>
      request<{ projects: Project[] }>("/projects", {}, signal),
    conversations: (signal?: AbortSignal) =>
      request<{ conversations: Conversation[] }>("/conversations", {}, signal),
    detail: (id: string, signal?: AbortSignal) =>
      request<ConversationDetail>(chat(id), {}, signal),
    create: (project_id?: string) =>
      request<Conversation>("/conversations", {
        method: "POST",
        body: JSON.stringify({ project_id: project_id || null }),
      }),
    deleteChat: (id: string) => request<void>(chat(id), { method: "DELETE" }),
    pin: (id: string, pinned: boolean) =>
      request<void>(`${chat(id)}/pin`, { method: pinned ? "PUT" : "DELETE" }),
    createProject: (name: string) =>
      request<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    deleteProject: (id: string) =>
      request<void>(`/projects/${segment(id)}`, { method: "DELETE" }),
    move: (id: string, projectId: string, remove = false) =>
      request<void>(
        `/projects/${segment(projectId)}/conversations/${segment(id)}`,
        { method: remove ? "DELETE" : "PUT" },
      ),
    uploadMaterial: (id: string, file: File) =>
      request<Material>(
        `${chat(id)}/materials?filename=${segment(file.name)}`,
        rawFile(file),
      ),
    removeMaterial: (id: string, material: string) =>
      request<void>(`${chat(id)}/materials/${segment(material)}`, {
        method: "DELETE",
      }),
    resources: (signal?: AbortSignal) =>
      request<{ resources: Resource[] }>("/library/resources", {}, signal),
    uploadResource: (file: File) =>
      request<Resource>(
        `/library/resources?filename=${segment(file.name)}`,
        rawFile(file),
      ),
    deleteResource: (id: string) =>
      request<void>(`/library/resources/${segment(id)}`, { method: "DELETE" }),
    passages: (id: string, signal?: AbortSignal) =>
      request<{ chunks: Passage[] }>(
        `/library/resources/${segment(id)}/chunks`,
        {},
        signal,
      ),
    link: (id: string, resource: string, remove = false) =>
      request<void>(`${chat(id)}/resources/${segment(resource)}`, {
        method: remove ? "DELETE" : "PUT",
      }),
    async stream(
      id: string,
      content: string,
      onEvent: (event: StreamEvent) => void,
      signal: AbortSignal,
    ) {
      const controller = new AbortController();
      const abort = () => controller.abort();
      [signal, lifetime].filter(Boolean).forEach((s) => {
        if (s.aborted) abort();
        s.addEventListener("abort", abort, { once: true });
      });
      // An idle timeout guards a dead connection, not the duration of a genuine model run.
      let timer: ReturnType<typeof setTimeout>;
      const heartbeat = () => {
        clearTimeout(timer);
        timer = setTimeout(abort, 120000);
      };
      heartbeat();
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      try {
        const response = await check(
          await fetch(`${base}${chat(id)}/messages/stream`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ content }),
            signal: controller.signal,
            credentials: "omit",
            cache: "no-store",
          }),
        );
        if (!response.body)
          throw new WorkspaceError(
            "没有收到回答内容，请重新载入对话确认结果。",
          );
        reader = response.body.getReader();
        await readStream(reader, onEvent, heartbeat);
      } catch (error) {
        if (
          signal.aborted ||
          lifetime?.aborted ||
          error instanceof WorkspaceError
        )
          throw error;
        throw new WorkspaceError(
          "回答连接中断，当前内容可能尚未保存。请重新载入对话确认结果。",
        );
      } finally {
        clearTimeout(timer);
        await reader?.cancel().catch(() => undefined);
        reader?.releaseLock();
        [signal, lifetime]
          .filter(Boolean)
          .forEach((s) => s.removeEventListener("abort", abort));
      }
    },
  };
}

// The backend sends NDJSON (not SSE). Decode bytes incrementally, including split UTF-8.
export async function readStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: StreamEvent) => void,
  heartbeat = () => {},
) {
  const decoder = new TextDecoder();
  let buffer = "";
  let done = false;
  function consume(line: string) {
    if (!line.trim()) return;
    let event: StreamEvent;
    try {
      event = JSON.parse(line);
    } catch {
      throw new WorkspaceError("回答数据不完整，请重新载入对话确认结果。");
    }
    if (event.type === "error")
      throw new WorkspaceError(
        event.message || "本轮回答未完成，请重新载入对话后重试。",
      );
    if (event.type === "delta" && typeof event.text !== "string")
      throw new WorkspaceError("回答数据不完整。");
    if (event.type === "done") {
      if (!event.user_message?.id || !event.assistant_message?.id)
        throw new WorkspaceError("尚未确认本轮保存状态，请重新载入对话。");
      done = true;
    }
    if (["delta", "progress", "done"].includes(event.type)) onEvent(event);
  }
  while (!done) {
    const part = await reader.read();
    heartbeat();
    buffer += decoder.decode(part.value, { stream: !part.done });
    let newline: number;
    while (!done && (newline = buffer.indexOf("\n")) >= 0) {
      consume(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
    }
    if (part.done) {
      if (!done) consume(buffer);
      break;
    }
  }
  if (!done)
    throw new WorkspaceError(
      "回答连接提前结束，当前内容可能尚未保存。请重新载入对话确认结果。",
    );
}

export function materialStatus(material: Material) {
  if (material.issue) return "需要检查";
  return (
    (
      {
        indexed: "可供检索",
        read: "已读取",
        read_ocr: "已识别，请核对",
        read_with_replacement: "已读取，请核对",
        extracted: "已读取",
        parsed: "已读取",
        ready: "已读取",
        empty: "未读到正文",
        unsupported: "暂不支持",
        failed: "读取失败",
        error: "读取失败",
      } as Record<string, string>
    )[material.status] || "已接收"
  );
}
