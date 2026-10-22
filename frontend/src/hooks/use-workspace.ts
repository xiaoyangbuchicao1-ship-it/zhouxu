import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { saveSession } from "@/lib/auth-session";
import type { AuthSession } from "@/lib/auth";
import {
  workspaceApi,
  WorkspaceError,
  type Activity,
} from "@/lib/workspace-api";

export interface PendingTurn {
  chatId: string;
  question: string;
  answer: string;
  activities: Activity[];
  state: "receiving" | "interrupted";
  error?: string;
}
export interface Upload {
  key: string;
  chatId: string;
  file: File;
  error?: string;
}

export function useWorkspace(session: AuthSession, id?: string) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [lifetime] = useState(() => new AbortController());
  const api = useMemo(
    () => workspaceApi(session.session.token, lifetime.signal),
    [session.session.token, lifetime],
  );
  const scope = useMemo(
    () => ["workspace", session.account.id],
    [session.account.id],
  );
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const actionLock = useRef(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [turn, setTurn] = useState<PendingTurn | null>(null);
  const streamController = useRef<AbortController>();
  const streamLock = useRef(false);
  const creation = useRef<Promise<string>>();
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [projectFilter, setProjectFilter] = useState<string | null>(null);

  const projects = useQuery({
    queryKey: [...scope, "projects"],
    queryFn: ({ signal }) => api.projects(signal),
    retry: false,
  });
  const history = useQuery({
    queryKey: [...scope, "history"],
    queryFn: ({ signal }) => api.conversations(signal),
    retry: false,
  });
  const resources = useQuery({
    queryKey: [...scope, "resources"],
    queryFn: ({ signal }) => api.resources(signal),
    retry: false,
  });
  const detail = useQuery({
    queryKey: [...scope, "chat", id],
    queryFn: ({ signal }) => api.detail(id!, signal),
    enabled: Boolean(id),
    retry: false,
  });

  const report = useCallback(
    (error: unknown) => {
      if (lifetime.signal.aborted) return;
      if (error instanceof WorkspaceError && error.status === 401) {
        navigate("/login", {
          replace: true,
          state: { notice: "登录状态已失效，请重新登录。" },
        });
        saveSession(null, "登录状态已失效，请重新登录。");
      } else
        setNotice(
          error instanceof Error ? error.message : "操作未完成，请稍后重试。",
        );
    },
    [navigate, lifetime],
  );

  useEffect(() => {
    const error = [
      projects.error,
      history.error,
      resources.error,
      detail.error,
    ].find((e) => e instanceof WorkspaceError && e.status === 401);
    if (error) report(error);
  }, [projects.error, history.error, resources.error, detail.error, report]);

  useEffect(
    () => () => {
      lifetime.abort();
      streamController.current?.abort();
      void client.cancelQueries({ queryKey: scope });
      client.removeQueries({ queryKey: scope });
    },
    [client, scope, lifetime],
  );

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: scope });
  }, [client, scope]);

  async function act(operation: () => Promise<unknown>) {
    if (actionLock.current || lifetime.signal.aborted) return false;
    actionLock.current = true;
    setBusy(true);
    try {
      await operation();
      await refresh();
      return true;
    } catch (error) {
      report(error);
      return false;
    } finally {
      actionLock.current = false;
      if (!lifetime.signal.aborted) setBusy(false);
    }
  }

  async function ensureChat() {
    if (id) return id;
    if (creation.current) return creation.current;
    creation.current = api
      .create(projectFilter || undefined)
      .then((chat) => {
        if (lifetime.signal.aborted)
          throw new DOMException("Aborted", "AbortError");
        setDrafts((current) => ({
          ...current,
          [chat.id]: current.new || "",
          new: "",
        }));
        client.setQueryData([...scope, "chat", chat.id], {
          conversation: chat,
          messages: [],
          pending_materials: [],
          linked_resources: [],
        });
        navigate(`/workspace/chat/${chat.id}`, { replace: true });
        void client.invalidateQueries({ queryKey: [...scope, "history"] });
        return chat.id;
      })
      .finally(() => {
        creation.current = undefined;
      });
    return creation.current;
  }

  const draft = drafts[id || "new"] || "";
  const setDraft = (text: string) =>
    setDrafts((current) => ({ ...current, [id || "new"]: text }));

  async function send() {
    const content = draft.trim();
    if (turn?.state === "interrupted") {
      setNotice("有一轮回答尚未确认保存。请先回到对应对话，重新载入并核对结果。");
      return;
    }
    if (
      !content ||
      content.length > 50000 ||
      streamLock.current ||
      busy ||
      preparing ||
      uploads.some((u) => !u.error)
    )
      return;
    streamLock.current = true;
    const controller = new AbortController();
    streamController.current = controller;
    let chatId: string | undefined;
    try {
      chatId = await ensureChat();
      setTurn({
        chatId,
        question: content,
        answer: "",
        activities: [],
        state: "receiving",
      });
      await api.stream(
        chatId,
        content,
        (event) => {
          if (lifetime.signal.aborted) return;
          if (event.type === "delta")
            setTurn((t) => (t ? { ...t, answer: t.answer + event.text } : t));
          if (event.type === "progress" && event.activity?.label)
            setTurn((t) =>
              t
                ? {
                    ...t,
                    activities: [
                      ...t.activities.filter((a) => a.id !== event.activity.id),
                      event.activity,
                    ],
                  }
                : t,
            );
          if (event.type === "done") {
            // Only the terminal event establishes persistence. Update both real messages together.
            client.setQueryData(
              [...scope, "chat", chatId],
              (
                current:
                  import("@/lib/workspace-api").ConversationDetail | undefined,
              ) =>
                current
                  ? {
                      ...current,
                      messages: [
                        ...current.messages.filter(
                          (m) =>
                            m.id !== event.user_message.id &&
                            m.id !== event.assistant_message.id,
                        ),
                        {
                          ...event.user_message,
                          materials: current.pending_materials,
                        },
                        event.assistant_message,
                      ],
                      pending_materials: [],
                    }
                  : current,
            );
            setDrafts((current) => ({ ...current, [chatId!]: "" }));
            setTurn(null);
          }
        },
        controller.signal,
      );
      void refresh();
    } catch (error) {
      if (!lifetime.signal.aborted && chatId) {
        setTurn((t) =>
          t
            ? {
                ...t,
                state: "interrupted",
                error: controller.signal.aborted
                  ? "已停止接收。后台可能仍在处理，请重新载入对话确认保存结果，再决定是否重发。"
                  : error instanceof Error
                    ? error.message
                    : "连接中断，请重新载入对话确认结果。",
              }
            : t,
        );
      }
      if (!controller.signal.aborted) report(error);
    } finally {
      streamLock.current = false;
    }
  }

  async function reloadTurn() {
    if (!turn || turn.state === "receiving") return;
    const result = await client
      .fetchQuery({
        queryKey: [...scope, "chat", turn.chatId],
        queryFn: ({ signal }) => api.detail(turn.chatId, signal),
        staleTime: 0,
        retry: false,
      })
      .catch((error) => {
        report(error);
        return undefined;
      });
    if (result) {
      const saved =
        result.messages.at(-2)?.content === turn.question &&
        result.messages.at(-1)?.role === "assistant";
      if (saved) setDrafts((current) => ({ ...current, [turn.chatId]: "" }));
      setTurn(null);
      setNotice(
        saved
          ? "本轮回答已保存在对话中。"
          : "尚未查到本轮完整记录，草稿已保留。若服务仍在处理，请稍后再刷新；重发可能产生重复记录。",
      );
      void refresh();
    }
  }

  async function uploadFiles(files: File[]) {
    if (streamLock.current) return;
    let chatId: string;
    const valid = files.filter((file) => {
      if (!file.size || file.size > 30 * 1024 * 1024) {
        setNotice(`${file.name}：请选择非空且不超过 30 MB 的文件。`);
        return false;
      }
      return true;
    });
    if (!valid.length) return;
    setPreparing(true);
    try {
      chatId = await ensureChat();
    } catch (error) {
      report(error);
      return;
    } finally {
      if (!lifetime.signal.aborted) setPreparing(false);
    }
    const entries = valid.map((file) => ({
      key: crypto.randomUUID(),
      chatId,
      file,
    }));
    setUploads((current) => [...current, ...entries]);
    // Bound parser concurrency, preserve the user's file order, and keep failed files retryable.
    for (const entry of entries) await uploadOne(entry);
  }

  async function uploadOne(entry: Upload) {
    if (streamLock.current || lifetime.signal.aborted) return;
    setUploads((current) =>
      current.map((u) =>
        u.key === entry.key ? { ...u, error: undefined } : u,
      ),
    );
    try {
      await api.uploadMaterial(entry.chatId, entry.file);
      await client.invalidateQueries({
        queryKey: [...scope, "chat", entry.chatId],
      });
      if (!lifetime.signal.aborted)
        setUploads((current) => current.filter((u) => u.key !== entry.key));
      void client.invalidateQueries({ queryKey: [...scope, "history"] });
    } catch (error) {
      if (!lifetime.signal.aborted)
        setUploads((current) =>
          current.map((u) =>
            u.key === entry.key
              ? {
                  ...u,
                  error:
                    error instanceof Error
                      ? error.message
                      : "上传未完成，请重试。",
                }
              : u,
          ),
        );
      report(error);
    }
  }

  return {
    api,
    scope,
    notice,
    setNotice,
    busy: busy || preparing,
    projects,
    history,
    resources,
    detail,
    act,
    refresh,
    report,
    projectFilter,
    setProjectFilter,
    draft,
    setDraft,
    turn,
    send,
    reloadTurn,
    stop: () => streamController.current?.abort(),
    ensureChat,
    uploads,
    uploadFiles,
    uploadOne,
    dismissUpload: (key: string) =>
      setUploads((current) => current.filter((u) => u.key !== key)),
  };
}
