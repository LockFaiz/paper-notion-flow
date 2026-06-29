// Notion -> window.GRAPH, for the Cloudflare edge.
//
// This is the JS port of `export_from_notion` + `_build_papers_meta` in
// src/paper_notion_flow/research_map.py. Notion is the source of truth: the 5
// databases (Papers + Problems/Concepts/Relations/Gaps) are read live and
// assembled into the `window.GRAPH` contract documented in
// docs/research-map-handoff/DATA_CONTRACT.md.
//
// The only thing the edge cannot do that the local `map build` can: resolve the
// Zotero PDF deep link and Zotero-local tags (those need the local Zotero
// SQLite). Everything else — title/authors/venue/abstract/tags/notion_url and
// the AI guide parsed into `analysis` sections — comes straight from Notion.

const NOTION_VERSION = "2025-09-03"; // data_sources API (2025-09 Notion upgrade)

// Property-name candidates, mirroring config.py defaults. Override via env if a
// workspace uses different property names (comma-separated).
const DEFAULT_CANDIDATES = {
  title: ["Title", "Name"],
  zoteroKey: ["Zotero Key", "Item Key", "Key"],
  zoteroUri: ["Zotero URI", "Zotero Link"],
  authors: ["Authors", "Author"],
  abstract: ["Abstract", "Abstract Note"],
  publication: ["Publication", "Journal", "Publication Title"],
  proceedings: ["Proceedings Title", "Proceedings"],
  tags: ["Tags", "Keywords"],
  collection: ["Collections", "Collection", "Topic", "Topics", "Tags", "Keywords", "Category", "Subject"],
};

// Localized guide headings (from notion_writer.TEXT), so analysis parsing works
// whatever language the guide was generated in.
const GUIDE_TITLES = [
  "论文解读",
  "Paper Guide",
  "論文解説",
  "논문 해설",
  "Guide de lecture",
  "Leseleitfaden",
  "Guía de lectura",
];
const SUMMARY_HEADINGS = new Set([
  "一句话总览",
  "One-sentence overview",
  "一文要約",
  "한 문장 요약",
  "Vue d'ensemble en une phrase",
  "Überblick in einem Satz",
  "Resumen en una frase",
]);
const CONTRIB_HEADINGS = new Set([
  "贡献与优点",
  "Contributions and strengths",
  "貢献と強み",
  "기여와 강점",
  "Contributions et forces",
  "Beiträge und Stärken",
  "Contribuciones y fortalezas",
]);

const ITEM_KEY_RE = /\/items\/([A-Za-z0-9]{8})/;

function candidates(env, key) {
  const envName = "NOTION_" + key.toUpperCase() + "_CANDIDATES";
  const raw = env[envName];
  if (raw) {
    const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length) return parts;
  }
  return DEFAULT_CANDIDATES[key];
}

// ---- Notion REST helpers ----------------------------------------------------

