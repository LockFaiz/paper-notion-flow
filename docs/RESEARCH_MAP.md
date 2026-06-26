# Research Map (Notion-managed visualization)

Status: in development on `feature/research-map`. This document is the design of
record; the chat mockup `research_map_interactive_ui` defines the interaction.

## Goal

Turn a PNF paper library into an interactive **research map**: a graph of the
research *problems* a field is tackling, the *concepts/methods* used to attack
them, the typed *relations* between them, and the *gaps* nobody has closed yet.

Notion is the source of truth. The graph is a read-rendering of Notion data, so
the user can edit/curate nodes in Notion and the map reflects it.

## Data model — 5 Notion databases

| DB | Role | Key properties |
| --- | --- | --- |
| **Papers** | existing PNF paper collection | title, authors, topic, Zotero Key … |
| **Problems** | research questions / objectives | `Name` (title), `Description`, `Status`, `Papers` (relation) |
| **Concepts** | methods, models, techniques (graph nodes) | `Name` (title), `Kind` (select: method/model/dataset/metric/concept), `Description`, `Papers` (relation), `Problems` (relation) |
| **Relations** | typed edges between nodes | `Name` (title), `Source`, `Target` (rich text = node name; an edge can link a Problem *or* a Concept, and a Notion relation targets only one DB), `Type` (select: addresses/builds-on/uses/contradicts/evaluates), `Rationale` |
| **Gaps** | unaddressed problems / untested claims | `Name` (title), `Rationale`, `Related` (relation to Concepts/Problems), `Papers` (relation) |

Edges in the graph are Notion `Relations` rows (and the relation properties on
Concepts/Problems/Gaps). Re-running `map build` dedups by normalized node name.

## Pipeline

```
Zotero papers ──(existing PNF sync)──▶ Notion Papers DB
        │
        ▼  paper-notion-flow map build
Local AI CLI reads each paper's guide/abstract
        │     → extracts {problems, concepts, relations, gaps}  (ResearchMapExtraction)
        ▼  merge + dedup against existing Notion rows (normalized name, like fingerprints)
Notion Problems / Concepts / Relations / Gaps DBs populated/updated
        │
        ▼  paper-notion-flow map render
landscape.html (Cytoscape.js) reads the 5 DBs via Notion API
        │
        ▼  served on localhost + linked from a Notion "Landscape" page
Interactive graph (view modes, detail inspector, gap highlighting)
```

Reuses existing PNF machinery: the local-CLI command builder (`ai._run_local_command`
+ `_extract_json`), the `Settings`/`Client` Notion access, and the `SyncState`
dedup pattern. No new external services; everything is local + the user's Notion.

## CLI

```
paper-notion-flow map init --parent <page-id> [--write-env]   # auto-create the 4 DBs + Landscape page (step 1)
paper-notion-flow map check    # validate the 5 DB ids + token (step 1)
paper-notion-flow map build    # extract + consolidate nodes/edges/gaps -> graph.json (step 2)
paper-notion-flow map sync     # write graph.json into the 4 databases (step 3; --dry-run to preview)
paper-notion-flow map render   # build landscape.html from Notion + optionally serve (step 4)
```

`map init` creates the databases under a Notion page you own (and that is shared
with your integration), then prints the ids — `--write-env` upserts them into
`./.env`. Run `map check` afterwards to confirm access.

`map build` reads the Papers database (filter with `--collection`), extracts per
paper (guide text preferred, abstract fallback), caches + dedups into
`<data-dir>/research-map/graph.json`, and is incremental (re-extracts only papers
whose Notion `last_edited` changed; `--force` re-extracts all). Node names use the
configured guide language (well-known acronyms kept in English). After per-paper
extraction a global consolidation pass asks the AI to cluster synonymous nodes
into canonical names, so the same concept from different papers becomes one node.

## Rendering

The frontend is the **Claude-Design handoff page** `research-map.html` — a
self-contained, zero-dependency, responsive (dark-mode aware) page that is driven
entirely by a single global `window.GRAPH`. The design is hi-fi and final; we do
**not** edit its UI. Source of record for the page is
`docs/research-map-handoff/` (README / DATA_CONTRACT / TASKS); the page itself
lives at `src/paper_notion_flow/templates/research-map.html`.

- `map build` now emits the **complete `window.GRAPH`** into `graph.json`:
  `problems / concepts / relations / gaps` (already matched the contract) plus
  `paper_dates` and `papers_meta`. Papers are keyed by **Zotero itemKey**
  throughout (stable across title changes); `papers_meta[key]` carries
  title/authors/venue/abstract/tags/contribution and the Zotero PDF deep link
  (`zotero://open-pdf/library/items/<attachmentKey>`), the Notion page URL, and
  the AI guide parsed into structured `analysis: [{h, b}]` sections.
- `papers_meta` is assembled by joining the Notion Papers DB (itemKey, notion_url,
  title) with the **Zotero SQLite** reader (authors/venue/tags/PDF attachment) and
  the Notion guide subpage (analysis/contribution). Every field is optional — the
  page degrades gracefully per `DATA_CONTRACT.md`.
- `map render` injects `window.GRAPH` (from `graph.json`) before the page's first
  `<script>`; the page's built-in demo (`window.GRAPH = window.GRAPH || {…}`) is
  thereby superseded without editing the page. `--serve` hosts it on localhost.
- Semantic colors (fixed in the page): problem = purple, concept = teal, gap =
  amber. The detail drawer shows PDF / AI analysis / graph position / related
  papers; "open PDF" and "open Notion" are the only external actions.

## Plugin wiring (step 5)

Two **Tools-menu** actions (library-wide, reusing the existing runtime command
builder for WSL / native):

- **Open Research Map** — `map render` from the existing `graph.json`, then opens
  the self-contained `research-map.html` in the system browser via
  `Zotero.launchURL(file://…)`. Fast; if no `graph.json` exists yet it prompts to
  rebuild first.
- **Rebuild Research Map** — `map build` (AI extraction; slow) for the collection
  currently selected in the library pane, or the whole library when none is
  selected, then renders and opens.

The page file is read from `<workspace>/data/research-map/research-map.html` (the
workspace is on the Windows drive, so the Windows path is handed straight to the
browser). No new settings pane controls — the mapped scope follows the pane
selection.

## Roadmap

- [x] 1. Schema + scaffolding — Pydantic models, config, `map` CLI group (`init`/`check`), this doc
- [x] 2. Extraction — `map build`: per-paper extraction, cache + dedup → graph.json
- [x] 3. Notion sync — `map sync` upserts Problems/Concepts/Relations/Gaps by name, links Papers
- [x] 4. Render — `map build` emits full `window.GRAPH` (itemKey-keyed, +paper_dates/papers_meta); `map render` injects it into the handoff `research-map.html` (+ `--serve`)
- [x] 5. Plugin wiring — Tools-menu "Open Research Map" + "Rebuild Research Map" (render/build via the runtime command builder, open in the system browser)
- [ ] 6. Polish — analysis parsing fidelity, Cloudflare private site (same GRAPH contract via `/api/graph`)
