"""Unified Legacy ERP Shell renderer.

Provides the shared HTML page chrome (top tab navigation, left Module Menu,
breadcrumb + footer) used by every legacy-style page in the service:
``/erp/work-queue``, ``/erp/work-queue/{id}``, ``/approvals/inbox``,
``/proposals/inbox``, ``/monitoring/live``, and ``/simulation/dashboard``.

This module is a pure extraction from ``app.main`` — no behavior, CSS,
DOM ids, class names, or visual layout have changed. The shell is built
with a list of HTML fragments (no templating engine) to keep the
dependency surface minimal and to preserve byte-for-byte output.

UiPath selector stability contract:
  - The ``ctl00_MainContent_*`` DOM ids are emitted by the *callers* of
    ``render_legacy_shell`` (in their ``content_html``), not by this
    function. This function only emits the shell chrome and must not add
    or rename any ``id=`` attribute.
  - The tab labels and module-menu labels below are part of the visible
    navigation and must not be reworded.
"""
from __future__ import annotations


# Top-level navigation tabs. Order and labels are part of the visible
# chrome and are asserted on by tests.
LEGACY_TABS: list[tuple[str, str]] = [
    ("Procurement", "/erp/work-queue"),
    ("Approvals", "/approvals/inbox"),
    ("Monitoring", "/monitoring/live"),
    ("Simulation", "/simulation/dashboard"),
    ("Proposals", "/proposals/inbox"),
]


# Left-hand Module Menu tree. Tuples are (entry_type, label, href) where
# entry_type is "section" (a non-clickable group header) or "item".
LEGACY_MODULE_MENU: list[tuple[str, str | None, str | None]] = [
    ("section", "Procurement", None),
    ("item", "Purchase Order Work Queue", "/erp/work-queue"),
    ("item", "Purchase Order Exceptions", "/erp/work-queue"),
    ("item", "Approval Requests", "/approvals/inbox"),
    ("item", "Vendor Master Review", None),
    ("section", "Monitoring", None),
    ("item", "Live Monitoring", "/monitoring/live"),
    ("item", "Robot Status", "/monitoring/live"),
    ("item", "Audit Logs", "/monitoring/live"),
    ("section", "Modernization", None),
    ("item", "Proposal Inbox", "/proposals/inbox"),
    ("item", "Evidence Snapshot", "/demo/evidence-snapshot"),
    ("item", "Interactive Replay", "/demo/replay"),
    ("item", "Case Portfolio", "/case-portfolio"),
    ("section", "Simulation", None),
    ("item", "Simulation Dashboard", "/simulation/dashboard"),
    ("item", "Inject Cases", "/monitoring/live"),
]


