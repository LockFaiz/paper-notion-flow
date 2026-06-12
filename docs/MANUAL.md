# Paper Flow Manual

**English** | [简体中文](MANUAL.zh-CN.md)

Open the settings via **Tools → Paper Flow Settings** in Zotero (or Edit → Settings → Paper Flow).

## Automatic processing

| Setting | Meaning |
| --- | --- |
| Enable Paper Flow plugin | Master switch. |
| Automatically process new or updated items | Watcher mode: new/changed Zotero items are queued and processed after the debounce delay. |
| Show progress popups | Toast notifications for runs. |
| Skip auto-processing when content has not changed | Fingerprints title/authors/date/DOI/abstract/PDF. Sync write-backs (which only touch metadata) never trigger a redundant AI re-run. Manual runs always process. |
| Only process items from this collection | Optional filter. Use the buttons to pick the currently selected Zotero collection. |
| Sync Zotero notes | Copies Zotero notes into a Notes child page. |
| Preview deleted-item cleanup | Dry-run before archiving Notion pages of deleted Zotero items. |
| Debounce seconds | Wait time after a Zotero change before launching. |
| Process timeout | A hung CLI run is killed after this many seconds (default 1800). |

## AI CLI

| Setting | Meaning |
| --- | --- |
| Version banner | Always shows the selected CLI's installed vs. latest version. Outdated + auto-update on → updates itself in the background. |
| Config line | The exact configuration that will run: tool · model · effort · context window. |
| Runtime | `Auto` (Windows→WSL, macOS/Linux→native), `Native shell`, or `WSL`. In WSL mode all Windows `/mnt/*` paths are stripped — the CLI must be installed inside the distro. |
| Reading-guide language | Output language of the guide (7 languages). |
| Workspace path | The folder containing this repo's `pyproject.toml`. Use the runtime's path style (WSL: `/mnt/c/...`). |
| Notion token / database ID | Override the workspace `.env`. Leave empty to use `.env` instead. The database ID (not a secret) syncs across devices through your Zotero account; the token never syncs — paste it once per device, and regenerate it at notion.so/my-integrations if it ever leaks. |
| AI tool | Codex CLI or Claude Code. |
| Model | Dropdown is populated from the CLIs' **official local caches** (claude: `/model` menu cache incl. limited-time models with exact ids; codex: `models_cache.json`). You can also type any model name. Empty = CLI default. Claude aliases (`opus`/`sonnet`/`haiku`) always track the latest version. |
| Reasoning effort | For codex, the levels offered follow the selected model's supported levels. Empty = default. |
| Keep CLI up to date | Once-a-day background update of the selected CLI; also triggered immediately when the banner detects an outdated version. |
| Refresh model list | Re-reads the official model caches and the version status. |

## Prompt presets

Save up to 10 custom reading-guide prompts. Select one to load it; **Set as default** (★) re-applies it on every startup, so it survives plugin updates and restarts. "Fill default prompt" inserts your default preset (or the built-in template if none is set).

Do **not** ask the model to report its own model/effort/token usage in your prompt — that metadata is appended programmatically as a "Generation info" footer (real model, effort, token count, and cost for Claude).

## Math rendering

The pipeline instructs the model to write all math as LaTeX (`$...$` inline, `$$...$$` display). The Notion writer converts these into native Notion equations (KaTeX), so formulas render with proper typography.

## Troubleshooting

Run **Check Paper Flow setup** and look for the first failing marker:

| Marker | Fix |
| --- | --- |
| `WORKSPACE:MISSING` | Workspace path must point at the folder containing `pyproject.toml`, in the runtime's path style. |
| `UV:MISSING` | Install [uv](https://docs.astral.sh/uv/) inside the selected runtime (WSL users: inside WSL). |
| `PAPER_NOTION_FLOW:MISSING` / `ModuleNotFoundError` | Run `uv sync` in the workspace. |
| `NOTION_TOKEN:MISSING` / `NOTION_DATABASE_ID:MISSING` | Fill the plugin fields or workspace `.env`. Plugin fields win when non-empty; empty plugin fields fall back to `.env`. |
| `NOTION_SCHEMA:...` errors / `object_not_found` | Connect your integration to the database: database page → `⋯` → Connections. |
| `ZOTERO_DB:MISSING` | Set `ZOTERO_DATA_DIR` in `.env` to your Zotero data directory. |
| `AI_PATH_SCOPE:WINDOWS_LEAK` | The CLI resolved to a Windows binary from inside WSL. Install it inside WSL (`npm i -g @openai/codex` in the distro). |
| `SELECTED_AI:MISSING` | The chosen CLI is not installed in the selected runtime. |
| `model ... requires a newer version` | The CLI is outdated. The banner auto-updates it (or click "Update CLI now"). If you use nvm with multiple node versions, remove stale installs from non-default versions. |
| `CLAUDE_UPDATE`/`CODEX_UPDATE:AVAILABLE` | Informational; auto-update will handle it. |

Every failure also writes a full diagnostic to a temp file (path shown in the status box) and copies it to the clipboard.
