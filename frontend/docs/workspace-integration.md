# 舟序研究工作台

## 启动与入口

沿用 [账户入口说明](./auth-integration.md) 的本地启动方式。后端仍运行在 8010，前端开发和预览通过同域 `/api` 代理访问。没有修改后端源码、表结构或原前端。

- `/workspace`：新对话。
- `/workspace/chat/:id`：已保存的对话；支持刷新恢复。
- `/workspace/library`：当前账户的资料库。
- 未登录和失效会话均回到登录页。所有读写由后端 bearer 权限校验，前端显示的账户字段不参与授权。

工作台按路由延迟加载；Markdown 使用 `react-markdown` 与 `remark-gfm`，不解析原始 HTML，外链使用安全新窗口属性，外部图片不自动加载。

## 现有接口的前端映射

| 界面操作 | 后端接口 |
| --- | --- |
| 历史、详情、新对话、删除 | `GET/POST /conversations`，`GET/DELETE /conversations/:id` |
| 置顶、取消置顶 | `PUT/DELETE /conversations/:id/pin` |
| 项目列表、创建、删除 | `GET/POST /projects`，`DELETE /projects/:id` |
| 对话归组、移出分组 | `PUT/DELETE /projects/:projectId/conversations/:chatId` |
| 本轮附件上传、移除 | `POST /conversations/:id/materials?filename=...`，`DELETE /conversations/:id/materials/:materialId` |
| 个人资料库上传、列表、删除 | `GET/POST /library/resources`，`DELETE /library/resources/:id` |
| 查看已读取正文 | `GET /library/resources/:id/chunks` |
| 对话选择资料、取消选择 | `PUT/DELETE /conversations/:id/resources/:resourceId` |
| 提问和真实流式回答 | `POST /conversations/:id/messages/stream` |

文件以原始二进制请求体上传，不改为 multipart；单份不超过 30 MB。上传读取结果和警告以服务实际返回为准。不支持的文件不会伪装为已成功提取。

## 保存、错误与数据边界

- 流式协议实际是 NDJSON，不是 SSE。客户端增量解码 UTF-8 和跨块行，仅 `done` 事件证明本轮消息已保存。
- 网络断开、服务错误或「停止接收」均保留草稿与已收到的未完成内容，要求重新载入核对。没有自动重试消息 POST，避免不确定状态下重复发送；「停止接收」不宣称取消后台任务。
- 用户向上阅读时暂停跟随滚动，并提供「回到最新」。正常逐流显示不额外制造打字延迟。
- 草稿与待重试文件保存在当前工作台内存，不把研究材料、消息或文件写进浏览器持久存储。离开工作台/刷新前应留意尚未发送内容；已保存历史从后端恢复。
- 会话附件只属于该对话；已发送附件不能从输入区移除。账户资料库需要明确选入某个对话；已选择不等于已经被回答引用。
- 删除项目只解除分组，不删除对话。删除对话与删除账户资料都有明确的不可恢复确认说明。
- 会话退出会清理当前账户的查询缓存、取消连接并移除 sessionStorage 中的会话。失效的会话不继续展示工作台。

八项任务提示只填入可编辑的问题草稿，不是八套独立工具，更不依据前端关键词固定路由后端能力。不存在的协作、提醒、Word 编辑或下载功能没有入口。

## 视觉与动效参考

遵循已认可的深色、灰蓝强调、三种本地字体角色。登录页 Hexagonal Vault 不进入工作阅读区。工作台使用实色阅读面与自然 Markdown，不新增落地页图片或视频。

参考 Framesbase 的 [App Sidebar 1](https://framesbase.app/reactbits-pro/app-ui/app-sidebar/app-sidebar-1)、[App Dialog 6](https://framesbase.app/reactbits-pro/app-ui/app-dialog/app-dialog-6)、[AI Chat 2](https://framesbase.app/reactbits-pro/app-ui/ai-chat/ai-chat-2) 的空间切换、触发点菜单、资料侧栏和滚动反馈思路；实现接入现有 Framer Motion 与 Radix，不保留演示账户、静态假数据或虚拟工具状态。

## 本次验证

- `npm test`：原 12 项 + 新增 10 项协议测试。覆盖跨字节中文解码、NDJSON 完成/中断、原始文件上传、文件大小、204、401、取消和不自动重试写操作。
- `npx tsc -p tsconfig.app.json --noEmit`、修改文件的 ESLint、生产构建。
- Chromium 操作真实后端工厂的隔离数据目录，使用明确标记的固定合成回答，不调用付费模型、不创建真实业务记录。验证注册跳转、项目、资料上传及正文、关联、本轮附件、流式完成、历史刷新、置顶、阅读滚动保护、流式失败恢复、项目删除保留对话、退出和 401。
- 桌面 1440×900，手机 390×844 和 320px；测试导航焦点圈定、Escape 与焦点恢复、减少动态效果模式。

隔离固定回答只证明接口与界面闭环，不能证明真实模型的法律判断、联网检索质量、所有文件解析器或生产部署环境。构建仍提示原有大体积页面/三维背景包；工作台已单独拆包，未擅改已确认页面的加载架构。
