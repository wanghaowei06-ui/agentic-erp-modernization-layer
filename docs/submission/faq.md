# FAQ

## Is UiPath still central?

Yes. UiPath opens the ERP work queue, extracts fields, calls the agent service,
branches, creates approvals, clicks ERP actions, and commits memory.

## What type of agent is used?

A coded Python/LangGraph agent service. The UiPath workflow calls it over HTTP.
This is not packaged as a UiPath Agent Builder agent.

## Does the agent use enterprise context?

Yes. Agent-required cases use mock enterprise context from `/company-context`,
including finance policy, sales risk, and operations constraints.

## Is every case an LLM decision?

No. Normal cases are deterministic precheck decisions and are marked as such.
Agent-required cases include `llm_validation_proof` showing mock or real call
mode.

## How are proposals triggered?

By committed Run Memory and Pattern Memory threshold evidence. They are not
created by an ERP button.

## What happens after proposal approval?

The Codex handoff session starts. In demo mode it shows a readable mock stream.
In real mode it can call local Codex CLI. It still does not auto-deploy or
auto-merge.

## Does the project modify Windows XAML automatically?

No. XAML/API modernization remains proposal or human-approved handoff work.

## Is this production ready?

No. It is a local judging/demo build with mock ERP and mock enterprise context.
