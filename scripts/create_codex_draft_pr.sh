#!/usr/bin/env bash
set -euo pipefail

if [[ "${CREATE_DRAFT_PR:-false}" != "true" ]]; then
  echo "CREATE_DRAFT_PR is not true; skipping draft PR creation."
  echo "Set CREATE_DRAFT_PR=true to create a draft PR handoff."
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is not installed. Install gh and authenticate before creating a draft PR."
  exit 0
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run gh auth login before creating a draft PR."
  exit 0
fi

BRANCH="codex/request-purchase-order-approval-api"
TITLE="Add API facade for purchase order approval request"
BODY_FILE="generated-artifacts/request_purchase_order_approval/codex-pr-prompt.md"

git checkout -B "$BRANCH"
git add generated-artifacts/request_purchase_order_approval
if git diff --cached --quiet; then
  echo "No generated artifacts to commit."
else
  git commit -m "docs: add purchase order approval modernization artifacts"
fi
git push -u origin "$BRANCH"
gh pr create --draft --title "$TITLE" --body-file "$BODY_FILE"
