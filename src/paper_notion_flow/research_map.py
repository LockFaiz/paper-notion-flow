from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from notion_client import Client
from notion_client.errors import APIResponseError

from .ai import LANGUAGE_NAMES, run_structured_extraction
from .config import Settings
from .models import MergeResult, ResearchMapExtraction
from .notion_writer import TEXT, NotionWriter
from .state import SyncState

GUIDE_TITLES = tuple({texts["guide_title"] for texts in TEXT.values()})
_TEXT_BLOCK_LIMIT = 12000

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
- Node names (problems, concepts, gaps) MUST be written in {language_name}, EXCEPT
  widely-used acronyms and proper names conventionally kept in English (e.g. DQN,
  SAC, PPO, MDP, GNN, CNN, SO(2), KOVI, and dataset/benchmark names). This keeps the
  same concept from different papers under one name.
- Names are short, CANONICAL noun phrases with NO paper-specific qualifiers — use the
  general term so it merges across papers. E.g. use the equivalent of "样本效率", not
  "扩散策略样本效率" or "机器人操作样本效率"; "数据增强", not "旋转数据增强".
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


MERGE_PROMPT = """You are consolidating a research map. Below is a list of {label}
extracted from multiple papers, one per line. Group the entries that refer to the
SAME thing and give each group one canonical name.

Rules:
- Only merge true synonyms / same referent (e.g. "群不变 MDP" and "group-invariant MDP";
  "样本效率" and "sample efficiency"). Do NOT merge related-but-distinct concepts.
- The canonical name MUST be in {language_name}, except widely-used acronyms / proper
  names conventionally kept in English (DQN, SAC, MDP, GNN, SO(2), KOVI, dataset names).
- Only output groups that actually merge (2 or more aliases). Omit singletons.
- Return only valid JSON, no markdown fences:
{{"groups": [{{"canonical": "string", "aliases": ["string", "string"]}}]}}

{label} list:
{items}
"""


