# 配置层

保存环境配置、阈值、策略、模型和服务参数的定义与说明。

禁止把密钥、真实环境变量、用户材料或运行数据放入此目录。

模型原始能力在 `model_capabilities.toml`，部署限制使用独立 deployment_limits，应用策略在 `defaults.toml` 的 context_policy。窗口比例仅在 model/budget.py 计算；已移除旧 perception.max_context_chars、model.max_tokens 和未生效的材料 max_units/resource_summary_chars。memory 与 RAG 的独立资源参数不代表模型上下文窗口。MinerU 使用 parsing/mineru_runtime 的独立依赖锁，主后端 uv.lock 未因其改变。

2026-11-04：删除未生效的inline_material_chars/pasted_material_chars；不再用字符数冒充模型窗口。model.modes.<none|low|high>.output_tokens分别为8192/16384/32768，是本次生成预留，不是原始最大输出，思考也消耗此额度。review/summary另有1400/1600额度。rag_tool.max_calls_per_run=0仅受总轮次/时间约束。60%～70%是应用策略，不是通用质量最优区间证明。主模型轮数上限50，不是目标执行次数；总运行仍有180秒上限。模式单次模型时限120/120/180秒，始终受剩余总时间约束。

材料导航参数在materials：navigation_preview_chars=1200为整个Run共享的初始预览额度；read_chars=12000为模型未指定读取量时的默认上界；search_max_results=16、search_excerpt_chars=240、search_result_chars=6000约束搜索返回，仍受本次工具分配额度收窄。模型可请求更小范围，不能突破统一预算。搜索/读取工具说明、导航提示和摘要边界说明均来自defaults.toml。有材料绑定时提供两个工具，内容足够时不强制调用。摘要模型只在真实压力下使用，不能把较新未纳入摘要的结果误报为缺失。

主模型固定DeepSeek Flash，通过model.thinking_mode选择none/low/high；不再提供speed/balanced/quality供应商组合。旧model_routing传入工厂会明确拒绝，历史对比脚本不再是生产选择入口。主模型决定方式由model.decision_protocol选择text/explicit（可替换）；默认explicit并使用auto工具选择，同时允许原生最终正文。review保持原JSON合同且关闭思考。
model.delivery_tool/delivery_description和prompts.decision_protocol定义交付控制名称、说明与协议提示。
控制工具计入原统一预算，不实际调用外部服务；compaction仍使用自由摘要正文。
配置修改需重建常驻Harness，未实现热切换。thinking=enabled时使用low/high effort；none发送disabled并省略effort。不得发送thinking+tool_choice=required。compaction独立关闭思考，使用摘要额度，不继承主模型的大输出预留。

context_policy.inventory_*控制当前可见信息索引及元信息的长度，不改变正文或解析分块。索引与系统提示、材料、记忆、工具及私有协议续接一起计入唯一预算。inventory_enabled可关闭用于消融实验，不应据此删除来源和权限校验。当前实现详见docs/architecture.md第18节。

2026-11-04：rag.review_enabled=false控制可选的RAG内部模型审查，与model.thinking_mode是两个独立开关。默认混合检索后将原文交主模型，由主模型决定是否补查；不再为每步检索额外加载审查模型。open_rag省略dynamic时跟随配置，显式dynamic=True/False仅供保留的诊断/对照覆盖。查库候选的任务、证据、生命周期、索引提示及工具说明已写入defaults.toml；不从evaluations目录运行时加载策略。修改后需重建常驻Harness。
