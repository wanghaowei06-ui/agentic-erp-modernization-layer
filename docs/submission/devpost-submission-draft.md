# Devpost Submission Draft

## Project Title

Agentic ERP Modernization Layer

## One-Line Pitch

A UiPath RPA-first ERP Worker that uses an enterprise-context agent and memory
thresholds to turn repeated legacy ERP work into human-approved modernization
proposals.

## Problem

ERP automation often begins in a browser queue. Business rules are spread across
order fields, exception messages, buyer notes, approval policies, and undocumented
UI side effects. Jumping straight to an API or new workflow is unsafe without
evidence.

## Solution

UiPath keeps control of the ERP process. It extracts purchase-order fields from
a realistic legacy ERP page, calls a coded LangGraph route agent, branches by
the returned route and policy gate, creates human approval tasks, and writes Run
Memory. Pattern Memory accumulates repeated evidence and triggers API/XAML
modernization proposals only after a threshold is reached.

## How It Uses UiPath

UiPath Studio and Robot run `Main.xaml`. The workflow opens the ERP work queue,
uses stable UI selectors, calls HTTP services, creates approval tasks, commits
memory, and performs governed ERP actions.

## How It Uses Agents

The reasoning service is a coded Python/LangGraph agent. For agent-required
cases it combines:

- ERP order fields
- system exception reason
- business remarks
- mock enterprise context
- finance, sales, and operations policy signals

The response includes route, policy gate, explanation, context proof, LLM proof,
and recommended ERP action.

## Human-In-The-Loop

Budget exceptions create approval tasks instead of clicking an ERP approval
button. Modernization proposals require human approval before Codex handoff.

## What Was Built

- UiPath project files under `uipath-workflows/AgenticErpMvpRpa/`.
- ERP work queue/detail pages with stable selectors.
- Mock enterprise context API.
- LangGraph route agent endpoint.
- Approval inbox.
- Run Memory and Pattern Memory.
- Threshold-triggered API and XAML proposal pipeline.
- Codex handoff UI with mock stream and real CLI switch.
- Validation suite and generated API facade support services.
- Demo dashboards and evidence pages.

## What Is Demoed

- PO-1000 deterministic standard processing.
- PO-1001 budget exception using enterprise context and human approval.
- PO-1002 vendor information missing.
- PO-1003 inventory shortage and XAML proposal evidence.
- PO-1004 ambiguous manual investigation.
- Pattern Memory threshold triggering proposals.
- Human-approved Codex handoff.

## Limitations

The ERP and enterprise context are local mocks. Real LLM mode requires
credentials. Real Codex mode requires local CLI setup. The demo does not
auto-deploy APIs, auto-register trusted capabilities, or auto-modify Windows
XAML.

## GitHub Repo

https://github.com/wanghaowei06-ui/agentic-erp-modernization-layer
