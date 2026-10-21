"use client";

import { ChangeEvent, DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDown, ArrowUp, BookOpen, Check, ChevronDown, CircleAlert, FileText, LoaderCircle, LogOut, Menu, Square,
  Folder, Home, MessageSquare, MoreHorizontal, Paperclip, Pin, Plus, Search, Trash2, Upload, X, ArrowLeft, ArrowUpRight,
} from "lucide-react";
import { Brand } from "@/components/brand";
import { MessageMarkdown } from "@/components/message-markdown";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import {
  clearSession, explainError, gateway, readSession, streamGateway,
  type StoredSession, type WorkActivity,
} from "@/lib/gateway";
import styles from "./conversation-workspace.module.css";

type Material = { id: string; filename: string; status: string; issue: string | null };
type LibraryResource = Material & {
  chunk_count: number;
  conversation_count?: number;
  parser_name?: string;
  updated_at: string;
};
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  materials?: Material[];
  activity?: WorkActivity[];
};
type Conversation = {
  id: string;
  title: string;
  preview?: string | null;
  material_count?: number;
  project_id?: string | null;
  pinned?: number;
  created_at: string;
  updated_at: string;
};
type Project = { id: string; name: string; conversation_count: number; created_at: string; updated_at: string };
type ConversationList = { conversations: Conversation[] };
type ProjectList = { projects: Project[] };
type ConversationDetail = {
  conversation: Conversation;
  messages: ChatMessage[];
  pending_materials: Material[];
  linked_resources: LibraryResource[];
};
type ResourceList = { resources: LibraryResource[] };
type MessageResponse = { user_message: ChatMessage; assistant_message: ChatMessage };
type Attempt = { content: string; files: File[]; pendingMaterials: Material[] };
type FailedAttempt = Attempt & { reason: string; answer?: string; activity?: WorkActivity[] };
type Stage = "idle" | "opening" | "creating" | "uploading" | "answering";

const ACTIVE_KEY = "zhouxu-active-conversation";
const acceptedExtensions = [
  "pdf", "docx", "txt", "md", "eml", "xlsx", "html", "htm", "csv", "json", "xml",
  "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp",
];
const acceptedFiles = acceptedExtensions.map((extension) => `.${extension}`).join(",");
const stageText: Record<Exclude<Stage, "idle">, string> = {
  opening: "正在打开对话",
  creating: "正在发送",
  uploading: "正在上传材料",
  answering: "正在整理本轮信息",
};

function mergeActivity(items: WorkActivity[], incoming: WorkActivity) {
  const index = items.findIndex((item) => item.id === incoming.id);
  if (index < 0) return [...items, incoming];
  return items.map((item, itemIndex) => itemIndex === index ? incoming : item);
}
const exampleQuestions = ["租船合同里的通知要求，应先核对什么？", "装卸延误产生的费用，可以怎样梳理？", "双方对货损原因有分歧，我该从哪里开始？"];

function WorkProcess({ activities, running, fallback }: {
  activities: WorkActivity[];
  running?: boolean;
  fallback?: string;
}) {
  const current = [...activities].reverse().find((item) => item.status === "running")
    ?? activities.at(-1);
  const label = current?.label ?? fallback ?? "正在处理";
  const hasFailure = activities.some((item) => item.status === "failed");
  const toolCount = activities.filter((item) => item.stage === "tool" && item.status === "completed").length;
  const summary = running
    ? label
    : hasFailure
      ? "部分步骤未完成 · 查看过程"
      : toolCount ? `已完成 ${toolCount} 次工具调用 · 查看过程` : "查看处理过程";

  return <details className={styles.workProcess}>
    <summary>
      <span className={`${styles.processState} ${running ? styles.processRunning : ""}`}>
        {running ? <LoaderCircle /> : hasFailure ? <CircleAlert /> : <Check />}
      </span>
      <span>{summary}</span>
      <ChevronDown className={styles.processChevron} />
    </summary>
    <ol className={styles.processSteps}>
      {activities.length ? activities.map((activity) => <li key={activity.id} data-status={activity.status}>
        <span className={styles.stepMarker}>{activity.status === "running" ? <LoaderCircle /> : activity.status === "failed" ? <CircleAlert /> : <Check />}</span>
        <div><strong>{activity.label}</strong>{activity.detail ? <p>{activity.detail}</p> : null}</div>
      </li>) : <li data-status="running">
        <span className={styles.stepMarker}><LoaderCircle /></span><div><strong>{label}</strong></div>
      </li>}
    </ol>
  </details>;
}