def render_legacy_shell(
    active_tab: str,
    title: str,
    breadcrumb: str,
    content_html: str,
    screen_id: str,
    *,
    extra_css: str = "",
    extra_script: str = "",
) -> str:
    """Render a complete HTML page wrapped in the unified Legacy ERP shell.

    All legacy-style pages share this shell for visual consistency:
    - Top dark header bar with "Contoso Legacy ERP 2009" + tab navigation
    - Left Module Menu with sections (Procurement / Monitoring / Modernization / Simulation)
    - Right content area with breadcrumb + title + page-specific content
    - Flex layout: content area scrolls internally, body doesn't scroll

    Parameters:
        active_tab: Tab name to highlight ("Procurement", "Approvals", "Monitoring",
                    "Simulation", "Proposals", or "" for none).
        title: Page H1 title text.
        breadcrumb: Breadcrumb trail HTML (e.g. "Home > Procurement > Work Queue").
        content_html: Inner HTML for the main content panel.
        screen_id: Legacy screen ID shown in the footer (e.g. "PROC-EXC-204").
        extra_css: Additional CSS rules appended after the shell CSS.
        extra_script: Additional JS appended before </body>.
    """
    h: list[str] = []
    h.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    h.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    h.append(f"<title>Legacy ERP — {title}</title>")
    h.append("<style>")
    # --- Core flex layout (body doesn't scroll; content-wrap scrolls) ---
    h.append("html, body { height: 100%; margin: 0; }")
    h.append("body { background: #d5dde8; overflow: hidden; color: #182433; "
             "font-family: Tahoma, Verdana, Arial, sans-serif; font-size: 12px; line-height: 1.35; }")
    h.append("a { color: #003f8c; text-decoration: underline; }")
    h.append("* { box-sizing: border-box; }")
    h.append(".erp-shell { height: 100vh; display: flex; flex-direction: column; }")
    h.append(".erp-header { flex: 0 0 auto; }")
    h.append(".erp-body { flex: 1 1 auto; display: flex; min-height: 0; }")
    h.append(".module-menu { width: 260px; flex: 0 0 260px; overflow: auto; "
             "border-right: 1px solid #9aa8b8; background: #f3f5f8; }")
    h.append(".erp-content-wrap { flex: 1 1 auto; min-width: 0; min-height: 0; overflow: auto; "
             "background: #eef2f7; }")
    h.append(".erp-content-panel { padding: 10px; }")
    # --- Top shell ---
    h.append(".top-shell { width: 100%; border-bottom: 1px solid #071629; "
             "background: linear-gradient(#304967, #17263b); color: #fff; }")
    h.append(".app-title-row { display: flex; align-items: center; justify-content: space-between; "
             "height: 42px; padding: 0 14px; }")
    h.append(".app-title { font-size: 17px; font-weight: 700; }")
    h.append(".session-strip { color: #dfe8f5; font-size: 11px; }")
    h.append(".legacy-tabs { display: flex; gap: 1px; padding: 0 8px; }")
    h.append(".legacy-tabs a { display: block; min-width: 92px; padding: 7px 10px 6px; "
             "border: 1px solid #071629; border-bottom: 0; "
             "background: linear-gradient(#f8fafc, #c7d2df); color: #10253d; "
             "font-weight: 700; text-align: center; text-decoration: none; }")
    h.append(".legacy-tabs a:hover { background: linear-gradient(#fff, #dce6f2); "
             "outline: 2px solid #f3c74f; outline-offset: -2px; }")
    h.append(".legacy-tabs a.active { background: linear-gradient(#fff, #e8d181); "
             "color: #1d314b; outline: 2px solid #8b6f1f; outline-offset: -2px; }")
    # --- Module menu ---
    h.append(".module-menu-title { padding: 7px 9px; border-bottom: 1px solid #9aa8b8; "
             "background: linear-gradient(#f7f9fc, #cbd5e2); color: #17324f; font-weight: 700; }")
    h.append(".module-tree { margin: 0; padding: 7px 0 10px; list-style: none; }")
    h.append(".module-tree li { padding: 4px 10px 4px 22px; border-bottom: 1px solid #e0e5ec; color: #26384d; }")
    h.append(".module-tree li.section { padding-left: 10px; background: #e8edf4; "
             "color: #1d314b; font-weight: 700; }")
    h.append(".module-tree li a { color: #26384d; text-decoration: none; }")
    h.append(".module-tree li a:hover { text-decoration: underline; }")
    # --- Content area ---
    h.append(".breadcrumb-bar { padding: 6px 9px; border-bottom: 1px solid #9aa8b8; "
             "background: #f8fafc; color: #4e5d70; }")
    h.append("main { padding: 10px; background: #fff; }")
    h.append(".erp-content-inner { background: #fff; border: 1px solid #9aa8b8; margin: 10px; }")
    h.append("h1 { margin: 0 0 8px; padding: 7px 9px; border: 1px solid #9aa8b8; "
             "background: linear-gradient(#f9fbfd, #d9e2ed); color: #102a47; font-size: 18px; }")
    h.append("h2 { margin: 0; padding: 6px 8px; border-bottom: 1px solid #9aa8b8; "
             "background: linear-gradient(#f9fbfd, #d7e0eb); color: #18314d; font-size: 13px; }")
    h.append(".legacy-panel { margin-bottom: 10px; border: 1px solid #9aa8b8; background: #fff; }")
    h.append(".legacy-panel-body { padding: 9px; }")
    h.append(".legacy-note { margin: 0 0 8px; color: #4e5d70; }")
    h.append(".legacy-footer { padding: 6px 10px; border-top: 1px solid #9aa8b8; "
             "background: #eef2f7; color: #4e5d70; }")
    # --- Tables ---
    h.append("table { width: 100%; border-collapse: collapse; background: #fff; }")
    h.append("th, td { padding: 6px 7px; border: 1px solid #c4ccd8; text-align: left; vertical-align: top; }")
    h.append("th { background: linear-gradient(#f6f8fb, #dce4ef); color: #25384f; font-weight: 700; }")
    h.append(".legacy-grid tr:nth-child(even) td { background: #fbfcfe; }")
    h.append(".legacy-grid tr:hover td { background: #fffde8; }")
    h.append(".legacy-grid td { white-space: nowrap; text-overflow: ellipsis; }")
    h.append(".form-table th { width: 185px; color: #304258; }")
    h.append(".form-table td { height: 31px; }")
    # --- Status colors ---
    h.append(".status-exception { color: #7a1f1f; font-weight: 700; }")
    h.append(".status-pending { color: #6a4b00; font-weight: 700; }")
    h.append(".status-normal { color: #2e7d32; }")
    h.append(".status-erp { color: #1565c0; font-weight: 700; }")
    h.append(".status-PENDING { color: #6a4b00; font-weight: 700; }")
    h.append(".status-APPROVED { color: #2e7d32; font-weight: 700; }")
    h.append(".status-APPROVED_PENDING_ERP_WRITEBACK { color: #1565c0; font-weight: 700; }")
    h.append(".status-ERP_WRITEBACK_IN_PROGRESS { color: #1565c0; font-weight: 700; }")
    h.append(".status-ERP_WRITEBACK_COMPLETED { color: #2e7d32; font-weight: 700; }")
    h.append(".status-REJECTED { color: #7a1f1f; font-weight: 700; }")
    h.append(".status-idle { color: #4e5d70; }")
    h.append(".status-running { color: #2e7d32; font-weight: 700; }")
    h.append(".status-completed { color: #2e7d32; }")
    h.append(".status-failed { color: #7a1f1f; font-weight: 700; }")
    h.append(".status-in_progress { color: #1565c0; font-weight: 700; }")
    h.append(".sim-pending { background: #fff9c4; }")
    h.append(".sim-in_progress { background: #e3f2fd; }")
    h.append(".sim-completed { background: #e8f5e9; }")
    h.append(".sim-failed { background: #ffebee; }")
    # --- Buttons ---
    h.append("button, .button, .btn { display: inline-block; min-height: 28px; padding: 5px 12px; "
             "border: 1px solid #687a91; border-radius: 0; "
             "background: linear-gradient(#ffffff, #d8e2ef); color: #10253d; "
             "cursor: pointer; font: inherit; font-weight: 700; text-align: center; text-decoration: none; }")
    h.append("button:hover, .button:hover, .btn:hover { background: linear-gradient(#fff9d8, #e8d181); "
             "outline: 2px solid #8b6f1f; outline-offset: 1px; }")
    h.append(".btn-approve { background: linear-gradient(#e8f5e9, #a5d6a7); border-color: #2e7d32; color: #1b5e20; }")
    h.append(".btn-reject { background: linear-gradient(#ffebee, #ef9a9a); border-color: #c62828; color: #b71c1c; }")
    h.append(".actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 8px; }")
    # --- Stats / cards ---
    h.append(".stat-grid { display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0; }")
    h.append(".stat-card { border: 1px solid #9aa8b8; background: linear-gradient(#fff, #f0f4f8); "
             "border-radius: 4px; padding: 12px; min-width: 110px; text-align: center; }")
    h.append(".stat-value { font-size: 22px; font-weight: 700; color: #102a47; }")
    h.append(".stat-label { font-size: 11px; color: #4e5d70; margin-top: 4px; }")
    h.append(".badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; }")
    h.append(".badge-real { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }")
    h.append(".badge-static { background: #fff9c4; color: #f57f17; border: 1px solid #ffe082; }")
    # --- Forms ---
    h.append("input[type='text'], textarea, select { border: 1px solid #9aa8b8; border-radius: 0; "
             "padding: 4px 8px; font: inherit; color: #182433; background: #fff; }")
    h.append("input[type='text'] { width: 180px; }")
    h.append("textarea { width: 350px; height: 50px; }")
    h.append(".approval-actions { display: grid; grid-template-columns: repeat(2, minmax(280px, 1fr)); "
             "gap: 10px; align-items: stretch; margin-top: 10px; }")
    h.append(".approval-decision-form { display: grid; grid-template-columns: minmax(0, 1fr); "
             "gap: 8px; align-content: start; border: 1px solid #c4ccd8; background: #f8fafc; "
             "padding: 10px; min-width: 0; }")
    h.append(".approval-decision-form label { display: grid; grid-template-columns: 82px minmax(0, 1fr); "
             "gap: 8px; align-items: center; margin: 0; font-weight: 700; color: #304258; }")
    h.append(".approval-decision-form input[type='text'], .approval-decision-form textarea { "
             "box-sizing: border-box; width: 100%; min-width: 0; }")
    h.append(".approval-decision-form input[type='text'] { height: 30px; }")
    h.append(".approval-decision-form textarea { height: 54px; resize: vertical; }")
    h.append(".approval-decision-form button { width: 100%; min-height: 32px; }")
    h.append("@media (max-width: 760px) { .approval-actions { grid-template-columns: 1fr; } "
             ".approval-decision-form label { grid-template-columns: 1fr; gap: 4px; } }")
    # --- Misc ---
    h.append(".first-pending-banner { margin-bottom: 10px; padding: 8px 10px; "
             "border: 1px solid #d8b94c; background: #fff6cf; color: #4f3900; font-weight: 700; }")
    h.append(".queue-empty { margin: 12px 0; padding: 10px; border: 1px solid #d8b94c; "
             "background: #fff6cf; color: #4f3900; font-weight: 700; text-align: center; }")
    h.append(".approval-card { border: 1px solid #9aa8b8; background: #fff; "
             "border-radius: 4px; padding: 12px; margin: 12px 0; }")
    h.append(".approval-card.pending { border-left: 4px solid #f57f17; }")
    h.append(".approval-card.approved { border-left: 4px solid #2e7d32; }")
    h.append(".approval-card.rejected { border-left: 4px solid #c62828; }")
    h.append(".audit { background: #f3f5f8; border: 1px solid #c4ccd8; border-radius: 3px; "
             "padding: 8px; margin-top: 8px; font-size: 11px; }")
    h.append(".meta { color: #4e5d70; font-size: 11px; }")
    h.append("pre { background: #f3f5f8; border: 1px solid #c4ccd8; border-radius: 3px; "
             "padding: 10px; overflow-x: auto; }")
    h.append("hr { border: 0; border-top: 1px solid #c4ccd8; margin: 16px 0; }")
    h.append(".refresh-note { color: #4e5d70; font-size: 11px; }")
    # --- Injection buttons (monitoring page) ---
    h.append(".inject-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }")
    h.append(".inject-btn { padding: 6px 14px; border: 1px solid #687a91; border-radius: 0; "
             "background: linear-gradient(#fff, #d8e2ef); color: #10253d; cursor: pointer; "
             "font: inherit; font-weight: 700; }")
    h.append(".inject-btn:hover { background: linear-gradient(#fff9d8, #e8d181); "
             "outline: 2px solid #8b6f1f; outline-offset: 1px; }")
    h.append(".inject-normal { border-color: #2e7d32; }")
    h.append(".inject-budget_exceeded { border-color: #c62828; }")
    h.append(".inject-vendor_info_missing { border-color: #f57f17; }")
    h.append(".inject-inventory_shortage { border-color: #6a1b9a; }")
    h.append(".inject-ambiguous { border-color: #4e5d70; }")
    h.append(".inject-result { background: #e8f5e9; border: 1px solid #2e7d32; border-radius: 3px; "
             "padding: 8px 12px; margin: 8px 0; color: #1b5e20; font-size: 12px; }")
    h.append(".erp-quick-link { margin: 10px 0; padding: 10px; "
             "border: 2px solid #1a3a5c; background: #e3f2fd; font-size: 13px; }")
    # --- Page-specific CSS ---
    if extra_css:
        h.append(extra_css)
    h.append("</style></head><body>")

    # --- Shell open ---
    h.append("<div class='erp-shell'>")

    # --- Header ---
    h.append("<div class='erp-header'>")
    h.append("<div class='top-shell'>")
    h.append("<div class='app-title-row'>")
    h.append("<div class='app-title'>Contoso Legacy ERP 2009</div>")
    h.append("<div class='session-strip'>Company: US01 | User: DEMO.RPA | Period: FY2026-P06</div>")
    h.append("</div>")
    h.append("<nav class='legacy-tabs' aria-label='primary navigation'>")
    for tab_label, tab_href in LEGACY_TABS:
        cls = " class='active'" if tab_label == active_tab else ""
        h.append(f"<a href='{tab_href}'{cls}>{tab_label}</a>")
    h.append("</nav>")
    h.append("</div>")
    h.append("</div>")  # erp-header

    # --- Body: module menu + content wrap ---
    h.append("<div class='erp-body'>")

    # Module menu.
    h.append("<aside class='module-menu' aria-label='module menu'>")
    h.append("<div class='module-menu-title'>Module Menu</div>")
    h.append("<ul class='module-tree'>")
    for entry_type, label, href in LEGACY_MODULE_MENU:
        if entry_type == "section":
            h.append(f"<li class='section'>{label}</li>")
        else:
            if href:
                h.append(f"<li><a href='{href}'>{label}</a></li>")
            else:
                h.append(f"<li>{label}</li>")
    h.append("</ul>")
    h.append("</aside>")

    # Content wrap.
    h.append("<div class='erp-content-wrap'>")
    h.append(f"<div class='breadcrumb-bar'>{breadcrumb}</div>")
    h.append(f"<div class='erp-content-panel'>")
    h.append(f"<h1>{title}</h1>")
    h.append(content_html)
    h.append("</div>")  # erp-content-panel
    h.append(f"<div class='legacy-footer'>Legacy WebForms compatibility mode | Screen ID: {screen_id}</div>")
    h.append("</div>")  # erp-content-wrap

    h.append("</div>")  # erp-body
    h.append("</div>")  # erp-shell

    if extra_script:
        h.append(extra_script)

    h.append("</body></html>")

    return "\n".join(h)
