# 舟序

舟序是面向海事争议资料阅读与持续对话的本地开发产品。用户可以直接交流，也可以上传附件或从个人资源库选择多份资料；系统保留历史上下文，按当前问题组织证据，并以流式方式返回回答。

本仓库是团队接手所需的精简基线，只保留当前产品及其维护资料：

| 目录 | 内容 |
| --- | --- |
| `frontend/` | Vite / React 前端：介绍页、登录注册、工作台、历史与资源库 |
| `backend/` | FastAPI 后端：账户、材料解析、OCR、检索、ACT 工具编排与流式回答 |
| `docs/` | 团队交接说明和 UI/UX 第一性原理 |

旧版前端、停用后端、设计过程稿、调试日志、模型 trace、聊天数据库和用户原件均未纳入公开仓库。

## 本地启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 20.9+ 与 npm。

```powershell
cd backend
uv sync --locked --group dev --group media --group perception
Copy-Item .env.example .env
# 在 backend/.env 中填写 DEEPSEEK_API_KEY
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
```

另开一个终端：

```powershell
cd frontend
npm ci
npm run dev -- --port 4173
```

访问 `http://127.0.0.1:4173`。后端接口文档位于 `http://127.0.0.1:8010/docs`。

## 当前处理链路

1. 保存用户输入、附件和已选择的资源。
2. 保留文档标题、条款、表格与邮件结构进行解析和分块。
3. 由模型判断本轮是否需要检索或调用工具；程序只负责权限、预算、超时与执行边界。
4. 从当前资料、历史对话与资源库中组织有限证据上下文。
5. 由回答模型生成并流式返回结果，同时保留可追溯的操作状态。

材料不足时，系统应先回答现有材料能够支持的结论与条件，再说明不确定项，不以反复索取材料代替分析。

## 检查

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q

cd ..\frontend
npx tsc -p tsconfig.app.json --noEmit
npm run build
```

详细配置、私密数据恢复边界与已知限制见 [团队交接说明](docs/团队交接.md)。界面修改必须遵循 [UI/UX 第一性原理](docs/UI_UX第一性原理.md)。

