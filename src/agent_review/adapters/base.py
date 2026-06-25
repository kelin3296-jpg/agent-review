from __future__ import annotations

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import Config, ReviewInputs, ReviewResult, VALID_REVIEWER_STATUSES, VALID_SEVERITIES
from ..utils import command_exists


REVIEW_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["PASS", "FIX"]},
        "summary": {"type": "string"},
        "severity": {"type": "string", "enum": ["none", "normal", "critical"]},
        "acceptance_checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "criterion": {"type": "string"},
                    "source": {"type": "string"},
                    "result": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "criterion", "source", "result", "evidence"],
            },
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "missing_requirement",
                            "evidence_missing",
                            "format_error",
                            "safety_risk",
                            "test_failure",
                            "scope_risk",
                            "reviewer_failed",
                            "other",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["normal", "critical"]},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "fix_instruction": {"type": "string"},
                },
                "required": ["type", "severity", "description", "evidence", "fix_instruction"],
            },
        },
        "fix_instructions": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "can_deliver": {"type": "boolean"},
        "reviewer_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "findings": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status",
        "summary",
        "severity",
        "acceptance_checklist",
        "issues",
        "fix_instructions",
        "suggestions",
        "can_deliver",
        "reviewer_confidence",
        "findings",
        "evidence",
    ],
}


def build_reviewer_prompt(case_path: Path) -> str:
    return f"""You are the review agent in a local multi-agent handoff.

Read this lightweight brief first:
{case_path / "review-brief.md"}

Treat that Markdown file as the only review handoff material. It is size-capped on purpose.
If the brief says the diff was truncated, you may inspect the current project files listed in the brief.
Do not read case-directory JSON, logs, stdout/stderr, indexes, or prior review-result files. They are tool artifacts and usually noise.

Rules:
- Do not edit files.
- The brief may be a delivery review or a plan review. Follow the review type stated in the brief.
- First extract 3-7 criteria from the user's original request, system constraints, host-agent promises, or explicit project rules.
- Every criterion must name its source. Do not invent new requirements.
- For delivery review, check whether the host agent's claimed delivery satisfies those criteria and is safe to hand back to the user.
- For plan review, check whether the proposed plan is aligned, low-risk, not overcomplicated, and ready for the host agent to execute.
- Use PASS when the deliverable can be handed to the user, or when the plan can proceed.
- Use FIX only for clear unmet requirements, required evidence missing, test/build failures, safety risk, format errors, scope risk, or a plan that should be changed before execution.
- Minor optional improvements and better alternatives belong in suggestions, not FIX.
- Use severity=critical for failing tests/builds, secret leakage, dangerous deletion, or unsafe auth/payment/database/config changes.
- Use severity=normal for ordinary missing requirements or missing required verification.
- When status=FIX, provide issues and actionable fix_instructions with evidence.
- The can_deliver boolean means "can hand off" for delivery review and "can proceed" for plan review.
- Return JSON only. No Markdown, no code fence, no surrounding text.

JSON schema:
{json.dumps(REVIEW_RESULT_SCHEMA, ensure_ascii=False)}
"""


def _coerce_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _coerce_string_dict_list(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append({str(key): str(val) for key, val in item.items() if val is not None})
    return rows


def _normalize_status(raw_status: str, raw_severity: Any, raw_can_deliver: Any) -> tuple[str, str, bool]:
    status = raw_status.upper()
    severity = str(raw_severity or "").lower()

    if status == "WARN":
        status = "FIX"
        severity = severity if severity in VALID_SEVERITIES and severity != "none" else "normal"
    elif status == "BLOCK":
        status = "FIX"
        severity = "critical"

    if status not in VALID_REVIEWER_STATUSES:
        raise RuntimeError(f"reviewer returned invalid status '{raw_status}'")

    can_deliver = bool(raw_can_deliver) if isinstance(raw_can_deliver, bool) else status == "PASS"
    if status == "PASS" and not can_deliver:
        status = "FIX"
    if status == "FIX" and severity not in ("normal", "critical"):
        severity = "normal"
    if status == "PASS" and severity not in VALID_SEVERITIES:
        severity = "none"
    if status == "PASS" and severity == "critical":
        status = "FIX"
        can_deliver = False
    return status, severity, can_deliver


def validate_review_payload(payload: Dict[str, Any]) -> ReviewResult:
    raw_status = str(payload.get("status") or payload.get("review_status") or "")
    status, severity, can_deliver = _normalize_status(raw_status, payload.get("severity"), payload.get("can_deliver"))

    summary = payload.get("summary")
    if not isinstance(summary, str):
        raise RuntimeError("reviewer result missing string summary")

    acceptance_checklist = _coerce_string_dict_list(payload.get("acceptance_checklist"))
    issues = _coerce_string_dict_list(payload.get("issues"))
    findings = _coerce_string_list(payload.get("findings"))
    evidence = _coerce_string_list(payload.get("evidence"))
    fix_instructions = _coerce_string_list(payload.get("fix_instructions"))
    suggestions = _coerce_string_list(payload.get("suggestions"))

    if not findings and issues:
        findings = [item.get("description", "") for item in issues if item.get("description")]
    if not evidence:
        evidence = [item.get("evidence", "") for item in acceptance_checklist + issues if item.get("evidence")]

    confidence = str(payload.get("reviewer_confidence") or "medium").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    return ReviewResult(
        status=status,
        summary=summary,
        findings=findings,
        evidence=evidence,
        reviewer_agent="",
        reviewer_kind="adapter",
        severity=severity,
        acceptance_checklist=acceptance_checklist,
        issues=issues,
        fix_instructions=fix_instructions,
        suggestions=suggestions,
        can_deliver=can_deliver,
        reviewer_confidence=confidence,
    )


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _candidate_texts(raw_text: str) -> List[str]:
    texts = [raw_text, _strip_code_fence(raw_text)]
    try:
        wrapper = json.loads(raw_text)
    except JSONDecodeError:
        wrapper = None

    if isinstance(wrapper, dict):
        for key in ("result", "content", "message", "text", "response"):
            value = wrapper.get(key)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        texts.append(item)
                    elif isinstance(item, dict):
                        item_text = item.get("text") or item.get("content")
                        if isinstance(item_text, str):
                            texts.append(item_text)
    return texts


def parse_review_output(raw_text: str) -> ReviewResult:
    decoder = json.JSONDecoder()
    last_payload: Optional[Dict[str, Any]] = None
    last_error: Optional[Exception] = None

    for text in _candidate_texts(raw_text):
        stripped = _strip_code_fence(text)
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict) and ("status" in payload or "review_status" in payload):
                return validate_review_payload(payload)
            if isinstance(payload, dict):
                last_payload = payload
        except Exception as exc:  # keep scanning for JSON embedded in noisy CLI output
            last_error = exc

        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(stripped[index:])
            except JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(payload, dict) and ("status" in payload or "review_status" in payload):
                last_payload = payload

    if last_payload is not None and ("status" in last_payload or "review_status" in last_payload):
        return validate_review_payload(last_payload)
    if last_error:
        raise RuntimeError(f"reviewer did not return parseable JSON: {last_error}") from last_error
    raise RuntimeError("reviewer returned JSON, but no review status payload was found")