export function ConversationWorkspace() {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const composerAnimationRef = useRef<Animation | null>(null);
  const historyRequestRef = useRef(0);
  const detailBackRef = useRef<HTMLButtonElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const conversationScrollRef = useRef(0);
  const resourceTriggerRef = useRef<HTMLButtonElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const resourceInputRef = useRef<HTMLInputElement>(null);
  const conversationRef = useRef<HTMLElement>(null);
  const stickToBottomRef = useRef(true);
  const turnControllerRef = useRef<AbortController | null>(null);
  const answerBufferRef = useRef("");
  const activitiesRef = useRef<WorkActivity[]>([]);
  const busyRef = useRef(false);
  const [session, setSession] = useState<StoredSession | null>(null);
  const [ready, setReady] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pendingMaterials, setPendingMaterials] = useState<Material[]>([]);
  const [resources, setResources] = useState<LibraryResource[]>([]);
  const [linkedResourceIds, setLinkedResourceIds] = useState<Set<string>>(new Set());
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [libraryError, setLibraryError] = useState("");
  const [librarySearch, setLibrarySearch] = useState("");
  const [libraryFilter, setLibraryFilter] = useState<"all" | "linked">("all");
  const [activeResourceId, setActiveResourceId] = useState<string | null>(null);
  const [resourcePickerOpen, setResourcePickerOpen] = useState(false);
  const [resourcePickerSearch, setResourcePickerSearch] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [draftProjectId, setDraftProjectId] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState("");
  const [sendingText, setSendingText] = useState("");
  const [sendingFiles, setSendingFiles] = useState<string[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState("");
  const [liveActivities, setLiveActivities] = useState<WorkActivity[]>([]);
  const [failedAttempt, setFailedAttempt] = useState<FailedAttempt | null>(null);
  const [showLatest, setShowLatest] = useState(false);
  const [compactNavigation, setCompactNavigation] = useState(false);
  const [deleteResourceId, setDeleteResourceId] = useState<string | null>(null);
  const [chatToDelete, setChatToDelete] = useState<Conversation | null>(null);
  const [deletingChat, setDeletingChat] = useState(false);
  const [deleteChatError, setDeleteChatError] = useState("");
  const [boundariesOpen, setBoundariesOpen] = useState(false);
  const cancelDeleteRef = useRef<HTMLButtonElement>(null);
  const chatMenuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deletedChatRef = useRef(false);

  useEffect(() => () => { turnControllerRef.current?.abort(); composerAnimationRef.current?.cancel(); historyRequestRef.current += 1; }, []);

  useEffect(() => {
    const draft = window.sessionStorage.getItem("zhouxu-start-question");
    if (draft && ready) { setInput(draft); window.sessionStorage.removeItem("zhouxu-start-question"); }
  }, [ready]);

  useEffect(() => {
    const viewport = window.visualViewport;
    const update = () => shellRef.current?.style.setProperty("--viewport-height", `${viewport?.height ?? window.innerHeight}px`);
    update();
    viewport?.addEventListener("resize", update);
    return () => viewport?.removeEventListener("resize", update);
  }, [ready]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
    }
  }, [input, libraryOpen, ready]);

  useEffect(() => {
    if (!libraryOpen && conversationRef.current) conversationRef.current.scrollTop = conversationScrollRef.current;
  }, [libraryOpen]);

  useEffect(() => {
    if (!sidebarOpen || !compactNavigation) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = sidebarRef.current;
    const controls = () => Array.from(panel?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled)') ?? []).filter((element) => element.getClientRects().length);
    controls()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (document.querySelector('[data-slot="dialog-content"], [role="menu"]')) return;
      if (event.key === "Escape") { event.preventDefault(); setSidebarOpen(false); }
      if (event.key !== "Tab") return;
      const items = controls();
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); if (previous?.isConnected) previous.focus(); };
  }, [sidebarOpen, compactNavigation]);

  const openLibrary = () => {
    conversationScrollRef.current = conversationRef.current?.scrollTop ?? conversationScrollRef.current;
    setLibraryOpen(true); setLibraryError(""); setSidebarOpen(false);
  };

  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const update = () => setCompactNavigation(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const busy = stage !== "idle";
  const displayName = session?.account.display_name || "你";

  useEffect(() => {
    const stored = readSession();
    if (!stored?.token) {
      clearSession();
      router.replace("/login");
      return;
    }
    setSession(stored);
    async function restore() {
      try {
        const [list, resourceList, projectList] = await Promise.all([
          gateway<ConversationList>("conversations", {}, stored!.token),
          gateway<ResourceList>("library/resources", {}, stored!.token),
          gateway<ProjectList>("projects", {}, stored!.token),
        ]);
        setConversations(list.conversations);
        setResources(resourceList.resources);
        setProjects(projectList.projects);
        const savedId = window.sessionStorage.getItem(ACTIVE_KEY);
        if (savedId && list.conversations.some((item) => item.id === savedId)) {
          const detail = await gateway<ConversationDetail>(`conversations/${savedId}`, {}, stored!.token);
          setActiveId(savedId);
          setMessages(detail.messages);
          setPendingMaterials(detail.pending_materials);
          setLinkedResourceIds(new Set(detail.linked_resources.map((item) => item.id)));
          setDraftProjectId(detail.conversation.project_id ?? null);
        }
      } catch (reason) {
        if (reason instanceof Error && reason.message === "authentication_required") {
          clearSession();
          router.replace("/login");
          return;
        }
        setError(explainError(reason, "暂时无法打开历史对话，请稍后重试。"));
      } finally {
        setReady(true);
      }
    }
    void restore();
  }, [router]);

  useEffect(() => {
    const view = conversationRef.current;
    if (view && stickToBottomRef.current) {
      view.scrollTop = view.scrollHeight;
      setShowLatest(false);
    } else if (view) {
      setShowLatest(view.scrollHeight - view.scrollTop - view.clientHeight >= 100);
    }
  }, [messages, stage, streamingAnswer, liveActivities]);

  const onConversationScroll = () => {
    const view = conversationRef.current;
    if (view) {
      conversationScrollRef.current = view.scrollTop;
      stickToBottomRef.current = view.scrollHeight - view.scrollTop - view.clientHeight < 100;
      setShowLatest(!stickToBottomRef.current);
    }
  };

  const returnToLatest = () => {
    const view = conversationRef.current;
    stickToBottomRef.current = true;
    setShowLatest(false);
    if (view) view.scrollTop = view.scrollHeight;
  };

  const refreshNavigation = async (token: string) => {
    const [list, projectList] = await Promise.all([
      gateway<ConversationList>("conversations", {}, token),
      gateway<ProjectList>("projects", {}, token),
    ]);
    setConversations(list.conversations);
    setProjects(projectList.projects);
  };

  const newChat = (projectId: string | null = null) => {
    if (busy) return;
    stickToBottomRef.current = true;
    setActiveId(null);
    window.sessionStorage.removeItem(ACTIVE_KEY);
    setMessages([]);
    setPendingMaterials([]);
    setLinkedResourceIds(new Set());
    setDraftProjectId(projectId);
    setFiles([]);
    setInput("");
    setError("");
    setFailedAttempt(null);
    setLiveActivities([]);
    setLibraryOpen(false);
    setSidebarOpen(false);
    window.setTimeout(() => textareaRef.current?.focus(), 20);
  };

  const openConversation = async (item: Conversation) => {
    if (!session || (busy && stage !== "opening")) return;
    const request = ++historyRequestRef.current;
    setStage("opening");
    setError("");
    try {
      const detail = await gateway<ConversationDetail>(`conversations/${item.id}`, {}, session.token);
      if (request !== historyRequestRef.current) return;
      stickToBottomRef.current = true;
      setActiveId(item.id);
      window.sessionStorage.setItem(ACTIVE_KEY, item.id);
      setMessages(detail.messages);
      setPendingMaterials(detail.pending_materials);
      setLinkedResourceIds(new Set(detail.linked_resources.map((resource) => resource.id)));
      setDraftProjectId(detail.conversation.project_id ?? null);
      setInput("");
      setFiles([]);
      setFailedAttempt(null);
      setLiveActivities([]);
      setLibraryOpen(false);
      setSidebarOpen(false);
    } catch (reason) {
      if (request !== historyRequestRef.current) return;
      setError(explainError(reason, "这段对话暂时无法打开，请重试。"));
    } finally {
      if (request === historyRequestRef.current) setStage("idle");
    }
  };

  const addFiles = (incoming: File[]) => {
    const valid = incoming.filter((file) => {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      return acceptedExtensions.includes(extension) && file.size > 0 && file.size <= 30 * 1024 * 1024;
    });
    setError(valid.length === incoming.length
      ? "" : "支持 PDF、Word、文本、邮件、表格和常见图片；单份文件不超过 30 MB。");
    setFiles((current) => {
      const merged = [...current];
      for (const file of valid) {
        if (!merged.some((item) => item.name === file.name && item.size === file.size)) merged.push(file);
      }
      return merged;
    });
  };

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!busy) addFiles(Array.from(event.dataTransfer.files));
  };

  const removePendingMaterial = async (materialId: string) => {
    if (!session || !activeId || busy) return;
    try {
      await gateway(`conversations/${activeId}/materials/${materialId}`,
        { method: "DELETE" }, session.token);
      setPendingMaterials((current) => current.filter((item) => item.id !== materialId));
    } catch (reason) {
      setError(explainError(reason, "这份材料暂时无法移除，请重试。"));
    }
  };

  const togglePin = async (item: Conversation) => {
    if (!session || busy) return;
    const pinned = Boolean(item.pinned);
    try {
      await gateway(`conversations/${item.id}/pin`, { method: pinned ? "DELETE" : "PUT" }, session.token);
      setConversations((current) => current.map((conversation) =>
        conversation.id === item.id ? { ...conversation, pinned: pinned ? 0 : 1 } : conversation));
    } catch (reason) {
      setError(explainError(reason, "暂时无法更新置顶状态。"));
    }
  };

  const createProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!session || busy || !projectName.trim()) return;
    try {
      const created = await gateway<Project>("projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: projectName.trim() }),
      }, session.token);
      setProjects((current) => [created, ...current]);
      setProjectName("");
      setCreatingProject(false);
      newChat(created.id);
    } catch (reason) {
      setError(explainError(reason, "项目没有创建成功，请重试。"));
    }
  };

  const refreshResources = async () => {
    if (!session) return;
    const list = await gateway<ResourceList>("library/resources", {}, session.token);
    setResources(list.resources);
  };

  const uploadResources = async (incoming: File[]) => {
    if (!session || libraryBusy) return;
    const valid = incoming.filter((file) => {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      return acceptedExtensions.includes(extension) && file.size > 0 && file.size <= 30 * 1024 * 1024;
    });
    if (valid.length !== incoming.length) {
      setLibraryError("支持 PDF、Word、文本、邮件、表格和常见图片；单份文件不超过 30 MB。");
    }
    if (!valid.length) return;
    setLibraryBusy(true);
    if (valid.length === incoming.length) setLibraryError("");
    try {
      for (const file of valid) {
        await gateway<LibraryResource>(
          `library/resources?filename=${encodeURIComponent(file.name)}`,
          { method: "POST", headers: { "content-type": file.type || "application/octet-stream" }, body: file },
          session.token,
        );
      }
      await refreshResources();
    } catch (reason) {
      setLibraryError(explainError(reason, "资料没有上传完成，请重试。"));
      await refreshResources().catch(() => undefined);
    } finally {
      setLibraryBusy(false);
    }
  };

  const toggleResource = async (resourceId: string) => {
    if (!session || libraryBusy) return;
    const selected = linkedResourceIds.has(resourceId);
    if (!activeId) {
      setLinkedResourceIds((current) => {
        const next = new Set(current);
        if (selected) next.delete(resourceId); else next.add(resourceId);
        return next;
      });
      return;
    }
    setLibraryBusy(true);
    setLibraryError("");
    try {
      await gateway(
        `conversations/${activeId}/resources/${resourceId}`,
        { method: selected ? "DELETE" : "PUT" },
        session.token,
      );
      setLinkedResourceIds((current) => {
        const next = new Set(current);
        if (selected) next.delete(resourceId); else next.add(resourceId);
        return next;
      });
    } catch (reason) {
      setLibraryError(explainError(reason, "暂时无法更新当前对话使用的资料。"));
    } finally {
      setLibraryBusy(false);
    }
  };

  const removeResource = async (resourceId: string) => {
    if (!session || libraryBusy) return;
    setLibraryBusy(true);
    setLibraryError("");
    try {
      await gateway(`library/resources/${resourceId}`, { method: "DELETE" }, session.token);
      setResources((current) => current.filter((item) => item.id !== resourceId));
      setDeleteResourceId(null);
      setLinkedResourceIds((current) => {
        const next = new Set(current);
        next.delete(resourceId);
        return next;
      });
    } catch (reason) {
      setLibraryError(`资料尚未删除。${explainError(reason, "删除没有完成，请稍后重试。")}`);
    } finally {
      setLibraryBusy(false);
    }
  };

  const runTurn = async (attempt: Attempt) => {
    if (!session || busyRef.current) return;
    busyRef.current = true;
    const { content, files: selectedFiles } = attempt;
    stickToBottomRef.current = true;
    setError("");
    setFailedAttempt(null);
    setSendingText(content);
    setSendingFiles([...new Set([...attempt.pendingMaterials.map((item) => item.filename), ...selectedFiles.map((file) => file.name)])]);
    setStreamingAnswer("");
    answerBufferRef.current = "";
    setLiveActivities([]);
    activitiesRef.current = [];
    setFiles([]);
    setPendingMaterials([]);
    let conversationId = activeId;
    let createdConversation = false;
    try {
      if (!conversationId) {
        setStage("creating");
        const created = await gateway<Conversation>("conversations", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ project_id: draftProjectId }),
        }, session.token);
        conversationId = created.id;
        setActiveId(created.id);
        window.sessionStorage.setItem(ACTIVE_KEY, created.id);
        setConversations((current) => [created, ...current]);
        createdConversation = true;
      }

      if (createdConversation && linkedResourceIds.size) {
        for (const resourceId of linkedResourceIds) {
          await gateway(
            `conversations/${conversationId}/resources/${resourceId}`,
            { method: "PUT" }, session.token,
          );
        }
      }

      if (selectedFiles.length) {
        setStage("uploading");
        for (const file of selectedFiles) {
          await gateway<Material>(
            `conversations/${conversationId}/materials?filename=${encodeURIComponent(file.name)}`,
            { method: "POST", headers: { "content-type": file.type || "application/octet-stream" }, body: file },
            session.token,
          );
        }
      }

      setStage("answering");
      const controller = new AbortController();
      turnControllerRef.current = controller;
      const result = await streamGateway<MessageResponse>(
        `conversations/${conversationId}/messages/stream`, content, session.token,
        (delta) => { answerBufferRef.current += delta; setStreamingAnswer(answerBufferRef.current); },
        (activity) => { activitiesRef.current = mergeActivity(activitiesRef.current, activity); setLiveActivities(activitiesRef.current); },
        controller.signal,
      );
      const userMessage: ChatMessage = {
        ...result.user_message,
        materials: [...attempt.pendingMaterials, ...selectedFiles.map((file) => ({
          id: `${file.name}-${file.size}`, filename: file.name, status: "read", issue: null,
        }))],
      };
      setMessages((current) => [...current, userMessage, result.assistant_message]);
      setSendingText("");
      setStreamingAnswer("");
      setLiveActivities([]);
      const detail = await gateway<ConversationDetail>(`conversations/${conversationId}`, {}, session.token)
        .catch(() => null);
      if (detail) {
        setMessages(detail.messages);
        setPendingMaterials(detail.pending_materials);
        setLinkedResourceIds(new Set(detail.linked_resources.map((resource) => resource.id)));
      }
      await refreshNavigation(session.token).catch(() => undefined);
    } catch (reason) {
      const detail = conversationId
        ? await gateway<ConversationDetail>(`conversations/${conversationId}`, {}, session.token).catch(() => null)
        : null;
      const lastTwo = detail?.messages.slice(-2) ?? [];
      if (detail && detail.messages.length >= messages.length + 2 && lastTwo.length === 2 && lastTwo[0].role === "user" && lastTwo[0].content === content && lastTwo[1].role === "assistant") {
        setMessages(detail!.messages);
        setPendingMaterials(detail!.pending_materials);
        setLinkedResourceIds(new Set(detail!.linked_resources.map((resource) => resource.id)));
        await refreshNavigation(session.token).catch(() => undefined);
      } else {
        setPendingMaterials(detail?.pending_materials ?? attempt.pendingMaterials);
        setFiles(selectedFiles.filter((file) => !detail?.pending_materials.some((item) => item.filename === file.name)));
        setFailedAttempt({
          ...attempt,
          answer: answerBufferRef.current,
          activity: activitiesRef.current,
          reason: turnControllerRef.current?.signal.aborted
            ? "已停止接收。本页保留已生成的内容，尚未形成完整回答。"
            : explainError(reason, "回答中断。已生成内容保留在本页，可以重试。"),
        });
      }
    } finally {
      busyRef.current = false;
      turnControllerRef.current = null;
      setStage("idle");
      setSendingText("");
      setSendingFiles([]);
      setStreamingAnswer("");
      setLiveActivities([]);
    }
  };

  const send = (event?: FormEvent) => {
    event?.preventDefault();
    if (!session || busyRef.current) return;
    const content = input.trim() || (files.length || pendingMaterials.length
      ? "请结合这些材料，先告诉我能得出什么。" : "");
    if (!content) {
      setError("先写下你想说的，或添加一份材料。");
      textareaRef.current?.focus();
      return;
    }
    const attempt = { content, files: [...files], pendingMaterials: [...pendingMaterials] };
    const previous = composerRef.current?.getBoundingClientRect();
    const pointerInitiated = event && "nativeEvent" in event && (event.nativeEvent as SubmitEvent).submitter?.matches(":focus-visible") === false;
    setInput("");
    void runTurn(attempt);
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      const next = composerRef.current?.getBoundingClientRect();
      if (!messages.length && pointerInitiated && previous && next && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
        composerAnimationRef.current?.cancel();
        composerAnimationRef.current = composerRef.current?.animate([{transform:`translateY(${previous.top - next.top}px)`},{transform:"translateY(0)"}], {duration:260,easing:"cubic-bezier(.23,1,.32,1)"}) ?? null;
      }
    });
  };

  const logout = async () => {
    if (session) await gateway("auth/logout", { method: "POST" }, session.token).catch(() => undefined);
    clearSession();
    window.sessionStorage.removeItem(ACTIVE_KEY);
    router.push("/login");
  };

  const removeConversation = async () => {
    if (!session || !chatToDelete || busy || deletingChat) return;
    setDeletingChat(true);
    setDeleteChatError("");
    try {
      try {
        await gateway(`conversations/${chatToDelete.id}`, { method: "DELETE" }, session.token);
      } catch (reason) {
        // Another tab may have deleted this chat already; converge on the same state.
        if (!(reason instanceof Error) || reason.message !== "没有找到这段对话") throw reason;
      }
      setConversations((current) => current.filter((item) => item.id !== chatToDelete.id));
      setProjects((current) => current.map((item) => item.id === chatToDelete.project_id
        ? { ...item, conversation_count: Math.max(0, item.conversation_count - 1) } : item));
      if (chatToDelete.id === activeId) newChat();
      deletedChatRef.current = true;
      setChatToDelete(null);
      // Refresh shared resource counts without turning a successful deletion into an error.
      void gateway<ResourceList>("library/resources", {}, session.token)
        .then((list) => setResources(list.resources)).catch(() => undefined);
    } catch (reason) {
      setDeleteChatError(explainError(reason, "这条聊天暂时无法删除，请重试。"));
    } finally {
      setDeletingChat(false);
    }
  };

  const conversationRow = (item: Conversation) => (
    <div className={`${styles.conversationRow} ${item.id === activeId ? styles.activeCase : ""}`} key={item.id}>
      <button type="button" disabled={busy && stage !== "opening"} aria-current={item.id === activeId ? "page" : undefined} onClick={() => void openConversation(item)} title={item.title}>
        <MessageSquare /><span>{item.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className={styles.chatMenuButton} type="button" disabled={busy || deletingChat}
            aria-label={`更多操作：${item.title}`} title="更多操作"
            onPointerDown={(event) => { deletedChatRef.current = false; chatMenuTriggerRef.current = event.currentTarget; }}
            onKeyDown={(event) => { deletedChatRef.current = false; chatMenuTriggerRef.current = event.currentTarget; }}>
            <MoreHorizontal />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className={styles.chatMenu} align="end"
          onCloseAutoFocus={(event) => { if (deletedChatRef.current || chatToDelete) event.preventDefault(); }}>
          <DropdownMenuItem onSelect={() => void togglePin(item)}><Pin />{item.pinned ? "取消置顶" : "置顶聊天"}</DropdownMenuItem>
          <DropdownMenuItem className={styles.deleteChatItem} onSelect={() => {
            deletedChatRef.current = false;
            setDeleteChatError("");
            setChatToDelete(item);
          }}><Trash2 />删除聊天</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );

  if (!ready) return <div className={styles.boot}><LoaderCircle />正在打开舟序</div>;

  const currentConversation = conversations.find((item) => item.id === activeId);
  const currentProject = projects.find((item) => item.id === (currentConversation?.project_id ?? draftProjectId));
  const pinnedConversations = conversations.filter((item) => item.pinned);
  const looseConversations = conversations.filter((item) => !item.project_id && !item.pinned);
  const normalizedSearch = librarySearch.trim().toLocaleLowerCase("zh-CN");
  const visibleResources = resources.filter((resource) => {
    if (libraryFilter === "linked" && !linkedResourceIds.has(resource.id)) return false;
    return !normalizedSearch || resource.filename.toLocaleLowerCase("zh-CN").includes(normalizedSearch);
  });
  const activeResource = visibleResources.find((resource) => resource.id === activeResourceId)
    ?? (compactNavigation ? null : visibleResources[0])
    ?? null;
  const isEmptyConversation = messages.length === 0 && !sendingText && !failedAttempt;
  const resourceToDelete = resources.find((resource) => resource.id === deleteResourceId);
  const normalizedPickerSearch = resourcePickerSearch.trim().toLocaleLowerCase("zh-CN");
  const pickerResources = resources.filter((resource) =>
    !normalizedPickerSearch || resource.filename.toLocaleLowerCase("zh-CN").includes(normalizedPickerSearch));

  return (
    <div ref={shellRef} className={`${styles.shell} ${sidebarOpen ? styles.directoryOpen : ""} ${libraryOpen ? styles.libraryMode : ""}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className={styles.globalBar} inert={compactNavigation && sidebarOpen}>
        <Brand compact />
        <nav aria-label="主要功能"><button type="button" aria-label="对话工作台" aria-current={!libraryOpen ? "page" : undefined} onClick={() => { setLibraryOpen(false); setSidebarOpen(false); }}><MessageSquare /><span>对话工作台</span></button><button type="button" aria-label="资源库" aria-current={libraryOpen ? "page" : undefined} onClick={openLibrary}><BookOpen /><span>资源库</span></button></nav>
        <div className={styles.globalActions}><button type="button" aria-label="打开或关闭会话与项目" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(value => !value)}><Menu /><span>会话与项目</span></button><button type="button" disabled={busy} onClick={() => newChat()} aria-label="新对话"><Plus /><span>新对话</span></button><button type="button" onClick={() => void logout()} aria-label={`退出登录：${displayName}`}><LogOut /></button></div>
      </header>
      <Dialog open={boundariesOpen} onOpenChange={setBoundariesOpen}><DialogContent className={styles.boundariesDialog}><DialogTitle>分析与判断的边界</DialogTitle><DialogDescription>舟序辅助你分析，由你作出判断。</DialogDescription><dl><div><dt>合同约定</dt><dd>以合同原文及适用条款为依据。</dd></div><div><dt>材料记录</dt><dd>文件记载的内容，不自动等于已确认的事实。</dd></div><div><dt>双方主张</dt><dd>各方陈述须与材料及反方意见分别核对。</dd></div><div><dt>模型分析</dt><dd>根据现有信息形成的分析，可能不完整或有误。</dd></div><div><dt>人工确认</dt><dd>通知效力、责任认定与最终金额等关键判断，由你结合原件及适用规则确认。</dd></div></dl></DialogContent></Dialog>
      <Dialog open={Boolean(chatToDelete)} onOpenChange={(open) => {
        if (!open && !deletingChat) { setChatToDelete(null); setDeleteChatError(""); }
      }}>
        <DialogContent className={styles.deleteDialog} showCloseButton={!deletingChat}
          onOpenAutoFocus={(event) => { event.preventDefault(); cancelDeleteRef.current?.focus(); }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (deletedChatRef.current) {
              if (compactNavigation && sidebarOpen) document.querySelector<HTMLButtonElement>(`button.${styles.newChat}`)?.focus();
              else textareaRef.current?.focus();
            } else chatMenuTriggerRef.current?.focus();
          }}>
          <DialogTitle>删除这条聊天？</DialogTitle>
          <DialogDescription>“{chatToDelete?.title}”的聊天记录和直接上传的附件将被删除，无法恢复。资源库资料会保留。</DialogDescription>
          {deleteChatError ? <p className={styles.inlineError} role="alert">{deleteChatError}</p> : null}
          <div className={styles.deleteDialogActions}>
            <button ref={cancelDeleteRef} type="button" disabled={deletingChat} onClick={() => setChatToDelete(null)}>取消</button>
            <button type="button" disabled={deletingChat} onClick={() => void removeConversation()}>{deletingChat ? "正在删除…" : "删除聊天"}</button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={resourcePickerOpen} onOpenChange={(open) => { setResourcePickerOpen(open); if (!open) setResourcePickerSearch(""); }}>
        <DialogContent className={styles.resourcePicker} showCloseButton>
          <header className={styles.pickerHeader}>
            <div><DialogTitle>选择参考资料</DialogTitle></div>
            <DialogDescription>可以多选。已选择仅表示可供本轮参考，不表示已经引用；是否使用，以实际回答为准。</DialogDescription>
          </header>
          {resources.length ? <label className={styles.pickerSearch}><Search /><span className="sr-only">搜索资源库</span><input value={resourcePickerSearch} onChange={(event) => setResourcePickerSearch(event.target.value)} placeholder="搜索文件名" /></label> : null}
          {libraryError ? <p className={styles.pickerError} role="alert"><CircleAlert />{libraryError}</p> : null}
          <div className={styles.pickerList} role="group" aria-label="选择参考资料">
            {pickerResources.length ? pickerResources.map((resource) => {
              const selected = linkedResourceIds.has(resource.id);
              return <button key={resource.id} type="button" disabled={busy || libraryBusy || resource.status !== "indexed"} aria-label={`${selected ? "取消选择" : "选择"}：${resource.filename}`} aria-pressed={selected} onClick={() => void toggleResource(resource.id)}>
                <span className={styles.pickerCheck}>{selected ? <Check /> : null}</span>
                <span><b title={resource.filename}>{resource.filename}</b><small>{resource.status === "indexed" ? selected ? "已选择 · 可供本轮参考" : "可供参考" : resource.issue || "文字尚未读取"}</small></span>
              </button>;
            }) : <div className={styles.pickerEmpty}><BookOpen /><b>{resources.length ? "没有找到对应资料" : "资源库还是空的"}</b><span>{resources.length ? "换一个文件名试试。" : "先上传资料，再把需要的内容加入本轮对话。"}</span></div>}
          </div>
          <footer className={styles.pickerFooter}>
            <button type="button" className={styles.manageLibrary} onClick={() => { setResourcePickerOpen(false); openLibrary(); }}>{resources.length ? "管理资源库" : "去上传资料"}</button>
            <span>{linkedResourceIds.size ? `已选择 ${linkedResourceIds.size} 份资料` : "尚未选择资料"}</span>
            <button type="button" className={styles.finishPicker} onClick={() => setResourcePickerOpen(false)}>返回对话</button>
          </footer>
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(resourceToDelete)} onOpenChange={(open) => { if (!open && !libraryBusy) setDeleteResourceId(null); }}>
        <DialogContent className={styles.deleteDialog} showCloseButton={!libraryBusy}>
          <DialogTitle>删除这份资料？</DialogTitle>
          <DialogDescription>{resourceToDelete?.filename} 将从资源库移除，并解除与对话的关联。此操作无法撤销。</DialogDescription>
          {libraryError ? <p className={styles.inlineError} role="alert">{libraryError}</p> : null}
          <div className={styles.deleteDialogActions}>
            <button type="button" disabled={libraryBusy} onClick={() => setDeleteResourceId(null)}>取消</button>
            <button type="button" disabled={libraryBusy} onClick={() => resourceToDelete && void removeResource(resourceToDelete.id)}>{libraryBusy ? "正在删除" : "删除资料"}</button>
          </div>
        </DialogContent>
      </Dialog>
      {sidebarOpen ? <button className={styles.scrim} type="button" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} /> : null}
      <aside ref={sidebarRef} className={`${styles.sidebar} ${sidebarOpen ? styles.sidebarOpen : ""}`} inert={!sidebarOpen} role={compactNavigation && sidebarOpen ? "dialog" : undefined} aria-modal={compactNavigation && sidebarOpen ? true : undefined} aria-label="工作导航">
        <div className={styles.sideHead}><strong>会话与项目</strong><button type="button" aria-label="关闭导航" onClick={() => setSidebarOpen(false)}><X /></button></div>
        {libraryOpen ? <>
          <div className={styles.sideContext}>资源库</div>
          <nav className={styles.libraryNavigation} aria-label="资源库筛选">
            {resources.length ? <>
            <p>资料范围</p>
            <button className={libraryFilter === "all" ? styles.navFilterActive : ""} type="button" onClick={() => setLibraryFilter("all")}><BookOpen /><span>全部资料</span><small>{resources.length}</small></button>
            <button className={libraryFilter === "linked" ? styles.navFilterActive : ""} type="button" onClick={() => setLibraryFilter("linked")}><Check /><span>用于当前对话</span><small>{linkedResourceIds.size}</small></button>
            </> : null}
          </nav>
        </> : <>
          <button className={styles.newChat} type="button" disabled={busy} onClick={() => newChat()}><Plus />新对话</button>
          <nav aria-label="工作导航">
            {pinnedConversations.length ? <section className={styles.navSection}>
              <p>置顶</p>
              <div className={styles.caseList}>{pinnedConversations.slice(0, 3).map(conversationRow)}</div>
            </section> : null}
            <section className={`${styles.navSection} ${!projects.length && !creatingProject ? styles.emptyProjects : ""}`}>
              {projects.length || creatingProject ? <div className={styles.sectionHeading}><p>项目</p><button type="button" aria-label="新建项目" title="新建项目" onClick={() => setCreatingProject(true)}><Plus /></button></div> : <button className={styles.emptyProjectButton} type="button" onClick={() => setCreatingProject(true)}><Folder />新建项目</button>}
              {creatingProject ? <form className={styles.projectForm} onSubmit={(event) => void createProject(event)}>
                <input autoFocus value={projectName} maxLength={80} placeholder="项目名称" aria-label="项目名称" onChange={(event) => setProjectName(event.target.value)} />
                <button type="submit" disabled={!projectName.trim()}>创建</button>
                <button type="button" aria-label="取消" onClick={() => { setCreatingProject(false); setProjectName(""); }}><X /></button>
              </form> : null}
              <div className={styles.projectList}>
                {projects.map((project) => <div className={styles.projectGroup} key={project.id}>
                  <div className={styles.projectTitle}><Folder /><span>{project.name}</span><button type="button" aria-label={`在 ${project.name} 中新建对话`} title="在项目中新建对话" onClick={() => newChat(project.id)}><Plus /></button></div>
                  {conversations.filter((item) => item.project_id === project.id).map(conversationRow)}
                </div>)}
              </div>
            </section>
            {looseConversations.length ? <section className={styles.navSection}>
              <p>聊天</p>
              <div className={styles.caseList}>{looseConversations.map(conversationRow)}</div>
            </section> : null}
          </nav>
        </>}
        <div className={styles.account}>
          <strong>{displayName}</strong>
          <button type="button" onClick={() => void logout()} aria-label="退出登录"><LogOut /></button>
        </div>
      </aside>

      {libraryOpen ? (
        <main id="main-content" className={styles.libraryWorkspace} tabIndex={-1} inert={compactNavigation && sidebarOpen}>
          <header className={styles.libraryHeader}>
            <div><button className={styles.mobileMenu} type="button" onClick={() => setSidebarOpen(true)} aria-label="打开导航"><Menu /></button><h1>资源库</h1><p>存放可在不同对话中反复使用的资料。</p></div>
            <button className={styles.backToWork} type="button" aria-label="返回工作台" onClick={() => setLibraryOpen(false)}><Home />返回工作台</button>
          </header>
          <input ref={resourceInputRef} className="sr-only" type="file" accept={acceptedFiles} multiple aria-label="选择要上传到资源库的资料"
            onChange={(event) => { void uploadResources(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
          {resources.length ? <div className={styles.libraryToolbar}>
            <label><Search /><span className="sr-only">搜索资料</span><input value={librarySearch} onChange={(event) => setLibrarySearch(event.target.value)} placeholder="搜索文件名" /></label>
            <button type="button" aria-label="上传资料" disabled={libraryBusy} onClick={() => resourceInputRef.current?.click()}>{libraryBusy ? <LoaderCircle /> : <Upload />}上传资料</button>
          </div> : null}
          {libraryError ? <p className={styles.libraryError} role="alert"><CircleAlert />{libraryError}</p> : null}
          {!resources.length ? <section className={styles.libraryFirstUse}>
            <BookOpen /><h2>把常用资料放在这里</h2>
            <p>上传书籍、规则或参考文件，聊天时可以选择多份一起使用。</p>
            <button type="button" disabled={libraryBusy} onClick={() => resourceInputRef.current?.click()}>{libraryBusy ? <LoaderCircle /> : <Upload />}{libraryBusy ? "正在上传" : "上传资料"}</button>
          </section> : <div className={`${styles.libraryGrid} ${!activeResource ? styles.libraryGridSingle : ""}`} data-mobile-detail={Boolean(activeResource)}>
            <section className={styles.resourceTable} aria-label="资料列表">
              <header><span>资料</span><span>状态</span><span>最近更新</span><span className="sr-only">操作</span></header>
              <div className={styles.resourceRows}>
                {visibleResources.length ? visibleResources.map((resource) => {
                  const linked = linkedResourceIds.has(resource.id);
                  const active = activeResource?.id === resource.id;
                  return <article className={`${active ? styles.resourceActive : ""} ${linked ? styles.resourceLinked : ""}`} key={resource.id}>
                    <button className={styles.resourceIdentity} type="button" aria-pressed={active} onClick={(event) => { resourceTriggerRef.current = event.currentTarget; setActiveResourceId(resource.id); if (compactNavigation) window.requestAnimationFrame(() => detailBackRef.current?.focus()); }}>
                      <span className={styles.fileIcon}><FileText /></span>
                      <span><b title={resource.filename}>{resource.filename}</b><small>{resource.status === "indexed" ? linked ? "已选入当前对话" : "可供参考" : resource.issue || "等待读取文字"}</small></span>
                    </button>
                    <span className={resource.status === "indexed" ? styles.statusReady : styles.statusIssue}>{resource.status === "indexed" ? <><Check />可使用</> : <><CircleAlert />需处理</>}</span>
                    <time dateTime={resource.updated_at}>{formatDate(resource.updated_at)}</time>
                    <button className={styles.quickLink} type="button" disabled={busy || libraryBusy || resource.status !== "indexed"} aria-label={`${linked ? "取消选择" : "用于对话"}：${resource.filename}`} aria-pressed={linked} onClick={() => void toggleResource(resource.id)}>{linked ? <><Check />已选择</> : "用于对话"}</button>
                  </article>;
                }) : <div className={styles.libraryEmpty}><BookOpen /><b>{normalizedSearch ? "没有找到对应资料" : "当前对话还未选择资料"}</b><span>{normalizedSearch ? "换一个文件名，或清空搜索后查看。" : "查看全部资料，再选择需要参考的文件。"}</span><button type="button" onClick={() => { setLibrarySearch(""); setLibraryFilter("all"); }}>{normalizedSearch ? "清空搜索并查看全部" : "查看全部资料"}</button></div>}
              </div>
            </section>
            {activeResource ? <aside className={styles.resourceDetail} aria-label="资料详情">
              <>
                <button ref={detailBackRef} className={styles.detailBack} type="button" onClick={() => { setActiveResourceId(null); window.requestAnimationFrame(() => resourceTriggerRef.current?.focus()); }}><ArrowLeft />返回资料列表</button>
                <div className={styles.detailTitle}><span><FileText /></span><div><h2>{activeResource.filename}</h2></div></div>
                <dl>
                  <div><dt>处理状态</dt><dd>{activeResource.status === "indexed" ? "文字已读取，可供对话参考" : activeResource.issue || "文字尚未读取"}</dd></div>
                  <div><dt>对话使用</dt><dd>{activeResource.conversation_count ?? 0} 次</dd></div>
                  <div><dt>最近更新</dt><dd>{formatDate(activeResource.updated_at)}</dd></div>
                </dl>
                <div className={styles.detailActions}>
                  <button className={styles.detailPrimary} type="button" disabled={busy || libraryBusy || activeResource.status !== "indexed"} onClick={() => void toggleResource(activeResource.id)}>{linkedResourceIds.has(activeResource.id) ? <><Check />取消选入当前对话</> : "用于当前对话"}</button>
                  <button className={styles.detailDelete} type="button" disabled={libraryBusy} onClick={() => { setLibraryError(""); setDeleteResourceId(activeResource.id); }}><Trash2 />删除资料</button>
                </div>
                <p className={styles.detailNote}>这里显示资料的处理状态与元信息，不是原文预览。选入对话仅表示可供参考，是否使用以实际回答为准。</p>
              </>
            </aside> : null}
          </div>}
        </main>
      ) : null}

      <div className={`${styles.workspace} ${libraryOpen ? styles.workspaceHidden : ""} ${isEmptyConversation ? styles.homeState : ""}`} inert={compactNavigation && sidebarOpen}>
        <header className={styles.topbar}>
          <div>{currentProject ? <span>{currentProject.name}</span> : null}<strong>{currentConversation?.title ?? "新对话"}</strong></div>
          <button className={styles.activeResources} type="button" aria-label="选择参考资料" onClick={() => { setLibraryError(""); setResourcePickerOpen(true); }}><BookOpen />{linkedResourceIds.size ? `已选 ${linkedResourceIds.size} 份资料` : "选择资料"}</button>
        </header>

        <main id={libraryOpen ? "conversation-content" : "main-content"} className={styles.conversation} tabIndex={-1} ref={conversationRef} onScroll={onConversationScroll}>
          {isEmptyConversation ? (
            <section className={styles.welcome}>
              <div className={styles.welcomeHorizon} aria-hidden="true" />
              <h1>让分歧，回到依据。</h1>
              <p>可以先提问，材料分次补充。</p>
            </section>
          ) : (
            <div className={styles.thread}>
              <h1 className="sr-only">当前对话</h1>
              {messages.map((message) => message.role === "user" ? (
                <article className={styles.userMessage} key={message.id} aria-label="你的消息">
                  <div className={styles.userContent}><p>{message.content}</p><SentFiles materials={message.materials ?? []} /></div>
                </article>
              ) : (
                <article className={styles.assistantMessage} key={message.id} aria-label="舟序的回答">
                  {message.activity?.length ? <WorkProcess activities={message.activity} /> : null}
                  <div className={styles.answer}><MessageMarkdown content={message.content} /></div>
                </article>
              ))}
              {sendingText ? <>
                <article className={styles.userMessage} aria-label="正在发送的消息">
                  <div className={styles.userContent}><p>{sendingText}</p><SentFiles materials={sendingFiles.map((filename) => ({ id: filename, filename, status: "pending", issue: null }))} /></div>
                </article>
                <article className={styles.assistantMessage} aria-live="polite" aria-label="舟序正在处理">
                  <WorkProcess activities={liveActivities} running fallback={stageText[stage as Exclude<Stage, "idle">] ?? "正在处理"} />
                  {streamingAnswer ? <div className={styles.answer} aria-live="off"><MessageMarkdown content={streamingAnswer} /></div> : null}
                </article>
              </> : null}
              {failedAttempt && !sendingText ? <article className={styles.failedAttempt} aria-label="未完成的消息">
                <p>{failedAttempt.content}</p>
                <div><span>{failedAttempt.reason}</span><button type="button" disabled={busy} onClick={() => void runTurn(failedAttempt)}>重试</button></div>
                {failedAttempt.activity?.length ? <WorkProcess activities={failedAttempt.activity} /> : null}
                {failedAttempt.answer ? <div className={styles.answer}><MessageMarkdown content={failedAttempt.answer} /></div> : null}
              </article> : null}
            </div>
          )}
        </main>

        <form ref={composerRef} className={styles.composerWrap} onSubmit={(event) => void send(event)} aria-label="发送消息">
          {showLatest && !isEmptyConversation ? <button className={styles.returnLatest} type="button" onClick={returnToLatest}><ArrowDown />查看最新</button> : null}
          <div className={`${styles.composer} ${error ? styles.composerError : ""}`} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
            {pendingMaterials.length || files.length ? <div className={styles.fileTray}>
              {pendingMaterials.map((item) => <span className={styles.fileChip} key={item.id} title={item.issue ?? undefined}><FileText /><b>{item.filename}</b>{item.status === "unread" ? <small className={styles.unreadNote}>文字未读取</small> : null}<button type="button" disabled={busy} aria-label={`移除 ${item.filename}`} onClick={() => void removePendingMaterial(item.id)}><X /></button></span>)}
              {files.map((file) => <span className={styles.fileChip} key={`${file.name}-${file.size}`}><FileText /><b>{file.name}</b><button type="button" disabled={busy} aria-label={`移除 ${file.name}`} onClick={() => setFiles((current) => current.filter((item) => item !== file))}><X /></button></span>)}
            </div> : null}
            <textarea
              ref={textareaRef} rows={1} value={input}
              onChange={(event) => { setInput(event.target.value); setError(""); }}
              placeholder="提出问题，也可以添加材料。" aria-label="消息"
              onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !busy && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); send(); } }}
            />
            <div className={styles.composerActions}>
              <input ref={fileInputRef} className="sr-only" type="file" accept={acceptedFiles} multiple aria-label="选择材料" onChange={onFileChange} />
              <button className={styles.attachButton} type="button" disabled={busy} onClick={() => fileInputRef.current?.click()}><Paperclip />添加材料</button>
              <button className={styles.resourcePickerButton} type="button" disabled={busy} onClick={() => { setLibraryError(""); setResourcePickerOpen(true); }}><BookOpen />选择资料{linkedResourceIds.size ? <span>{linkedResourceIds.size}</span> : null}</button>
              {stage === "answering" ? <button className={styles.sendButton} type="button" aria-label="停止接收回答" title="停止接收本轮回答" onClick={() => turnControllerRef.current?.abort()}><Square size={15} fill="currentColor" /></button> : <button className={styles.sendButton} type="submit" disabled={busy || (!input.trim() && !files.length && !pendingMaterials.length)} aria-label="发送消息" title="发送（Enter）；换行（Shift+Enter）"><ArrowUp /></button>}
            </div>
          </div>
          {error ? <p className={styles.inlineError} role="alert"><CircleAlert />{error}</p> : null}
          {linkedResourceIds.size ? <p className={styles.selectionNote}>已选择 {linkedResourceIds.size} 份资料，可供本轮参考；不代表已引用。</p> : null}
          {isEmptyConversation ? <div className={styles.exampleQuestions}><p>也可以从一个示例问题开始</p>{exampleQuestions.map((question) => <button type="button" key={question} onClick={() => { setInput(question); textareaRef.current?.focus(); }}>{question}<ArrowUpRight /></button>)}</div> : null}
          <p className={styles.composerNote}>舟序辅助分析，关键判断由你确认。<button type="button" onClick={() => setBoundariesOpen(true)}>了解边界</button></p>
        </form>
      </div>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "暂无日期";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
}

function SentFiles({ materials }: { materials: Material[] }) {
  return materials.length ? <div className={styles.sentFiles}>{materials.map((material, index) => <span key={`${material.id}-${index}`} title={material.issue ?? undefined}><FileText />{material.filename}{material.status === "unread" ? <small className={styles.unreadNote}>文字未读取</small> : null}</span>)}</div> : null;
}
