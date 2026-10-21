import type { Metadata } from "next";
import "./globals.css";
import { InputModality } from "@/components/input-modality";

export const metadata: Metadata = {
  title: "舟序 · 海事争议工作台",
  description: "理清案件材料、证据、争点与下一步。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><div hidden aria-hidden="true" dangerouslySetInnerHTML={{ __html: `<!-- THESIS: Deep sea, evidence in order. Reject the split SaaS hero and generic chat skin.
OWN-WORLD: Ink navy, silver Chinese type, cobalt actions, open reading axes and file spines.
STORY: Ask first, add materials, distinguish evidence from analysis, retain human judgment.
FIRST VIEWPORT: Full maritime photograph, upper-left Chinese title, stable lower actions and ship at right. The scene contracts into login; workspace becomes still.
FORM: Brief-pinned deep-sea direction overrides assigned index 4; seed 957b00bc.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md -->` }} /><InputModality />{children}</body></html>;
}
