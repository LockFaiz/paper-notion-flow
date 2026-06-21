from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from notion_client import Client
from notion_client.errors import APIResponseError

from .config import Settings
from .models import DocumentRecord, ReadingGuide


# LaTeX delimiters produced by the guide prompt; rendered via Notion equations.
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")


@dataclass(slots=True)
class NotionSyncResult:
    paper_page_id: str
    paper_page_url: str
    guide_page_id: str | None = None
    guide_page_url: str | None = None


TEXT = {
    "zh-CN": {
        "guide_title": "\u8bba\u6587\u89e3\u8bfb",
        "extracted_title": "\u539f\u6587\u63d0\u53d6",
        "collection": "\u4e3b\u9898\u5f52\u6863",
        "authors": "\u4f5c\u8005",
        "year": "\u5e74\u4efd",
        "summary": "\u4e00\u53e5\u8bdd\u603b\u89c8",
        "problem": "\u9762\u5411\u7684\u95ee\u9898",
        "related_work": "\u76f8\u5173\u5de5\u4f5c\u600e\u4e48\u5206\u7c7b",
        "mechanism": "\u6838\u5fc3\u673a\u5236\u600e\u4e48\u7406\u89e3",
        "contributions": "\u8d21\u732e\u4e0e\u4f18\u70b9",
        "figures_tables": "\u5173\u952e\u56fe\u8868\u600e\u4e48\u8bfb",
        "experiments": "\u5b9e\u9a8c\u4e0e\u6570\u636e\u7ed3\u8bba",
        "conclusions": "\u672c\u6587\u7ed3\u8bba",
        "limitations": "\u5c40\u9650\u4e0e\u95ee\u9898",
        "none": "\u65e0",
        "generation_info": "生成信息",
    },
    "en": {
        "guide_title": "Paper Guide",
        "extracted_title": "Extracted Content",
        "collection": "Collection",
        "authors": "Authors",
        "year": "Year",
        "summary": "One-sentence overview",
        "problem": "Problem",
        "related_work": "Related-work landscape",
        "mechanism": "Core mechanism",
        "contributions": "Contributions and strengths",
        "figures_tables": "Key figures and tables",
        "experiments": "Experiments and data-backed conclusions",
        "conclusions": "Conclusions",
        "limitations": "Limitations and concerns",
        "none": "None",
        "generation_info": "Generation info",
    },
    "ja": {
        "guide_title": "論文解説",
        "extracted_title": "抽出本文",
        "collection": "コレクション",
        "authors": "著者",
        "year": "年",
        "summary": "一文要約",
        "problem": "扱っている問題",
        "related_work": "関連研究の位置づけ",
        "mechanism": "核心的な仕組み",
        "contributions": "貢献と強み",
        "figures_tables": "重要な図表",
        "experiments": "実験とデータに基づく結論",
        "conclusions": "結論",
        "limitations": "限界と懸念",
        "none": "なし",
        "generation_info": "生成情報",
    },
    "ko": {
        "guide_title": "논문 해설",
        "extracted_title": "추출된 원문",
        "collection": "컬렉션",
        "authors": "저자",
        "year": "연도",
        "summary": "한 문장 요약",
        "problem": "다루는 문제",
        "related_work": "관련 연구 지형",
        "mechanism": "핵심 메커니즘",
        "contributions": "기여와 강점",
        "figures_tables": "핵심 그림과 표",
        "experiments": "실험과 데이터 기반 결론",
        "conclusions": "결론",
        "limitations": "한계와 우려",
        "none": "없음",
        "generation_info": "생성 정보",
    },
    "fr": {
        "guide_title": "Guide de lecture",
        "extracted_title": "Contenu extrait",
        "collection": "Collection",
        "authors": "Auteurs",
        "year": "Année",
        "summary": "Vue d'ensemble en une phrase",
        "problem": "Problème",
        "related_work": "Paysage des travaux connexes",
        "mechanism": "Mécanisme central",
        "contributions": "Contributions et forces",
        "figures_tables": "Figures et tableaux clés",
        "experiments": "Expériences et conclusions appuyées par les données",
        "conclusions": "Conclusions",
        "limitations": "Limites et réserves",
        "none": "Aucun",
        "generation_info": "Infos de génération",
    },
    "de": {
        "guide_title": "Leseleitfaden",
        "extracted_title": "Extrahierter Inhalt",
        "collection": "Sammlung",
        "authors": "Autorinnen und Autoren",
        "year": "Jahr",
        "summary": "Überblick in einem Satz",
        "problem": "Problem",
        "related_work": "Einordnung verwandter Arbeiten",
        "mechanism": "Kernmechanismus",
        "contributions": "Beiträge und Stärken",
        "figures_tables": "Wichtige Abbildungen und Tabellen",
        "experiments": "Experimente und datenbasierte Schlussfolgerungen",
        "conclusions": "Schlussfolgerungen",
        "limitations": "Grenzen und Bedenken",
        "none": "Keine",
        "generation_info": "Generierungsinfo",
    },
    "es": {
        "guide_title": "Guía de lectura",
        "extracted_title": "Contenido extraído",
        "collection": "Colección",
        "authors": "Autores",
        "year": "Año",
        "summary": "Resumen en una frase",
        "problem": "Problema",
        "related_work": "Panorama de trabajos relacionados",
        "mechanism": "Mecanismo central",
        "contributions": "Contribuciones y fortalezas",
        "figures_tables": "Figuras y tablas clave",
        "experiments": "Experimentos y conclusiones respaldadas por datos",
        "conclusions": "Conclusiones",
        "limitations": "Limitaciones y dudas",
        "none": "Ninguno",
        "generation_info": "Información de generación",
    },
}


