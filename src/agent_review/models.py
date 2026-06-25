from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


VALID_AGENTS = ("claude-code", "codex", "hermes")
VALID_STATUSES = ("PASS", "FIX", "SKIP")
VALID_REVIEWER_STATUSES = ("PASS", "FIX")
VALID_SEVERITIES = ("none", "normal", "critical")


@dataclass
class Config:
    knowledge_base_root: Optional[Path] = None
    reviewer_commands: Dict[str, str] = field(default_factory=dict)
    reviewer_adapters: Dict[str, bool] = field(default_factory=dict)
    reviewer_timeout_seconds: int = 180
    privacy_masks: List[str] = field(default_factory=lambda: [".env", ".pem", ".key", "token", "secret"])
    sync_kb: bool = False
    retention_days: int = 7


@dataclass
class ReviewInputs:
    host_agent: str
    reviewer: Optional[str]
    project_path: Path
    task_text: str
    final_response_text: str
    commands_log_text: str
    tests_log_text: str
    changed_files: List[str]
    diff_text: str
    sync_kb: bool = False
    review_type: str = "delivery"
    plan_text: str = ""
    context_text: str = ""


@dataclass
class PolicyDecision:
    should_review: bool
    reason: str
    high_risk_files: List[str] = field(default_factory=list)


@dataclass
class ReviewerSelection:
    requested: Optional[str]
    selected: str
    fallback_reason: Optional[str] = None
    used_builtin: bool = False
    kind: str = "builtin"


@dataclass
class ReviewResult:
    status: str
    summary: str
    findings: List[str]
    evidence: List[str]
    reviewer_agent: str
    reviewer_kind: str
    fallback_reason: Optional[str] = None
    raw_output_path: Optional[str] = None
    severity: str = "none"
    acceptance_checklist: List[Dict[str, str]] = field(default_factory=list)
    issues: List[Dict[str, str]] = field(default_factory=list)
    fix_instructions: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    can_deliver: bool = True
    reviewer_confidence: str = "medium"
