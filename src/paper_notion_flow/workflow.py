from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ai import apply_inferred_metadata, build_reading_guide, last_run_model_effort
from .config import Settings
from .extract import download_pdf, prepare_pdf_for_cli
from .models import DocumentRecord
from .notion_writer import NotionWriter
from .state import SyncState
from .zotero import ZoteroLibrary


def import_pdf_document(
    *,
    settings: Settings,
    data_dir: Path,
    pdf_url: str | None,
    pdf_path: str | None,
    title: str | None,
    doc_type: str,
    skip_ai: bool,
    prompt_override: str | None,
) -> str:
    pdf_dir = data_dir / "pdfs"
    if pdf_url:
        local_pdf_path = download_pdf(pdf_url, pdf_dir)
        source_url = pdf_url
    else:
        local_pdf_path = Path(pdf_path).expanduser().resolve()
        if not local_pdf_path.exists():
            raise RuntimeError(f"PDF not found: {local_pdf_path}")
        source_url = None

    record = DocumentRecord(
        source_kind="local_pdf",
        title=title or local_pdf_path.stem,
        item_type=doc_type,
        source_url=source_url,
        pdf_path=local_pdf_path,
    )
    return _process_record(
        settings=settings,
        data_dir=data_dir,
        record=record,
        skip_ai=skip_ai,
        prompt_override=prompt_override,
    )


def process_zotero_item(
    *,
    settings: Settings,
    data_dir: Path,
    item_key: str,
    skip_ai: bool,
    force: bool,
    prompt_override: str | None,
    preset_name: str | None = None,
) -> str:
    library = ZoteroLibrary(settings.zotero_data_dir)
    record = library.get_document_by_key(item_key)
    if not record:
        raise RuntimeError(f"Could not find Zotero item: {item_key}")

    state = SyncState(data_dir / "state" / "zotero_state.json")
    if not force and state.was_processed(record.stable_id, record.date_modified):
        return f"Skipped {record.title} ({record.stable_id}) because it is already up to date."

    result = _process_record(
        settings=settings,
        data_dir=data_dir,
        record=record,
        skip_ai=skip_ai,
        prompt_override=prompt_override,
        preset_name=preset_name,
    )
    state.mark_processed(record.stable_id, record.date_modified)
    return result


def sync_zotero(
    *,
    settings: Settings,
    data_dir: Path,
    since_hours: float,
    collections: list[str],
    skip_ai: bool,
    force: bool,
    prompt_override: str | None,
) -> str:
    library = ZoteroLibrary(settings.zotero_data_dir)
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    records = library.iter_recent_documents(since=since, collection_names=collections)
    state = SyncState(data_dir / "state" / "zotero_state.json")

    processed = []
    skipped = []
    for record in records:
        if not force and state.was_processed(record.stable_id, record.date_modified):
            skipped.append(record.stable_id)
            continue
        processed.append(
            _process_record(
                settings=settings,
                data_dir=data_dir,
                record=record,
                skip_ai=skip_ai,
                prompt_override=prompt_override,
            )
        )
        state.mark_processed(record.stable_id, record.date_modified)

    return (
        f"Processed {len(processed)} document(s), skipped {len(skipped)} already-synced item(s).\n"
        + "\n".join(processed[:20])
    )


def prune_zotero_deletions(
    *,
    settings: Settings,
    data_dir: Path,
    collections: list[str],
    dry_run: bool = False,
) -> str:
    library = ZoteroLibrary(settings.zotero_data_dir)
    state = SyncState(data_dir / "state" / "zotero_state.json")
    current_keys = library.existing_document_keys(collection_names=collections)
    stale_keys = [key for key in state.processed_ids() if key not in current_keys]

    writer = NotionWriter(settings)
    archived_count = 0
    archived_keys: list[str] = []
    for key in stale_keys:
        if not dry_run:
            archived_count += writer.archive_document_by_zotero_key(key)
            state.remove_processed(key)
        else:
            archived_count += 1
        archived_keys.append(key)

    notion_archived_count, notion_archived_keys = writer.archive_documents_missing_from_zotero(
        current_zotero_keys=current_keys,
        collection_names=collections,
        dry_run=dry_run,
    )
    archived_count += notion_archived_count
    archived_keys.extend(notion_archived_keys)

    if not archived_count:
        return "PRUNE_MODE:DRY_RUN\nNo deleted Zotero items would be removed from Notion." if dry_run else "No deleted Zotero items needed to be removed from Notion."

    unique_keys = []
    for key in archived_keys:
        if key and key not in unique_keys:
            unique_keys.append(key)

    action = "Would archive" if dry_run else "Archived"
    mode = "PRUNE_MODE:DRY_RUN" if dry_run else "PRUNE_MODE:ARCHIVE"
    return f"{mode}\n{action} {archived_count} Notion page(s) for deleted Zotero item(s): " + ", ".join(unique_keys[:20])


def check_notion(settings: Settings) -> str:
    writer = NotionWriter(settings)
    return writer.check_database()


def _process_record(
    *,
    settings: Settings,
    data_dir: Path,
    record: DocumentRecord,
    skip_ai: bool,
    prompt_override: str | None,
    preset_name: str | None = None,
) -> str:
    source_text, source_pdf_path = _prepare_record_source(data_dir, record)

    guide = None
    if not skip_ai:
        guide = build_reading_guide(
            settings,
            record,
            source_text,
            source_pdf_path=source_pdf_path,
            prompt_override=prompt_override,
        )
        apply_inferred_metadata(record, guide)

    variant_label = None
    variant_date = None
    if guide is not None:
        model, effort = last_run_model_effort(settings)
        variant_label = " · ".join(part for part in (preset_name, model, effort) if part) or None
        variant_date = datetime.now().strftime("%m-%d")

    writer = NotionWriter(settings)
    result = writer.sync_document(
        record=record,
        guide=guide,
        extracted_markdown=source_text,
        variant_label=variant_label,
        variant_date=variant_date,
    )

    source_label = f"pdf {source_pdf_path}" if source_pdf_path else "metadata fallback"
    lines = [
        f"ZOTERO_KEY:{record.zotero_key or ''}",
        f"PAPER_PAGE_ID:{result.paper_page_id}",
        f"PAPER_PAGE_URL:{result.paper_page_url}",
        f"GUIDE_PAGE_ID:{result.guide_page_id or ''}",
        f"GUIDE_PAGE_URL:{result.guide_page_url or ''}",
        f"AI_STATUS:{'Done' if guide else 'Metadata Only'}",
        f"SOURCE:{source_label}",
        f"TITLE:{record.title}",
    ]
    return "\n".join(lines)


def _prepare_record_source(data_dir: Path, record: DocumentRecord) -> tuple[str, Path | None]:
    if record.pdf_path and record.pdf_path.exists():
        pdf_path = prepare_pdf_for_cli(
            record.pdf_path,
            data_dir / "pdf-inputs",
            record.content_id,
        )
        return _record_context(record), pdf_path

    return _record_context(record), None


def _record_context(record: DocumentRecord) -> str:
    fallback_parts = [record.title]
    if record.abstract:
        fallback_parts.append(record.abstract)
    if record.source_url:
        fallback_parts.append(f"Source URL: {record.source_url}")
    return "\n\n".join(part for part in fallback_parts if part).strip()
