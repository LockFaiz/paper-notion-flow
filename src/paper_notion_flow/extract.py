from __future__ import annotations

import shutil
from pathlib import Path

import requests


def sanitize_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in name)
    return safe.strip("._") or "document"


def download_pdf(pdf_url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(Path(pdf_url.split("?")[0]).name or "document.pdf")
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    output_path = output_dir / filename

    response = requests.get(pdf_url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def prepare_pdf_for_cli(pdf_path: Path, output_dir: Path, content_id: str) -> Path:
    """Copy a source PDF into the workspace so local CLIs can read it safely."""
    source_path = pdf_path.expanduser().resolve()
    if not source_path.exists():
        raise RuntimeError(f"PDF not found: {source_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(content_id)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    output_path = (output_dir / filename).resolve()

    if source_path != output_path:
        shutil.copy2(source_path, output_path)
    return output_path
