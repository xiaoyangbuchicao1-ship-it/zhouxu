export type Account = { id: string; email: string; display_name: string };
export type StoredSession = { account: Account; token: string };
export type WorkActivity = {
  id: string;
  stage: "perception" | "planning" | "tool" | "finalizing";
  status: "running" | "completed" | "failed";
  label: string;
  detail?: string;
  tool?: string;
};

const SESSION_KEY = "zhouxu-session";

export function saveSession(session: StoredSession) {
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function readSession(): StoredSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) as StoredSession : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  window.sessionStorage.removeItem(SESSION_KEY);
}

export async function gateway<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("authorization", `Bearer ${token}`);
  const response = await fetch(`/api/gateway/${path.replace(/^\//, "")}`, { ...options, headers });
  const value = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const code = value?.detail ?? value?.error ?? `http_${response.status}`;
    throw new Error(code);
  }
  return value as T;
}

export async function streamGateway<T>(
  path: string, content: string, token: string, onDelta: (text: string) => void,
  onProgress?: (activity: WorkActivity) => void,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/gateway/${path.replace(/^\//, "")}`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!response.ok) {
    const value = await response.json().catch(() => null);
    throw new Error(value?.detail ?? value?.error ?? `http_${response.status}`);
  }
  if (!response.body) throw new Error("stream_incomplete");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | undefined;
  const receive = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as {
      type: string;
      text?: string;
      message?: string;
      activity?: WorkActivity;
    };
    if (event.type === "delta") onDelta(event.text ?? "");
    if (event.type === "progress" && event.activity) onProgress?.(event.activity);
    if (event.type === "error") throw new Error(event.message || "stream_incomplete");
    if (event.type === "done") result = event as T;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        receive(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    buffer += decoder.decode();
    receive(buffer);
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  if (!result) throw new Error("stream_incomplete");
  return result;
}

export const errorMessages: Record<string, string> = {
  authentication_failed: "邮箱或密码不正确，请重新输入。",
  authentication_required: "登录已失效，请重新登录。",
  account_email_exists: "这个邮箱已经注册，可以直接登录。",
  email_invalid: "请输入有效的邮箱地址。",
  password_length_invalid: "密码需要 12 个字符以上。",
  service_unavailable: "暂时无法连接回答服务，请稍后重试。",
  "暂时无法连接模型服务": "暂时无法连接回答服务，请稍后重试。",
  "模型没有返回回答": "这次没有生成完整回答，请重新发送。",
  stream_incomplete: "回答中断。已生成内容保留在本页，可以重试。",
  "模型回答中断": "回答中断。已生成内容保留在本页，可以重试。",
  "对话已在另一处更新，请重新打开后再试": "对话已更新，请重新打开后再试。",
};

export function explainError(error: unknown, fallback = "操作没有完成，请稍后重试。") {
  const code = error instanceof Error ? error.message : "";
  return errorMessages[code] ?? fallback;
}
