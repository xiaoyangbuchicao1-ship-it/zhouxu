# 技能工坊无法下滑修复

问题：工作台锁定 body 滚动，外层为固定视口；业务页高度自动增长且没有 overflow 滚动区域。超出屏幕的卡片被外层裁切。

修复：仅在共享的 `.maritime-desk` 上设置高度 100%、纵向自动滚动及滚动边界；对话和侧栏继续使用各自原有滚动区域。

真实 Chromium 回归先在 1366×620、390×650 都复现：滚轮向下 3000 后，最后一项“开始任务”的 viewport ratio 仍为 0。修复后验证上下滚动、底部操作可达、弹窗关闭后继续滚动、无横向溢出。

命令：`npx playwright test --grep-invert 'live model' --reporter=list --output=../.ai-tmp/office-scroll-fix-20261105/playwright`（在 frontend 执行）。结果：3 passed，包含原有基础业务旅程和两个新增滚动回归。生产构建通过，仍有原有分包体积提示。

[实际测试输出](office-scroll-checks.txt) · [桌面底部](office-scroll-1366.png) · [手机底部](office-scroll-390.png)

本次为局部 CSS 修复，没有重跑后端或付费模型；此前完整验收报告属于其当时输入版本，不用它证明本次修改。
