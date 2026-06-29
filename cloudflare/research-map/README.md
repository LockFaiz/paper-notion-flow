# Research Map — Cloudflare 私有站点

把研究地图从"本地 html 文件"变成一个**固定网址、跨设备登录可见、实时读 Notion** 的私有网站。

## 它怎么工作

```
你的浏览器(任何设备)
   │ 访问 https://<你的项目>.pages.dev
   ▼
Cloudflare Access  ── 只放行你的邮箱,别人打不开
   │ 通过
   ▼
Pages Function (functions/index.js)
   1. 从 Cloudflare 环境变量读 NOTION_TOKEN + 5 个库 ID
   2. 现场查 Notion 5 个库  (functions/_graph.js)
   3. 服务器端拼出 window.GRAPH 注入 public/research-map.html
   4. 边缘缓存 GRAPH_CACHE_SECONDS 秒
   ▼
返回带数据的完整网页 → 你看到最新地图
```

- **Notion 是唯一数据源。** 在 Notion 改了节点/论文,刷新网页即反映(默认有 10 分钟边缘缓存;`?refresh=1` 立即刷新)。**改 Notion 不需要重新部署。**
- **代码变了才需要重新部署**(`wrangler pages deploy`)。
- **Token 只存在 Cloudflare 一处**,不进 git、不进 Notion、不随 Zotero 漫游。

### 文件
```
cloudflare/research-map/
├── functions/
│   ├── index.js        # GET / → 注入 GRAPH 并返回页面(带缓存)
│   └── _graph.js       # Notion → window.GRAPH(export_from_notion 的 JS 移植)
├── public/
│   └── research-map.html   # 前端页面(src/.../templates/research-map.html 的副本)
├── wrangler.toml       # 项目配置(只有 public/ 会被上传)
├── .dev.vars.example   # 本地环境变量样板
└── README.md
```
> 注:`public/research-map.html` 是 `src/paper_notion_flow/templates/research-map.html` 的副本。改了模板就重新拷一份过来(UI 一般不用动)。

---

## 终端约定

**Cloudflare 这套命令全程在 WSL bash 里执行**(wrangler 装在 WSL),与项目里跑 Python 的 WSL 流程同一个环境。先进到本目录:
```bash
cd /mnt/c/Users/test/Documents/Codex/2026-04-20-terminal-wsl-ps-e-software-notionpaperflow/cloudflare/research-map
```

## 前置(一次性)

1. **Notion**:用你新建的**只读** integration token,并把 **5 个库都 share 给它**:
   Papers、Problems、Concepts、Relations、Gaps。(解读子页是 Papers 页面的子页,跟随 Papers 权限,无需单独 share。)
2. **wrangler**(已装则跳过装):
   ```bash
   # npm install -g wrangler   # 如未装
   wrangler login              # WSL 里会打印一个 URL,复制到浏览器授权 Cloudflare
   ```

---

## A. 本地先跑通(确认数据对再上云)

> 全程在 `cloudflare/research-map/` 目录里执行。

1. 复制环境变量样板并填值:
   ```bash
   cp .dev.vars.example .dev.vars
   ```
   编辑 `.dev.vars`,填 `NOTION_TOKEN` 和 5 个库 ID(库 ID 就是你项目 `.env` 里那几个)。
2. 启动本地开发服务器:
   ```bash
   wrangler pages dev
   ```
   打开它给出的地址(通常 `http://localhost:8788`)。
3. 验收:地图能加载、节点和论文是你 Notion 里的真实数据。点论文看详情抽屉里有 AI 解读分段。
   - 数据不对/想强刷:访问 `http://localhost:8788/?refresh=1`。

`.dev.vars` 已被 `.gitignore` 忽略,且不在 `public/` 里,**不会**被上传或提交。

---

## B. 部署上云

```bash
wrangler pages deploy
```
第一次会让你确认/创建一个 Pages 项目名(如 `research-map`)。完成后拿到一个网址:
`https://research-map.pages.dev`(或带哈希前缀的预览域名)。

