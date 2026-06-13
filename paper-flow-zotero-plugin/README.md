# Paper Flow Zotero Plugin

The Zotero-side automation layer of [Paper Notion Flow](../README.md). It watches Zotero events and launches the local `paper-notion-flow` Python CLI — it never calls Notion or AI APIs directly.

User documentation lives in the repo root: [README](../README.md) · [Manual](../docs/MANUAL.md) ([中文](../docs/MANUAL.zh-CN.md)).

## What it does

- Right-click a paper for **Paper Flow: Sync + AI guide**, **Sync metadata only**, **Open Notion page**, and **Open Reading Guide**; right-click a collection for **Sync collection + AI** / **Sync collection metadata only**; **Paper Flow Settings** lives in the Tools menu.
- Watches `item` / `collection-item` events with debouncing, a serial run queue, a process timeout, and content fingerprinting (sync write-backs don't trigger re-runs).
- Builds cross-platform runtime commands (Windows WSL / native Windows / macOS / Linux). Unix runtimes execute a generated script file via `bash -l`; in WSL mode all `/mnt/*` Windows PATH entries are stripped so resolution stays inside the distro.
- Manages the AI CLI: version banner with background auto-update (per-tool, throttled), model dropdown fed by the CLIs' official local model caches, model-aware effort levels, and an always-visible config line (tool · model · effort · context window).
- Prompt presets (max 10) with a startup-reapplied default.
- Setup check with machine-readable markers (`WORKSPACE:OK`, `NOTION_TOKEN:OK`, `AI_PATH_SCOPE:NATIVE`, …) and one-click diagnostics copy.
- Preference UI localized in 7 languages with dark-mode support.

## Build

```bash
npm install
npm run build       # builds + typechecks; xpi at .scaffold/build/paper-flow.xpi
npm run lint:check  # prettier + eslint
```

Install the xpi via Zotero → Tools → Plugins → gear icon → Install Plugin From File, then restart Zotero.

Releases are built automatically by CI from version tags — see [.github/workflows](../.github/workflows).

## Source map

| File                              | Responsibility                                                                                            |
| --------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `src/modules/paperFlow.ts`        | Core: notifier handling, command building/quoting per runtime, model discovery, CLI updates, setup check. |
| `src/modules/preferenceScript.ts` | Preference pane wiring: banner, config line, dropdown rebuild rules, presets.                             |
| `addon/content/preferences.xhtml` | Card-based settings UI.                                                                                   |
| `addon/locale/*`                  | 7-language Fluent files (`addon.ftl` for runtime strings, `preferences.ftl` for the pane).                |

## License

AGPL-3.0-or-later
