from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .adapters import get_adapter
from .adapters.base import validate_review_payload
from .models import Config, ReviewInputs, ReviewResult, ReviewerSelection
from .policy import detect_secret_leak, has_failing_test_signal
from .utils import command_exists


ROUTING_TABLE = {
    "claude-code": ["codex", "hermes"],
    "codex": ["claude-code", "hermes"],
    "hermes": ["codex", "claude-code"],
}


def select_reviewer(host_agent: str, requested: Optional[str], config: Config) -> ReviewerSelection:
    if requested == "local":
        return ReviewerSelection(requested="local", selected="local", used_builtin=True, kind="builtin")

    if requested and requested == host_agent:
        raise ValueError("reviewer cannot be the same as host agent")

    if requested:
        command = config.reviewer_commands.get(requested)
        if command and _command_template_available(command):
            return ReviewerSelection(requested=requested, selected=requested, kind="command")
        if _adapter_enabled_and_available(requested, config):
            return ReviewerSelection(requested=requested, selected=requested, kind="adapter")
        return ReviewerSelection(
            requested=requested,
            selected="local",
            fallback_reason=f"requested reviewer '{requested}' is not installed or not configured",
            used_builtin=True,
            kind="builtin",
        )

    for candidate in ROUTING_TABLE[host_agent]:
        command = config.reviewer_commands.get(candidate)
        if command and _command_template_available(command):
            return ReviewerSelection(requested=None, selected=candidate, kind="command")
        if _adapter_enabled_and_available(candidate, config):
            return ReviewerSelection(requested=None, selected=candidate, kind="adapter")

    return ReviewerSelection(
        requested=None,
        selected="local",
        fallback_reason="no external reviewer command or enabled adapter available; using builtin policy reviewer",
        used_builtin=True,
        kind="builtin",
    )


def _command_template_available(command_template: str) -> bool:
    try:
        args = shlex.split(command_template)
    except ValueError:
        return False
    if not args:
        return False
    head = args[0]
    return command_exists(head)


def _adapter_enabled_and_available(agent_name: str, config: Config) -> bool:
    if not config.reviewer_adapters.get(agent_name, False):
        return False
    adapter = get_adapter(agent_name)
    return bool(adapter and adapter.is_available())


def _checklist_item(item_id: str, criterion: str, source: str, result: str, evidence: str) -> Dict[str, str]:
    return {
        "id": item_id,
        "criterion": criterion,
        "source": source,
        "result": result,
        "evidence": evidence,
    }


def _issue(issue_type: str, severity: str, description: str, evidence: str, fix_instruction: str) -> Dict[str, str]:
    return {
        "type": issue_type,
        "severity": severity,
        "description": description,
        "evidence": evidence,
        "fix_instruction": fix_instruction,
    }


def _security_preflight(inputs: ReviewInputs) -> Optional[ReviewResult]:
    if inputs.review_type == "plan":
        return None

    secret_findings = detect_secret_leak(inputs.diff_text, inputs.changed_files)
    if not secret_findings:
        return None

    return ReviewResult(
        status="FIX",
        summary="发现疑似密钥或环境敏感信息，不能直接交付。",
        findings=secret_findings,
        evidence=["detected secret-like content in diff or environment file changes"],
        reviewer_agent="local",
        reviewer_kind="builtin-preflight",
        severity="critical",
        acceptance_checklist=[
            _checklist_item(
                "AC-1",
                "交付内容不能包含疑似密钥、token 或环境敏感信息",
                "系统安全约束",
                "fail",
                "diff 或改动文件命中了敏感信息规则",
            )
        ],
        issues=[
            _issue(
                "safety_risk",
                "critical",
                "发现疑似密钥或环境敏感信息",
                "; ".join(secret_findings),
                "移除敏感信息，改用本地环境变量或安全配置，并重新复核。",
            )
        ],
        fix_instructions=[
            "移除疑似密钥或环境敏感信息。",
            "确认 .env / token / secret 没有进入交付内容。",
            "重新运行 agent-review。",
        ],
        can_deliver=False,
        reviewer_confidence="high",
    )


