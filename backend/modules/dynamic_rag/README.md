# 动态 RAG 模块

当前通过行动工具接入 Harness、记忆与感知。生产默认每次工具调用做一轮混合检索，返回预算内原文与来源，由主模型结合任务决定回答或继续查询；rag.review_enabled=false不加载内部证据审查模型。原内部“检索→审查→补查”端口保留，可显式启用。

## 算法与边界

1. 工具只暴露 `RagQueryPlan`：`query` 必填，`document_name/location/content_query/anchors/version_scope/limit` 可选。普通回答无此结构。内部 `RagInput` 继承计划并保留可信调用方的 filters；模型不能指定数据库 ID、任意 DSL、运行身份或原始用户问题。
2. 使用常驻本地 BGE-M3 编码本次查询，复用现有数据库交付代码中的 `bm25_body`、`vector_body`、`rrf`。本地模式在进程内强制 Hugging Face/Transformers 离线，生产默认不允许编码失败后静默退为 BM25；BM25 降级只用于明确打开的诊断配置。两路使用相同过滤条件，经 RRF 融合后返回候选；不把分数解释成法律置信度。
3. 对查询中的通用文档身份和结构位置做确定性抽取：识别 `《文档名》` 或常见规范性文件后缀，以及阿拉伯/中文条号，并统一成语料的 `article_no` 形式。这一步不判断海事业务类型，也不增加按业务增长的 `if` 分支。
4. `query_plan.py` 将中文/阿拉伯单条和连续范围统一为 article_no 列表，合并模型与程序锚点。搜索适配器先从库内元数据解析法规名（优先精确名，否则最短后缀匹配），再把法规名、条号、版本变成两路共同过滤条件；只有 BM25 和向量两路正文检索，无第三路精确置顶。内容短语以可配置权重加入 BM25 的 should，不要求所有短语命中；精确条号的正文查询允许 match_all，避免无关内容词阻断定位。未知法规名忽略该可选限制并返回 `document_scope_unresolved`，保留查询与可信 filters，不能将宽泛结果伪装成已验证的目标法规。
5. 按 `chunk_id` 合并本次运行已经见过的证据。相同 ID 的正文/来源变化不能静默覆盖；不同来源的同文内容不按文字去重。
6. 仅启用内部审查时，将原始问题、已执行查询和候选证据交给 `EvidenceReviewPort`。Harness 额外提供感知已选中的少量历史用户原话（默认最多4条、3000字符），帮助解释“后面几条”等指代，不传历史证据正文或模型结论。首个检索 query 是本工具的子任务，充分性不得扩大到用户没问的事项；主模型拆分多次工具调用时，最终跨调用的完整性由主模型负责。未要求历史时优先选择库内现行、相关的最少片段；版本日期不等于施行日期。检查模型由现有模型预设选择，不生成最终答案。
7. 内部审查启用时，已充分则停止；缺证据且值得补查则执行新查询。补查明确要求既定条号范围之外的位置时，返回 `scope_change_required` 和已有相关证据，交主模型决定新的工具调用，避免范围锁定后的无效循环。其余重复查询可以使用一次受控内容扩展；扩展次数由配置限制，当前默认最多一次。补查保持原始过滤条件不变，不允许无限改写查询。
8. 选中的证据按条数和字符预算输出，保留完整原始 chunk，不静默截断，不承诺尚未实现的全文读取。选中的必要证据因预算被省略时，不继续声称充分。

这里的动态性是根据真实检索结果决定下一步和停止时机。简单问题可以一次结束，多要点问题可在剩余预算内补查；不是固定检索三次，也没有按海事业务种类写分支。

默认关闭内部审查时，sufficiency=unassessed，表示未额外评定，不自动代表用户任务缺少资料；stop_reason=single_pass只描述该次工具内部步骤。主模型继续依据正文、当前目标及工具能力决定是否补查。启用审查时，`sufficiency` 是检查器对当前证据的判断，不是法律正确性认证。`is_current` 仅沿用库内标记；历史版本、`unresolved` 和 `amends` 原样保留。法规证据、案件事实、账户信息不能混用。

## 状态与运行记录

- 正常空结果仍为 `ok`。检查器可建议换查询，也可明确不足。
- 编码不可用且配置明确允许时，退为 BM25，并在 `notices` 记录实际降级；生产默认 `allow_bm25_fallback = false`，因此会返回明确的 `rag_embedding_failed`，不会把降级结果记为混合检索。
- 检查器不可用时保留已有结果，停止动态补查，充分性为 `unassessed`。
- 检索失败或超时为 `failed`，可附已获得的部分证据；不会伪装成空结果或完整成功。
- 取消向调用方传播。HTTP 任务可取消；已经执行的本地 PyTorch 推理只能等待结束，单工作线程限制并发，不宣称已强制杀死原生计算。
- `steps` 返回每次查询、命中 ID、新增数、编码/搜索/检查耗时、简短决策依据和可获得的 Token；`notices` 会记录 `content_anchor_applied` 和 `query_expansion_applied`。它目前随结果返回，没有接入持久化 Trace 或管理员页面。

默认上限：最多 3 次检索、最多 1 次内容扩展、总时间 35 秒、返回最多 10 个完整片段；条号范围最多20条，过大或无效的可选位置忽略并留 notice，不静默只取范围前半段。参数、提示词、模型路径、锚点权重和连接配置都在 `backend/config/defaults.toml`。RAG 的字符预算只约束证据载荷，不能替代感知层对完整模型请求的预算检查。

