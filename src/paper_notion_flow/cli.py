from __future__ import annotations

import argparse
import time
from pathlib import Path

from .config import Settings
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
