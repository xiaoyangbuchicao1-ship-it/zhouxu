# 可执行海事办公：首个真实闭环

2026-11-05。本地 http://127.0.0.1:4185/workspace 。首轮按用户选择验收证据整理与Excel/Word交付。

## 实际能力

`上传授权材料 → 解析文本及真实定位索引 → read/write/edit/bash → 实际运行Python → 重新打开检查 → present快照 → 账户鉴权下载`

顶部按用户参考增加“聊天 / 工作”。默认聊天，只保留问答、材料阅读和检索等业务能力，不暴露执行工具或创建容器。工作模式才按下一次请求开放五个执行工具；服务端验证task_mode并保存，运行中追加指令不能更改原任务权限，重试保留原模式。

沿用舟序界面。文件在回答下显示下载卡片，桌面与390px手机均实际操作。Office文件可从当前用户的交付快照打开在线预览：容器内 LibreOffice 生成 PDF 后再由同一隔离容器栅格化为分页图片 HTML，浏览器不接触宿主路径；预览最多显示前8页，完整内容仍以原始下载文件为准。不是返回一段代码让用户自己运行，也不是仅把Markdown另存为Word扩展名。例题：切换工作模式，选择证据整理，上传材料，输入“整理事件时间线和补证清单，生成并检查Excel与Word草稿”。只问一个常识问题不要求启动执行环境。

业务检索、原文阅读、精确金额计算和结构化工作包仍各司其职。执行工具没有扩大案件成员、原语料写入、对外发送或社区发布权限。首轮容器提供Python、openpyxl、python-docx；无网络，不能在任务里安装依赖。

## 工具为什么这样设计

参考本地DeepSeek Harness的fs/shell/sandbox/deliverables约定，把模型工具接口和执行后端分开。read可列目录或读UTF-8；write创建文件，覆盖需当前哈希；edit要求旧文本唯一匹配及当前哈希；bash负责通用运行；present负责把已有文件变成正式交付。业务工具保留专业来源与计算边界。

材料由服务端按可信run范围导入，inputs/index.json含名称、解析状态、文本哈希与真实unit locator。超180KB的单份文本不悄悄截断，而提示继续用read_material。解析文本不代表原件，自己拆分的行号不能冒充原文页码。模型看不到宿主资料存储路径。

## 执行边界与寿命

独立Linux容器、固定镜像ID、无宿主bind mount、无网络、非root、只读根、cap-drop ALL、no-new-privileges；1CPU、512MiB内存且无额外swap、64PID、64MiB工作区和32MiB临时区。每条命令600秒，stdout/stderr分别保留最多64KiB；服务内最多4个活跃执行世界。达到限额或环境缺失明确失败，不回退宿主执行。

read/write/edit在同一容器执行，不存在一条工具绕回宿主文件系统。拒绝父路径、绝对路径、符号链接；非阻塞打开后检查普通单链接文件。任务结束或取消销毁容器及后代；等待补充输入时暂保留内存工作区。服务重启后未交付脚本不保证恢复，超过容器寿命的工作区需从授权输入重建。正式present快照保存在backend/.runtime/execution/files.sqlite3，与临时容器分离，重启后仍可按原run鉴权下载。

相同脚本错误第二次提示先读取实际脚本/数据再修复，第五次终止，不接受模型后续的完成声明。

单文件最多10MiB，每run最多10份/30MiB快照。Office文件检查ZIP展开限额、必需XML和根元素、拒绝bin活动部件；这些是有限结构检查，不是完整Office验证、无外链证明或专业内容认证。文件以attachment、nosniff、private no-store返回。

## 实际验收与负面发现

`work-mode-v2/` 是 2026-11-06 的真实模型/API/容器复查：模型读取授权材料，在 `zhouxu-task-runtime:0.2` 中生成、重新打开核对并 `present` 两个文件。下载 SHA256 与快照一致，另一账户返回 404、匿名请求返回 401。独立核对保留 1/7/8/9 日顺序、封签、单方陈述和未知金额/责任；检验完成、请求发出和请求收到仍按三个事件分别列示。它证明这一运行成功，不建立稳定率保证，也不替代专业人员复核。

