from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI

from .config import Settings
from .models import DocumentRecord, ReadingGuide


LANGUAGE_NAMES = {
    "zh-CN": "Simplified Chinese",
    "en": "clear English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}


DEFAULT_INSTRUCTIONS = {
    "zh-CN": """用简体中文大白话解读这篇论文，不要做全文翻译。

目标：读者只看这份解读，就能把握论文的问题意识、研究逻辑、理论或机制、实验设计、关键数据结论和局限。

写法要求：
- 准确、精简、具体，像研究组里靠谱的师兄师姐在讲论文，不要宣传语。
- 优先说明：这篇论文解决什么问题；相关工作有哪些路线、如何分类；本文相对这些路线的差异和优点；核心机制/理论怎么理解；做了哪些实验；哪些结论和数字支撑了主张；还有哪些薄弱点或疑问。
- 不要写“为什么值得读”“阅读时追问的问题”“阅读计划”这类泛泛栏目。
- 不要逐节翻译，不要复述摘要，不要把全文从头到尾改写一遍。
- 如果 Zotero 元数据缺失，请尽量从 PDF/正文中推断标题、作者和年份。
- 如果正文、图注或表格文本里能读到关键信息，请解释关键图/表在证明什么；如果提取文本看不到图表细节，就明确说明无法可靠读取，不要编造。
- 控制在 10 分钟内读完，优先高信息密度。""",
    "en": """Explain this paper in clear English, not as a full-text translation.

Goal: after reading this guide, the reader should understand the paper's problem, research logic, theory or mechanism, experiments, main data-backed conclusions, and limitations.

Writing requirements:
- Be accurate, concise, and concrete. Write like a senior labmate explaining the paper.
- Prioritize: the problem being solved; how related work can be grouped; how this paper differs; the core mechanism/theory; what experiments were run; what conclusions and numbers support the claims; what remains weak or questionable.
- Do not write generic sections such as "why it is worth reading", "questions to ask while reading", or a reading plan.
- Do not translate section by section, do not restate the abstract, and do not paraphrase the whole paper end to end.
- If Zotero metadata is missing, infer title, authors, and year from the PDF/body when possible.
- Explain high-value figures/tables when captions or extracted table text are available. If visual content is not available from extraction, say that instead of inventing details.
- Keep the guide readable in under 10 minutes.""",
}


def _default_instructions(guide_language: str) -> str:
    if guide_language in DEFAULT_INSTRUCTIONS:
        return DEFAULT_INSTRUCTIONS[guide_language]
    language_name = _language_name(guide_language)
    return f"""Explain this paper in {language_name}, not as a full-text translation.

Goal: after reading this guide, the reader should understand the paper's problem, research logic, theory or mechanism, experiments, main data-backed conclusions, and limitations.

Writing requirements:
- Write all explanatory content in {language_name}.
- Be accurate, concise, and concrete. Write like a senior labmate explaining the paper.
- Prioritize: the problem being solved; how related work can be grouped; how this paper differs; the core mechanism/theory; what experiments were run; what conclusions and numbers support the claims; what remains weak or questionable.
- Do not write generic sections such as "why it is worth reading", "questions to ask while reading", or a reading plan.
- Do not translate section by section, do not restate the abstract, and do not paraphrase the whole paper end to end.
- If Zotero metadata is missing, infer title, authors, and year from the PDF/body when possible.
- Explain high-value figures/tables when captions or extracted table text are available. If visual content is not available from extraction, say that instead of inventing details.
- Keep the guide readable in under 10 minutes."""


def _language_name(guide_language: str) -> str:
    return LANGUAGE_NAMES.get(guide_language, LANGUAGE_NAMES["zh-CN"])


PROMPT_TEMPLATE = """You are helping a researcher understand a paper or technical document.

Return only valid JSON. Do not wrap the JSON in markdown fences.

User reading-guide requirements, highest priority:
{instructions}

Use exactly this JSON schema:
{{
  "title": "string",
  "authors": ["string"],
  "year": 2025,
  "summary": "string",
  "problem": "string",
  "related_work": ["string"],
  "mechanism": ["string"],
  "contributions": ["string"],
  "figures_tables": ["string"],
  "experiments": ["string"],
  "conclusions": ["string"],
  "limitations": ["string"]
}}

Schema requirements:
- Keep the paper title in its original language if possible.
- Infer authors and year from the PDF or source context when Zotero metadata is missing.
- If a field cannot be inferred confidently, use an empty list or null instead of hallucinating.
- All explanatory fields must be written in {language_name}.
- Prefer concrete mechanisms, comparisons, metrics, and conclusions over generic comments.
- In figures_tables, explain only high-value figures/tables that can be read from the PDF, captions, or source context. For each one, say what it demonstrates and how it supports or weakens the paper's argument.
- If figure/table content is not visible, say so briefly instead of inventing visual details.
- Write ALL mathematics as LaTeX: inline math wrapped in single dollar signs like $V_h^\\pi(s)$, and standalone display equations wrapped in double dollar signs ($$...$$). Never typeset math with Unicode superscripts/subscripts or lookalike glyphs — they render as unreadable plain text.
- Do not state AI model names, versions, reasoning effort, or token counts anywhere in the fields; that metadata is appended programmatically.

Context:
- source kind: {source_kind}
- Zotero key: {zotero_key}
- title hint: {title_hint}
- item type: {item_type}
- collections: {collections}
- authors: {authors}
- source url: {source_url}
- local PDF path: {pdf_path}

Source material:
If a local PDF path is present, use your native file/PDF-reading capability to read that PDF directly. Treat the PDF as the primary evidence. The text below is only fallback context for items without a readable local PDF.

{content}
"""


def build_reading_guide(
    settings: Settings,
    record: DocumentRecord,
    content: str,
    *,
    source_pdf_path: Path | None = None,
    prompt_override: str | None = None,
) -> ReadingGuide:
    prompt = _compose_prompt(
        prompt_override=prompt_override,
        guide_language=settings.guide_language,
        source_kind=record.source_kind,
        zotero_key=record.zotero_key or "",
        title_hint=record.title,
        item_type=record.item_type or "",
        collections=", ".join(record.collections),
        authors=", ".join(record.authors),
        source_url=record.source_url or "",
        pdf_path=_format_prompt_path(source_pdf_path),
        content=content,
    )

    if settings.ai_backend == "openai":
        return _build_with_openai(settings, prompt)
    if settings.ai_backend == "command":
        return _build_with_command(settings, prompt)
    raise RuntimeError(f"Unsupported AI backend: {settings.ai_backend}")


def _compose_prompt(
    *,
    prompt_override: str | None,
    guide_language: str,
    source_kind: str,
    zotero_key: str,
    title_hint: str,
    item_type: str,
    collections: str,
    authors: str,
    source_url: str,
    pdf_path: str,
    content: str,
) -> str:
    language_name = _language_name(guide_language)
    values = {
        "instructions": (prompt_override or _default_instructions(guide_language)).strip(),
        "language_name": language_name,
        "source_kind": source_kind,
        "zotero_key": zotero_key,
        "title_hint": title_hint,
        "item_type": item_type,
        "collections": collections,
        "authors": authors,
        "source_url": source_url,
        "pdf_path": pdf_path,
        "content": content,
    }

    if prompt_override and any(
        token in prompt_override
        for token in ("{content}", "{title_hint}", "{authors}", "{source_url}", "{pdf_path}")
    ):
        return prompt_override.format(**values)

    return PROMPT_TEMPLATE.format(**values)


def apply_inferred_metadata(record: DocumentRecord, guide: ReadingGuide) -> None:
    if guide.title:
        record.title = guide.title
    if guide.authors and not record.authors:
        record.authors = guide.authors
    if guide.year and not _record_has_year(record):
        record.date = str(guide.year)


def _build_with_openai(settings: Settings, prompt: str) -> ReadingGuide:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when AI_BACKEND=openai.")

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=settings.openai_model,
        input=[{"role": "user", "content": prompt}],
        text_format=ReadingGuide,
    )
    return response.output_parsed