## 文件职责

| 文件 | 内容 |
|---|---|
| `service.py` | 通过搜索和检查端口执行上述闭环，管理本次调用的证据池和预算 |
| `../../contracts/modules/dynamic_rag.py` | 输入、证据、批次、检查结果、步骤记录及输出 |
| `../../contracts/ports.py` | 查询编码、搜索和证据检查接口 |
| `../../infrastructure/search/bge_m3.py` | 常驻本地编码，CLS pooling、float32、L2 归一化，匹配 1024 维库 |
| `../../infrastructure/search/opensearch.py` | 只读搜索、复用已有查询与融合、来源字段映射 |
| `article_locator.py` | 通用文档身份与条号抽取、中文/阿拉伯条号归一化 |
| `query_anchors.py` | 从身份、位置和可配置文本短语中整理通用内容锚点 |
| `query_plan.py` | 工具计划归一化、连续范围、模型/程序锚点合并、补查保持范围 |
| `query_expansion.py` | 在重复或无新增证据时生成一次保留原范围的查询变体 |
| `../../infrastructure/search/deepseek_review.py` | 用已有模型连接做证据检查，证据只放在数据消息中 |
| `../../infrastructure/tools/rag.py` | 通过 Module 端口桥接 Action，预算内输出真实观察与引用投影 |
| `../../bootstrap.py` | `open_rag` 独立入口；`open_harness` 预热、注册工具、管理常驻生命周期 |

依赖为 `DynamicRag → contracts/ports ← adapters`。模块不直接访问数据库、模型 SDK 或其他模块内部实现。未来重排、资料源选择、版本追踪、正文读取依实际需要接入端口，不提前建立插件框架。

## 使用与测试

从工作区根目录安装可选依赖：`uv sync --project backend --extra rag --locked`。

程序入口为 `backend.bootstrap.open_rag(settings=..., events=...)`，在异步上下文内调用 `handle(ModuleRequest[RagInput])`。省略dynamic参数时跟随rag.review_enabled，生产默认false。兼容诊断参数dynamic=True/False显式覆盖内部审查开关；这里的dynamic不表示整个Agent是否可继续检索。默认不加载审查模型凭证，主模型凭证仍由Harness正常使用。编码模型先预热再接收请求，避免把模型加载耗时混入每次查询。

当前部署沿用本机开发凭证与回环地址证书配置；适配器只发查询请求，但当前数据库身份仍为开发 admin，不等同于已配置数据库只读账户。未修改凭证、索引、别名或数据。正式部署需另行配置只读身份及证书。

```powershell
uv run --project backend --extra rag pytest backend/tests -q
uv run --project backend --extra rag ruff check --no-cache backend
uv run --project backend --extra rag python -m backend.tests.benchmarks.run_rag --live --output .ai_tmp/rag_evaluation/comparison.json
```

基准使用同一模型、同一生成提示、同一索引及证据预算，对比单次混合检索与动态检索；题目与预标注证据要点在 `tests/benchmarks/rag_cases.json`。`--retrieval-only` 可只测数据库基线，不调用付费模型；不得把这种运行称为动态效果评测。输出路径必须选新的文件，避免覆盖历史结果。

## Harness 交接与后续关卡

初始感知不检索；推理理解问题后决定是否调用。RAG 返回的完整片段经行动结果写入 run 记忆，再由感知装配。内部 steps 不复制进工具正文。桥接默认 9,000 字符预算，按完整片段删减并记录省略 ID；删减后不宣称证据充分。session 只留来源和版本引用，不能将引用冒充已读取正文。当前rag_tool.max_calls_per_run=0，不额外限制工具次数，仍受总运行时间与轮次约束；默认每次内部一轮，显式启用内部审查后最多三轮，参数均外置。

桥接评估入口为 `backend.tests.benchmarks.run_rag_harness --live --compare`，对照使用同一推断查询、过滤条件、输出预算及生成器，比较第一次工具调用；不代表整个多轮 Harness 的等成本对照。原独立评估保留。RAG 模块本身仍不生成最终答案。

尚未实现私有材料权限、RAG 跨调用证据池、自动法律效力核验、全文读取和前端引用跳转。记忆支持同进程 session 历史，不等于法规检索会话持久化。

## QueryPlan 与普通问答测评

固定题集 `tests/benchmarks/query_plan_cases.json` 共30场景31条消息。`run_query_plan.py --live --compare --output <新文件>` 复用真实 Harness，工具选择保持模型自主；题目不带工具使用提示。31次逐步观察供人工检查，正式结果只保留问题、答案、耗时、计划与评价。传统对照为相同模型查询、相同版本范围的单次纯 BM25＋向量前十，不使用新增的法规/条号范围和内容锚点，也不把其检索耗时拿来冒充完整回答耗时。

`historical` 只筛选库内 `is_current=false`；未知状态不等于历史。指定某日有效性仍需明确证据。文档简称解析是可替换启发式，不承诺所有别名准确；相对指代依赖会话信息和主模型能力，不是程序能理解一切自然语言。

2026-11-04：按用户选择采用查库候选提示词及上下文索引，默认关闭内部模型审查。配置、CLI与FastAPI复用同一open_harness入口。旧独立评测若显式传dynamic=True仍会启用审查，不能用它代表生产默认。对照证据见docs/evaluations/rag-review-ablation-20261104.md；这项选择不保证所有专业结论正确或每次复现同一分数。
