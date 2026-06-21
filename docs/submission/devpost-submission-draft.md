# Devpost Submission Draft

## Project Title

Agentic ERP Modernization Layer

## One-Line Pitch

A UiPath-governed modernization case that turns fragile legacy ERP clicks into validated, human-approved API tools for enterprise agents.

## Problem

Enterprise ERP processes often start as fragile browser workflows. Teams need automation, but direct API modernization is risky when the process is exception-heavy, approval-gated, and embedded in legacy UI behavior.

## Solution

This MVP demonstrates a UiPath-governed modernization path. UiPath uses RPA to extract legacy ERP fields, calls a deterministic triage support service, routes cases by exception type, keeps humans in control for approvals, validates a generated API facade candidate, and only then switches the approved path to API-mode execution.

## How It Uses UiPath

UiPath is the main orchestration and governance layer. UiPath owns the case lifecycle, browser RPA, dynamic routing, human approval, validation governance, trusted-tool registration, and API-mode execution.

## How It Uses Agents

The triage support service behaves as a deterministic agentic classifier for exception routing. It returns structured evidence, confidence, recommended path, and next stage. The optional tool wrapper shows how an enterprise agent could consume an approved API tool later, after UiPath governance.

## How It Uses Robots

UiPath robots interact with the mock legacy ERP through Chrome UI, scrape stable fields, and perform the RPA write-back path by filling and clicking the approval form.

## How It Keeps Humans In Control

High-risk and review-required routes stop for human approval. Trusted-tool registration also requires approval before API mode is used. The validation failure branch keeps execution in RPA mode and routes to IT review.

## What Was Built

- Mock legacy ERP UI with stable RPA element IDs.
- Exception triage support service.
- Generated API facade candidate.
- Validation suite with passed and failed simulation paths.
- Enhanced demo pages: dashboard, timeline, readiness scorecard, and tool registry.
- UiPath implementation pack with request bodies, expected outputs, selector references, and runbooks.
- CI, Docker Compose, Makefile, and submission documentation.

## What Is Demoed

- PO-1001 main `budget_exceeded` path.
- PO-1002 `vendor_info_missing` route proof.
- PO-1003 `inventory_shortage` route proof.
- RPA write-back through the legacy UI.
- Validation passed and validation failed simulation.
- Trusted tool registration evidence.
- API-mode execution response.
- Dashboard, timeline, scorecard, and registry evidence screens.

## Technical Architecture

The repository contains four FastAPI support services: mock legacy ERP UI, triage support service, generated API facade, and validation suite. UiPath Studio / Automation Cloud is configured by the human builder to call these services and drive the case.

## Impact

The project shows a pragmatic modernization pattern: start with governed RPA where APIs are missing, collect evidence, validate parity, keep humans in control, and graduate only narrow business actions into approved API tools.

## Limitations

This is a hackathon MVP with a mock ERP and deterministic triage. It is not production deployment, real ERP integration, or automatic production code modernization.

## Roadmap

Expand exception coverage, integrate real UiPath Maestro or case management, strengthen validation, add RBAC and security controls, support real ERP adapter patterns, and build a governed tool registry with access control.

## GitHub Repo

https://github.com/wanghaowei06-ui/agentic-erp-modernization-layer
