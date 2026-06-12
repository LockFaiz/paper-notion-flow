from __future__ import annotations

import json
from pathlib import Path


class SyncState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"processed": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"processed": {}}

    def was_processed(self, stable_id: str, date_modified: str | None) -> bool:
        if not date_modified:
            return stable_id in self._data["processed"]
        return self._data["processed"].get(stable_id) == date_modified

    def mark_processed(self, stable_id: str, date_modified: str | None) -> None:
        self._data["processed"][stable_id] = date_modified or "processed"
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def remove_processed(self, stable_id: str) -> None:
        self._data["processed"].pop(stable_id, None)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def processed_ids(self) -> list[str]:
        return list(self._data["processed"].keys())
