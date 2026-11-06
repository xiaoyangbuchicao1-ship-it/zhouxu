# 执行能力首轮独立审查计划

2026-11-05。当前只完成架构材料审阅，未审实施代码，未执行容器探针；以下是待验证条件，不是安全认证。

## 借鉴与不直接照搬

- sandbox-local：执行器不可用必须失败关闭。Windows ACL只承诺部分写侧限制，不能当作网络和读取隔离。
- fs：工具接口、提供方和策略分层；read/write/edit/bash必须进入同一执行世界。fs-observation-policy的先读再改和版本防护能避免丢失并发修改，但不是任意bash写入的权限边界。
- tool-bash：普通非零退出应给模型真实退出码；超时、输出截断、取消和派生进程必须有确定状态。原版可超时转后台，首轮若不提供后台管理则不能照搬继续运行行为。
- tool-present：普通文件与归属检查有用。原版仅登记路径、不复制字节且可允许工作区外路径；舟序需收紧为任务内文件快照与账号鉴权，不能直接移植这两项默认语义。

## 必须执行的负向场景

| 边界 | 探针 | 必须观察到 |
|---|---|---|
| 可信身份 | 工具参数伪造run_id/account_id、API枚举其他用户run或artifact | 不能选择执行归属；另一账号及匿名下载拒绝 |
| 无宿主暴露 | inspect本轮容器，尝试读宿主.env、Docker socket、其他任务目录 | 无宿主挂载、无socket、无凭证环境；不能读取其他工作区 |
| 路径 | read/write/edit/present传../、绝对路径、符号链接链、目录、FIFO、设备 | 不越过任务根；特殊文件不能阻塞或被交付 |
| bash同权 | 用bash创建指向根外的软链，再经文件工具读写；通过Python绕过shell写系统路径 | 不得因绕过工具路径校验而获得宿主权限；rootfs写拒绝 |
| 网络 | DNS、外部IP、host.docker.internal、宿主网关请求 | 不可联网，不仅仅在工具schema中禁止 |
| 权限 | id、capabilities、尝试提权及修改rootfs | 非root、cap-drop、no-new-privileges真实生效 |
| 资源 | fork、内存分配、磁盘写满、无限stdout、无限循环及子进程 | PID/内存/磁盘/时间/输出限额有效；宿主内存不因capture无限增长 |
| 超时取消 | 前台脚本派生sleep/后台写文件后超时或用户停止 | 派生进程也停止；后续不再偷偷改文件；UI如实失败/停止 |
| 生命周期 | 两次工具调用、两个run、后端重启、Docker不可用 | 同run状态连续；不同run隔离；恢复语义明确；不可用不宿主回退 |
| 编辑正确性 | 不存在/重复old_text、旧内容已变、新建覆盖冲突 | 可操作错误，无静默多处替换或丢失修改 |
| 交付快照 | present后修改/删除源文件，重复文件名、多文件、下载重启 | 已交付字节和hash固定；文件确实可下载且重启可读 |
| 真实业务 | 实际材料→代码→运行→验证→Excel/Word文件 | 文件真实可打开、行数/金额/日期符合材料，不能仅检查扩展名或模型自述 |

## 首轮设计判定

建议把每轮容器、工作区与快照资源完全由后端创建和定位；模型只给相对路径和操作内容，不给Docker参数、宿主路径、用户ID或权限升级选项。Docker客户端本身拥有强宿主权限，因此所有argv必须服务端构造且不经过宿主shell解释。应限制并发容器数、单任务总预算、工作区尺寸、文件数与生命周期；仅CPU/内存/PID限制不能防磁盘耗尽和容器堆积。

文件发布须先在容器内有界读取普通文件，再保存服务端不可变快照，下载以可信run/account关系鉴权；不要docker cp解压不可信tar到宿主，也不要用未校验的Content-Disposition文件名。异步调用不得在事件循环中阻塞Docker进程。失败回执不能泄露宿主stderr中的凭证/路径。

以上边界测试通过也只证明被测条件；容器共享宿主内核，不应表述为抵御所有恶意代码的绝对安全沙箱。

## 阅读来源

本地harness/packages/sandbox/sandbox-local/README.zh.md、fs/README.zh.md、fs/fs-observation-policy/README.zh.md、shell/tool-bash/README.zh.md、deliverables/README.zh.md、deliverables/tool-present/README.zh.md。未读取凭证、未运行上游脚本或操作任何容器。
