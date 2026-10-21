"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, CircleAlert, LoaderCircle } from "lucide-react";
import { Brand } from "@/components/brand";
import { explainError, gateway, saveSession, type Account } from "@/lib/gateway";
import styles from "./auth-panel.module.css";

type Mode = "login" | "register";
type AuthResponse = { account: Account; session: { token: string } };

export function AuthPanel({ initialMode }: { initialMode: Mode }) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<"name" | "email" | "password", string>>>({});
  const panelRef = useRef<HTMLElement>(null);
  const modeAnimationRef = useRef<Animation | null>(null);
  useEffect(() => () => modeAnimationRef.current?.cancel(), []);

  const switchMode = (next: Mode, pointer: boolean) => {
    if (next === mode) return;
    modeAnimationRef.current?.cancel();
    setMode(next); setError(""); setFieldErrors({});
    router.replace(next === "register" ? "/login?mode=register" : "/login", { scroll: false });
    if (pointer && !matchMedia("(prefers-reduced-motion: reduce)").matches) modeAnimationRef.current = panelRef.current?.animate([{opacity:.7},{opacity:1}], {duration:200,easing:"cubic-bezier(.23,1,.32,1)"}) ?? null;
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loading) return;
    setError("");
    const invalid: typeof fieldErrors = {};
    if (mode === "register" && !name.trim()) invalid.name = "请输入你的称呼。";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) invalid.email = "请输入有效的邮箱地址。";
    if (password.length < 12) invalid.password = "密码至少需要 12 个字符。";
    setFieldErrors(invalid);
    const firstInvalid = Object.keys(invalid)[0];
    if (firstInvalid) {
      (event.currentTarget.elements.namedItem(firstInvalid) as HTMLInputElement)?.focus();
      return;
    }
    setLoading(true);
    try {
      const response = await gateway<AuthResponse>(mode === "login" ? "auth/login" : "auth/register", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(mode === "login" ? { email, password } : { email, password, display_name: name }),
      });
      saveSession({ account: response.account, token: response.session.token });
      router.push("/app");
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      if (["account_email_exists", "email_invalid"].includes(code)) setFieldErrors({ email: explainError(reason) });
      else if (["authentication_failed", "password_length_invalid"].includes(code)) setFieldErrors({ password: explainError(reason) });
      else setError(explainError(reason));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.backdrop}><Image src="/images/deepsea-cargo.webp" alt="" fill sizes="100vw" preload /></div>
      <a className="skip-link" href="#auth-form">跳到账户表单</a>
      <header className={styles.header}><Brand /><Link href="/"><ArrowLeft size={17} />返回首页</Link></header>
      <main className={styles.main}>
        <section className={styles.context}>
          <h1>把分歧展开。<br />把依据留下。</h1>
          <p>从一个问题，进入材料与事实。<br />每一次追问，都可以接着上一次继续。</p>
        </section>

        <section ref={panelRef} className={styles.card} aria-labelledby="auth-title" aria-busy={loading}>
          <div className={styles.tabs} role="group" aria-label="账户操作">
            <button aria-pressed={mode === "login"} disabled={loading} type="button" onClick={(event) => switchMode("login", event.detail > 0)}>登录</button>
            <button aria-pressed={mode === "register"} disabled={loading} type="button" onClick={(event) => switchMode("register", event.detail > 0)}>创建账户</button>
          </div>
          <div className={styles.cardHead}>
            <h2 id="auth-title">{mode === "login" ? "欢迎回来" : "开始使用舟序"}</h2>
            <p>{mode === "login" ? "登录后，继续你的对话。" : "创建账户，保存对话与资料。"}</p>
          </div>
          <form id="auth-form" onSubmit={submit} noValidate>
            {mode === "register" ? <label><span id="name-label">你的称呼</span><input aria-labelledby="name-label" name="name" autoComplete="name" disabled={loading} value={name} onChange={(event) => { setName(event.target.value); setFieldErrors((current) => ({ ...current, name: undefined })); }} placeholder="希望我们怎样称呼你" aria-invalid={Boolean(fieldErrors.name)} aria-describedby={fieldErrors.name ? "name-error" : undefined} />{fieldErrors.name ? <small id="name-error" className={styles.fieldError}>{fieldErrors.name}</small> : null}</label> : null}
            <label><span id="email-label">邮箱</span><input aria-labelledby="email-label" name="email" type="email" autoComplete="email" disabled={loading} value={email} onChange={(event) => { setEmail(event.target.value); setFieldErrors((current) => ({ ...current, email: undefined })); }} placeholder="name@example.com" aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? "email-error" : undefined} />{fieldErrors.email ? <small id="email-error" className={styles.fieldError}>{fieldErrors.email}</small> : null}</label>
            <label><span id="password-label">密码</span><input aria-labelledby="password-label" name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} disabled={loading} value={password} onChange={(event) => { setPassword(event.target.value); setFieldErrors((current) => ({ ...current, password: undefined })); }} placeholder={mode === "register" ? "至少 12 个字符" : "输入密码"} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "password-error" : undefined} />{fieldErrors.password ? <small id="password-error" className={styles.fieldError}>{fieldErrors.password}</small> : null}</label>
            {error ? <p className={styles.error} role="alert"><CircleAlert size={17} />{error}</p> : null}
            <button className={styles.submit} type="submit" disabled={loading}>{loading ? <><LoaderCircle className={styles.spin} />{mode === "login" ? "正在登录" : "正在创建账户"}</> : <>{mode === "login" ? "登录" : "创建账户"}<ArrowRight size={18} /></>}</button>
          </form>
          <p className={styles.legal}>舟序辅助分析与研究，关键判断由你确认。</p>
        </section>
      </main>
      <footer><a href="https://commons.wikimedia.org/wiki/File:Aerial_photograph_of_a_cargo_ship.jpg" target="_blank" rel="noreferrer">摄影 Wilfredor / CC BY-SA 3.0</a><a href="/fonts/MiSans-License.pdf">MiSans 字体许可</a></footer>
    </div>
  );
}