def _builtin_plan_review(inputs: ReviewInputs, selection: ReviewerSelection) -> ReviewResult:
    if len(inputs.plan_text.strip()) < 20:
        return ReviewResult(
            status="FIX",
            summary="方案内容太短，复核 Agent 无法判断是否可以执行。",
            findings=["plan text is too short to review"],
            evidence=["plan-review requires a concrete proposed plan"],
            reviewer_agent=selection.selected,
            reviewer_kind="builtin",
            fallback_reason=selection.fallback_reason,
            severity="normal",
            acceptance_checklist=[
                _checklist_item(
                    "AC-1",
                    "方案复核需要看到主 Agent 拟执行的具体方案",
                    "方案复核输入要求",
                    "fail",
                    "方案文本少于 20 个字符",
                )
            ],
            issues=[
                _issue(
                    "evidence_missing",
                    "normal",
                    "缺少可复核的具体方案",
                    "plan text is too short",
                    "补充执行步骤、边界、验证方式或取舍理由后重新做方案复核。",
                )
            ],
            fix_instructions=["补充更具体的方案后重新运行 plan-review。"],
            can_deliver=False,
            reviewer_confidence="medium",
        )

    return ReviewResult(
        status="PASS",
        summary="方案复核已完成；内置规则未发现必须先改的阻断项，可以继续执行。",
        findings=["plan review brief contains a concrete proposed plan"],
        evidence=["plan text was provided before execution"],
        reviewer_agent=selection.selected,
        reviewer_kind="builtin",
        fallback_reason=selection.fallback_reason,
        severity="none",
        acceptance_checklist=[
            _checklist_item(
                "AC-1",
                "执行前已提供可复核的主 Agent 方案",
                "方案复核输入要求",
                "pass",
                "方案文本已提供",
            )
        ],
        suggestions=[
            "内置方案复核只做轻量兜底；如果要判断是否存在更优方案，优先使用 Codex / Claude Code / Hermes 等外部 reviewer。",
        ],
        can_deliver=True,
        reviewer_confidence="low",
    )


def _builtin_review(inputs: ReviewInputs, selection: ReviewerSelection) -> ReviewResult:
    if inputs.review_type == "plan":
        return _builtin_plan_review(inputs, selection)

    findings: List[str] = []
    evidence: List[str] = []

    if inputs.tests_log_text.strip():
        if has_failing_test_signal(inputs.tests_log_text):
            findings.append("test output shows failure signals")
            evidence.append("provided test output contains failure keywords")
            return ReviewResult(
                status="FIX",
                summary="测试日志里有失败信号，但任务准备交付，先不要放行。",
                findings=findings,
                evidence=evidence,
                reviewer_agent=selection.selected,
                reviewer_kind="builtin",
                fallback_reason=selection.fallback_reason,
                severity="critical",
                acceptance_checklist=[
                    _checklist_item(
                        "AC-1",
                        "交付前测试或验证不能出现失败信号",
                        "系统交付约束",
                        "fail",
                        "测试日志包含失败关键词",
                    )
                ],
                issues=[
                    _issue(
                        "test_failure",
                        "critical",
                        "测试日志显示失败",
                        "provided test output contains failure keywords",
                        "先修复失败测试或解释误报，并重新运行复核。",
                    )
                ],
                fix_instructions=[
                    "修复测试失败或说明失败信号为何是误报。",
                    "重新运行相关测试。",
                    "重新运行 agent-review。",
                ],
                can_deliver=False,
                reviewer_confidence="high",
            )
    elif any(item.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java")) for item in inputs.changed_files):
        findings.append("code changed without test output")
        evidence.append("changed files include source code but no test output was provided")
        return ReviewResult(
            status="FIX",
            summary="代码有改动，但没有看到测试记录，建议补一轮验证。",
            findings=findings,
            evidence=evidence,
            reviewer_agent=selection.selected,
            reviewer_kind="builtin",
            fallback_reason=selection.fallback_reason,
            severity="normal",
            acceptance_checklist=[
                _checklist_item(
                    "AC-1",
                    "源码改动需要有对应测试或验证记录",
                    "系统交付约束",
                    "unknown",
                    "未提供测试日志",
                )
            ],
            issues=[
                _issue(
                    "evidence_missing",
                    "normal",
                    "源码改动缺少测试记录",
                    "changed files include source code but no test output was provided",
                    "补充测试日志；如果无法运行测试，需要在最终回复中说明原因和替代验证。",
                )
            ],
            fix_instructions=[
                "补充测试或验证记录。",
                "如果不能运行测试，说明原因和替代检查结果。",
            ],
            can_deliver=False,
            reviewer_confidence="medium",
        )

    if inputs.changed_files and all(item.endswith((".md", ".txt")) for item in inputs.changed_files):
        findings.append("documentation-only changes")
        evidence.append("all changed files are documentation")
        return ReviewResult(
            status="PASS",
            summary="这次看起来只是文档改动，风险较低，可以交付。",
            findings=findings,
            evidence=evidence,
            reviewer_agent=selection.selected,
            reviewer_kind="builtin",
            fallback_reason=selection.fallback_reason,
            severity="none",
            acceptance_checklist=[
                _checklist_item(
                    "AC-1",
                    "仅文档改动没有明显代码运行风险",
                    "内置规则",
                    "pass",
                    "改动文件全部是 .md 或 .txt",
                )
            ],
            can_deliver=True,
            reviewer_confidence="medium",
        )

    if not inputs.changed_files:
        return ReviewResult(
            status="SKIP",
            summary="没有检测到文件改动，跳过自动复核。",
            findings=[],
            evidence=[],
            reviewer_agent=selection.selected,
            reviewer_kind="builtin",
            fallback_reason=selection.fallback_reason,
            severity="none",
            can_deliver=True,
        )

    return ReviewResult(
        status="PASS",
        summary="未发现明确阻断项，可以交付。",
        findings=findings,
        evidence=evidence,
        reviewer_agent=selection.selected,
        reviewer_kind="builtin",
        fallback_reason=selection.fallback_reason,
        severity="none",
        acceptance_checklist=[
            _checklist_item(
                "AC-1",
                "没有检测到明确交付风险",
                "内置规则",
                "pass",
                "未命中测试失败、敏感信息或缺少必要验证规则",
            )
        ],
        can_deliver=True,
        reviewer_confidence="medium",
    )


def _render_command_args(command_template: str, case_path: Path) -> List[str]:
    try:
        args = shlex.split(command_template)
    except ValueError as exc:
        raise RuntimeError(f"invalid reviewer command template: {exc}") from exc

    if not args:
        raise RuntimeError("reviewer command template is empty")

    return [arg.replace("{case_path}", str(case_path)) for arg in args]


def _external_review(command_template: str, case_path: Path, selection: ReviewerSelection) -> ReviewResult:
    command = _render_command_args(command_template, case_path)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())
        raise RuntimeError(detail[:2000] or f"reviewer command failed with code {result.returncode}")

    payload: Dict[str, object] = json.loads(result.stdout)
    review = validate_review_payload(payload)
    review.reviewer_agent = selection.selected
    review.reviewer_kind = "external"
    review.fallback_reason = selection.fallback_reason
    return review


