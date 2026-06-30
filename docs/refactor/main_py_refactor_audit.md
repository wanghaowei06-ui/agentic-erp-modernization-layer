# main.py Refactor Audit — Safe Refactor Hygiene Round

Date: 2026-06-29
Scope: `reasoning-agent/app/main.py` low-risk refactor to reduce redundancy
while preserving all endpoint URLs, request/response shapes, status-machine
semantics, UiPath selectors, DOM ids, and demo behavior.

---

## 1. Pre-refactor metrics

| Metric | Value |
|---|---|
| Total lines | 8,293 |
| Top-level functions | 139 |
| All functions (incl. nested) | 160 |
| FastAPI routes | 61 |
| Imports | 31 |
| `<style>` blocks | 6 |
| Inline `padding:` occurrences | 75 |
| `ctl00_MainContent_*` selector occurrences | 30 |

### Top 20 functions by size (pre-refactor)

| Lines | Range | Function |
|---:|---|---|
| 436 | L2044–2479 | `evaluate_capability_evolution` |
| 417 | L5918–6334 | `monitoring_live` |
| 273 | L3048–3320 | `_render_run_memory_html` |
| 232 | L7093–7324 | `_render_legacy_shell` |
| 190 | L8083–8272 | `memory_run_commit` |
| 189 | L3521–3709 | `_render_portfolio_html` |
| 174 | L2747–2920 | `_render_dashboard_html` |
| 170 | L1872–2041 | `_build_evolution_explainability` |
| 167 | L4524–4690 | `_build_evidence_snapshot` |
| 158 | L4119–4276 | `_evaluate_policy_gate` |
| 154 | L1514–1667 | `precheck` |
| 149 | L5642–5790 | `simulation_dashboard` |
| 129 | L7797–7925 | `_record_proposal` |
| 126 | L3949–4074 | `_render_router_lab_html` |
| 125 | L3764–3888 | `_build_route_plan` |
| 115 | L7328–7442 | `erp_work_queue` |
| 110 | L4705–4814 | `_build_evidence_markdown` |
| 108 | L2938–3045 | `_build_run_memory_dashboard` |
| 108 | L4293–4400 | `_render_policy_lab_html` |
| 102 | L7446–7547 | `erp_work_queue_detail` |

---

## 2. Duplication / redundancy findings

### 2.1 Repeated HTML/CSS rendering fragments
- 6 inline `<style>` blocks across render functions, each rebuilding the
  same Legacy ERP shell CSS (`.erp-shell`, `.erp-body`, `.module-menu`,
  `ctl00_MainContent_*` form-table rules, status colors, button styles).
  The largest was 143 lines.
- 113 `<tr>`, 251 `<td>`, 179 `<th>` occurrences — most built inline with
  f-strings; no shared `render_table_row` / `render_cell` helper exists.
- Stat-card markup (`<div class='stat-card'>…`) repeated across
  dashboard / portfolio / simulation render functions.

### 2.2 Repeated safety-boundary text
Variants of "no Codex / no XAML / no API deployment / no trusted capability"
appear across many endpoints with slight wording differences:
- `no Codex` × 5, `does not call Codex` × 7, `no XAML` × 15,
  `no API deployment` × 6, `no trusted capability` × 3.
- Only **2** were byte-identical
  (`"No Codex calls, no XAML modifications, no API deployments, no trusted capability registration."`
  in `/proposals/inbox` and `/approvals/inbox`). The rest are
  context-specific rewordings and were left untouched.

### 2.3 Repeated queue/approval/proposal summary logic
- `_build_simulation_summary`, `_simulation_state`,
  `_find_simulation_case`, `claim_simulation_case` all operate on the
  same `_SIMULATION_QUEUE` module-level dict and were scattered across
  different sections of `main.py`.
- `_build_approvals_summary`, `_list_approvals`,
  `_pending_approval_count`, `_generate_approval_id`,
  `_append_approval_event_to_run_memory`,
  `_append_erp_writeback_event_to_run_memory` all operate on the same
  `_APPROVAL_TASKS` dict.
- Both groups are pure (no FastAPI route coupling) but were inlined in
  `main.py`, making them hard to locate.

### 2.4 Possibly-unused imports / helpers / constants
AST-based scan (top-level functions defined but never referenced outside
their own body, across the whole repo including tests and docs):

| Lines | Function | Disposition |
|---|---|---|
| 8 | `model_unavailable_metadata` | **Deleted** (dead: superseded by inline `metadata(...)` calls) |
| 20 | `call_llm_with_retries` | **Deleted** (dead: superseded by `call_llm_structured`) |
| 19 | `_build_approval_audit_record` | **Deleted** (dead: audit record built inline in `_process_approval_decision`) |
| 90 | `run_triage_agent` | **Kept** (dead, but a 90-line LangGraph reference implementation — high-risk to delete; flagged for next round) |

