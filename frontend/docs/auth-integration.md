# 舟序账户入口

落地页导航的「登录」进入 `/login`；`/register` 创建账户。认证成功进入 `/workspace`，已有有效会话访问登录页也会进入工作台。`/account` 保留真实账户信息和撤销会话功能；落地页已登录入口为「工作台」。工作台的使用与接口边界见 [workspace-integration.md](./workspace-integration.md)。

## 本地运行

在 `D:\projects\codex\OPC\海事Agent项目\backend` 按原有环境启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
```

在本前端目录运行 `npm run dev`，或 `npm run build` 后运行 `npm run preview -- --host 127.0.0.1 --port 4173`。这两种模式都把 `/api/*` 转发至本机 `8010`，去掉 `/api` 前缀。无须修改原后端或复制原前端。

`.env.example` 仅包含公开连接配置；需要自定义时复制为 `.env.local`。`ZHOUXU_API_TARGET` 只供 Vite 代理使用，不会暴露到客户端。可用 `VITE_API_BASE_URL` 指定另一公开 API 地址，但跨域访问须由后端允许对应来源。不要把密钥放入 `VITE_` 变量。

正式托管时需配置 SPA 路由回退，并将同域 `/api/*` 反向代理到后端；Vite 的开发/预览代理不是生产网关。外网部署必须使用 HTTPS。

## 现有后端合同

| 动作 | 请求 | 结果 |
| --- | --- | --- |
| 注册 | `POST /auth/register`，`email/password/display_name` | 201，账户与 bearer token；密码至少 12 个 Unicode 字符；称呼选填 |
| 登录 | `POST /auth/login`，`email/password` | 200，账户与 bearer token |
| 确认会话 | `GET /projects`，bearer token | 已有只读受保护接口；仅确认权限，不保存或展示返回的项目资料 |
| 退出 | `POST /auth/logout`，bearer token | 204；失效会话的 401 也清理本地状态 |

后端没有验证码、OAuth、密码重置或 `/me` 接口，所以没有制作对应入口。错误按实际后端错误码转换为中文；网络失败保留输入供重试，退出失败不会谎报已撤销远端会话。

会话只保存在当前标签页的 `sessionStorage`，不存密码、不写 `localStorage`、不打印认证头。页面重新进入时再次通过后端确认会话，401 清除失效状态。浏览器禁用存储时退回当前页面内存，刷新后需重新登录。后端仍是实际授权边界；本地账户字段只用于显示，不可用于授权。未来若后端引入 HttpOnly Cookie，应同步升级存储策略，而不是在前端模拟 Cookie 安全性。

## 视觉来源

沿用已确认的舟序字体、深色背景、灰蓝强调与中文排版，保留中央表单、左上品牌字标、右上返回入口和底部品牌短句。全屏背景使用用户提供的 React Bits Pro [Hexagonal Vault](https://framesbase.app/reactbits-pro/backgrounds/hexagonal-vault) 授权源码，保留六边形网格、自动游走及鼠标跟随柔光，适配为灰蓝配色，关闭点击脉冲。组件位于 `src/components/react-bits/hexagonal-vault.tsx`，通过 `AuthVaultBackground` 延迟加载，Three.js 不进入落地页的初始包；Fiber 使用兼容 React 18 的 v8。

背景以被动的 window 指针监听接收位置，不拦截表单点击；像素比上限 1.25，开启减少动态效果时按需渲染静态网格与柔光，标签页隐藏时停止持续绘制。WebGL 不可用或组件加载失败时使用本地 SVG 静态网格，账户操作不依赖背景加载。卸载时清理事件、观察器与 GPU 渲染目标。中央模块继续使用轻量 CSS 一次性淡入，减少动态效果时关闭入场动画；登录注册接口、会话存储及落地页素材均未改变。
