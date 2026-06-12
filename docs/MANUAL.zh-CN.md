# Paper Flow 使用手册

[English](MANUAL.md) | **简体中文**

在 Zotero 中通过 **工具 → Paper Flow 设置** 打开(或 编辑 → 设置 → Paper Flow)。

## 自动处理

| 设置项 | 含义 |
| --- | --- |
| 启用 Paper Flow 插件 | 总开关。 |
| 自动处理新增或更新的条目 | 监听模式:新增/修改的条目经防抖延迟后排队处理。 |
| 显示提示弹窗 | 运行进度通知。 |
| 内容未变化时跳过自动处理 | 对标题/作者/日期/DOI/摘要/PDF 做指纹。同步回写(只动元数据)不会触发重复的 AI 解读;手动运行总是执行。 |
| 仅处理某收藏夹 | 可选过滤;用按钮一键选取当前 Zotero 选中的收藏夹。 |
| 同步 Zotero 笔记 | 把 Zotero 笔记复制到 Notes 子页。 |
| 删除清理前先预览 | 归档已删条目的 Notion 页面前先做演练。 |
| 防抖秒数 | Zotero 变化后等待多久再启动。 |
| 进程超时 | 卡死的 CLI 运行在此秒数后被终止(默认 1800)。 |

## AI CLI

| 设置项 | 含义 |
| --- | --- |
| 版本横幅 | 常驻显示所选 CLI 的已装/最新版本。过期且自动更新开启 → 后台自动更新。 |
| 配置行 | 实际将运行的配置:工具 · 模型 · 推理强度 · 上下文窗口。 |
| 运行环境 | `自动`(Windows→WSL,macOS/Linux→本机)、`本机 shell`、`WSL`。WSL 模式会剔除所有 Windows `/mnt/*` 路径——CLI 必须装在发行版内。 |
| 论文解读语言 | 解读输出语言(七种)。 |
| 工作区路径 | 含本仓库 `pyproject.toml` 的文件夹,用所选运行环境的路径风格(WSL:`/mnt/c/...`)。 |
| Notion token / 数据库 ID | 覆盖工作区 `.env`;留空则回退使用 `.env`。数据库 ID(非机密)会随 Zotero 账户跨设备同步;token 永不同步——每台设备粘贴一次即可,若怀疑泄露可在 notion.so/my-integrations 一键重置。 |
| AI 工具 | Codex CLI 或 Claude Code。 |
| 模型 | 下拉来自 CLI **官方本地缓存**(claude:`/model` 菜单缓存,含限时模型精确 id;codex:`models_cache.json`),也可手动输入任意模型名。留空 = CLI 默认。Claude 别名(`opus`/`sonnet`/`haiku`)永远指向最新版。 |
| 推理强度 | codex 的档位跟随所选模型支持的档位;留空 = 默认。 |
| 自动保持 CLI 最新 | 每天一次后台更新所选 CLI;横幅检测到过期时也会立即触发。 |
| 刷新模型列表 | 重新读取官方模型缓存与版本状态。 |

## Prompt 预设

最多保存 10 条自定义解读 prompt;选中即载入,**设为默认**(★)后每次启动自动重新应用,跨插件升级保留。"填入默认 prompt" 插入你的默认预设(未设默认时用内置模板)。

**不要**在 prompt 里要求模型自报模型名/推理强度/token——这些元数据由程序自动以"生成信息"页脚追加(真实模型、强度、token 数,Claude 还含花费)。

## 公式渲染

管线要求模型把全部数学写成 LaTeX(行内 `$...$`,独立 `$$...$$`),写入 Notion 时转换为原生公式(KaTeX),排版正确。

## 故障排查

运行 **检查 Paper Flow 环境**,找第一个失败标记:

| 标记 | 修法 |
| --- | --- |
| `WORKSPACE:MISSING` | 工作区路径需指向含 `pyproject.toml` 的文件夹,且用所选运行环境的路径风格。 |
| `UV:MISSING` | 在所选运行环境内安装 [uv](https://docs.astral.sh/uv/)(WSL 用户装在 WSL 里)。 |
| `PAPER_NOTION_FLOW:MISSING` / `ModuleNotFoundError` | 在工作区执行 `uv sync`。 |
| `NOTION_TOKEN:MISSING` / `NOTION_DATABASE_ID:MISSING` | 填插件设置或工作区 `.env`;插件设置非空时优先,留空回退 `.env`。 |
| `NOTION_SCHEMA` 错误 / `object_not_found` | 把 integration 连接到数据库:数据库页 → `⋯` → Connections。 |
| `ZOTERO_DB:MISSING` | 在 `.env` 设 `ZOTERO_DATA_DIR` 为你的 Zotero 数据目录。 |
| `AI_PATH_SCOPE:WINDOWS_LEAK` | CLI 在 WSL 内解析到了 Windows 二进制。请在 WSL 发行版内安装(`npm i -g @openai/codex`)。 |
| `SELECTED_AI:MISSING` | 所选 CLI 未安装在所选运行环境中。 |
| `model ... requires a newer version` | CLI 过期。横幅会自动更新(或点"立即更新 CLI")。若用 nvm 装了多个 node 版本,请清理非默认版本下的旧 CLI。 |
| `CLAUDE_UPDATE`/`CODEX_UPDATE:AVAILABLE` | 仅提示;自动更新会处理。 |

每次失败都会把完整诊断写入临时文件(路径见状态框)并复制到剪贴板。
