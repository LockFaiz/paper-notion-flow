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
from .zotero import ZoteroLibrary

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
#   4. Render                (render_research_map: inject GRAPH into research-map.html)
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
- problems: extract ONLY the 1-3 CORE problems the paper's MAIN CONTRIBUTION targets
  (read the contribution / abstract). Name the central problem(s) the paper set out to
  solve — NOT every sub-issue, side concern, or background difficulty. Fewer, sharper
  problems are far better; prefer 1-2 when possible.
- relations.source/target MUST be names that appear in this extraction's problems
  or concepts. For EACH main method/concept, add a relation linking it (source) to the
  core problem it addresses (target, type "addresses"), so methods can be grouped under
  problems in the map.
- A gap is something this paper reveals as unaddressed, untested, or missing —
  not a contribution. Use gaps sparingly; omit if none are evident.
- Prefer 4-7 concepts and 1-3 problems. Do not pad. Omit anything you cannot ground
  in the paper.
- All free-text (description/rationale) in {language_name}.

Paper context:
- title: {title_hint}
- authors: {authors}
- collections: {collections}

Paper reading guide / content:
{content}
"""


MERGE_PROMPT = """You are consolidating a research map. Below is a NUMBERED list of
{label} extracted from multiple papers. Group the entries that refer to the SAME
thing and give each group one canonical name.

Rules:
- Actively merge: cross-language synonyms (e.g. "群不变 MDP" = "group-invariant MDP"),
  singular/plural variants, and qualifier variants (e.g. "扩散策略样本效率",
  "机器人操作样本效率" and "样本效率" are all the same entry).
- Do NOT merge related-but-distinct concepts (e.g. "等变 DQN" and "等变 SAC" are
  different methods; keep them separate).
- The canonical name MUST be in {language_name}, except widely-used acronyms / proper
  names conventionally kept in English (DQN, SAC, MDP, GNN, SO(2), KOVI, dataset names).
- members are the NUMBERS of the entries in each group. Only output groups with 2 or
  more members; omit singletons.
- Return only valid JSON, no markdown fences:
{{"groups": [{{"canonical": "string", "members": [1, 5, 9]}}]}}

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
    period: str | None = None
    zotero_key: str = ""
    page_url: str = ""

    @property
    def paper_key(self) -> str:
        """Stable identifier used across the whole GRAPH (Zotero itemKey preferred)."""
        return self.zotero_key or self.page_id


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


def _paper_period(page: dict) -> str | None:
    """Best-effort publication period as YYYY-MM, falling back to YYYY."""

    def from_date(prop: dict) -> str | None:
        start = (prop.get("date") or {}).get("start") or ""
        if len(start) >= 7 and start[4] == "-":
            return start[:7]
        if len(start) >= 4 and start[:4].isdigit():
            return start[:4]
        return None

    props = page.get("properties", {})
    # 1) a publication-like date property (skip added/modified/updated bookkeeping dates)
    for name, prop in props.items():
        if prop.get("type") == "date" and not any(k in name.lower() for k in ("add", "modif", "updat")):
            value = from_date(prop)
            if value:
                return value
    # 2) a Year number property (year precision only)
    for name, prop in props.items():
        if prop.get("type") == "number" and "year" in name.lower():
            number = prop.get("number")
            if number:
                return str(int(number))
    # 3) any remaining date property
    for prop in props.values():
        if prop.get("type") == "date":
            value = from_date(prop)
            if value:
                return value
    return None


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


def _is_guide_title(title: str) -> bool:
    """Match a reading-guide subpage, including v0.4.0 versioned titles
    like ``论文解读 · 预设 1 · gpt-5.5 · high · 06-21``."""
    return any(title == base or title.startswith(f"{base} · ") for base in GUIDE_TITLES)


def _find_guide_page_id(writer: NotionWriter, paper_page_id: str) -> str | None:
    """The most relevant reading-guide subpage id, or None."""
    for block in writer._list_all_child_blocks(paper_page_id):
        if block.get("type") == "child_page" and _is_guide_title(block["child_page"].get("title", "")):
            return block["id"]
    return None


