# Demo Narration Script

## 0:00-0:30 Problem

"Many ERP automations start in a browser queue, not with a clean API. The hard
part is not clicking one button. The hard part is deciding safely when an order
needs vendor data, human approval, manual investigation, or modernization."

## 0:30-1:00 Architecture

"UiPath remains the RPA and execution layer. The Python services provide the ERP
demo pages, a coded LangGraph route agent, memory, approvals, proposals, and
evidence dashboards."

## 1:00-1:45 ERP Extraction

"UiPath opens the ERP work queue and extracts normal business fields: PO number,
amount, budget, vendor, ERP status, system message, and business remarks. The
technical IDs still exist for selectors, but the page looks like a real ERP
order screen."

## 1:45-2:30 Agent With Enterprise Context

"UiPath sends those fields to `/case-intake/route`. The agent fetches mock
enterprise context and uses finance policy, sales pressure, operations policy,
and buyer notes to return a route, policy gate, reasoning summary, LLM proof,
and recommended ERP action."

## 2:30-3:20 Governance

"For budget exceptions, UiPath creates a web approval task instead of clicking
an approval button. For vendor issues it marks waiting vendor. For inventory
shortage it flags a capability gap. For ambiguous cases it sends manual
investigation."

## 3:20-4:10 Memory And Proposals

"Each run commits structured memory. Pattern Memory groups repeated behavior and
counts evidence toward a threshold. Once repeated budget or inventory patterns
reach the threshold, the system creates API or XAML modernization proposals."

## 4:10-5:00 Human-Approved Codex Handoff

"A proposal still does nothing by itself. A human opens the proposal inbox and
approves the handoff. Then the Codex session page shows a readable execution
timeline. Mock mode is used for recording; real CLI mode is available with an
explicit switch."
