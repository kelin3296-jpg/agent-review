from __future__ import annotations

from pathlib import Path
from typing import Dict

from .models import PolicyDecision, ReviewInputs, ReviewerSelection
from .utils import ensure_dir, now_utc_iso, write_json, write_text


MAX_TASK_CHARS = 2000
MAX_FINAL_RESPONSE_CHARS = 3000
MAX_DIFF_CHARS = 6000
MAX_TESTS_CHARS = 4000
MAX_CHANGED_FILES = 80


def _clip_text(text: str, limit: int, empty: str = "无") -> str:
    value = text.strip()
    if not value:
        return empty
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n\n...已截断，原始内容 {len(value)} 字符，本节只保留前 {limit} 字符。"


def _line_items(items: list[str], empty: str = "无", limit: int = MAX_CHANGED_FILES) -> str:
    if not items:
        return f"- {empty}"
    visible = items[:limit]
    lines = [f"- {item}" for item in visible]
    if len(items) > limit:
        lines.append(f"- ...已截断，另有 {len(items) - limit} 个文件未展开")
    return "\n".join(lines)


def _build_delivery_review_brief(inputs: ReviewInputs, decision: PolicyDecision, selection: ReviewerSelection, case_id: str) -> str:
    tests_state = "有测试日志，下面只放截断摘要。" if inputs.tests_log_text.strip() else "没有提供测试日志。"
    commands_state = "有命令日志，但不进入 reviewer 输入。" if inputs.commands_log_text.strip() else "没有提供命令日志。"
    return f"""# Review Brief

这份 brief 是复核 Agent 默认应该看的全部原始材料，刻意保持小体积。不要读取案卷里的 JSON、日志或历史输出，避免把工具噪音塞进上下文。

## 1. 本轮身份

- 本地案卷编号：{case_id}
- 主 Agent：{inputs.host_agent}
- 计划复核 Agent：{selection.selected}
- 复核触发原因：{decision.reason}
- 项目路径：{inputs.project_path}

## 2. 用户任务

{_clip_text(inputs.task_text, MAX_TASK_CHARS, "未提供")}

## 3. 主 Agent 准备交付的说法

{_clip_text(inputs.final_response_text, MAX_FINAL_RESPONSE_CHARS, "未提供")}

## 4. 改动文件

{_line_items(inputs.changed_files)}

## 5. 高风险提示

{_line_items(decision.high_risk_files)}

## 6. 验证情况

- 测试日志：{tests_state}
- 命令日志：{commands_state}

## 7. Diff 摘要

```text
{_clip_text(inputs.diff_text, MAX_DIFF_CHARS, "没有提供 diff。")}
```

## 8. 测试摘要

```text
{_clip_text(inputs.tests_log_text, MAX_TESTS_CHARS, "无")}
```

## 9. 复核规则

- 只基于这份 Markdown brief 和项目当前文件判断，不要读取案卷里的日志、JSON 或历史 reviewer 输出。
- 如果 diff 被截断，可以按“改动文件”列表读取项目当前文件，但不要读取大日志。
- 先从用户任务、系统约束、主 Agent 承诺和明确项目规则中提取验收标准，不能创造新需求。
- 只有明确未满足项、必要证据缺失、测试失败、安全风险、格式错误或范围风险才返回 FIX。
- 普通优化建议不要触发 FIX，放到 suggestions。
- 如果证据不足，不要硬猜；必要证据缺失用 FIX + severity=normal，非必要建议放 suggestions。
"""


def _build_plan_review_brief(inputs: ReviewInputs, decision: PolicyDecision, selection: ReviewerSelection, case_id: str) -> str:
    return f"""# Review Brief

这份 brief 是方案复核 Agent 默认应该看的全部原始材料，刻意保持小体积。不要读取案卷里的 JSON、日志或历史输出，避免把工具噪音塞进上下文。

## 1. 本轮身份

- 本地案卷编号：{case_id}
- 复核类型：方案复核
- 主 Agent：{inputs.host_agent}
- 计划复核 Agent：{selection.selected}
- 复核触发原因：{decision.reason}
- 项目路径：{inputs.project_path}

## 2. 用户任务

{_clip_text(inputs.task_text, MAX_TASK_CHARS, "未提供")}

## 3. 主 Agent 拟执行方案

{_clip_text(inputs.plan_text, MAX_FINAL_RESPONSE_CHARS, "未提供")}

## 4. 补充上下文

{_clip_text(inputs.context_text, MAX_DIFF_CHARS, "无")}

## 5. 方案复核重点

- 方案是否对齐用户真实目标和明确边界。
- 是否存在更低成本、更稳、更少改动的做法。
- 是否遗漏关键风险、验收方式、回滚方式或用户明确要求。
- 是否有不必要的复杂度、过度设计或范围扩大。
- 是否存在执行前必须先确认的信息缺口。

## 6. 复核规则

- 这是做之前的方案复核，不是交付验收。
- 只审不发：复核 Agent 的输出只回流给主 Agent，由主 Agent 判断采纳和最终回复。
- 先从用户任务、系统约束、项目规则和主 Agent 方案中提取判断标准，不能创造新需求。
- PASS 表示方案可以继续执行；FIX 表示执行前必须改方案或补关键确认。
- 更好的可选方案、降复杂度建议、替代路径放到 suggestions；只有会影响执行正确性的内容才放 issues / fix_instructions。
- 如果信息不足且会影响方案方向，用 FIX + severity=normal；如果只是可执行细节不足，放 suggestions。
"""


def build_review_brief(inputs: ReviewInputs, decision: PolicyDecision, selection: ReviewerSelection, case_id: str) -> str:
    if inputs.review_type == "plan":
        return _build_plan_review_brief(inputs, decision, selection, case_id)
    return _build_delivery_review_brief(inputs, decision, selection, case_id)


def write_case_files(
    case_path: Path,
    case_id: str,
    inputs: ReviewInputs,
    decision: PolicyDecision,
    selection: ReviewerSelection,
    knowledge_note_path: Path | None,
) -> Dict[str, object]:
    ensure_dir(case_path)
    manifest = {
        "case_id": case_id,
        "review_type": inputs.review_type,
        "host_agent": inputs.host_agent,
        "review_agent": selection.selected,
        "project_path": str(inputs.project_path),
        "runtime_case_path": str(case_path),
        "knowledge_note_path": str(knowledge_note_path) if knowledge_note_path else "",
        "created_at": now_utc_iso(),
        "retention_days": 7,
        "changed_files_count": len(inputs.changed_files),
        "has_tests_log": bool(inputs.tests_log_text.strip()),
        "privacy_level": "local-default",
        "fallback_reason": selection.fallback_reason or "",
        "used_builtin_reviewer": selection.used_builtin,
        "review_brief_path": str(case_path / "review-brief.md"),
        "review_input_files": ["review-brief.md"],
        "review_input_format": "markdown",
    }

    write_json(case_path / "manifest.json", manifest)
    brief = build_review_brief(inputs, decision, selection, case_id)
    write_text(case_path / "review-brief.md", brief)
    manifest["review_brief_size_chars"] = len(brief)
    write_json(case_path / "manifest.json", manifest)
    return manifest
