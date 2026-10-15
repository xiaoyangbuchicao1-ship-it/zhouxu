"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
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

  const switchMode = (next: Mode) => { setMode(next); setError(""); setFieldErrors({}); };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
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
      setError(explainError(reason));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <a className="skip-link" href="#auth-form">跳到账户表单</a>
      <header className={styles.header}><Brand /><Link href="/"><ArrowLeft size={17} />返回首页</Link></header>
      <main className={styles.main}>
        <section className={styles.context}>
          <p className={styles.kicker}>海事争议工作台</p>
          <h1>从一个问题，<br />理清一件事情。</h1>
          <p>提问、添加材料、接着追问。<br />你的工作，可以从一次对话开始。</p>
        </section>

        <section className={styles.card} aria-labelledby="auth-title">
          <div className={styles.tabs} role="group" aria-label="账户操作">
            <button aria-pressed={mode === "login"} disabled={loading} type="button" onClick={() => switchMode("login")}>登录</button>
            <button aria-pressed={mode === "register"} disabled={loading} type="button" onClick={() => switchMode("register")}>创建账户</button>
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
      <footer><span>本软件使用 MiSans 字体</span><a href="/fonts/MiSans-License.pdf">查看字体许可</a></footer>
    </div>
  );
}
