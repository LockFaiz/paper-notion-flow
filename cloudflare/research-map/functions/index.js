// GET / — render the research map live from Notion.
//
// 1. fetch the static research-map.html template (ASSETS binding)
// 2. assemble window.GRAPH from Notion (functions/_graph.js)
// 3. inject it before the page's first <script> (the page's built-in
//    `window.GRAPH = window.GRAPH || {…demo…}` then keeps our value)
// 4. cache the rendered HTML at the edge for GRAPH_CACHE_SECONDS
//
// Cloudflare Access (configured in the dashboard) gates this in front of the
// function, so only your email can reach it. `?refresh=1` bypasses the cache.

import { buildGraph } from "./_graph.js";

const EMPTY_GRAPH = {
  problems: [],
  concepts: [],
  relations: [],
  gaps: [],
  paper_dates: {},
  papers_meta: {},
};

// KaTeX, loaded from CDN and auto-rendering `$…$` / `$$…$$`. The detail drawer
// is built dynamically when a paper is clicked, so a MutationObserver re-renders
// any newly-inserted nodes. This lives entirely in the injected block — the
// designed page (research-map.html) is not modified.
const KATEX_BLOCK = `
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" crossorigin="anonymous">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js" crossorigin="anonymous"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" crossorigin="anonymous"></script>
<script>
(function () {
  var OPTS = { delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false }
  ], throwOnError: false, ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"] };
  var observer = null, pending = false;
  function renderAll() {
    pending = false;
    if (!window.renderMathInElement) return;
    // Disconnect while rendering: KaTeX edits the DOM, and an attached observer
    // would re-fire on its own output → render storm → tab crash. Re-rendering
    // the whole body is idempotent (rendered math has no $ left to match).
    if (observer) observer.disconnect();
    try { window.renderMathInElement(document.body, OPTS); } catch (e) {}
    if (observer) observer.observe(document.body, { childList: true, subtree: true });
  }
  function schedule() {
    if (pending) return;
    pending = true;
    (window.requestAnimationFrame || window.setTimeout)(renderAll);
  }
  function start() {
    renderAll();
    observer = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        if (muts[i].addedNodes && muts[i].addedNodes.length) { schedule(); return; }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  // auto-render.js is deferred; poll briefly until it's ready, then start.
  var tries = 0;
  var t = setInterval(function () {
    if (window.renderMathInElement) { clearInterval(t); start(); }
    else if (tries++ > 80) { clearInterval(t); }
  }, 75);
})();
</script>
`;

function injectGraph(template, graph) {
  // Escape `<` so a stray "</script>" inside guide text can't break out.
  const json = JSON.stringify(graph).replace(/</g, "\\u003c");
  const injection = `${KATEX_BLOCK}<script>window.GRAPH = ${json};</script>\n`;
  const idx = template.indexOf("<script>");
  if (idx === -1) return template + injection;
  return template.slice(0, idx) + injection + template.slice(idx);
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const refresh = url.searchParams.has("refresh");
  const ttl = Number(env.GRAPH_CACHE_SECONDS ?? "600");

  // Cache key is independent of Access cookies/query, so every allowed user
  // shares one cached render.
  const cacheKey = new Request(new URL("/__render", url.origin).toString(), { method: "GET" });
  const cache = caches.default;
  if (!refresh && ttl > 0) {
    const hit = await cache.match(cacheKey);
    if (hit) return hit;
  }

  const templateRes = await env.ASSETS.fetch(new URL("/research-map.html", url.origin).toString());
  if (!templateRes.ok) {
    return new Response("research-map.html asset not found", { status: 500 });
  }
  const template = await templateRes.text();

  let graph;
  let ok = true;
  try {
    graph = await buildGraph(env);
  } catch (e) {
    ok = false;
    graph = { ...EMPTY_GRAPH, _meta: { error: String(e && e.message ? e.message : e) } };
  }

  const html = injectGraph(template, graph);
  const res = new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": ok && ttl > 0 ? `public, max-age=${ttl}` : "no-store",
    },
  });
  if (ok && ttl > 0) context.waitUntil(cache.put(cacheKey, res.clone()));
  return res;
}
