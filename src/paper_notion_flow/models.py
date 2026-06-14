from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(slots=True)
class AttachmentRecord:
    item_id: int
    item_key: str
    content_type: str | None
    path: str | None
    file_path: Path | None
    link_mode: int | None = None


@dataclass(slots=True)
class NoteRecord:
    item_id: int
    item_key: str
    title: str
    text: str
    date_modified: str | None = None


@dataclass(slots=True)
class DocumentRecord:
    source_kind: str
    title: str
    item_type: str | None = None
    zotero_item_id: int | None = None
    zotero_key: str | None = None
    zotero_parent_key: str | None = None
    zotero_select_uri: str | None = None
    source_url: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    date: str | None = None
    date_added: str | None = None
    date_modified: str | None = None
    doi: str | None = None
    publication: str | None = None
    proceedings_title: str | None = None
    citation_key: str | None = None
    extra: str | None = None
    file_path: str | None = None
    notion_page_url: str | None = None
    notion_page_id: str | None = None
    pdf_path: Path | None = None
    attachments: list[AttachmentRecord] = field(default_factory=list)
    notes: list[NoteRecord] = field(default_factory=list)

    @property
    def stable_id(self) -> str:
        return self.zotero_key or self.title

    @property
    def content_id(self) -> str:
        return (self.zotero_key or self.title).replace("/", "_")


class ReadingGuide(BaseModel):
    title: str = Field(description="Normalized document title. Keep the original paper title language.")
    authors: list[str] = Field(default_factory=list, description="Authors or organization names if they can be inferred.")
    year: int | None = Field(default=None, description="Publication year if it can be inferred confidently.")
    summary: str = Field(default="", description="One concise plain-Chinese overview of the paper's logic.")
    problem: str = Field(default="", description="The concrete problem the paper addresses, in Simplified Chinese.")
    related_work: list[str] = Field(default_factory=list, description="Related-work categories and how this paper differs.")
    mechanism: list[str] = Field(default_factory=list, description="Core theory, method, or mechanism explained plainly.")
    contributions: list[str] = Field(default_factory=list, description="Concrete contributions and advantages.")
    figures_tables: list[str] = Field(
        default_factory=list,
        description="High-value figures or tables and what each one shows, based on captions or extracted table text.",
    )
    experiments: list[str] = Field(default_factory=list, description="Experimental setup, baselines, metrics, and key results.")
    conclusions: list[str] = Field(default_factory=list, description="Main conclusions and empirical takeaways.")
    limitations: list[str] = Field(default_factory=list, description="Limitations, assumptions, or risks.")

    # Filled programmatically after generation (tool/model/effort/tokens) —
    # never produced by the model itself, which cannot know real token usage.
    generation_info: str | None = Field(default=None, description="Set by the pipeline, not the model.")

    # Legacy fields kept so older generated payloads remain readable.
    why_it_matters: str = Field(default="", description="Legacy field. Prefer problem/contributions instead.")
    key_points: list[str] = Field(default_factory=list, description="Legacy field. Prefer mechanism/contributions instead.")
    reading_plan: list[str] = Field(default_factory=list, description="Legacy field. Leave empty unless explicitly requested.")
    questions: list[str] = Field(default_factory=list, description="Legacy field. Leave empty unless explicitly requested.")


# --- Research map extraction (feature/research-map) ---------------------------
# Per-paper extraction produced by the local AI CLI. The build step merges and
# dedups these across the library by normalized node name before writing Notion.


class MapProblem(BaseModel):
    name: str = Field(description="Short research problem / objective the field is tackling. Noun phrase, not a sentence.")
    description: str = Field(default="", description="One concise line explaining the problem.")


class MapConcept(BaseModel):
    name: str = Field(description="A method, model, technique, dataset, or concept used in the paper. Short noun phrase.")
    kind: str = Field(default="concept", description="One of: method, model, dataset, metric, concept.")
    description: str = Field(default="", description="One concise line explaining the concept.")


class MapRelation(BaseModel):
    source: str = Field(description="Name of the source node (a problem or concept named elsewhere in this extraction).")
    target: str = Field(description="Name of the target node (a problem or concept named elsewhere in this extraction).")
    type: str = Field(default="uses", description="One of: addresses, builds-on, uses, contradicts, evaluates.")
    rationale: str = Field(default="", description="One concise line explaining why this relation holds.")


class MapGap(BaseModel):
    name: str = Field(description="An unaddressed problem, untested claim, or missing comparison. Short noun phrase.")
    rationale: str = Field(default="", description="One concise line explaining why this is an open gap.")
    related: list[str] = Field(default_factory=list, description="Names of nodes this gap concerns.")


class ResearchMapExtraction(BaseModel):
    problems: list[MapProblem] = Field(default_factory=list)
    concepts: list[MapConcept] = Field(default_factory=list)
    relations: list[MapRelation] = Field(default_factory=list)
    gaps: list[MapGap] = Field(default_factory=list)
