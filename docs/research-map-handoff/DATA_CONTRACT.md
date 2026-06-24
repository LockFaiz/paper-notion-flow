# 数据契约：`window.GRAPH`

页面在 `DOMContentLoaded` 时读取全局变量 `window.GRAPH` 并渲染。**必须在加载/执行页面脚本之前注入它。** 拿不到（或四个数组全空）时页面显示空状态，不报错。

```js
window.GRAPH = {
  problems:    [ … ],   // 核心研究问题
  concepts:    [ … ],   // 方法/模型/数据集/指标/概念
  relations:   [ … ],   // 节点之间的关系
  gaps:        [ … ],   // 尚未解决的空白 / 可做方向
  paper_dates: { … },   // 论文 → 发表年月
  papers_meta: { … }    // 论文 → 详情（详情页数据源，可选）
};
```

---

## 关键约定：论文用"键字符串"互相引用

`problems[].papers`、`concepts[].papers`、`gaps[].papers`、`paper_dates` 的键、`papers_meta` 的键 —— **全部用同一个字符串作为论文标识**。

- 推荐用 **Zotero itemKey** 或稳定的内部 ID 作为这个键（避免标题变化导致引用断裂），再在 `papers_meta[key].title` 里放真正展示的原标题。
- 也可以直接用标题字符串当键（演示数据就是这么做的），但不如 itemKey 稳。
- **同一篇论文在所有地方必须用完全一致的键**，否则关联会断。

---

## 字段定义

### `problems: []`  研究问题
```ts
{
  name: string,           // 必填，唯一，作区块标题
  description: string,    // 一句话描述（区块副标题 + hover）
  papers: string[]        // 该问题下的论文键；数组长度 = 该方向"比重"
}
```

### `concepts: []`  方法 / 模型 / 数据集 / 指标 / 概念
```ts
{
  name: string,           // 必填，唯一
  kind: "method" | "model" | "dataset" | "metric" | "concept",
  description: string,    // 一句话（hover 浮层 + 详情页贡献兜底）
  papers: string[]        // 提出/使用该概念的论文键
}
```
> 指标卡"方法/模型"数 = `kind` 为 `method` 或 `model` 的 concept 数。

### `relations: []`  关系（决定脉络连线）
```ts
{
  source: string,         // concept.name 或 problem.name
  target: string,         // concept.name 或 problem.name
  type: "addresses" | "builds-on" | "uses" | "contradicts" | "evaluates",
  rationale: string       // 为什么（hover 浮层）
}
```
- **`type:"addresses"` 且 `target` 是某问题名** → 该方法挂到该问题区块的"解决方法"下。这是把方法归入问题的**唯一**途径。
- 其余 type（`builds-on`/`uses`/`evaluates`/`contradicts`）出现在论文详情页的"学术脉络"里。

### `gaps: []`  空白 / 可做方向
```ts
{
  name: string,           // 必填，机会标题
  rationale: string,      // 为什么是空白（详情页"为什么留白"）
  related: string[],      // 关联的 concept.name 或 problem.name
  papers: string[]        // 关联论文键
}
```
- gap 通过 `related` 挂到问题区块：`related` 里若是问题名→直接挂；若是方法名→挂到该方法 `addresses` 的所有问题下。
- "已有工作做了什么"列表 = `related` 里的 concept 的论文 + `gaps[].papers`，按日期排序展示。

### `paper_dates: {}`  发表年月
```ts
{ [论文键: string]: "YYYY-MM" }   // 例 "2024-09"
```
- 驱动"发表趋势"柱状图与所有日期标签。格式必须是 `YYYY-MM`，否则该论文不计入趋势。

### `papers_meta: {}`  论文详情（详情抽屉数据源，整体可选）
```ts
{
  [论文键: string]: {
    title?: string,          // 原标题（英文）。给了它，页面各处显示原标题、把"键"作中文副标题
    authors?: string[],      // 作者
    venue?: string,          // 会议/期刊，如 "CoRL 2024"
    abstract?: string,       // 摘要
    contribution?: string,   // 一句话核心贡献（清单 + 详情页顶部）
    tags?: string[],         // 标签
    pdf?: string,            // PDF 地址，见下表
    notion_url?: string,     // Notion 页面链接 → "在 Notion 中打开"按钮
    analysis?: Array<{       // AI 解读，结构化分段，直接渲染在抽屉里（无需打开 Notion）
      h: string,             // 小标题，如"解决了什么"/"怎么做的"/"关键结果"/"局限/留白"
      b: string | string[]   // 段落(string) 或 要点列表(string[])
    }>,
    bibtex?: string          // 给了就显示"复制引用"按钮
  }
}
```

#### `pdf` 字段取值与页面行为
| 形式 | 例 | 页面行为 |
|---|---|---|
| `zotero://open-pdf/library/items/<itemKey>` | Zotero 内置阅读器 | 按钮"在 Zotero 中打开 PDF" |
| `file://<绝对路径>` | 本地文件 | 按钮"用本地阅读器打开" |
| `http(s)://… / blob:` | arXiv / 远程 | **直接内嵌 `<iframe>` 预览** + 打开按钮 |
| 缺省 | — | 显示"未关联 PDF"提示 |

---

## 降级规则（页面已实现，放心缺字段）

- 没有 `papers_meta[key]` → 论文各处用"键"本身当标题，详情页 PDF/解读区显示对应提示。
- 有 `papers_meta[key]` 但缺 `title` → 仍用"键"当标题，不显示中文副标题。
- 缺 `analysis` 但有 `notion_url` → 解读区显示"点击上方在 Notion 中打开"。
- 缺 `analysis` 且缺 `notion_url` → 解读区显示"同步 Notion 解析后将在此显示"。
- `relations` / `gaps` 为空 → 对应区块/挂载自动省略。
- 四个数组全空 → 整页空状态。

---

## 最小可用注入示例

```html
<script>
window.GRAPH = {
  problems: [{ name:"样本效率", description:"在有限演示下学到可用策略",
               papers:["ITEMKEY_A","ITEMKEY_B"] }],
  concepts: [{ name:"等变扩散策略", kind:"method",
               description:"在动作扩散中引入对称等变结构", papers:["ITEMKEY_A"] }],
  relations:[{ source:"等变扩散策略", target:"样本效率",
               type:"addresses", rationale:"对称先验降低所需演示数量" }],
  gaps:     [{ name:"真实机器人长期部署验证", rationale:"多数方法只在短期评测",
               related:["等变扩散策略"], papers:["ITEMKEY_A"] }],
  paper_dates: { "ITEMKEY_A":"2024-09", "ITEMKEY_B":"2023-03" },
  papers_meta: {
    "ITEMKEY_A": {
      title:"Equivariant Diffusion Policy for Sample-Efficient Manipulation",
      authors:["L. Chen","M. Rivera"], venue:"CoRL 2024",
      pdf:"zotero://open-pdf/library/items/ITEMKEY_A",
      notion_url:"https://www.notion.so/...",
      contribution:"用对称先验把所需演示量降到约 1/4。",
      analysis:[
        { h:"解决了什么", b:"标准扩散策略需要大量演示。" },
        { h:"怎么做的",   b:["SE(2)-等变骨干","对称权重共享"] }
      ]
    }
  }
};
</script>
<!-- 然后再加载 research-map.html 的页面脚本（或直接打开该文件） -->
```
