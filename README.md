# 舟序 · 海事智能体工作平台

当前版本把已有研究工作台、自研法规/案例语料、模块化 Harness 和案件工作记录接成可本地运行的产品。面向海事专业人员，重要分析保留专业复核环节。

## 本地启动（Windows）

在本目录执行，Python 使用 uv 管理的 3.11 环境：

```powershell
uv sync --project backend --group dev --extra materials
npm --prefix frontend ci
# 首次启用代码执行（Docker Desktop 的 Linux 引擎需运行）
docker build -t zhouxu-task-runtime:0.2 backend/infrastructure/execution/image
# 首次运行或语料更新后，构建派生全文索引，不修改原始语料
& backend/.venv/Scripts/python.exe -m backend.infrastructure.search.maritime_corpus
Set-Location frontend
npm run dev:all
```

打开 http://127.0.0.1:4185，注册自己的账户。后端使用 8025，避开参考项目原来的 8010/4173。模型密钥由后端读取根目录现有 `.env` 的 `DEEPSEEK_API_KEY`；不要放进 `VITE_` 变量。配置位于 `backend/config/defaults.toml`，模型请求只发往配置允许的 DeepSeek 官方主机。无密钥时仍可检查服务状态，模型任务会如实失败。

开发启动器只监听回环地址；不要把开发服务器直接作为公网生产部署。前端生产产物通过 `npm --prefix frontend run build` 生成；正式托管须配置 HTTPS、SPA 回退及 `/api` 的流式反向代理。

## 已连接的工作流

- **技能工坊**：八项独立海事方法 v2，分别交付适用规则、类案比较、条款修改、责任假设、证据时间线、索赔明细、文书草稿和案件交接；个人创建、改编、版本保存；私有或分享给平台注册用户。私有历史不会随某次公开而开放。
- **研究对话**：真实模型、材料读取/搜索、运行历史、停止/恢复和流式结果；选定技能由服务端解析，客户端不能伪造已解析正文。
- **海事检索**：读取现有 OpenSearch；不可用时明确切换到同源本地全文索引。已索引法规 93,858 片段、案例 3,156 片段。当前默认不运行向量检索，不把库内“现行”当成实时效力认证。
- **执行与文件交付**：隔离任务工作区内使用 read/write/edit/bash，读取本轮授权材料解析文本，编写并运行Python；生成文件经 present 保存快照后，回答中可下载Excel/Word，并可在同一隔离边界内打开前8页分页预览。已接通真实生成、修改、重跑、预览与下载，执行边界和验收见 [执行v1](docs/acceptance/execution-v1/README.md)。
- **正式工作包**：模型通过工具保存当前运行的结构化成果，回答中打开、逐表核对并导出；服务端校验字段、引用和索赔金额，只有本账户能读取。结构合格不代表专业判断正确。
- **引用核对**：短引用标记由后端解析为不可变原文快照链接。检查内部链接能否解析，不保证相关法律结论正确，也不保证任意外部网站真实。
- **索赔测算**：十进制计算、逐项舍入、按币种分别汇总、展示公式和导出 Markdown；不自动推断赔偿责任、汇率或法定期限。
- **案件协作**：明确邀请已注册成员；任务、责任人、日期、证据笔记、文书草稿、复核角色、版本冲突和操作留痕。可撤销成员权限。
- **案件与智能体往返**：有权成员主动附上案件记录快照供分析，再将回答保存为待复核文书；保存不等于发送、提交或律师批准。

合同审查、案件研判、证据整理和文书起草通过“基础技能 + 真实材料工具 + 模型”完成，尚不是经海事专家验收的独立专业系统。当前没有 Word 协同编辑、定时提醒、外部送达、开放插件市场、宿主终端授权或任务容器联网安装。案件笔记不等于关联了资料库原始附件，原始文件仍需在对应对话中明确上传/选择。历史已授权保存的对话快照不会因后续撤销成员而自动抹除。

## 验证与继续维护

本轮改进、失败记录及真实模型样例见 [方法 v2 验收](docs/acceptance/method-v2/README.md)，七个 Huashu 仓库的固定版本研究见 [研究记录](ai-product-craft/research/huashu-20261105/FINDINGS.md)。此前验收在 [docs/acceptance](docs/acceptance)。完整可执行合同见 [acceptance.json](acceptance.json)。复跑原生检查：

```powershell
& backend/.venv/Scripts/python.exe -m pytest backend/tests -q
npm --prefix frontend test
Set-Location frontend
npx tsc -p tsconfig.app.json --noEmit
npx playwright test                 # 需要已运行的本地前后端
# 显式允许真实模型调用后，另跑模型往返用例
$env:ZHOUXU_E2E_LIVE='1'
npx playwright test
```

八项真实模型冒烟：`backend/.venv/Scripts/python.exe backend/tests/scenarios/run_maritime_acceptance.py --live --work-packages`，会使用实际模型额度和创建明确标记的验收账户/数据。它核验执行、必要输出锚点及引用 HTTP 可读性；专业法律准确性仍需人工验收。

本项目采用 [evidence-driven-delivery](ai-product-craft/skill/SKILL.md) 候选工作流。返工经验保存在本项目 `.delivery/lessons`，有证据的经验可提出规则修订，未经审核不自动改全局 Skill。它不训练模型权重，也不保证以后所有聊天自动留痕。

`backend/source-baseline.json` 记录参考后端的迁移起点；`harness/` 保留为架构参考，未整体嵌入本产品。运行数据在 `data/`、`backend/.runtime/`。发布前须单独核实代码、前端素材与语料的授权和分发范围；本次没有提交、推送或公开发布这些内容。