After-extraction unused-import cleanup also removed 4 alias imports
that were no longer referenced in `main.py`:
`APPROVED_STATUSES as _APPROVED_STATUSES`,
`SIMULATION_DEFAULT_CASES as _SIMULATION_DEFAULT_CASES`,
`LEGACY_MODULE_MENU as _LEGACY_MODULE_MENU`,
`LEGACY_TABS as _LEGACY_TABS`.

---

## 3. Suggested module split list

| Proposed module | Status this round |
|---|---|
| `app/ui/legacy_shell.py` — `_render_legacy_shell` + tab/menu constants | **Done** |
| `app/ui/html_helpers.py` — shared safety-disclaimer constant | **Done (minimal)** |
| `app/services/simulation_store.py` — queue state + pure helpers | **Done** |
| `app/services/approval_store.py` — approval state + pure helpers | **Done** |
| `app/ui/render_run_memory.py` — `_render_run_memory_html` (273 lines) | Not done — high-risk HTML, defer |
| `app/ui/render_portfolio.py` — `_render_portfolio_html` (189 lines) | Not done — high-risk HTML, defer |
| `app/ui/render_dashboard.py` — `_render_dashboard_html` (174 lines) | Not done — high-risk HTML, defer |
| `app/services/erp_action_store.py` — `_process_erp_action` + `_ERP_ACTIONS` | Not done — tightly coupled to routes |
| `app/services/proposal_store.py` — `_record_proposal`, `_build_codex_prompt`, `_list_proposals` | Not done — touches memory/proposal files |
| `app/services/memory_run_service.py` — `memory_run_*` routes + helpers | Not done — touches Run Memory write paths |

---

## 4. Actual modifications this round

### 4.1 New files
- `reasoning-agent/app/ui/__init__.py` (empty package marker)
- `reasoning-agent/app/ui/legacy_shell.py` (288 lines)
  — `LEGACY_TABS`, `LEGACY_MODULE_MENU`, `render_legacy_shell`
- `reasoning-agent/app/ui/html_helpers.py` (20 lines)
  — `SAFETY_NO_CODEX_NO_XAML_NO_DEPLOY_NO_TRUSTED`
- `reasoning-agent/app/services/__init__.py` (empty package marker)
- `reasoning-agent/app/services/simulation_store.py` (181 lines)
  — `SIMULATION_QUEUE`, `SIMULATION_DEFAULT_CASES`,
    `reset_simulation_queue`, `simulation_state`,
    `find_simulation_case`, `claim_simulation_case`,
    `build_simulation_summary`
- `reasoning-agent/app/services/approval_store.py` (197 lines)
  — `APPROVAL_TASKS`, `APPROVED_STATUSES`,
    `generate_approval_id`, `list_approvals`, `pending_approval_count`,
    `append_approval_event_to_run_memory`,
    `append_erp_writeback_event_to_run_memory`,
    `build_approvals_summary`

### 4.2 main.py changes
- **Added** import block (lines 29–49) binding the extracted names back
  to their original private aliases (`_SIMULATION_QUEUE`,
  `_APPROVAL_TASKS`, `_render_legacy_shell`, etc.) so call sites are
  unchanged.
- **Removed** the inline definitions of the 17 moved functions/constants
  listed in §4.1 (579 lines total).
- **Removed** 3 dead functions (`model_unavailable_metadata`,
  `call_llm_with_retries`, `_build_approval_audit_record`).
- **Replaced** 2 inline safety-disclaimer strings with the
  `SAFETY_NO_CODEX_NO_XAML_NO_DEPLOY_NO_TRUSTED` constant.
- Net: 8,293 → 7,714 lines (−579, −7.0%).

### 4.3 Files NOT modified (high-risk areas preserved)
- `reasoning-agent/app/main_pre_refactor_20260629_142007.py` (backup)
- All Windows XAML files (`uipath-workflows/**/*.xaml`)
- `triage.py`, `schemas.py`, `shared/**`
- `memory/**` (Run Memory / Pattern Memory / proposal file storage)
- All test files (`reasoning-agent/tests/test_*.py`) — unchanged
- `mock-legacy-erp/**`, `generated-api-facade/**`

