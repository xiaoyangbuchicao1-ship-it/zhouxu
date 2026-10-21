"use client";
import Link from "next/link";
import { useState } from "react";
import { Menu, ArrowUpRight } from "lucide-react";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import styles from "./landing-page.module.css";

export function LandingNavigation() {
 const [open,setOpen]=useState(false);
 return <>
  <nav className={styles.desktopNav} aria-label="首页导航"><a className={styles.navLink} href="#method">工作方式</a><Link className={styles.navLink} href="/login">登录</Link><Link className={styles.headerCta} href="/app">开始分析</Link></nav>
  <button className={styles.mobileNavButton} type="button" aria-label="打开导航" aria-expanded={open} onClick={()=>setOpen(true)}><Menu /></button>
  <Dialog open={open} onOpenChange={setOpen}><DialogContent className={styles.navigationDrawer}><DialogTitle>舟序</DialogTitle><DialogDescription>海事争议与仲裁分析辅助</DialogDescription><nav aria-label="手机首页导航"><a href="#method" onClick={()=>setOpen(false)}>了解工作方式</a><Link href="/login">登录</Link><Link className={styles.headerCta} href="/app">开始分析 <ArrowUpRight /></Link></nav></DialogContent></Dialog>
 </>;
}
