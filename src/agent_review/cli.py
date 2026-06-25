from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .collector import write_case_files
from .config import DEFAULT_GLOBAL_CONFIG_PATH, load_config
from .git_tools import detect_changed_files, detect_diff, ensure_agent_review_excluded
from .models import PolicyDecision, ReviewInputs, ReviewResult, ReviewerSelection, VALID_AGENTS
from .policy import should_trigger_review
from .retention import cleanup_old_cases
from .reviewers import run_reviewer, select_reviewer
from .runtime_index import write_case_readme, write_runtime_index
from .utils import ensure_dir, make_case_id, rel_paths, write_json, write_text


def _read_optional_text(arg_value: str | None, stdin_allowed: bool = False) -> str:
    if not arg_value:
        return ""
    if arg_value == "-" and stdin_allowed:
        return sys.stdin.read()
    return Path(arg_value).read_text(encoding="utf-8")


def _read_changed_files(arg_value: str | None, project_path: Path) -> list[str]:
    if not arg_value:
        return detect_changed_files(project_path)
    if arg_value == "-":
        items = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
        return rel_paths(items, project_path)
    path = Path(arg_value)
    items = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rel_paths(items, project_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-review", description="Local review gate for agent-delivered work.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="Run a review case")
    review.add_argument("--host", required=True, choices=VALID_AGENTS)
    review.add_argument("--reviewer", choices=[*VALID_AGENTS, "local"])
    review.add_argument("--project-path", default=".")
    review.add_argument("--task-file")
    review.add_argument("--task-text")
    review.add_argument("--final-response-file")
    review.add_argument("--final-response-text")
    review.add_argument("--commands-log")
    review.add_argument("--tests-log")
    review.add_argument("--changed-files")
    review.add_argument("--diff-file")
    review.add_argument("--diff-text")
    review.add_argument("--sync-kb", action="store_true", help="Deprecated; review records stay local only")
    review.add_argument("--json", action="store_true")

    plan_review = subparsers.add_parser("plan-review", help="Run a before-work plan review case")
    plan_review.add_argument("--host", required=True, choices=VALID_AGENTS)
    plan_review.add_argument("--reviewer", choices=[*VALID_AGENTS, "local"])
    plan_review.add_argument("--project-path", default=".")
    plan_review.add_argument("--task-file")
    plan_review.add_argument("--task-text")
    plan_review.add_argument("--plan-file")
    plan_review.add_argument("--plan-text")
    plan_review.add_argument("--context-file")
    plan_review.add_argument("--context-text")
    plan_review.add_argument("--json", action="store_true")

    init_config = subparsers.add_parser("init-config", help="Write example config")
    init_config.add_argument("--project-path", default=".")
    init_config.add_argument("--global-config", action="store_true", help="Write config to ~/.agent-review/config.json")

    return parser


def _resolve_inputs(args: argparse.Namespace) -> ReviewInputs:
    project_path = Path(args.project_path).expanduser().resolve()
    task_text = args.task_text or _read_optional_text(args.task_file)
    final_response_text = args.final_response_text or _read_optional_text(args.final_response_file)
    commands_log_text = _read_optional_text(args.commands_log)
    tests_log_text = _read_optional_text(args.tests_log)
    changed_files = _read_changed_files(args.changed_files, project_path)
    diff_text = args.diff_text or _read_optional_text(args.diff_file) or detect_diff(project_path)
    return ReviewInputs(
        host_agent=args.host,
        reviewer=args.reviewer,
        project_path=project_path,
        task_text=task_text,
        final_response_text=final_response_text,
        commands_log_text=commands_log_text,
        tests_log_text=tests_log_text,
        changed_files=changed_files,
        diff_text=diff_text,
        sync_kb=args.sync_kb,
    )


