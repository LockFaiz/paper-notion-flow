# 实现任务清单：研究地图功能接入

按顺序实现。每个任务标注了**目标 / 在哪做 / 验收标准**。先读 `DATA_CONTRACT.md`。

> 背景：`research-map.html` 是已完成的纯前端页面，只消费 `window.GRAPH`。你的工作是**供数 + 打通外部动作**，不要改 UI/CSS。插件本身（见仓库 `src/modules/paperFlow.ts`、`addon/bootstrap.js`）已经在监听 Zotero 事件并向 Notion 同步——你是在它之上加一个"导出 GRAPH + 展示页面"的能力。

---

## 任务 0 · 把页面纳入插件资源

- **目标**：让插件能在 Zotero 内打开这张研究地图。
- **在哪做**：把 `research-map.html` 放进 `addon/content/`（如 `addon/content/research-map.html`）。在插件菜单/工具栏加一个"打开研究地图"入口，用 Zotero 的方式打开它（独立 tab 或 `Zotero.getMainWindow().open(...)` 加载该资源）。
- **验收**：菜单点击后能看到页面 + 空状态（此时还没注入数据）。

---

## 任务 1 · 设计论文键（itemKey）

- **目标**：确定全局唯一、稳定的论文标识，贯穿 `window.GRAPH` 所有引用。
- **在哪做**：取数逻辑层（建议新建 `src/modules/researchMap.ts`）。
- **要求**：用 Zotero `item.key`（itemKey）作为论文键，**不要用标题**（标题会变、会重复）。展示用的原标题放进 `papers_meta[key].title`。
- **验收**：能从一个 Zotero collection/library 拿到 `{ itemKey: item }` 映射。

---

## 任务 2 · 构建 `paper_dates`

- **目标**：每篇论文 → `"YYYY-MM"`。
- **在哪做**：`researchMap.ts`。
- **要求**：优先用 Zotero 条目的 `date` 字段，解析成 `YYYY-MM`（无月份时可补 `-01`，但要标注）。解析不出的论文不放进 `paper_dates`（页面会自动从趋势图排除，但仍可出现在其它区块）。
- **验收**："发表趋势"柱状图出现，hover 某月能看到该月论文清单。

---

## 任务 3 · 构建 `papers_meta`（基础字段）

- **目标**：填 `title / authors / venue / abstract / tags`。
- **在哪做**：`researchMap.ts`，从 Zotero 条目字段映射。
- **映射**：`title`←Zotero title；`authors`←creators（格式化为姓名数组）；`venue`←publicationTitle/conferenceName/proceedingsTitle；`abstract`←abstractNote；`tags`←item.getTags()。
- **验收**：论文清单显示英文原标题 + 中文副标题；详情抽屉顶部显示作者/会议/年月/摘要。

---

## 任务 4 · 接入本地 PDF

- **目标**：详情页能打开每篇论文在 Zotero 里保存的 PDF。
- **在哪做**：`researchMap.ts` 填 `papers_meta[key].pdf`。
- **要求**：找到条目的 PDF 附件，生成 `zotero://open-pdf/library/items/<attachmentKey>`（群组库用 `groups/<id>/items/<key>`）。确认该 scheme 在你的 Zotero 版本能拉起内置阅读器；不行则回退到附件的 `file://` 绝对路径（页面会显示"用本地阅读器打开"）。
- **验收**：详情页"PDF 全文"出现按钮，点击在 Zotero 内打开对应 PDF。
- **注意**：不要给 `papers_meta` 里所有论文都强加 pdf；没有附件的就不填，页面会显示"未关联 PDF"。

---

## 任务 5 · 接入 Notion AI 解读

- **目标**：把 Notion 上 AI 生成的解读**直接显示在详情抽屉里**，并提供"在 Notion 打开"外链。
- **在哪做**：取 Notion 数据的逻辑（复用插件现有的 Notion 同步通道，见 `paperFlow.ts` 里与 Notion 交互的部分）。
- **要求**：
  1. `notion_url` ← 该论文对应的 Notion page URL（已有同步映射的话直接取）。
  2. `analysis` ← 把 Notion 页面里的解读解析成结构化分段 `[{h, b}]`：
     - `h` = 小标题（如"解决了什么""怎么做的""关键结果""局限/留白"）。
     - `b` = 段落字符串，或要点字符串数组。
     - 如果 Notion 内容是自由富文本，至少按标题块切分；拿不到结构就整段塞进一个 `{h:"AI 解读", b:"<纯文本>"}`。
  3. 解析失败时**只填 `notion_url`**，页面会优雅降级为"点击在 Notion 打开"。
- **验收**：详情页"AI 解读 · Notion"区直接显示解读分段，无需离开页面；同时有"在 Notion 中打开"按钮。

---

## 任务 6 · 构建知识图谱四要素

- **目标**：填 `problems / concepts / relations / gaps`。
- **在哪做**：从 AI 抽取结果映射（插件已用 AI 从每篇论文抽取 problem/concept/relation/gap，见 README 描述的流程）。
- **要求**：
  - `problems[].papers` / `concepts[].papers` / `gaps[].papers` 全部用**任务 1 的 itemKey**。
  - 方法归入问题：生成 `relations: {source:方法, target:问题, type:"addresses", rationale}`。
  - gap 用 `related` 挂到问题或方法名上。
  - `concept.kind` 用枚举值之一；`relation.type` 用枚举值之一（见契约）。
- **验收**：指标卡数字正确；问题区块按论文数排序、含方法与空白；"研究机会"出现闭环断点图；论文详情页"它在研究地图中的位置"显示该论文的问题/方法/空白节点。

---

## 任务 7 · 注入与生命周期

- **目标**：打开页面时注入最新的 `window.GRAPH`。
- **在哪做**：任务 0 的打开逻辑。
- **要求**：
  1. 打开前把任务 1–6 组装成一个 GRAPH 对象。
  2. 在页面脚本执行前注入（推荐：插件侧把对象 `JSON.stringify` 后写入页面 `window.GRAPH`，或通过 `<script>` 注入，或 postMessage 后由页面在收到时再渲染——若用 postMessage 需在页面侧加监听，**这是唯一允许的 HTML 改动**）。
  3. **删除** `research-map.html` 里那段带"演示数据，可整段删除"注释的内置 `window.GRAPH`。
- **验收**：页面显示真实文献库数据；空库时显示空状态，无报错。

---

## 任务 8 · 联调与边界

- **验收清单**：
  - [ ] 大库（数百篇）下趋势图可横向滚动、不卡顿。
  - [ ] 论文键在所有数组里一致，无断链（点节点能正确跳转、相关论文正确）。
  - [ ] 缺 PDF / 缺 Notion / 缺 meta 的论文都能优雅降级，不报错。
  - [ ] 中英文标题、长标题截断正常。
  - [ ] 深色模式（跟随系统）正常。
  - [ ] `zotero://` 链接在目标 Zotero 版本确实能拉起阅读器。

---

## 不要做

- 不要改页面的 HTML 结构 / CSS / 配色 / 动画（唯一例外：任务 7 若选 postMessage 方案，可加一个消息监听）。
- 不要引入前端框架或重型库——页面是零依赖的，保持现状。
- 不要把"键"用标题硬编码；用 itemKey。
