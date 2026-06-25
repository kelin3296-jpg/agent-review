from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple


def _run_git(args: List[str], project_path: Path) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False, ""

    if result.returncode != 0:
        return False, ""
    return True, result.stdout


def is_git_repo(project_path: Path) -> bool:
    ok, output = _run_git(["rev-parse", "--show-toplevel"], project_path)
    return ok and bool(output.strip())


def _git_path(project_path: Path, relative_path: str) -> Path | None:
    ok, output = _run_git(["rev-parse", "--git-path", relative_path], project_path)
    if not ok or not output.strip():
        return None
    path = Path(output.strip())
    if path.is_absolute():
        return path
    return project_path / path


def ensure_agent_review_excluded(project_path: Path) -> None:
    if not is_git_repo(project_path):
        return
    exclude_path = _git_path(project_path, "info/exclude")
    if not exclude_path:
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    if ".agent-review/" in lines or ".agent-review" in lines:
        return
    suffix = "" if existing.endswith("\n") or not existing else "\n"
    exclude_path.write_text(f"{existing}{suffix}.agent-review/\n", encoding="utf-8")


def detect_changed_files(project_path: Path) -> List[str]:
    ok, output = _run_git(["status", "--short"], project_path)
    if not ok:
        return []
    files: List[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        files.append(line[3:])
    return sorted(dict.fromkeys(files))


def detect_diff(project_path: Path) -> str:
    ok, output = _run_git(["diff", "--binary", "--no-ext-diff"], project_path)
    if not ok:
        return ""
    return output