def _read_guide_text(writer: NotionWriter, paper_page_id: str) -> str:
    guide_page_id = _find_guide_page_id(writer, paper_page_id)
    if guide_page_id:
        return _blocks_to_text(writer._list_all_child_blocks(guide_page_id))
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
                period=_paper_period(page),
                zotero_key=_first_text_property(page, settings.notion_zotero_key_candidates),
                page_url=page.get("url", ""),
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
        items="\n".join(f"{index + 1}. {name}" for index, name in enumerate(unique)),
    )
    result = run_structured_extraction(settings, prompt, MergeResult)
    mapping: dict[str, str] = {}
    for group in result.groups:
        canonical = group.canonical.strip()
        if not canonical:
            continue
        for index in group.members:
            if 1 <= index <= len(unique):
                mapping[unique[index - 1]] = canonical
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


# ---- papers_meta (detail-drawer data source for the GRAPH contract) ---------

# Localized guide section headings → their canonical role, so the analysis we
# show in the detail drawer survives whatever guide language was used.
_SUMMARY_HEADINGS = frozenset(texts["summary"] for texts in TEXT.values())
_CONTRIB_HEADINGS = frozenset(texts["contributions"] for texts in TEXT.values())


def _block_sections(blocks: list[dict]) -> list[dict]:
    """Group flat guide blocks into ``[{h, b}]`` sections by heading.

    Headings open a section; paragraphs/bullets fill its body. Consecutive
    bullets become a list; loose paragraphs are joined into a string.
    """
    sections: list[dict] = []
    current: dict | None = None
    bullets: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if current is not None and bullets:
            existing = current.get("b")
            items = (existing if isinstance(existing, list) else ([existing] if existing else [])) + bullets
            current["b"] = items
            bullets = []

    for block in blocks:
        block_type = block.get("type") or ""
        payload = block.get(block_type, {}) if block_type else {}
        text = _plain(payload["rich_text"]) if isinstance(payload, dict) and "rich_text" in payload else ""
        if block_type in ("heading_1", "heading_2", "heading_3"):
            flush_bullets()
            current = {"h": text, "b": ""}
            sections.append(current)
        elif block_type == "bulleted_list_item" or block_type == "numbered_list_item":
            if text:
                bullets.append(text)
        elif text:
            flush_bullets()
            if current is None:
                current = {"h": "", "b": ""}
                sections.append(current)
            prev = current.get("b")
            current["b"] = f"{prev}\n{text}".strip() if isinstance(prev, str) and prev else (prev or text)
    flush_bullets()
    # The first section is usually the guide title heading with an empty body; drop empties.
    return [s for s in sections if s.get("h") or s.get("b")]


def _parse_guide_analysis(writer: NotionWriter, paper_page_id: str) -> tuple[list[dict], str]:
    """Return ``(analysis, contribution)`` parsed from the paper's guide subpage."""
    guide_page_id = _find_guide_page_id(writer, paper_page_id)
    if not guide_page_id:
        return [], ""
    sections = [
        s
        for s in _block_sections(writer._list_all_child_blocks(guide_page_id))
        if s["h"] not in GUIDE_TITLES
    ]
    contribution = ""
    for section in sections:
        if section["h"] in _SUMMARY_HEADINGS and isinstance(section["b"], str):
            contribution = section["b"]
            break
    if not contribution:
        for section in sections:
            if section["h"] in _CONTRIB_HEADINGS:
                body = section["b"]
                contribution = body[0] if isinstance(body, list) and body else (body if isinstance(body, str) else "")
                break
    return sections, contribution