此时网址还**没有密钥**,函数会因为读不到 `NOTION_TOKEN` 返回空地图——下一步补上。

---

## C. 设生产环境密钥

把这 6 个值设进生产环境(命令行逐个设,会提示你粘贴值):
```bash
wrangler pages secret put NOTION_TOKEN
wrangler pages secret put NOTION_DATABASE_ID
wrangler pages secret put NOTION_PROBLEMS_DATABASE_ID
wrangler pages secret put NOTION_CONCEPTS_DATABASE_ID
wrangler pages secret put NOTION_RELATIONS_DATABASE_ID
wrangler pages secret put NOTION_GAPS_DATABASE_ID
```
> 或在面板里设:**Workers & Pages → research-map → Settings → Environment variables**,逐个 Add(Token 选 "Encrypt")。

设完密钥后,重新部署一次让其生效(或在面板点 Retry deployment):
```bash
wrangler pages deploy
```
打开 `https://research-map.pages.dev` 应能看到地图了。

---

## D. 加门禁(变成私有,只有你能进)

1. Cloudflare 面板 → **Zero Trust**(没开过会让你建一个免费 team,起个名即可)。
2. **Access → Applications → Add an application → Self-hosted**。
3. Application domain 填你的 pages 域名:`research-map.pages.dev`。
4. 加一条 **Policy**:Action = Allow,Include = **Emails** → 填 `kivated@outlook.com`。
5. 保存。以后访问该网址会先跳转登录(邮箱收一次性验证码),只有你的邮箱能进。

---

## E. 日常使用

- 任何设备打开 `https://research-map.pages.dev` → 邮箱验证 → 看最新地图。
- 在 Notion 改了东西:等≤10 分钟边缘缓存过期自动更新,或加 `?refresh=1` 立即刷新。
- 想换 AI 解读 / 加论文:照旧用本地 `map build` + `map sync` 写回 Notion,网站随之反映。

---

## 取舍与限制(已知,可接受)

- **边缘无本机 Zotero**:`papers_meta` 里**没有** `zotero://` 打开本地 PDF 的深链,也没有 Zotero 本地标签。
  标题/作者/会议/摘要/Notion 标签/Notion 链接/AI 解读分段都有。需要点 PDF 时用本地 `map render` 版。
- **AI 解读的 subrequest 预算**:每篇论文要多读 2 次 Notion(找解读子页 + 读其内容)。
  Cloudflare 免费版单次请求上限 50 次子请求,所以默认 `GRAPH_ANALYSIS_CAP=20` 篇封顶,
  超出的论文详情抽屉退化为"在 Notion 中打开"。库更大就调 `wrangler.toml` 里的这个值
  (并考虑升级 Workers Paid),或设 `GRAPH_INCLUDE_ANALYSIS=false` 完全跳过解读读取(最快)。
  超出封顶的数量会写在返回数据的 `_meta.analysis_truncated` 里,不会静默截断。

## 可调参数(`wrangler.toml [vars]`,非密钥)

| 变量 | 默认 | 作用 |
|---|---|---|
| `GRAPH_CACHE_SECONDS` | `600` | 边缘缓存秒数;`0` 关缓存;`?refresh=1` 永远绕过 |
| `GRAPH_INCLUDE_ANALYSIS` | `true` | 是否读取每篇论文的 AI 解读分段 |
| `GRAPH_ANALYSIS_CAP` | `20` | 最多为多少篇读取解读(子请求预算) |

## 排错

- **空地图 / 报错**:查返回 HTML 里注入的 `window.GRAPH._meta.error`(本地用 `?refresh=1` + 看 `wrangler pages dev` 终端日志)。多半是 token 没设、库没 share 给 integration、或库 ID 填错。
- **数据是旧的**:加 `?refresh=1`,或把 `GRAPH_CACHE_SECONDS` 调小。
- **谁都能打开**:Access 的 Application domain 没覆盖到该网址,或漏了 Allow 策略。
