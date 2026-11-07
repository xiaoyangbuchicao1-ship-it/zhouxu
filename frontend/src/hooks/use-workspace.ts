import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useLocation } from "react-router-dom";
import { saveSession } from "@/lib/auth-session";
import type { AuthSession } from "@/lib/auth";
import { workbenchApi, WorkbenchError, acceptStreamSequence, type Activity, type Material, type ModelChoice, type RunEnvelope, type RunStatus, type TaskMode } from "@/lib/workbench-api";

export interface PendingTurn { conversationId: string; runId?: string; question: string; answer: string; materials: Material[]; updates: string[]; activities: Activity[]; state: "receiving" | "waiting" | "completed" | "failed" | "stopped" | "interrupted"; status: RunStatus; revision: number; error?: string; inputRequest?: { requestId: string; question: string }; }
export interface Upload { key: string; conversationId: string; file: File; error?: string; }
const terminal = new Set<RunStatus>(["completed", "failed", "stopped", "interrupted"]);
const stateFor = (status: RunStatus): PendingTurn["state"] => status === "waiting_user" ? "waiting" : terminal.has(status) ? status as PendingTurn["state"] : "receiving";

export function useWorkspace(session: AuthSession, id?: string) {
  const navigate = useNavigate();
  const location = useLocation();
  const skillId = new URLSearchParams(location.search).get("skill") || undefined;
  const caseId = new URLSearchParams(location.search).get("case") || undefined;
  const client = useQueryClient();
  const [lifetime, setLifetime] = useState(() => new AbortController());
  const api = useMemo(() => workbenchApi(session.session.token, lifetime.signal), [session.session.token, lifetime]);
  const scope = useMemo(() => ["workbench", session.account.id], [session.account.id]);
  const attachmentKey = `zhouxu.attachments.v1.${session.account.id}`;
  const [notice, setNotice] = useState("");
  const initialMode = new URLSearchParams(location.search).get("mode") === "work" ? "work" : "chat";
  const [taskMode, setTaskModeState] = useState<TaskMode>(initialMode);
  const [modelChoice, setModelChoice] = useState<ModelChoice>("deepseek-v4");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<Record<string, Material[]>>(() => {
    try { return JSON.parse(sessionStorage.getItem(attachmentKey) || "{}"); } catch { return {}; }
  });
  const [cards, setCards] = useState<Record<string, PendingTurn>>({});
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [projectFilter, setProjectFilter] = useState<string | null>(null);
  const controllers = useRef<Record<string, AbortController>>({});
  const sequences = useRef<Record<string, number>>({});
  const submitLock = useRef(false);
  const creation = useRef<Promise<string>>();
  const draftTarget = useRef(id || 'new');
  useEffect(() => { draftTarget.current = id || 'new'; }, [id]);
  const projects = useQuery({ queryKey: [...scope, "projects"], queryFn: () => api.projects(), retry: false });
  const history = useQuery({ queryKey: [...scope, "history"], queryFn: ({ signal }) => api.conversations(signal), retry: false });
  const resources = useQuery({ queryKey: [...scope, "resources"], queryFn: ({ signal }) => api.resources(signal), retry: false });
  const models = useQuery({ queryKey: [...scope, "models"], queryFn: ({ signal }) => api.models(signal), retry: false });
  useEffect(() => {
    const available = models.data?.models.find((option) => option.available);
    if (available && !models.data?.models.some((option) => option.id === modelChoice && option.available)) {
      setModelChoice(available.id);
    }
  }, [modelChoice, models.data]);
  const detail = useQuery({ queryKey: [...scope, "chat", id], queryFn: ({ signal }) => api.detail(id!, signal), enabled: Boolean(id), retry: false });
  const conversationMode = detail.data?.conversation.task_mode;
  useEffect(() => {
    if (id) {
      if (conversationMode) setTaskModeState(conversationMode);
      return;
    }
    const requested = new URLSearchParams(location.search).get("mode");
    setTaskModeState(requested === "work" ? "work" : "chat");
  }, [conversationMode, id, location.search]);
  const setTaskMode = useCallback((next: TaskMode) => {
    // A persisted conversation owns its mode.  The control is rendered as a
    // locked status for it; this guard also protects callers outside the UI.
    if (id && conversationMode) return;
    setTaskModeState(next);
    const params = new URLSearchParams(location.search);
    if (next === "work") params.set("mode", "work");
    else params.delete("mode");
    const query = params.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ""}`, { replace: true });
  }, [conversationMode, id, location.pathname, location.search, navigate]);
  const currentCards = Object.values(cards).filter(c => c.conversationId === id);
  const turn = currentCards.find(c => c.status === "running") || currentCards.find(c => c.status === "queued") || currentCards.find(c => c.status === "waiting_user") || currentCards.at(-1) || null;
  const queuedRuns = currentCards.filter(c => c !== turn);
  const draft = drafts[id || "new"] || "";
  const draftMaterials = attachments[id || "new"] || [];
  const setDraft = (value: string) => {
    const key = draftTarget.current;
    setDrafts(current => ({ ...current, [key]: value }));
  };
  useEffect(() => {
    const starter=location.state?.skillStarter;
    if(typeof starter==='string') {
      setDrafts(current=>({...current,[id||'new']:starter}));
      navigate(location.pathname+location.search,{replace:true,state:null});
    }
  },[id,location.pathname,location.search,location.state,navigate]);
  useEffect(() => { try { sessionStorage.setItem(attachmentKey, JSON.stringify(attachments)); } catch { /* memory state remains usable */ } }, [attachmentKey, attachments]);
  useEffect(() => {
    if (lifetime.signal.aborted) setLifetime(new AbortController());
    return () => { lifetime.abort(); Object.values(controllers.current).forEach(c => c.abort()); client.removeQueries({ queryKey: scope }); };
  }, [client, lifetime, scope]);
  const report = useCallback((error: unknown) => {
    if (lifetime.signal.aborted) return;
    if (error instanceof WorkbenchError && error.status === 401) {
      saveSession(null, "登录状态已失效，请重新登录。"); navigate("/login", { replace: true }); return;
    }
    setNotice(error instanceof Error ? error.message : "操作未完成，请稍后重试。");
  }, [lifetime, navigate]);
  const refresh = useCallback(async () => { await client.invalidateQueries({ queryKey: scope }); }, [client, scope]);
  async function act(operation: () => Promise<unknown>) { try { await operation(); await refresh(); return true; } catch (e) { report(e); return false; } }
  async function ensureConversation() {
    if (id) return id;
    if (!creation.current) creation.current = api.create(undefined, taskMode).then(chat => {
      if (lifetime.signal.aborted) throw new DOMException("Aborted", "AbortError");
      draftTarget.current = chat.id;
      setTaskModeState(chat.task_mode || taskMode);
      setDrafts(current => { const next = { ...current, [chat.id]: current.new || current[chat.id] || "" }; delete next.new; return next; });
      navigate(`/workspace/chat/${chat.id}${location.search}`, { replace: true });
      void client.invalidateQueries({ queryKey: [...scope, "history"] }); return chat.id;
    }).finally(() => { creation.current = undefined; });
    return creation.current;
  }
  const update = useCallback((runId: string, fn: (card: PendingTurn) => PendingTurn) => setCards(current => current[runId] ? { ...current, [runId]: fn(current[runId]) } : current), []);
  const applyRun = useCallback(async (conversationId: string, run: RunEnvelope, materials?: Material[]) => {
    setCards(current => {
      const previous = current[run.run_id];
      if (previous && previous.revision > run.revision) return current;
      return { ...current, [run.run_id]: {
        conversationId, runId: run.run_id, question: run.payload.text, revision: run.revision,
        answer: (run.last_sequence || 0) >= (sequences.current[run.run_id] || 0) ? (run.output ?? run.partial_output ?? "") : (previous?.answer || ""),
        materials: [...new Map([...(previous?.materials || []), ...(materials || [])].map(m => [m.id, m])).values()], updates: Object.values({ ...((run.payload.facts.user_answers || {}) as Record<string, unknown>), ...run.answers }).filter((v): v is string => typeof v === "string"), activities: previous?.activities || [],
        status: run.status, state: stateFor(run.status), error: run.error?.message,
        inputRequest: run.status === "waiting_user" ? previous?.inputRequest : undefined,
      } };
    });
    if (run.status === "waiting_user" && run.pending_request_ids[0]) {
      const value = await api.inputRequest(run.run_id, conversationId, run.pending_request_ids[0]);
      update(run.run_id, card => card.status === "waiting_user" && card.revision === run.revision ? { ...card, inputRequest: { requestId: value.request.request_id, question: value.request.question } } : card);
    }
  }, [api, update]);
  const follow = useCallback(async (conversationId: string, runId: string, controller: AbortController) => {
    let failures = 0;
    try {
      while (!controller.signal.aborted) {
        try {
          await api.stream(conversationId, runId, event => {
            if (!acceptStreamSequence(sequences.current[runId] || 0, event)) return;
            if (event.type !== "lifecycle") sequences.current[runId] = event.sequence;
            if (event.type === "delta") update(runId, c => ({ ...c, answer: c.answer + event.text, status: "running", state: "receiving" }));
            if (event.type === "answer" || event.type === "done") update(runId, c => ({ ...c, answer: event.text }));
            if (event.type === "progress") update(runId, c => ({ ...c, activities: [...c.activities.filter(a => a.id !== event.activity.id).map(a => ({ ...a, status: "completed" })), event.activity] }));
            if (event.type === "error") update(runId, c => ({ ...c, error: event.message }));
            if (event.type === "lifecycle") void applyRun(conversationId, event.run).catch(report);
          }, controller.signal, sequences.current[runId] || 0);
        const { snapshot } = await api.snapshot(conversationId, controller.signal);
        const run = snapshot.runs.find(r => r.run_id === runId);
        if (!run) return;
        await applyRun(conversationId, run);
        if (terminal.has(run.status) || run.status === "waiting_user") {
          await client.invalidateQueries({ queryKey: [...scope, "chat", conversationId] });
          await client.invalidateQueries({ queryKey: [...scope, "history"] });
          // The presentation buffer acknowledges completion after its final characters appear.
          return;
        }
        failures = 0;
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof WorkbenchError && [401, 403, 404].includes(error.status)) { report(error); throw error; }
          failures += 1;
          update(runId, c => ({ ...c, activities: [...c.activities.filter(a => a.stage !== "connection"), { id: `${runId}:connection`, stage: "connection", status: "running", label: "正在恢复连接" }] }));
          await new Promise(resolve => window.setTimeout(resolve, Math.min(3000, failures * 350)));
          continue;
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) update(runId, c => ({ ...c, state: "interrupted", error: error instanceof Error ? error.message : "连接中断，已保留当前内容；请继续处理或重新开始。" }));
    } finally { if (controllers.current[runId] === controller) delete controllers.current[runId]; }
  }, [api, applyRun, client, scope, update, report]);
  const observe = useCallback((conversationId: string, runId: string) => {
    if (controllers.current[runId]) return;
    const controller = new AbortController(); controllers.current[runId] = controller;
    void follow(conversationId, runId, controller);
  }, [follow]);
  useEffect(() => {
    setNotice("");
    if (!id) return;
    const controller = new AbortController();
    void api.snapshot(id, controller.signal).then(async ({ snapshot }) => {
      for (const run of snapshot.runs.filter(r => ["running", "queued", "waiting_user"].includes(r.status))) {
        if (controller.signal.aborted) return;
        sequences.current[run.run_id] = run.last_sequence || 0;
        const all = detail.data?.linked_resources || [];
        await applyRun(id, run, all.filter(m => run.payload.attached_material_ids?.includes(m.id)));
        observe(id, run.run_id);
      }
    }).catch(error => { if (!controller.signal.aborted) report(error); });
    return () => { controller.abort(); Object.values(controllers.current).forEach(c => c.abort()); controllers.current = {}; };
    // Detail updates must not restart active subscriptions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, applyRun, id, observe, report]);
  function consumeDraft(conversationId: string, text: string, sent: Material[]) {
    setDrafts(current => { const next = { ...current }; for (const key of [conversationId, "new"]) if ((next[key] || "").trim() === text) next[key] = ""; return next; });
    const ids = new Set(sent.map(m => m.id));
    setAttachments(current => ({ ...current, [conversationId]: (current[conversationId] || []).filter(m => !ids.has(m.id)) }));
  }
  async function send(mode: "queue" | "steer" = "queue") {
    const question = draft.trim();
    if (!question || submitLock.current || uploads.some(u => !u.error) || question.length > 50000) return;
    submitLock.current = true; setNotice("");
    try {
      const conversationId = await ensureConversation(); const sent = [...draftMaterials];
      let run: RunEnvelope;
      const requestMode = detail.data?.conversation.task_mode || taskMode;
      if (mode === "steer" && turn?.runId && turn.status === "running") {
        run = (await api.steer(turn.runId, conversationId, question, [], sent.map(m => m.id), skillId, caseId, requestMode, modelChoice)).run;
        controllers.current[run.run_id]?.abort(); delete controllers.current[run.run_id];
      } else if (turn?.state === "waiting" && turn.runId && turn.inputRequest) {
        run = (await api.resume(turn.runId, conversationId, turn.inputRequest.requestId, question, sent.map(m => m.id))).run;
      } else {
        const key = crypto.randomUUID();
        const submit = () => api.submit(conversationId, question, [], lifetime.signal, key, undefined, sent.map(m => m.id), skillId, caseId, requestMode, modelChoice);
        try { run = (await submit()).run; }
        catch (e) { if (e instanceof WorkbenchError && e.status === 0 && !lifetime.signal.aborted) run = (await submit()).run; else throw e; }
      }
      consumeDraft(conversationId, question, sent);
      await applyRun(conversationId, run, sent);
      observe(conversationId, run.run_id);
      void client.invalidateQueries({ queryKey: [...scope, "chat", conversationId] });
      void client.invalidateQueries({ queryKey: [...scope, "history"] });
    } catch (e) { report(e); }
    finally { submitLock.current = false; }
  }
  function stageMaterial(conversationId: string, material: Material, remove = false) {
    setAttachments(current => ({ ...current, [conversationId]: remove ? (current[conversationId] || []).filter(m => m.id !== material.id) : [...(current[conversationId] || []).filter(m => m.id !== material.id), material] }));
  }
  async function uploadOne(entry: Upload) {
    try {
      const material = await api.uploadMaterial(entry.conversationId, entry.file);
      stageMaterial(entry.conversationId, { ...material, size_bytes: entry.file.size });
      setUploads(current => current.filter(u => u.key !== entry.key));
      await client.invalidateQueries({ queryKey: [...scope, "chat", entry.conversationId] });
      await client.invalidateQueries({ queryKey: [...scope, "resources"] });
    } catch (e) { setUploads(current => current.map(u => u.key === entry.key ? { ...u, error: e instanceof Error ? e.message : "上传未完成。" } : u)); report(e); }
  }
  async function uploadFiles(files: File[]) {
    if (!files.length || preparing) return;
    const valid = files.filter(f => f.size > 0 && f.size <= 30 * 1024 * 1024);
    if (!valid.length) { setNotice("请选择非空且不超过 30 MB 的文件。"); return; }
    setPreparing(true);
    try { const conversationId = await ensureConversation(); const entries = valid.map(file => ({ key: crypto.randomUUID(), conversationId, file })); setUploads(current => [...current, ...entries]); for (const entry of entries) await uploadOne(entry); }
    catch (e) { report(e); } finally { setPreparing(false); }
  }
  async function removeDraftMaterial(material: Material) {
    if (!id) return;
    if (await act(() => api.removeMaterial(id, material.id))) stageMaterial(id, material, true);
  }
  async function reloadTurn(runId = turn?.runId) { await refresh(); if (runId && id) { controllers.current[runId]?.abort(); delete controllers.current[runId]; const { run } = await api.run(runId, id); await applyRun(id, run); sequences.current[run.run_id] = run.last_sequence || 0; if (!terminal.has(run.status)) observe(id, run.run_id); } }
  async function continueRun(runId = turn?.runId) {
    if (!id || !runId) return;
    try { const { run } = await api.continueRun(runId, id); await applyRun(id, run); sequences.current[runId] = run.last_sequence || 0; observe(id, runId); } catch (e) { report(e); }
  }
  async function retryTurn(runId = turn?.runId) {
    if (!id || !runId || submitLock.current) return;
    submitLock.current = true;
    try { const source = (await api.run(runId, id)).run; if (source.status === "waiting_user") { const stopped = await api.stop(runId, id); await applyRun(id, stopped.run); } const selectedSkill = typeof source.payload.facts.skill_id === "string" ? source.payload.facts.skill_id : undefined; const requestMode = detail.data?.conversation.task_mode || (source.payload.facts.task_mode === "work" ? "work" : "chat"); const selectedModel = source.payload.model_id || modelChoice; const { run } = await api.submit(id, source.payload.text, source.payload.material_ids, lifetime.signal, crypto.randomUUID(), runId, source.payload.attached_material_ids, selectedSkill, typeof source.payload.facts.case_id === "string" ? source.payload.facts.case_id : undefined, requestMode, selectedModel); await applyRun(id, run); observe(id, run.run_id); await refresh(); }
    catch (e) { report(e); } finally { submitLock.current = false; }
  }
  async function cancel(interrupt = false) {
    if (!turn?.runId || !id) return;
    try { const { run } = await (interrupt ? api.interrupt : api.stop)(turn.runId, id); controllers.current[run.run_id]?.abort(); delete controllers.current[run.run_id]; await applyRun(id, run); await refresh(); }
    catch (e) { report(e); }
  }
  const finishPresentation = useCallback(async (runId: string) => {
    if (!id) return;
    await client.invalidateQueries({ queryKey: [...scope, "chat", id] });
    setCards(current => { if (current[runId]?.status !== "completed") return current; const next = { ...current }; delete next[runId]; return next; });
  }, [client, id, scope]);
  return { taskMode, setTaskMode, modelChoice, setModelChoice, models, visibleRuns: currentCards, finishPresentation, api, scope, notice, setNotice, busy: turn?.state === "receiving", projects, history, resources, detail, act, refresh, report, projectFilter, setProjectFilter, draft, setDraft, draftMaterials, turn, queuedRuns, send, reloadTurn, retryTurn, continueRun, stop: () => cancel(), interrupt: () => cancel(true), steer: () => send("steer"), ensureConversation, uploads, uploadFiles, uploadOne, stageMaterial, removeDraftMaterial, dismissUpload: (key: string) => setUploads(current => current.filter(u => u.key !== key)) };
}