def _build_papers_meta(settings: Settings, writer: NotionWriter, papers: list[PaperContent]) -> dict[str, dict]:
    """Assemble ``papers_meta`` keyed by paper_key, joining Notion + Zotero SQLite.

    Every field is optional; the page degrades gracefully when one is missing.
    """
    try:
        library = ZoteroLibrary(settings.zotero_data_dir)
    except Exception:  # noqa: BLE001 - Zotero DB may be unavailable; meta still works from Notion
        library = None

    meta: dict[str, dict] = {}
    for paper in papers:
        entry: dict = {}
        if paper.title:
            entry["title"] = paper.title
        if paper.authors:
            entry["authors"] = [a.strip() for a in re.split(r"[;,]|\band\b", paper.authors) if a.strip()]
        if paper.abstract:
            entry["abstract"] = paper.abstract
        if paper.page_url:
            entry["notion_url"] = paper.page_url

        # Zotero SQLite enriches with venue/tags/authors and the PDF deep link.
        record = None
        if library is not None and paper.zotero_key:
            try:
                record = library.get_document_by_key(paper.zotero_key)
            except Exception:  # noqa: BLE001
                record = None
        if record is not None:
            if record.authors:
                entry["authors"] = record.authors
            venue = record.publication or record.proceedings_title
            if venue:
                entry["venue"] = venue
            if record.abstract and "abstract" not in entry:
                entry["abstract"] = record.abstract
            if record.tags:
                entry["tags"] = record.tags
            pdf_key = next((a.item_key for a in record.attachments if a.content_type == "application/pdf"), "")
            if pdf_key:
                entry["pdf"] = f"zotero://open-pdf/library/items/{pdf_key}"

        # AI guide → structured analysis + one-line contribution.
        analysis, contribution = _parse_guide_analysis(writer, paper.page_id)
        if analysis:
            entry["analysis"] = analysis
        if contribution:
            entry["contribution"] = contribution

        if entry:
            meta[paper.paper_key] = entry
    return meta


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
        _merge(graph, extraction, paper.paper_key)

    before = {key: len(value) for key, value in graph.items()}
    node_names = [n.name for n in graph["problems"].values()] + [n.name for n in graph["concepts"].values()]
    gap_names = [n.name for n in graph["gaps"].values()]
    nodemap = _canonical_map(settings, node_names, "research problems and concepts")
    gapmap = _canonical_map(settings, gap_names, "research gaps")
    graph = _apply_merge(graph, nodemap, gapmap)

    graph_json = _graph_to_json(graph)
    graph_json["paper_dates"] = {paper.paper_key: paper.period for paper in papers if paper.period}
    graph_json["papers_meta"] = _build_papers_meta(settings, writer, papers)
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
            f"MERGED_ALIASES:nodes={len(nodemap)} gaps={len(gapmap)}",
            f"PAPERS_META:{len(graph_json['papers_meta'])}",
            f"GRAPH_JSON:{graph_path}",
            "Run `paper-notion-flow map render` to open the map, or `map sync` to write into Notion.",
        ]
    )


def _rel_ids(page: dict, name: str) -> list[str]:
    prop = page.get("properties", {}).get(name, {})
    if prop.get("type") != "relation":
        return []
    return [item.get("id") for item in prop.get("relation", []) if item.get("id")]


def _select_val(page: dict, name: str) -> str:
    prop = page.get("properties", {}).get(name, {})
    selected = prop.get("select") if prop.get("type") == "select" else None
    return selected.get("name", "") if selected else ""


def _rich_val(page: dict, name: str) -> str:
    prop = page.get("properties", {}).get(name, {})
    return _plain(prop.get("rich_text", [])) if prop.get("type") == "rich_text" else ""


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value[:2000]}}]}


def _select_value(value: str) -> dict:
    return {"select": {"name": value[:100]}}


def _relation_ids(ids: list[str]) -> dict:
    return {"relation": [{"id": page_id} for page_id in ids]}