def normalize_node_name(name: str) -> str:
    """Canonical key for dedup/merge across papers."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _language_name(settings: Settings) -> str:
    return LANGUAGE_NAMES.get(settings.guide_language, "Simplified Chinese")


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


@dataclass(slots=True)
class PaperContent:
    page_id: str
    title: str
    authors: str
    topics: list[str]
    last_edited: str
    abstract: str


def _plain(items: list[dict]) -> str:
    return "".join(
        item.get("plain_text") or item.get("text", {}).get("content", "") for item in items
    ).strip()


def _first_text_property(page: dict, candidates: tuple[str, ...]) -> str:
    props = page.get("properties", {})
    for name in candidates:
        prop = props.get(name)
        if not prop:
            continue
        prop_type = prop.get("type")
        if prop_type in ("rich_text", "title"):
            value = _plain(prop.get(prop_type, []))
            if value:
                return value
        elif prop_type == "url" and prop.get("url"):
            return prop["url"]
    return ""


def _title_property(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title", []))
    return ""


def _multi_values(page: dict, candidates: tuple[str, ...]) -> list[str]:
    props = page.get("properties", {})
    for name in candidates:
        prop = props.get(name)
        if not prop:
            continue
        if prop.get("type") == "multi_select":
            values = [item.get("name", "") for item in prop.get("multi_select", [])]
            if values:
                return [v for v in values if v]
        elif prop.get("type") == "rich_text":
            value = _plain(prop.get("rich_text", []))
            if value:
                return [value]
    return []


def _blocks_to_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        payload = block.get(block_type, {}) if block_type else {}
        if isinstance(payload, dict) and "rich_text" in payload:
            text = _plain(payload["rich_text"])
            if text:
                parts.append(text)
        elif block_type == "equation":
            expr = payload.get("expression", "")
            if expr:
                parts.append(expr)
    return "\n".join(parts)[:_TEXT_BLOCK_LIMIT]


def _read_guide_text(writer: NotionWriter, paper_page_id: str) -> str:
    for block in writer._list_all_child_blocks(paper_page_id):
        if block.get("type") != "child_page":
            continue
        if block["child_page"].get("title") in GUIDE_TITLES:
            return _blocks_to_text(writer._list_all_child_blocks(block["id"]))
    return ""


def _iter_papers(writer: NotionWriter, settings: Settings, collections: list[str]) -> list[PaperContent]:
    wanted = {name.lower() for name in collections}
    papers: list[PaperContent] = []
    for page in writer._iter_database_pages():
        topics = _multi_values(page, settings.notion_collection_candidates)
        if wanted and not ({t.lower() for t in topics} & wanted):
            continue
        papers.append(
            PaperContent(
                page_id=page["id"],
                title=_title_property(page),
                authors=_first_text_property(page, settings.notion_authors_candidates),
                topics=topics,
                last_edited=page.get("last_edited_time", ""),
                abstract=_first_text_property(page, settings.notion_abstract_candidates),
            )
        )
    return papers


def _extract_for_paper(
    writer: NotionWriter, settings: Settings, paper: PaperContent, prompt_override: str | None
) -> ResearchMapExtraction:
    language_name = _language_name(settings)
    content = _read_guide_text(writer, paper.page_id) or paper.abstract or paper.title
    prompt = EXTRACTION_PROMPT.format(
        language_name=language_name,
        title_hint=paper.title,
        authors=paper.authors,
        collections=", ".join(paper.topics),
        content=content,
    )
    if prompt_override:
        prompt = f"{prompt_override.strip()}\n\n{prompt}"
    return run_structured_extraction(settings, prompt, ResearchMapExtraction)


@dataclass(slots=True)
class _Node:
    name: str
    detail: str = ""
    kind: str = ""
    papers: set[str] = field(default_factory=set)
    related: set[str] = field(default_factory=set)


def _merge(graph: dict[str, dict], extraction: ResearchMapExtraction, paper_title: str) -> None:
    for problem in extraction.problems:
        node = graph["problems"].setdefault(normalize_node_name(problem.name), _Node(problem.name))
        node.detail = node.detail or problem.description
        node.papers.add(paper_title)
    for concept in extraction.concepts:
        node = graph["concepts"].setdefault(normalize_node_name(concept.name), _Node(concept.name))
        node.detail = node.detail or concept.description
        node.kind = node.kind or concept.kind
        node.papers.add(paper_title)
    for relation in extraction.relations:
        key = (normalize_node_name(relation.source), normalize_node_name(relation.target), relation.type)
        node = graph["relations"].setdefault(
            key, _Node(relation.source, detail=relation.rationale, kind=relation.type)
        )
        node.related.add(relation.target)
        node.papers.add(paper_title)
    for gap in extraction.gaps:
        node = graph["gaps"].setdefault(normalize_node_name(gap.name), _Node(gap.name))
        node.detail = node.detail or gap.rationale
        node.related.update(gap.related)
        node.papers.add(paper_title)


def _graph_to_json(graph: dict[str, dict]) -> dict:
    problems = [
        {"name": n.name, "description": n.detail, "papers": sorted(n.papers)}
        for n in graph["problems"].values()
    ]
    concepts = [
        {"name": n.name, "kind": n.kind or "concept", "description": n.detail, "papers": sorted(n.papers)}
        for n in graph["concepts"].values()
    ]
    relations = [
        {"source": n.name, "target": next(iter(n.related), ""), "type": n.kind, "rationale": n.detail, "papers": sorted(n.papers)}
        for n in graph["relations"].values()
    ]
    gaps = [
        {"name": n.name, "rationale": n.detail, "related": sorted(n.related), "papers": sorted(n.papers)}
        for n in graph["gaps"].values()
    ]
    return {"problems": problems, "concepts": concepts, "relations": relations, "gaps": gaps}


def _canonical_map(settings: Settings, names: list[str], label: str) -> dict[str, str]:
    """Ask the AI to cluster synonymous names; return alias -> canonical."""
    unique = sorted({name for name in names if name})
    if len(unique) < 2:
        return {}
    prompt = MERGE_PROMPT.format(
        label=label,
        language_name=_language_name(settings),
        items="\n".join(f"- {name}" for name in unique),
    )
    result = run_structured_extraction(settings, prompt, MergeResult)
    mapping: dict[str, str] = {}
    for group in result.groups:
        canonical = group.canonical.strip()
        if not canonical:
            continue
        for alias in group.aliases:
            alias = alias.strip()
            if alias:
                mapping[alias] = canonical
        mapping[canonical] = canonical
    return mapping


def _resolve(mapping: dict[str, str], name: str) -> str:
    return mapping.get(name, name)


def _apply_merge(graph: dict[str, dict], nodemap: dict[str, str], gapmap: dict[str, str]) -> dict[str, dict]:
    merged: dict[str, dict] = {"problems": {}, "concepts": {}, "relations": {}, "gaps": {}}
    for category in ("problems", "concepts"):
        for node in graph[category].values():
            canonical = _resolve(nodemap, node.name)
            target = merged[category].setdefault(normalize_node_name(canonical), _Node(canonical))
            target.detail = target.detail or node.detail
            target.kind = target.kind or node.kind
            target.papers |= node.papers
    for node in graph["gaps"].values():
        canonical = _resolve(gapmap, node.name)
        target = merged["gaps"].setdefault(normalize_node_name(canonical), _Node(canonical))
        target.detail = target.detail or node.detail
        target.related |= {_resolve(nodemap, related) for related in node.related}
        target.papers |= node.papers
    for node in graph["relations"].values():
        source = _resolve(nodemap, node.name)
        target_name = _resolve(nodemap, next(iter(node.related), ""))
        if not target_name or source == target_name:
            continue
        key = (normalize_node_name(source), normalize_node_name(target_name), node.kind)
        relation = merged["relations"].setdefault(key, _Node(source, detail=node.detail, kind=node.kind))
        relation.related.add(target_name)
        relation.papers |= node.papers
    return merged


def build_research_map(
    settings: Settings,
    *,
    data_dir: Path,
    collections: list[str],
    force: bool,
    prompt_override: str | None = None,
) -> str:
    """Extract graph elements from each paper's Notion guide and merge them.

    Step 2: per-paper extraction (guide text preferred, abstract fallback),
    cached and deduped into <data-dir>/research-map/graph.json. Notion sync is
    step 3.
    """
    if not settings.notion_token or not settings.notion_database_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_DATABASE_ID (the Papers database) are required.")

    writer = NotionWriter(settings)
    papers = _iter_papers(writer, settings, collections)

    cache_dir = data_dir / "research-map" / "papers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    state = SyncState(data_dir / "state" / "research_map_state.json")

    extracted = 0
    cached = 0
    graph: dict[str, dict] = {"problems": {}, "concepts": {}, "relations": {}, "gaps": {}}

    for paper in papers:
        cache_path = cache_dir / f"{paper.page_id.replace('-', '')}.json"
        reuse = (
            not force
            and cache_path.exists()
            and state.was_processed(paper.page_id, paper.last_edited)
        )
        if reuse:
            extraction = ResearchMapExtraction.model_validate_json(cache_path.read_text(encoding="utf-8"))
            cached += 1
        else:
            extraction = _extract_for_paper(writer, settings, paper, prompt_override)
            cache_path.write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
            state.mark_processed(paper.page_id, paper.last_edited)
            extracted += 1
        _merge(graph, extraction, paper.title or paper.page_id)

    before = {key: len(value) for key, value in graph.items()}
    node_names = [n.name for n in graph["problems"].values()] + [n.name for n in graph["concepts"].values()]
    gap_names = [n.name for n in graph["gaps"].values()]
    nodemap = _canonical_map(settings, node_names, "research problems and concepts")
    gapmap = _canonical_map(settings, gap_names, "research gaps")
    graph = _apply_merge(graph, nodemap, gapmap)

    graph_json = _graph_to_json(graph)
    graph_path = data_dir / "research-map" / "graph.json"
    graph_path.write_text(json.dumps(graph_json, indent=2, ensure_ascii=False), encoding="utf-8")

    return "\n".join(
        [
            "MAP_BUILD:OK",
            f"PAPERS:{len(papers)} (extracted {extracted}, reused {cached})",
            f"PROBLEMS:{before['problems']}->{len(graph_json['problems'])}",
            f"CONCEPTS:{before['concepts']}->{len(graph_json['concepts'])}",
            f"RELATIONS:{before['relations']}->{len(graph_json['relations'])}",
            f"GAPS:{before['gaps']}->{len(graph_json['gaps'])}",
            f"GRAPH_JSON:{graph_path}",
            "Notion sync lands in step 3.",
        ]
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
