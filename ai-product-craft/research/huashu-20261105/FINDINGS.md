# 花叔技能研究与舟序改造

核验日期：2026-11-05。账号：alchaincyf。口径：按公开技能相关仓库的 Star 排序，包含技能合集，排除电子书、桌面应用、仅 MCP 工具和 Fork。Star 是当时快照，不代表技能质量或增长因果；没有查到这些项目达到百万 Star，不能将目标写成既成事实。

| 排名 | 项目 | Star 快照 | 具体学习材料 | 对舟序有用的机制 |
|---|---|---:|---|---|
| 1 | [nuwa-skill](https://github.com/alchaincyf/nuwa-skill) | 37,510 | SKILL、extraction-framework、fidelity-scorecard、quality_check | 从需求选决策框架，要求框架有行动证据和区分度，不是模仿口吻 |
| 2 | [huashu-design](https://github.com/alchaincyf/huashu-design) | 28,687 | SKILL、evolution、verification、并行案例 | 真实资产与用途决定结构；反馈、外观重复与可执行检测分别留账 |
| 3 | [zhangxuefeng-skill](https://github.com/alchaincyf/zhangxuefeng-skill) | 10,706 | SKILL、决策记录、时间线 | 事实问题与框架问题分流；先查影响判断的数据，显式写失败回退 |
| 4 | [darwin-skill](https://github.com/alchaincyf/darwin-skill) | 6,804 | SKILL、skilllens-evidence | 同题配对比较、实际输出与反例；绝对自评分不用于保留改动 |
| 5 | [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion) | 6,251 | SKILL、CHANGELOG、正面经验、经验回流 | 不同媒介有不同路径和可观察产物；重复失败进入检测器，而不是无限追加禁令 |
| 6 | [huashu-motion](https://github.com/alchaincyf/huashu-motion) | 2,363 | SKILL、contract、experience、verification | 产物可定位、可编辑、可版本化；用户指到的具体位置成为修订上下文 |
| 7 | [huashu-skills](https://github.com/alchaincyf/huashu-skills) | 2,205 | skills.json、research、skill-updater | 按需求索引能力，研究增量保存，安装来源和版本可追踪 |

38 份文本材料已固定提交并保留许可证，见 sources.lock.json 和 sources/。未执行上游脚本、安装钩子、推送或采集私人资料。上游文档中的分数、论文数字和个人经历只作为作者主张记录；这里没有独立复现实验，不引用它们为舟序效果背书。

## 从公开迭代能看到什么

- nuwa 将“明确人物”与“模糊需求”分成两个入口，再把资料提炼成模型、启发式与边界；公开提交后续解决宿主目录和插件安装问题。可迁移的是“先找会改变决定的机制”，不是冒充专业人士。
- design 的经验文档记录过“多方向实际仍是换皮”“偏好外溢成统一审美”“用换符号躲检测”等失败，并将部分问题移到检测脚本和引擎。对应舟序本次失败：八项说明前半段相同，术语变化没有改变产物。
- 张雪峰技能的公开提交明确增加失败回退、检查点和黑名单。可借鉴的是按问题选择研究路径；舟序不采用其强确定性角色语气，法律不确定性必须保留。
- Darwin 的公开版本说明把绝对分差降为粗排，改用同一评判条件下的配对判断；提交记录还修复了扫描器把自己的说明判成红灯。对应舟序：先保留旧版，运行同题，再检查实际错误与近似任务是否被过度处理。
- art-motion 的记录把“画面问题”回到脚本、素材或引擎，交付脚本承担能量化的门槛。对应舟序：表格缺列、引用不可读、数值不能复算由代码拒绝，不让模型自行宣称合格。
- motion 把画面元素与时间位置变成稳定标识。对应舟序：争点、条款、证据事件、金额项目与交接任务成为可独立核对的交付行，而不是全部混在聊天长文里。
- 合集通过机器目录和版本留痕降低选错技能、装错版本的成本。对应舟序：每项技能显示输入、具体方法、交付物和开始示例，保留 v1 的可读历史。

这些是对公开材料的观察与迁移推论，不是作者完整私人创作史；当前提交历史也不能证明哪项机制带来了多少 Star，更不能用一次研究冒充一年真实实战。

## 本次原创落点

舟序围绕海事工作里需要交接和核对的对象设计八条路径：

| 工作 | 关键决定 | 独立产物 |
|---|---|---|
| 法律研究 | 事件时间/法域/适用前提是否支持引用规则 | 规则适用表、事实对应 |
| 类案研究 | 相似事实下，哪项差异会改变裁判理由 | 类案差异矩阵、本案启示 |
| 合同审查 | 当前立场愿意交换什么风险、哪些不可退让 | 原文/替代条款/谈判底线 |
| 案件研判 | 哪种相反解释仍然成立、哪项证据能翻转判断 | 主体关系、责任假设对照 |
| 证据整理 | 这个事件由什么材料支持、哪里矛盾或只有转述 | 时间线、待证事实与补证动作 |
| 索赔辅助 | 项目是否重复、口径与单位是否一致、金额能否复算 | 按币种明细、未定条件 |
| 文书起草 | 为哪个读者推动哪个动作、哪些字段必须补齐 | 可编辑正文、签发前补齐 |
| 案件协作 | 下一步依赖谁、什么状态与凭证证明任务完成 | 推进依赖、专业交接 |

实现位置：backend/skills/maritime 各自独立的 SKILL.md 和 method.json；prepare_maritime_work 工具保存私人运行工作包，检查列与字段、引用解析和金额复算；对话页可打开、逐表核对和导出。结构检查不会自动证明原文忠实、事实成立或法律判断正确。

共用运行约束由服务端单独注入，面向用户的默认方法页展示业务步骤；改编时取得该技能自己的规范。原有 v1 存为历史版本，已有运行快照保持其原文。

公开海事实务资料同时用于校正工作对象：

- [Gard cargo surveys](https://gard.no/forms/p-and-i-surveys-forms-instructions/)：检查、调查与原始凭证是实际工作对象；不把其个案规则当普遍适用法。
- [Wallenius Wilhelmsen cargo claim guidance](https://www.walleniuswilhelmsen.com/ocean-support/faq/how-do-i-file-a-claim-if-i-do-not-have-cargo-insurance)：索赔材料应支持损失范围，期限依单证和适用法核对。
- [Allianz cargo claims](https://www.allianz.co.uk/broker/documents-and-tools/claims/cargo.html)：货损金额、运输文件与责任证据分别组织，计算与责任判断分开。

验证以真实模型配对样例、工作包保存与读取、金额/引用负向测试、浏览器交付和独立审查为准。少量样例只支持当前条件下的观察；没有统计稳定性或海事专家验收时，不宣称“专业质量全面提升”。
