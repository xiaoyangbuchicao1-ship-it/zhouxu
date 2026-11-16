# 舟序部署清单

更新时间：2026-11-16

## 一句话结论

源码只有一份；运行时只启动一套应用。Windows 原生用于本机开发，Docker Compose 用于另一台 Windows、Linux 和 ECS。

```text
Windows 原生开发：backend/.venv-windows + frontend/node_modules-windows
Docker Compose：zhouxu-web + zhouxu-api + opensearch
```

WSL2 是 Docker Desktop 的底层 Linux 环境，不是第三套舟序。当前 Linux 启动脚本只调用 Compose，不再创建 `backend/.venv-linux`。

## 当前正式目录

```text
真正的舟序/
├─ backend/                       FastAPI 源码、uv.lock
├─ frontend/                      React/Vite 源码、package-lock.json
├─ data/                          账户、材料、案例快照、SQLite（不进 Git）
├─ opensearch/
│  ├─ corpus/                     法条语料（数据包）
│  ├─ import/                     法条向量和清单（数据包）
│  ├─ vector_db_data/opensearch/  OpenSearch 运行快照（可选数据包）
│  └─ opensearch_delivery/        索引定义、导入脚本和查询
├─ deploy/app/
│  ├─ compose.yaml                三容器编排
│  ├─ Dockerfile                  前端构建 + 后端运行镜像
│  ├─ nginx.conf                  前端静态文件和 /api 代理
│  ├─ .env.example                部署配置模板
│  └─ README.md                   迁移与启动说明
├─ start/
│  ├─ windows/                    本机 Windows 原生开发入口
│  ├─ linux/                      Docker Compose 入口
│  └─ common/                     两端共用的索引就绪检查
└─ docs/                          架构和部署说明
```

`backend/.venv-windows`、`frontend/node_modules-windows` 是本机缓存，不是交付物。另一台电脑通过 Dockerfile 在 Linux 容器内重建依赖。

## 三个 Compose 服务

| 服务 | 内容 | 数据/网络边界 |
|---|---|---|
| `zhouxu-web` | Nginx + 前端静态文件 | 发布宿主机 `8080` |
| `zhouxu-api` | FastAPI + 文档工具 + CPU PyTorch | 只在 Compose 网络监听 `8010` |
| `opensearch` | OpenSearch 2.19 | 只在 Compose 网络监听 `9200`，数据 bind mount |

`zhouxu-init` 是一次性初始化任务，不是第四个长期服务：它等待 OpenSearch 健康，校验/导入法条和案例，成功后退出码为 0。

## 两个逻辑库，一个 OpenSearch

| 逻辑库 | 索引/别名 | 输入 |
|---|---|---|
| 法条库 | `maritime_law_chunks_v2` / `maritime_law_chunks_read` | `opensearch/corpus`、`opensearch/import` |
| 案例库 | 当前案例快照 ID / `maritime_cases_read` | `data/cases/<snapshot>/` |

它们不是两个容器，也不是一个索引混装。一个 OpenSearch 集群承载多个索引，数据目录独立于应用镜像。

## 另一台 Windows 的交付包

源码构建至少包含：

```text
deploy/app/
backend/（源码、pyproject.toml、uv.lock）
frontend/（源码、package.json、package-lock.json）
start/common/
data/
opensearch/corpus/
opensearch/import/
opensearch/opensearch_delivery/
```

已有索引快照可额外携带 `opensearch/vector_db_data/opensearch/`。不复制：

```text
backend/.venv-*
frontend/node_modules-*
deploy/app/.env（在目标机单独填写）
模型权重、日志和运行缓存
```

镜像仓库方案可以省略 backend/frontend/start 的源码，只保留 Compose、.env、数据包和索引发布资产。

## 真实启动命令

源码构建：

```powershell
Copy-Item deploy\app\.env.example deploy\app\.env
notepad deploy\app\.env
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml config --quiet
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d --build
```

另一台 Windows 电脑也可以直接运行项目内的一键入口（只要求 Docker Desktop）：

```powershell
.\start\windows\start-compose.cmd
```

镜像仓库：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml pull
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d
```

访问 `http://127.0.0.1:8080/`，查看：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml ps -a
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml logs zhouxu-init
```

停止时使用 `down`，不要使用 `down -v`。

## Windows 原生开发入口

本机 PowerShell 运行：

```powershell
Set-Location 'D:\projects\codex\论文库\真正的舟序'
.\start\windows\start.cmd
```

它使用 Windows 专属依赖并连接本机开发 OpenSearch。不要同时在 Windows 和 WSL 启动两套后端，否则会并发写 SQLite/RunStore。

## 端口

| 端口 | 来源 | 作用 |
|---:|---|---|
| `4173` | Windows 原生 Vite | 本机开发页面 |
| `8010` | Windows 原生 FastAPI | 本机开发 API |
| `127.0.0.1:8080` | Compose web 容器 | Docker 页面/本机反向代理 |
| `9200` | Compose 网络内部 | OpenSearch，不公网开放 |
| ECS 80/443 | 外部 Nginx/FRP/Cloudflare | 公网入口 |