class NotionWriter:
    def __init__(self, settings: Settings) -> None:
        if not settings.notion_token or not settings.notion_database_id:
            raise RuntimeError("NOTION_TOKEN and NOTION_DATABASE_ID are required.")

        self.settings = settings
        self.client = Client(auth=settings.notion_token)
        self.database_id = settings.notion_database_id
        self.database = self.client.databases.retrieve(self.database_id)
        self.data_source_id = None
        if "properties" in self.database:
            self.properties = self.database["properties"]
        else:
            data_sources = self.database.get("data_sources", [])
            if not data_sources:
                raise RuntimeError("Could not find Notion database properties or data sources.")
            self.data_source_id = data_sources[0]["id"]
            self.data_source = self.client.data_sources.retrieve(self.data_source_id)
            self.properties = self.data_source["properties"]
        self.title_property = self._first_property_of_type("title", settings.notion_title_candidates)
        if not self.title_property:
            raise RuntimeError("Could not find a title property in the Notion database.")

    def sync_document(
        self,
        *,
        record: DocumentRecord,
        guide: ReadingGuide | None,
        extracted_markdown: str,
        variant_label: str | None = None,
        variant_date: str | None = None,
        overwrite_version: str | None = None,
    ) -> NotionSyncResult:
        paper_page_id = self._find_paper_page(record) or self._create_paper_page(record)
        self._update_paper_page(paper_page_id, record, guide_present=guide is not None)
        guide_page_id = None
        if guide is not None:
            guide_title = self._text("guide_title")
            if variant_label:
                # Version key (prompt/model/effort) drives overwrite; the date is
                # appended to the title only. Same version → overwritten (date
                # refreshed); a new prompt/model/effort combo → a new subpage.
                version_key = f"{guide_title} · {variant_label}"
                title = f"{version_key} · {variant_date}" if variant_date else version_key
                self._delete_child_pages_by_prefix(paper_page_id, version_key)
                # When the user picked an older version to overwrite (cap reached),
                # delete that specific subpage too to free its slot.
                if overwrite_version and overwrite_version != title:
                    self._delete_child_pages(paper_page_id, (overwrite_version,))
                guide_page_id = self._create_child_page(paper_page_id, title)
            else:
                title = guide_title
                guide_page_id = self._reset_child_page(
                    paper_page_id,
                    guide_title,
                    legacy_titles=self._all_text_values("guide_title", extra=("Reading Guide",)),
                )
            self._append_page_content(guide_page_id, self._build_guide_blocks(record, guide, title=title))
        if self.settings.sync_notes and record.notes:
            notes_page_id = self._reset_child_page(paper_page_id, "Notes", legacy_titles=("Zotero Notes",))
            self._append_page_content(notes_page_id, self._build_note_blocks(record))
        self._delete_child_pages(paper_page_id, self._all_text_values("extracted_title"))
        return NotionSyncResult(
            paper_page_id=paper_page_id,
            paper_page_url=_notion_page_url(paper_page_id),
            guide_page_id=guide_page_id,
            guide_page_url=_notion_page_url(guide_page_id) if guide_page_id else None,
        )

    def list_guide_variants(self, record: DocumentRecord) -> list[str]:
        """Existing reading-guide subpage titles (versions) for this paper."""
        paper_page_id = self._find_paper_page(record)
        if not paper_page_id:
            return []
        guide_title = self._text("guide_title")
        variants: list[str] = []
        for block in self._list_all_child_blocks(paper_page_id):
            if block.get("type") != "child_page":
                continue
            title = block["child_page"].get("title", "")
            if title == guide_title or title.startswith(f"{guide_title} · "):
                variants.append(title)
        return variants

    def check_database(self) -> str:
        required = [("title", self.title_property)]
        recommended = {
            "Zotero key": self._first_matching_property(self.settings.notion_zotero_key_candidates),
            "Zotero URI": self._first_matching_property(self.settings.notion_zotero_uri_candidates),
            "URL": self._first_matching_property(self.settings.notion_url_candidates),
            "Authors": self._first_matching_property(self.settings.notion_authors_candidates),
            "Collections": self._first_matching_property(self.settings.notion_collection_candidates),
            "Tags": self._first_matching_property(self.settings.notion_tag_candidates),
            "DOI": self._first_matching_property(self.settings.notion_doi_candidates),
            "Date Added": self._first_matching_property(self.settings.notion_date_added_candidates),
            "Date Modified": self._first_matching_property(self.settings.notion_date_modified_candidates),
            "File Path": self._first_matching_property(self.settings.notion_file_path_candidates),
            "Publication": self._first_matching_property(self.settings.notion_publication_candidates),
            "Abstract": self._first_matching_property(self.settings.notion_abstract_candidates),
            "Citation Key": self._first_matching_property(self.settings.notion_citation_key_candidates),
            "AI Status": self._first_matching_property(self.settings.notion_ai_status_candidates),
            "AI Last Updated": self._first_matching_property(self.settings.notion_ai_updated_candidates),
        }
        lines = [
            "NOTION_SCHEMA:OK",
            f"NOTION_TITLE_PROPERTY:{required[0][1]}",
        ]
        for label, property_name in recommended.items():
            lines.append(f"NOTION_PROPERTY_{_label_key(label)}:{property_name or 'MISSING_OPTIONAL'}")
        return "\n".join(lines)

    def archive_document_by_zotero_key(self, zotero_key: str) -> int:
        key_property = self._first_matching_property(self.settings.notion_zotero_key_candidates)
        if not key_property:
            return 0

        query = {"page_size": 100, "filter": self._equals_filter(key_property, zotero_key)}
        if self.data_source_id:
            response = self.client.data_sources.query(data_source_id=self.data_source_id, **query)
        else:
            response = self.client.databases.query(database_id=self.database_id, **query)

        archived = 0
        for result in response.get("results", []):
            if result.get("archived") or result.get("in_trash"):
                continue
            self.client.pages.update(page_id=result["id"], archived=True)
            archived += 1
        return archived

    def archive_documents_missing_from_zotero(
        self,
        *,
        current_zotero_keys: set[str],
        collection_names: list[str] | None = None,
        dry_run: bool = False,
    ) -> tuple[int, list[str]]:
        key_property = self._first_matching_property(self.settings.notion_zotero_key_candidates)
        if not key_property:
            return 0, []

        archived = 0
        archived_keys: list[str] = []
        for page in self._iter_database_pages():
            if page.get("archived") or page.get("in_trash"):
                continue
            page_key_values = self._page_property_values(page, key_property)
            if not page_key_values:
                continue
            if any(key in current_zotero_keys for key in page_key_values):
                continue
            if not self._page_matches_collection_filter(page, collection_names):
                continue

            if not dry_run:
                self.client.pages.update(page_id=page["id"], archived=True)
            archived += 1
            archived_keys.extend(page_key_values)
        return archived, archived_keys

    def _find_paper_page(self, record: DocumentRecord) -> str | None:
        linked_page_id = record.notion_page_id or _page_id_from_url(record.notion_page_url)
        if linked_page_id:
            try:
                page = self.client.pages.retrieve(linked_page_id)
                if not page.get("archived") and not page.get("in_trash"):
                    return linked_page_id
            except APIResponseError:
                pass

        filters = []

        key_property = self._first_matching_property(self.settings.notion_zotero_key_candidates)
        if key_property and record.zotero_key:
            filters.append(self._equals_filter(key_property, record.zotero_key))

        uri_property = self._first_matching_property(self.settings.notion_zotero_uri_candidates)
        if uri_property and record.zotero_select_uri:
            filters.append(self._equals_filter(uri_property, record.zotero_select_uri))

        url_property = self._first_matching_property(self.settings.notion_url_candidates)
        if url_property and record.source_url:
            filters.append(self._equals_filter(url_property, record.source_url))

        if record.title:
            filters.append(self._equals_filter(self.title_property, record.title))

        if not filters:
            return None

        query = {"page_size": 1, "filter": filters[0] if len(filters) == 1 else {"or": filters}}
        if self.data_source_id:
            response = self.client.data_sources.query(data_source_id=self.data_source_id, **query)
        else:
            response = self.client.databases.query(database_id=self.database_id, **query)
        results = response.get("results", [])
        return results[0]["id"] if results else None

    def _create_paper_page(self, record: DocumentRecord) -> str:
        parent = {"data_source_id": self.data_source_id} if self.data_source_id else {"database_id": self.database_id}
        page = self.client.pages.create(
            parent=parent,
            properties=self._paper_properties(record, guide_present=False),
        )
        return page["id"]

    def _update_paper_page(self, page_id: str, record: DocumentRecord, *, guide_present: bool) -> None:
        self.client.pages.update(page_id=page_id, properties=self._paper_properties(record, guide_present=guide_present))

    def _paper_properties(self, record: DocumentRecord, *, guide_present: bool) -> dict:
        properties: dict[str, dict] = {
            self.title_property: {"title": [{"text": {"content": record.title[:2000]}}]},
        }

        for property_name in self.settings.notion_title_candidates:
            if property_name == self.title_property:
                continue
            self._set_if_present(properties, property_name, record.title)

        for property_name in self.settings.notion_zotero_key_candidates:
            self._set_if_present(properties, property_name, record.zotero_key)
        for property_name in self.settings.notion_zotero_uri_candidates:
            self._set_if_present(properties, property_name, record.zotero_select_uri)
        for property_name in self.settings.notion_url_candidates:
            self._set_if_present(properties, property_name, record.source_url)
        for property_name in self.settings.notion_type_candidates:
            self._set_if_present(properties, property_name, record.item_type)
        for property_name in self.settings.notion_collection_candidates:
            self._set_if_present(properties, property_name, ", ".join(record.collections))
        for property_name in self.settings.notion_tag_candidates:
            self._set_multi_text_if_present(properties, property_name, record.tags)
        for property_name in self.settings.notion_authors_candidates:
            self._set_if_present(properties, property_name, ", ".join(record.authors))
        for property_name in self.settings.notion_doi_candidates:
            self._set_if_present(properties, property_name, record.doi)
        for property_name in self.settings.notion_file_path_candidates:
            self._set_if_present(properties, property_name, record.file_path)
        for property_name in self.settings.notion_publication_candidates:
            self._set_if_present(properties, property_name, record.publication)
        for property_name in self.settings.notion_proceedings_candidates:
            self._set_if_present(properties, property_name, record.proceedings_title)
        for property_name in self.settings.notion_abstract_candidates:
            self._set_if_present(properties, property_name, record.abstract)
        for property_name in self.settings.notion_citation_key_candidates:
            self._set_if_present(properties, property_name, record.citation_key)
        for property_name in self.settings.notion_date_added_candidates:
            self._set_date_if_present(properties, property_name, record.date_added)
        for property_name in self.settings.notion_date_modified_candidates:
            self._set_date_if_present(properties, property_name, record.date_modified)

        year_value = _extract_year(record.date)
        if year_value is not None:
            self._set_if_present(properties, "Year", str(year_value))

        ai_status = "Done" if guide_present else "Metadata Only"
        for property_name in self.settings.notion_ai_status_candidates:
            self._set_if_present(properties, property_name, ai_status)

        updated_date = datetime.now(timezone.utc).date().isoformat()
        for property_name in self.settings.notion_ai_updated_candidates:
            schema = self.properties.get(property_name)
            if schema and schema["type"] == "date":
                properties[property_name] = {"date": {"start": updated_date}}
        return properties

    def _set_if_present(self, properties: dict, property_name: str, value: str | None) -> None:
        if not value:
            return
        schema = self.properties.get(property_name)
        if not schema:
            return

        prop_type = schema["type"]
        if prop_type == "rich_text":
            properties[property_name] = {"rich_text": [{"text": {"content": value[:2000]}}]}
        elif prop_type == "url":
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
                if property_name in self.settings.notion_doi_candidates:
                    value = f"https://doi.org/{value}"
                else:
                    return
            properties[property_name] = {"url": value}
        elif prop_type == "select":
            properties[property_name] = {"select": {"name": value[:100]}}
        elif prop_type == "multi_select":
            items = [part.strip() for part in value.split(",") if part.strip()]
            properties[property_name] = {"multi_select": [{"name": item[:100]} for item in items]}
        elif prop_type == "number":
            try:
                properties[property_name] = {"number": int(value)}
            except ValueError:
                return
        elif prop_type == "title":
            properties[property_name] = {"title": [{"text": {"content": value[:2000]}}]}

    def _set_multi_text_if_present(self, properties: dict, property_name: str, values: list[str]) -> None:
        if not values:
            return
        self._set_if_present(properties, property_name, ", ".join(values))

    def _set_date_if_present(self, properties: dict, property_name: str, value: str | None) -> None:
        if not value:
            return
        schema = self.properties.get(property_name)
        if not schema or schema["type"] != "date":
            return
        date_value = _date_start(value)
        if date_value:
            properties[property_name] = {"date": {"start": date_value}}

    def _append_page_content(self, page_id: str, blocks: list[dict]) -> None:
        for chunk_start in range(0, len(blocks), 100):
            self.client.blocks.children.append(block_id=page_id, children=blocks[chunk_start : chunk_start + 100])

    def _build_guide_blocks(
        self,
        record: DocumentRecord,
        guide: ReadingGuide | None,
        *,
        title: str | None = None,
    ) -> list[dict]:
        blocks: list[dict | list[dict]] = [
            self._heading(title or self._text("guide_title")),
        ]

        if record.collections:
            blocks.append(self._paragraph(f"{self._text('collection')}: {', '.join(record.collections)}"))
        if record.date:
            blocks.append(self._paragraph(f"{self._text('year')}: {record.date}"))

        if guide:
            if guide.summary:
                blocks.extend([self._heading(self._text("summary")), *self._paragraph_blocks(guide.summary)])
            if guide.problem:
                blocks.extend([self._heading(self._text("problem")), *self._paragraph_blocks(guide.problem)])
            blocks.extend(
                [
                    self._bullet_list(self._text("related_work"), guide.related_work),
                    self._bullet_list(self._text("mechanism"), guide.mechanism or guide.key_points),
                    self._bullet_list(self._text("contributions"), guide.contributions),
                    self._bullet_list(self._text("figures_tables"), guide.figures_tables),
                    self._bullet_list(self._text("experiments"), guide.experiments),
                    self._bullet_list(self._text("conclusions"), guide.conclusions),
                    self._bullet_list(self._text("limitations"), guide.limitations),
                ]
            )
            if guide.generation_info:
                blocks.append(
                    self._paragraph(f"{self._text('generation_info')}: {guide.generation_info}")
                )

        flattened: list[dict] = []
        for block in blocks:
            if isinstance(block, list):
                flattened.extend(block)
            else:
                flattened.append(block)
        return flattened

    def _build_note_blocks(self, record: DocumentRecord) -> list[dict]:
        blocks: list[dict] = [self._heading("Zotero Notes")]
        for note in record.notes:
            blocks.append(self._heading(note.title or "Zotero Note"))
            blocks.append(self._paragraph(note.text))
        return blocks

    def _text(self, key: str) -> str:
        language = self.settings.guide_language if self.settings.guide_language in TEXT else "zh-CN"
        return TEXT[language].get(key, TEXT["zh-CN"][key])

    def _all_text_values(self, key: str, *, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
        values = [*extra, *(labels[key] for labels in TEXT.values() if key in labels)]
        return tuple(dict.fromkeys(values))

    def _first_property_of_type(self, prop_type: str, candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            schema = self.properties.get(candidate)
            if schema and schema["type"] == prop_type:
                return candidate
        for name, schema in self.properties.items():
            if schema["type"] == prop_type:
                return name
        return None

    def _first_matching_property(self, candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in self.properties:
                return candidate
        return None

    def _equals_filter(self, property_name: str, value: str) -> dict:
        schema = self.properties[property_name]
        prop_type = schema["type"]
        if prop_type == "title":
            return {"property": property_name, "title": {"equals": value[:2000]}}
        if prop_type == "rich_text":
            return {"property": property_name, "rich_text": {"equals": value[:2000]}}
        if prop_type == "url":
            return {"property": property_name, "url": {"equals": value}}
        if prop_type == "select":
            return {"property": property_name, "select": {"equals": value[:100]}}
        if prop_type == "multi_select":
            return {"property": property_name, "multi_select": {"contains": value[:100]}}
        if prop_type == "number":
            try:
                return {"property": property_name, "number": {"equals": int(value)}}
            except ValueError:
                return {"property": property_name, "number": {"is_empty": True}}
        raise RuntimeError(f"Unsupported Notion filter type for property {property_name}: {prop_type}")

    def _heading(self, text: str) -> dict:
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
        }

    def _paragraph(self, text: str) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": self._math_rich_text(text)},
        }

    def _math_rich_text(self, text: str) -> list[dict]:
        """Rich text with $...$ rendered as real Notion inline equations (KaTeX).

        Without this, LaTeX from the guide shows up as raw source and Unicode
        math soup, which is what made formulas unreadable on the page.
        """
        # Display math inside flowing text degrades gracefully to inline math.
        normalized = _DISPLAY_MATH.sub(lambda m: f"${m.group(1).strip()}$", text)
        segments: list[dict] = []
        cursor = 0
        for match in _INLINE_MATH.finditer(normalized):
            if match.start() > cursor:
                segments.append(
                    {"type": "text", "text": {"content": normalized[cursor : match.start()][:2000]}}
                )
            segments.append(
                {"type": "equation", "equation": {"expression": match.group(1).strip()[:1000]}}
            )
            cursor = match.end()
        if cursor < len(normalized):
            segments.append({"type": "text", "text": {"content": normalized[cursor:][:2000]}})
        return segments or [{"type": "text", "text": {"content": ""}}]

    def _paragraph_blocks(self, text: str) -> list[dict]:
        """Paragraph content where $$...$$ becomes centered Notion equation blocks."""
        blocks: list[dict] = []
        cursor = 0
        for match in _DISPLAY_MATH.finditer(text):
            before = text[cursor : match.start()].strip()
            if before:
                blocks.append(self._paragraph(before))
            blocks.append(
                {
                    "object": "block",
                    "type": "equation",
                    "equation": {"expression": match.group(1).strip()[:1000]},
                }
            )
            cursor = match.end()
        tail = text[cursor:].strip()
        if tail or not blocks:
            blocks.append(self._paragraph(tail or text))
        return blocks

    def _bullet_list(self, heading: str, items: list[str]) -> list[dict]:
        blocks = [self._heading(heading)]
        if not items:
            blocks.append(self._paragraph(self._text("none")))
            return blocks
        for item in items:
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": self._math_rich_text(item),
                    },
                }
            )
        return blocks

    def _iter_database_pages(self) -> list[dict]:
        results: list[dict] = []
        next_cursor = None
        while True:
            query = {"page_size": 100}
            if next_cursor:
                query["start_cursor"] = next_cursor
            if self.data_source_id:
                response = self.client.data_sources.query(data_source_id=self.data_source_id, **query)
            else:
                response = self.client.databases.query(database_id=self.database_id, **query)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")
        return results

    def _page_matches_collection_filter(self, page: dict, collection_names: list[str] | None) -> bool:
        if not collection_names:
            return True
        wanted = {name.lower() for name in collection_names}
        for property_name in self.settings.notion_collection_candidates:
            values = self._page_property_values(page, property_name)
            if any(value.lower() in wanted for value in values):
                return True
        return False

    def _page_property_values(self, page: dict, property_name: str) -> list[str]:
        prop = page.get("properties", {}).get(property_name)
        if not prop:
            return []
        prop_type = prop.get("type")
        if prop_type == "title":
            return [_join_rich_text(prop.get("title", []))]
        if prop_type == "rich_text":
            return [_join_rich_text(prop.get("rich_text", []))]
        if prop_type == "url":
            return [prop.get("url") or ""]
        if prop_type == "select":
            selected = prop.get("select")
            return [selected.get("name", "")] if selected else []
        if prop_type == "multi_select":
            return [item.get("name", "") for item in prop.get("multi_select", [])]
        if prop_type == "number":
            value = prop.get("number")
            return [str(value)] if value is not None else []
        return []

    def _list_all_child_blocks(self, block_id: str) -> list[dict]:
        results: list[dict] = []
        next_cursor = None
        while True:
            response = self.client.blocks.children.list(block_id=block_id, page_size=100, start_cursor=next_cursor)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")
        return results

    def _reset_child_page(self, parent_page_id: str, child_title: str, *, legacy_titles: tuple[str, ...] = ()) -> str:
        self._delete_child_pages(parent_page_id, (child_title, *legacy_titles))
        page = self.client.pages.create(
            parent={"page_id": parent_page_id},
            properties={"title": [{"type": "text", "text": {"content": child_title}}]},
        )
        return page["id"]

    def _delete_child_pages(self, parent_page_id: str, child_titles: tuple[str, ...]) -> None:
        children = self._list_all_child_blocks(parent_page_id)
        titles_to_delete = set(child_titles)
        for block in children:
            if block.get("type") != "child_page":
                continue
            if block["child_page"]["title"] not in titles_to_delete:
                continue
            if block.get("archived") or block.get("in_trash"):
                continue
            try:
                self.client.blocks.delete(block_id=block["id"])
            except APIResponseError as exc:
                if "archived" in str(exc).lower():
                    continue
                raise

    def _create_child_page(self, parent_page_id: str, title: str) -> str:
        page = self.client.pages.create(
            parent={"page_id": parent_page_id},
            properties={"title": [{"type": "text", "text": {"content": title}}]},
        )
        return page["id"]

    def _delete_child_pages_by_prefix(self, parent_page_id: str, prefix: str) -> None:
        for block in self._list_all_child_blocks(parent_page_id):
            if block.get("type") != "child_page":
                continue
            child_title = block["child_page"].get("title", "")
            if child_title != prefix and not child_title.startswith(f"{prefix} · "):
                continue
            if block.get("archived") or block.get("in_trash"):
                continue
            try:
                self.client.blocks.delete(block_id=block["id"])
            except APIResponseError as exc:
                if "archived" in str(exc).lower():
                    continue
                raise


def _extract_year(date_text: str | None) -> int | None:
    if not date_text:
        return None
    digits = "".join(char if char.isdigit() else " " for char in date_text)
    for token in digits.split():
        if len(token) == 4:
            year = int(token)
            if 1900 <= year <= 2100:
                return year
    return None


def _date_start(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    year = _extract_year(value)
    return f"{year}-01-01" if year else None


def _notion_page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _page_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"([0-9a-fA-F]{32})(?:[?#].*)?$", url.replace("-", ""))
    if not match:
        return None
    raw = match.group(1).lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _label_key(label: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")


def _join_rich_text(items: list[dict]) -> str:
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in items).strip()
