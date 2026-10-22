import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  Paperclip,
  Library,
  FileText,
  X,
  LoaderCircle,
  Square,
  Copy,
  Check,
  ChevronDown,
  ArrowUpRight,
  Scale,
  ScanText,
  BookOpen,
  MoreHorizontal,
  RefreshCw,
} from "lucide-react";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { Message, Activity } from "@/lib/workspace-api";
import { materialStatus } from "@/lib/workspace-api";
import { IconButton, Menu, MenuItem, Loading, LoadError } from "./Primitives";

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
    label: "索赔测算",
    prompt:
      "请协助梳理以下索赔的计算依据与项目：\n\n请列出公式、已知条件与缺失数据，不要推定尚未提供的数值。",
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
];

export function Markdown({ content }: { content: string }) {
  return (
    <div className="ws-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
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

function Activities({
  activities,
  live,
}: {
  activities?: Activity[];
  live?: boolean;
}) {
  if (!activities?.length)
    return live ? (
      <div className="ws-activity-live" role="status">
        <LoaderCircle className="ws-spin" size={15} />
        正在等待服务响应…
      </div>
    ) : null;
  const last = activities[activities.length - 1];
  return (
    <details className="ws-activities">
      <summary>
        {live ? (
          <LoaderCircle className="ws-spin" size={15} />
        ) : (
          <Check size={15} />
        )}
        <span>{live ? last.label : "查看处理过程"}</span>
        <ChevronDown size={14} />
      </summary>
      <ol>
        {activities.map((a) => (
          <li key={a.id}>
            <strong>{a.label}</strong>
            {a.detail && <p>{a.detail}</p>}
          </li>
        ))}
      </ol>
    </details>
  );
}

function Answer({
  message,
  onNotice,
}: {
  message: Message;
  onNotice: (message: string) => void;
}) {
  const [copied, setCopied] = useState(false);
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
      <Activities activities={message.activity} />
      <Markdown content={message.content} />
      <div className="ws-answer-tools">
        <button className="ws-text-button" onClick={() => void copy()}>
          {copied ? <Check size={15} /> : <Copy size={15} />}
          {copied ? "已复制" : "复制回答"}
        </button>
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
  const reader = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const atEnd = useRef(true);
  const previousId = useRef(id);
  const [showLatest, setShowLatest] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const detail = ws.detail.data;
  const turn = ws.turn?.chatId === id ? ws.turn : null;
  const receiving = ws.turn?.state === "receiving";
  const empty = !detail?.messages.length && !turn;
  const materials = detail?.pending_materials || [];
  const uploads = ws.uploads.filter((u) => u.chatId === id);
  const uploading = ws.uploads.some((u) => !u.error);
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
        if (!receiving && !ws.busy)
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
              {receiving
                ? "请等待本轮回答完成后添加材料"
                : "松开，添加到当前对话"}
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
              {detail?.messages.map((message) =>
                message.role === "user" ? (
                  <article className="ws-question" key={message.id}>
                    <div>{message.content}</div>
                    {Boolean(message.materials?.length) && (
                      <ul className="ws-sent-materials">
                        {message.materials.map((m) => (
                          <li key={m.id}>
                            <FileText size={15} />
                            <span>{m.filename}</span>
                            <span className="ws-material-state">
                              {materialStatus(m)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </article>
                ) : (
                  <Answer
                    key={message.id}
                    message={message}
                    onNotice={ws.setNotice}
                  />
                ),
              )}
              {turn && (
                <>
                  <article className="ws-question">
                    <div>{turn.question}</div>
                  </article>
                  <article
                    className="ws-answer"
                    aria-busy={turn.state === "receiving"}
                  >
                    <div className="ws-answer-byline">
                      <span className="ws-answer-mark" aria-hidden="true">
                        Z.
                      </span>
                      <span>舟序</span>
                      <span className="ws-stream-state">
                        {turn.state === "receiving" ? "正在回答" : "未确认保存"}
                      </span>
                    </div>
                    <Activities
                      activities={turn.activities}
                      live={turn.state === "receiving"}
                    />
                    {turn.answer && <Markdown content={turn.answer} />}
                    {turn.state === "interrupted" && (
                      <div className="ws-interruption" role="alert">
                        <p>{turn.error}</p>
                        <button
                          className="ws-text-button"
                          onClick={() => void ws.reloadTurn()}
                        >
                          <RefreshCw size={16} />
                          重新载入对话
                        </button>
                      </div>
                    )}
                  </article>
                </>
              )}
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
            <AnimatePresence initial={false}>
              {(materials.length > 0 || uploads.length > 0) && (
                <motion.div
                  className="ws-attachment-list"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                >
                  {materials.map((m) => (
                    <motion.div
                      layout
                      key={m.id}
                      className={`ws-attachment ${m.issue ? "has-issue" : ""}`}
                    >
                      <FileText size={17} />
                      <div>
                        <strong title={m.filename}>{m.filename}</strong>
                        <span title={m.issue || undefined}>
                          {materialStatus(m)}
                          {m.issue ? ` · ${m.issue}` : " · 随本轮发送"}
                        </span>
                      </div>
                      <IconButton
                        label={`移除 ${m.filename}`}
                        disabled={receiving || ws.busy}
                        onClick={() =>
                          void ws.act(() => ws.api.removeMaterial(id!, m.id))
                        }
                      >
                        <X size={14} />
                      </IconButton>
                    </motion.div>
                  ))}
                  {uploads.map((u) => (
                    <motion.div
                      layout
                      key={u.key}
                      className={`ws-attachment ${u.error ? "has-issue" : ""}`}
                    >
                      {u.error ? (
                        <FileText size={17} />
                      ) : (
                        <LoaderCircle className="ws-spin" size={17} />
                      )}
                      <div>
                        <strong>{u.file.name}</strong>
                        <span>{u.error || "正在上传与读取…"}</span>
                      </div>
                      {u.error && (
                        <>
                          <button
                            type="button"
                            className="ws-text-button"
                            onClick={() => void ws.uploadOne(u)}
                          >
                            重试
                          </button>
                          <IconButton
                            label={`移除 ${u.file.name}`}
                            onClick={() => ws.dismissUpload(u.key)}
                          >
                            <X size={14} />
                          </IconButton>
                        </>
                      )}
                    </motion.div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
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
              disabled={receiving || ws.busy}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  if (!turn || turn.state !== "interrupted") {
                    atEnd.current = true;
                    void ws.send();
                  }
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
                      disabled={receiving || ws.busy}
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
                <span className="ws-composer-hint">
                  {detail?.linked_resources.length ? (
                    <button type="button" onClick={openMaterials}>
                      {detail.linked_resources.length} 份已选资料
                    </button>
                  ) : (
                    "仅当前对话使用"
                  )}
                </span>
              </div>
              {receiving ? (
                <button
                  type="button"
                  className="ws-stop"
                  onClick={ws.stop}
                  title="仅停止浏览器接收，后台可能继续处理"
                >
                  <Square size={13} />
                  停止接收
                </button>
              ) : (
                <button
                  className="ws-send"
                  type="submit"
                  aria-label="发送问题"
                  disabled={
                    !ws.draft.trim() ||
                    uploading ||
                    ws.busy ||
                    turn?.state === "interrupted"
                  }
                >
                  <ArrowUp size={20} />
                </button>
              )}
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
