"""Record of applied fixes, kept so they can be undone later."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

APPLIED = "applied"
FAILED = "failed"
UNDONE = "undone"


@dataclass
class Record:
    run_id: str
    entry_id: str
    title: str
    time: float
    status: str
    edited: bool
    reboot: bool
    says: list[str]
    undo_script: str


class History:
    def __init__(self, path: Path | None = None):
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.path = path or state / "troubleshooter" / "history.json"

    def load(self) -> list[Record]:
        try:
            raw = json.loads(self.path.read_text())
            return [Record(**item) for item in raw]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, records: list[Record]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps([asdict(r) for r in records], indent=1))
        temp.replace(self.path)

    def add(self, plan, status: str, edited: bool) -> Record:
        record = Record(plan.run_id, plan.entry_id, plan.title, time.time(), status, edited,
                        plan.reboot, plan.summary, plan.undo_script)
        self._save([record, *self.load()])
        return record

    def mark(self, run_id: str, status: str) -> None:
        records = self.load()
        for record in records:
            if record.run_id == run_id:
                record.status = status
        self._save(records)

    def pending_restart(self, boot_time: float) -> list[Record]:
        """Fixes applied since this boot that don't take effect until the next one."""
        return [r for r in self.load() if r.status == APPLIED and r.reboot and r.time > boot_time]


class Dismissed:
    """Recommendations the user doesn't want to see on launch. Nothing on the system changes."""

    def __init__(self, path: Path | None = None):
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.path = path or state / "troubleshooter" / "dismissed.json"

    def ids(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text())
            return {item for item in raw if isinstance(item, str)}
        except (OSError, ValueError, TypeError):
            return set()

    def _save(self, ids: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(sorted(ids), indent=1))
        temp.replace(self.path)

    def add(self, entry_id: str) -> None:
        self._save(self.ids() | {entry_id})

    def remove(self, entry_id: str) -> None:
        self._save(self.ids() - {entry_id})