def _resolve_plan_inputs(args: argparse.Namespace) -> ReviewInputs:
    project_path = Path(args.project_path).expanduser().resolve()
    task_text = args.task_text or _read_optional_text(args.task_file)
    plan_text = args.plan_text or _read_optional_text(args.plan_file)
    context_text = args.context_text or _read_optional_text(args.context_file)
    return ReviewInputs(
        host_agent=args.host,
        reviewer=args.reviewer,
        project_path=project_path,
        task_text=task_text,
        final_response_text="",
        commands_log_text="",
        tests_log_text="",
        changed_files=[],
        diff_text="",
        review_type="plan",
        plan_text=plan_text,
        context_text=context_text,
    )


def _shorten(value: str | None, limit: int = 280) -> str:
    if not value:
        return ""
    one_line = " ".join(value.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def _reviewer_kind(selection: ReviewerSelection, result: ReviewResult | None = None) -> str:
    if result:
        return result.reviewer_kind
    if selection.used_builtin:
        return "builtin"
    return selection.kind


def _format_checklist(items: object) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["- None"]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") or "AC"
        criterion = item.get("criterion") or ""
        source = item.get("source") or ""
        result = item.get("result") or ""
        evidence = item.get("evidence") or ""
        lines.append(f"- {item_id}: {criterion} [{result}]")
        if source:
            lines.append(f"  - Source: {source}")
        if evidence:
            lines.append(f"  - Evidence: {evidence}")
    return lines or ["- None"]


def _format_issues(items: object) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["- None"]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        issue_type = item.get("type") or "other"
        severity = item.get("severity") or "normal"
        description = item.get("description") or ""
        evidence = item.get("evidence") or ""
        fix_instruction = item.get("fix_instruction") or ""
        lines.append(f"- [{severity}] {issue_type}: {description}")
        if evidence:
            lines.append(f"  - Evidence: {evidence}")
        if fix_instruction:
            lines.append(f"  - Fix: {fix_instruction}")
    return lines or ["- None"]


def _build_result_payload(
    case_id: str,
    status: str,
    summary: str,
    findings: list[str],
    evidence: list[str],
    inputs: ReviewInputs,
    selection: ReviewerSelection,
    reviewer: str,
    reviewer_kind: str,
    fallback_reason: str | None,
    case_path: Path,
    review_log_path: Path,
    latest_review_path: Path,
    knowledge_note_path: Path | None = None,
    knowledge_index_path: Path | None = None,
    runtime_index_path: Path | None = None,
    raw_reviewer_output_path: str | None = None,
    retention_days: int = 7,
    retention_removed_cases: list[Path] | None = None,
    severity: str = "none",
    acceptance_checklist: list[dict[str, str]] | None = None,
    issues: list[dict[str, str]] | None = None,
    fix_instructions: list[str] | None = None,
    suggestions: list[str] | None = None,
    can_deliver: bool = True,
    reviewer_confidence: str = "medium",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "review_type": inputs.review_type,
        "status": status,
        "review_status": status,
        "summary": summary,
        "severity": severity,
        "can_deliver": can_deliver,
        "can_proceed": can_deliver,
        "findings": findings,
        "evidence": evidence,
        "acceptance_checklist": acceptance_checklist or [],
        "issues": issues or [],
        "fix_instructions": fix_instructions or [],
        "suggestions": suggestions or [],
        "reviewer_confidence": reviewer_confidence,
        "host_decision": "NONE",
        "decision_reason": "",
        "auto_fix_count": 0,
        "max_auto_fix_count": 1,
        "host_agent": inputs.host_agent,
        "requested_reviewer": selection.requested or "",
        "selected_reviewer": selection.selected,
        "reviewer": reviewer,
        "reviewer_kind": reviewer_kind,
        "fallback_reason": fallback_reason,
        "case_path": str(case_path),
        "review_log_path": str(review_log_path),
        "latest_review_path": str(latest_review_path),
        "raw_reviewer_output_path": raw_reviewer_output_path or "",
        "knowledge_note_name": knowledge_note_path.name if knowledge_note_path else "",
        "knowledge_note_path": str(knowledge_note_path) if knowledge_note_path else "",
        "knowledge_index_path": str(knowledge_index_path) if knowledge_index_path else "",
        "runtime_index_path": str(runtime_index_path) if runtime_index_path else "",
        "retention_days": retention_days,
        "retention_removed_cases": [str(path) for path in (retention_removed_cases or [])],
    }


def _format_result_markdown(payload: dict[str, object]) -> str:
    findings = [f"- {item}" for item in payload["findings"]] or ["- None"]  # type: ignore[index]
    evidence = [f"- {item}" for item in payload["evidence"]] or ["- None"]  # type: ignore[index]
    checklist = _format_checklist(payload.get("acceptance_checklist"))
    issues = _format_issues(payload.get("issues"))
    fix_instructions = [f"- {item}" for item in payload.get("fix_instructions", [])] or ["- None"]  # type: ignore[union-attr]
    suggestions = [f"- {item}" for item in payload.get("suggestions", [])] or ["- None"]  # type: ignore[union-attr]
    review_type = str(payload.get("review_type") or "delivery")
    readiness_label = "Can Proceed" if review_type == "plan" else "Can Deliver"
    lines = [
        "# Review Result",
        "",
        f"- Review Type: {review_type}",
        f"- Status: {payload['status']}",
        f"- {readiness_label}: {payload['can_deliver']}",
        f"- Severity: {payload['severity']}",
        f"- Reviewer Confidence: {payload['reviewer_confidence']}",
        f"- Host Decision: {payload['host_decision']}",
        f"- Auto Fix Count: {payload['auto_fix_count']} / {payload['max_auto_fix_count']}",
        f"- Host Agent: {payload['host_agent']}",
        f"- Requested Reviewer: {payload['requested_reviewer'] or 'auto'}",
        f"- Selected Reviewer: {payload['selected_reviewer']}",
        f"- Actual Reviewer: {payload['reviewer']}",
        f"- Reviewer Kind: {payload['reviewer_kind']}",
        f"- Fallback Reason: {payload['fallback_reason'] or 'None'}",
        f"- Case Path: {payload['case_path']}",
        f"- Review Log: {payload['review_log_path']}",
        f"- Latest Review: {payload['latest_review_path']}",
        f"- Raw Reviewer Output: {payload['raw_reviewer_output_path'] or 'Not written'}",
        f"- Knowledge Note Name: {payload['knowledge_note_name'] or 'Local only'}",
        f"- Knowledge Note: {payload['knowledge_note_path'] or 'Local only'}",
        f"- Knowledge Index: {payload['knowledge_index_path'] or 'Local only'}",
        f"- Runtime Index: {payload['runtime_index_path'] or 'Not written'}",
        "",
        "## Summary",
        "",
        str(payload["summary"]),
        "",
        "## Acceptance Checklist",
        "",
        *checklist,
        "",
        "## Issues",
        "",
        *issues,
        "",
        "## Fix Instructions",
        "",
        *fix_instructions,
        "",
        "## Suggestions",
        "",
        *suggestions,
        "",
        "## Findings",
        "",
        *findings,
        "",
        "## Evidence",
        "",
        *evidence,
    ]
    return "\n".join(lines)


def _write_review_outputs(
    payload: dict[str, object],
    case_path: Path,
    review_log_path: Path,
    latest_review_path: Path,
) -> None:
    markdown = _format_result_markdown(payload)
    write_json(case_path / "review-result.json", payload)
    write_text(review_log_path, markdown)
    write_text(latest_review_path, markdown)


def _print_payload(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    review_type = str(payload.get("review_type") or "delivery")
    readiness_label = "Can proceed" if review_type == "plan" else "Can deliver"
    lines = [
        f"{payload['status']} {payload['case_id']} {payload['summary']}",
        f"Review type: {review_type}",
        f"{readiness_label}: {payload['can_deliver']} | Severity: {payload['severity']}",
        f"Reviewer: {payload['reviewer']} ({payload['reviewer_kind']})",
        f"Selected reviewer: {payload['selected_reviewer']}",
        f"Review log: {payload['review_log_path']}",
        f"Case path: {payload['case_path']}",
    ]
    if payload["raw_reviewer_output_path"]:
        lines.append(f"Raw reviewer output: {payload['raw_reviewer_output_path']}")
    lines.append("Knowledge note: Local only")
    if payload["runtime_index_path"]:
        lines.append(f"Runtime index: {payload['runtime_index_path']}")
    fallback = _shorten(str(payload["fallback_reason"] or ""))
    if fallback:
        lines.append(f"Fallback: {fallback}")
    print("\n".join(lines))


def _print_loop_guard(as_json: bool) -> int:
    payload = {
        "status": "SKIP",
        "summary": "No review triggered: AGENT_REVIEW_ACTIVE loop guard is set",
        "findings": [],
        "evidence": [],
        "reviewer": "local",
        "reviewer_kind": "loop-guard",
        "case_path": "",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if as_json else "SKIP loop guard active")
    return 0


def _run_review_case(inputs: ReviewInputs, decision: PolicyDecision, as_json: bool) -> int:
    config = load_config(inputs.project_path)

    try:
        selection = select_reviewer(inputs.host_agent, inputs.reviewer, config)
    except ValueError as exc:
        payload = {
            "status": "ERROR",
            "summary": str(exc),
            "findings": [str(exc)],
            "evidence": [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if as_json else f"ERROR {exc}")
        return 1
    review_root = ensure_dir(inputs.project_path / ".agent-review")
    removed_cases = cleanup_old_cases(review_root, config.retention_days)
    case_id = make_case_id(review_root / "cases")
    ensure_agent_review_excluded(inputs.project_path)
    case_path = ensure_dir(review_root / "cases" / case_id)
    latest_review_path = review_root / "latest-review.md"
    review_log_path = case_path / "review-result.md"
    manifest = write_case_files(case_path, case_id, inputs, decision, selection, None)

    if not decision.should_review:
        result_payload = _build_result_payload(
            case_id=case_id,
            status="SKIP",
            summary="No review triggered: " + decision.reason,
            findings=[],
            evidence=[],
            inputs=inputs,
            selection=selection,
            reviewer=selection.selected,
            reviewer_kind=_reviewer_kind(selection),
            fallback_reason=selection.fallback_reason,
            case_path=case_path,
            review_log_path=review_log_path,
            latest_review_path=latest_review_path,
            retention_days=config.retention_days,
            retention_removed_cases=removed_cases,
        )
        manifest.update(
            {
                "actual_review_agent": selection.selected,
                "reviewer_kind": result_payload["reviewer_kind"],
                "review_log_path": str(review_log_path),
                "latest_review_path": str(latest_review_path),
            }
        )
        write_json(case_path / "manifest.json", manifest)
        _write_review_outputs(result_payload, case_path, review_log_path, latest_review_path)
        write_case_readme(case_path, result_payload)
        runtime_index_path = write_runtime_index(inputs.project_path)
        result_payload["runtime_index_path"] = str(runtime_index_path)
        manifest["runtime_index_path"] = str(runtime_index_path)
        write_json(case_path / "manifest.json", manifest)
        _write_review_outputs(result_payload, case_path, review_log_path, latest_review_path)
        write_case_readme(case_path, result_payload)
        _print_payload(result_payload, as_json)
        return 0

    result = run_reviewer(inputs, selection, config, case_path)
    kb_path = None
    kb_index_path = None

    payload = _build_result_payload(
        case_id=case_id,
        status=result.status,
        summary=result.summary,
        findings=result.findings,
        evidence=result.evidence,
        inputs=inputs,
        selection=selection,
        reviewer=result.reviewer_agent,
        reviewer_kind=result.reviewer_kind,
        fallback_reason=result.fallback_reason,
        case_path=case_path,
        review_log_path=review_log_path,
        latest_review_path=latest_review_path,
        knowledge_note_path=kb_path,
        knowledge_index_path=kb_index_path,
        raw_reviewer_output_path=result.raw_output_path,
        retention_days=config.retention_days,
        retention_removed_cases=removed_cases,
        severity=result.severity,
        acceptance_checklist=result.acceptance_checklist,
        issues=result.issues,
        fix_instructions=result.fix_instructions,
        suggestions=result.suggestions,
        can_deliver=result.can_deliver,
        reviewer_confidence=result.reviewer_confidence,
    )
    manifest.update(
        {
            "actual_review_agent": result.reviewer_agent,
            "reviewer_kind": result.reviewer_kind,
            "status": result.status,
            "severity": result.severity,
            "can_deliver": result.can_deliver,
            "review_log_path": str(review_log_path),
            "latest_review_path": str(latest_review_path),
            "raw_reviewer_output_path": result.raw_output_path or "",
            "knowledge_note_name": kb_path.name if kb_path else "",
            "knowledge_note_path": str(kb_path) if kb_path else "",
            "knowledge_index_path": str(kb_index_path) if kb_index_path else "",
            "retention_days": config.retention_days,
            "retention_removed_cases": [str(path) for path in removed_cases],
        }
    )
    write_json(case_path / "manifest.json", manifest)
    _write_review_outputs(payload, case_path, review_log_path, latest_review_path)
    write_case_readme(case_path, payload)
    runtime_index_path = write_runtime_index(inputs.project_path)
    payload["runtime_index_path"] = str(runtime_index_path)
    manifest["runtime_index_path"] = str(runtime_index_path)
    write_json(case_path / "manifest.json", manifest)
    _write_review_outputs(payload, case_path, review_log_path, latest_review_path)
    write_case_readme(case_path, payload)
    _print_payload(payload, as_json)
    if result.status == "FIX" and result.severity == "critical" and not result.can_deliver:
        return 2
    return 0


def run_review(args: argparse.Namespace) -> int:
    if os.environ.get("AGENT_REVIEW_ACTIVE") == "1":
        return _print_loop_guard(args.json)

    inputs = _resolve_inputs(args)
    decision = should_trigger_review(
        changed_files=inputs.changed_files,
        diff_text=inputs.diff_text,
        commands_log_text=inputs.commands_log_text,
        tests_log_text=inputs.tests_log_text,
        task_text=inputs.task_text,
        final_response_text=inputs.final_response_text,
    )
    return _run_review_case(inputs, decision, args.json)


def run_plan_review(args: argparse.Namespace) -> int:
    if os.environ.get("AGENT_REVIEW_ACTIVE") == "1":
        return _print_loop_guard(args.json)

    inputs = _resolve_plan_inputs(args)
    if not inputs.plan_text.strip():
        message = "plan-review requires --plan-text or --plan-file"
        payload = {"status": "ERROR", "summary": message, "findings": [message], "evidence": []}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"ERROR {message}")
        return 1

    decision = PolicyDecision(True, "plan review requested", [])
    return _run_review_case(inputs, decision, args.json)


def run_init_config(args: argparse.Namespace) -> int:
    if args.global_config:
        output_path = DEFAULT_GLOBAL_CONFIG_PATH
    else:
        project_path = Path(args.project_path).expanduser().resolve()
        output_path = project_path / "agent-review.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "knowledge_base_root": "",
        "sync_kb": False,
        "retention_days": 7,
        "reviewer_commands": {},
        "reviewer_adapters": {
            "codex": True,
            "claude-code": True,
            "hermes": True
        },
        "reviewer_timeout_seconds": 180,
        "reviewer_command_examples": {
            "codex": "built-in adapter: codex exec -C <project> -s read-only ...",
            "claude-code": "built-in adapter: claude -p --json-schema ...",
            "hermes": "built-in adapter: hermes chat -q ... -Q"
        },
        "privacy_masks": [".env", ".pem", ".key", "token", "secret"]
    }
    write_json(output_path, payload)
    print(output_path)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "review":
        return run_review(args)
    if args.command == "plan-review":
        return run_plan_review(args)
    if args.command == "init-config":
        return run_init_config(args)
    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