def _build_with_command(settings: Settings, prompt: str) -> ReadingGuide:
    raw_output = _run_local_command(settings, prompt)
    payload = _extract_json(raw_output)
    guide = ReadingGuide.model_validate(payload)
    guide.generation_info = _compose_generation_info(settings)
    return guide


# Metadata captured from the most recent local CLI run. Observed values beat
# configured ones: with no --model flag the CLI still REPORTS what it actually
# used (codex prints a "model: gpt-5.5" header; claude's JSON envelope carries
# modelUsage), so the footer can always name the real model.
_LAST_RUN_TOKENS: str | None = None
_LAST_RUN_MODEL: str | None = None
_LAST_RUN_EFFORT: str | None = None
_LAST_RUN_COST: str | None = None


def _capture_run_metadata(output: str) -> None:
    global _LAST_RUN_TOKENS, _LAST_RUN_MODEL, _LAST_RUN_EFFORT
    usage_match = re.search(r"tokens used\s*:?\s*([0-9][0-9,]*)", output, re.IGNORECASE)
    if usage_match:
        _LAST_RUN_TOKENS = usage_match.group(1)
    model_match = re.search(r"^model:\s*(\S+)", output, re.MULTILINE)
    if model_match:
        _LAST_RUN_MODEL = model_match.group(1)
    effort_match = re.search(r"^reasoning effort:\s*(\S+)", output, re.MULTILINE)
    if effort_match:
        _LAST_RUN_EFFORT = effort_match.group(1)


