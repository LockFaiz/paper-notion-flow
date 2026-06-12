from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .models import AttachmentRecord, DocumentRecord, NoteRecord


IGNORE_ITEM_TYPES = {"annotation", "note"}
PAPER_FLOW_NOTION_LINK_TITLE = "Paper Flow Notion"


class ZoteroLibrary:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "zotero.sqlite"
        if not self.db_path.exists():
            raise RuntimeError(f"Could not find Zotero database: {self.db_path}")

    def iter_recent_documents(self, *, since: datetime, collection_names: list[str] | None = None) -> list[DocumentRecord]:
        since_text = since.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        item_ids: dict[int, str] = {}
        standalone_ids: dict[int, str] = {}

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT i.itemID, it.typeName, i.dateModified, ia.parentItemID, ia.contentType
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN itemAttachments ia ON ia.itemID = i.itemID
                WHERE i.dateModified >= ?
                ORDER BY i.dateModified DESC
                """,
                (since_text,),
            )
            for item_id, type_name, date_modified, parent_item_id, content_type in cur.fetchall():
                if type_name in IGNORE_ITEM_TYPES:
                    continue
                if type_name == "attachment":
                    if content_type != "application/pdf":
                        continue
                    if parent_item_id:
                        item_ids[parent_item_id] = date_modified
                    else:
                        standalone_ids[item_id] = date_modified
                else:
                    item_ids[item_id] = date_modified

        records: list[DocumentRecord] = []
        for item_id, date_modified in item_ids.items():
            record = self._build_parent_record(item_id, date_modified)
            if record and self._matches_collections(record, collection_names):
                records.append(record)

        for item_id, date_modified in standalone_ids.items():
            record = self._build_standalone_attachment_record(item_id, date_modified)
            if record and self._matches_collections(record, collection_names):
                records.append(record)

        records.sort(key=lambda record: record.date_modified or "", reverse=True)
        return records

    def get_document_by_key(self, item_key: str) -> DocumentRecord | None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT i.itemID, it.typeName, ia.parentItemID, i.dateModified
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN itemAttachments ia ON ia.itemID = i.itemID
                WHERE i.key = ?
                """,
                (item_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            item_id, type_name, parent_item_id, date_modified = row

        if type_name == "attachment" and parent_item_id is None:
            return self._build_standalone_attachment_record(item_id, date_modified)
        if type_name == "attachment" and parent_item_id:
            return self._build_parent_record(parent_item_id, date_modified)
        if type_name in IGNORE_ITEM_TYPES:
            return None
        return self._build_parent_record(item_id, date_modified)

    def existing_document_keys(self, *, collection_names: list[str] | None = None) -> set[str]:
        records = self.iter_recent_documents(
            since=datetime(1970, 1, 1, tzinfo=timezone.utc),
            collection_names=collection_names,
        )
        return {record.zotero_key for record in records if record.zotero_key}

    def _connect(self) -> sqlite3.Connection:
        raw_path = self.db_path.as_posix()
        if re.match(r"^[A-Za-z]:/", raw_path):
            raw_path = f"/{raw_path}"
        uri = f"file:{quote(raw_path)}?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True)

    def _build_parent_record(self, item_id: int, date_modified: str) -> DocumentRecord | None:
        meta = self._base_item_meta(item_id)
        if not meta:
            return None

        attachments = self._pdf_attachments(item_id)
        title = meta["fields"].get("title") or (attachments[0].file_path.stem if attachments and attachments[0].file_path else meta["key"])
        file_path = _first_path_text(attachments)
        notion_link = self._paper_flow_link(item_id)
        return DocumentRecord(
            source_kind="zotero_metadata",
            title=title,
            item_type=meta["type_name"],
            zotero_item_id=item_id,
            zotero_key=meta["key"],
            zotero_select_uri=f"zotero://select/library/items/{meta['key']}",
            source_url=meta["fields"].get("url"),
            abstract=meta["fields"].get("abstractNote"),
            authors=self._authors(item_id),
            collections=self._collections(item_id),
            tags=self._tags(item_id),
            date=meta["fields"].get("date"),
            date_added=meta["date_added"],
            date_modified=date_modified,
            doi=meta["fields"].get("DOI"),
            publication=meta["fields"].get("publicationTitle") or meta["fields"].get("journalAbbreviation"),
            proceedings_title=meta["fields"].get("proceedingsTitle"),
            citation_key=_citation_key(meta["fields"].get("extra")),
            extra=meta["fields"].get("extra"),
            file_path=file_path,
            notion_page_url=notion_link,
            notion_page_id=_notion_page_id_from_url(notion_link),
            pdf_path=attachments[0].file_path if attachments else None,
            attachments=attachments,
            notes=self._notes(item_id),
        )

    def _build_standalone_attachment_record(self, item_id: int, date_modified: str) -> DocumentRecord | None:
        meta = self._base_item_meta(item_id)
        if not meta:
            return None

        attachment = self._attachment_for_item(item_id)
        if not attachment or attachment.content_type != "application/pdf":
            return None

        title = meta["fields"].get("title") or (attachment.file_path.stem if attachment.file_path else meta["key"])
        notion_link = self._paper_flow_link(item_id)
        return DocumentRecord(
            source_kind="zotero_standalone_pdf",
            title=title,
            item_type="attachment",
            zotero_item_id=item_id,
            zotero_key=meta["key"],
            zotero_select_uri=f"zotero://select/library/items/{meta['key']}",
            authors=[],
            collections=self._collections(item_id),
            tags=self._tags(item_id),
            date_added=meta["date_added"],
            date_modified=date_modified,
            file_path=str(attachment.file_path) if attachment.file_path else attachment.path,
            notion_page_url=notion_link,
            notion_page_id=_notion_page_id_from_url(notion_link),
            pdf_path=attachment.file_path,
            attachments=[attachment],
            notes=self._notes(item_id),
        )

    def _base_item_meta(self, item_id: int) -> dict | None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT i.key, it.typeName, i.dateAdded
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                WHERE i.itemID = ?
                """,
                (item_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            key, type_name, date_added = row
            cur.execute(
                """
                SELECT fc.fieldName, idv.value
                FROM itemData id
                JOIN fieldsCombined fc ON fc.fieldID = id.fieldID
                JOIN itemDataValues idv ON idv.valueID = id.valueID
                WHERE id.itemID = ?
                """,
                (item_id,),
            )
            fields = {field_name: value for field_name, value in cur.fetchall()}
        return {"key": key, "type_name": type_name, "date_added": date_added, "fields": fields}

    def _authors(self, item_id: int) -> list[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.firstName, c.lastName, c.fieldMode
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                WHERE ic.itemID = ?
                ORDER BY ic.orderIndex
                """,
                (item_id,),
            )
            authors = []
            for first_name, last_name, field_mode in cur.fetchall():
                if field_mode == 1:
                    authors.append(last_name)
                else:
                    authors.append(" ".join(part for part in (first_name, last_name) if part))
            return [author for author in authors if author]

    def _collections(self, item_id: int) -> list[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.collectionName
                FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE ci.itemID = ?
                ORDER BY c.collectionName
                """,
                (item_id,),
            )
            return [name for (name,) in cur.fetchall()]

    def _tags(self, item_id: int) -> list[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT t.name
                FROM itemTags it
                JOIN tags t ON t.tagID = it.tagID
                WHERE it.itemID = ?
                ORDER BY t.name
                """,
                (item_id,),
            )
            return [name for (name,) in cur.fetchall()]

    def _notes(self, parent_item_id: int) -> list[NoteRecord]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT n.itemID, i.key, n.title, n.note, i.dateModified
                FROM itemNotes n
                JOIN items i ON i.itemID = n.itemID
                WHERE n.parentItemID = ?
                ORDER BY i.dateModified DESC
                """,
                (parent_item_id,),
            )
            notes = []
            for item_id, item_key, title, note, date_modified in cur.fetchall():
                text = _plain_note_text(note)
                if not text:
                    continue
                notes.append(
                    NoteRecord(
                        item_id=item_id,
                        item_key=item_key,
                        title=title or "Zotero Note",
                        text=text,
                        date_modified=date_modified,
                    )
                )
            return notes

    def _paper_flow_link(self, parent_item_id: int) -> str | None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ia.path, idv.value
                FROM itemAttachments ia
                JOIN itemData id ON id.itemID = ia.itemID
                JOIN fieldsCombined fc ON fc.fieldID = id.fieldID
                JOIN itemDataValues idv ON idv.valueID = id.valueID
                WHERE ia.parentItemID = ?
                  AND ia.linkMode = 3
                  AND fc.fieldName = 'title'
                  AND idv.value = ?
                ORDER BY ia.itemID DESC
                """,
                (parent_item_id, PAPER_FLOW_NOTION_LINK_TITLE),
            )
            row = cur.fetchone()
            if not row:
                return None
            path, _title = row
            return path if path and path.startswith(("http://", "https://")) else None

    def _pdf_attachments(self, parent_item_id: int) -> list[AttachmentRecord]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT i.itemID, i.key, ia.contentType, ia.path, ia.linkMode
                FROM itemAttachments ia
                JOIN items i ON i.itemID = ia.itemID
                WHERE ia.parentItemID = ? AND ia.contentType = 'application/pdf'
                ORDER BY i.itemID DESC
                """,
                (parent_item_id,),
            )
            return [
                AttachmentRecord(
                    item_id=item_id,
                    item_key=item_key,
                    content_type=content_type,
                    path=path,
                    file_path=self._resolve_attachment_path(item_key, path),
                    link_mode=link_mode,
                )
                for item_id, item_key, content_type, path, link_mode in cur.fetchall()
            ]

    def _attachment_for_item(self, item_id: int) -> AttachmentRecord | None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT i.itemID, i.key, ia.contentType, ia.path, ia.linkMode
                FROM itemAttachments ia
                JOIN items i ON i.itemID = ia.itemID
                WHERE ia.itemID = ?
                """,
                (item_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            item_id, item_key, content_type, path, link_mode = row
            return AttachmentRecord(
                item_id=item_id,
                item_key=item_key,
                content_type=content_type,
                path=path,
                file_path=self._resolve_attachment_path(item_key, path),
                link_mode=link_mode,
            )

    def _resolve_attachment_path(self, item_key: str, attachment_path: str | None) -> Path | None:
        if not attachment_path:
            return None
        if attachment_path.startswith("storage:"):
            filename = attachment_path.split("storage:", 1)[1]
            return self.data_dir / "storage" / item_key / filename
        if re.match(r"^[A-Za-z]:\\", attachment_path):
            if os.name == "nt":
                return Path(attachment_path)
            drive = attachment_path[0].lower()
            rest = attachment_path[2:].replace("\\", "/")
            return Path(f"/mnt/{drive}{rest}")
        return Path(attachment_path)

    def _matches_collections(self, record: DocumentRecord, collection_names: list[str] | None) -> bool:
        if not collection_names:
            return True
        wanted = {name.lower() for name in collection_names}
        return any(collection.lower() in wanted for collection in record.collections)


def _first_path_text(attachments: list[AttachmentRecord]) -> str | None:
    for attachment in attachments:
        if attachment.file_path:
            return str(attachment.file_path)
        if attachment.path:
            return attachment.path
    return None


def _citation_key(extra: str | None) -> str | None:
    if not extra:
        return None
    for line in extra.splitlines():
        if line.lower().startswith("citation key:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _plain_note_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def _notion_page_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"([0-9a-fA-F]{32})(?:[?#].*)?$", url.replace("-", ""))
    if not match:
        return None
    raw = match.group(1).lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
