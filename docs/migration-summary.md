# Migration Summary

This repository contains a hackathon MVP for an Agentic ERP Modernization Layer. The Python services are support assets that UiPath can call during a UiPath-governed modernization case.

The generated API facade is a modernization facade for one business action: `request_purchase_order_approval`. It does not replace UiPath orchestration, case lifecycle, approval, validation governance, or API-mode execution decisions.

## Before

The mock legacy ERP exposes the purchase order exception process only through a browser UI. UiPath RPA can scrape stable fields and click the approval request form.

## Candidate Modernization

After UiPath validates parity between a cloned RPA test case and a cloned API test case, the generated API facade can be considered as a trusted-tool candidate. UiPath remains responsible for registration approval and deciding when to switch a case from RPA execution to API execution.

## Demo Scope

This is not full ERP modernization and does not modify production ERP code. It demonstrates how a fragile UI action can be surrounded by validation, approval, and a narrow API facade candidate.
