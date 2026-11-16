# 舟序 Compose 部署

这套 Compose 把应用拆成三个独立容器：

```text
zhouxu-web  (Nginx + 前端静态文件)
      -> /api
zhouxu-api  (FastAPI + CPU PyTorch)
      -> https://opensearch:9200
opensearch  (法条索引 + 案例索引)
```

这样拆分的关键原因是 OpenSearch 的数据必须独立保存。更新前端或后端镜像时，只替换应用镜像，不会覆盖检索数据。法条库和案例库仍然是两个逻辑库、两个索引/别名，但由同一个 OpenSearch 容器承载。

## 给另一台 Windows 电脑什么

### 方案 A：带源码构建（第一次迁移推荐）

复制以下内容；不需要复制任何 `.venv-*`、`node_modules-*` 或 Windows/WSL 的安装目录：

```text
deploy/app/                  compose.yaml、Dockerfile、nginx.conf、.env.example
backend/                     源码、pyproject.toml、uv.lock
frontend/                    源码、package.json、package-lock.json
start/common/                ensure_indexes.py 等启动检查代码
data/                        账户、材料、案例快照和运行数据
opensearch/corpus/           法条语料清单与正文
opensearch/import/           法条向量和导入清单
opensearch/opensearch_delivery/  索引定义、导入脚本、查询
```

如果要保留现有 OpenSearch 的快速运行快照，再复制：

```text
opensearch/vector_db_data/opensearch/
```

复制运行快照时，目标机的 `OPENSEARCH_INITIAL_ADMIN_PASSWORD` 必须使用创建该快照时的同一密码；OpenSearch 只在第一次初始化数据目录时读取这个变量。若原密码不确定，省略运行快照，让 `zhouxu-init` 从语料和案例快照重新导入，并在目标机设置一个新密码。

没有这个目录也可以启动；`zhouxu-init` 会在 OpenSearch 就绪后根据 `corpus/`、`import/` 和 `data/cases/` 导入两个逻辑库。第一次导入会花时间，但不会静默把错误当成空库。

### 方案 B：使用已发布镜像

另一台电脑只需要：

```text
deploy/app/compose.yaml
deploy/app/.env                  通过安全方式填写，不要从聊天中复制密钥
data/
opensearch/corpus/
opensearch/import/
opensearch/opensearch_delivery/
```

若已经把 OpenSearch 数据目录作为迁移快照发布，也复制 `opensearch/vector_db_data/opensearch/`。此方案不需要 `backend/`、`frontend/` 和本机 Python/Node；Compose 会拉取已经构建好的 `zhouxu-api`、`zhouxu-web` 镜像。

## 另一台 Windows 的真正启动步骤

先安装 Docker Desktop，打开 WSL2 后端，然后在项目根目录执行。第一次用源码构建：

```powershell
Copy-Item deploy\app\.env.example deploy\app\.env
notepad deploy\app\.env
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml config --quiet
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d --build
```

`.env` 至少要设置一个真实的 `OPENSEARCH_INITIAL_ADMIN_PASSWORD` 和后端所需的 `DEEPSEEK_API_KEY`。不要把 `.env` 上传 GitHub。

说明：这套镜像自带 CPU PyTorch，但没有把约 6.8GB 的 BGE-M3 权重打进镜像。网页、健康检查、索引初始化可以先完成；真正执行向量检索前，还要为 API 容器提供与配置中 revision 一致的本地模型。模型可以后续选择“宿主机只读挂载”“打进发布镜像”或“首次联网下载”，当前 Compose 尚未默认启用这三种方式中的任何一种。

启动完成后访问：

```text
http://127.0.0.1:8080/
```

如果目标电脑的 `8080` 已被 Apache 或其他程序占用，把 `.env` 中的 `ZHOUXU_WEB_PORT` 改成空闲端口（例如 `18080`），再访问 `http://127.0.0.1:18080/`。

也可以使用项目内的 Windows Compose 一键入口（它不要求本机安装 Python、Node 或 uv）：

```powershell
.\start\windows\start-compose.cmd
```

停止并保留数据：

```powershell
.\start\windows\stop-compose.cmd
```

`start\windows\start.cmd` 仍然保留给本机原生开发模式；交付给只安装 Docker Desktop 的 Windows 电脑时，请使用上面的 `start-compose.cmd`。

查看状态与初始化结果：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml ps -a
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml logs zhouxu-init
```

使用镜像仓库时，把 `.env` 中的 `ZHOUXU_API_IMAGE`、`ZHOUXU_WEB_IMAGE` 换成完整镜像地址，然后执行：

```powershell
docker login <你的镜像仓库>
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml pull
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d
```

停止服务但保留数据：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml down
```

不要使用 `docker compose down -v`，也不要手动删除 `opensearch/vector_db_data/opensearch/`；那会删除检索数据。

## Linux/ECS

Linux 服务器走同一份 Compose 文件，不安装项目 Python 和 Node 依赖。把项目放到例如 `/srv/zhouxu`，准备 `deploy/app/.env`，然后：

```bash
cd /srv/zhouxu
chmod +x start/linux/*.sh
./start/linux/start.sh
```

该脚本只调用 Docker Compose；它不会创建 `.venv-linux`。若服务器使用镜像仓库，直接按上面的 `pull` 与 `up -d` 命令运行即可。Linux bind mount 的 OpenSearch 数据目录需要让容器用户可写，例如首次迁移后执行 `chown -R 1000:1000 opensearch/vector_db_data/opensearch`。

`ZHOUXU_WEB_PORT` 默认是 `127.0.0.1:8080:80`。ECS 对外开放的是 Nginx/FRP 的公网端口，不要把 OpenSearch `9200` 发布给公网。
