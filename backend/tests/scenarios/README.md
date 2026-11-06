# 单智能体首版回归集

目标是覆盖中小产品首版的主要使用方式和高风险边界，服务“做到80%即可”的交付尺度。
没有真实用户流量分布，不能声称这32题等于覆盖80%的用户请求，更不能把程序通过率当业务准确率。
本目录只供测试，生产代码不导入；模型不接收 expected、rubric、参考答案或风险标签。

## 固定场景与材料

| 分组 | 稳定ID | 要验证的不同失败模式 |
|---|---|---|
| 办公 | O01—O06 | 问候、改写、翻译、表格抽取、计算、占位草稿；不强制检索 |
| 信息依据 | G01—G04 | 已知与未知、冲突、材料注入指令、没有记载与未发生的区别 |
| 决策 | D01—D04 | 核实私人事实与方法说明配对；必要事实与可占位字段配对 |
| 材料 | M01—M08 | 预览足够、局部与全文、多来源、CSV、长文首中尾、长粘贴 |
| 多轮 | T01—T04 | 最新纠正、保持工作范围、历史指代、事实增量更新 |
| 工具困难状态 | F01—F03 | 真实模型面对测试注入的故障、部分、空回执 |
| 法规工具 | R01—R03 | 精确条文要正文、概念说明不强查、相对条号承接后检索 |
| 专业补充 | P01 | 用户原始完整Word，混合判断/选择、部分无题号、历史法律语境 |

core_v1：32个不同场景，24个基础场景、8个挑战场景，共37个用户轮次。
professional_v1：独立的1个真实专业场景，不与简单资料题混算专业准确率。
“挑战”用于难例报告，不是真正盲测集；本轮作者已看过，不虚称独立外部评审。

core_v1的Word题是可确定答案的资料理解夹具，用来区分“文件链路不通”和“专业知识判断错”。
长文有开头、中段、末尾不同信息，标记不在初始预览内。短材料不强制工具，长文搜索命中本身
足以回答位置查询时不强迫再读取。max_calls只是性能参考，超出记warning，不修改生产轮数上限。

专业Word是用户提供的《单选题.docx》的逐字节副本，位于 fixtures/maritime_questions.docx；
本地测试使用，不向外发布。前十题参考答案来自用户《单选题答案.docx》的首组1—10，
不是文末重新编号的另一组。参考答案有历史语境，不能冒充当前法律权威。
使用原始完整文件而非仅复制前十题，以便测出范围识别、原顺序和不必要工具调用。

## 三层验收

1. 离线合同：可大量运行，不消耗模型额度。覆盖状态、预算、关联、异常、恢复与隔离。
2. 真实模型：同时提供实际工具目录，使用生产模型适配器和Harness；不以脚本模型冒充真实模型。
3. 主观审阅：看实际消息、决定、工具回执、下一轮上下文和交付；不索取或保存隐藏思维链。

程序断言检查状态、固定事实、工具选择、正文证据存在、流式一致、预算和已知内部字段。
这些不能证明每句话正确。例如“14:30”出现，不代表“其他字段原件没登记”的推断合理。
自动题号解析失败必须标记人工检查；不得悄悄给满分。
现有Harness正常等待可携带missing_input标记；仅当目标与实际状态都是needs_input、问题非空时接受该标记。
超时、空追问或completed携带missing_input仍失败，不能把真实等待误计为系统故障。
每轮都需要按rubric检查事实忠实、范围、策略和用户呈现。选项正确但理由错误，不算专业通过。

## 系统边界覆盖：复用已有测试

| 风险 | 真实检查入口（backend/tests/下） |
|---|---|
| 大量段落引用膨胀、保留私有覆盖记录 | integration/test_core_reference_projection.py |
| 预算水位、压力整理、保留最新观察 | integration/test_material_navigation.py、test_unified_context.py |
| 部分/空/错误回执、取消、未知副作用、重复操作 | integration/test_reasoning_action.py |
| 工具调用与结果原生配对 | integration/test_reasoning_action.py、test_adapters.py |
| 用户事实与指令身份、去重与来源 | unit/test_perception.py |
| 流式正文、终态与断线序号 | integration/test_streaming.py、test_workbench_contract.py |
| 幂等提交、失败部分答案落盘 | integration/test_workbench_contract.py |
| 等待、继续、队列与进程恢复 | integration/test_scheduler_harness.py、test_scheduler_persistence.py |
| Conversation材料继承与权限 | integration/test_workspace_api.py、test_material_upload_api.py |
| 账户隔离与认证 | integration/test_auth_api.py、test_workspace_api.py |
| 检查器能否抓住故意错误的结果 | unit/test_core_scenarios.py |

上述系统测试用临时SQLite、脚本模型或MockTransport时，只证明合同，不证明真实模型业务质量。
不要为了增加数量再复制一套相同测试。保留固定样本、失败样本和明确边界，比重复跑同一问答更重要。

