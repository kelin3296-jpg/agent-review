#!/usr/bin/env bash
set -euo pipefail

# Example Hermes hook entrypoint.

if [ "${AGENT_REVIEW_ACTIVE:-}" = "1" ]; then
  exit 0
fi

PROJECT_PATH="${HERMES_PROJECT_DIR:-$PWD}"

agent-review review \
  --host hermes \
  --project-path "$PROJECT_PATH" \
  --json
