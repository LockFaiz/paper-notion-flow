from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _zotero_profile_roots() -> list[Path]:
    roots: list[Path] = []

    appdata = os.getenv("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Zotero" / "Zotero" / "Profiles")

    userprofile = os.getenv("USERPROFILE")
    if userprofile:
        roots.append(Path(userprofile) / "AppData" / "Roaming" / "Zotero" / "Zotero" / "Profiles")

    mounted_windows_users = Path("/mnt/c/Users")
    if mounted_windows_users.exists():
        for user_dir in mounted_windows_users.iterdir():
            roots.append(user_dir / "AppData" / "Roaming" / "Zotero" / "Zotero" / "Profiles")

    unique_roots: list[Path] = []
    seen = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)
    return unique_roots


def _read_zotero_pref(pref_name: str) -> str | None:
    for profile_root in _zotero_profile_roots():
        try:
            exists = profile_root.exists()
        except OSError:
            continue
        if not exists:
            continue
        try:
            prefs_paths = sorted(profile_root.glob("*/prefs.js"))
        except OSError:
            continue
        for prefs_path in prefs_paths:
            try:
                for line in prefs_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    marker = f'user_pref("{pref_name}", "'
                    if marker in line:
                        return line.split(marker, 1)[1].rsplit('"', 2)[0]
            except OSError:
                continue
    return None


def _normalize_path(value: str) -> Path:
    if os.name != "nt" and re.match(r"^[A-Za-z]:\\", value):
        drive = value[0].lower()
        rest = value[2:].replace("\\", "/")
        return Path(f"/mnt/{drive}{rest}")
    return Path(value)


def _default_zotero_data_dir() -> Path:
    configured = os.getenv("ZOTERO_DATA_DIR") or _read_zotero_pref("extensions.zotero.dataDir")
    if configured:
        return _normalize_path(configured)
    if os.name != "nt":
        mounted_windows_users = Path("/mnt/c/Users")
        try:
            exists = mounted_windows_users.exists()
        except OSError:
            exists = False
        if exists:
            try:
                user_dirs = list(mounted_windows_users.iterdir())
            except OSError:
                user_dirs = []
            for user_dir in user_dirs:
                candidate = user_dir / "Zotero"
                try:
                    candidate_exists = candidate.exists()
                except OSError:
                    continue
                if candidate_exists:
                    return candidate
    return Path.home() / "Zotero"


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _guide_language() -> str:
    value = os.getenv("PAPER_FLOW_GUIDE_LANGUAGE", "zh-CN").strip().lower()
    aliases = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "chinese": "zh-CN",
        "cn": "zh-CN",
        "en": "en",
        "en-us": "en",
        "english": "en",
        "ja": "ja",
        "jp": "ja",
        "ja-jp": "ja",
        "japanese": "ja",
        "ko": "ko",
        "ko-kr": "ko",
        "korean": "ko",
        "fr": "fr",
        "fr-fr": "fr",
        "french": "fr",
        "de": "de",
        "de-de": "de",
        "german": "de",
        "es": "es",
        "es-es": "es",
        "spanish": "es",
    }
    return aliases.get(value, "zh-CN")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    notion_token: str | None = os.getenv("NOTION_TOKEN")
    notion_database_id: str | None = os.getenv("NOTION_DATABASE_ID")

    notion_title_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_TITLE_PROPERTY_CANDIDATES", "Title,Name")
    )
    notion_zotero_key_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_ZOTERO_KEY_PROPERTY_CANDIDATES", "Zotero Key,Item Key,Key")
    )
    notion_zotero_uri_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_ZOTERO_URI_PROPERTY_CANDIDATES", "Zotero URI,Zotero Link")
    )
    notion_url_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_URL_PROPERTY_CANDIDATES", "URL,Source URL,Link")
    )
    notion_authors_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_AUTHORS_PROPERTY_CANDIDATES", "Authors,Author")
    )
    notion_type_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_TYPE_PROPERTY_CANDIDATES", "Item Type,Doc Type,Type")
    )
    notion_collection_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "NOTION_COLLECTION_PROPERTY_CANDIDATES",
            "Collections,Collection,Topic,Topics,Tags,Keywords,Category,Subject",
        )
    )
    notion_tag_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_TAG_PROPERTY_CANDIDATES", "Tags,Keywords")
    )
    notion_doi_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_DOI_PROPERTY_CANDIDATES", "DOI")
    )
    notion_date_added_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_DATE_ADDED_PROPERTY_CANDIDATES", "Date Added,Added")
    )
    notion_date_modified_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_DATE_MODIFIED_PROPERTY_CANDIDATES", "Date Modified,Modified")
    )
    notion_file_path_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_FILE_PATH_PROPERTY_CANDIDATES", "File Path,PDF Path")
    )
    notion_publication_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "NOTION_PUBLICATION_PROPERTY_CANDIDATES",
            "Publication,Journal,Publication Title",
        )
    )
    notion_proceedings_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_PROCEEDINGS_PROPERTY_CANDIDATES", "Proceedings Title,Proceedings")
    )
    notion_abstract_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_ABSTRACT_PROPERTY_CANDIDATES", "Abstract,Abstract Note")
    )
    notion_citation_key_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_CITATION_KEY_PROPERTY_CANDIDATES", "Citation Key,BibTeX Key")
    )
    notion_ai_status_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "NOTION_AI_STATUS_PROPERTY_CANDIDATES",
            "AI Status,Status,\u7ba1\u7406\u7528\uff5c\u6587\u732e\u7ba1\u7406\u72b6\u6001",
        )
    )
    notion_ai_updated_candidates: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("NOTION_AI_UPDATED_PROPERTY_CANDIDATES", "AI Last Updated,Last Updated")
    )

    # Research map (feature/research-map): the 4 extra Notion databases plus the
    # Landscape page that links to the rendered graph. Papers DB is the existing
    # notion_database_id above.
    notion_problems_database_id: str | None = os.getenv("NOTION_PROBLEMS_DATABASE_ID")
    notion_concepts_database_id: str | None = os.getenv("NOTION_CONCEPTS_DATABASE_ID")
    notion_relations_database_id: str | None = os.getenv("NOTION_RELATIONS_DATABASE_ID")
    notion_gaps_database_id: str | None = os.getenv("NOTION_GAPS_DATABASE_ID")
    notion_landscape_page_id: str | None = os.getenv("NOTION_LANDSCAPE_PAGE_ID")

    ai_backend: str = os.getenv("AI_BACKEND", "command")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    local_ai_command: str = os.getenv("LOCAL_AI_COMMAND", "codex")
    local_ai_args: list[str] = field(
        default_factory=lambda: shlex.split(os.getenv("LOCAL_AI_ARGS", "exec --skip-git-repo-check"))
    )
    local_ai_prompt_mode: str = os.getenv("LOCAL_AI_PROMPT_MODE", "positional")
    local_ai_template: str | None = os.getenv("LOCAL_AI_TEMPLATE")
    guide_language: str = field(default_factory=_guide_language)
    sync_notes: bool = field(default_factory=lambda: _bool_env("PAPER_FLOW_SYNC_NOTES", False))

    zotero_data_dir: Path = field(default_factory=_default_zotero_data_dir)
