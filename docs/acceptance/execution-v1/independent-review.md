# 执行工具第一轮独立审查

2026-11-05。审查runtime.py、worker.py、bootstrap接线、loop销毁和交付API；实际Docker探针脚本在.ai-tmp/execution-review-20261105/probe.py，原始结果已归档probes/initial.json（原results.json）。仅创建并销毁自己的两个zhouxu-task容器，未读取凭证、未修改业务代码或其他容器。

## 已复现：read操作可被参数改为write

runtime.operate把请求序列化为`{op: trusted_op, **value}`。read的模型参数携带`op:write, content:unexpected mutation, path:injected.txt`时，返回ok:true并实际创建19字节文件；紧接read读出unexpected mutation。这是标记read_only的工具执行写操作。schema的additionalProperties:false没有在此执行边界验证，因此不能依赖模型遵守schema。

修复应服务端拒绝未知字段/操作，且可信op不可被value覆盖；应保留跨RegisteredAction的回归。该缺陷未观察到宿主逃逸，但违反工具能力与副作用合同。

## 源码确认的可用性问题

worker.content先path.open再fstat；若bash创建FIFO，读取会在验证普通文件之前阻塞。30秒外层超时会销毁整个workspace；特殊文件不应把合法同run材料一并拖入超时恢复。应无阻塞打开并核对文件类型，考虑符号链接竞态。路径target与open间也存在TOCTOU；当前无宿主挂载限制其宿主影响，但不宜把路径检查描述为无竞态防护。

runtime.locks按每个run创建且销毁时不清除，长时间运行会积累锁对象；属于源码观察，尚未运行大规模内存压力探针，不作为已复现耗尽。

## 实际通过的有限探针

- 两run：A创建文件，B读同名路径返回不存在。
- read ../etc/passwd拒绝；bash创建指向/etc/passwd软链，read拒绝symlink。
- bash产生200001字节stdout，宿主返回65536字节，报告输出上限；未执行无限输出压测。
- 交付report.txt内容abc，后修改源为changed，服务端快照仍为abc。
- 实际inspect已存档，用于核对network none、readonlyrootfs、capdrop、nonroot与资源配置；配置存在不等同逐项攻击防御实测。
- 所有独立容器finally销毁。

## 尚未完成

下载路由源码先以conversation/session查run，并验证run.account_id、conversation_id，随后按run_id/artifact_id读快照；Content-Disposition编码文件名并nosniff、no-store。尚未独立执行真实双账号HTTP下载。

尚未测网络拒绝、磁盘/内存/PID耗尽、取消派生进程、后端重启恢复、Docker不可用和真实Office业务内容。本报告不作完整安全或专业质量验收；已复现缺陷修复后需重跑原探针。


## 修复复查（保留以上初轮发现）

使用本轮新构建zhouxu-task-runtime:0.1；独立脚本.ai-tmp/execution-review-20261105/recheck.py，原始结果recheck-results.json。未修改业务代码。

- 原read/op写入探针现返回invalid_operation_fields，且未创建容器；覆盖可信op的原缺陷已无法复现。
- 实际bash创建FIFO后read在0.375秒返回regular_single_link_file_under_10MiB_required，没有等30秒。随后同run读取normal.txt仍为retained，工作区未销毁。先open阻塞缺陷已修复。
- 实际bash派生sleep并wait后取消operate任务；随后inspect本任务容器不存在，证明该取消路径销毁容器及内部派生进程。close_run后该run锁也移除。
- 启动取消：包装command只在真实docker run成功返回后暂停，等待启动通知后取消world任务；随后真实inspect容器不存在。此探针覆盖“实际启动完成但调用尚未返回”的窗口，不覆盖Docker服务本身卡死/响应不确定等所有竞态。
- 本轮独立容器全部已销毁，没有删除新旧镜像或其他容器。

复查未新增已确认缺陷；未完成项仍以初轮范围清单为准。没有执行真实双账号下载和专业业务内容验收。


## 真实Office内容独立核对

直接用zipfile和XML解析实际下载的证据时间线.xlsx、补证清单.docx，对照run_execution_acceptance.py的MATERIAL，没有执行文件内指令，也没有以模型自述代替检查。

两份内容均保留10月1、7、8、9日顺序及封签ZX-EXEC-4816；20箱运输与3箱外包装受潮没有扩写为内部已受损。甲方完成检验与乙方收到请求均保留邮件陈述主体，注明未附报告、未有发送记录/回执。两份均明确不同事件日期不同不当然冲突；发送人、发送日期未知，未填造提单号、适用法、金额、责任或受潮原因。补证建议与已知事实分开，草稿标识保留。

