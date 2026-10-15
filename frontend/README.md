# 舟序前端

当前网页围绕一个连续工作区组织：用户可以直接交流，也可以上传附件或从个人资源库选择多份资料，随后继续追问、追加材料、打开历史或删除聊天。

## 启动

先按根目录说明启动 `backend/`，然后执行：

```powershell
npm ci
npm run dev -- --port 4173
```

默认通过同源网关访问 `http://127.0.0.1:8010`。需要覆盖时，将 `.env.example` 复制为 `.env.local` 并设置其中的地址。

## 页面与组件

- `app/page.tsx`：产品介绍页
- `app/login/page.tsx`：登录与注册
- `app/app/page.tsx`：聊天、历史和资源库工作区
- `components/conversation-workspace.tsx`：核心交互与流式状态
- `components/message-markdown.tsx`：Markdown、表格、代码与引用展示
- `app/tokens.css`：当前视觉变量

界面继续使用青瓷、银白与深墨色体系，并以内容阅读和操作反馈为优先。修改前先读 [UI/UX 第一性原理](../docs/UI_UX第一性原理.md)。

## 检查

```powershell
npm run typecheck
npm run build
```

MiSans 字体及其许可文件位于 `public/fonts/`；舟序图形标记位于 `public/zhou-xu-mark.png`，两者都是当前界面的正式资源。

