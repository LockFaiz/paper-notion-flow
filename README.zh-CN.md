# Paper Notion Flow

[English](README.md) | **简体中文**

[![CI](https://github.com/LockFaiz/paper-notion-flow/actions/workflows/ci.yml/badge.svg)](https://github.com/LockFaiz/paper-notion-flow/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

把 **Zotero、Notion 和本地 AI CLI**(Codex CLI / Claude Code)串成一条论文阅读工作流:Zotero 收藏论文 → 自动同步到 Notion 论文数据库 → 本地 AI 直接阅读 PDF,生成结构化中文解读(公式以 KaTeX 正确渲染),写入论文页面的子页。

全程本地运行:不上传你的文献库,AI 调用走你自己的 Codex/Claude 订阅。

## 快速开始

1. **复制 Notion 模板**(所需属性已配好):
   👉 [论文数据库模板](https://slime-effect-c47.notion.site/24b70931354a4182801ac4c2f96840cb?v=b031fe6acbf5419aa20cbea1f77b3185) — 点右上角 **Duplicate**。
2. 创建 [Notion integration](https://www.notion.so/my-integrations),复制 token,并把它连接到你复制的数据库(数据库页 → `⋯` → Connections)。
3. 安装 [uv](https://docs.astral.sh/uv/),然后:
   ```bash
   git clone https://github.com/LockFaiz/paper-notion-flow.git
   cd paper-notion-flow
   uv sync
   ```
4. 安装本地 AI CLI:[Codex CLI](https://github.com/openai/codex)(`npm i -g @openai/codex`)或 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)。**Windows 用户必须装在 WSL 里**。
5. 从 [Releases](https://github.com/LockFaiz/paper-notion-flow/releases) 下载 `paper-flow.xpi`,在 Zotero 安装(工具 → 插件 → 齿轮 → Install Plugin From File),重启 Zotero。
6. 打开 **工具 → Paper Flow 设置**:填工作区路径(本仓库文件夹)、Notion token、数据库 ID,选择 AI 工具,点 **检查 Paper Flow 环境**——所有标记应为 `OK`。
7. 在 Zotero 里右键任意论文 → **Paper Flow:处理当前选中文献**,解读会出现在对应 Notion 页面的子页里。

## 功能一览

- **自动化**:Zotero 新增/修改条目自动触发;可限定监听某个收藏夹;内容未变化(如同步回写)自动跳过,不重复烧 token。
- **AI 直接读 PDF**:把 PDF 路径交给 Codex/Claude 原生阅读,不经过有损的文本抽取;图表、公式可被理解。
- **公式渲染**:解读中的数学以 LaTeX 生成,写入 Notion 时转为行内公式与公式块(KaTeX),不再是乱码符号。
- **模型管理**:模型下拉来自 CLI 官方本地缓存(含限时新模型的精确 id);CLI 版本横幅常驻显示,过期自动后台更新。
- **Prompt 预设**:保存最多 10 条自定义解读 prompt,可设默认,跨插件升级保留。
- **生成信息页脚**:每篇解读末尾自动标注实际使用的模型、推理强度、token 数(Claude 还含花费)。
- **跨平台**:Windows(WSL / 原生)、macOS、Linux;WSL 模式强制只使用发行版内原生 CLI。
- **七语言界面**:英、简中、日、韩、法、德、西。

## 架构

```text
Zotero(浏览器插件收藏 / 拖入 PDF)
        │
        ▼
Paper Flow Zotero 插件监听条目事件
        │
        ▼
paper-notion-flow Python CLI 读取 Zotero 数据库与 PDF
        │
        ▼
本地 Codex CLI / Claude Code 生成结构化解读(JSON)
        │
        ▼
Notion:论文行(元数据)+ 子页(解读,公式渲染,生成信息页脚)
```

## 推荐组织方式

**全部论文放一个 Notion 数据库**,不要按主题建多个库。主题用 Zotero 收藏夹表达,Paper Flow 会把收藏夹名写进 Notion 的 `Topic` 多选属性——一个可检索的统一文献库,主题归属仍在 Zotero 里管理。

## 文档

- [English README](README.md) — 完整英文文档(数据库属性、CLI 配置、三种工作流详解)
- [使用手册(中文)](docs/MANUAL.zh-CN.md) / [Manual (EN)](docs/MANUAL.md) — 每个设置项的含义与故障排查
- 插件开发文档:[paper-flow-zotero-plugin/README.md](paper-flow-zotero-plugin/README.md)

## 致谢

- [Notero](https://github.com/dvanoni/notero)(David Vanoni)开创了 Zotero → Notion 同步的工作流,启发了本项目。Paper Flow 与其共用 Notion 属性命名习惯以保持兼容,但不包含任何 Notero 代码。
- Zotero 插件基于 windingwind 与 Zotero 插件社区的 [zotero-plugin-template](https://github.com/windingwind/zotero-plugin-template)、[zotero-plugin-toolkit](https://github.com/windingwind/zotero-plugin-toolkit) 和 [zotero-plugin-scaffold](https://github.com/northword/zotero-plugin-scaffold) 构建,`bootstrap.js` 源自 Zotero 官方 [Make It Red](https://github.com/zotero/make-it-red) 示例。
- 论文解读由你本地安装的 [Codex CLI](https://github.com/openai/codex) 或 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 生成。

## 许可

AGPL-3.0-or-later
