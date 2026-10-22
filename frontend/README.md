# 舟序前端

基于开源前端模板二次设计的舟序界面，使用 React、Vite、TypeScript、Tailwind CSS、Radix UI 和 Framer Motion。包含落地页、登录注册及对话优先的研究工作台，对接原有 FastAPI 后端。

## 舟序本地运行

需要 Node.js 20.19+ 或 22.12+、npm，以及运行在 `http://127.0.0.1:8010` 的舟序后端。

```powershell
npm ci
Copy-Item .env.example .env
npm run dev -- --port 4173
```

打开 `http://127.0.0.1:4173`。前端通过同域 `/api` 代理访问后端；可在本地 `.env` 中设置 `ZHOUXU_API_TARGET`。不要在任何 `VITE_` 配置中存放密钥。

## 验证与构建

```powershell
npm test
npx tsc -p tsconfig.app.json --noEmit
npm run build
npm run preview -- --port 4173
```

生产构建输出为 `dist/`。正式托管需要支持 SPA 路由回退，将 `/api` 转发到后端，并保留流式响应；Vite 的开发和预览代理不能替代生产反向代理。

工作台需要登录，账户和案件资料的授权由后端校验。八项任务提示只填入可编辑草稿，不表示八项业务能力均已独立实现。

## 维护资料

- [设计规范](DESIGN.md)
- [产品边界](PRODUCT.md)
- [登录注册对接](docs/auth-integration.md)
- [工作台接口、状态与验证](docs/workspace-integration.md)
- [本地字体及许可说明](public/fonts/FONT-NOTICE.md)

不提交 `.env`、运行数据、用户材料、密钥、依赖目录、构建产物或 `.ai-tmp/` 过程文件。界面与协议测试不等同于真实法律答案质量通过专业验收。

## 原始模板说明

以下为上游 Lovable 模板自带说明，项目占位链接并非舟序部署地址；舟序运行与部署以本文上方说明为准。

## Project info

**URL**: https://lovable.dev/projects/REPLACE_WITH_PROJECT_ID

## How can I edit this code?

There are several ways of editing your application.

**Use Lovable**

Simply visit the [Lovable Project](https://lovable.dev/projects/REPLACE_WITH_PROJECT_ID) and start prompting.

Changes made via Lovable will be committed automatically to this repo.

**Use your preferred IDE**

If you want to work locally using your own IDE, you can clone this repo and push changes. Pushed changes will also be reflected in Lovable.

The only requirement is having Node.js & npm installed - [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating)

Follow these steps:

```sh
# Step 1: Clone the repository using the project's Git URL.
git clone <YOUR_GIT_URL>

# Step 2: Navigate to the project directory.
cd <YOUR_PROJECT_NAME>

# Step 3: Install the necessary dependencies.
npm i

# Step 4: Start the development server with auto-reloading and an instant preview.
npm run dev
```

**Edit a file directly in GitHub**

- Navigate to the desired file(s).
- Click the "Edit" button (pencil icon) at the top right of the file view.
- Make your changes and commit the changes.

**Use GitHub Codespaces**

- Navigate to the main page of your repository.
- Click on the "Code" button (green button) near the top right.
- Select the "Codespaces" tab.
- Click on "New codespace" to launch a new Codespace environment.
- Edit files directly within the Codespace and commit and push your changes once you're done.

## What technologies are used for this project?

This project is built with:

- Vite
- TypeScript
- React
- shadcn-ui
- Tailwind CSS

## How can I deploy this project?

Simply open [Lovable](https://lovable.dev/projects/REPLACE_WITH_PROJECT_ID) and click on Share -> Publish.

## Can I connect a custom domain to my Lovable project?

Yes, you can!

To connect a domain, navigate to Project > Settings > Domains and click Connect Domain.

Read more here: [Setting up a custom domain](https://docs.lovable.dev/features/custom-domain#custom-domain)
