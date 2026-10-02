"""JSONL evidence store.

Every search and every item fetch is appended to a JSONL file so a run can be
audited or replayed without touching eBay again. Raw HTML lives in ``cache/``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Iterable


class Store:
    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.runs_dir = os.path.join(state_dir, "runs")
        os.makedirs(self.runs_dir, exist_ok=True)
        self.cache_dir = os.path.join(state_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def run_path(self, name: str | None = None) -> str:
        name = name or time.strftime("scan-%Y-%m-%dT%H-%M-%S")
        return os.path.join(self.runs_dir, f"{name}.jsonl")

    def append(self, record: dict, path: str) -> None:
        record = dict(record)
        record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with open(path, "a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def read(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows

    def latest_run(self) -> str | None:
        runs = [f for f in os.listdir(self.runs_dir) if f.endswith(".jsonl")]
        if not runs:
            return None
        return os.path.join(self.runs_dir, sorted(runs)[-1])

    def cached_items(self, max_age: int = 86400) -> dict[str, dict]:
        """Return the newest validation record per item id within ``max_age``."""
        best: dict[str, dict] = {}
        cutoff = time.time() - max_age
        for name in sorted(os.listdir(self.runs_dir), reverse=True):
            if not name.endswith(".jsonl"):
                continue
            for row in self.read(os.path.join(self.runs_dir, name)):
                if row.get("kind") != "item":
                    continue
                item_id = str(row.get("item_id"))
                if item_id in best:
                    continue
                try:
                    if time.mktime(time.strptime(row.get("ts", "")[:19], "%Y-%m-%dT%H:%M:%S")) < cutoff:
                        continue
                except Exception:
                    pass
                best[item_id] = row
        return best

    def save_report(self, name: str, text: str) -> str:
        path = os.path.join(self.runs_dir, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path
