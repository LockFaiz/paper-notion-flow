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
paper-notion-flow map build    # extract + sync nodes/edges/gaps to Notion (steps 2–3)
paper-notion-flow map render   # build landscape.html from Notion + optionally serve (step 4)
```

`map init` creates the databases under a Notion page you own (and that is shared
with your integration), then prints the ids — `--write-env` upserts them into
`./.env`. Run `map check` afterwards to confirm access.

`map build` flags mirror `sync-zotero`: `--collection`, `--since-hours`,
`--force`, `--data-dir`, prompt-override.

## Rendering

- `landscape.html` is a static file using **Cytoscape.js** (real force /
  hierarchical / timeline layouts, zoom, 100s of nodes — the chat mockup used
  hand-placed SVG only to convey interaction).
- Node color encodes type (problem = purple, concept = teal, gap = amber), size
  encodes linked-paper count; edges carry the relation type.
- Right-hand detail inspector shows the selected node's papers, related nodes,
  and gaps, plus "Open Notion page".
- Served from `localhost`; a link is embedded on a Notion Landscape page so the
  map is reachable from inside the workspace.

## Plugin wiring (step 5)

- New Tools/menu action **"Open Research Map"** → runs `map build` (optional) →
  `map render` → opens the local page, reusing the existing runtime command
  builder (WSL / native).
- Settings: which collection to map, default layout, auto-rebuild toggle.

## Roadmap

- [x] 1. Schema + scaffolding — Pydantic models, config, `map` CLI group (`init`/`check`), this doc
- [ ] 2. Extraction — prompt + `map build` produces ResearchMapExtraction JSON
- [ ] 3. Notion sync — write/dedup Problems/Concepts/Relations/Gaps
- [ ] 4. Render — `map render` → Cytoscape `landscape.html` + local serve
- [ ] 5. Plugin wiring — "Open Research Map" action + settings
- [ ] 6. Polish — view modes, gap highlighting, timeline layout, inspector
