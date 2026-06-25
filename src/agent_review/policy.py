from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from .models import PolicyDecision


HIGH_RISK_KEYWORDS = (
    "auth",
    "permission",
    "payment",
    "billing",
    "database",
    "migration",
    "schema",
    "config",
    ".env",
    "secret",
    "token",
    "credential",
)


def find_high_risk_files(changed_files: Iterable[str]) -> List[str]:
    risk: List[str] = []
    for item in changed_files:
        lowered = item.lower()
        if any(keyword in lowered for keyword in HIGH_RISK_KEYWORDS):
            risk.append(item)
    return risk


def should_trigger_review(
    changed_files: List[str],
    diff_text: str,
    commands_log_text: str,
    tests_log_text: str,
    task_text: str,
    final_response_text: str,
) -> PolicyDecision:
    high_risk = find_high_risk_files(changed_files)

    if high_risk:
        return PolicyDecision(True, "high-risk file changes detected", high_risk)

    project_mutation_words = ("npm ", "pnpm ", "yarn ", "pip ", "pytest", "build", "deploy", "migrate", "install")
    combined_logs = f"{commands_log_text}\n{tests_log_text}".lower()
    if changed_files:
        return PolicyDecision(True, "files changed")
    if diff_text.strip():
        return PolicyDecision(True, "git diff detected")
    if any(word in combined_logs for word in project_mutation_words):
        return PolicyDecision(True, "project-changing commands detected")
    claim_text = f"{task_text}\n{final_response_text}".lower()
    if any(phrase in claim_text for phrase in ("done", "fixed", "完成", "已修复")) and changed_files:
        return PolicyDecision(True, "deliverable completion claim detected")

    return PolicyDecision(False, "no file changes or project-changing actions detected")


def has_failing_test_signal(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\btraceback\b|\bexit (?:code|status) [1-9]\d*\b", lowered):
        return True
    if re.search(r"(?m)^\s*(?:failed|error)\b", lowered):
        return True
    if re.search(r"\b[1-9]\d*\s+(?:failed|failures?|errors?)\b", lowered):
        return True
    if re.search(r"\b(?:failed|failures?|errors?)\s*[:=]\s*[1-9]\d*\b", lowered):
        return True
    return False


def detect_secret_leak(diff_text: str, changed_files: List[str]) -> List[str]:
    findings: List[str] = []
    lowered = diff_text.lower()
    secret_patterns = (
        r"\bapi[_-]?key\s*=",
        r"\btoken\s*=",
        r"\bsecret\s*=",
        r"\bpassword\s*=",
        r"\bbearer\s+[a-z0-9._~+/=-]+",
    )
    if any(re.search(pattern, lowered) for pattern in secret_patterns):
        findings.append("diff contains secret-like tokens")
    if any(Path(item).name.startswith(".env") for item in changed_files):
        findings.append("environment file changed")
    return findings
