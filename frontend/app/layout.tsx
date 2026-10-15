import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "舟序 · 海事争议工作台",
  description: "理清案件材料、证据、争点与下一步。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
