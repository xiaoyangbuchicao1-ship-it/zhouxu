import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { downloadDraft, maritimeApi, type CorpusSource, type MaritimeCase } from "@/lib/maritime-api";
import { useAuthSession } from "@/lib/auth-session";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  Paperclip,
  Library,
  FileText,
  Square,
  Copy,
  Check,
  ChevronDown,
  ArrowUpRight,
  Scale,
  ScanText,
  BookOpen,
  Bot,
  MoreHorizontal,
  RefreshCw,
  Download,
} from "lucide-react";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { Message } from "@/lib/workbench-api";
import { mergeRunMessages } from "@/lib/workbench-api";
import { IconButton, Menu, MenuItem, Loading, LoadError, Modal } from "./Primitives";

import { RunProgress } from "./RunProgress";
import { WorkPackages } from './WorkPackages';
import { useStreamingText } from "@/hooks/use-streaming-text";

type Workspace = ReturnType<typeof useWorkspace>;

const tasks = [
  {
    label: "法律研究",
    prompt:
      "我想研究一个海事法律问题：\n\n请结合适用法、法律依据与适用条件展开，并说明仍需核实的信息。",
  },
  {
    label: "类案检索",
    prompt:
      "我想检索与以下争议相关的案例：\n\n请区分已核实来源与待核实线索，并比较关键事实与裁判理由。",
  },
  {
    label: "合同审阅",
    prompt:
      "请审阅我将提供的合同材料，关注权利义务、责任边界与争议解决条款。请区分原文约定、风险分析与修改建议。",
  },
  {
    label: "风险研判",
    prompt:
      "请根据以下事实和材料分析争议风险：\n\n请说明判断依据、关键假设、相反观点与信息缺口。",
  },
  {
    label: "证据梳理",
    prompt:
      "请梳理我将提供的材料，按时间线、待证事实与对应证据整理，并标出矛盾与缺口。",
  },
  {
    label: "文书起草",
    prompt:
      "请协助起草一份海事争议相关文书：\n文书类型：\n用途与对象：\n事实与诉求：\n\n请将尚未确认的信息保留为待补充项。",
  },
  {
    label: "案件管理",
    prompt:
      "请根据以下案件信息梳理事项：\n\n请整理事实脉络、程序进展与待处理问题，不把待确认的日期或安排当作既定事实。",
  },
  {
    label: "索赔测算",
    prompt:
      "请帮我做一份基础索赔测算：\n\n项目、数量、单价或每天费用、币种：\n\n请按币种列出小计，并标出还缺的凭证或条件，不判断责任。",
  },
];

function SourceCitation({href,children}:{href:string;children:ReactNode}) {
  const session=useAuthSession();const [open,setOpen]=useState(false);const [source,setSource]=useState<CorpusSource|null>(null);const [error,setError]=useState("");
  async function show(){setOpen(true);setError("");setSource(null);try{if(!session)throw new Error("请登录后查看来源。");const url=new URL(href,window.location.origin);const match=url.pathname.match(/^\/(?:api\/)?maritime\/sources\/(laws|cases)\/(.+)$/);if(!match)throw new Error("引用链接不完整。");setSource(await maritimeApi(session.session.token).source(match[1],decodeURIComponent(match[2]),url.searchParams.get("snapshot")||undefined));}catch(e){setError(e instanceof Error?e.message:"来源未能载入。");}}
  return <><button className="ws-text-button md-citation" onClick={()=>void show()}>{children}<ArrowUpRight size={13}/></button><Modal open={open} onOpenChange={setOpen} title={source?.title||"核对引用来源"} description={source?"舟序资料库中的完整原文":"从舟序资料库读取原文。"}>{error?<p role="alert">{error}</p>:source?<p className="md-source-text">{source.text}</p>:<Loading label="正在载入原文…"/>}</Modal></>;
}

