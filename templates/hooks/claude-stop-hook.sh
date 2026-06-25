#!/usr/bin/env bash
set -euo pipefail

# Example only. Wire this into Claude Code Stop Hook after confirming actual hook env vars.

if [ "${AGENT_REVIEW_ACTIVE:-}" = "1" ]; then
  exit 0
fi

PROJECT_PATH="${CLAUDE_PROJECT_DIR:-$PWD}"
TASK_FILE="${AGENT_REVIEW_TASK_FILE:-}"
FINAL_RESPONSE_FILE="${AGENT_REVIEW_FINAL_RESPONSE_FILE:-}"
TESTS_LOG="${AGENT_REVIEW_TESTS_LOG:-}"
COMMANDS_LOG="${AGENT_REVIEW_COMMANDS_LOG:-}"

agent-review review \
  --host claude-code \
  --project-path "$PROJECT_PATH" \
  ${TASK_FILE:+--task-file "$TASK_FILE"} \
  ${FINAL_RESPONSE_FILE:+--final-response-file "$FINAL_RESPONSE_FILE"} \
  ${TESTS_LOG:+--tests-log "$TESTS_LOG"} \
  ${COMMANDS_LOG:+--commands-log "$COMMANDS_LOG"} \
  --json