def _compose_generation_info(settings: Settings) -> str:
    args = list(settings.local_ai_args)
    tool = Path(settings.local_ai_command).name or settings.local_ai_command

    model = _LAST_RUN_MODEL or ""
    if not model:
        for flag in ("--model", "-m"):
            if flag in args:
                index = args.index(flag)
                if index + 1 < len(args):
                    model = args[index + 1]
                    break
    effort = _LAST_RUN_EFFORT or ""
    if not effort:
        for index, arg in enumerate(args):
            if arg == "--effort" and index + 1 < len(args):
                effort = args[index + 1]
                break
            match = re.search(r'model_reasoning_effort="?([A-Za-z]+)"?', arg)
            if match:
                effort = match.group(1)
                break

    parts = [tool, model or "default model", f"effort {effort}" if effort else "default effort"]
    if _LAST_RUN_TOKENS:
        parts.append(f"{_LAST_RUN_TOKENS} tokens")
    if _LAST_RUN_COST:
        parts.append(f"${_LAST_RUN_COST}")
    return " · ".join(parts)


def last_run_model_effort(settings: Settings) -> tuple[str, str]:
    """The model and reasoning effort of the most recent generation (observed > configured)."""
    args = list(settings.local_ai_args)
    model = _LAST_RUN_MODEL or ""
    if not model:
        for flag in ("--model", "-m"):
            if flag in args:
                index = args.index(flag)
                if index + 1 < len(args):
                    model = args[index + 1]
                    break
    if not model and settings.ai_backend == "openai":
        model = settings.openai_model
    effort = _LAST_RUN_EFFORT or ""
    if not effort:
        for index, arg in enumerate(args):
            if arg == "--effort" and index + 1 < len(args):
                effort = args[index + 1]
                break
            match = re.search(r'model_reasoning_effort="?([A-Za-z]+)"?', arg)
            if match:
                effort = match.group(1)
                break
    return model or "default", effort or "default"


