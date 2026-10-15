import Link from "next/link";
import { ArrowRight, FileText } from "lucide-react";
import { Brand } from "@/components/brand";
import styles from "./landing-page.module.css";

const steps = [
  { number: "01", title: "提出问题", body: "先聊一个问题。不必先建项目，也不必一次准备齐所有材料。" },
  { number: "02", title: "添加材料", body: "直接上传文件，或从资源库选择多份资料，放进当前对话。" },
  { number: "03", title: "接着追问", body: "补充情况、比较结果、提出异议。重新打开对话，也可以继续。" },
];

export function LandingPage() {
  return (
    <div className={styles.page}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className={styles.header}>
        <Brand />
        <nav aria-label="首页导航">
          <a className={styles.navLink} href="#method">如何使用</a>
          <Link className={styles.navLink} href="/login">登录</Link>
          <Link className={styles.headerCta} href="/login?mode=register">开始使用</Link>
        </nav>
      </header>
      <main id="main-content">
        <section className={styles.hero}>
          <div className={styles.heroCopy}>
            <p className={styles.kicker}>海事争议工作台</p>
            <h1>把海事问题，<br /><em>一步步理清。</em></h1>
            <p className={styles.lead}>核对合同、梳理争议、计算费用。<br />可以先提问，材料分次补充。</p>
            <div className={styles.heroActions}>
              <Link className={styles.primaryCta} href="/login?mode=register">开始使用 <ArrowRight size={19} /></Link>
              <a className={styles.secondaryCta} href="#method">看看怎么用</a>
            </div>
          </div>
          <div className={styles.productPreview} aria-label="虚构的对话示意，非实际分析结果">
            <div className={styles.previewTop}><Brand compact /><span>对话示意</span></div>
            <div className={styles.previewBody}>
              <div className={styles.previewPrompt}>
                <p>帮我整理这份合同的通知要求。</p>
                <span><FileText size={17} />租船合同.pdf</span>
              </div>
              <div className={styles.previewAnswer}>
                <h2>先核对送达要求</h2>
                <p>通知发出与有效送达，需要分别核对。</p>
                <ul><li>合同指定谁接收通知</li><li>材料记录何时收到通知</li><li>哪些生效条件仍有争议</li></ul>
                <span className={styles.previewSource}>依据：租船合同 · 通知条款</span>
              </div>
            </div>
          </div>
        </section>
        <section className={styles.method} id="method">
          <div className={styles.sectionIntro}><p>如何使用</p><h2>一个问题，就可以开始。</h2></div>
          <div className={styles.steps}>{steps.map((step) => <article key={step.number}><span>{step.number}</span><h3>{step.title}</h3><p>{step.body}</p></article>)}</div>
        </section>
        <section className={styles.boundary} aria-label="专业判断边界">
          <h2>辅助你分析，由你作出判断。</h2>
          <p>舟序帮助整理依据与争议。涉及通知效力、责任认定和最终金额时，仍需结合原件与适用规则确认。</p>
        </section>
      </main>
      <footer className={styles.footer}>
        <Brand compact />
        <div><a href="/fonts/MiSans-License.pdf">字体许可</a><span>本软件使用 MiSans 字体</span></div>
      </footer>
    </div>
  );
}