def run_reviewer(
    inputs: ReviewInputs,
    selection: ReviewerSelection,
    config: Config,
    case_path: Path,
) -> ReviewResult:
    preflight_result = _security_preflight(inputs)
    if preflight_result:
        return preflight_result

    if selection.used_builtin:
        return _builtin_review(inputs, selection)

    if selection.kind == "adapter":
        adapter = get_adapter(selection.selected)
        if not adapter:
            fallback = ReviewerSelection(
                requested=selection.requested,
                selected="local",
                fallback_reason=f"reviewer adapter '{selection.selected}' is not configured",
                used_builtin=True,
                kind="builtin",
            )
            return _builtin_review(inputs, fallback)
        if not adapter.is_available():
            fallback = ReviewerSelection(
                requested=selection.requested,
                selected="local",
                fallback_reason=f"reviewer adapter '{selection.selected}' command is not installed",
                used_builtin=True,
                kind="builtin",
            )
            return _builtin_review(inputs, fallback)
        try:
            return adapter.run(inputs, config, case_path)
        except (OSError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            return _fallback_after_external_failure(inputs, selection, f"reviewer adapter '{selection.selected}' failed: {exc}")

    command_template = config.reviewer_commands.get(selection.selected)
    if not command_template:
        fallback = ReviewerSelection(
            requested=selection.requested,
            selected="local",
            fallback_reason=f"reviewer '{selection.selected}' is not configured",
            used_builtin=True,
            kind="builtin",
        )
        return _builtin_review(inputs, fallback)

    if not _command_template_available(command_template):
        fallback = ReviewerSelection(
            requested=selection.requested,
            selected="local",
            fallback_reason=f"reviewer '{selection.selected}' is not installed",
            used_builtin=True,
            kind="builtin",
        )
        return _builtin_review(inputs, fallback)

    try:
        return _external_review(command_template, case_path, selection)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        return _fallback_after_external_failure(inputs, selection, f"reviewer '{selection.selected}' failed: {exc}")


def _fallback_after_external_failure(inputs: ReviewInputs, selection: ReviewerSelection, fallback_reason: str) -> ReviewResult:
    fallback = ReviewerSelection(
        requested=selection.requested,
        selected="local",
        fallback_reason=fallback_reason,
        used_builtin=True,
        kind="builtin",
    )
    fallback_result = _builtin_review(inputs, fallback)
    fallback_result.fallback_reason = fallback_reason
    if fallback_result.status in ("PASS", "SKIP"):
        return ReviewResult(
            status="FIX",
            summary="外部 reviewer 执行失败，已降级到内置规则复核；这次没有完成真实跨 Agent 复核。",
            findings=["external reviewer failed; builtin fallback used"],
            evidence=[fallback_reason],
            reviewer_agent="local",
            reviewer_kind="builtin",
            fallback_reason=fallback_reason,
            severity="normal",
            acceptance_checklist=[
                _checklist_item(
                    "AC-1",
                    "真实外部 reviewer 应完成交付前语义复核",
                    "系统复核约束",
                    "unknown",
                    fallback_reason,
                )
            ],
            issues=[
                _issue(
                    "reviewer_failed",
                    "normal",
                    "外部 reviewer 执行失败",
                    fallback_reason,
                    "查看 fallback_reason，确认是否接受内置规则结果，或修复 reviewer 后重新复核。",
                )
            ],
            fix_instructions=[
                "确认是否接受本地规则降级结果。",
                "必要时修复外部 reviewer 配置后重新复核。",
            ],
            can_deliver=False,
            reviewer_confidence="low",
        )
    fallback_result.findings.append("external reviewer failed; builtin fallback used")
    fallback_result.evidence.append(fallback_reason)
    return fallback_result
