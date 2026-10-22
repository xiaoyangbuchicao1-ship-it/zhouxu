# 舟序字体试排

本目录是独立的排版确认样稿，不是正式落地页，不接登录或工作台，也没有修改正式页面及媒体资源。

直接打开 `index.html` 即可；字体自托管，不依赖 Google Fonts 在线服务。也可从项目的 Vite 开发服务访问 `/design-previews/typography/index.html`。

## 字体决策

- 英文展示：Instrument Serif Italic，延续开源模板。
- 中文展示主选：方正清刻本悦宋。本机未安装，未取得可供本项目嵌入的网页授权文件；没有下载、打包或假冒该字体。后续取得合法字体与授权后，再独立比较实际字形。
- 当前中文展示备选：Noto Serif SC，500。与思源宋体有项目渊源，但此处按实际文件名称标注，不声称其为清刻本悦宋或另一套字体。
- 当前中文正文备选：Noto Sans SC，400。

## 字体来源与许可

本页的三份字体通过 Google Fonts CSS API 的 `text` 参数生成页面文字子集，遵循其原有 SIL Open Font License。子集只覆盖当前试排文字；修改文案后必须更新子集，不能直接拿来支撑完整产品输入和对话。

更新方式：使用 PowerShell 7 执行本目录的 `refresh-fonts.ps1`。脚本只从 Google Fonts 与 Google Fonts 官方仓库下载所列字体和许可，不安装系统字体。

- [Instrument Serif 官方字体仓库](https://github.com/Instrument/instrument-serif)
- [Instrument Serif 许可](https://github.com/google/fonts/blob/main/ofl/instrumentserif/OFL.txt)；本地副本 `fonts/Instrument-Serif-OFL.txt`。
- [Noto Serif SC 许可](https://github.com/google/fonts/blob/main/ofl/notoserifsc/OFL.txt)；本地副本 `fonts/Noto-Serif-SC-OFL.txt`。
- [Noto Sans SC 许可](https://github.com/google/fonts/blob/main/ofl/notosanssc/OFL.txt)；本地副本 `fonts/Noto-Sans-SC-OFL.txt`。

本样稿未安装任何系统字体，未使用商业字体文件。

## 审核重点

英文斜体的实际字形、中文正体与英文的对比、中文断行、细笔画可见度、模块名称可读性，以及桌面与手机首屏的比例。可使用“英文字标 / 中文字标”按钮切换首屏主字标。
