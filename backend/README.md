# 舟序后端

FastAPI 服务负责账户、历史聊天、附件与个人资源库、材料解析和 OCR、任务相关检索、ACT 工具调用以及流式回答。

## 安装与启动

```powershell
uv sync --locked --group dev --group media --group perception
Copy-Item .env.example .env
# 在 .env 中填写 DEEPSEEK_API_KEY
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
```

启动后访问 `/health` 检查服务，访问 `/docs` 查看接口。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `app.py` | API、鉴权、持久化、SSE 流式编排与模型调用 |
| `materials.py` | 常用办公文件、邮件、图片与 OCR 解析 |
| `perception_layer/` | 结构化分块、检索规划、混合召回、上下文预算与 trace |
| `resource_library/` | 个人资源上传、索引、选择与删除 |
| `act_runtime/` | 模型工具决策、注册工具、预算与隔离执行 |

## 配置

`backend/.env` 只读取 `DEEPSEEK_API_KEY`。其他选项通过启动进程环境变量设置：

| 变量 | 默认 / 用途 |
| --- | --- |
| `ZHOUXU_MODEL` | `deepseek-v4-pro`，最终回答 |
| `ZHOUXU_ACT_MODEL` | `deepseek-flash`，工具决策 |
| `ZHOUXU_PLANNER_MODEL` | `deepseek-flash`，检索规划 |
| `ZHOUXU_MODEL_ENDPOINT` | DeepSeek 兼容聊天接口 |
| `ZHOUXU_MODEL_CACHE` | 本地语义与 OCR 模型缓存目录 |
| `ZHOUXU_SEMANTIC_SEARCH` | `1`；设为 `0` 关闭语义召回 |
| `ZHOUXU_SEARCH_ENDPOINT` | 可选 SearXNG 地址；未配置则不提供联网检索 |
| `ZHOUXU_CODE_IMAGE` | `zhouxu-code-runtime:0.1` |
| `ZHOUXU_ACT_MAX_ROUNDS` | `4` |
| `ZHOUXU_ACT_MAX_TOOL_CALLS` | `6` |
| `ZHOUXU_CONTEXT_LIMIT` | `64000` Token 上下文预算 |
| `ZHOUXU_EVIDENCE_BUDGET` | `30000` Token 证据预算 |

代码工具需要 Docker。构建本地运行镜像：

```powershell
& .\act_runtime\docker\build.ps1
```

联网检索是可选能力，可通过 `scripts/start-search.ps1` 启动本地 SearXNG。其本地密钥和运行数据不得提交。

## 检查与边界

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

这些测试覆盖工程合同、历史上下文、感知规划与工具边界，不等同于海事法律答案通过专业验收。真实用户资料、账户库、检索索引、模型 trace 与运行日志写入受忽略目录，不能进入公开仓库。