def _short_process_detail(text: str, limit: int = 2000) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value

    lower = value.lower()
    markers = [
        "failed to load skill",
        "missing yaml frontmatter",
        "invalid_json_schema",
        "error:",
        "traceback",
    ]
    marker_index = max((lower.rfind(marker) for marker in markers), default=-1)
    if marker_index >= 0:
        context_chars = min(80, max(0, limit // 3))
        start = max(0, marker_index - context_chars)
        prefix = "..." if start > 0 else ""
        available = limit - len(prefix)
        return prefix + value[start : start + available]

    head_len = max(0, limit // 3)
    tail_len = max(0, limit - head_len - 5)
    return value[:head_len] + " ... " + value[-tail_len:]


class AgentReviewerAdapter(ABC):
    agent_name: str
    command_name: str

    def is_available(self) -> bool:
        return command_exists(self.command_name)

    @abstractmethod
    def build_command(self, inputs: ReviewInputs, case_path: Path, schema_path: Path, output_path: Path) -> List[str]:
        raise NotImplementedError

    def run(self, inputs: ReviewInputs, config: Config, case_path: Path) -> ReviewResult:
        with tempfile.TemporaryDirectory(prefix="agent-review-") as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "review-result.schema.json"
            output_path = tmp_path / f"{self.agent_name}-reviewer-output.json"
            schema_path.write_text(json.dumps(REVIEW_RESULT_SCHEMA, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            command = self.build_command(inputs, case_path, schema_path, output_path)
            env = dict(os.environ)
            env["AGENT_REVIEW_ACTIVE"] = "1"
            try:
                result = subprocess.run(
                    command,
                    cwd=inputs.project_path,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=config.reviewer_timeout_seconds,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{self.agent_name} reviewer timed out after {exc.timeout} seconds") from exc
            if result.returncode != 0:
                detail = _short_process_detail(result.stderr or result.stdout) or f"reviewer command failed with code {result.returncode}"
                raise RuntimeError(detail)

            raw_output = output_path.read_text(encoding="utf-8") if output_path.exists() else (result.stdout or "")
        review_result = parse_review_output(raw_output)
        review_result.reviewer_agent = self.agent_name
        review_result.reviewer_kind = f"adapter:{self.agent_name}"
        return review_result


class CodexReviewerAdapter(AgentReviewerAdapter):
    agent_name = "codex"
    command_name = "codex"

    def build_command(self, inputs: ReviewInputs, case_path: Path, schema_path: Path, output_path: Path) -> List[str]:
        return [
            "codex",
            "exec",
            "-C",
            str(inputs.project_path),
            "-s",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            build_reviewer_prompt(case_path),
        ]


class ClaudeReviewerAdapter(AgentReviewerAdapter):
    agent_name = "claude-code"
    command_name = "claude"

    def build_command(self, inputs: ReviewInputs, case_path: Path, schema_path: Path, output_path: Path) -> List[str]:
        return [
            "claude",
            "-p",
            "--add-dir",
            str(inputs.project_path),
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(REVIEW_RESULT_SCHEMA, ensure_ascii=False),
            "--allowedTools",
            "Read,Grep,Glob",
            "--no-session-persistence",
            build_reviewer_prompt(case_path),
        ]


class HermesReviewerAdapter(AgentReviewerAdapter):
    agent_name = "hermes"
    command_name = "hermes"

    def build_command(self, inputs: ReviewInputs, case_path: Path, schema_path: Path, output_path: Path) -> List[str]:
        return [
            "hermes",
            "chat",
            "-q",
            build_reviewer_prompt(case_path),
            "-Q",
            "--source",
            "agent-review",
            "--max-turns",
            "20",
        ]


ADAPTERS: Dict[str, AgentReviewerAdapter] = {
    "codex": CodexReviewerAdapter(),
    "claude-code": ClaudeReviewerAdapter(),
    "hermes": HermesReviewerAdapter(),
}


def get_adapter(agent_name: str) -> Optional[AgentReviewerAdapter]:
    return ADAPTERS.get(agent_name)