### 4.4 Kept unchanged — high-risk areas (deferred)
| Area | Reason |
|---|---|
| `run_triage_agent` (L964–1053, 90 lines) | Dead but a LangGraph reference implementation; deleting risks removing demo-narrative reference code. Recommend separate review. |
| All `*_render_*_html` functions except `_render_legacy_shell` | Large HTML blocks; extraction risks visual regressions. Needs per-function visual diff verification. |
| `_process_erp_action` + `_ERP_ACTIONS` | Tightly coupled to 5 ERP routes + 5 form-route variants; defer. |
| `evaluate_capability_evolution` (436 lines) | Core capability-evolution logic — out of scope for "low-risk hygiene". |
| `memory_run_*` routes + `_record_proposal` | Touch Run Memory / proposal file write paths — safety-critical, defer. |

---

## 5. Verification

### 5.1 Tests
```
278 passed, 1 warning in 25.41s
```
All 278 tests pass (same count as pre-refactor baseline; 0 regressions).

### 5.2 Endpoint smoke (TestClient)
| Endpoint | Status | Result |
|---|---:|---|
| `GET /erp/work-queue` | 200 | PASS |
| `GET /erp/work-queue/SIM-001` | 200 | PASS |
| `POST /simulation/inject-form` | 303 | PASS (redirect) |
| `GET /monitoring/live` | 200 | PASS |
| `GET /monitoring/live-data` | 200 | PASS (all 10 required keys present) |
| `GET /approvals/inbox` | 200 | PASS |
| `GET /proposals/inbox` | 200 | PASS (HTML) |
| `GET /proposals/inbox?format=json` | 200 | PASS (JSON) |
| `GET /simulation/dashboard` | 200 | PASS |
| `GET /openapi.json` | 200 | PASS (61 paths) |

### 5.3 OpenAPI required paths
All 4 required paths still registered in `/openapi.json`:
- `/erp/work-queue` ✓
- `/approvals/approved-pending-writeback` ✓
- `/simulation/cases/{simulation_case_id}/claim` ✓
- `/proposals/inbox` ✓

### 5.4 UiPath selector preservation
- `ctl00_MainContent_*` ids present on `/erp/work-queue` and detail page. ✓
- All 11 monitoring/live DOM ids present
  (`robot-status-panel`, `queue-summary-panel`, `latest-cases-panel`,
  `real-run-memory-panel`, `proposal-inbox-panel`, `audit-log-panel`,
  `heartbeat-history-panel`, `approval-summary-panel`, `injection-result`,
  `last-updated`, `polling-indicator`). ✓

### 5.5 State-sharing invariant
Module-singleton semantics verified at runtime:
```
SIMULATION_QUEUE is _SIMULATION_QUEUE  -> True
APPROVAL_TASKS   is _APPROVAL_TASKS   -> True
```
The imported aliases in `main.py` point to the **same dict objects** as
the store modules, so mutations remain visible across both modules.

---

## 6. Safety confirmation

| Constraint | Status |
|---|---|
| No Windows XAML modified | ✓ (no `.xaml` files touched) |
| No endpoint URL renamed | ✓ (61 routes, same paths) |
| No request/response schema changed | ✓ (Pydantic models unchanged) |
| No status-machine semantics changed | ✓ (simulation + approval state machines intact) |
| No UiPath selector renamed | ✓ (`ctl00_MainContent_*` preserved) |
| No DOM id renamed | ✓ (all 11 monitoring ids preserved) |
| No page layout / visual change | ✓ (`render_legacy_shell` byte-identical output) |
| No memory write behavior changed | ✓ (Run Memory append helpers unchanged) |
| No proposal threshold logic changed | ✓ (untouched) |
| No Codex call added | ✓ |
| No API deployment / trusted-capability registration | ✓ |
| No test expectations changed to mask behavior | ✓ (tests untouched) |

---

## 7. Recommendation for next round

**Yes — continue refactoring, but stay conservative.** Recommended next
round scope (in priority order):

1. **Extract `_render_run_memory_html` (273 lines) → `app/ui/render_run_memory.py`.**
   Largest remaining HTML render. Must byte-diff the output before/after.
2. **Extract `_render_portfolio_html` (189 lines) + `_render_dashboard_html` (174 lines) → `app/ui/render_case_pages.py`.**
   Pair them since they share the case-dashboard data shape.
3. **Extract `_process_erp_action` + `_ERP_ACTIONS` → `app/services/erp_action_store.py`.**
   Straightforward but coupled to 10 ERP routes — needs careful aliasing.
4. **Decide on `run_triage_agent` (90 lines, dead).**
   Either re-wire it as an alternate `/triage/langgraph` route, or delete
   it after confirming no docs/video reference it.

**Do NOT attempt next round:**
- Migrating routes to `APIRouter` (high-risk, huge diff, no behavioral gain).
- Splitting `evaluate_capability_evolution` (core logic, needs PRD re-read).
- Moving `memory_run_*` routes (Run Memory write safety).
