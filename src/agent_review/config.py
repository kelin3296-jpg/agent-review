from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .models import Config


DEFAULT_GLOBAL_CONFIG_PATH = Path.home() / ".agent-review" / "config.json"


def _coerce_path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value)).expanduser()


def _config_from_payload(payload: Dict[str, Any], base: Config | None = None) -> Config:
    current = base or Config()
    reviewer_commands = payload.get("reviewer_commands") or {}
    reviewer_adapters = payload.get("reviewer_adapters") or {}
    privacy_masks = payload.get("privacy_masks") or current.privacy_masks
    return Config(
        knowledge_base_root=_coerce_path(payload.get("knowledge_base_root")) or current.knowledge_base_root,
        reviewer_commands={**current.reviewer_commands, **{str(k): str(v) for k, v in reviewer_commands.items()}},
        reviewer_adapters={**current.reviewer_adapters, **{str(k): bool(v) for k, v in reviewer_adapters.items()}},
        reviewer_timeout_seconds=int(payload.get("reviewer_timeout_seconds", current.reviewer_timeout_seconds)),
        privacy_masks=[str(item) for item in privacy_masks],
        sync_kb=bool(payload.get("sync_kb", current.sync_kb)),
        retention_days=max(1, int(payload.get("retention_days", current.retention_days))),
    )


def _read_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _global_config_path() -> Path:
    override = os.environ.get("AGENT_REVIEW_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_GLOBAL_CONFIG_PATH


def load_config(project_path: Path) -> Config:
    config = Config()
    if os.environ.get("AGENT_REVIEW_IGNORE_GLOBAL_CONFIG") != "1":
        global_path = _global_config_path()
        if global_path.exists():
            config = _config_from_payload(_read_config(global_path), config)

    project_config_path = project_path / "agent-review.json"
    if project_config_path.exists():
        config = _config_from_payload(_read_config(project_config_path), config)

    return config