class _MapDatabase:
    """Minimal upsert wrapper for one research-map database (data-source aware)."""

    def __init__(self, client: Client, database_id: str) -> None:
        self.client = client
        self.database_id = database_id
        database = client.databases.retrieve(database_id)
        if "properties" in database:
            self.properties = database["properties"]
            self.data_source_id = None
        else:
            self.data_source_id = database["data_sources"][0]["id"]
            self.properties = client.data_sources.retrieve(self.data_source_id)["properties"]
        self.title_property = next(
            (name for name, schema in self.properties.items() if schema["type"] == "title"), "Name"
        )

    def has(self, prop: str, prop_type: str) -> bool:
        schema = self.properties.get(prop)
        return bool(schema) and schema["type"] == prop_type

    def _query(self, **kwargs):
        if self.data_source_id:
            return self.client.data_sources.query(data_source_id=self.data_source_id, **kwargs)
        return self.client.databases.query(database_id=self.database_id, **kwargs)

    def query_all(self) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self._query(**kwargs)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
        return results

    def find_by_title(self, name: str) -> str | None:
        response = self._query(
            page_size=1, filter={"property": self.title_property, "title": {"equals": name[:2000]}}
        )
        results = response.get("results", [])
        return results[0]["id"] if results else None

    def upsert(self, name: str, properties: dict) -> tuple[str, bool]:
        payload = dict(properties)
        payload[self.title_property] = {"title": [{"text": {"content": name[:2000]}}]}
        existing = self.find_by_title(name)
        if existing:
            self.client.pages.update(page_id=existing, properties=payload)
            return existing, False
        parent = {"data_source_id": self.data_source_id} if self.data_source_id else {"database_id": self.database_id}
        page = self.client.pages.create(parent=parent, properties=payload)
        return page["id"], True


