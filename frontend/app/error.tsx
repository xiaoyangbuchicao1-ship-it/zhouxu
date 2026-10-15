"use client";
import { Button } from "@/components/ui/button";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main id="main-content" className="flex min-h-dvh flex-col items-center justify-center gap-5 p-8 text-center"><h1 className="text-2xl">工作台暂时未能打开</h1><p className="text-muted-foreground">请重新加载页面，已保存在此浏览器的案件记录会保留。</p><Button onClick={reset}>重新打开</Button></main>;
}
