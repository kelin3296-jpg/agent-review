from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .utils import ensure_dir, write_text


def _safe_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text or "无"


def _relative_link(root: Path, target: str | None, label: str) -> str:
    if not target:
        return "无"
    target_path = Path(target)
    try:
        rel = target_path.relative_to(root)
        return f"[{label}]({rel})"
    except ValueError:
        return f"[{label}]({target})"


def write_case_readme(case_path: Path, payload: Mapping[str, object]) -> Path:
    readme_path = case_path / "README.md"
    raw_output = _safe_text(payload.get("raw_reviewer_output_path"))
    case_id = _safe_text(payload.get("case_id"))
    review_type = _safe_text(payload.get("review_type"))
    readiness_label = "是否可继续" if review_type == "plan" else "是否可交付"
    content = f"""# 复核案卷 {case_id}

这个文件夹是本次复核案卷。给 reviewer 的原始材料只有小体积 Markdown：`review-brief.md`。

本地案卷编号：`{case_id}`

程序案卷编号：`{payload.get("case_id")}`

## 默认只看

- `review-brief.md`：给复核 Agent 的唯一原始材料，已做大小截断。
- `review-result.md`：复核后的可读结果。

## 不给 reviewer 默认读取

- JSON / stdout / stderr / 历史输出都是工具产物，不是复核原始材料。
- 如果 brief 里提示 diff 被截断，reviewer 可以按改动文件列表去读项目当前文件。
- 外部 reviewer 原始输出：`{raw_output}`（默认不持久化）

## 本次结果

- 复核阶段：{review_type}
- 结论：{payload.get("status")}
- {readiness_label}：{payload.get("can_deliver")}
- 严重程度：{payload.get("severity")}
- 主 Agent：{payload.get("host_agent")}
- 选中的复核 Agent：{payload.get("selected_reviewer")}
- 实际复核 Agent：{payload.get("reviewer")}
- 复核类型：{payload.get("reviewer_kind")}
- 是否降级：{payload.get("fallback_reason") or "无"}

## 知识库同步

不写入知识库。复核记录和原始资料只保存在当前项目的 `.agent-review/` 目录内，默认最多保留 7 天。
"""
    write_text(readme_path, content)
    return readme_path


def _read_case_payload(result_path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_runtime_index(project_path: Path) -> Path:
    review_root = ensure_dir(project_path / ".agent-review")
    cases_dir = ensure_dir(review_root / "cases")
    index_path = review_root / "复核案卷索引.md"
    rows: list[str] = []

    for result_path in sorted(cases_dir.glob("*/review-result.json"), reverse=True):
        payload = _read_case_payload(result_path)
        if not payload:
            continue
        case_id = _safe_text(payload.get("case_id"))
        case_path = result_path.parent
        readme_target = case_path / "README.md"
        case_label = "原始案卷说明" if readme_target.exists() else "原始案卷"
        case_target = readme_target if readme_target.exists() else case_path
        rows.append(
            "| {case_id} | {review_type} | {status} | {severity} | {can_continue} | {reviewer} | {kind} | {review_file} | {case_folder} |".format(
                case_id=case_id,
                review_type=_safe_text(payload.get("review_type")),
                status=_safe_text(payload.get("status")),
                severity=_safe_text(payload.get("severity")),
                can_continue=_safe_text(payload.get("can_deliver")),
                reviewer=_safe_text(payload.get("reviewer")),
                kind=_safe_text(payload.get("reviewer_kind")),
                review_file=_relative_link(review_root, str(case_path / "review-result.md"), "复核文件"),
                case_folder=_relative_link(review_root, str(case_target), case_label),
            )
        )

    if not rows:
        rows.append("| 暂无 | - | - | - | - | - | - | - | - |")

    content = f"""# 复核案卷索引

这个文件是当前项目里的自动复核原始资料入口。每次复核都会生成一个独立案卷目录：

```text
.agent-review/cases/<YYMMDD-编号>/
```

正常只看两个 Markdown 文件：

- `review-brief.md`：复核 Agent 默认读取的唯一原始材料，内容已截断控大小。
- `review-result.md`：本次复核结果，给人看。

案卷里的 JSON / stdout / stderr 是工具产物，不是 reviewer 默认输入。

复核记录和原始资料只保存在当前项目的 `.agent-review/` 目录，不写入知识库。每次运行 `agent-review review` 或 `agent-review plan-review` 时会自动清理超过 7 天的案卷。

最近一次复核报告固定在：

```text
.agent-review/latest-review.md
```

## 全部复核案卷

| 本地案卷编号 | 复核阶段 | 结论 | 严重程度 | 是否可继续 | 实际复核 Agent | 复核类型 | 复核文件 | 原始案卷 |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}
"""
    write_text(index_path, content)
    return index_path
