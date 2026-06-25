# Install agent-review With An AI Agent

Copy this prompt into Codex, Claude Code, Hermes, or another coding agent that can use a terminal.

```text
You are helping me install an open-source local CLI named agent-review.

Goal:
- Install https://github.com/kelin3296-jpg/agent-review as a local command.
- Verify it works.
- Add manual review instructions to my active AI agent configuration only when you can locate the right instruction file.
- Do not enable automatic hooks unless I explicitly ask.

Important behavior:
- agent-review is a local multi-agent review gate.
- It supports plan review before work and delivery review after work.
- It should be manually triggered by user review keywords, not automatically run on every task.
- Runtime review records must stay inside the target project under .agent-review/ and should not be committed.

Installation steps:
1. Check that Python 3.9+ and git are available.
2. Choose an install folder, for example:
   - macOS/Linux: ~/Developer/agent-review or ~/Projects/agent-review
   - Windows: %USERPROFILE%\Projects\agent-review
3. Clone the repository:
   git clone https://github.com/kelin3296-jpg/agent-review.git
4. Enter the repo and run tests:
   cd agent-review
   python3 -m unittest discover -s tests -v
5. Install the CLI:
   python3 -m pip install .
6. Verify:
   agent-review --help
7. Initialize global config:
   agent-review init-config --global-config
8. For the project I want reviewed, initialize project config:
   agent-review init-config --project-path <PROJECT_PATH>

If pip install does not work:
- Use the repo-local wrapper instead:
  chmod +x ./agent-review
  bash ./agent-review --help
- Optionally add the repository folder to PATH, or create a shell alias named agent-review.

Agent instruction setup:
1. Detect which agent environment you are currently in.
2. If this is Codex, read templates/core-rules/codex-AGENTS-snippet.md and append its rules to the appropriate AGENTS.md only after showing me the target path.
3. If this is Claude Code, read templates/core-rules/claude-CLAUDE-snippet.md and append its rules to the appropriate CLAUDE.md only after showing me the target path.
4. If this is Hermes, read templates/core-rules/hermes-SOUL-snippet.md and append its rules to the appropriate SOUL.md only after showing me the target path.
5. If you cannot identify the correct instruction file, do not guess. Print the snippet path and tell me to choose where to add it.

Manual usage examples:

Plan review before execution:
agent-review plan-review \
  --host codex \
  --project-path <PROJECT_PATH> \
  --task-text "Describe the user task" \
  --plan-text "Describe the proposed plan" \
  --json

Delivery review after execution:
agent-review review \
  --host codex \
  --project-path <PROJECT_PATH> \
  --task-text "Describe the user task" \
  --final-response-text "Paste the host agent final response draft" \
  --json

Acceptance:
- Show me the installed agent-review version/help output.
- Show me the test command result.
- Show me where config was created.
- Tell me whether any instruction file was changed.
- Do not print secrets or commit .agent-review runtime files.
```

## Host Values

Use the right `--host` value for the agent that is doing the main work:

| Main agent | `--host` value |
|---|---|
| Codex | `codex` |
| Claude Code | `claude-code` |
| Hermes | `hermes` |

## Review Keywords

Recommended manual trigger phrases:

```text
review this plan
run a plan review
review this delivery
run agent-review
复核
方案复核
帮我复核一下
```

The host agent should decide the phase:

- Before execution: `agent-review plan-review`
- After execution: `agent-review review`
