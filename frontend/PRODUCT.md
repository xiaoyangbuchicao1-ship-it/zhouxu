# 舟序产品边界

<!-- impeccable:product-schema 1 -->

本记录依据用户 2026-10-21 的明确改版要求；与旧设计说明冲突时以本轮要求为准。

## Platform

web

## Product Purpose

海事争议资料阅读与持续对话工作台。可以直接提问，材料分次补充，不以建项目为前置条件。

## Capabilities and Constraints

保留登录注册、附件上传、个人资源库多选、项目组织、历史恢复、流式回答、真实处理过程、停止接收与失败恢复。
本次只改前端，不改鉴权、账户隔离、上传限制、数据结构及流式协议。未接入的业务不新增入口或假数据。
合同约定、材料记录、双方主张、模型分析、人工确认必须明确区分；选择资料不代表已引用。

## Brand Commitments

本轮按 docs/前端设计参考与开发提示词.md（2026-10-21 最新修订）完整重构。前两版外观均不沿用；旧白底、青瓷蓝、字体、Logo 呈现和固定布局不再是约束。名称舟序与产品事实保留。探索「深海·仲裁之序」：连续海洋舞台、银白中文、材料目录与受控阅读轴。该方向是本轮要求下的设计探索，不是用户已认可的结果。

首页 Persuade，登录 Operate，工作区 Operate/Read。Taste 只负责首页构图；Impeccable 负责系统与验收；Emil 负责及时、可中断的反馈。

## Evidence on Hand

现有 Next.js / React / TypeScript / Tailwind v4 / CSS Modules / Radix / Lucide / Markdown 实现，`public/zhou-xu-mark.png`、本地字体及许可。
没有可公开的真实案件、原文定位或结构化页码接口；不得伪造分析、引用、进度、工具调用或 PDF 预览。团队姓名、肖像授权、任职与出处尚缺，正式首页不渲染虚构团队；保留经核实内容的接入结构和集中待补清单。

## Accessibility & Inclusion

中文正文和输入至少 16px，辅助说明至少 14px；触控至少 44px；键盘、Escape、焦点归还及减少动效；覆盖 360、390、768、1024、1440px。
