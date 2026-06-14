from __future__ import annotations

import argparse
import time
from pathlib import Path

from .config import Settings
from .research_map import build_research_map, check_map_setup, init_research_map, render_research_map
from .workflow import check_notion, import_pdf_document, process_zotero_item, prune_zotero_deletions, sync_zotero


def _add_prompt_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prompt-override",
        help="Optional custom reading-guide prompt suffix. Appended after the built-in instructions.",
    )
    parser.add_argument(
        "--prompt-override-file",
        help="Read an optional custom reading-guide prompt suffix from a UTF-8 text file.",
    )


def _resolve_prompt_override(args: argparse.Namespace) -> str | None:
    prompt_override = getattr(args, "prompt_override", None)
    prompt_override_file = getattr(args, "prompt_override_file", None)
    if prompt_override:
        return args.prompt_override
    if prompt_override_file:
        return Path(prompt_override_file).read_text(encoding="utf-8").strip()
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync Zotero papers into Notion child pages and generate local-AI reading guides."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Import one local PDF or PDF URL into Notion.")
    source_group = import_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--pdf-url", help="URL of the PDF to import.")
    source_group.add_argument("--pdf-path", help="Local path to the PDF.")
    import_parser.add_argument("--title", help="Optional title override.")
    import_parser.add_argument("--doc-type", default="Whitepaper", help="Doc type written to Notion when possible.")
    import_parser.add_argument("--skip-ai", action="store_true", help="Skip AI reading guide generation.")
    import_parser.add_argument("--data-dir", default="data", help="Directory for downloaded PDFs, markdown, and state.")
    _add_prompt_override_args(import_parser)

    sync_parser = subparsers.add_parser("sync-zotero", help="Process recent Zotero items and write guides to Notion.")
    sync_parser.add_argument("--since-hours", type=float, default=24.0, help="How far back to scan Zotero changes.")
    sync_parser.add_argument("--collection", action="append", default=[], help="Limit processing to one or more collections.")
    sync_parser.add_argument("--data-dir", default="data", help="Directory for markdown and state.")
    sync_parser.add_argument("--skip-ai", action="store_true", help="Skip AI reading guide generation.")
    sync_parser.add_argument("--force", action="store_true", help="Reprocess items even if they were already handled.")
    _add_prompt_override_args(sync_parser)

    item_parser = subparsers.add_parser("process-zotero-item", help="Process one Zotero item by key.")
    item_parser.add_argument("--key", required=True, help="Zotero item key.")
    item_parser.add_argument("--data-dir", default="data", help="Directory for markdown and state.")
    item_parser.add_argument("--skip-ai", action="store_true", help="Skip AI reading guide generation.")
    item_parser.add_argument("--force", action="store_true", help="Reprocess even if already handled.")
    _add_prompt_override_args(item_parser)

    prune_parser = subparsers.add_parser(
        "prune-zotero-deletions",
        help="Archive Notion pages for Zotero items that were deleted locally.",
    )
    prune_parser.add_argument("--collection", action="append", default=[], help="Limit cleanup to one or more collections.")
    prune_parser.add_argument("--data-dir", default="data", help="Directory for markdown and state.")
    prune_parser.add_argument("--dry-run", action="store_true", help="Preview deleted-item cleanup without archiving pages.")

    subparsers.add_parser("check-notion", help="Validate Notion token, database access, and useful Paper Flow properties.")

    map_parser = subparsers.add_parser("map", help="Build and render the Notion-managed research map.")
    map_sub = map_parser.add_subparsers(dest="map_command", required=True)

    map_init = map_sub.add_parser("init", help="Create the 4 research-map databases + Landscape page via the Notion API.")
    map_init.add_argument("--parent", required=True, help="Notion page id (or URL) to create the databases under.")
    map_init.add_argument("--write-env", action="store_true", help="Write the resulting ids into ./.env.")

    map_sub.add_parser("check", help="Validate the research-map Notion databases and token.")

    map_build = map_sub.add_parser("build", help="Extract problems/concepts/relations/gaps and sync to Notion.")
    map_build.add_argument("--collection", action="append", default=[], help="Limit to one or more collections.")
    map_build.add_argument("--data-dir", default="data", help="Directory for markdown and state.")
    map_build.add_argument("--force", action="store_true", help="Re-extract even if a paper was already mapped.")
    _add_prompt_override_args(map_build)

    map_render = map_sub.add_parser("render", help="Render landscape.html from the Notion databases.")
    map_render.add_argument("--data-dir", default="data", help="Directory for the rendered landscape file.")
    map_render.add_argument("--output", help="Output path for landscape.html (default: <data-dir>/landscape.html).")
    map_render.add_argument("--serve", action="store_true", help="Serve the rendered map on localhost after building.")

    watch_parser = subparsers.add_parser("watch-zotero", help="Poll Zotero and process new or changed items.")
    watch_parser.add_argument("--since-hours", type=float, default=6.0, help="Initial lookback window.")
    watch_parser.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds.")
    watch_parser.add_argument("--collection", action="append", default=[], help="Limit processing to one or more collections.")
    watch_parser.add_argument("--data-dir", default="data", help="Directory for markdown and state.")
    watch_parser.add_argument("--skip-ai", action="store_true", help="Skip AI reading guide generation.")
    _add_prompt_override_args(watch_parser)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = Settings()
    prompt_override = _resolve_prompt_override(args)

    if args.command == "import":
        result = import_pdf_document(
            settings=settings,
            data_dir=Path(args.data_dir),
            pdf_url=args.pdf_url,
            pdf_path=args.pdf_path,
            title=args.title,
            doc_type=args.doc_type,
            skip_ai=args.skip_ai,
            prompt_override=prompt_override,
        )
        print(result)
        return

    if args.command == "sync-zotero":
        result = sync_zotero(
            settings=settings,
            data_dir=Path(args.data_dir),
            since_hours=args.since_hours,
            collections=args.collection,
            skip_ai=args.skip_ai,
            force=args.force,
            prompt_override=prompt_override,
        )
        print(result)
        return

    if args.command == "process-zotero-item":
        result = process_zotero_item(
            settings=settings,
            data_dir=Path(args.data_dir),
            item_key=args.key,
            skip_ai=args.skip_ai,
            force=args.force,
            prompt_override=prompt_override,
        )
        print(result)
        return

    if args.command == "prune-zotero-deletions":
        result = prune_zotero_deletions(
            settings=settings,
            data_dir=Path(args.data_dir),
            collections=args.collection,
            dry_run=args.dry_run,
        )
        print(result)
        return

    if args.command == "check-notion":
        print(check_notion(settings))
        return

    if args.command == "map":
        if args.map_command == "init":
            print(
                init_research_map(
                    settings=settings,
                    parent_page_id=args.parent,
                    write_env=args.write_env,
                )
            )
            return
        if args.map_command == "check":
            print(check_map_setup(settings))
            return
        if args.map_command == "build":
            print(
                build_research_map(
                    settings=settings,
                    data_dir=Path(args.data_dir),
                    collections=args.collection,
                    force=args.force,
                    prompt_override=prompt_override,
                )
            )
            return
        if args.map_command == "render":
            print(
                render_research_map(
                    settings=settings,
                    data_dir=Path(args.data_dir),
                    output=Path(args.output) if args.output else None,
                    serve=args.serve,
                )
            )
            return
        return

    if args.command == "watch-zotero":
        while True:
            result = sync_zotero(
                settings=settings,
                data_dir=Path(args.data_dir),
                since_hours=args.since_hours,
                collections=args.collection,
                skip_ai=args.skip_ai,
                force=False,
                prompt_override=prompt_override,
            )
            print(result)
            time.sleep(args.poll_seconds)
