## Task Completion Review Protocol

## Plan Review Protocol

Use manual plan review only when the user explicitly asks for `复核`, `Agent 复核`, `方案复核`, `先复核方案`, `帮我复核一下`, or `plan-review` before execution.

If plan review is needed:

1. Treat Claude Code as the `host_agent`.
2. Do not review your own work.
3. Run `agent-review plan-review --host claude-code --project-path <project_path> --task-text <task> --plan-text <proposed_plan>`.
4. Wait for `PASS / FIX`.
5. Adopt only grounded advice; optional better approaches belong in `suggestions`, not automatic changes.
6. Explain the plan review in simple Chinese: whether the plan can continue, who reviewed it, what issue was found, and what Claude Code will do next. Do not show raw field names or review log paths by default.

Use manual review triggering for now. Do not run `agent-review` automatically at Stop Hook time or before final delivery just because a task changed files, has git diff, ran tests/builds, or is about to be marked complete.

When a review keyword appears, first decide the phase:

- Before execution: use `agent-review plan-review`.
- After execution or after a deliverable exists: use `agent-review review`.

After finishing a deliverable task, add a short prompt at the end of the user-facing reply:

```text
需要我再调用 agent-review 做一次复核吗？你可以回复“复核”或“帮我复核一下”。
```

Trigger review only when any of these are true:

- The user explicitly asks for review.
- The user prompt contains `复核`, `帮我复核`, `帮我复核一下`, `Agent 复核`, `自动复核`, or `agent-review`.

Skip review when:

- `AGENT_REVIEW_ACTIVE=1` is set.
- The user has not explicitly asked for review.
- The task is only Q&A or planning, and the user did not explicitly ask for plan review.
- The user explicitly asked to skip review.

If review is needed:

1. Treat Claude Code as the `host_agent`.
2. Do not review your own work.
3. Run `agent-review review --host claude-code --project-path <project_path>`.
4. Wait for `PASS / FIX`.
5. If the result is `FIX`, inspect `severity`, `issues`, and `fix_instructions`; do not auto-fix unless the issue is grounded in the user request, system constraints, host-agent promises, or project rules.
6. Auto-fix at most once for the same task. If it is still `FIX`, stop and explain the remaining issues to the user.
7. Surface the review result in Claude Code's reply.

After review completes, Claude Code's user-facing reply must be in simple Chinese that a non-technical user can understand. Do not show raw field names by default.

Include the same four user-facing lines every time:

- 复核结论：用“通过 / 需要修改 / 不建议继续”这类人话说明结果。
- 谁复核的：说明是哪个 Agent 复核；如果是 `local` / `builtin`，说“本地规则检查”，不要包装成外部 Agent。
- 主 Agent 怎么处理：说明 Claude Code 已采纳什么、改了什么、验证了什么；如果没采纳，说明原因。
- 下一步：说明可以交付、先修复，还是等用户确认。

Use this fixed format by default:

```markdown
复核结论：通过 / 需要修改 / 不建议继续

- 复核方：Codex / Claude Code / Hermes / 本地规则检查
- 发现的问题：一句话说明；没有问题就写“未发现需要修改的问题”。
- 主 Agent 处理：已采纳什么、已修改什么、已验证什么；未采纳就说明原因。
- 下一步：可以交付 / 我先修复 / 需要你决定。
```

When the next step is `我先修复`, continue fixing immediately and do not wait for user confirmation.

If the content is long, use short bullet points. Do not include `review_log_path` or `latest_review_path` unless the user asks for the report path or debugging requires it.

If `agent-review` is unavailable or fails, say that manual review did not run and include the error briefly.
