from __future__ import annotations

import re
from pathlib import Path

from notion_client import Client
from notion_client.errors import APIResponseError

from .config import Settings

# Research map orchestration (feature/research-map). See docs/RESEARCH_MAP.md.
#
# Roadmap:
#   1. Schema + scaffolding  <- this module's current scope
#   2. Extraction            (build_research_map: extract ResearchMapExtraction)
#   3. Notion sync           (build_research_map: write/dedup the 4 DBs)
#   4. Render                (render_research_map: Cytoscape landscape.html)
#   5. Plugin wiring
#   6. Polish


RELATION_TYPES = ("addresses", "builds-on", "uses", "contradicts", "evaluates")
CONCEPT_KINDS = ("method", "model", "dataset", "metric", "concept")


EXTRACTION_PROMPT = """You are mapping the research landscape of a paper library.

From the paper described below, extract the research-map graph elements. Return
only valid JSON, no markdown fences.

Use exactly this JSON schema:
{{
  "problems":  [{{"name": "string", "description": "string"}}],
  "concepts":  [{{"name": "string", "kind": "method|model|dataset|metric|concept", "description": "string"}}],
  "relations": [{{"source": "string", "target": "string", "type": "addresses|builds-on|uses|contradicts|evaluates", "rationale": "string"}}],
  "gaps":      [{{"name": "string", "rationale": "string", "related": ["string"]}}]
}}

Extraction rules:
- Names are short, reusable noun phrases (e.g. "world models", "sample efficiency"),
  NOT sentences and NOT paper-specific phrasings — so the same concept from two
  papers produces the same name and can be merged.
- relations.source/target MUST be names that appear in this extraction's problems
  or concepts.
- A gap is something this paper reveals as unaddressed, untested, or missing —
  not a contribution. Use gaps sparingly; omit if none are evident.
- Prefer 3-8 concepts, 1-4 problems. Do not pad. Omit anything you cannot ground
  in the paper.
- All free-text (description/rationale) in {language_name}.

Paper context:
- title: {title_hint}
- authors: {authors}
- collections: {collections}

Paper reading guide / content:
{content}
"""


def normalize_node_name(name: str) -> str:
    """Canonical key for dedup/merge across papers."""
    return re.sub(r"\s+", " ", name.strip().lower())


def check_map_setup(settings: Settings) -> str:
    """Validate the research-map Notion configuration. Used by `map check`."""
    lines: list[str] = []
    token = settings.notion_token
    lines.append(f"NOTION_TOKEN:{'OK' if token else 'MISSING'}")

    databases = {
        "PAPERS_DB": settings.notion_database_id,
        "PROBLEMS_DB": settings.notion_problems_database_id,
        "CONCEPTS_DB": settings.notion_concepts_database_id,
        "RELATIONS_DB": settings.notion_relations_database_id,
        "GAPS_DB": settings.notion_gaps_database_id,
    }
    for marker, value in databases.items():
        lines.append(f"{marker}:{'CONFIGURED' if value else 'MISSING'}")
    lines.append(
        f"LANDSCAPE_PAGE:{'CONFIGURED' if settings.notion_landscape_page_id else 'MISSING'}"
    )

    if token:
        client = Client(auth=token)
        for marker, value in databases.items():
            if not value:
                continue
            try:
                client.databases.retrieve(database_id=value)
                lines.append(f"{marker}_ACCESS:OK")
            except APIResponseError as error:
                lines.append(f"{marker}_ACCESS:ERROR {error.code}")

    return "\n".join(lines)


def build_research_map(
    settings: Settings,
    *,
    data_dir: Path,
    collections: list[str],
    since_hours: float,
    force: bool,
    prompt_override: str | None = None,
) -> str:
    """Extract graph elements from papers and sync them to Notion.

    Implemented in roadmap steps 2 (extraction) and 3 (Notion sync).
    """
    raise NotImplementedError(
        "map build lands in roadmap step 2 (extraction) + step 3 (Notion sync). "
        "Schema and scaffolding are in place; see docs/RESEARCH_MAP.md."
    )


def render_research_map(
    settings: Settings,
    *,
    data_dir: Path,
    output: Path | None = None,
    serve: bool = False,
) -> str:
    """Render landscape.html (Cytoscape) from the Notion databases.

    Implemented in roadmap step 4 (render).
    """
    raise NotImplementedError(
        "map render lands in roadmap step 4 (Cytoscape landscape.html). "
        "Schema and scaffolding are in place; see docs/RESEARCH_MAP.md."
    )
