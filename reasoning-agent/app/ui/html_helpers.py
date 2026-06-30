"""Shared HTML helpers for the Legacy ERP shell pages.

This module holds small, dependency-free constants and helpers that are
reused across multiple HTML render functions in ``app.main``. It is
intentionally minimal — only genuinely duplicated text is extracted here,
to avoid inventing abstractions that aren't backed by real reuse.

Nothing in this module performs I/O or mutates shared state. All functions
are pure and return strings.
"""
from __future__ import annotations


# Repeated safety disclaimer used by both /proposals/inbox and
# /approvals/inbox. Extracted verbatim — do not reword without updating
# both call sites, since tests assert on the exact phrasing.
SAFETY_NO_CODEX_NO_XAML_NO_DEPLOY_NO_TRUSTED: str = (
    "No Codex calls, no XAML modifications, no API deployments, "
    "no trusted capability registration."
)
