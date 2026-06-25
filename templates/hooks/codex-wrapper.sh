#!/usr/bin/env bash
set -euo pipefail

# Example wrapper. Call this after a Codex task if you want a project-local review.

if [ "${AGENT_REVIEW_ACTIVE:-}" = "1" ]; then
  exit 0
fi

PROJECT_PATH="${1:-$PWD}"

agent-review review \
  --host codex \
  --project-path "$PROJECT_PATH" \
  --json
