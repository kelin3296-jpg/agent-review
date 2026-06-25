from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List


AGENT_ABBR = {
    "codex": "cx",
    "claude-code": "cc",
    "hermes": "hm",
    "local": "lo",
}


def agent_abbr(agent: str) -> str:
    if agent in AGENT_ABBR:
        return AGENT_ABBR[agent]
    cleaned = "".join(char for char in agent.lower() if char.isalnum())
    return (cleaned[:4] or "agent")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_case_id(cases_dir: Path | None = None) -> str:
    day = datetime.now().strftime("%y%m%d")
    max_number = 0
    if cases_dir and cases_dir.exists():
        pattern = re.compile(rf"^{day}-(\d{{3}})$")
        for item in cases_dir.iterdir():
            match = pattern.match(item.stem)
            if match:
                max_number = max(max_number, int(match.group(1)))
    return f"{day}-{max_number + 1:03d}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path | None) -> str:
    if not path:
        return ""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel_paths(paths: Iterable[str], project_path: Path) -> List[str]:
    output: List[str] = []
    for item in paths:
        p = Path(item)
        if p.is_absolute():
            try:
                output.append(str(p.relative_to(project_path)))
            except ValueError:
                output.append(str(p))
        else:
            output.append(str(p))
    return sorted(dict.fromkeys(output))


def command_exists(command_name: str) -> bool:
    for search_path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(search_path) / command_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return True
    return False
