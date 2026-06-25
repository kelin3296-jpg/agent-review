from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_review.models import Config, ReviewInputs  # noqa: E402
from agent_review.adapters.base import (  # noqa: E402
    REVIEW_RESULT_SCHEMA,
    _short_process_detail,
    parse_review_output,
)
from agent_review.reviewers import run_reviewer, select_reviewer  # noqa: E402


def run_cli(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["AGENT_REVIEW_IGNORE_GLOBAL_CONFIG"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agent_review", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class ReviewFlowTests(unittest.TestCase):
    def write_fake_reviewer(self, project: Path, script_body: str) -> Path:
        reviewer = project / "fake reviewer.py"
        reviewer.write_text(script_body, encoding="utf-8")
        reviewer.chmod(0o755)
        return reviewer

    def write_config(self, project: Path, reviewer_command: str) -> None:
        (project / "agent-review.json").write_text(
            json.dumps({"reviewer_commands": {"codex": reviewer_command}}),
            encoding="utf-8",
        )

    def test_docs_change_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            changed.write_text("README.md\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["host_agent"], "codex")
            self.assertEqual(payload["selected_reviewer"], "local")
            self.assertEqual(payload["reviewer"], "local")
            self.assertTrue(Path(payload["review_log_path"]).exists())
            self.assertTrue(Path(payload["latest_review_path"]).exists())
            self.assertTrue(Path(payload["runtime_index_path"]).exists())
            self.assertEqual(payload["severity"], "none")
            self.assertTrue(payload["can_deliver"])
            self.assertEqual(payload["review_status"], "PASS")
            case_readme = Path(payload["case_path"]) / "README.md"
            review_brief = Path(payload["case_path"]) / "review-brief.md"
            self.assertTrue(case_readme.exists())
            self.assertTrue(review_brief.exists())
            self.assertIn("唯一原始材料", case_readme.read_text(encoding="utf-8"))
            self.assertIn("Review Brief", review_brief.read_text(encoding="utf-8"))
            self.assertIn("## Acceptance Checklist", Path(payload["review_log_path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["knowledge_note_path"], "")

    def test_code_change_without_tests_returns_fix_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            changed.write_text("src/app.py\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "normal")
            self.assertFalse(payload["can_deliver"])
            self.assertEqual(payload["issues"][0]["type"], "evidence_missing")

    def test_failing_tests_return_fix_critical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("FAILED test_example\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "hermes",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "critical")
            self.assertFalse(payload["can_deliver"])

    def test_secret_leak_returns_fix_critical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            diff_file = project / "diff.patch"
            changed.write_text(".env\n", encoding="utf-8")
            secret_like_line = "+API" + "_KEY=" + "example-value\n"
            diff_file.write_text(secret_like_line, encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--diff-file",
                str(diff_file),
                "--json",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "critical")
            self.assertFalse(payload["can_deliver"])

    def test_plan_review_with_local_reviewer_writes_plan_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = run_cli(
                "plan-review",
                "--host",
                "codex",
                "--reviewer",
                "local",
                "--project-path",
                str(project),
                "--task-text",
                "给复核插件增加方案复核能力",
                "--plan-text",
                "先保留现有交付复核命令，再新增 plan-review 命令，复用案卷、路由和结果输出，只补方案复核 brief。",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["review_type"], "plan")
            self.assertTrue(payload["can_proceed"])
            self.assertTrue(payload["can_deliver"])
            self.assertEqual(payload["reviewer"], "local")
            self.assertEqual(payload["reviewer_kind"], "builtin")
            self.assertEqual(payload["reviewer_confidence"], "low")
            review_brief = (Path(payload["case_path"]) / "review-brief.md").read_text(encoding="utf-8")
            self.assertIn("复核类型：方案复核", review_brief)
            self.assertIn("主 Agent 拟执行方案", review_brief)
            self.assertIn("只审不发", review_brief)
            review_log = Path(payload["review_log_path"]).read_text(encoding="utf-8")
            self.assertIn("Review Type: plan", review_log)
            self.assertIn("Can Proceed: True", review_log)
            runtime_index = Path(payload["runtime_index_path"]).read_text(encoding="utf-8")
            self.assertIn("| 本地案卷编号 | 复核阶段 | 结论 | 严重程度 | 是否可继续 |", runtime_index)

    def test_plan_review_requires_plan_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = run_cli(
                "plan-review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--task-text",
                "缺少方案文本",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ERROR")
            self.assertIn("requires --plan-text or --plan-file", payload["summary"])

    def test_external_reviewer_cannot_bypass_secret_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            reviewer = self.write_fake_reviewer(
                project,
                "import json\nprint(json.dumps({'status': 'PASS', 'summary': 'external ok', 'findings': [], 'evidence': []}))\n",
            )
            self.write_config(project, f"{shlex.quote(sys.executable)} {shlex.quote(str(reviewer))} {{case_path}}")
            changed = project / "changed.txt"
            diff_file = project / "diff.patch"
            changed.write_text(".env\n", encoding="utf-8")
            secret_like_line = "+API" + "_KEY=" + "example-value\n"
            diff_file.write_text(secret_like_line, encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--diff-file",
                str(diff_file),
                "--json",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "critical")
            self.assertEqual(payload["reviewer_kind"], "builtin-preflight")

    def test_external_reviewer_case_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent review ") as tmp:
            project = Path(tmp)
            reviewer = self.write_fake_reviewer(
                project,
                "\n".join(
                    [
                        "import json",
                        "import sys",
                        "from pathlib import Path",
                        "case_path = Path(sys.argv[1])",
                        "print(json.dumps({'status': 'PASS', 'summary': 'external ok', 'findings': [], 'evidence': [str(case_path.exists())]}))",
                        "",
                    ]
                ),
            )
            self.write_config(project, f"{shlex.quote(sys.executable)} {shlex.quote(str(reviewer))} {{case_path}}")
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("ok\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["reviewer_kind"], "external")
            self.assertEqual(payload["evidence"], ["True"])
            self.assertEqual(payload["raw_reviewer_output_path"], "")

    def test_invalid_external_status_returns_fix_with_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            reviewer = self.write_fake_reviewer(
                project,
                "import json\nprint(json.dumps({'status': 'MAYBE', 'summary': 'bad status', 'findings': [], 'evidence': []}))\n",
            )
            self.write_config(project, f"{shlex.quote(sys.executable)} {shlex.quote(str(reviewer))} {{case_path}}")
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("ok\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "normal")
            self.assertFalse(payload["can_deliver"])
            self.assertIn("invalid status", payload["fallback_reason"])

    def test_legacy_external_block_maps_to_fix_critical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            reviewer = self.write_fake_reviewer(
                project,
                "import json\nprint(json.dumps({'status': 'BLOCK', 'summary': 'legacy block', 'findings': ['bad'], 'evidence': ['legacy evidence']}))\n",
            )
            self.write_config(project, f"{shlex.quote(sys.executable)} {shlex.quote(str(reviewer))} {{case_path}}")
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("ok\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FIX")
            self.assertEqual(payload["severity"], "critical")
            self.assertFalse(payload["can_deliver"])

    def test_passing_log_with_zero_errors_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("All tests passed with 0 errors\n0 failed\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")

    def test_parse_reviewer_output_reads_cli_wrapper_result(self) -> None:
        raw = json.dumps(
            {
                "type": "result",
                "result": json.dumps(
                    {
                        "status": "PASS",
                        "summary": "wrapped result parsed",
                        "findings": [],
                        "evidence": ["wrapper"],
                    }
                ),
            }
        )

        result = parse_review_output(raw)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.summary, "wrapped result parsed")
        self.assertEqual(result.evidence, ["wrapper"])

    def test_codex_output_schema_requires_every_declared_property(self) -> None:
        self.assertFalse(REVIEW_RESULT_SCHEMA["additionalProperties"])
        self.assertEqual(set(REVIEW_RESULT_SCHEMA["properties"]), set(REVIEW_RESULT_SCHEMA["required"]))

    def test_process_detail_keeps_actionable_error_tail(self) -> None:
        noisy_output = "startup prompt " * 200 + "ERROR: invalid_json_schema Missing 'suggestions'."

        detail = _short_process_detail(noisy_output, limit=120)

        self.assertIn("invalid_json_schema", detail)
        self.assertIn("suggestions", detail)
        self.assertLessEqual(len(detail), 120)

    def test_reviewer_cannot_be_same_as_host_returns_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            changed.write_text("src/app.py\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "codex",
                "--reviewer",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ERROR")
            self.assertIn("reviewer cannot be the same as host agent", payload["summary"])

    def test_loop_guard_skips_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            changed = project / "changed.txt"
            changed.write_text("src/app.py\n", encoding="utf-8")
            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--json",
                env_extra={"AGENT_REVIEW_ACTIVE": "1"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "SKIP")
            self.assertEqual(payload["reviewer_kind"], "loop-guard")

    def test_codex_adapter_runs_fake_binary_and_sets_loop_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            bin_dir = project / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "output_path = Path(sys.argv[sys.argv.index('-o') + 1])",
                        "output_path.write_text(json.dumps({",
                        "  'status': 'PASS',",
                        "  'summary': 'fake codex reviewed',",
                        "  'findings': [],",
                        "  'evidence': [os.environ.get('AGENT_REVIEW_ACTIVE', '')],",
                        "}), encoding='utf-8')",
                        "print('fake codex ok')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            inputs = ReviewInputs(
                host_agent="claude-code",
                reviewer=None,
                project_path=project,
                task_text="test task",
                final_response_text="done",
                commands_log_text="",
                tests_log_text="ok",
                changed_files=["src/app.py"],
                diff_text="",
            )
            config = Config(reviewer_adapters={"codex": True}, reviewer_timeout_seconds=5)
            with patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                selection = select_reviewer("claude-code", None, config)
                self.assertEqual(selection.selected, "codex")
                self.assertEqual(selection.kind, "adapter")
                case_path = project / ".agent-review" / "cases" / "adapter-test"
                case_path.mkdir(parents=True)
                result = run_reviewer(inputs, selection, config, case_path)

            self.assertEqual(result.status, "PASS")
            self.assertEqual(result.reviewer_agent, "codex")
            self.assertEqual(result.reviewer_kind, "adapter:codex")
            self.assertEqual(result.evidence, ["1"])
            self.assertIsNone(result.raw_output_path)

    def test_global_config_enables_adapter_without_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            bin_dir = project / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, sys",
                        "from pathlib import Path",
                        "output_path = Path(sys.argv[sys.argv.index('-o') + 1])",
                        "output_path.write_text(json.dumps({",
                        "  'status': 'PASS',",
                        "  'summary': 'global config adapter ran',",
                        "  'findings': [],",
                        "  'evidence': ['global-config'],",
                        "}), encoding='utf-8')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            global_config = project / "global-agent-review.json"
            global_config.write_text(
                json.dumps({"reviewer_adapters": {"codex": True}, "reviewer_timeout_seconds": 5}),
                encoding="utf-8",
            )
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("ok\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--json",
                env_extra={
                    "AGENT_REVIEW_IGNORE_GLOBAL_CONFIG": "0",
                    "AGENT_REVIEW_CONFIG": str(global_config),
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["reviewer"], "codex")
            self.assertEqual(payload["reviewer_kind"], "adapter:codex")
            self.assertTrue(Path(payload["review_log_path"]).exists())
            self.assertTrue(Path(payload["latest_review_path"]).exists())

    def test_review_records_stay_local_even_when_sync_kb_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            vault = project / "vault"
            old_review_dir = vault / "02_项目资料" / "03_个人项目" / "多 Agent 自动复核插件" / "复核记录"
            old_review_dir.mkdir(parents=True)
            (old_review_dir / "old-case.md").write_text(
                "\n".join(
                    [
                        "# 自动复核记录 old-case",
                        "",
                        "## 基本信息",
                        "",
                        "- 主 Agent：codex",
                        "- 实际复核 Agent：claude-code",
                        "- 结论：WARN",
                        "",
                        "## 主 Agent 最终回复草稿",
                        "",
                        "旧记录里主 Agent 说自己已经完成。",
                        "",
                        "## 复核结论",
                        "",
                        "旧记录里复核 Agent 说需要补充说明。",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (project / "agent-review.json").write_text(
                json.dumps(
                    {
                        "knowledge_base_root": str(vault),
                        "sync_kb": True,
                    }
                ),
                encoding="utf-8",
            )
            changed = project / "changed.txt"
            tests = project / "tests.log"
            changed.write_text("src/app.py\n", encoding="utf-8")
            tests.write_text("ok\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--tests-log",
                str(tests),
                "--task-text",
                "修复一个示例问题",
                "--final-response-text",
                "已经修复完成",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["knowledge_note_name"], "")
            self.assertEqual(payload["knowledge_note_path"], "")
            self.assertEqual(payload["knowledge_index_path"], "")
            self.assertEqual(Path(payload["case_path"]).name, payload["case_id"])
            self.assertRegex(payload["case_id"], r"^\d{6}-\d{3}$")
            self.assertEqual(list(old_review_dir.glob("260*.md")), [])
            runtime_index = Path(payload["runtime_index_path"]).read_text(encoding="utf-8")
            self.assertIn("| 本地案卷编号 | 复核阶段 | 结论 | 严重程度 | 是否可继续 |", runtime_index)
            self.assertIn(payload["case_id"], runtime_index)
            self.assertIn("不写入知识库", runtime_index)
            case_readme = (Path(payload["case_path"]) / "README.md").read_text(encoding="utf-8")
            self.assertIn(f"本地案卷编号：`{payload['case_id']}`", case_readme)
            self.assertIn("不写入知识库", case_readme)
            review_brief = (Path(payload["case_path"]) / "review-brief.md").read_text(encoding="utf-8")
            self.assertIn("这份 brief 是复核 Agent 默认应该看的全部原始材料", review_brief)
            self.assertIn(f"本地案卷编号：{payload['case_id']}", review_brief)
            self.assertIn("## 7. Diff 摘要", review_brief)
            self.assertIn("## 9. 复核规则", review_brief)
            self.assertFalse((Path(payload["case_path"]) / "diff.patch").exists())
            self.assertFalse((Path(payload["case_path"]) / "tests.log").exists())

    def test_old_review_cases_are_cleaned_before_writing_new_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cases = project / ".agent-review" / "cases"
            cases.mkdir(parents=True)
            old_id = (datetime.now() - timedelta(days=8)).strftime("%y%m%d") + "-001"
            recent_id = datetime.now().strftime("%y%m%d") + "-001"
            old_case = cases / old_id
            recent_case = cases / recent_id
            old_case.mkdir()
            recent_case.mkdir()
            (old_case / "review-result.json").write_text("{}", encoding="utf-8")
            (recent_case / "review-result.json").write_text("{}", encoding="utf-8")
            (project / "agent-review.json").write_text(json.dumps({"retention_days": 7}), encoding="utf-8")
            changed = project / "changed.txt"
            changed.write_text("README.md\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "codex",
                "--project-path",
                str(project),
                "--changed-files",
                str(changed),
                "--task-text",
                "更新文档",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(old_case.exists())
            self.assertTrue(recent_case.exists())
            self.assertIn(str(old_case.resolve()), payload["retention_removed_cases"])
            self.assertEqual(Path(payload["case_path"]).name, datetime.now().strftime("%y%m%d") + "-002")

    def test_case_directory_is_excluded_from_git_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
            (project / "src").mkdir()
            app = project / "src" / "app.py"
            app.write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/app.py"], cwd=project, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            app.write_text("print('hello again')\n", encoding="utf-8")

            result = run_cli(
                "review",
                "--host",
                "claude-code",
                "--reviewer",
                "local",
                "--project-path",
                str(project),
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
            self.assertIn(".agent-review/", exclude)
            status = subprocess.run(["git", "status", "--short"], cwd=project, text=True, capture_output=True, check=True)
            self.assertIn("src/app.py", status.stdout)
            self.assertNotIn(".agent-review", status.stdout)

    def test_adapter_timeout_returns_fix_without_dumping_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            bin_dir = project / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import time",
                        "time.sleep(5)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            inputs = ReviewInputs(
                host_agent="claude-code",
                reviewer=None,
                project_path=project,
                task_text="test task",
                final_response_text="done",
                commands_log_text="",
                tests_log_text="ok",
                changed_files=["src/app.py"],
                diff_text="",
            )
            config = Config(reviewer_adapters={"codex": True}, reviewer_timeout_seconds=1)
            with patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                selection = select_reviewer("claude-code", None, config)
                case_path = project / ".agent-review" / "cases" / "timeout-test"
                case_path.mkdir(parents=True)
                result = run_reviewer(inputs, selection, config, case_path)

            self.assertEqual(result.status, "FIX")
            self.assertEqual(result.severity, "normal")
            self.assertFalse(result.can_deliver)
            self.assertIn("timed out after", result.fallback_reason or "")
            self.assertNotIn("You are the review agent", result.fallback_reason or "")


if __name__ == "__main__":
    unittest.main()
