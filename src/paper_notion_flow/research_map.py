from __future__ import annotations

import re
from pathlib import Path

from notion_client import Client
from notion_client.errors import APIResponseError

from .config import Settings

MAP_ENV_KEYS = (
    "NOTION_PROBLEMS_DATABASE_ID",
    "NOTION_CONCEPTS_DATABASE_ID",
    "NOTION_RELATIONS_DATABASE_ID",
    "NOTION_GAPS_DATABASE_ID",
    "NOTION_LANDSCAPE_PAGE_ID",
)

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


def extract_notion_id(value: str) -> str:
    """Accept a raw id or a Notion URL and return the 32-hex id."""
    matches = re.findall(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    return matches[-1] if matches else value.strip()


def _title(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}]


def _relation(database_id: str) -> dict:
    return {"relation": {"database_id": database_id, "single_property": {}}}


def _select(options: tuple[str, ...]) -> dict:
    return {"select": {"options": [{"name": name} for name in options]}}


def _upsert_env(env_path: Path, values: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Z0-9_]+)=", line)
        if match and match.group(1) in values:
            out.append(f"{match.group(1)}={values[match.group(1)]}")
            seen.add(match.group(1))
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def init_research_map(
    settings: Settings,
    *,
    parent_page_id: str,
    write_env: bool = False,
) -> str:
    """Create the 4 research-map databases + a Landscape page via the Notion API.

    Creation order matters: relation properties can only target databases that
    already exist (Papers exists; then Problems before Concepts/Gaps reference it).
    """
    if not settings.notion_token:
        raise RuntimeError("NOTION_TOKEN is required to create the research-map databases.")
    papers_id = settings.notion_database_id
    if not papers_id:
        raise RuntimeError(
            "NOTION_DATABASE_ID (the existing Papers database) must be configured before `map init`."
        )

    client = Client(auth=settings.notion_token)
    parent = {"type": "page_id", "page_id": extract_notion_id(parent_page_id)}

    def create_db(name: str, properties: dict) -> str:
        try:
            response = client.databases.create(parent=parent, title=_title(name), properties=properties)
        except APIResponseError as error:
            raise RuntimeError(
                f"Failed to create the {name} database ({error.code}). "
                "Check that the parent page id is correct and shared with your Notion integration."
            ) from error
        return response["id"]

    problems_id = create_db(
        "Problems",
        {
            "Name": {"title": {}},
            "Description": {"rich_text": {}},
            "Status": _select(("open", "active", "addressed")),
            "Papers": _relation(papers_id),
        },
    )
    concepts_id = create_db(
        "Concepts",
        {
            "Name": {"title": {}},
            "Kind": _select(CONCEPT_KINDS),
            "Description": {"rich_text": {}},
            "Papers": _relation(papers_id),
            "Problems": _relation(problems_id),
        },
    )
    relations_id = create_db(
        "Relations",
        {
            "Name": {"title": {}},
            "Source": {"rich_text": {}},
            "Target": {"rich_text": {}},
            "Type": _select(RELATION_TYPES),
            "Rationale": {"rich_text": {}},
        },
    )
    gaps_id = create_db(
        "Gaps",
        {
            "Name": {"title": {}},
            "Rationale": {"rich_text": {}},
            "Related": _relation(concepts_id),
            "Papers": _relation(papers_id),
        },
    )

    try:
        landscape = client.pages.create(
            parent=parent,
            properties={"title": {"title": _title("Research Map — Landscape")}},
        )
    except APIResponseError as error:
        raise RuntimeError(f"Databases created, but the Landscape page failed ({error.code}).") from error
    landscape_id = landscape["id"]

    values = {
        "NOTION_PROBLEMS_DATABASE_ID": problems_id,
        "NOTION_CONCEPTS_DATABASE_ID": concepts_id,
        "NOTION_RELATIONS_DATABASE_ID": relations_id,
        "NOTION_GAPS_DATABASE_ID": gaps_id,
        "NOTION_LANDSCAPE_PAGE_ID": landscape_id,
    }
    lines = [f"{key}={value}" for key, value in values.items()]
    if write_env:
        env_path = Path(".env").resolve()
        _upsert_env(env_path, values)
        lines.append(f"WROTE_ENV:{env_path}")
    else:
        lines.append("Add these to your .env (or re-run with --write-env).")
    return "\n".join(lines)


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