def sync_research_map(settings: Settings, *, data_dir: Path, dry_run: bool = False) -> str:
    """Write graph.json into the Problems/Concepts/Relations/Gaps databases.

    Step 3. Upserts by title (name) so re-runs update rather than duplicate.
    """
    if not settings.notion_token:
        raise RuntimeError("NOTION_TOKEN is required.")
    required = {
        "Problems": settings.notion_problems_database_id,
        "Concepts": settings.notion_concepts_database_id,
        "Relations": settings.notion_relations_database_id,
        "Gaps": settings.notion_gaps_database_id,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing database ids for: {', '.join(missing)}. Run `map init` / set them in .env.")

    graph_path = data_dir / "research-map" / "graph.json"
    if not graph_path.exists():
        raise RuntimeError(f"{graph_path} not found. Run `map build` first.")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    # Paper key (Zotero itemKey, matching the GRAPH) -> page id, so nodes can
    # link back to the paper rows. Fall back to the Notion page id for papers
    # without a Zotero key (keeps parity with build's paper_key).
    writer = NotionWriter(settings)
    paper_map: dict[str, str] = {}
    for page in writer._iter_database_pages():
        zotero_key = _first_text_property(page, settings.notion_zotero_key_candidates)
        paper_map[zotero_key or page["id"]] = page["id"]

    def paper_links(keys: list[str]) -> list[str]:
        return [paper_map[key] for key in keys if key in paper_map]

    if dry_run:
        matched = sum(len(paper_links(n["papers"])) for cat in ("problems", "concepts", "gaps") for n in graph[cat])
        total = sum(len(n["papers"]) for cat in ("problems", "concepts", "gaps") for n in graph[cat])
        return "\n".join(
            [
                "MAP_SYNC:DRY_RUN",
                f"PROBLEMS:{len(graph['problems'])}",
                f"CONCEPTS:{len(graph['concepts'])}",
                f"RELATIONS:{len(graph['relations'])}",
                f"GAPS:{len(graph['gaps'])}",
                f"PAPER_LINKS:matched {matched}/{total}",
                "No pages written (dry run).",
            ]
        )

    client = Client(auth=settings.notion_token)
    problems_db = _MapDatabase(client, required["Problems"])
    concepts_db = _MapDatabase(client, required["Concepts"])
    relations_db = _MapDatabase(client, required["Relations"])
    gaps_db = _MapDatabase(client, required["Gaps"])

    def counts() -> dict[str, int]:
        return {"created": 0, "updated": 0}

    stats = {"problems": counts(), "concepts": counts(), "gaps": counts(), "relations": counts()}

    problem_ids: dict[str, str] = {}
    for problem in graph["problems"]:
        props: dict = {}
        if problems_db.has("Description", "rich_text"):
            props["Description"] = _rich_text(problem["description"])
        if problems_db.has("Papers", "relation"):
            props["Papers"] = _relation_ids(paper_links(problem["papers"]))
        page_id, created = problems_db.upsert(problem["name"], props)
        problem_ids[problem["name"]] = page_id
        stats["problems"]["created" if created else "updated"] += 1

    concept_ids: dict[str, str] = {}
    for concept in graph["concepts"]:
        props = {}
        if concept.get("kind") and concepts_db.has("Kind", "select"):
            props["Kind"] = _select_value(concept["kind"])
        if concepts_db.has("Description", "rich_text"):
            props["Description"] = _rich_text(concept["description"])
        if concepts_db.has("Papers", "relation"):
            props["Papers"] = _relation_ids(paper_links(concept["papers"]))
        page_id, created = concepts_db.upsert(concept["name"], props)
        concept_ids[concept["name"]] = page_id
        stats["concepts"]["created" if created else "updated"] += 1

    for gap in graph["gaps"]:
        props = {}
        if gaps_db.has("Rationale", "rich_text"):
            props["Rationale"] = _rich_text(gap["rationale"])
        if gaps_db.has("Papers", "relation"):
            props["Papers"] = _relation_ids(paper_links(gap["papers"]))
        if gaps_db.has("Related", "relation"):
            props["Related"] = _relation_ids([concept_ids[r] for r in gap["related"] if r in concept_ids])
        _, created = gaps_db.upsert(gap["name"], props)
        stats["gaps"]["created" if created else "updated"] += 1

    for relation in graph["relations"]:
        name = f"{relation['source']} → {relation['target']} ({relation['type']})"
        props = {}
        if relations_db.has("Source", "rich_text"):
            props["Source"] = _rich_text(relation["source"])
        if relations_db.has("Target", "rich_text"):
            props["Target"] = _rich_text(relation["target"])
        if relation.get("type") and relations_db.has("Type", "select"):
            props["Type"] = _select_value(relation["type"])
        if relations_db.has("Rationale", "rich_text"):
            props["Rationale"] = _rich_text(relation["rationale"])
        _, created = relations_db.upsert(name, props)
        stats["relations"]["created" if created else "updated"] += 1

    return "\n".join(
        [
            "MAP_SYNC:OK",
            f"PROBLEMS:created {stats['problems']['created']} updated {stats['problems']['updated']}",
            f"CONCEPTS:created {stats['concepts']['created']} updated {stats['concepts']['updated']}",
            f"GAPS:created {stats['gaps']['created']} updated {stats['gaps']['updated']}",
            f"RELATIONS:created {stats['relations']['created']} updated {stats['relations']['updated']}",
            f"PAPERS_INDEXED:{len(paper_map)}",
        ]
    )


def export_from_notion(settings: Settings, *, data_dir: Path) -> str:
    """Read the 4 research-map databases back into graph.json (Notion as source of truth).

    The Python prototype of the Cloudflare /api/graph function: reflects manual
    edits made in Notion (merged nodes, edited descriptions, adjusted relations).
    """
    if not settings.notion_token:
        raise RuntimeError("NOTION_TOKEN is required.")
    required = {
        "Problems": settings.notion_problems_database_id,
        "Concepts": settings.notion_concepts_database_id,
        "Relations": settings.notion_relations_database_id,
        "Gaps": settings.notion_gaps_database_id,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing database ids for: {', '.join(missing)}. Run `map init` / set them in .env.")

    client = Client(auth=settings.notion_token)
    papers = NotionWriter(settings)
    pid_title: dict[str, str] = {}
    paper_dates: dict[str, str] = {}
    for page in papers._iter_database_pages():
        title = _title_property(page)
        if not title:
            continue
        pid_title[page["id"]] = title
        period = _paper_period(page)
        if period:
            paper_dates[title] = period

    def titles(ids: list[str]) -> list[str]:
        return [pid_title[i] for i in ids if i in pid_title]

    problems_db = _MapDatabase(client, required["Problems"])
    concepts_db = _MapDatabase(client, required["Concepts"])
    relations_db = _MapDatabase(client, required["Relations"])
    gaps_db = _MapDatabase(client, required["Gaps"])

    concepts = []
    cid_name: dict[str, str] = {}
    for page in concepts_db.query_all():
        name = _title_property(page)
        cid_name[page["id"]] = name
        concepts.append(
            {
                "name": name,
                "kind": _select_val(page, "Kind"),
                "description": _rich_val(page, "Description"),
                "papers": titles(_rel_ids(page, "Papers")),
            }
        )
    problems = [
        {
            "name": _title_property(page),
            "description": _rich_val(page, "Description"),
            "papers": titles(_rel_ids(page, "Papers")),
        }
        for page in problems_db.query_all()
    ]
    relations = [
        {
            "source": _rich_val(page, "Source"),
            "target": _rich_val(page, "Target"),
            "type": _select_val(page, "Type"),
            "rationale": _rich_val(page, "Rationale"),
            "papers": [],
        }
        for page in relations_db.query_all()
    ]
    gaps = [
        {
            "name": _title_property(page),
            "rationale": _rich_val(page, "Rationale"),
            "related": [cid_name[i] for i in _rel_ids(page, "Related") if i in cid_name],
            "papers": titles(_rel_ids(page, "Papers")),
        }
        for page in gaps_db.query_all()
    ]

    graph = {
        "problems": problems,
        "concepts": concepts,
        "relations": relations,
        "gaps": gaps,
        "paper_dates": paper_dates,
    }
    out_path = data_dir / "research-map" / "graph.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    return "\n".join(
        [
            "MAP_EXPORT:OK",
            f"PROBLEMS:{len(problems)}",
            f"CONCEPTS:{len(concepts)}",
            f"RELATIONS:{len(relations)}",
            f"GAPS:{len(gaps)}",
            f"PAPERS_DATED:{len(paper_dates)}",
            f"GRAPH_JSON:{out_path}",
        ]
    )


def inject_graph(template: str, graph: dict) -> str:
    """Inject ``window.GRAPH`` before the page's first <script>.

    The page's data block is ``window.GRAPH = window.GRAPH || {…demo…}``, so a
    GRAPH defined earlier wins and the demo fallback is never used.
    """
    injection = f"<script>window.GRAPH = {json.dumps(graph, ensure_ascii=False)};</script>\n"
    index = template.find("<script>")
    if index == -1:
        return template + injection
    return template[:index] + injection + template[index:]


def render_research_map(
    settings: Settings,
    *,
    data_dir: Path,
    output: Path | None = None,
    serve: bool = False,
) -> str:
    """Render the map by injecting graph.json into research-map.html (step 4).

    research-map.html is a self-contained, zero-dependency frontend driven by a
    global ``window.GRAPH``; no internet is needed to view it.
    """
    graph_path = data_dir / "research-map" / "graph.json"
    if not graph_path.exists():
        raise RuntimeError(f"{graph_path} not found. Run `map build` first.")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    template = (Path(__file__).parent / "templates" / "research-map.html").read_text(encoding="utf-8")
    html = inject_graph(template, graph)

    out_path = output or (data_dir / "research-map" / "research-map.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    counts = (
        f"{len(graph.get('problems', []))} problems, {len(graph.get('concepts', []))} concepts, "
        f"{len(graph.get('relations', []))} relations, {len(graph.get('gaps', []))} gaps, "
        f"{len(graph.get('papers_meta', {}))} papers"
    )

    if serve:
        import functools
        import http.server
        import socketserver

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out_path.parent))
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
            port = httpd.server_address[1]
            print(f"MAP_RENDER:OK ({counts})")
            print(f"Serving at http://127.0.0.1:{port}/{out_path.name}  (Ctrl+C to stop)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
        return "Server stopped."

    return "\n".join(
        [
            "MAP_RENDER:OK",
            f"GRAPH:{counts}",
            f"RESEARCH_MAP_HTML:{out_path}",
            "Open it in a browser — the page is self-contained (no internet needed).",
        ]
    )