本轮没有覆盖：真实浏览器点击/断网，OCR与复杂PDF版面质量，图像理解，生产并发负载，
写入外部系统的副作用，任意长输出续写，全部模型预设、全面法律知识正确性。
既有单元涉及这些格式，不代表本轮重新进行了全链路人工验收。

## 命令（工作区根目录）

```powershell
# 不联网的完整后端回归
backend/.venv/Scripts/python.exe -B -m pytest backend/tests -q -p no:cacheprovider

# 以下显式消耗额度；保持证据文件独立，不允许覆盖已有文件
backend/.venv/Scripts/python.exe -B -m backend.tests.scenarios.run_core --live --output .ai_tmp/core-new.json
backend/.venv/Scripts/python.exe -B -m backend.tests.scenarios.run_core --live --cases F01,F02,R03 --repeats 2 --output .ai_tmp/core-focused-new.json
backend/.venv/Scripts/python.exe -B -m backend.tests.scenarios.run_core --live --corpus backend/tests/scenarios/professional_v1.json --output .ai_tmp/professional-new.json

# 同一Run真实等待→补充→恢复；使用合成流水摘录，不是实际付款核验
backend/.venv/Scripts/python.exe -B -m pytest backend/tests/integration/test_explicit_decisions.py::test_paid_native_wait_and_resume_keep_one_run --live -q -s
```

默认使用DeepSeek的model.thinking_mode（none/low/high），凭证走生产读取器，不复制密钥。旧speed/balanced/quality是历史研究配置，不再作为生产选择。编码器启动耗时单列，Run耗时不含预热。
主模型call的首字不是网页首字。tokens只统计module=model的主模型事件，RAG审核的直接请求未进入该事件列表，不能当完整用量账单；不把预算估算当精确Token。
真实材料派生文件在独立临时目录中，结束自动清理；输出只保存合成/已授权题库的输入与公开决定，
没有账户令牌、HTTP认证头或供应商隐藏推理。不要拿此采集器记录未经授权的客户私密材料。

退出码：0表示当前自动断言通过，仍需人工审阅；1表示存在自动断言失败；2表示模型服务不可用。
运行结束状态responses_collected/human_review_required均不等于质量验收通过。

## 局部修复与停止规则

先保存失败证据；按最早出错边界归并根因，给出文件白名单，只修一个根因。
修复后跑原失败项、相邻合同和固定核心集；只有新变化、失败或未解决风险才补充测试。
P0/P1未解决必须明示不能通过发布关卡，不以通过率淹没严重问题。
偏长、风格、额外合理读取等P2进入清单，达到关卡后停止，不借此扩写架构。
新增场景应有不同风险、清楚原始事实、可复核期望和反例；不只堆换一种说法的相同问答。
改题、改gold或改rubric都要记录理由；不得为让当前模型通过而降低期望。

## 当前可见信息与三档协议回归（2026-11-04）

context_delivery_v1.json引用上述固定场景，不修改core_v1的题目或期望。另有原Word自然问法“前十题”/“全部题目”和明确标为合成的64K部署压力测试。压力夹具通过真实历史body触发整理，不以资源外置成功冒充语义压缩成功。数据与范围详见docs/evaluations/context-delivery-20261104.md。

```powershell
backend/.venv/Scripts/python.exe -X utf8 -B -m backend.tests.benchmarks.run_context_delivery --live --workdir .ai_tmp/context-new --output .ai_tmp/context-new.json
backend/.venv/Scripts/python.exe -X utf8 -B -m backend.tests.benchmarks.run_context_delivery --live --mode high --cases P10,P97 --workdir .ai_tmp/context-high-new --output .ai_tmp/context-high-new.json
backend/.venv/Scripts/python.exe -X utf8 -B -m backend.tests.benchmarks.run_context_delivery --live --without-inventory --cases P10,P97 --workdir .ai_tmp/context-ablation-new --output .ai_tmp/context-ablation-new.json
```

支持--source-root指向独立基线worktree，用同一个驱动运行旧代码；两侧统一50轮。baseline不支持的新模式必须明确拒绝，不能替换旧适配器后仍称纯基线。观察器位于预算传输守卫内部，记录实际消息、调用参数、回执、正文可见覆盖、时限与用量。隐藏推理仅记录长度，不写正文。原生流和最终正文一致不等于验证了浏览器逐字动画。

analyze_context_delivery.py用--reference <用户答案.docx> --reports <多个证据.json> --output <分析.json>离线评分，答案文件绝不传入模型。缺题、分组或解析不一致时不得给总分。首选项匹配和解释质量分开；主观检查不能省略。高档更慢、预留更大，但不承诺更准确。早期未采集到的摘要usage必须当未知，不能作0计完整账单。