async function notion(token, path, body) {
  const res = await fetch("https://api.notion.com/v1/" + path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + token,
      "Notion-Version": NOTION_VERSION,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Notion ${path} -> ${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

// A database id may resolve to a data source (new API) or be queried directly
// (legacy). Returns a query path builder either way.
async function resolveSource(token, databaseId) {
  const db = await notion(token, "databases/" + databaseId);
  const sources = db.data_sources || [];
  if (sources.length) return "data_sources/" + sources[0].id + "/query";
  return "databases/" + databaseId + "/query";
}

async function queryAll(token, queryPath) {
  const results = [];
  let cursor;
  do {
    const body = { page_size: 100 };
    if (cursor) body.start_cursor = cursor;
    const data = await notion(token, queryPath, body);
    results.push(...(data.results || []));
    cursor = data.has_more ? data.next_cursor : null;
  } while (cursor);
  return results;
}

async function listBlocks(token, blockId) {
  const results = [];
  let cursor;
  do {
    const qs = new URLSearchParams({ page_size: "100" });
    if (cursor) qs.set("start_cursor", cursor);
    const data = await notion(token, "blocks/" + blockId + "/children?" + qs.toString());
    results.push(...(data.results || []));
    cursor = data.has_more ? data.next_cursor : null;
  } while (cursor);
  return results;
}

// ---- property readers (port of research_map.py helpers) ---------------------

function plain(items) {
  return (items || [])
    .map((i) => {
      // Inline equations: Notion stores them as equation objects (that's why
      // Notion renders math). Restore the `$…$` delimiters that were stripped
      // when the guide was written, so KaTeX on the page can render them.
      if (i.type === "equation" && i.equation && i.equation.expression) {
        return "$" + i.equation.expression + "$";
      }
      return i.plain_text || (i.text && i.text.content) || "";
    })
    .join("")
    .trim();
}

function props(page) {
  return (page && page.properties) || {};
}

function firstTextProperty(page, names) {
  const p = props(page);
  for (const name of names) {
    const prop = p[name];
    if (!prop) continue;
    if (prop.type === "rich_text" || prop.type === "title") {
      const v = plain(prop[prop.type]);
      if (v) return v;
    } else if (prop.type === "url" && prop.url) {
      return prop.url;
    }
  }
  return "";
}

function titleProperty(page) {
  for (const prop of Object.values(props(page))) {
    if (prop.type === "title") return plain(prop.title);
  }
  return "";
}

function multiValues(page, names) {
  const p = props(page);
  for (const name of names) {
    const prop = p[name];
    if (!prop) continue;
    if (prop.type === "multi_select") {
      const v = (prop.multi_select || []).map((i) => i.name).filter(Boolean);
      if (v.length) return v;
    } else if (prop.type === "rich_text") {
      const v = plain(prop.rich_text);
      if (v) return [v];
    }
  }
  return [];
}

function selectVal(page, name) {
  const prop = props(page)[name];
  if (prop && prop.type === "select" && prop.select) return prop.select.name || "";
  return "";
}

function richVal(page, name) {
  const prop = props(page)[name];
  if (prop && prop.type === "rich_text") return plain(prop.rich_text);
  return "";
}

function relIds(page, name) {
  const prop = props(page)[name];
  if (!prop || prop.type !== "relation") return [];
  return (prop.relation || []).map((r) => r.id).filter(Boolean);
}

function itemKeyFromUri(uri) {
  const m = ITEM_KEY_RE.exec(uri || "");
  return m ? m[1] : "";
}

function paperKey(page, env) {
  const k = firstTextProperty(page, candidates(env, "zoteroKey"));
  if (k) return k;
  const fromUri = itemKeyFromUri(firstTextProperty(page, candidates(env, "zoteroUri")));
  return fromUri || page.id;
}

function paperPeriod(page) {
  const p = props(page);
  const fromDate = (prop) => {
    const start = (prop.date && prop.date.start) || "";
    if (start.length >= 7 && start[4] === "-") return start.slice(0, 7);
    if (start.length >= 4 && /^\d{4}$/.test(start.slice(0, 4))) return start.slice(0, 4);
    return null;
  };
  for (const [name, prop] of Object.entries(p)) {
    if (prop.type === "date" && !["add", "modif", "updat"].some((k) => name.toLowerCase().includes(k))) {
      const v = fromDate(prop);
      if (v) return v;
    }
  }
  for (const [name, prop] of Object.entries(p)) {
    if (prop.type === "number" && name.toLowerCase().includes("year") && prop.number) {
      return String(Math.trunc(prop.number));
    }
  }
  for (const prop of Object.values(p)) {
    if (prop.type === "date") {
      const v = fromDate(prop);
      if (v) return v;
    }
  }
  return null;
}

// ---- guide analysis parsing (port of _block_sections / _parse_guide_analysis)

function blockText(block) {
  const bt = block.type;
  // Standalone display equations ($$…$$): a top-level equation block has an
  // `expression` but no rich_text, so it was being dropped entirely before.
  if (bt === "equation" && block.equation && block.equation.expression) {
    return "$$" + block.equation.expression + "$$";
  }
  const payload = bt ? block[bt] : null;
  if (payload && payload.rich_text) return plain(payload.rich_text);
  return "";
}

function blockSections(blocks) {
  const sections = [];
  let current = null;
  let bullets = [];
  const flush = () => {
    if (current && bullets.length) {
      const existing = current.b;
      const base = Array.isArray(existing) ? existing : existing ? [existing] : [];
      current.b = base.concat(bullets);
    }
    bullets = [];
  };
  for (const block of blocks) {
    const bt = block.type || "";
    const text = blockText(block);
    if (bt === "heading_1" || bt === "heading_2" || bt === "heading_3") {
      flush();
      current = { h: text, b: "" };
      sections.push(current);
    } else if (bt === "bulleted_list_item" || bt === "numbered_list_item") {
      if (text) bullets.push(text);
    } else if (text) {
      flush();
      if (!current) {
        current = { h: "", b: "" };
        sections.push(current);
      }
      const prev = current.b;
      current.b = typeof prev === "string" && prev ? (prev + "\n" + text).trim() : prev || text;
    }
  }
  flush();
  return sections.filter((s) => s.h || (Array.isArray(s.b) ? s.b.length : s.b));
}

function isGuideTitle(title) {
  return GUIDE_TITLES.some((b) => title === b || title.startsWith(b + " · "));
}

async function parseGuideAnalysis(token, paperPageId) {
  const blocks = await listBlocks(token, paperPageId);
  const guide = blocks.find(
    (b) => b.type === "child_page" && isGuideTitle((b.child_page && b.child_page.title) || "")
  );
  if (!guide) return { analysis: [], contribution: "" };
  const sections = blockSections(await listBlocks(token, guide.id)).filter(
    (s) => !GUIDE_TITLES.includes(s.h)
  );
  let contribution = "";
  for (const s of sections) {
    if (SUMMARY_HEADINGS.has(s.h) && typeof s.b === "string") {
      contribution = s.b;
      break;
    }
  }
  if (!contribution) {
    for (const s of sections) {
      if (CONTRIB_HEADINGS.has(s.h)) {
        contribution = Array.isArray(s.b) ? s.b[0] || "" : typeof s.b === "string" ? s.b : "";
        break;
      }
    }
  }
  return { analysis: sections, contribution };
}

// bounded-concurrency map, so per-paper block reads stay under the Notion rate
// limit and the Cloudflare subrequest cap.
async function pool(items, size, fn) {
  const out = new Array(items.length);
  let i = 0;
  const worker = async () => {
    while (i < items.length) {
      const idx = i++;
      out[idx] = await fn(items[idx], idx);
    }
  };
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, worker));
  return out;
}

// ---- assembly ---------------------------------------------------------------

export async function buildGraph(env) {
  const token = env.NOTION_TOKEN;
  if (!token) throw new Error("NOTION_TOKEN is not set");

  const ids = {
    papers: env.NOTION_DATABASE_ID,
    problems: env.NOTION_PROBLEMS_DATABASE_ID,
    concepts: env.NOTION_CONCEPTS_DATABASE_ID,
    relations: env.NOTION_RELATIONS_DATABASE_ID,
    gaps: env.NOTION_GAPS_DATABASE_ID,
  };
  const missing = Object.entries(ids).filter(([, v]) => !v).map(([k]) => k);
  if (missing.length) throw new Error("Missing database id(s): " + missing.join(", "));

  // Resolve all 5 sources, then query all 5, in parallel.
  const [papersSrc, problemsSrc, conceptsSrc, relationsSrc, gapsSrc] = await Promise.all([
    resolveSource(token, ids.papers),
    resolveSource(token, ids.problems),
    resolveSource(token, ids.concepts),
    resolveSource(token, ids.relations),
    resolveSource(token, ids.gaps),
  ]);
  const [paperPages, problemPages, conceptPages, relationPages, gapPages] = await Promise.all([
    queryAll(token, papersSrc),
    queryAll(token, problemsSrc),
    queryAll(token, conceptsSrc),
    queryAll(token, relationsSrc),
    queryAll(token, gapsSrc),
  ]);

  // Notion page id -> stable paper key (Zotero itemKey, else page id).
  const pidToKey = {};
  for (const page of paperPages) pidToKey[page.id] = paperKey(page, env);

  // Scope: GRAPH_COLLECTION restricts the map to one Zotero collection (the
  // Papers DB is a cumulative store of every collection ever synced). Empty =
  // all papers. `allowedKeys` is the set of paper keys in scope; node paper
  // links and papers_meta are restricted to it, then orphan nodes are pruned —
  // so the live Notion read reconstructs a clean per-collection subgraph.
  const collectionFilter = (env.GRAPH_COLLECTION || "").trim().toLowerCase();
  const scopedPages = collectionFilter
    ? paperPages.filter((p) =>
        multiValues(p, candidates(env, "collection")).some((c) => c.toLowerCase() === collectionFilter)
      )
    : paperPages;
  const allowedKeys = new Set(scopedPages.map((p) => pidToKey[p.id]).filter(Boolean));
  const keysOf = (rids) => rids.map((id) => pidToKey[id]).filter((k) => k && allowedKeys.has(k));

  let problems = problemPages
    .map((pg) => ({
      name: titleProperty(pg),
      description: richVal(pg, "Description"),
      papers: keysOf(relIds(pg, "Papers")),
    }))
    .filter((n) => n.name && n.papers.length); // drop orphan nodes (no in-scope paper)

  const cidToName = {};
  let concepts = conceptPages
    .map((pg) => {
      const name = titleProperty(pg);
      cidToName[pg.id] = name;
      return {
        name,
        kind: selectVal(pg, "Kind") || "concept",
        description: richVal(pg, "Description"),
        papers: keysOf(relIds(pg, "Papers")),
      };
    })
    .filter((n) => n.name && n.papers.length);

  // Surviving node names — relations/gaps may only reference these.
  const nodeNames = new Set([...problems.map((n) => n.name), ...concepts.map((n) => n.name)]);
  const conceptNames = new Set(concepts.map((n) => n.name));

  const relations = relationPages
    .map((pg) => ({
      source: richVal(pg, "Source"),
      target: richVal(pg, "Target"),
      type: selectVal(pg, "Type"),
      rationale: richVal(pg, "Rationale"),
    }))
    .filter((r) => r.source && r.target && nodeNames.has(r.source) && nodeNames.has(r.target));

  let gaps = gapPages
    .map((pg) => ({
      name: titleProperty(pg),
      rationale: richVal(pg, "Rationale"),
      related: relIds(pg, "Related").map((id) => cidToName[id]).filter((n) => conceptNames.has(n)),
      papers: keysOf(relIds(pg, "Papers")),
    }))
    .filter((n) => n.name && (n.papers.length || n.related.length));

  const paper_dates = {};
  for (const page of scopedPages) {
    const period = paperPeriod(page);
    if (period) paper_dates[pidToKey[page.id]] = period;
  }

  // papers_meta: Notion fields always; AI analysis when enabled and under cap.
  const includeAnalysis = String(env.GRAPH_INCLUDE_ANALYSIS ?? "true").toLowerCase() !== "false";
  const cap = Number(env.GRAPH_ANALYSIS_CAP || "20");
  const papers_meta = {};
  for (const page of scopedPages) {
    const key = pidToKey[page.id];
    const entry = {};
    const title = titleProperty(page);
    if (title) entry.title = title;
    const authors = firstTextProperty(page, candidates(env, "authors"));
    if (authors) {
      const list = authors.split(/[;,]|\band\b/).map((a) => a.trim()).filter(Boolean);
      if (list.length) entry.authors = list;
    }
    const venue =
      firstTextProperty(page, candidates(env, "publication")) ||
      firstTextProperty(page, candidates(env, "proceedings"));
    if (venue) entry.venue = venue;
    const abstract = firstTextProperty(page, candidates(env, "abstract"));
    if (abstract) entry.abstract = abstract;
    const tags = multiValues(page, candidates(env, "tags"));
    if (tags.length) entry.tags = tags;
    if (page.url) entry.notion_url = page.url;
    papers_meta[key] = entry;
  }

  let truncatedAnalysis = 0;
  if (includeAnalysis) {
    const targets = scopedPages.slice(0, Math.max(0, cap));
    await pool(targets, 4, async (page) => {
      try {
        const { analysis, contribution } = await parseGuideAnalysis(token, page.id);
        const entry = papers_meta[pidToKey[page.id]];
        if (analysis.length) entry.analysis = analysis;
        if (contribution) entry.contribution = contribution;
      } catch (e) {
        // one paper's guide failing must not break the whole map
      }
    });
    truncatedAnalysis = Math.max(0, scopedPages.length - targets.length);
  }

  // `_meta` is non-contract diagnostics (ignored by the page); it records the
  // scope + any analysis cap hit, so coverage is never silently truncated.
  const graph = { problems, concepts, relations, gaps, paper_dates, papers_meta };
  graph._meta = {
    collection: collectionFilter || "(all)",
    papers_total: paperPages.length,
    papers_in_scope: scopedPages.length,
    problems: problems.length,
    concepts: concepts.length,
    relations: relations.length,
    gaps: gaps.length,
    analysis_truncated: truncatedAnalysis,
  };
  return graph;
}