def _run_local_command(settings: Settings, prompt: str) -> str:
    if settings.local_ai_template:
        with tempfile.TemporaryDirectory(prefix="paper-notion-flow-") as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.txt"
            output_path = Path(tmpdir) / "response.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            command = settings.local_ai_template.format(
                prompt_file=prompt_path,
                output_file=output_path,
            )
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Local AI command failed.\n"
                    f"Command: {command}\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if output_path.exists():
                return output_path.read_text(encoding="utf-8")
            return completed.stdout

    global _LAST_RUN_TOKENS, _LAST_RUN_MODEL, _LAST_RUN_EFFORT, _LAST_RUN_COST
    _LAST_RUN_TOKENS = _LAST_RUN_MODEL = _LAST_RUN_EFFORT = _LAST_RUN_COST = None

    resolved_command = _resolve_local_command(settings.local_ai_command)
    command = [resolved_command, *settings.local_ai_args]
    env = _build_local_ai_env(resolved_command)
    output_file: Path | None = None
    claude_json_envelope = False
    if _is_codex_exec_command(command):
        if "--skip-git-repo-check" not in command:
            command.append("--skip-git-repo-check")
        if "-o" not in command and "--output-last-message" not in command:
            tmpdir = tempfile.mkdtemp(prefix="paper-notion-flow-codex-")
            output_file = Path(tmpdir) / "last_message.txt"
            command.extend(["--output-last-message", str(output_file)])
    elif _is_claude_print_command(command):
        # The JSON envelope reports the RESOLVED model (e.g. an alias like
        # "opus" -> claude-opus-4-8), token usage, and cost — the only way to
        # get real generation metadata out of claude non-interactively.
        if "--output-format" not in command:
            command.extend(["--output-format", "json"])
            claude_json_envelope = True
    if settings.local_ai_prompt_mode == "stdin":
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    else:
        completed = subprocess.run(
            [*command, prompt],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    if completed.returncode != 0:
        raise RuntimeError(
            "Local AI command failed.\n"
            f"Command: {shlex.join(command)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    _capture_run_metadata(f"{completed.stdout}\n{completed.stderr}")
    if claude_json_envelope:
        unwrapped = _unwrap_claude_envelope(completed.stdout)
        if unwrapped is not None:
            return unwrapped
    if output_file and output_file.exists():
        return output_file.read_text(encoding="utf-8")
    return completed.stdout


def _is_claude_print_command(command: list[str]) -> bool:
    return Path(command[0]).name.startswith("claude") and "--print" in command


def _unwrap_claude_envelope(stdout: str) -> str | None:
    """Extracts the guide text and run metadata from claude's JSON envelope."""
    global _LAST_RUN_TOKENS, _LAST_RUN_MODEL, _LAST_RUN_COST
    try:
        envelope = json.loads(stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict) or "result" not in envelope:
        return None
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        _LAST_RUN_MODEL = next(iter(model_usage))
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        total = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        if total:
            _LAST_RUN_TOKENS = f"{total:,}"
    cost = envelope.get("total_cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        _LAST_RUN_COST = f"{cost:.4f}"
    return str(envelope.get("result") or "")


def _extract_json(raw_output: str) -> dict:
    text = raw_output.strip()
    if text.startswith("```"):
        parts = [part for part in text.split("```") if part.strip()]
        if parts:
            text = parts[-1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"Local AI output did not contain JSON.\nOutput was:\n{text}")
    return json.loads(text[start : end + 1])


def _format_prompt_path(path: Path | None) -> str:
    if not path:
        return ""
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_local_command(command: str) -> str:
    if _looks_like_path(command):
        return command

    # Trust the inherited PATH FIRST. The launcher script already curated it
    # (runtime-native binaries, nvm default loaded, Windows /mnt entries
    # stripped under WSL). Our own directory scan sorts nvm versions as
    # strings, so it used to pick a stale codex from a non-default node
    # (v24 > v22 lexically) and override the freshly updated default install.
    resolved = shutil.which(command)
    if resolved:
        return resolved

    path = _augmented_path()
    resolved = shutil.which(command, path=path)
    if resolved:
        return resolved

    for candidate in _iter_command_candidates(command):
        if candidate.exists():
            return str(candidate)
    return command


def _build_local_ai_env(resolved_command: str) -> dict[str, str]:
    env = os.environ.copy()
    command_parent = Path(resolved_command).parent
    path_parts = [
        str(command_parent) if str(command_parent) != "." else "",
        *_candidate_path_dirs(),
    ]
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([part for part in path_parts if part]) + os.pathsep + existing_path
    return env


def _looks_like_path(command: str) -> bool:
    separators = [os.path.sep]
    if os.path.altsep:
        separators.append(os.path.altsep)
    return any(separator in command for separator in separators)


def _augmented_path() -> str:
    return os.pathsep.join([*_candidate_path_dirs(), os.environ.get("PATH", "")])


def _candidate_path_dirs() -> list[str]:
    home = Path.home()
    dirs: list[Path] = [
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        home / ".volta" / "bin",
        home / ".asdf" / "shims",
        home / ".local" / "share" / "mise" / "shims",
        home / ".bun" / "bin",
    ]

    appdata = os.getenv("APPDATA")
    localappdata = os.getenv("LOCALAPPDATA")
    program_files = os.getenv("ProgramFiles")
    program_files_x86 = os.getenv("ProgramFiles(x86)")
    nvm_symlink = os.getenv("NVM_SYMLINK")
    nvm_home = os.getenv("NVM_HOME")

    for raw_path in (
        appdata and Path(appdata) / "npm",
        localappdata and Path(localappdata) / "Volta" / "bin",
        nvm_symlink and Path(nvm_symlink),
        nvm_home and Path(nvm_home),
        program_files and Path(program_files) / "nodejs",
        program_files_x86 and Path(program_files_x86) / "nodejs",
    ):
        if raw_path:
            dirs.append(raw_path)

    dirs.extend(_nvm_bin_dirs())
    dirs.extend(_npm_global_dirs(dirs))
    return [str(path) for path in dirs if path.exists()]


def _nvm_bin_dirs() -> list[Path]:
    home = Path.home()
    nvm_bin_root = home / ".nvm" / "versions" / "node"
    if not nvm_bin_root.exists():
        return []
    return sorted(nvm_bin_root.glob("*/bin"), reverse=True)


def _npm_global_dirs(existing_dirs: list[Path]) -> list[Path]:
    path = os.pathsep.join([str(path) for path in existing_dirs if path.exists()] + [os.environ.get("PATH", "")])
    npm = shutil.which("npm", path=path)
    if not npm:
        return []
    try:
        completed = subprocess.run(
            [npm, "prefix", "-g"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    prefix = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    if not prefix:
        return []
    prefix_path = Path(prefix)
    return [prefix_path / "bin", prefix_path]


def _iter_command_candidates(command: str) -> list[Path]:
    candidates: list[Path] = []
    for directory in _candidate_path_dirs():
        for name in _command_names(command):
            candidates.append(Path(directory) / name)
    nvm_candidates = [path for path in candidates if ".nvm" in path.parts]
    if nvm_candidates:
        return sorted(nvm_candidates, key=_command_version_key, reverse=True) + [
            path for path in candidates if path not in nvm_candidates
        ]
    return candidates


def _command_names(command: str) -> list[str]:
    names = [command]
    if os.name == "nt" and not Path(command).suffix:
        names.extend([f"{command}.exe", f"{command}.cmd", f"{command}.bat"])
    return names


def _command_version_key(command_path: Path) -> tuple[int, int, int]:
    env = os.environ.copy()
    env["PATH"] = str(command_path.parent) + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            [str(command_path), "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=10,
        )
    except Exception:
        return (0, 0, 0)

    text = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _is_codex_command(command: str) -> bool:
    return Path(command).name in {"codex", "codex.exe"}


def _is_codex_exec_command(command: list[str]) -> bool:
    return len(command) > 1 and _is_codex_command(command[0]) and command[1] in {"exec", "e"}


def _record_has_year(record: DocumentRecord) -> bool:
    if not record.date:
        return False
    return any(char.isdigit() for char in record.date)