- `work-mode-v2/live.json` 与同目录 Excel/Word：本次真实复查的报告与原始下载字节。
- `browser.png`、`browser-mobile.png`：同一工作模式的真实上传、生成、分页预览、下载与 390px 手机宽度检查，不是注入演示卡片；交付卡片显示真实文件类型、大小和 SHA256 前缀，下载中会锁定重复操作。
- `preview-desktop.png`、`preview-mobile.png`：同一交付快照的 Excel 在线预览桌面/手机截图；接口返回自包含分页 HTML，截图中可见两页真实表格内容，未再依赖浏览器 PDF 插件的空白渲染。
- 本次首次浏览器复跑因 Docker Linux 引擎未连接而失败；Playwright 重跑会清理失败目录，故仅保留任务本地的失败观察转录 `.ai-tmp/visual-refresh-20261106/docker-failure-observation.md`。恢复引擎后重跑为 1 passed（29.0s；前一轮为 31.5s），这次环境失败没有被改写成产品通过。
- `visual-v2/` 是可复用的确定性模板样例；镜像预装的 `zhouxu_office` 能力和证据整理 Skill 约定了同一套排版边界，真实复查文件的内容由模型从材料生成并完成渲染检查。
- independent-review.md：独立源码与Docker探针，保留read参数覆盖可信op、FIFO阻塞这两项原缺陷和修复复查；真实Harness中错误脚本→失败→read哈希→edit→重跑→present→销毁容器的六步回归。ScriptedModel仅用于可重复决策，Docker、执行、输出、错误与文件均真实。
- 当前合同复验（`.delivery/runs/`，2026-11-06，交付卡片增强后）：后端 437 passed / 12 skipped；前端 21 passed；TypeScript、生产构建通过；常规浏览器 4 passed / 4 skipped；隔离执行 8 passed。跳过项仍不计为通过。
- 正式测试：integration/test_task_execution.py、independent/test_execution_recovery.py、integration/test_execution_file_api.py、unit/test_execution_delivery.py。常规测试不偷偷启动 Docker，`acceptance.json` 单独将 8 项执行/权限隔离回归列为必须通过且不得跳过。
- 浏览器曾暴露上传创建会话时草稿输入跨路由丢失；已修复并保留task-flow单元回归。第一次浏览器脚本也有在空输入时等待发送按钮启用的顺序错误，修正后才继续检验实际产品行为；没有将那次脚本失败称为产品验收证据。

当前执行环境是进程/容器隔离，不宣称抵御所有共享内核漏洞。持续待办调度、跨会话工作区、任意插件安装、外发文书和真实客户案件专业评分仍不包含在结论内。Office 文件已完成本轮逐页渲染检查；这证明排版没有发现裁切、越界或表格断裂，不等于用户已对最终视觉风格作出认可。分页预览是核对辅助，不替代下载原件或专业复核。

## 复跑

先运行Docker Desktop Linux引擎：

```powershell
docker build -t zhouxu-task-runtime:0.2 backend/infrastructure/execution/image
backend/.venv/Scripts/python.exe ai-product-craft/skill/scripts/delivery.py run --contract acceptance.json --timeout 600
# 显式使用模型额度，生成合成材料的实际文件
backend/.venv/Scripts/python.exe backend/tests/scenarios/run_execution_acceptance.py --live --output docs/acceptance/execution-v1/new-run/live.json
$env:ZHOUXU_E2E_LIVE='1'
node frontend/node_modules/@playwright/test/cli.js test execution.spec.ts --config=frontend/playwright.config.ts
```

容器镜像由本项目Dockerfile构建，官方Python基础镜像按digest固定，依赖版本固定；没有改动现有OpenSearch或删除其他镜像/容器。原版参考仅研究接口与机制，没有执行其安装钩子或整体嵌入另一套系统。

官方机制资料：[Docker资源限制](https://docs.docker.com/engine/containers/resource_constraints/)、[none网络](https://docs.docker.com/engine/network/drivers/none/)。具体限制是否接线有效以本项目真实Docker探针为准。
