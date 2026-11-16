# 舟序启动入口

`start` 提供两条明确的路径：

- `start/windows/start.cmd`：本机 Windows 原生开发，使用 Windows 的 Python/Node 依赖。
- `start/windows/start-compose.cmd`：Windows + Docker Desktop 部署，不要求宿主机安装 Python/Node/uv。
- `start/linux/start.sh`：Linux、ECS 或 WSL 中的 Docker Compose 入口，不创建 `.venv-linux`。

两条路径不要同时启动同一份应用；否则会并发写 SQLite 和 RunStore。

## Windows 原生开发

在项目根目录运行：

```powershell
.\start\windows\start.cmd
```

它首次创建 `backend/.venv-windows` 和 `frontend/node_modules-windows`，连接本机开发 OpenSearch，并启动：

- 前端：`http://127.0.0.1:4173/`
- 后端：`http://127.0.0.1:8010/healthz`
- OpenSearch：`https://127.0.0.1:19200`

停止本机开发进程：

```powershell
.\start\windows\stop.ps1
```

`start.cmd` 会设置 UTF-8 代码页；PowerShell 脚本本身使用 ASCII 文本，兼容 Windows PowerShell 5.1，不依赖管理员权限。只有 Docker Desktop、uv、Node/npm 未安装或端口被占用时，才需要处理系统环境。

## Docker Compose（另一台 Windows、Linux、ECS）

先把 `deploy/app/.env.example` 复制为 `deploy/app/.env`，填写 OpenSearch 管理员密码和模型凭证。然后在项目根目录运行：

```bash
chmod +x start/linux/*.sh
./start/linux/start.sh
```

Windows Docker Desktop 直接运行同一份 Compose 命令：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d --build
```

交付给另一台 Windows 电脑时，推荐使用项目内的一键入口：

```powershell
.\start\windows\start-compose.cmd
```

停止并保留数据：

```powershell
.\start\windows\stop-compose.cmd
```

页面地址是 `http://127.0.0.1:8080/`。此路径不使用 `backend/.venv-linux`、`backend/.venv-windows` 或宿主机 `node_modules`；依赖都在镜像中。

停止而保留数据：

```bash
./start/linux/stop.sh
```

或：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml down
```

不要使用 `down -v`。OpenSearch 数据位于 `opensearch/vector_db_data/opensearch`，删除它会删除检索库。

更多迁移文件清单和镜像仓库用法见 [`deploy/app/README.md`](../deploy/app/README.md) 和 [部署合同](../docs/deployment.md)。