可改进点：另列“实际送达/收到日期未知”与10月9日单方陈述相邻，虽写明区别，仍易被理解为第二次收件；宜明确同一事件的陈述与未证事实。来源定位用“第N段”，fixture实际为逐行文字，建议定位单位统一为行。此次核对只评价合成样例的事实忠实与可核对性，没有进行专业法律质量认证或Office视觉渲染验收。

## 独立正式执行恢复回归

新增backend/tests/independent/test_execution_recovery.py，显式ZHOUXU_E2E_DOCKER=1启用。使用真实Harness、RegisteredAction及Docker执行工具，只有模型决策采用ScriptedModel，不调用真实模型。

任务序列为write错误Python→bash实际RuntimeError/nonzero→read返回真实sha256→edit用该sha256修复→bash运行并重新读取断言→present保存真实文件。另以新ExecutionStore实例重读快照字节，并inspect确认Harness终结后自己的容器不存在。负向回归纳入read/op注入和真实FIFO非阻塞拒绝、保留同run普通文件。

首次测试严格断言六次工具操作时遇到Harness材料staging额外写inputs/index.json而失败；不是执行恢复失败。测试明确区分该固定系统索引写入，继续要求六步真实任务顺序，没有删除真实报错、修复、字节和容器销毁断言。

最终执行：上述独立正式测试2 passed in 8.52s；自己的容器全部finally清理。


## 最后增量只读复核

阅读最终runtime.validate_artifact、stage_materials、ExecutionStore配额以及task_file下载路由。未新增可复现严重缺陷，也未把这次只读检查描述为已执行的新安全测试。

- 材料索引保留实际parser unit_id与locator；明确解析文本与原件/全文不同。索引不伪造自拆行号为原文段号，改善前述定位单位问题。历史下载文件的段号不会因此自动更正，应按对应运行版本理解。
- 发布端检查非空、OOXML展开尺寸及成员数量、重复/路径异常、.bin、必需parts和XML根，之后才保存快照。10份/30MiB配额在实际保存事务中按run统计。它仍是有界格式检查，不是Office完整兼容性、无外链、无公式风险或业务内容认证。
- 下载端仍先验证可信run账户及conversation，再取该run的artifact快照；文件名UTF-8百分号编码，attachment/octet-stream/nosniff/no-store保持。没有从请求参数拼接宿主文件路径。
- 独立恢复回归只排除固定系统写inputs/index.json，错误脚本、真实报错、read hash、edit修复、真实成功、present和终结销毁断言仍在。

本轮未重新执行双账号下载、资源压力及恢复探针。前端draftTarget修复未在此独立复核，不把主线浏览器成功转述成独立执行。整体安全和专业质量结论仍受前文明确范围限制。


## 聊天/工作权限分叉与重复失败终止独立验证

新增正式独立回归后，以ZHOUXU_E2E_DOCKER=1执行test_execution_recovery.py：6 passed in 12.99s。保留前述真实错误恢复、op注入与FIFO测试。

- 真实Harness的聊天请求中，ScriptedModel故意请求隐藏write工具；运行failed，没有调用runtime、创建容器或写快照。源码同时在Tool.execute_context按可信request.context.user_facts检查work，前端工具隐藏不是唯一保护。
- 真实Scheduler挂起运行并调用steer，分别尝试chat→work及work→chat，查询同一run仍保持原模式。测试执行了两个方向，而非只检查客户端按钮。HTTP resume源码只扩展原payload的材料范围，保留原facts；本轮未单独执行HTTP steer/resume探针。
- 真实Harness+Docker连续执行五个略有不同命令（避免工具去重提前截断），每次真实Python抛出同末行IndexError。第二次回执带repair_required，第五次触发execution_repeated_failure，Harness failed且模型后续声称完成的答案未执行；无交付快照，终结后容器销毁。错误次数统计真实退出结果，非替身伪造。
- 有效work恢复测试仍通过，证明不是把所有执行一律关闭来满足拒绝测试。

未发现本次分叉可复现的权限升级。工具执行上下文来自服务端value.facts，不能把任意材料中的task_mode文字视为授权。本轮没有重新执行前端模式切换或真实模型业务质量评估；不以六项通过覆盖先前真实模型round_limit失败，后者仍属业务执行失败记录。
