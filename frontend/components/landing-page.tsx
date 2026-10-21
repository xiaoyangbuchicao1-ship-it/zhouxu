import Link from "next/link";
import Image from "next/image";
import { ArrowUpRight } from "lucide-react";
import { Brand } from "@/components/brand";
import { LandingNavigation } from "@/components/landing-navigation";
import { OceanIntro, MethodExplorer, StartQuestion } from "@/components/landing-interactions";
import { TeamSection } from "@/components/team-section";
import styles from "./landing-page.module.css";

export function LandingPage() {
  return <div className={styles.page}>
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <section className={styles.stage}>
      <div className={styles.scene} data-ocean-scene>
        <Image src="/images/deepsea-cargo.webp" alt="俯瞰海面上的集装箱船，船行航迹穿过银色波光" fill sizes="100vw" preload />
      </div>
      <div className={styles.sceneShade} aria-hidden="true" />
      <header className={styles.header}><Brand inverse /><LandingNavigation /></header>
      <main id="main-content" className={styles.hero}>
        <h1><span data-intro-line>让海事争议，</span><span data-intro-line>回到事实与依据。</span></h1>
        <div className={styles.heroBottom}>
          <p>面向海事争议与仲裁准备，<br />梳理合同、材料与分歧。先提问，再逐步补充。</p>
          <div className={styles.heroActions}><Link className={styles.primaryCta} href="/app">开始分析 <ArrowUpRight size={20} /></Link><a className={styles.secondaryCta} href="#method">了解工作方式 <span aria-hidden="true">↓</span></a></div>
        </div>
      </main>
      <OceanIntro />
    </section>
    <div className={styles.below}>
      <section id="method" className={styles.method}>
        <h2>从材料出发，<br />让判断有据可循。</h2>
        <p className={styles.sectionLead}>合同写了什么，记录说明了什么，哪些还需要确认。<br className={styles.desktopBreak} /> 在同一段对话里，逐步核对。</p>
        <MethodExplorer />
      </section>
      <section className={styles.productSection}>
        <div className={styles.productIntro}><h2>不必准备齐全，<br />就从你的问题开始。</h2><p>输入一个真实问题，进入工作台继续。<br />可以随后添加附件、选择多份资料，<br className={styles.desktopBreak} />也可以从历史对话接着问。</p></div>
        <StartQuestion />
      </section>
      <TeamSection />
      <section className={styles.boundary} id="boundary"><h2>辅助分析。<br />判断，仍由人作出。</h2><div><p>舟序辅助整理材料与分析，不代替仲裁庭或人的最终判断。</p><p>合同约定、材料记录、双方主张与模型分析有不同的归属。通知效力、责任认定及最终金额，仍需结合原件和适用规则确认。</p><Link className={styles.secondaryCta} href="/app">开始分析 <ArrowUpRight size={18} /></Link></div></section>
      <footer className={styles.footer}><Brand compact /><p>海事争议与仲裁分析辅助</p><div><a href="https://commons.wikimedia.org/wiki/File:Aerial_photograph_of_a_cargo_ship.jpg" target="_blank" rel="noreferrer">摄影 Wilfredor / CC BY-SA 3.0</a><a href="/fonts/MiSans-License.pdf">字体许可</a></div></footer>
    </div>
  </div>;
}
