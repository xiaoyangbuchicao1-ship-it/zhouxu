"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, ArrowUpRight, FileText, MessageSquare, Layers } from "lucide-react";
import styles from "./landing-page.module.css";

export function OceanIntro() {
  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || sessionStorage.getItem("zhouxu-ocean-seen")) return;
    sessionStorage.setItem("zhouxu-ocean-seen", "1");
    const animations: Animation[] = [];
    document.querySelectorAll<HTMLElement>("[data-intro-line]").forEach((line, i) => {
      animations.push(line.animate([{ clipPath: "inset(0 0 100% 0)", transform: "translateY(20px)" }, { clipPath: "inset(0 0 0 0)", transform: "translateY(0)" }], { duration: 640, delay: i * 80, easing: "cubic-bezier(.16,1,.3,1)" }));
    });
    const scene = document.querySelector<HTMLElement>("[data-ocean-scene]");
    if (scene) animations.push(scene.animate([{ opacity: .5, transform: "scale(1.035)" }, { opacity: 1, transform: "scale(1)" }], { duration: 1100, easing: "cubic-bezier(.16,1,.3,1)" }));
    return () => animations.forEach(a => a.cancel());
  }, []);
  return null;
}

const chapters = [
  { title: "核对合同", subtitle: "先确定约定", file: "航次租船合同", kind: "合同约定", heading: "装卸时间与通知", text: "“有效的准备就绪通知送达后，装卸时间的起算按合同约定执行。”", note: "先读约定，再核对通知送达与船舶实际准备情况。条款内容不自动等于履行事实。", Icon: FileText },
  { title: "整理材料", subtitle: "再对照记录", file: "港口作业记录与往来函件", kind: "材料记录", heading: "同一时间，不同记载", text: "作业记录：周一 08:00 抵达锚地。来函：周一 10:30 发出通知。通知是否有效，仍需对照合同及送达凭据。", note: "把文件记载与各方陈述分开。材料不齐时，先保留已有依据，再补关键缺口。", Icon: Layers },
  { title: "继续追问", subtitle: "明确待确认项", file: "本轮问题与待核对事项", kind: "人工确认", heading: "如果通知效力存在争议？", text: "可以继续追问：哪些材料会改变起算时间的判断？合同中的例外条款是否适用？费用计算分别以什么假设为前提？", note: "追问沿用当前对话。模型分析提供思路，最终事实与适用规则仍需人工确认。", Icon: MessageSquare },
];

export function MethodExplorer() {
  const [index, setIndex] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const animationRef = useRef<Animation | null>(null);
  const keyboardRef = useRef(false);
  const chapter = chapters[index];
  useEffect(() => {
    animationRef.current?.cancel();
    if (!keyboardRef.current && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      animationRef.current = panelRef.current?.animate([{ opacity: .45, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 500, easing: "cubic-bezier(.16,1,.3,1)" }) ?? null;
    }
    keyboardRef.current = false;
    return () => animationRef.current?.cancel();
  }, [index]);
  const onKey = (event: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const next = event.key === "ArrowRight" || event.key === "ArrowDown" ? (i + 1) % 3 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? (i + 2) % 3 : event.key === "Home" ? 0 : event.key === "End" ? 2 : null;
    if (next === null) return;
    event.preventDefault(); keyboardRef.current = true; setIndex(next);
    document.getElementById(`method-tab-${next}`)?.focus();
  };
  return <div className={styles.explorer}>
    <div className={styles.methodTabs} role="tablist" aria-label="工作方式">
      {chapters.map((item, i) => <button id={`method-tab-${i}`} role="tab" key={item.title} type="button" aria-selected={i === index} aria-controls="method-panel" tabIndex={i === index ? 0 : -1} onKeyDown={e => onKey(e, i)} onClick={e => { keyboardRef.current = e.detail === 0; setIndex(i); }}><b>{item.title}</b><small>{item.subtitle}</small></button>)}
    </div>
    <div className={styles.methodPanel} ref={panelRef} id="method-panel" role="tabpanel" aria-labelledby={`method-tab-${index}`} tabIndex={0}>
      <article className={styles.samplePaper}><h3>{chapter.file}</h3><p className={styles.materialLabel}><span>{chapter.kind}</span><span>合成材料示例，非真实案件</span></p><p><strong>{chapter.heading}</strong></p><blockquote>{chapter.text}</blockquote></article>
      <p className={styles.panelFoot}><chapter.Icon size={18} />{chapter.note}</p>
    </div>
  </div>;
}

export function StartQuestion() {
  const [question, setQuestion] = useState("");
  const router = useRouter();
  const start = (event: FormEvent) => {
    event.preventDefault();
    sessionStorage.setItem("zhouxu-start-question", question.trim());
    router.push("/app");
  };
  return <form className={styles.startQuestion} onSubmit={start}>
    <label htmlFor="first-question">你想先理清什么？</label>
    <div className={styles.startComposer}><textarea id="first-question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：双方对装卸延误有分歧，我应先核对哪些材料？" /><button type="submit">带着问题进入工作台 <ArrowUpRight size={20} /></button></div>
    <p>这是工作台的实际提问入口。尚未登录时会先进入登录页；草稿不会自动发送。</p>
  </form>;
}