export function Markdown({ content }: { content: string }) {
  return (
    <div className="ws-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => href?.startsWith("/api/maritime/sources/") || href?.startsWith("/maritime/sources/") ? <SourceCitation href={href}>{children}</SourceCitation> : (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
              <ArrowUpRight size={13} aria-hidden="true" />
            </a>
          ),
          table: ({ children }) => (
            <div
              className="ws-table-scroll"
              tabIndex={0}
              role="region"
              aria-label="回答中的表格"
            >
              <table>{children}</table>
            </div>
          ),
          img: ({ alt }) => (
            <span className="ws-muted">
              [图片：{alt || "未加载的外部图片"}]
            </span>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function StreamingAnswer({ text, live, onSettled }: { text: string; live: boolean; onSettled?: () => void }) {
  const visible = useStreamingText(text, live);
  useEffect(() => { if (!live && visible === text) onSettled?.(); }, [live, visible, text, onSettled]);
  return <div data-testid="stream-answer"><Markdown content={visible} /></div>;
}

function SaveAnswer({message,onNotice}:{message:Message;onNotice:(text:string)=>void}) {
  const session=useAuthSession();const location=useLocation();
  const [open,setOpen]=useState(false);const [cases,setCases]=useState<MaritimeCase[]>([]);
  const [caseId,setCaseId]=useState("");const [title,setTitle]=useState("海事研究草稿");const [busy,setBusy]=useState(false);const [error,setError]=useState("");
  async function show(){if(!session)return;setOpen(true);setBusy(true);setError("");try{const value=await maritimeApi(session.session.token).cases();setCases(value.cases);const preferred=new URLSearchParams(location.search).get("case");setCaseId(value.cases.find(c=>c.id===preferred)?.id||value.cases[0]?.id||"");}catch(e){setError(e instanceof Error?e.message:"案件未能载入");}finally{setBusy(false);}}
  async function save(){if(!session||!caseId)return;setBusy(true);setError("");try{await maritimeApi(session.session.token).saveEntry(caseId,{kind:"document",title,content:message.content+(message.run_id?`\n\n---\n来源运行：${message.run_id}`:""),status:"draft"});setOpen(false);onNotice("已保存为案件文书草稿，待专业复核。");}catch(e){setError(e instanceof Error?e.message:"保存未完成");}finally{setBusy(false);}}
  return <><button className="ws-text-button" onClick={()=>void show()}><FileText size={15}/>存入案件</button><Modal open={open} onOpenChange={v=>{if(!busy)setOpen(v);}} title="存入案件" description="只保存为草稿，不自动确认专业复核或发送。">{error&&<p role="alert">{error}</p>}{busy?<Loading label="处理中…"/>:!cases.length?<p>请先在<Link to="/workspace/cases">案件协作</Link>建立案件，再保存这份草稿。</p>:<form className="md-form" onSubmit={e=>{e.preventDefault();void save();}}><label>目标案件<select aria-label="目标案件" value={caseId} onChange={e=>setCaseId(e.target.value)}>{cases.map(c=><option key={c.id} value={c.id}>{c.title}</option>)}</select></label><label>文书标题<input required maxLength={160} value={title} onChange={e=>setTitle(e.target.value)}/></label><button className="ws-primary-button" disabled={busy}>保存草稿</button></form>}</Modal></>;
}

function Answer({
  message,
  onNotice,
  onContinue,
  onRetry,
}: {
  message: Message;
  onNotice: (message: string) => void;
  onContinue?: () => void;
  onRetry?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const sourceLinks = [...new Set(message.content.match(/\/api\/maritime\/sources\/[^\s<>)\]`"，。；]+/g) || [])];
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      onNotice("未能复制，请选择需要的文字后手动复制。");
    }
  }
  return (
    <article className="ws-answer">
      <div className="ws-answer-byline">
        <span className="ws-answer-mark" aria-hidden="true">
          Z.
        </span>
        <span>舟序</span>
      </div>
      <RunProgress activities={message.activity} />
      <Markdown content={message.content} />
      <WorkPackages runId={message.run_id} renderText={text=><Markdown content={text}/>}/>
      {sourceLinks.length>0&&<div className="md-source-links" aria-label="回答引用原文">{sourceLinks.map((href,i)=><SourceCitation key={href} href={href}>核对原文 {i+1}</SourceCitation>)}</div>}
      <div className="ws-answer-tools">
        {["stopped", "interrupted", "failed"].includes(message.status || "") && <><button className="ws-text-button" onClick={onContinue}>继续处理</button><button className="ws-text-button" onClick={onRetry}>重新做</button></>}
        <button className="ws-text-button" onClick={() => void copy()}>
          {copied ? <Check size={15} /> : <Copy size={15} />}
          {copied ? "已复制" : "复制回答"}
        </button>
        <button className="ws-text-button" onClick={()=>downloadDraft("舟序研究草稿",message.content)}><Download size={15}/>导出草稿</button>
        <SaveAnswer message={message} onNotice={onNotice}/>
      </div>
    </article>
  );
}

export default function Conversation({
  ws,
  id,
  openMaterials,
}: {
  ws: Workspace;
  id?: string;
  openMaterials: () => void;
}) {
  const reduced = useReducedMotion();
  const navigate = useNavigate();
  const location = useLocation();
  const selectedSkillId = new URLSearchParams(location.search).get("skill");
  const builtins = ["law-research","case-research","contract-review","case-assessment","evidence-review","document-drafting","case-management","claim-calculation"];
  const selectedTask = selectedSkillId ? tasks[builtins.indexOf(selectedSkillId)]?.label || "自定义技能" : undefined;
  const modelOptions = ws.models.data?.models?.length
    ? ws.models.data.models
    : [
        { id: "deepseek-v4" as const, label: "DeepSeek v4", available: true },
        { id: "gpt" as const, label: "GPT", available: false },
      ];
  const reader = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const atEnd = useRef(true);
  const previousId = useRef(id);
  const [showLatest, setShowLatest] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const detail = ws.detail.data;
  const turn = ws.turn?.conversationId === id ? ws.turn : null;
  const receiving = ws.turn?.state === "receiving";
  const empty = !detail?.messages.length && !turn;
  const uploading = ws.uploads.some(
    (upload) => upload.conversationId === id && !upload.error,
  );
  const selectedModel =
    modelOptions.find((option) => option.id === ws.modelChoice) || modelOptions[0];
  const scrollLatest = useCallback(
    (smooth = false) => {
      const el = reader.current;
      if (el)
        el.scrollTo({
          top: el.scrollHeight,
          behavior: smooth && !reduced ? "smooth" : "instant",
        });
      atEnd.current = true;
      setShowLatest(false);
    },
    [reduced],
  );
  useEffect(() => {
    if (previousId.current !== id) {
      atEnd.current = true;
      previousId.current = id;
    }
    if (atEnd.current) {
      const frame = requestAnimationFrame(() => {
        if (atEnd.current) scrollLatest();
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [
    id,
    detail?.messages.length,
    turn?.answer,
    turn?.activities.length,
    scrollLatest,
  ]); // Never pull a reader away from older messages.
  useEffect(() => {
    const el = input.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
    }
  }, [ws.draft, id]);
  useEffect(() => {
    if (!reader.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (atEnd.current) scrollLatest();
    });
    const content = reader.current.firstElementChild;
    if (content) observer.observe(content);
    return () => observer.disconnect();
  }, [empty, id, ws.detail.isPending, scrollLatest]);
  const chooseTask = (index: number) => {
    ws.setDraft(tasks[index].prompt);
    const params=new URLSearchParams(location.search);params.set("skill",builtins[index]);
    navigate(`${location.pathname}?${params}`, {replace:true});
    requestAnimationFrame(() => input.current?.focus());
  };

  if (id && ws.detail.isPending && !turn)
    return (
      <div className="ws-center-state">
        <Loading label="正在打开对话…" />
      </div>
    );
  if (id && ws.detail.isError)
    return (
      <div className="ws-center-state">
        <LoadError
          label={ws.detail.error.message}
          retry={() => void ws.detail.refetch()}
        />
      </div>
    );

  return (
    <div
      className={`ws-conversation ${empty ? "is-empty" : ""} ${dragOver ? "is-dragging" : ""}`}
      onDragEnter={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          dragDepth.current++;
          setDragOver(true);
        }
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        dragDepth.current--;
        if (dragDepth.current <= 0) setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragOver(false);
        void ws.uploadFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <AnimatePresence>
        {dragOver && (
          <motion.div
            className="ws-drop-target"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <Paperclip size={28} />
            <span>
              松开，添加到当前对话
            </span>
          </motion.div>
        )}
      </AnimatePresence>
      <div
        className="ws-reader"
        ref={reader}
        tabIndex={0}
        aria-label="对话内容"
        onScroll={() => {
          const el = reader.current;
          if (el) {
            atEnd.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 90;
            setShowLatest(!atEnd.current);
          }
        }}
      >
        <div className="ws-message-column">
          <AnimatePresence mode="popLayout">
          {empty ? (
            <motion.div
              key="welcome"
              className="ws-welcome"
              initial={{
                opacity: 0,
                transform: reduced ? "none" : "translateY(8px)",
              }}
              animate={{ opacity: 1, transform: "none" }}
              exit={{ opacity: 0, filter: reduced ? "none" : "blur(3px)", transition: { duration: 0.12 } }}
              transition={{ duration: 0.28 }}
            >
              <h1>今天，先理清哪件事？</h1>
              <p><span>描述问题，或添加</span><wbr /><span>合同、提单与往来材料。</span></p>
            </motion.div>
          ) : (
            <motion.div key="thread" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .18 }}>
              {mergeRunMessages(detail?.messages || [], [turn?.runId, ...ws.queuedRuns.map(r => r.runId)]).map((message) =>
                message.role === "user" ? (
                  <article className="ws-question" key={message.id}>
                    <div>{message.content}</div>
                    {message.updates?.map((text, index) => <div className="ws-user-update" key={index}><small>补充要求</small><p>{text}</p></div>)}
                  </article>
                ) : (
                  <Answer
                    key={message.id}
                    message={message}
                    onNotice={ws.setNotice}
                    onContinue={() => void ws.continueRun(message.run_id || undefined)}
                    onRetry={() => void ws.retryTurn(message.run_id || undefined)}
                  />
                ),
              )}
              {ws.visibleRuns.map((card) => (
                <Fragment key={card.runId}>
                  <article className="ws-question">
                    <div>{card.question}</div>
                    {card.updates?.map((text, index) => <div className="ws-user-update" key={index}><small>补充要求</small><p>{text}</p></div>)}
                  </article>
                  <article
                    className="ws-answer"
                    aria-busy={card.state === "receiving"}
                  >
                    <div className="ws-answer-byline">
                      <span className="ws-answer-mark" aria-hidden="true">
                        Z.
                      </span>
                      <span>舟序</span>
                      <span className="ws-stream-state">
                        {card.status === "queued" ? "排队中" : card.status === "running" ? "正在回答" : card.status === "waiting_user" ? "等待补充" : card.status === "completed" ? "已完成" : card.status === "failed" ? "运行失败" : card.status === "stopped" ? "已停止" : card.status === "interrupted" ? "已打断" : "未确认保存"}
                      </span>
                    </div>
                    <RunProgress
                      activities={card.activities}
                      live={card.state === "receiving"}
                    />
                    <StreamingAnswer key={card.runId} text={card.answer} live={card.state === "receiving"} onSettled={card.status === "completed" ? () => void ws.finishPresentation(card.runId!) : undefined} />
                    {card.state === "waiting" && card.inputRequest && (
                      <div className="ws-interruption" role="status">
                        <p>{card.inputRequest.question}</p>
                        <p className="ws-muted">请在下方输入补充信息后发送。</p>
                        <button className="ws-text-button" onClick={() => void ws.retryTurn(card.runId)}>重新处理本任务</button>
                      </div>
                    )}
                    {(card.state === "interrupted" || card.state === "failed" || card.state === "stopped") && (
                      <div className="ws-interruption" role="alert">
                        <p>{card.error || (card.state === "stopped" ? "已停止，已有内容已保留。" : "处理未完成，已有内容已保留。")}</p>
                        <button className="ws-text-button" onClick={() => void ws.continueRun(card.runId)}>继续处理</button>
                        <button
                          className="ws-text-button"
                          onClick={() => void ws.reloadTurn(card.runId)}
                        >
                          <RefreshCw size={16} />
                          重新载入对话
                        </button>
                        <button
                          className="ws-text-button"
                          onClick={() => void ws.retryTurn(card.runId)}
                        >
                          <RefreshCw size={16} />
                          重新做
                        </button>
                      </div>
                    )}
                  </article>
                </Fragment>
              ))}

            </motion.div>
          )}
          </AnimatePresence>
          <div className="ws-reader-end" />
        </div>
      </div>
      <div className="ws-composer-zone">
        <AnimatePresence>
          {!empty && showLatest && (
            <motion.button
              className="ws-latest"
              onClick={() => scrollLatest(true)}
              initial={{ opacity: 0, transform: "translateY(6px)" }}
              animate={{ opacity: 1, transform: "translateY(0)" }}
              exit={{ opacity: 0 }}
            >
              <ArrowDown size={15} />
              回到最新
            </motion.button>
          )}
        </AnimatePresence>
        <motion.div
          layout="position"
          transition={{ type: "spring", stiffness: 400, damping: 40 }}
          className="ws-composer-wrap"
        >
          <form
            className="ws-composer"
            onSubmit={(e) => {
              e.preventDefault();
              atEnd.current = true;
              void ws.send();
            }}
          >
            {selectedTask&&<div className="md-selected-skill"><BookOpen size={14}/>本次任务使用：{selectedTask}<Link to="/workspace/skills">查看 / 更换技能</Link></div>}
            {new URLSearchParams(location.search).has("case")&&<div className="md-selected-skill"><FileText size={14}/>本次发送将附上有权访问的案件记录快照<Link to="/workspace/cases">查看案件</Link></div>}
            <label className="ws-sr-only" htmlFor="workspace-question">
              你的问题
            </label>
            <textarea
              id="workspace-question"
              ref={input}
              value={ws.draft}
              onChange={(e) => ws.setDraft(e.target.value)}
              placeholder="让事实有序，让判断有据。"
              rows={2}
              maxLength={50000}
              disabled={false}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey && !e.ctrlKey && !e.metaKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  atEnd.current = true;
                  void ws.send();
                }
              }}
            />
            <div className="ws-composer-toolbar">
              <div className="ws-composer-actions">
                <Menu
                  align="start"
                  trigger={
                    <IconButton
                      label="添加材料"
                      disabled={false}
                    >
                      <Paperclip size={19} />
                    </IconButton>
                  }
                >
                  <MenuItem onSelect={() => fileInput.current?.click()}>
                    <Paperclip size={16} />
                    上传本轮附件
                  </MenuItem>
                  <MenuItem onSelect={openMaterials}>
                    <Library size={16} />
                    从资料库选择
                  </MenuItem>
                </Menu>
                <Menu
                  align="start"
                  trigger={
                    <button
                      type="button"
                      className="ws-model-trigger"
                      disabled={receiving || ws.busy}
                      aria-label={`选择模型：${selectedModel.label}`}
                    >
                      <span className="ws-model-trigger-icon" aria-hidden="true">
                        <Bot size={14} />
                      </span>
                      <span className="ws-model-trigger-label">{selectedModel.label}</span>
                      <ChevronDown size={14} aria-hidden="true" />
                    </button>
                  }
                >
                  {modelOptions.map((option) => (
                    <MenuItem
                      key={option.id}
                      disabled={!option.available || receiving || ws.busy}
                      onSelect={() =>
                        ws.setModelChoice(option.id as "deepseek-v4" | "gpt")
                      }
                    >
                      <span className="ws-model-menu-option">
                        <span>{option.label}</span>
                        {ws.modelChoice === option.id && <Check size={14} aria-hidden="true" />}
                      </span>
                    </MenuItem>
                  ))}
                </Menu>
              </div>
              {receiving && (
                <button
                  type="button"
                  className="ws-stop"
                  onClick={ws.stop}
                  title="停止当前运行并保留已生成结果"
                >
                  <Square size={13} />
                  停止
                </button>
              )}
              {turn?.status === "running" && <Menu trigger={<IconButton label="发送方式"><ChevronDown size={15} /></IconButton>}>
                <MenuItem onSelect={() => void ws.send()}>排队发送</MenuItem>
                <MenuItem onSelect={() => void ws.steer()}>调整当前任务</MenuItem>
              </Menu>}
              {
                <button
                  className="ws-send"
                  type="submit"
                  aria-label="发送问题"
                  disabled={
                    !ws.draft.trim() ||
                    uploading
                  }
                >
                  <ArrowUp size={20} />
                </button>
              }
            </div>
            <input
              ref={fileInput}
              type="file"
              multiple
              className="ws-sr-only"
              tabIndex={-1}
              aria-label="上传本轮附件"
              onChange={(e) => {
                void ws.uploadFiles(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
          </form>
          {empty ? (
            <div className="ws-suggestions">
              <button onClick={() => chooseTask(4)}>
                <ScanText size={16} />
                梳理事实
              </button>
              <button onClick={() => chooseTask(2)}>
                <Scale size={16} />
                审阅合同
              </button>
              <button onClick={() => chooseTask(0)}>
                <BookOpen size={16} />
                研究法律问题
              </button>
              <Menu
                trigger={
                  <button>
                    <MoreHorizontal size={16} />
                    更多任务
                  </button>
                }
              >
                {tasks.map((task, i) => (
                  <MenuItem key={task.label} onSelect={() => chooseTask(i)}>
                    {task.label}
                  </MenuItem>
                ))}
              </Menu>
            </div>
          ) : (
            <p className="ws-disclaimer">
              分析供研究参考，重要结论请核对原文与适用条件。
              <span>Shift + Enter 换行</span>
            </p>
          )}
        </motion.div>
      </div>
    </div>
  );
}

