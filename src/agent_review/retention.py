from __future__ import annotations

import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from .utils import ensure_dir


CASE_ID_RE = re.compile(r"^(\d{6})-\d{3}$")


def _case_date(path: Path) -> date | None:
    match = CASE_ID_RE.match(path.name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%y%m%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def cleanup_old_cases(review_root: Path, retention_days: int = 7, today: date | None = None) -> list[Path]:
    cases_dir = ensure_dir(review_root / "cases")
    current_day = today or datetime.now().date()
    cutoff = current_day - timedelta(days=retention_days)
    removed: list[Path] = []

    for item in cases_dir.iterdir():
        if not item.is_dir():
            continue
        item_date = _case_date(item)
        if item_date and item_date < cutoff:
            shutil.rmtree(item)
            removed.append(item)

    return removed
